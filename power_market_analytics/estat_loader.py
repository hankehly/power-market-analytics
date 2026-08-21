"""Loader for e-Stat census 500 m population-mesh text files into a raw warehouse table.

Each ``tbl{statsId}H{primary}.txt`` (format in
docs/eStat-Census-Population-Mesh-Retrieval.md) is a CP932 comma-separated
table with two header rows — the source codes (``KEY_CODE,HTKSYORI,HTKSAKI,
GASSAN,T000847001,…``) and then Japanese labels — followed by one row per
nine-digit mesh. Only the total-population column is loaded, and its header
differs per census (``T000847001`` in 2015, ``T001101001`` in 2020), so
:class:`EstatCensusMeshCsvLoader` identifies each file's vintage from its
name (``statsId`` → :data:`~power_market_analytics.estat.VINTAGES`), selects
that vintage's population column, injects the vintage attributes and the
file's primary mesh code, and validates every row before casting: total
population must be a non-negative integer (it is never ``*``-suppressed),
mesh codes must be well-formed and lie inside the file's primary mesh, and
``HTKSYORI`` must be 0, 1 or 2. Anything else fails the load before writing.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

from loguru import logger
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from power_market_analytics.csv_loader import CsvLoader, CsvTableSchema
from power_market_analytics.estat import (
    MESH_CODE_RE,
    VINTAGES,
    CensusVintage,
)

__all__ = ["EstatCensusMeshCsvLoader"]

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

    Works like :class:`~power_market_analytics.csv_loader.CsvLoader` (same
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
        :data:`~power_market_analytics.estat.VINTAGES`.
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

    def _read_file(self, file: str) -> DataFrame:
        vintage, primary_mesh_code = self._identify(file)
        self._check_headers(file, vintage)
        raw = self.spark.read.options(header="true", **self.schema.read_options).csv(file)
        self._check_rows(raw, file, vintage, primary_mesh_code)
        data = (
            raw.filter(F.col(KEY_CODE).isNotNull())
            .withColumn(CENSUS_YEAR_SOURCE, F.lit(vintage.census_year))
            .withColumn(CENSUS_DATE_SOURCE, F.lit(vintage.census_date))
            .withColumn(GEODETIC_DATUM_SOURCE, F.lit(vintage.geodetic_datum))
            .withColumn(STATS_ID_SOURCE, F.lit(vintage.stats_id))
            .withColumn(PRIMARY_MESH_CODE_SOURCE, F.lit(primary_mesh_code))
            .withColumn(POPULATION_SOURCE, F.col(vintage.population_source_column))
            .withColumn(SOURCE_FILE_SOURCE, F.lit(Path(file).name))
        )
        return data.select([self._cast(data, c) for c in self.schema.columns])

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

    def _check_headers(self, file: str, vintage: CensusVintage) -> None:
        """Verify the two header rows before Spark reads the file.

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

    def _check_rows(
        self, raw: DataFrame, file: str, vintage: CensusVintage, primary_mesh_code: str
    ) -> None:
        """Validate every data row of one file before casting.

        Raises
        ------
        ValueError
            If any mesh code is malformed or outside ``primary_mesh_code``,
            the population is not a non-negative integer literal (this
            includes ``*``), ``HTKSYORI`` is not 0/1/2, or the number of
            rows without a ``KEY_CODE`` is not exactly one (the label row).
        """
        key = F.col(KEY_CODE)
        population = F.col(vintage.population_source_column)
        privacy = F.col(PRIVACY_CODE)
        checks = {
            "mesh code": key.isNotNull() & ~key.rlike(MESH_CODE_RE.pattern),
            f"mesh code outside primary mesh {primary_mesh_code}": key.isNotNull()
            & ~key.startswith(primary_mesh_code),
            f"population ({vintage.population_source_column}) not a non-negative integer": (
                key.isNotNull() & (population.isNull() | ~population.rlike(_POPULATION_RE))
            ),
            f"{PRIVACY_CODE} not in {list(_ACCEPTED_PRIVACY_CODES)}": key.isNotNull()
            & ~privacy.isin(*_ACCEPTED_PRIVACY_CODES),
        }
        # collect()[0] over first(): an aggregation always yields one row,
        # and unlike first() the element is not Optional.
        counts = raw.agg(
            F.count(F.when(key.isNull(), True)).alias("__label_rows"),
            *[
                F.count(F.when(cond, True)).alias(f"__c{i}")
                for i, cond in enumerate(checks.values())
            ],
        ).collect()[0]
        if counts["__label_rows"] != 1:
            raise ValueError(
                f"{file}: expected exactly one label row without a KEY_CODE, found "
                f"{counts['__label_rows']} rows with an empty KEY_CODE"
            )
        for i, (label, cond) in enumerate(checks.items()):
            n_bad = counts[f"__c{i}"]
            if n_bad:
                examples = [
                    (r[KEY_CODE], r[PRIVACY_CODE], r[vintage.population_source_column])
                    for r in raw.filter(cond)
                    .select(KEY_CODE, PRIVACY_CODE, vintage.population_source_column)
                    .limit(_EXAMPLE_LIMIT)
                    .collect()
                ]
                raise ValueError(
                    f"{file}: {n_bad} row(s) with {label}; first "
                    f"(KEY_CODE, HTKSYORI, population): {examples}"
                )
        logger.debug("{}: header and row checks passed", file)
