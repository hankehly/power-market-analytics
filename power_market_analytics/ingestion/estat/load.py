"""Full reload of the extracted mesh text files into a raw warehouse table.

The vintage is taken from each file name; files are read one Spark scan per
exact header line, that vintage's population column is selected, the vintage
attributes are injected, and mesh codes, population and ``HTKSYORI`` are
validated per file in one grouped pass before any cast.
"""

from __future__ import annotations

import glob
import re
from functools import reduce
from pathlib import Path

from loguru import logger
from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

from power_market_analytics.ingestion.estat.mesh import MESH_CODE_RE
from power_market_analytics.ingestion.estat.vintages import (
    VINTAGES,
    CensusVintage,
)
from power_market_analytics.ingestion.loader import SOURCE_FILE_COL, CsvLoader, CsvTableSchema

#: Python codec equivalent of the ``windows-31j`` Java charset used by the
#: Spark reader (e-Stat serves Shift_JIS with Windows extensions).
_SNIFF_ENCODING = "cp932"

#: ``tbl{statsId}H{primary mesh}.txt`` — the archive member name e-Stat uses.
_FILENAME_RE = re.compile(r"tbl(?P<stats_id>[A-Za-z0-9]+)H(?P<code>\d{4})\.txt$")

#: Contract ``source`` names of the columns the loader injects per file.
CENSUS_YEAR_SOURCE = "__census_year"
CENSUS_DATE_SOURCE = "__census_date"
GEODETIC_DATUM_SOURCE = "__geodetic_datum"
STATS_ID_SOURCE = "__stats_id"
PRIMARY_MESH_CODE_SOURCE = "__primary_mesh_code"
POPULATION_SOURCE = "__population_total"
SOURCE_FILE_SOURCE = "__source_file"

#: Physical headers the loader reads by name in every vintage.
KEY_CODE = "KEY_CODE"
PRIVACY_CODE = "HTKSYORI"
_ACCEPTED_PRIVACY_CODES = ("0", "1", "2")
_POPULATION_RE = r"^\d+$"

#: How many offending values an error message quotes.
_EXAMPLE_LIMIT = 5


