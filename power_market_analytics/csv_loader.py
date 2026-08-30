"""Generic full-reload loader from CSV files into Spark warehouse tables.

The source schema is declared in YAML (see ``conf/schemas/``) and parsed into
:class:`CsvTableSchema`. :class:`CsvLoader` reads every CSV file matching a
path, applies the schema (rename to canonical column names, cast, validate),
and overwrites the destination table.
"""

from __future__ import annotations

import glob
import operator
from functools import reduce
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, Field
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
        listed here are dropped.
    """

    description: str | None = None
    read_options: dict[str, str] = Field(default_factory=dict)
    grain: list[str] = Field(default_factory=list)
    columns: list[CsvColumn]

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

        The default unions one :meth:`_read_file` frame per file, which is
        only fit for a handful of files: every ``unionByName`` re-analyses
        the whole growing plan and each task deserialises a binary that grows
        with the file count (1,600 files ≈ 8 min of planning, a 45 MiB task
        binary and hours per load). Loaders with hundreds of files override
        this to build a single frame — :meth:`_scan_positional` +
        :meth:`_project` for positional layouts (JMA), one ``createDataFrame``
        over Python-parsed rows (TEPCO でんき予報).

        Parameters
        ----------
        files : list of str
            Resolved file paths, as returned by :meth:`_resolve_files`.

        Returns
        -------
        pyspark.sql.DataFrame
        """
        return reduce(DataFrame.unionByName, (self._read_file(f) for f in files))

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
            self.spark.read.options(**self.schema.read_options)
            .schema(spark_schema)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, F.col("_metadata.file_name"))
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

    def _read_file(self, file: str) -> DataFrame:
        raw = self.spark.read.options(header="true", **self.schema.read_options).csv(file)
        present = set(raw.columns)
        missing = [
            c.source_name
            for c in self.schema.columns
            if c.required and c.source_name not in present
        ]
        if missing:
            raise ValueError(f"{file} is missing required columns: {missing}")
        return raw.select([self._cast(raw, c) for c in self.schema.columns])

    @staticmethod
    def _cast(raw: DataFrame, column: CsvColumn) -> Column:
        # Source headers contain characters Spark's name resolver would
        # otherwise interpret — dots as nested-field paths (ブロックNo.),
        # parentheses — so the name is backtick-quoted (a literal backtick is
        # escaped by doubling it), which both raw[name] and F.col(name) need.
        if column.source_name in raw.columns:
            col = F.col("`" + column.source_name.replace("`", "``") + "`")
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
