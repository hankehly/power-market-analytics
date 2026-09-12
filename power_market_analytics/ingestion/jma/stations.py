"""The JMA station master, scraped from the per-prefecture ``top/station`` pages.

``POST top/station`` with ``pd=00`` yields the prefecture map, from which the
area codes are discovered dynamically; each area page then yields its stations
(including discontinued ones) as hidden-input blocks plus a ``title`` tooltip
carrying kana, coordinates, elevation and an optional end-of-observation date.
"""

from __future__ import annotations

import csv
import datetime
import re
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.jma.client import JmaDownloader

#: JMA area (``pd``) codes whose stations lie outside every JEPX area:
#: 91 is Okinawa — 沖縄電力's supply area, and JEPX trades no Okinawa
#: product — and 99 is Antarctica (昭和基地).
NON_JEPX_AREA_PREFECTURE_CODES = {91, 99}

#: Stations outside every JEPX area despite an in-scope prefecture code:
#: s47991 南鳥島 belongs to 東京都 (pd 44) but is excluded from TEPCO PG's
#: supply area per its 託送供給等約款 (the JMA/JSDF outpost self-generates).
NON_JEPX_AREA_STATION_IDS = {"s47991"}

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


class JmaStationMasterDownloader(JmaDownloader):
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
    staffed_only : bool, default False
        Write only staffed stations (気象官署, ``s``-prefixed ids) and drop
        every AMeDAS row. The scrape itself is unchanged — the same
        per-prefecture pages are fetched — only the output is filtered.
    jepx_areas_only : bool, default False
        Drop stations outside every JEPX area — Okinawa and Antarctica
        (``NON_JEPX_AREA_PREFECTURE_CODES``) plus 南鳥島
        (``NON_JEPX_AREA_STATION_IDS``). Like ``staffed_only``, only the
        output is filtered.
    **kwargs
        HTTP behavior options passed through to ``JmaDownloader``
        (``timeout``, ``request_interval``, ``max_retries``,
        ``backoff_base``, ``session``).

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
        self,
        dest: Path | str = Path("data/jma/stations.csv"),
        staffed_only: bool = False,
        jepx_areas_only: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.dest = Path(dest)
        self.staffed_only = staffed_only
        self.jepx_areas_only = jepx_areas_only

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
            logger.info("Using cached JMA station master: {}", self.dest)
            return self.dest

        prefecture_codes = self._prefecture_codes()
        logger.info("Enumerating stations for {} areas", len(prefecture_codes))

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
                        "Station {} appears with conflicting metadata "
                        "(prefectures {} and {}); keeping the first",
                        row["station_id"],
                        existing["prefecture_code"],
                        row["prefecture_code"],
                    )
            logger.info("Area {:02d}: {} stations ({} new)", code, len(rows), new)

        if self.staffed_only:
            stations = {sid: row for sid, row in stations.items() if sid.startswith("s")}
        if self.jepx_areas_only:
            stations = {
                sid: row
                for sid, row in stations.items()
                if row["prefecture_code"] not in NON_JEPX_AREA_PREFECTURE_CODES
                and sid not in NON_JEPX_AREA_STATION_IDS
            }

        ordered = sorted(stations.values(), key=lambda r: (r["prefecture_code"], r["station_id"]))
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        partial = self.dest.with_name(self.dest.name + ".part")
        with open(partial, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            writer.writerows(ordered)
        partial.replace(self.dest)
        logger.info("Saved {} ({} stations)", self.dest, len(ordered))
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
        response = self._post_with_retry(self.STATION_URL, {"pd": f"{int(prefecture_code):02d}"})
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
        codes = sorted({int(m) for m in re.findall(r'<div class="prefecture" id="pr(\d+)"', html)})
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
