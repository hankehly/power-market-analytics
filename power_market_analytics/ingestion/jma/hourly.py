"""Hourly JMA observation CSVs (時別値) from the obsdl ``show/table`` endpoint.

One file per station, element set and calendar year. A station-year whose value
count is over the endpoint's per-request budget is fetched as several time
windows and stitched into one file, so the file layout never depends on the
budget. The reverse-engineered protocol and the CSV format are documented in
``docs/JMA-Weather-Data-Retrieval.md``.
"""

from __future__ import annotations

import calendar
import datetime
import math
import re
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.jma.client import JmaDownloader

#: Hourly (時別値, ``aggrgPeriod=9``) element codes accepted by the
#: ``show/table`` endpoint. Elements marked (官署のみ) only have values at
#: staffed stations (s-prefixed ids), not AMeDAS stations.
HOURLY_ELEMENTS = {
    "temperature": "201",  # 気温
    "precipitation": "101",  # 降水量（前1時間）
    "snowfall": "503",  # 降雪の深さ（前1時間）
    "snow_depth": "501",  # 積雪の深さ
    "sunshine": "401",  # 日照時間（前1時間）
    "wind": "301",  # 風向・風速
    "solar_radiation": "610",  # 全天日射量（前1時間）(官署のみ)
    "station_pressure": "601",  # 現地気圧 (官署のみ)
    "sea_level_pressure": "602",  # 海面気圧 (官署のみ)
    "humidity": "605",  # 相対湿度
    "vapor_pressure": "604",  # 蒸気圧
    "dew_point": "612",  # 露点温度
    "weather": "703",  # 天気 (官署のみ)
    "cloud_cover": "607",  # 雲量 (官署のみ)
    "visibility": "704",  # 視程 (官署のみ)
}

#: Value columns each element contributes to the CSV (and, apparently, to
#: the per-request data-volume cap). Wind yields two: speed and direction.
ELEMENT_VALUE_COLUMNS = {name: 2 if name == "wind" else 1 for name in HOURLY_ELEMENTS}

#: The element set scraped for every staffed station: the core four plus the
#: 官署 additions chosen in the 2026-08 re-scope (spec:
#: docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md).
#: 8 value columns -> 2 requests (windows) per station-year.
SCRAPE_ELEMENTS = [
    "precipitation",
    "temperature",
    "wind",
    "sunshine",
    "snow_depth",
    "humidity",
    "solar_radiation",
]


