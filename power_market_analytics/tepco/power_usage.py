"""TEPCO でんき予報 過去の電力使用実績 (Tokyo-area hourly 電力使用状況) archive spec.

TEPCO Power Grid publishes the でんき予報 demand history on
https://www.tepco.co.jp/forecast/html/download-j.html in two packagings:

* **2016-04-01 → 2022-03-31**: one CP932 CSV per calendar year
  (``juyo-YYYY.csv``, 2016 … 2022) holding only the hourly actual
  ``DATE,TIME,実績(万kW)`` — the 1時間平均 demand of each hour, in 万kW.
* **2022-04-01 → today**: one zip per month
  (``YYYYMM_power_usage.zip``) of daily multi-section CSVs
  (``YYYYMMDD_power_usage.csv``): an ``UPDATE`` stamp, headline blocks
  (ピーク時供給力, 予想最大電力, …), the 24-row **hourly** table
  ``DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)`` and, after it,
  the 288-row **5-minute** table (``当日実績(５分間隔値)``, 太陽光 columns).

Only the hourly table is ingested; the 5-minute table is a separate 速報
measurement and is skipped. The yearly 2022 file also carries April–December
2022, which the daily files cover too, so yearly rows on or after
:data:`DAILY_FILES_FROM` are dropped at load time and the daily files win.
Format, quirks and the comparison against the A-1 series
(``tepco_area_demand_generation_actual``) are documented in
docs/TEPCO-Power-Usage-Retrieval.md.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import NamedTuple

import requests
from loguru import logger
from pyspark.sql import DataFrame
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.area_actuals import (
    AreaActualsDownloader,
    AreaActualsDownloadError,
    AreaActualsSource,
)
from power_market_analytics.csv_loader import CsvLoader

__all__ = [
    "DAILY_FILES_FROM",
    "DAILY_HOURLY_HEADER",
    "TEPCO_POWER_USAGE",
    "YEARLY_HEADER",
    "YEARLY_URL_TEMPLATE",
    "YEARLY_YEARS",
    "HourlyFile",
    "HourlyRow",
    "TepcoPowerUsageCsvLoader",
    "TepcoPowerUsageDownloader",
    "parse_hourly",
]

#: Column-header line of the hourly table in the daily files (2022-04 →).
DAILY_HOURLY_HEADER = "DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)"
#: Column-header line of the yearly files (2016 … 2022).
YEARLY_HEADER = "DATE,TIME,実績(万kW)"

#: First delivery day covered by the daily files; yearly rows from this day
#: on are dropped so the two packagings never overlap in the warehouse.
DAILY_FILES_FROM = datetime.date(2022, 4, 1)
#: ``str.format`` template of a yearly file's URL (``{year}``).
YEARLY_URL_TEMPLATE = "https://www.tepco.co.jp/forecast/html/images/juyo-{year}.csv"
#: Calendar years published as yearly files (the 2022 file runs to December).
YEARLY_YEARS = range(2016, 2023)
#: First day of the published history (the 2016 file starts here, not on Jan 1).
HISTORY_START = datetime.date(2016, 4, 1)


def expected_yearly_dates(year: int) -> list[str]:
    """Every delivery date a yearly file must cover, as ``yyyyMMdd``.

    The whole calendar year, except that 2016 starts at :data:`HISTORY_START`.

    Parameters
    ----------
    year : int
        Calendar year of the ``juyo-YYYY.csv`` file.

    Returns
    -------
    list of str
    """
    first = max(datetime.date(year, 1, 1), HISTORY_START)
    last = datetime.date(year, 12, 31)
    return [
        (first + datetime.timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range((last - first).days + 1)
    ]


TEPCO_POWER_USAGE = AreaActualsSource(
    code="tepco_power_usage",
    url_template="https://www.tepco.co.jp/forecast/html/images/{year:04d}{month:02d}_power_usage.zip",
    #: First monthly archive; the daily files start with it.
    earliest_month=(2022, 4),
    #: One member per day; members are flat (the day's 5-minute rows live in
    #: the same file, below the hourly table).
    member_re=re.compile(r"\d{8}_power_usage\.csv$"),
    accepted_headers=frozenset({DAILY_HOURLY_HEADER, YEARLY_HEADER}),
    default_data_dir="data/tepco/power_usage",
)

_ENCODING = "cp932"
_UPDATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2}) UPDATE$")
_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


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
        ``予測値(万kW)``, ``使用率(%)``, ``供給力(万kW)`` — daily files only;
        None for the yearly layout.
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
    # The yearly layout has the actual only; the daily layout adds three more.
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


def parse_hourly(file: Path | str) -> HourlyFile:
    """Read the hourly table out of a yearly or daily でんき予報 file.

    The file's first line must be the ``UPDATE`` stamp. The hourly table is
    the block under the first line that equals one of the source's accepted
    headers; it ends at the first blank line, so the 5-minute table that
    follows it in the daily files is never read.

    Parameters
    ----------
    file : pathlib.Path or str
        Path to a ``juyo-YYYY.csv`` or ``YYYYMMDD_power_usage.csv`` (CP932).

    Returns
    -------
    HourlyFile

    Raises
    ------
    ValueError
        If the stamp is missing, no accepted header is found, the block is
        empty, a row is malformed (field count, date, or a TIME that is not
        on the hour), a day does not cover hours 0–23 exactly once, or a
        daily file holds more than one target date.
    """
    with open(file, encoding=_ENCODING) as f:
        lines = [line.rstrip("\r\n") for line in f]
    if not lines:
        raise ValueError(f"{file}: empty file, expected an UPDATE stamp on the first line")
    file_updated_at = _parse_update_stamp(file, lines[0])
    accepted = TEPCO_POWER_USAGE.accepted_headers
    header_index = next((i for i, line in enumerate(lines) if line in accepted), None)
    if header_index is None:
        raise ValueError(
            f"{file}: no accepted hourly header line found — expected one of "
            f"{sorted(accepted)!r} (did TEPCO change the layout?)"
        )
    header = lines[header_index]
    rows: list[HourlyRow] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            break
        rows.append(_parse_row(file, line, header))
    if not rows:
        raise ValueError(f"{file}: no hourly rows under the header {header!r}")
    _check_complete(file, header, rows)
    return HourlyFile(file_updated_at=file_updated_at, header=header, rows=rows)


def _check_complete(file: Path | str, header: str, rows: list[HourlyRow]) -> None:
    """Require every day in the block to carry hours 0–23 exactly once.

    A truncated file that still ends in a well-formed row and a blank line
    would otherwise load as a day with absent hours — a gap the grain
    uniqueness check downstream cannot see. A daily file must also hold a
    single target date.
    """
    hours_by_date: dict[str, list[int]] = {}
    for row in rows:
        hours_by_date.setdefault(row.target_date, []).append(row.hour_start)
    if header == DAILY_HOURLY_HEADER and len(hours_by_date) != 1:
        raise ValueError(
            f"{file}: a daily file must hold exactly one target date, found {sorted(hours_by_date)}"
        )
    for target_date, hours in hours_by_date.items():
        if sorted(hours) != list(range(24)):
            raise ValueError(
                f"{file}: {target_date} does not cover hours 0-23 exactly once "
                f"(got {sorted(hours)}) — truncated or duplicated block?"
            )


class TepcoPowerUsageDownloader(AreaActualsDownloader):
    """Download the yearly files and the monthly archives of the でんき予報 history.

    The monthly ``YYYYMM_power_usage.zip`` archives (2022-04 → the current
    month) go through the shared
    :class:`~power_market_analytics.area_actuals.AreaActualsDownloader`:
    always re-downloaded, daily members extracted into ``csv/``. The yearly
    ``juyo-YYYY.csv`` files (2016 … 2022) are immutable, so they are fetched
    once into the same ``csv/`` folder and reused unless ``force`` is given.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/tepco/power_usage"``
        Root directory: ``zip/`` for the monthly archives, ``csv/`` for the
        yearly files and the extracted daily files.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to use; defaults to a fresh one.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(TEPCO_POWER_USAGE, data_dir=data_dir, timeout=timeout, session=session)

    def yearly_path_for(self, year: int) -> Path:
        """Return the local path of one yearly file (``csv/juyo-YYYY.csv``)."""
        return self.csv_dir / f"juyo-{year}.csv"

    def download_yearly(self, year: int, force: bool = False) -> Path:
        """Fetch one yearly file, unless it is already on disk.

        Parameters
        ----------
        year : int
            Calendar year of the file (``juyo-YYYY.csv``).
        force : bool, default False
            Re-download even when the file exists.

        Returns
        -------
        pathlib.Path
            The local file.

        Raises
        ------
        AreaActualsDownloadError
            If the response is not a yearly file (its first lines lack the
            hourly header — e.g. an HTML maintenance page), does not parse
            as one, or does not cover every day of the year
            (:func:`expected_yearly_dates`) with 24 hours each — a cached
            file is only refetched with ``force``, so a gap must never be
            cached.
        requests.HTTPError
            If TEPCO responds with an error status.
        """
        dest = self.yearly_path_for(year)
        if dest.exists() and not force:
            logger.info("Using cached {}", dest)
            return dest
        url = YEARLY_URL_TEMPLATE.format(year=year)
        logger.info("Downloading {} -> {}", url, dest)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        content = response.content
        head = content[:1024].decode(_ENCODING, errors="ignore").splitlines()[:3]
        if YEARLY_HEADER not in head:
            raise AreaActualsDownloadError(
                f"{url} did not return the yearly file {dest.name} "
                f"(Content-Type={response.headers.get('Content-Type')!r}); "
                f"body starts {content[:120]!r}"
            )
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        try:
            self._check_yearly_coverage(url, partial, year)
        except AreaActualsDownloadError:
            partial.unlink()
            raise
        partial.replace(dest)
        logger.info("Saved {} ({} bytes)", dest, dest.stat().st_size)
        return dest

    @staticmethod
    def _check_yearly_coverage(url: str, file: Path, year: int) -> None:
        try:
            parsed = parse_hourly(file)
        except ValueError as exc:
            raise AreaActualsDownloadError(f"{url} returned an invalid yearly file: {exc}") from exc
        expected = expected_yearly_dates(year)
        found = sorted({row.target_date for row in parsed.rows})
        if found != expected:
            missing = sorted(set(expected) - set(found))
            extra = sorted(set(found) - set(expected))
            raise AreaActualsDownloadError(
                f"{url} does not cover {expected[0][:4]}-{expected[0][4:6]}-{expected[0][6:]} → "
                f"{expected[-1][:4]}-{expected[-1][4:6]}-{expected[-1][6:]}: "
                f"{len(missing)} day(s) missing (first {missing[:3]}), "
                f"{len(extra)} unexpected (first {extra[:3]})"
            )

    def download_all(
        self, today: datetime.date | None = None, force_yearly: bool = False
    ) -> list[Path]:
        """Fetch the yearly files, then every monthly archive through the current month.

        Parameters
        ----------
        today : datetime.date, optional
            Date whose month is the last archive downloaded (default: today).
        force_yearly : bool, default False
            Re-download the yearly files even when cached.

        Returns
        -------
        list of pathlib.Path
            The yearly files (2016 … 2022) followed by every extracted daily
            file, in month then day order.
        """
        yearly = [self.download_yearly(year, force=force_yearly) for year in YEARLY_YEARS]
        daily = super().download_all(today=today)
        return [*yearly, *daily]


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


