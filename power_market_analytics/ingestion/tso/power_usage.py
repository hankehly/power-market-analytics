"""TSO でんき予報 過去の電力使用実績 (hourly 電力使用状況) — the shared parser and loader.

Every 一般送配電事業者 publishes its でんき予報 demand history as one zip per
month of daily multi-section CSVs (CP932): an ``UPDATE`` stamp, headline
blocks (ピーク時供給力, 予想最大電力, …), the 24-row **hourly** table — the
1時間平均 demand of each hour in 万kW with the TSO's forecast, 使用率 and,
in later layouts, supply capacity — and after it a 5-minute table. TEPCO and
Kansai publish the same four hourly measures in that shape; only the column
names (per TSO and per era, hence a source's ``accepted_headers``), the URLs
and extra packagings such as TEPCO's yearly files differ, and those live in
each TSO's :class:`PowerUsageSource` (``power_market_analytics.ingestion.tso.tepco.power_usage``,
``power_market_analytics.ingestion.tso.kansai.power_usage``). Only the hourly table is
ingested; the 5-minute table is parsed past.

Two rules cover the quirks seen in the archives. Every line is read with its
trailing commas removed: files re-saved from Excel pad every line to the
widest row (``,,,,,`` where a blank line belongs, a header ending in a comma)
and without the rule the parser would read past the hourly table. A row
whose first field is ``修正後`` corrects the row above it: its non-blank
measures replace the original's, blank ones keep it (Kansai 2016-04-24 00:00,
1267 → 1212 万kW, ``システム不具合による数値誤りのため修正``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.ingestion.loader import SOURCE_FILE_COL, CsvLoader, CsvTableSchema
from power_market_analytics.ingestion.tso.area_actuals import AreaActualsSource

__all__ = [
    "CORRECTION_MARKER",
    "HourlyFile",
    "HourlyRow",
    "PowerUsageCsvLoader",
    "PowerUsageSource",
    "parse_hourly",
]

#: First field of a row that corrects the hourly row above it.
CORRECTION_MARKER = "修正後"

_ENCODING = "cp932"
_UPDATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2}) UPDATE$")
_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class PowerUsageSource(AreaActualsSource):
    """Where and how a TSO publishes its でんき予報 hourly archive.

    Everything of :class:`~power_market_analytics.ingestion.tso.area_actuals.AreaActualsSource`
    applies — the monthly zips go through the shared
    :class:`~power_market_analytics.ingestion.tso.area_actuals.AreaActualsDownloader` — with
    ``accepted_headers`` naming the hourly table's header line in every
    layout the TSO has used.

    Attributes
    ----------
    multi_day_headers : frozenset of str, default empty
        Accepted headers whose files may hold many delivery dates (TEPCO's
        yearly ``juyo-YYYY.csv``). A file under any other header must hold
        exactly one date: a daily member spanning two dates is corrupt.
    """

    multi_day_headers: frozenset[str] = frozenset()


class HourlyRow(NamedTuple):
    """One hour of the hourly table, values still as published (strings).

    Attributes
    ----------
    target_date : str
        Delivery date as ``yyyyMMdd``.
    hour_start : int
        Hour the value covers, 0–23 (``TIME`` ``h:00`` = hour ``h``–``h+1``).
    demand : str
        ``実績`` / ``当日実績`` in 万kW (1時間平均).
    forecast, usage_rate, supply_capacity : str or None
        The TSO's forecast (万kW), ``使用率(%)`` and supply capacity (万kW);
        None where the layout has no such column (TEPCO's yearly files carry
        the actual only; Kansai adds the capacity on 2019-09-12).
    """

    target_date: str
    hour_start: int
    demand: str
    forecast: str | None
    usage_rate: str | None
    supply_capacity: str | None


class HourlyFile(NamedTuple):
    """The hourly table of one source file plus its metadata.

    Attributes
    ----------
    file_updated_at : str
        The ``UPDATE`` stamp as ``yyyyMMdd HH:mm:ss``.
    header : str
        The accepted column-header line the rows were read under.
    rows : list of HourlyRow
    """

    file_updated_at: str
    header: str
    rows: list[HourlyRow]


def _read_lines(file: Path | str) -> list[str]:
    """The file's lines without their terminators and trailing commas."""
    with open(file, encoding=_ENCODING) as f:
        return [line.rstrip("\r\n").rstrip(",") for line in f]


def _parse_update_stamp(file: Path | str, line: str) -> str:
    match = _UPDATE_RE.match(line)
    if match is None:
        raise ValueError(f"{file}: first line {line!r} is not a '<yyyy/M/d H:mm> UPDATE' stamp")
    year, month, day, hour, minute = match.groups()
    return f"{year}{int(month):02d}{int(day):02d} {int(hour):02d}:{minute}:00"


