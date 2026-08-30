"""Generic full-reload loader from CSV files into Spark warehouse tables.

The source schema is declared in YAML (see ``conf/schemas/``) and parsed into
:class:`CsvTableSchema`. :class:`CsvLoader` reads every CSV file matching a
path, applies the schema (rename to canonical column names, cast, validate),
and overwrites the destination table.
"""

from __future__ import annotations

import bz2
import glob
import gzip
import io
import operator
import re
import zlib
from collections import Counter
from collections.abc import Iterator
from functools import reduce
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, Field, model_validator
from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.spark import get_spark_session

#: Hidden per-row column naming the file a row came from (base name only).
#: :meth:`CsvLoader._scan_positional` attaches it, :meth:`CsvLoader._validate`
#: reports problems per file with it and :meth:`CsvLoader.load` drops it
#: before the write. Never a contract column — but a contract may *source*
#: a column from it (``source: _source_file``) to expose the file name.
SOURCE_FILE_COL = "_source_file"

#: Files / duplicated keys named in a validation error message.
REPORT_LIMIT = 10

#: Bytes read per step while looking for a file's first header line.
_CHUNK = 65536


def _decompressed(file: str) -> Iterator[bytes] | None:
    """``file`` in ``_CHUNK``-byte pieces, as Spark would see it after decompression.

    Parameters
    ----------
    file : str
        Path; the suffix selects the codec — ``.gz`` and ``.bz2`` through the
        standard library, ``.deflate`` (Hadoop's DefaultCodec, zlib format)
        streamed through ``zlib``, anything else read as is.

    Returns
    -------
    Iterator of bytes or None
        ``None`` for a Hadoop codec Python cannot open (``.zst``, ``.lz4``,
        ``.snappy``).
    """
    suffix = Path(file).suffix.lower()
    if suffix in (".zst", ".lz4", ".snappy"):
        return None
    if suffix == ".deflate":
        return _inflated(file)
    if suffix == ".gz":
        return _chunks(gzip.open(file, "rb"))
    if suffix == ".bz2":
        return _chunks(bz2.open(file, "rb"))
    return _chunks(open(file, "rb"))


def _chunks(f: io.BufferedIOBase) -> Iterator[bytes]:
    with f:
        while chunk := f.read(_CHUNK):
            yield chunk


def _inflated(file: str) -> Iterator[bytes]:
    inflater = zlib.decompressobj()
    with open(file, "rb") as f:
        while chunk := f.read(_CHUNK):
            yield inflater.decompress(chunk)


def _is_header_line(line: bytes, comment: bytes) -> bool:
    """Whether Spark takes ``line`` as a header rather than skipping it.

    Spark passes over lines that are empty once spaces are stripped — a
    tab-only line is a header to it — and, with ``comment`` set, lines
    starting with that byte.
    """
    return bool(line.strip(b" ")) and not (comment and line.startswith(comment))


def _source_file_name() -> Column:
    """The base name of the file each row was read from.

    Built on ``input_file_name()`` rather than the hidden ``_metadata`` struct,
    which a source header literally named ``_metadata`` would shadow.

    Returns
    -------
    pyspark.sql.Column
        The URI's last path segment, percent-decoded. A literal ``+`` is
        preserved: Hadoop paths encode a space as ``%20``, whereas
        ``url_decode`` (form decoding) would read the plus itself as one.
    """
    name = F.element_at(F.split(F.input_file_name(), "/"), -1)
    return F.url_decode(F.regexp_replace(name, r"\+", "%2B"))


class CsvColumn(BaseModel):
    """One column of a CSV source and its canonical form in the warehouse.

    Attributes
    ----------
    name : str
        Canonical column name in the destination table.
    type : str
        Spark SQL type to cast to, e.g. ``string``, ``int``, ``bigint``,
        ``double``, ``date``, ``timestamp``.
    source : str, optional
        Column header in the CSV file. Defaults to ``name``.
    format : str, optional
        Datetime pattern for ``date``/``timestamp`` parsing,
        e.g. ``yyyy/MM/dd``.
    nullable : bool, default True
        If False, the load fails when the column contains nulls after
        casting (which also catches values that fail to parse).
    required : bool, default True
        If False, the column may be absent from a source file (e.g. columns
        the provider appended in later years) and is filled with nulls.
        If True, the load fails when a file lacks the column.
    """

    name: str
    type: str
    source: str | None = None
    format: str | None = None
    nullable: bool = True
    required: bool = True

    @property
    def source_name(self) -> str:
        """Header name expected in the CSV file."""
        return self.source if self.source is not None else self.name