class TepcoPowerUsageCsvLoader(CsvLoader):
    """Full reload of the でんき予報 hourly tables into a warehouse table.

    Works like :class:`~power_market_analytics.csv_loader.CsvLoader` (same
    validation and write behaviour) except for how each file is read: the
    files are multi-section, so :func:`parse_hourly` extracts the hourly
    table in Python and the contract addresses the parsed values by the
    ``__``-prefixed source names above. Yearly rows on or after
    :data:`DAILY_FILES_FROM` are dropped — those days come from the daily
    files — so the two packagings never collide on the grain.

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`CsvLoader`; ``filepath`` is the ``csv/`` folder holding
        both ``juyo-YYYY.csv`` and ``YYYYMMDD_power_usage.csv``.
    """

    def _read_all(self, files: list[str]) -> DataFrame:
        # One frame for the whole history: ~1,600 daily files as separate
        # local frames unioned together gave Spark 16k tasks per action.
        return self._frame(self._rows(files))

    def _rows(self, files: list[str]) -> list[tuple[str | None, ...]]:
        """Parse the hourly tables of ``files`` into contract-source string tuples."""
        data: list[tuple[str | None, ...]] = []
        for file in files:
            parsed = parse_hourly(file)
            rows = parsed.rows
            if parsed.header == YEARLY_HEADER:
                cutoff = DAILY_FILES_FROM.strftime("%Y%m%d")
                kept = [row for row in rows if row.target_date < cutoff]
                if len(kept) != len(rows):
                    logger.info(
                        "{}: dropped {} hourly row(s) on/after {} (covered by the daily files)",
                        file,
                        len(rows) - len(kept),
                        DAILY_FILES_FROM,
                    )
                rows = kept
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
                for row in rows
            )
        return data

    def _frame(self, data: list[tuple[str | None, ...]]) -> DataFrame:
        """Build the contract-typed DataFrame from parsed string tuples."""
        spark_schema = StructType([StructField(name, StringType()) for name in _SOURCE_COLUMNS])
        raw = self.spark.createDataFrame(data, spark_schema)
        return raw.select([self._cast(raw, c) for c in self.schema.columns])