class EstatCensusMeshCsvLoader(CsvLoader):
    """Vintage-aware full reload of census population-mesh text files.

    Works like :class:`~power_market_analytics.ingestion.loader.CsvLoader` (same
    validation and write behaviour) except for how files are found and read;
    see the module docstring. The contract's ``source`` fields are the shared
    physical headers (``KEY_CODE``, ``HTKSYORI``, ``HTKSAKI``, ``GASSAN``)
    plus the injected ``__census_year``, ``__census_date``,
    ``__geodetic_datum``, ``__stats_id``, ``__primary_mesh_code``,
    ``__population_total`` and ``__source_file``.

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`CsvLoader`. A directory ``filepath`` is the downloader's
        root (``{year}/txt/*.txt`` underneath); a glob pattern or single file
        also works.
    vintages : tuple of CensusVintage, optional
        Census configurations to recognise, keyed by ``stats_id``. Defaults to
        :data:`VINTAGES`.
    """

    def __init__(
        self,
        schema: CsvTableSchema,
        filepath: Path | str,
        table: str,
        spark: SparkSession | None = None,
        vintages: tuple[CensusVintage, ...] | None = None,
    ) -> None:
        self.vintages = VINTAGES if vintages is None else vintages
        super().__init__(schema=schema, filepath=filepath, table=table, spark=spark)

    def _resolve_files(self) -> list[str]:
        if self.filepath.is_dir():
            files = sorted(str(p) for p in self.filepath.glob("*/txt/*.txt"))
        else:
            files = sorted(glob.glob(str(self.filepath)))
        if not files:
            raise FileNotFoundError(f"No census mesh text files found at {self.filepath}")
        return files

    def _read_all(self, files: list[str]) -> DataFrame:
        names = [Path(file).name for file in files]
        if len(set(names)) != len(names):
            clash = next(name for name in names if names.count(name) > 1)
            raise ValueError(
                f"{names.count(clash)} files share the file name {clash}: the primary mesh "
                "code is joined back on the name, so every file name must be unique"
            )
        # One scan per (vintage, exact header line): Spark applies the first
        # file's header to every file of a multi-path read, so files whose
        # columns are ordered differently must not share a scan.
        groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        vintages: dict[str, CensusVintage] = {}
        codes: dict[str, str] = {}
        for file in files:
            vintage, primary_mesh_code = self._identify(file)
            header = self._check_headers(file, vintage)
            groups.setdefault((vintage.stats_id, tuple(header)), []).append(file)
            vintages[vintage.stats_id] = vintage
            codes[Path(file).name] = primary_mesh_code
        frames = [
            self._read_group(vintages[stats_id], group, codes)
            for (stats_id, _), group in groups.items()
        ]
        return reduce(DataFrame.unionByName, frames)

    def _read_group(
        self, vintage: CensusVintage, files: list[str], codes: dict[str, str]
    ) -> DataFrame:
        """Read files that share ``vintage`` and a header line in one scan.

        Parameters
        ----------
        vintage : CensusVintage
            The census the files belong to (population column, attributes).
        files : list of str
            Paths with identical header lines.
        codes : dict of str to str
            File name → primary mesh code, for every file.

        Returns
        -------
        pyspark.sql.DataFrame
            Contract columns plus ``SOURCE_FILE_COL``, rows validated.
        """
        lookup = self.spark.createDataFrame(
            [(Path(file).name, codes[Path(file).name]) for file in files],
            f"{SOURCE_FILE_COL} string, {PRIMARY_MESH_CODE_SOURCE} string",
        )
        raw = (
            self.spark.read.options(header="true", **self.schema.read_options)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, F.col("_metadata.file_name"))
            .join(F.broadcast(lookup), SOURCE_FILE_COL, "inner")
        )
        self._check_rows(raw, vintage)
        data = (
            raw.filter(F.col(KEY_CODE).isNotNull())
            .withColumn(CENSUS_YEAR_SOURCE, F.lit(vintage.census_year))
            .withColumn(CENSUS_DATE_SOURCE, F.lit(vintage.census_date))
            .withColumn(GEODETIC_DATUM_SOURCE, F.lit(vintage.geodetic_datum))
            .withColumn(STATS_ID_SOURCE, F.lit(vintage.stats_id))
            .withColumn(POPULATION_SOURCE, F.col(vintage.population_source_column))
            .withColumn(SOURCE_FILE_SOURCE, F.col(SOURCE_FILE_COL))
        )
        return self._project(data)

    def _identify(self, file: str) -> tuple[CensusVintage, str]:
        """Return the vintage and primary mesh code encoded in a file name.

        Raises
        ------
        ValueError
            If the name is not ``tbl{statsId}H{code}.txt`` or the ``statsId``
            has no configured vintage.
        """
        match = _FILENAME_RE.search(file)
        if match is None:
            raise ValueError(f"{file}: cannot parse a statsId and primary mesh code from the name")
        stats_id = match["stats_id"]
        for vintage in self.vintages:
            if vintage.stats_id == stats_id:
                return vintage, match["code"]
        raise ValueError(
            f"{file}: no census vintage configured for statsId {stats_id} "
            f"(configured: {[v.stats_id for v in self.vintages]})"
        )

    def _physical_columns(self) -> list[str]:
        return [c.source_name for c in self.schema.columns if not c.source_name.startswith("__")]

    def _check_headers(self, file: str, vintage: CensusVintage) -> list[str]:
        """Verify the two header rows before Spark reads the file.

        Returns
        -------
        list of str
            The header row (source codes), for grouping files by layout.

        Raises
        ------
        ValueError
            If the header lacks a physical contract column or the vintage's
            population column, if the second line is not the label row
            (empty code columns), or if the file has no data rows.
        """
        with open(file, encoding=_SNIFF_ENCODING) as f:
            header = f.readline().rstrip("\r\n").split(",")
            label_row = f.readline().rstrip("\r\n").split(",")
            has_data = bool(f.readline())
        missing = [c for c in self._physical_columns() if c not in header]
        if missing:
            raise ValueError(f"{file} is missing required columns: {missing} (header {header!r})")
        if vintage.population_source_column not in header:
            raise ValueError(
                f"{file}: population column {vintage.population_source_column} of census "
                f"{vintage.census_year} is absent (header {header!r})"
            )
        positions = [header.index(c) for c in self._physical_columns()]
        if len(label_row) != len(header) or any(label_row[i] != "" for i in positions):
            raise ValueError(
                f"{file}: line 2 is not the label row (empty {self._physical_columns()} "
                f"under the Japanese labels), got {label_row[:6]!r}"
            )
        if not has_data:
            raise ValueError(f"{file}: no data rows after the two header rows")
        return header

    def _check_rows(self, raw: DataFrame, vintage: CensusVintage) -> None:
        """Validate every data row of a scan, reporting per file.

        Parameters
        ----------
        raw : pyspark.sql.DataFrame
            A header-based scan carrying ``SOURCE_FILE_COL`` and
            ``PRIMARY_MESH_CODE_SOURCE``.
        vintage : CensusVintage
            Supplies the population column to check.

        Raises
        ------
        ValueError
            Named after the first offending file: a malformed mesh code, a
            mesh code outside that file's primary mesh, a population that is
            not a non-negative integer literal (``*`` included), ``HTKSYORI``
            outside 0/1/2, or a number of rows without a ``KEY_CODE`` other
            than exactly one (the label row).
        """
        key = F.col(KEY_CODE)
        population = F.col(vintage.population_source_column)
        privacy = F.col(PRIVACY_CODE)
        checks: list[tuple[str, Column]] = [
            ("mesh code", key.isNotNull() & ~key.rlike(MESH_CODE_RE.pattern)),
            (
                "mesh code outside primary mesh {code}",
                key.isNotNull() & ~key.startswith(F.col(PRIMARY_MESH_CODE_SOURCE)),
            ),
            (
                f"population ({vintage.population_source_column}) not a non-negative integer",
                key.isNotNull() & (population.isNull() | ~population.rlike(_POPULATION_RE)),
            ),
            (
                f"{PRIVACY_CODE} not in {list(_ACCEPTED_PRIVACY_CODES)}",
                key.isNotNull() & ~privacy.isin(*_ACCEPTED_PRIVACY_CODES),
            ),
        ]
        counts = (
            raw.groupBy(SOURCE_FILE_COL, PRIMARY_MESH_CODE_SOURCE)
            .agg(
                F.count(F.when(key.isNull(), True)).alias("__label_rows"),
                *[
                    F.count(F.when(cond, True)).alias(f"__c{i}")
                    for i, (_, cond) in enumerate(checks)
                ],
            )
            .orderBy(SOURCE_FILE_COL)
            .collect()
        )
        for row in counts:
            file = row[SOURCE_FILE_COL]
            if row["__label_rows"] != 1:
                raise ValueError(
                    f"{file}: expected exactly one label row without a KEY_CODE, found "
                    f"{row['__label_rows']} rows with an empty KEY_CODE"
                )
            for i, (label, cond) in enumerate(checks):
                n_bad = row[f"__c{i}"]
                if n_bad:
                    examples = [
                        (r[KEY_CODE], r[PRIVACY_CODE], r[vintage.population_source_column])
                        for r in raw.filter((F.col(SOURCE_FILE_COL) == file) & cond)
                        .select(KEY_CODE, PRIVACY_CODE, vintage.population_source_column)
                        .limit(_EXAMPLE_LIMIT)
                        .collect()
                    ]
                    raise ValueError(
                        f"{file}: {n_bad} row(s) with "
                        f"{label.format(code=row[PRIMARY_MESH_CODE_SOURCE])}; first "
                        f"(KEY_CODE, HTKSYORI, population): {examples}"
                    )
        logger.debug("{}: header and row checks passed", [r[SOURCE_FILE_COL] for r in counts])