def _parse_row(file: Path | str, line: str, header: str) -> HourlyRow:
    fields = line.split(",")
    expected = header.count(",") + 1
    if len(fields) != expected:
        raise ValueError(f"{file}: row {line!r} has {len(fields)} fields, expected {expected}")
    date_match = _DATE_RE.match(fields[0])
    if date_match is None:
        raise ValueError(f"{file}: row {line!r} does not start with a yyyy/M/d date")
    year, month, day = date_match.groups()
    time_match = _TIME_RE.match(fields[1])
    if time_match is None or time_match.group(2) != "00" or not 0 <= int(time_match.group(1)) <= 23:
        raise ValueError(f"{file}: row {line!r} is not on the hour (TIME {fields[1]!r})")
    # Every layout starts DATE,TIME,<actual>; the forecast, 使用率 and supply
    # capacity follow where the layout has them.
    extras: list[str | None] = [field.strip() for field in fields[3:]]
    extras += [None] * (3 - len(extras))
    return HourlyRow(
        target_date=f"{year}{int(month):02d}{int(day):02d}",
        hour_start=int(time_match.group(1)),
        demand=fields[2].strip(),
        forecast=extras[0],
        usage_rate=extras[1],
        supply_capacity=extras[2],
    )


def _apply_correction(row: HourlyRow, line: str, header: str) -> HourlyRow:
    """Merge a ``修正後`` row into the hourly row above it.

    The correction row repeats the table's columns — blank ``DATE`` and
    ``TIME``, then a value under each measure that changes — and may carry
    the reason after the header's last column, which is ignored.
    """
    expected = header.count(",") + 1
    fields = [field.strip() for field in line.split(",")[2:expected]]
    fields += [""] * (expected - 2 - len(fields))
    demand, *extras = fields
    extras += [""] * (3 - len(extras))
    return HourlyRow(
        target_date=row.target_date,
        hour_start=row.hour_start,
        demand=demand or row.demand,
        forecast=extras[0] or row.forecast,
        usage_rate=extras[1] or row.usage_rate,
        supply_capacity=extras[2] or row.supply_capacity,
    )


def parse_hourly(file: Path | str, source: PowerUsageSource) -> HourlyFile:
    """Read the hourly table out of one でんき予報 file.

    Every line is read with its trailing commas removed. The first line must
    be the ``UPDATE`` stamp. The hourly table is the block under the first
    line that equals one of ``source.accepted_headers``; it ends at the first
    blank line, so the 5-minute table that follows it is never read. A row
    whose first field is :data:`CORRECTION_MARKER` corrects the row above it.

    Parameters
    ----------
    file : pathlib.Path or str
        Path to a daily member or a multi-day file (CP932).
    source : PowerUsageSource
        Supplies the accepted headers and the multi-day headers.

    Returns
    -------
    HourlyFile

    Raises
    ------
    ValueError
        If the stamp is missing, no accepted header is found, the block is
        empty, a row is malformed (field count after the trailing-comma
        strip, date, or a TIME that is not on the hour), a ``修正後`` row has
        no row above it, a day does not cover hours 0–23 exactly once, or a
        file under a header outside ``source.multi_day_headers`` holds more
        than one target date.
    """
    lines = _read_lines(file)
    if not lines:
        raise ValueError(f"{file}: empty file, expected an UPDATE stamp on the first line")
    file_updated_at = _parse_update_stamp(file, lines[0])
    accepted = source.accepted_headers
    header_index = next((i for i, line in enumerate(lines) if line in accepted), None)
    if header_index is None:
        raise ValueError(
            f"{file}: no accepted hourly header line found — expected one of "
            f"{sorted(accepted)!r} (did the TSO change the layout?)"
        )
    header = lines[header_index]
    rows: list[HourlyRow] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            break
        if line.split(",", 1)[0].strip() == CORRECTION_MARKER:
            if not rows:
                raise ValueError(f"{file}: correction row {line!r} has no hourly row above it")
            rows[-1] = _apply_correction(rows[-1], line, header)
            continue
        rows.append(_parse_row(file, line, header))
    if not rows:
        raise ValueError(f"{file}: no hourly rows under the header {header!r}")
    _check_complete(file, header, rows, source)
    return HourlyFile(file_updated_at=file_updated_at, header=header, rows=rows)