class CsvTableSchema(BaseModel):
    """Declarative schema for a CSV-backed source table.

    Attributes
    ----------
    description : str, optional
        Human-readable description of the source data.
    read_options : dict of str to str
        Extra options passed to Spark's CSV reader (e.g. ``encoding``).
        ``header`` is always ``true``.
    grain : list of str
        Column names that must be unique together across the loaded data.
        Empty list disables the uniqueness check.
    columns : list of CsvColumn
        Columns to load, in destination-table order. Source columns not
        listed here are dropped. The name ``_source_file`` — in any case —
        is reserved for the loader's hidden file-name column (a column may
        *source* it to expose the file name under another name).
    """

    description: str | None = None
    read_options: dict[str, str] = Field(default_factory=dict)
    grain: list[str] = Field(default_factory=list)
    columns: list[CsvColumn]

    @model_validator(mode="after")
    def _no_reserved_column_name(self) -> CsvTableSchema:
        # In any case: Spark resolves names case-insensitively by default.
        reserved = [c.name for c in self.columns if c.name.lower() == SOURCE_FILE_COL]
        if reserved:
            raise ValueError(
                f"column name {reserved[0]!r} is reserved for the loader's file-name column "
                f"{SOURCE_FILE_COL!r}; expose the file name under another name "
                f"(source: {SOURCE_FILE_COL})"
            )
        return self

    @classmethod
    def from_yaml(cls, path: Path | str) -> CsvTableSchema:
        """Load and validate a schema definition from a YAML file.

        Parameters
        ----------
        path : pathlib.Path or str
            Path to the YAML schema file.

        Returns
        -------
        CsvTableSchema
        """
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f))


