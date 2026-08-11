"""Download JMA (Japan Meteorological Agency) historical weather observations.

JMA serves historical observation data through the interactive page at
``https://www.data.jma.go.jp/risk/obsdl/index.php``. There is no documented
API; the page drives a small set of form-POST endpoints that this module
calls directly. The full reverse-engineered protocol — endpoints, payloads,
station model, request caps, and CSV format — is documented in
``docs/JMA-Weather-Data-Retrieval.md``.

Two downloaders live here:

- ``JmaHourlyDownloader`` — hourly observation CSVs from ``show/table``,
  one file per station, element set and calendar year.
- ``JmaStationMasterDownloader`` — the station master (id, name, kana,
  prefecture, coordinates, elevation, observed-element mask, end-of-
  observation date) scraped from the per-prefecture ``top/station`` pages
  into a single UTF-8 CSV.

The site asks users to avoid excessive automated access and rate-limits
bursts (HTTP 429), so all requests are spaced ``request_interval`` seconds
apart and retried with exponential backoff.
"""

from __future__ import annotations

import csv
import datetime
import logging
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

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

#: Digit order of the ``kansoku`` observed-element mask, as defined in the
#: site's own JS (``web/js/top.2.1.js``). Digit values: 0 = not observed,
#: 1 = observed, 2 = estimated (satellite-derived sunshine at AMeDAS).
KANSOKU_DIGITS = [
    "precipitation",  # 降水量
    "wind",  # 風
    "temperature",  # 気温
    "sunshine",  # 日照時間
    "snow",  # 積雪・降雪
    "other",  # その他 (staffed extras; humidity at modernized AMeDAS)
]


