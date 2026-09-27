"""JMA climatological normals (平年値): the vintage config, the downloader and the raw loader.

JMA publishes the normals of its staffed stations as one archive,
``normal_surface.zip``, on the 平年値ダウンロード page. Each station's daily file
holds 81 elements × 12 months, one row each, with 31 (value, flag) cells per row
and no header. The page, the archive, the record layout, the element codes, the
versions and the warehouse models are documented in
``docs/JMA-Climatological-Normals-Retrieval.md``.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
from loguru import logger

#: The 平年値ダウンロード page; its ``<year>年平年値（第<n>版）`` heading names the version.
NORMALS_PAGE_URL = "https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html"

#: The station list inside the archive, extracted for reference and never loaded.
STATION_INDEX_MEMBER = "normal_surface/surface_station_index.csv"

#: One daily file per station inside the archive.
DAILY_MEMBER_RE = re.compile(r"^normal_surface/daily/nml_sfc_d_(?P<station>\d{5})\.csv$")

#: ``2020年平年値（第5版)`` — JMA mixes full-width and ASCII brackets. The notices
#: (``2020年平年値の第5版を公開しました``) have no bracket and do not match.
_HEADING_RE = re.compile(r"(?P<year>\d{4})年平年値[（(]第(?P<version>\d+(?:\.\d+)*)版[)）]")


@dataclass(frozen=True)
class NormalsVintage:
    """One normals period as JMA publishes it.

    Attributes
    ----------
    period_start_year, period_end_year : int
        The 30 years the normals average, e.g. 1991 and 2020.
    in_use_since : datetime.date
        The day JMA put the period's normals into use (2021-05-19 for
        1991–2020); the ``available_at`` of every row of the period.
    zip_url : str
        The surface-observation archive of the period.
    expected_station_count : int
        Daily files the archive must hold; another count fails the download.
    """

    period_start_year: int
    period_end_year: int
    in_use_since: datetime.date
    zip_url: str
    expected_station_count: int


#: Configured periods, oldest first. The 2030 normals become a second entry.
VINTAGES: tuple[NormalsVintage, ...] = (
    NormalsVintage(
        period_start_year=1991,
        period_end_year=2020,
        in_use_since=datetime.date(2021, 5, 19),
        zip_url="https://www.data.jma.go.jp/stats/data/mdrr/normal/2020/data/normal_surface.zip",
        expected_station_count=157,
    ),
)


def vintage_for_year(period_end_year: int) -> NormalsVintage:
    """Return the configured vintage whose period ends in ``period_end_year``.

    Parameters
    ----------
    period_end_year : int
        The last year of the period, e.g. ``2020``.

    Returns
    -------
    NormalsVintage

    Raises
    ------
    KeyError
        If no vintage is configured for the year.
    """
    for vintage in VINTAGES:
        if vintage.period_end_year == period_end_year:
            return vintage
    raise KeyError(f"no normals vintage configured for the period ending {period_end_year}")


def parse_versions(html: str) -> dict[int, str]:
    """Every ``<year>年平年値（第<version>版）`` heading of the page, by period end year.

    Parameters
    ----------
    html : str
        The decoded page.

    Returns
    -------
    dict of int to str
        Period end year → version as written (``"5"``, ``"4.0.1"``); the last
        heading wins should a year repeat.
    """
    return {int(m["year"]): m["version"] for m in _HEADING_RE.finditer(html)}


class JmaNormalsDownloadError(RuntimeError):
    """Raised when the page or the archive is not what the vintage config expects."""


def _bytes_writer(data: bytes) -> Callable[[Path], None]:
    """A ``write(partial)`` for :meth:`JmaNormalsDownloader._atomic_write` that writes ``data``."""

    def write(partial: Path) -> None:
        partial.write_bytes(data)

    return write


class JmaNormalsDownloader:
    """Download one normals period: the version off the page, the archive, the daily files.

    The archive is re-downloaded on every call — JMA replaces it under the same
    URL when a new version comes out, and nothing inside names the version — and
    validated before anything is written: a zip holding the station index and
    exactly ``expected_station_count`` daily files. Every file is written
    atomically (``.part``, then replace). The manifest is removed first and
    written last, so a run that fails midway leaves no manifest and
    :class:`JmaNormalsCsvLoader` refuses the directory.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/jma/normals"``
        Root; each period gets ``{period_end_year}/`` underneath.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        Session to issue ``get`` calls with; a plain :class:`requests.Session`
        by default. Injected by the tests.
    page_url : str, default :data:`NORMALS_PAGE_URL`
        The page whose heading names the version.
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/jma/normals"),
        timeout: float = 60.0,
        session: requests.Session | None = None,
        page_url: str = NORMALS_PAGE_URL,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self.page_url = page_url

    # ----------------------------------------------------------------- paths

    def vintage_dir(self, vintage: NormalsVintage) -> Path:
        """Directory of a period, ``data_dir/<period end year>``."""
        return self.data_dir / str(vintage.period_end_year)

    def zip_path_for(self, vintage: NormalsVintage) -> Path:
        """Where the period's archive is kept."""
        return self.vintage_dir(vintage) / "zip" / "normal_surface.zip"

    def daily_dir(self, vintage: NormalsVintage) -> Path:
        """Directory of the period's extracted daily files."""
        return self.vintage_dir(vintage) / "csv" / "daily"

    def station_index_path_for(self, vintage: NormalsVintage) -> Path:
        """Where the period's station index is extracted, for reference."""
        return self.vintage_dir(vintage) / "csv" / "surface_station_index.csv"

    def manifest_path_for(self, vintage: NormalsVintage) -> Path:
        """The period's manifest JSON, read by the loader."""
        return self.vintage_dir(vintage) / "manifest.json"

    # ----------------------------------------------------------------- the page

    def read_version(self, vintage: NormalsVintage) -> str:
        """Return the version the page names for the vintage's period.

        Parameters
        ----------
        vintage : NormalsVintage
            The period whose heading to read.

        Returns
        -------
        str
            The version as written, e.g. ``"5"``.

        Raises
        ------
        JmaNormalsDownloadError
            If the page has no heading for the period — JMA has moved on, and
            a vintage for the newer normals is the fix.
        requests.HTTPError
            If the page cannot be fetched.
        """
        response = self._get(self.page_url)
        versions = parse_versions(response.content.decode("utf-8", errors="replace"))
        if vintage.period_end_year not in versions:
            found = ", ".join(str(year) for year in sorted(versions)) or "none"
            raise JmaNormalsDownloadError(
                f"{self.page_url}: no heading for the {vintage.period_start_year}–"
                f"{vintage.period_end_year} normals (periods on the page: {found}); add a "
                "vintage for the newer normals"
            )
        return versions[vintage.period_end_year]

    # ----------------------------------------------------------------- the archive

    def download_vintage(self, vintage: NormalsVintage) -> list[Path]:
        """Fetch the period's archive and extract its daily files and station index.

        Parameters
        ----------
        vintage : NormalsVintage
            The period to fetch.

        Returns
        -------
        list of pathlib.Path
            The extracted daily files, sorted by name.

        Raises
        ------
        JmaNormalsDownloadError
            If the page has no heading for the period, or the archive is not a
            zip, lacks the station index or holds another number of daily
            files; nothing is written in that case.
        requests.HTTPError
            If the page or the archive cannot be fetched.
        """
        version = self.read_version(vintage)
        logger.info(
            "Normals {}–{}: version {} on the page; downloading {}",
            vintage.period_start_year,
            vintage.period_end_year,
            version,
            vintage.zip_url,
        )
        content = self._get(vintage.zip_url).content
        members = self._validate_archive(content, vintage)
        downloaded_at = datetime.datetime.now(datetime.timezone.utc)
        # No manifest while the files are being replaced: a run that fails
        # midway must not look complete to the loader.
        self.manifest_path_for(vintage).unlink(missing_ok=True)
        self._atomic_write(self.zip_path_for(vintage), _bytes_writer(content))
        paths: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for member in members:
                dest = self.daily_dir(vintage) / Path(member).name
                self._atomic_write(dest, _bytes_writer(archive.read(member)))
                paths.append(dest)
            index = archive.read(STATION_INDEX_MEMBER)
        self._atomic_write(self.station_index_path_for(vintage), _bytes_writer(index))
        # A daily file of a station the archive no longer holds would otherwise
        # be loaded next to the new ones.
        for stale in self.daily_dir(vintage).glob("nml_sfc_d_*.csv"):
            if stale not in paths:
                stale.unlink()
                logger.warning("Removed {}: not in the archive any more", stale)
        manifest = {
            "period_start_year": vintage.period_start_year,
            "period_end_year": vintage.period_end_year,
            "version": version,
            "in_use_since": vintage.in_use_since.isoformat(),
            "downloaded_at_utc": downloaded_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zip_url": vintage.zip_url,
            "zip_sha256": hashlib.sha256(content).hexdigest(),
            "zip_bytes": len(content),
            "daily_file_count": len(paths),
        }
        self._atomic_write(
            self.manifest_path_for(vintage),
            _bytes_writer(json.dumps(manifest, indent=2).encode("utf-8")),
        )
        logger.info(
            "Normals {}–{}: {} daily file(s) in {} ({} bytes, sha256 {})",
            vintage.period_start_year,
            vintage.period_end_year,
            len(paths),
            self.daily_dir(vintage),
            len(content),
            manifest["zip_sha256"],
        )
        return paths

    @staticmethod
    def _validate_archive(content: bytes, vintage: NormalsVintage) -> list[str]:
        """The archive's daily members, sorted, once the archive passes its checks.

        Raises
        ------
        JmaNormalsDownloadError
            If ``content`` is not a zip, lacks :data:`STATION_INDEX_MEMBER`, or
            holds a number of daily members other than the vintage's.
        """
        buffer = io.BytesIO(content)
        if not zipfile.is_zipfile(buffer):
            raise JmaNormalsDownloadError(
                f"{vintage.zip_url}: not a zip archive ({content[:60]!r})"
            )
        with zipfile.ZipFile(buffer) as archive:
            names = archive.namelist()
        if STATION_INDEX_MEMBER not in names:
            raise JmaNormalsDownloadError(
                f"{vintage.zip_url}: no member {STATION_INDEX_MEMBER} among {len(names)} members"
            )
        members = sorted(name for name in names if DAILY_MEMBER_RE.match(name))
        if len(members) != vintage.expected_station_count:
            raise JmaNormalsDownloadError(
                f"{vintage.zip_url}: expected {vintage.expected_station_count} daily files, "
                f"found {len(members)} — JMA opened or closed a station; update "
                "expected_station_count on purpose"
            )
        return members

    def download_all(self, years: list[int] | None = None) -> list[Path]:
        """Fetch every configured period, or the given period end years.

        Parameters
        ----------
        years : list of int, optional
            Period end years to fetch; every entry of :data:`VINTAGES` by default.

        Returns
        -------
        list of pathlib.Path
            The daily files of all requested periods, oldest period first.

        Raises
        ------
        KeyError
            If a year has no configured vintage.
        """
        vintages = list(VINTAGES) if years is None else [vintage_for_year(y) for y in years]
        paths: list[Path] = []
        for vintage in vintages:
            paths.extend(self.download_vintage(vintage))
        return paths

    # ----------------------------------------------------------------- HTTP and files

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response

    @staticmethod
    def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
        """Write to ``path`` atomically: ``write`` fills a ``.part`` file, then replace.

        On any exception from ``write`` or the replace, the partial file is
        deleted and the exception re-raised; a prior file at ``path`` is
        untouched until the replace. The same rule as the MSM downloader's,
        repeated here because importing that module pulls in eccodes.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        try:
            write(partial)
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