class JmaHourlyDownloader(JmaDownloader):
    """Download hourly JMA observation CSVs by station, element set and year.

    Downloads are cached: if the file for a station/element-set/year already
    exists in ``data_dir``, it is not re-downloaded unless ``force=True``.
    The current year's file grows daily, so pass ``force=True`` to refresh
    it.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/jma/hourly"``
        Directory where downloaded CSV files are stored. Created on first
        download if it does not exist.
    **kwargs
        HTTP behavior options passed through to ``JmaDownloader``
        (``timeout``, ``request_interval``, ``max_retries``,
        ``backoff_base``, ``session``).

    Examples
    --------
    >>> downloader = JmaHourlyDownloader()
    >>> path = downloader.download("s47662", ["temperature", "wind"], 2016)
    >>> path
    PosixPath('data/jma/hourly/s47662_201-301_2016.csv')
    """

    SHOW_TABLE_URL = "https://www.data.jma.go.jp/risk/obsdl/show/table"

    #: Earliest year we scrape (matches the JEPX spot price history).
    EARLIEST_YEAR = 2016

    #: Encoding of the CSV files served by JMA.
    ENCODING = "cp932"

    #: Empirical per-request cap on the number of values (value columns x
    #: hours). The largest proven-passing request is the core set over a
    #: full leap year (5 x 8784 = 43,920); the smallest proven-failing is
    #: ~61k (7 columns x a full year); 8-column half-years (~35k) were
    #: confirmed by the 2026-08-20 spike. Requests over the budget are
    #: split into windows and stitched, never rejected.
    MAX_VALUES_PER_REQUEST = 44_000

    def __init__(self, data_dir: Path | str = Path("data/jma/hourly"), **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = Path(data_dir)

    def path_for(self, station_id: str, elements: list[str], year: int) -> Path:
        """Return the local path where a station/element-set/year CSV is stored.

        The element set is encoded in the file name as the sorted numeric
        element codes joined with ``-``, so the same set always maps to the
        same file regardless of argument order.

        Parameters
        ----------
        station_id : str
            JMA station id, e.g. ``"s47662"`` for 東京.
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        year : int
            Calendar year.

        Returns
        -------
        pathlib.Path
            Path to the (possibly not yet downloaded) CSV file.
        """
        codes = "-".join(self._element_codes(elements))
        return self.data_dir / f"{station_id}_{codes}_{year}.csv"

    def window_count(self, elements: list[str], year: int) -> int:
        """Number of requests needed for one station-year of ``elements``.

        Parameters
        ----------
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        year : int
            Calendar year (leap years have more hours).

        Returns
        -------
        int
            ``ceil(value columns x hours / MAX_VALUES_PER_REQUEST)``, >= 1.
        """
        hours = 24 * (366 if calendar.isleap(year) else 365)
        values = hours * sum(ELEMENT_VALUE_COLUMNS[e] for e in elements)
        return max(1, math.ceil(values / self.MAX_VALUES_PER_REQUEST))

    def _window_bounds(self, elements: list[str], year: int) -> list[tuple]:
        """Split ``year`` into the request windows for ``elements``.

        Parameters
        ----------
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        year : int
            Calendar year to split.

        Returns
        -------
        list of (datetime.date, datetime.date)
            ``window_count`` contiguous, non-overlapping [start, end] spans
            covering Jan 1 .. Dec 31.
        """
        n = self.window_count(elements, year)
        first = datetime.date(year, 1, 1)
        days = (datetime.date(year, 12, 31) - first).days + 1
        return [
            (
                first + datetime.timedelta(days=i * days // n),
                first + datetime.timedelta(days=(i + 1) * days // n - 1),
            )
            for i in range(n)
        ]

    def _windows(self, elements: list[str], year: int, today: datetime.date) -> list[tuple]:
        """The windows to actually request: bounds clamped to yesterday.

        An end date later than yesterday makes the endpoint return an HTML
        error page, so current-year windows are cut at yesterday and
        entirely-future windows are dropped.

        Parameters
        ----------
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        year : int
            Calendar year to split.
        today : datetime.date
            The current date. Windows starting after yesterday are dropped;
            windows ending after yesterday are cut at yesterday.

        Returns
        -------
        list of (datetime.date, datetime.date)
            May be empty (e.g. on January 1, when the year has no
            observable days yet).
        """
        yesterday = today - datetime.timedelta(days=1)
        return [
            (start, min(end, yesterday))
            for start, end in self._window_bounds(elements, year)
            if start <= yesterday
        ]

    def download(
        self,
        station_id: str,
        elements: list[str],
        year: int,
        force: bool = False,
        today: datetime.date | None = None,
    ) -> Path:
        """Download one station/element-set/year of hourly values.

        Parameters
        ----------
        station_id : str
            JMA station id, e.g. ``"s47662"`` for 東京.
        elements : list of str
            Element names to fetch; any subset of ``HOURLY_ELEMENTS``; sets
            over the per-request value budget are fetched in multiple time
            windows and stitched into one file.
        year : int
            Calendar year to download (January 1 through December 31,
            clamped to yesterday for the current year).
        force : bool, default False
            Re-download even if the file already exists locally.
        today : datetime.date, optional
            The current date; defaults to ``datetime.date.today()``. Bounds
            the supported year range and clamps the current year's period
            to yesterday. Injected mainly for tests.

        Returns
        -------
        pathlib.Path
            Path to the downloaded (or cached) CSV file.

        Raises
        ------
        ValueError
            If an element is unknown or duplicated, ``year`` is outside
            ``EARLIEST_YEAR``..current year, the year has no observable
            days yet, or a response is not a CSV.
        requests.HTTPError
            If JMA still responds with an error status after retries.
        """
        self._validate_elements(elements)
        if today is None:
            today = datetime.date.today()
        current_year = today.year
        if not self.EARLIEST_YEAR <= year <= current_year:
            raise ValueError(
                f"Year {year} outside supported range {self.EARLIEST_YEAR}..{current_year}"
            )

        dest = self.path_for(station_id, elements, year)
        if dest.exists() and not force:
            logger.info("Using cached JMA hourly file: {}", dest)
            return dest

        windows = self._windows(elements, year, today)
        if not windows:
            raise ValueError(f"Year {year} has no observable days before {today}")
        parts = []
        for start, end in windows:
            logger.info("Downloading {} {} {}..{}", station_id, sorted(elements), start, end)
            response = self._post_with_retry(
                self.SHOW_TABLE_URL, self._payload(station_id, elements, start, end)
            )
            head = response.content[:64].decode(self.ENCODING, errors="replace")
            if not head.startswith("ダウンロードした時刻"):
                raise ValueError(
                    f"Unexpected response for {station_id}/{sorted(elements)}/"
                    f"{start}..{end} (not a JMA CSV): {head!r}"
                )
            parts.append(response.content)

        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Write-once: assemble in memory, land via temp-file + rename, and
        # never append to or patch an existing file (stale current-year
        # files are replaced wholesale via force=True).
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(self._stitch(parts))
        partial.replace(dest)
        logger.info("Saved {} ({} bytes)", dest, dest.stat().st_size)
        return dest

    def _validate_elements(self, elements: list[str]) -> None:
        """Check element names and uniqueness.

        Parameters
        ----------
        elements : list of str
            Element names to validate.

        Raises
        ------
        ValueError
            If ``elements`` is empty or contains an unknown or duplicate
            name.
        """
        if not elements:
            raise ValueError("At least one element is required")
        unknown = sorted(set(elements) - set(HOURLY_ELEMENTS))
        if unknown:
            raise ValueError(f"Unknown elements {unknown}; expected keys of HOURLY_ELEMENTS")
        if len(set(elements)) != len(elements):
            raise ValueError(f"Duplicate elements in {elements}")

    def _element_codes(self, elements: list[str]) -> list[str]:
        """Return the numeric codes for ``elements``, sorted ascending.

        Parameters
        ----------
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.

        Returns
        -------
        list of str
            Numeric element codes in ascending numeric order.
        """
        return sorted((HOURLY_ELEMENTS[e] for e in elements), key=int)

    _DATA_ROW = re.compile(rb"^\d{4}/")

    @classmethod
    def _stitch(cls, parts: list[bytes]) -> bytes:
        """Concatenate window responses into one CSV.

        The first part is kept whole (download-timestamp line, blank line,
        header rows, data); subsequent parts contribute only their data
        rows, so the header block appears exactly once. Note: 均質番号
        restarts at 1 in every server response, so in the stitched file the
        numbering resets at each window boundary — breaks are only
        meaningful within a window.

        Parameters
        ----------
        parts : list of bytes
            One cp932 response body per window, in chronological order.

        Returns
        -------
        bytes
            The stitched file content, CRLF line endings throughout.
        """
        stitched = bytearray(parts[0])
        if not stitched.endswith(b"\r\n"):
            stitched += b"\r\n"
        for part in parts[1:]:
            for line in part.split(b"\r\n"):
                if cls._DATA_ROW.match(line):
                    stitched += line + b"\r\n"
        return bytes(stitched)

    def _payload(
        self,
        station_id: str,
        elements: list[str],
        start: datetime.date,
        end: datetime.date,
    ) -> dict:
        """Build the ``show/table`` form payload for one station-elements-window.

        Parameters
        ----------
        station_id : str
            JMA station id.
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        start : datetime.date
            First day of the requested period.
        end : datetime.date
            Last day of the requested period. The caller must keep this at
            or before yesterday — an end date later than yesterday makes
            the endpoint return an HTML error page instead of a CSV;
            ``_windows`` is what enforces that clamp.

        Returns
        -------
        dict
            Form fields for the ``show/table`` POST.
        """
        element_num_list = (
            "[" + ",".join(f'["{c}",""]' for c in self._element_codes(elements)) + "]"
        )
        return {
            "stationNumList": f'["{station_id}"]',
            "aggrgPeriod": "9",  # 時別値 (hourly values)
            "elementNumList": element_num_list,
            "interAnnualType": "1",  # one continuous period
            # [yearFrom, yearTo, monthFrom, monthTo, dayFrom, dayTo]
            "ymdList": (
                f'["{start.year}","{end.year}","{start.month}","{end.month}",'
                f'"{start.day}","{end.day}"]'
            ),
            "optionNumList": "[]",
            "downloadFlag": "true",
            "rmkFlag": "1",  # include quality flags as numeric columns
            "disconnectFlag": "1",  # include homogeneity numbers
            "youbiFlag": "0",
            "fukenFlag": "0",
            "kijiFlag": "0",
            "csvFlag": "1",
            "jikantaiFlag": "0",  # all 24 hours
            "jikantaiList": "[1,24]",
            "ymdLiteral": "1",  # store 24:00 as next-day 00:00
        }