class CsvLoader:
    """Full reload of CSV files into a managed Spark warehouse table.

    Each load reads every matching CSV file, applies the schema, validates
    it, and overwrites the destination table (data and table schema), so the
    table always reflects exactly the current contents of ``filepath``.

    Parameters
    ----------
    schema : CsvTableSchema
        Source schema definition (see :meth:`CsvTableSchema.from_yaml`).
    filepath : pathlib.Path or str
        A CSV file, a directory (all ``*.csv`` files in it), or a glob
        pattern.
    table : str
        Destination table, e.g. ``pma_raw.jepx_spot``. The database is created
        if it does not exist.
    spark : pyspark.sql.SparkSession, optional
        Existing session to use. Defaults to a Hive-enabled session, which
        picks up the metastore/warehouse settings from ``SPARK_CONF_DIR``.
    """

    def __init__(
        self,
        schema: CsvTableSchema,
        filepath: Path | str,
        table: str,
        spark: SparkSession | None = None,
    ) -> None:
        self.schema = schema
        self.filepath = Path(filepath)
        self.table = table
        self.spark = spark if spark is not None else get_spark_session()
        # Spark 4 limits the CSV reader to a handful of charsets by default;
        # sources like JEPX need Java charsets such as windows-31j.
        self.spark.conf.set("spark.sql.legacy.javaCharsets", "true")

    def load(self) -> int:
        """Run the full reload.

        Returns
        -------
        int
            Number of rows written to the destination table.

        Raises
        ------
        FileNotFoundError
            If ``filepath`` matches no CSV files.
        ValueError
            If a file lacks required columns, a non-nullable column
            contains nulls after casting, or the grain is not unique.
        """
        files = self._resolve_files()
        logger.info("Loading {} file(s) into {}: {}", len(files), self.table, files)
        df = self._read_all(files)
        df.cache()
        try:
            # The hidden source-file column serves validation only.
            out = df.drop(SOURCE_FILE_COL)
            n_rows = df.count()
            logger.info(
                "Read shape=({}, {}); schema: {}",
                n_rows,
                len(out.columns),
                ", ".join(f"{f.name}:{f.dataType.simpleString()}" for f in out.schema),
            )
            self._validate(df)
            self._write(out)
        finally:
            df.unpersist()
        logger.info("Loaded {} rows into {}", n_rows, self.table)
        return n_rows

    def _resolve_files(self) -> list[str]:
        if self.filepath.is_dir():
            files = sorted(str(p) for p in self.filepath.glob("*.csv"))
        else:
            files = sorted(glob.glob(str(self.filepath)))
        if not files:
            raise FileNotFoundError(f"No CSV files found at {self.filepath}")
        return files

    def _read_all(self, files: list[str]) -> DataFrame:
        """Read every file into one DataFrame in the contract's column order.

        Files are grouped by the bytes of their first header line
        (:meth:`_first_line`) and every group is read in one scan: Spark
        applies the first file's header to all files of a multi-path read, so
        differently laid-out files must not share one, while one frame per
        file re-analyses the whole plan on every ``unionByName`` and ships a
        task binary that grows with the file count (1,600 files ≈ 8 min of
        planning and a 45 MiB binary). Each group's header is judged once,
        by Spark's own column names for its first file
        (:meth:`_group_header`), and the scan itself checks every file's
        header (``enforceSchema=false``): the contract's columns must sit at
        the same positions under the same names as in the scan's schema, or
        the read fails naming the file. For a line-mode read the key *is*
        the header line, and files whose header the first line does not
        determine (``multiLine``) are grouped alone, so a group never mixes
        layouts; the scan's check is belt and braces. Loaders whose files carry
        no usable header override this — :meth:`_scan_positional` +
        :meth:`_project` for positional layouts, one ``createDataFrame``
        over Python-parsed rows.

        Parameters
        ----------
        files : list of str
            Resolved file paths, as returned by :meth:`_resolve_files`.

        Returns
        -------
        pyspark.sql.DataFrame
            Contract columns plus ``SOURCE_FILE_COL``.

        Raises
        ------
        ValueError
            If a group's header lacks a required column, repeats a contract
            column or carries the loader's reserved ``_source_file`` column;
            the message names the group's first file and its size.
        """
        groups: dict[bytes | str, list[str]] = {}
        for file in files:
            line = self._first_line(file)
            groups.setdefault(file if line is None else line, []).append(file)
        frames = []
        for members in groups.values():
            names, cells = self._group_header(members[0])
            problem = self._header_problem(names, cells)
            if problem is not None:
                raise ValueError(f"{self._group_label(members)} {problem}")
            frames.append(self._read_layout(members))
        return reduce(DataFrame.unionByName, frames)

    def _group_header(self, file: str) -> tuple[list[str], list[str]]:
        """Spark's column names for ``file``'s header, and the header's cells.

        Two small jobs — run once per group, on its first file.

        Parameters
        ----------
        file : str
            CSV path, any codec Spark reads.

        Returns
        -------
        tuple of (list of str, list of str)
            The columns a ``header=true`` read yields (an empty cell or one
            equal to ``nullValue`` is ``_c<i>``, duplicates carry their
            position — Spark's ``makeSafeHeader``) and the raw cells of the
            same line from a headerless read (a cell Spark reads as null
            comes back as ``""``); both empty for an empty file. Type
            inference is off for both reads whatever the contract says.
        """
        names = (
            self.spark.read.options(**self._spark_options(header="true", inferSchema="false"))
            .csv(file)
            .columns
        )
        rows = (
            self.spark.read.options(**self._spark_options(header="false", inferSchema="false"))
            .csv(file)
            .head(1)
        )
        cells = ["" if cell is None else str(cell) for cell in rows[0]] if rows else []
        return names, cells

    @staticmethod
    def _group_label(members: list[str]) -> str:
        """How a group is named in an error: its first file, and how many share the header."""
        if len(members) == 1:
            return members[0]
        return f"{members[0]} (+{len(members) - 1} files with the same header)"

    def _header_problem(self, names: list[str], cells: list[str]) -> str | None:
        """Why a header cannot serve the contract, or ``None``.

        Parameters
        ----------
        names : list of str
            Spark's column names for the header (:meth:`_group_header`).
        cells : list of str
            The header's raw cells, for telling a duplicated source from an
            absent one.

        Returns
        -------
        str or None
            ``"has the reserved header column [...]"`` when a cell is the
            loader's own ``_source_file`` (in any case the session's resolver
            matches): the file-name column would overwrite it, and a
            contract that sources ``_source_file`` means the file name;
            ``"has duplicated header columns: [...]"`` when a contract source
            recurs among the cells — compared as the session resolves names,
            so ``id,ID`` counts unless ``spark.sql.caseSensitive`` is on —
            since Spark suffixes them (``id0``/``ID1``) and the contract
            column would silently become null; ``"is missing required
            columns: [...]"`` when a required source other than
            ``_source_file`` resolves to none of the names
            (:meth:`_resolve`); otherwise ``None``.
        """
        reserved = [n for n in names if self._fold(n) == self._fold(SOURCE_FILE_COL)]
        if reserved:
            return f"has the reserved header column {reserved}"
        counts = Counter(self._fold(cell) for cell in cells)
        sources = [c.source_name for c in self.schema.columns]
        duplicated = sorted({s for s in sources if s not in names and counts[self._fold(s)] > 1})
        if duplicated:
            return f"has duplicated header columns: {duplicated}"
        missing = [
            c.source_name
            for c in self.schema.columns
            if c.required
            and c.source_name != SOURCE_FILE_COL
            and self._resolve(c.source_name, names) is None
        ]
        if missing:
            return f"is missing required columns: {missing}"
        return None

    def _resolve(self, source: str, names: list[str]) -> str | None:
        """The column of ``names`` that ``source`` denotes, as the session resolves names.

        Parameters
        ----------
        source : str
            A contract column's source name.
        names : list of str
            The columns available (Spark's names for a header, or a frame's).

        Returns
        -------
        str or None
            ``source`` itself when present; otherwise, unless
            ``spark.sql.caseSensitive`` is on, the column that matches it
            case-insensitively (unique, since Spark suffixes case-only
            duplicates); ``None`` when nothing matches.
        """
        if source in names:
            return source
        folded = self._fold(source)
        return next((name for name in names if self._fold(name) == folded), None)

    def _fold(self, name: str) -> str:
        """``name`` as the session compares column names."""
        case_sensitive = self.spark.conf.get("spark.sql.caseSensitive", "false")
        return name if str(case_sensitive).lower() == "true" else name.lower()

    def _spark_options(self, **overrides: str) -> dict[str, str]:
        """The contract's ``read_options`` with ``overrides`` in force.

        Spark matches option keys case-insensitively, so an override
        replaces the contract's value under any spelling of its key.

        Parameters
        ----------
        **overrides : str
            Spark options the loader owns for this read — ``header`` (true
            for the grouped scan, false for a header or positional read),
            ``inferSchema`` (always false: values are read as strings and
            cast per the contract, so the files sharing a scan never change
            the type a value is read with) and ``enforceSchema`` (false on
            the grouped scan: Spark then checks, for every file, that the
            contract's columns sit at the same positions under the same
            names as in the scan's schema, and refuses — naming it — a file
            where they do not; never a silent misalignment).

        Returns
        -------
        dict of str to str
            Options to pass to ``spark.read.options``.
        """
        owned = {key.lower() for key in overrides}
        kept = {k: v for k, v in self.schema.read_options.items() if k.lower() not in owned}
        return {**kept, **overrides}

    def _first_line(self, file: str) -> bytes | None:
        """The bytes of the line Spark will take as ``file``'s header.

        Nothing is decoded: a BOM, quotes, escapes and separators are just
        bytes, so the value serves only to group files Spark will read
        identically — the same bytes parse the same way, and for a
        line-mode read the header *is* this line. Lines Spark skips before
        the header are passed over the same way (:func:`_is_header_line`);
        lines end at ``\\n``, ``\\r`` or ``\\r\\n`` as for Hadoop's line reader,
        or at the contract's ``lineSep`` alone when it sets one.

        Parameters
        ----------
        file : str
            Path, any codec :func:`_decompressed` opens.

        Returns
        -------
        bytes or None
            The line without its terminator; ``b""`` for a file with no such
            line; ``None`` when the file must be grouped alone, Spark reading
            its header by itself: the contract sets ``multiLine`` (a quoted
            header cell may span lines, so the first physical line does not
            determine the header), Python cannot open the codec, or the
            contract's ``lineSep`` / ``comment`` is not ASCII.
        """
        if self._option("multiLine", "false").lower() == "true":
            return None
        try:
            line_sep = self._option("lineSep", "").encode("ascii")
            comment = self._option("comment", "").encode("ascii")
        except UnicodeEncodeError:
            return None
        chunks = _decompressed(file)
        if chunks is None:
            return None
        terminator = re.compile(re.escape(line_sep) if line_sep else rb"\r\n|\n|\r")
        buffer = b""
        for chunk in chunks:
            buffer += chunk
            while (end := terminator.search(buffer)) is not None:
                line, buffer = buffer[: end.start()], buffer[end.end() :]
                if _is_header_line(line, comment):
                    return line
        return buffer if _is_header_line(buffer, comment) else b""

    def _option(self, name: str, default: str) -> str:
        """The contract's ``read_options[name]`` as Spark reads it.

        Spark keeps reader options in a case-insensitive map, so a contract
        may spell ``nullValue`` as ``nullvalue``; the loader must see the
        same value the scan will use.

        Parameters
        ----------
        name : str
            Spark option name, in any case.
        default : str
            Spark's default for the option.

        Returns
        -------
        str
            The contract's value under any spelling of ``name``, else
            ``default``.
        """
        options = {key.lower(): value for key, value in self.schema.read_options.items()}
        return options.get(name.lower(), default)

    def _read_layout(self, files: list[str]) -> DataFrame:
        """One header-based scan of files that share a header row.

        Parameters
        ----------
        files : list of str
            Paths whose header rows are identical.

        Returns
        -------
        pyspark.sql.DataFrame
            Contract columns plus ``SOURCE_FILE_COL``.
        """
        raw = (
            self.spark.read.options(
                **self._spark_options(header="true", inferSchema="false", enforceSchema="false")
            )
            .csv(files)
            .withColumn(SOURCE_FILE_COL, _source_file_name())
        )
        return self._project(raw)

    def _scan_positional(self, files: list[str], column_count: int) -> DataFrame:
        """Read ``files`` headerless, in one scan, as ``_c0`` .. ``_c<n-1>`` strings.

        One ``FileScan`` over every path (Spark packs the files into
        partitions by size) rather than one frame per file: a union of
        per-file frames re-analyses the whole plan on every ``unionByName``
        and ships a task binary proportional to the file count (~8 min of
        planning and a 45 MiB binary for the 1,608 JMA files). Header and
        metadata lines come back as ordinary rows — filter them out with a
        pattern on ``_c0``. The hidden ``SOURCE_FILE_COL`` holds each row's
        file name so a subclass can derive per-file values from it and
        :meth:`_validate` can report per file.

        Parameters
        ----------
        files : list of str
            Paths to read, all in the contract's ``read_options`` encoding.
        column_count : int
            Number of physical columns to expose; extra columns are ignored,
            missing ones read as null.

        Returns
        -------
        pyspark.sql.DataFrame
            ``_c0`` .. ``_c<column_count-1>`` (string) plus ``SOURCE_FILE_COL``.
        """
        spark_schema = StructType(
            [StructField(f"_c{i}", StringType()) for i in range(column_count)]
        )
        return (
            self.spark.read.options(**self._spark_options(header="false"))
            .schema(spark_schema)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, _source_file_name())
        )

    def _project(self, raw: DataFrame) -> DataFrame:
        """Cast ``raw`` to the contract's columns, keeping ``SOURCE_FILE_COL``.

        Parameters
        ----------
        raw : pyspark.sql.DataFrame
            Source-named columns (``_c<n>`` positions or header names, plus
            any injected ``__``-prefixed sources) and ``SOURCE_FILE_COL``.

        Returns
        -------
        pyspark.sql.DataFrame
            The contract columns in contract order, then ``SOURCE_FILE_COL``.
        """
        return raw.select(
            [self._cast(raw, c) for c in self.schema.columns] + [F.col(SOURCE_FILE_COL)]
        )

    def _cast(self, raw: DataFrame, column: CsvColumn) -> Column:
        # The source resolves to a physical column the way the session does
        # (case-insensitively by default), the same rule the header check
        # applied; an absent source reads as null. Source headers contain
        # characters Spark's name resolver would otherwise interpret — dots
        # as nested-field paths (ブロックNo.), parentheses — so the name is
        # backtick-quoted (a literal backtick is escaped by doubling it),
        # which both raw[name] and F.col(name) need.
        physical = self._resolve(column.source_name, raw.columns)
        if physical is not None:
            col = F.col("`" + physical.replace("`", "``") + "`")
        else:
            col = F.lit(None)
        if column.type == "date" and column.format:
            col = F.to_date(col, column.format)
        elif column.type == "timestamp" and column.format:
            col = F.to_timestamp(col, column.format)
        else:
            col = col.cast(column.type)
        return col.alias(column.name)

    def _validate(self, df: DataFrame) -> None:
        non_nullable = [c.name for c in self.schema.columns if not c.nullable]
        if non_nullable:
            # collect()[0] over first(): an aggregation always yields one row,
            # and unlike first() the element is not Optional.
            null_counts = df.select(
                [F.count(F.when(F.col(name).isNull(), True)).alias(name) for name in non_nullable]
            ).collect()[0]
            bad = {name: null_counts[name] for name in non_nullable if null_counts[name]}
            if bad:
                raise ValueError(
                    "Non-nullable columns contain nulls after casting "
                    f"(null count per column): {bad}{self._nulls_by_file(df, list(bad))}"
                )
        if self.schema.grain:
            total = df.count()
            distinct = df.select(self.schema.grain).distinct().count()
            if distinct != total:
                raise ValueError(
                    f"Grain {self.schema.grain} is not unique: "
                    f"{total} rows but {distinct} distinct keys{self._duplicates_by_file(df)}"
                )

    def _nulls_by_file(self, df: DataFrame, columns: list[str]) -> str:
        """Null counts of ``columns`` per source file, as an error-message suffix.

        Parameters
        ----------
        df : pyspark.sql.DataFrame
            The loaded frame; reported per file only if it carries
            ``SOURCE_FILE_COL``.
        columns : list of str
            Non-nullable contract columns that contain nulls.

        Returns
        -------
        str
            ``"; by file (first 10): {file: {column: nulls}}"`` for the first
            ``REPORT_LIMIT`` offending files (by name), or ``""``.
        """
        if SOURCE_FILE_COL not in df.columns:
            return ""
        rows = (
            df.groupBy(SOURCE_FILE_COL)
            .agg(*[F.count(F.when(F.col(c).isNull(), True)).alias(c) for c in columns])
            .filter(reduce(operator.or_, (F.col(c) > 0 for c in columns)))
            .orderBy(SOURCE_FILE_COL)
            .limit(REPORT_LIMIT)
            .collect()
        )
        by_file = {r[SOURCE_FILE_COL]: {c: r[c] for c in columns if r[c]} for r in rows}
        return f"; by file (first {REPORT_LIMIT}): {by_file}"

    def _duplicates_by_file(self, df: DataFrame) -> str:
        """The first duplicated grain keys and their files, as an error-message suffix.

        Parameters
        ----------
        df : pyspark.sql.DataFrame
            The loaded frame; reported only if it carries ``SOURCE_FILE_COL``.

        Returns
        -------
        str
            ``"; first 10 duplicated keys (key, rows, files): [...]"`` — one
            ``(key tuple, row count, sorted file names)`` per duplicated key,
            the first ``REPORT_LIMIT`` in key order — or ``""``.
        """
        if SOURCE_FILE_COL not in df.columns:
            return ""
        rows = (
            df.groupBy(*self.schema.grain)
            .agg(
                F.count(F.lit(1)).alias("_rows"),
                F.sort_array(F.collect_set(SOURCE_FILE_COL)).alias("_files"),
            )
            .filter(F.col("_rows") > 1)
            .orderBy(*self.schema.grain)
            .limit(REPORT_LIMIT)
            .collect()
        )
        duplicates = [
            (tuple(r[k] for k in self.schema.grain), r["_rows"], r["_files"]) for r in rows
        ]
        return f"; first {REPORT_LIMIT} duplicated keys (key, rows, files): {duplicates}"

    def _write(self, df: DataFrame) -> None:
        if "." in self.table:
            database = self.table.split(".")[0]
            self.spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
        df.write.mode("overwrite").format("parquet").saveAsTable(self.table)
