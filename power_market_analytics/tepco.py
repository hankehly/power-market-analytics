"""Download TEPCO エリア需要・発電情報 (Tokyo-area demand & generation) archives.

TEPCO Power Grid publishes 30-minute Tokyo-area total demand, total
generation and wind+solar generation on
https://www.tepco.co.jp/forecast/html/area-download-j.html. The history is
served as one zip per month
(``https://www4.tepco.co.jp/forecast/html/images/AREA_YYYYMM.zip``, 2022-04
onward, ~100 KB each) holding three CP932 CSVs per day: 実績 actuals
(``AREA_JISEKI_YYYYMMDD.csv``), 予測 forecasts (``AREA_YOSOKU_``) and
BG計画総計 balancing-group plans (``AREA_BGKEI_``). Only the actuals files are
extracted. The archive layout, CSV format and data quirks are documented in
docs/TEPCO-Area-Demand-Generation-Retrieval.md.
"""

from __future__ import annotations

import datetime
import io
import re
import zipfile
from pathlib import Path

import requests
from loguru import logger

URL_TEMPLATE = "https://www4.tepco.co.jp/forecast/html/images/AREA_{year:04d}{month:02d}.zip"

#: First month published on the download page.
EARLIEST_MONTH = (2022, 4)

#: Zip members to extract: the daily actuals files. The archive also holds
#: AREA_YOSOKU_* (forecast) and AREA_BGKEI_* (BG plan) files, which are skipped.
ACTUALS_MEMBER_RE = re.compile(r"AREA_JISEKI_\d{8}\.csv$")


class TepcoDownloadError(RuntimeError):
    """Raised when TEPCO returns something other than the expected zip archive."""


def month_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Return every ``(year, month)`` from ``start`` through ``end`` inclusive.

    Parameters
    ----------
    start, end : tuple of (int, int)
        ``(year, month)`` bounds, both inclusive.

    Returns
    -------
    list of tuple of (int, int)
        Consecutive months in ascending order.

    Raises
    ------
    ValueError
        If ``start`` is after ``end``.
    """
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    months = []
    year, month = start
    while (year, month) <= end:
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


class TepcoAreaDownloader:
    """Download the monthly TEPCO area archives and extract the daily actuals CSVs.

    Every call re-downloads the requested month: TEPCO occasionally revises
    past days (e.g. 2022-12-01/02 were re-issued on 2022-12-14, 2024-03-11 on
    2024-04-19) and the current month's zip grows daily, and the whole history
    is only ~5 MB, so no caching is attempted.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/tepco/area_demand_generation"``
        Root directory. Zips are kept under ``zip/`` and the extracted
        ``AREA_JISEKI_YYYYMMDD.csv`` files under ``csv/``. Created on first
        download if it does not exist.
    timeout : float, default 60.0
        HTTP request timeout in seconds.

    Examples
    --------
    >>> downloader = TepcoAreaDownloader()
    >>> downloader.download(2025, 7)[:2]
    [PosixPath('data/tepco/area_demand_generation/csv/AREA_JISEKI_20250701.csv'),
     PosixPath('data/tepco/area_demand_generation/csv/AREA_JISEKI_20250702.csv')]
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/tepco/area_demand_generation"),
        timeout: float = 60.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout

    @property
    def zip_dir(self) -> Path:
        """Directory holding the downloaded monthly zip archives."""
        return self.data_dir / "zip"

    @property
    def csv_dir(self) -> Path:
        """Directory holding the extracted daily actuals CSV files."""
        return self.data_dir / "csv"

    def zip_path_for(self, year: int, month: int) -> Path:
        """Return the local path of a month's zip archive.

        Parameters
        ----------
        year, month : int
            Calendar year and month of the archive.

        Returns
        -------
        pathlib.Path
            Path to the (possibly not yet downloaded) zip file.
        """
        return self.zip_dir / f"AREA_{year:04d}{month:02d}.zip"

    def download(self, year: int, month: int) -> list[Path]:
        """Download one month's archive and extract its actuals files.

        Parameters
        ----------
        year, month : int
            Calendar year and month of the archive.

        Returns
        -------
        list of pathlib.Path
            Extracted ``AREA_JISEKI_YYYYMMDD.csv`` paths, sorted by name.

        Raises
        ------
        TepcoDownloadError
            If the response is not a zip archive or contains no actuals files.
        requests.HTTPError
            If TEPCO responds with an error status (e.g. 404 for a month that
            is not published).
        """
        url = URL_TEMPLATE.format(year=year, month=month)
        dest = self.zip_path_for(year, month)
        logger.info("Downloading {} -> {}", url, dest)
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        content = response.content
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise TepcoDownloadError(
                f"{url} did not return a zip archive "
                f"(Content-Type={response.headers.get('Content-Type')!r}); "
                f"body starts {content[:120]!r}"
            )

        self.zip_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated archive at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        partial.replace(dest)
        extracted = self._extract_actuals(dest)
        logger.info(
            "Saved {} ({} bytes); extracted {} actuals file(s) to {}",
            dest,
            dest.stat().st_size,
            len(extracted),
            self.csv_dir,
        )
        return extracted

    def download_all(self, today: datetime.date | None = None) -> list[Path]:
        """Download every month from ``EARLIEST_MONTH`` through the current month.

        Parameters
        ----------
        today : datetime.date, optional
            Date whose month is the last one downloaded. Defaults to the
            current local date.

        Returns
        -------
        list of pathlib.Path
            All extracted actuals CSV paths, in month then day order.
        """
        if today is None:
            today = datetime.date.today()
        extracted: list[Path] = []
        for year, month in month_range(EARLIEST_MONTH, (today.year, today.month)):
            extracted.extend(self.download(year, month))
        logger.info(
            "Downloaded {}-{:02d}..{}-{:02d}: {} actuals file(s)",
            EARLIEST_MONTH[0],
            EARLIEST_MONTH[1],
            today.year,
            today.month,
            len(extracted),
        )
        return extracted

    def _extract_actuals(self, zip_path: Path) -> list[Path]:
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        extracted = []
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not ACTUALS_MEMBER_RE.search(member.filename):
                    continue
                # Members are flat in every archive except AREA_202403.zip,
                # which nests them under AREA_202403/ — keep the base name only.
                target = self.csv_dir / Path(member.filename).name
                target.write_bytes(archive.read(member))
                extracted.append(target)
        if not extracted:
            raise TepcoDownloadError(f"{zip_path} contains no AREA_JISEKI_*.csv members")
        return sorted(extracted)
