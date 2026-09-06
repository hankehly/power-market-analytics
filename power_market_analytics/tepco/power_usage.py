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
The parser and loader are the shared :mod:`power_market_analytics.power_usage`;
this module holds what is TEPCO's — the source spec, the yearly files and the
yearly-row drop. Format, quirks and the comparison against the A-1 series
(``tepco_area_demand_generation_actual``) are documented in
docs/TEPCO-Power-Usage-Retrieval.md.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import requests
from loguru import logger

from power_market_analytics.area_actuals import AreaActualsDownloader, AreaActualsDownloadError
from power_market_analytics.power_usage import (
    HourlyFile,
    HourlyRow,
    PowerUsageCsvLoader,
    PowerUsageSource,
)
from power_market_analytics.power_usage import parse_hourly as _parse_hourly

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

_ENCODING = "cp932"


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


TEPCO_POWER_USAGE = PowerUsageSource(
    code="tepco_power_usage",
    url_template="https://www.tepco.co.jp/forecast/html/images/{year:04d}{month:02d}_power_usage.zip",
    #: First monthly archive; the daily files start with it.
    earliest_month=(2022, 4),
    #: One member per day; members are flat (the day's 5-minute rows live in
    #: the same file, below the hourly table).
    member_re=re.compile(r"\d{8}_power_usage\.csv$"),
    accepted_headers=frozenset({DAILY_HOURLY_HEADER, YEARLY_HEADER}),
    default_data_dir="data/tepco/power_usage",
    #: A yearly file holds a whole calendar year; a daily member one date.
    multi_day_headers=frozenset({YEARLY_HEADER}),
)


def parse_hourly(file: Path | str) -> HourlyFile:
    """Read the hourly table out of a yearly or daily TEPCO file.

    :func:`power_market_analytics.power_usage.parse_hourly` bound to
    :data:`TEPCO_POWER_USAGE`.

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
        As the shared parser: a missing stamp, no accepted header, a
        malformed row, an incomplete day, or a daily file holding more than
        one target date.
    """
    return _parse_hourly(file, TEPCO_POWER_USAGE)


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


class TepcoPowerUsageCsvLoader(PowerUsageCsvLoader):
    """Full reload of the TEPCO でんき予報 hourly tables into a warehouse table.

    The shared :class:`~power_market_analytics.power_usage.PowerUsageCsvLoader`
    bound to :data:`TEPCO_POWER_USAGE`, dropping yearly rows on or after
    :data:`DAILY_FILES_FROM` — those days come from the daily files — so the
    two packagings never collide on the grain.

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`~power_market_analytics.csv_loader.CsvLoader`;
        ``filepath`` is the ``csv/`` folder holding both ``juyo-YYYY.csv``
        and ``YYYYMMDD_power_usage.csv``.
    """

    source = TEPCO_POWER_USAGE

    def _file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]:
        if parsed.header != YEARLY_HEADER:
            return parsed.rows
        cutoff = DAILY_FILES_FROM.strftime("%Y%m%d")
        kept = [row for row in parsed.rows if row.target_date < cutoff]
        if len(kept) != len(parsed.rows):
            logger.info(
                "{}: dropped {} hourly row(s) on/after {} (covered by the daily files)",
                file,
                len(parsed.rows) - len(kept),
                DAILY_FILES_FROM,
            )
        return kept