class _JmaDownloader:
    """Shared HTTP behavior for the JMA obsdl endpoints.

    Consecutive requests are separated by ``request_interval`` seconds and
    retried on HTTP 429/5xx with exponential backoff, to respect JMA's
    request to avoid excessive automated access.

    Parameters
    ----------
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    request_interval : float, default 5.0
        Minimum seconds between consecutive HTTP requests.
    max_retries : int, default 4
        Retries on HTTP 429/5xx before giving up. The n-th retry waits
        ``backoff_base * 2**n`` seconds.
    backoff_base : float, default 30.0
        Base wait in seconds for the exponential backoff.
    """

    _HEADERS = {
        "Referer": "https://www.data.jma.go.jp/risk/obsdl/index.php",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        timeout: float = 60.0,
        request_interval: float = 5.0,
        max_retries: int = 4,
        backoff_base: float = 30.0,
    ) -> None:
        self.timeout = timeout
        self.request_interval = request_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._last_request_at = 0.0

    def _post_with_retry(self, url: str, payload: dict) -> requests.Response:
        """POST to a JMA endpoint, retrying on HTTP 429/5xx with backoff.

        Parameters
        ----------
        url : str
            Endpoint URL.
        payload : dict
            Form fields for the POST.

        Returns
        -------
        requests.Response
            The successful response.

        Raises
        ------
        requests.HTTPError
            If the final attempt still returns an error status.
        """
        for attempt in range(self.max_retries + 1):
            self._throttle()
            response = requests.post(
                url, data=payload, headers=self._HEADERS, timeout=self.timeout
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.max_retries:
                response.raise_for_status()
                return response
            wait = self.backoff_base * 2**attempt
            logger.warning(
                "JMA returned HTTP %d; retrying in %.0f s (attempt %d/%d)",
                response.status_code,
                wait,
                attempt + 1,
                self.max_retries,
            )
            time.sleep(wait)
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        """Sleep so consecutive HTTP requests are ``request_interval`` apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.monotonic()


class JmaHourlyDownloader(_JmaDownloader):
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
        HTTP behavior options passed through to ``_JmaDownloader``
        (``timeout``, ``request_interval``, ``max_retries``,
        ``backoff_base``).

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

    #: Empirical per-request cap on value columns for a full-year period.
    MAX_VALUE_COLUMNS = 5

    def __init__(
        self, data_dir: Path | str = Path("data/jma/hourly"), **kwargs
    ) -> None:
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

    def download(
        self,
        station_id: str,
        elements: list[str],
        year: int,
        force: bool = False,
    ) -> Path:
        """Download one station/element-set/year of hourly values.

        Parameters
        ----------
        station_id : str
            JMA station id, e.g. ``"s47662"`` for 東京.
        elements : list of str
            Element names to fetch in one request; keys of
            ``HOURLY_ELEMENTS``. Together they may contribute at most
            ``MAX_VALUE_COLUMNS`` value columns (wind counts as two).
        year : int
            Calendar year to download (January 1 through December 31,
            clamped to yesterday for the current year).
        force : bool, default False
            Re-download even if the file already exists locally.

        Returns
        -------
        pathlib.Path
            Path to the downloaded (or cached) CSV file.

        Raises
        ------
        ValueError
            If an element is unknown or duplicated, the element set
            exceeds ``MAX_VALUE_COLUMNS`` value columns, ``year`` is
            outside ``EARLIEST_YEAR``..current year, or the response is
            not a CSV (e.g. an HTML error page for an over-cap request).
        requests.HTTPError
            If JMA still responds with an error status after retries.
        """
        self._validate_elements(elements)
        current_year = datetime.date.today().year
        if not self.EARLIEST_YEAR <= year <= current_year:
            raise ValueError(
                f"Year {year} outside supported range "
                f"{self.EARLIEST_YEAR}..{current_year}"
            )

        dest = self.path_for(station_id, elements, year)
        if dest.exists() and not force:
            logger.info("Using cached JMA hourly file: %s", dest)
            return dest

        logger.info(
            "Downloading %s %s %d -> %s", station_id, sorted(elements), year, dest
        )
        response = self._post_with_retry(
            self.SHOW_TABLE_URL, self._payload(station_id, elements, year)
        )

        head = response.content[:64].decode(self.ENCODING, errors="replace")
        if not head.startswith("ダウンロードした時刻"):
            raise ValueError(
                f"Unexpected response for {station_id}/{sorted(elements)}/{year} "
                f"(not a JMA CSV): {head!r}"
            )

        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated file at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(response.content)
        partial.replace(dest)
        logger.info("Saved %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    def _validate_elements(self, elements: list[str]) -> None:
        """Check element names, uniqueness and the value-column budget.

        Parameters
        ----------
        elements : list of str
            Element names to validate.

        Raises
        ------
        ValueError
            If ``elements`` is empty, contains an unknown or duplicate
            name, or exceeds ``MAX_VALUE_COLUMNS`` value columns.
        """
        if not elements:
            raise ValueError("At least one element is required")
        unknown = sorted(set(elements) - set(HOURLY_ELEMENTS))
        if unknown:
            raise ValueError(
                f"Unknown elements {unknown}; expected keys of HOURLY_ELEMENTS"
            )
        if len(set(elements)) != len(elements):
            raise ValueError(f"Duplicate elements in {elements}")
        columns = sum(ELEMENT_VALUE_COLUMNS[e] for e in elements)
        if columns > self.MAX_VALUE_COLUMNS:
            raise ValueError(
                f"Element set {sorted(elements)} needs {columns} value columns; "
                f"JMA rejects full-year requests above {self.MAX_VALUE_COLUMNS}. "
                "Split the set across multiple downloads."
            )

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

    def _payload(self, station_id: str, elements: list[str], year: int) -> dict:
        """Build the ``show/table`` form payload for one station-elements-year.

        Parameters
        ----------
        station_id : str
            JMA station id.
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        year : int
            Calendar year.

        Returns
        -------
        dict
            Form fields for the ``show/table`` POST.
        """
        # An end date later than yesterday makes the endpoint return an HTML
        # error page, so the current year's period must stop at yesterday.
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        end = min(datetime.date(year, 12, 31), yesterday)
        element_num_list = (
            "[" + ",".join(f'["{c}",""]' for c in self._element_codes(elements)) + "]"
        )
        return {
            "stationNumList": f'["{station_id}"]',
            "aggrgPeriod": "9",  # 時別値 (hourly values)
            "elementNumList": element_num_list,
            "interAnnualType": "1",  # one continuous period
            # [yearFrom, yearTo, monthFrom, monthTo, dayFrom, dayTo]
            "ymdList": f'["{year}","{year}","1","{end.month}","1","{end.day}"]',
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


class JmaStationMasterDownloader(_JmaDownloader):
    """Scrape the JMA station master into a single UTF-8 CSV.

    ``POST top/station`` with ``pd=00`` yields the prefecture map, from
    which the area codes are discovered dynamically; each area page then
    yields its stations (including discontinued ones) as hidden-input
    blocks plus a ``title`` tooltip carrying kana, coordinates (degrees +
    decimal minutes, 北緯/南緯 and 東経/西経), elevation, and an optional
    end-of-observation date.

    The output has one row per station, sorted by (prefecture_code,
    station_id), with the raw 6-digit ``kansoku`` mask and its decoded
    per-element digits (see ``KANSOKU_DIGITS``).

    Parameters
    ----------
    dest : pathlib.Path or str, default ``"data/jma/stations.csv"``
        Output CSV path. Parent directories are created as needed.
    **kwargs
        HTTP behavior options passed through to ``_JmaDownloader``
        (``timeout``, ``request_interval``, ``max_retries``,
        ``backoff_base``).

    Examples
    --------
    >>> downloader = JmaStationMasterDownloader()
    >>> path = downloader.download()
    >>> path
    PosixPath('data/jma/stations.csv')
    """

    STATION_URL = "https://www.data.jma.go.jp/risk/obsdl/top/station"

    FIELDNAMES = [
        "station_id",
        "prefecture_code",
        "station_name",
        "station_kana",
        "latitude",
        "longitude",
        "elevation_m",
        "kansoku",
        *[f"obs_{name}" for name in KANSOKU_DIGITS],
        "observation_ended_on",
    ]

    _STATION_RE = re.compile(
        r'<div[^>]*class="station[^"]*"[^>]*title="([^"]*)"[^>]*>'
        r'<input type="hidden" name="stid" value="([^"]+)">'
        r'<input type="hidden" name="stname" value="([^"]+)">'
        r'<input type="hidden" name="prid" value="(\d+)">'
        r'<input type="hidden" name="kansoku" value="(\d+)">'
    )

    def __init__(
        self, dest: Path | str = Path("data/jma/stations.csv"), **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.dest = Path(dest)

    def download(self, force: bool = False) -> Path:
        """Download the station master for every prefecture into ``dest``.

        Station metadata changes rarely (new stations, discontinuations),
        so the output is cached like the hourly files: pass ``force=True``
        to refresh it.

        Parameters
        ----------
        force : bool, default False
            Re-download even if ``dest`` already exists.

        Returns
        -------
        pathlib.Path
            Path to the written (or cached) CSV file.

        Raises
        ------
        ValueError
            If no prefecture codes are found, or a prefecture page
            contains station ids the parser failed to capture (regex
            drift).
        requests.HTTPError
            If JMA still responds with an error status after retries.
        """
        if self.dest.exists() and not force:
            logger.info("Using cached JMA station master: %s", self.dest)
            return self.dest

        prefecture_codes = self._prefecture_codes()
        logger.info("Enumerating stations for %d areas", len(prefecture_codes))

        stations: dict[str, dict] = {}
        for code in prefecture_codes:
            html = self._fetch_area(code)
            rows = self._parse_stations(html, code)
            new = 0
            for row in rows:
                existing = stations.get(row["station_id"])
                if existing is None:
                    stations[row["station_id"]] = row
                    new += 1
                elif existing != row:
                    logger.warning(
                        "Station %s appears with conflicting metadata "
                        "(prefectures %s and %s); keeping the first",
                        row["station_id"],
                        existing["prefecture_code"],
                        row["prefecture_code"],
                    )
            logger.info("Area %02d: %d stations (%d new)", code, len(rows), new)

        ordered = sorted(
            stations.values(), key=lambda r: (r["prefecture_code"], r["station_id"])
        )
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        partial = self.dest.with_name(self.dest.name + ".part")
        with open(partial, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            writer.writerows(ordered)
        partial.replace(self.dest)
        logger.info("Saved %s (%d stations)", self.dest, len(ordered))
        return self.dest

    def _fetch_area(self, prefecture_code: int | str) -> str:
        """Fetch one ``top/station`` page.

        Parameters
        ----------
        prefecture_code : int or str
            Area code (``"00"`` for the prefecture map itself).

        Returns
        -------
        str
            The response HTML.
        """
        response = self._post_with_retry(
            self.STATION_URL, {"pd": f"{int(prefecture_code):02d}"}
        )
        return response.text

    def _prefecture_codes(self) -> list[int]:
        """Discover the area codes from the prefecture map (``pd=00``).

        Returns
        -------
        list of int
            Sorted area codes (61 as of 2026: 14 Hokkaidō subprefectural
            areas, the other prefectures, and 99 for Antarctica).

        Raises
        ------
        ValueError
            If no codes are found in the response.
        """
        html = self._fetch_area(0)
        codes = sorted(
            {int(m) for m in re.findall(r'<div class="prefecture" id="pr(\d+)"', html)}
        )
        if not codes:
            raise ValueError("No prefecture codes found in the pd=00 response")
        return codes

    def _parse_stations(self, html: str, prefecture_code: int) -> list[dict]:
        """Parse the station blocks out of one prefecture page.

        Each station appears multiple times in the page (map marker and
        name label); duplicates are collapsed by station id.

        Parameters
        ----------
        html : str
            ``top/station`` response for one prefecture.
        prefecture_code : int
            The requested area code (used for error messages; the row's
            ``prefecture_code`` comes from the page's ``prid`` input).

        Returns
        -------
        list of dict
            One dict per unique station, keyed by ``FIELDNAMES``.

        Raises
        ------
        ValueError
            If the page contains station ids the block regex failed to
            capture — a signal that JMA changed the markup.
        """
        rows: dict[str, dict] = {}
        for title, stid, stname, prid, kansoku in self._STATION_RE.findall(html):
            if stid in rows:
                continue
            row = {
                "station_id": stid,
                "prefecture_code": int(prid),
                "station_name": stname,
                "kansoku": kansoku,
                **{
                    f"obs_{name}": int(kansoku[i]) if i < len(kansoku) else None
                    for i, name in enumerate(KANSOKU_DIGITS)
                },
                **self._parse_title(title),
            }
            rows[stid] = row

        # h-prefixed ids are navigation controls (class="movepr" jump-to-
        # neighboring-prefecture cells, class="selectallst" select-all), not
        # observation stations.
        all_ids = {
            stid
            for stid in re.findall(r'name="stid" value="([^"]+)"', html)
            if not stid.startswith("h")
        }
        if all_ids != set(rows):
            raise ValueError(
                f"Area {prefecture_code:02d}: parsed {len(rows)} stations but the "
                f"page contains {len(all_ids)} station ids "
                f"(missed: {sorted(all_ids - set(rows))}) — markup may have changed"
            )
        return list(rows.values())

    @staticmethod
    def _parse_title(title: str) -> dict:
        """Parse kana, coordinates, elevation and end date from a tooltip.

        Parameters
        ----------
        title : str
            The station div's ``title`` attribute, e.g.
            ``"地点名：東京\\nカナ:トウキヨウ\\n北緯：35度41.5分\\n東経：139度45.0分\\n標高：25.2m"``,
            optionally followed by ``"...に観測終了"``. Antarctic stations
            use 南緯; longitudes west of Greenwich would use 西経.

        Returns
        -------
        dict
            ``station_kana``, ``latitude``, ``longitude`` (decimal
            degrees, 4 dp, signed by hemisphere), ``elevation_m`` and
            ``observation_ended_on`` (ISO date or None). Fields the
            tooltip lacks are None.
        """
        kana = re.search(r"カナ:([^\n]+)", title)
        lat = re.search(r"(北緯|南緯)：(\d+)度([\d.]+)分", title)
        lon = re.search(r"(東経|西経)：(\d+)度([\d.]+)分", title)
        elev = re.search(r"標高：(-?[\d.]+)m", title)
        ended = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日に観測終了", title)

        def to_degrees(match: re.Match | None, negative_hemisphere: str) -> float | None:
            if match is None:
                return None
            degrees = int(match.group(2)) + float(match.group(3)) / 60
            if match.group(1) == negative_hemisphere:
                degrees = -degrees
            return round(degrees, 4)

        return {
            "station_kana": kana.group(1).strip() if kana else None,
            "latitude": to_degrees(lat, "南緯"),
            "longitude": to_degrees(lon, "西経"),
            "elevation_m": float(elev.group(1)) if elev else None,
            "observation_ended_on": (
                datetime.date(
                    int(ended.group(1)), int(ended.group(2)), int(ended.group(3))
                ).isoformat()
                if ended
                else None
            ),
        }