def _check_complete(
    file: Path | str, header: str, rows: list[HourlyRow], source: PowerUsageSource
) -> None:
    """Require every day in the block to carry hours 0–23 exactly once.

    A truncated file that still ends in a well-formed row and a blank line
    would otherwise load as a day with absent hours — a gap the grain
    uniqueness check downstream cannot see. A file whose header is not one
    of the source's multi-day headers must also hold a single target date.
    """
    hours_by_date: dict[str, list[int]] = {}
    for row in rows:
        hours_by_date.setdefault(row.target_date, []).append(row.hour_start)
    if header not in source.multi_day_headers and len(hours_by_date) != 1:
        raise ValueError(
            f"{file}: a daily file must hold exactly one target date, found {sorted(hours_by_date)}"
        )
    for target_date, hours in hours_by_date.items():
        if sorted(hours) != list(range(24)):
            raise ValueError(
                f"{file}: {target_date} does not cover hours 0-23 exactly once "
                f"(got {sorted(hours)}) — truncated or duplicated block?"
            )


#: Contract ``source`` names of the columns the loader hands to the contract
#: (``__``-prefixed: they are emitted by the parser, not read from a header).
TARGET_DATE_SOURCE = "__target_date"
HOUR_START_SOURCE = "__hour_start"
DEMAND_SOURCE = "__demand_mankw"
FORECAST_SOURCE = "__forecast_mankw"
USAGE_RATE_SOURCE = "__usage_rate_pct"
SUPPLY_CAPACITY_SOURCE = "__supply_capacity_mankw"
FILE_UPDATED_AT_SOURCE = "__file_updated_at"
SOURCE_FILE_SOURCE = "__source_file"
_SOURCE_COLUMNS = (
    TARGET_DATE_SOURCE,
    HOUR_START_SOURCE,
    DEMAND_SOURCE,
    FORECAST_SOURCE,
    USAGE_RATE_SOURCE,
    SUPPLY_CAPACITY_SOURCE,
    FILE_UPDATED_AT_SOURCE,
    SOURCE_FILE_SOURCE,
)


class PowerUsageCsvLoader(CsvLoader):
    """Full reload of でんき予報 hourly tables into a warehouse table.

    Works like :class:`~power_market_analytics.ingestion.loader.CsvLoader` (same
    validation and write behaviour) except for how each file is read: the
    files are multi-section, so :func:`parse_hourly` extracts the hourly
    table in Python and the contract addresses the parsed values by the
    ``__``-prefixed source names above. Every file's rows land in one
    ``createDataFrame`` (~1,600 daily files as separate frames unioned
    together gave Spark 16k tasks per action). A subclass may drop rows per
    file through :meth:`_file_rows` (TEPCO drops the yearly rows its daily
    files cover).

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`CsvLoader`; ``filepath`` is the ``csv/`` folder of the
        extracted daily files.
    source : PowerUsageSource, optional
        The TSO spec (accepted and multi-day headers). Subclasses may fix it
        via the ``source`` class attribute instead.
    """

    #: Default source for subclasses (e.g. ``KansaiPowerUsageCsvLoader.source``).
    source: PowerUsageSource | None = None

    def __init__(
        self,
        schema: CsvTableSchema,
        filepath: Path | str,
        table: str,
        spark: SparkSession | None = None,
        source: PowerUsageSource | None = None,
    ) -> None:
        resolved = source if source is not None else type(self).source
        if resolved is None:
            raise ValueError("PowerUsageCsvLoader needs a source (argument or class attribute)")
        self._source = resolved
        super().__init__(schema=schema, filepath=filepath, table=table, spark=spark)

    def _read_all(self, files: list[str]) -> DataFrame:
        return self._frame(self._rows(files))

    def _file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]:
        """The rows of one parsed file to load — all of them unless a subclass says otherwise.

        Parameters
        ----------
        file : str
            Path of the parsed file (for log messages).
        parsed : HourlyFile
            Its hourly table.

        Returns
        -------
        list of HourlyRow
        """
        return parsed.rows

    def _rows(self, files: list[str]) -> list[tuple[str | None, ...]]:
        """Parse the hourly tables of ``files`` into contract-source string tuples."""
        data: list[tuple[str | None, ...]] = []
        for file in files:
            parsed = parse_hourly(file, self._source)
            source_file = Path(file).name
            data.extend(
                (
                    row.target_date,
                    str(row.hour_start),
                    row.demand,
                    row.forecast,
                    row.usage_rate,
                    row.supply_capacity,
                    parsed.file_updated_at,
                    source_file,
                )
                for row in self._file_rows(file, parsed)
            )
        return data

    def _frame(self, data: list[tuple[str | None, ...]]) -> DataFrame:
        """Build the contract-typed DataFrame from parsed string tuples.

        The parsed file name doubles as the hidden ``SOURCE_FILE_COL`` so
        that validation failures name the offending files, as for the
        Spark-scanned loaders.
        """
        spark_schema = StructType([StructField(name, StringType()) for name in _SOURCE_COLUMNS])
        raw = self.spark.createDataFrame(data, spark_schema)
        return self._project(raw.withColumn(SOURCE_FILE_COL, F.col(SOURCE_FILE_SOURCE)))
