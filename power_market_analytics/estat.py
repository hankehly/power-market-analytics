"""Download Japan's census 500 m population-mesh tables from e-Stat 統計GIS.

The 国勢調査 (Population Census) is published on the 4次メッシュ (500 m grid,
nine-digit ``KEY_CODE``) as one CP932 text file per 第１次地域区画 (primary
mesh, four digits) at https://www.e-stat.go.jp/gis/statmap-search. Each census
is a separate e-Stat statistics table (``statsId``) whose listing spans several
pages; the per-vintage differences (table id, population column, census date,
datum) live in :class:`CensusVintage` so a later census is a new entry in
:data:`VINTAGES`, not new code. Protocol, file format and privacy rules:
docs/eStat-Census-Population-Mesh-Retrieval.md.

The listing page is a JavaScript shell — its rows come from the portal's
``search_detail`` JSON endpoint (same query string plus
``mesh_data_flg=1&download_disp_flg=1``), whose ``detail`` field is the HTML
fragment holding the ``statmap-search/data?statsId=…&code=…&downloadType=2``
links and whose ``paginate`` field carries the ``N/Mページ`` page index.
"""

from __future__ import annotations

import datetime
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from loguru import logger

BASE_URL = "https://www.e-stat.go.jp"
#: JSON endpoint behind the listing page (rows + pager as HTML fragments).
LISTING_DETAIL_PATH = "/gis/statmap-search/search_detail"
#: One zip archive per (statsId, primary mesh code).
DOWNLOAD_PATH = "/gis/statmap-search/data"


@dataclass(frozen=True)
class CensusVintage:
    """One census on the 500 m mesh as published by e-Stat.

    Attributes
    ----------
    census_year : int
        Census year, e.g. ``2015``.
    census_date : datetime.date
        Reference date of the census (October 1 of ``census_year``).
    geodetic_datum : str
        Datum of the mesh product, e.g. ``"JGD2000"`` (e-Stat also publishes
        JGD2011 duplicates for recent censuses; one datum is used throughout
        so mesh geography is consistent across vintages).
    stats_id : str
        e-Stat statistics table id (``statsId``), e.g. ``"T000847"``.
    population_source_column : str
        Header of the total-population column in the text files, e.g.
        ``"T000847001"``.
    listing_url : str
        First page of the filtered 第１次地域区画 listing on e-Stat (the
        human-facing URL; :meth:`listing_detail_url` derives the JSON
        endpoint from its query string).
    expected_file_count : int
        Number of primary-mesh downloads the listing must contain.
    """

    census_year: int
    census_date: datetime.date
    geodetic_datum: str
    stats_id: str
    population_source_column: str
    listing_url: str
    expected_file_count: int

    def listing_detail_url(self, page: int) -> str:
        """Return the ``search_detail`` JSON URL for one listing page.

        Parameters
        ----------
        page : int
            1-based listing page.

        Returns
        -------
        str
            ``listing_url`` with the path swapped for the JSON endpoint,
            ``page`` replaced and the two flags the portal's JavaScript adds.
        """
        parts = urlsplit(self.listing_url)
        query = dict(parse_qsl(parts.query))
        query["page"] = str(page)
        query["mesh_data_flg"] = "1"
        query["download_disp_flg"] = "1"
        return urlunsplit((parts.scheme, parts.netloc, LISTING_DETAIL_PATH, urlencode(query), ""))

    def download_url(self, primary_mesh_code: str) -> str:
        """Return the zip download URL of one primary mesh."""
        return (
            f"{BASE_URL}{DOWNLOAD_PATH}"
            f"?statsId={self.stats_id}&code={primary_mesh_code}&downloadType=2"
        )

    def zip_name(self, primary_mesh_code: str) -> str:
        """Return the local archive name (as served in Content-Disposition)."""
        return f"tbl{self.stats_id}H{primary_mesh_code}.zip"

    def member_name(self, primary_mesh_code: str) -> str:
        """Return the single text member every archive must contain."""
        return f"tbl{self.stats_id}H{primary_mesh_code}.txt"


def _listing_url(year: int, stats_id: str) -> str:
    return (
        f"{BASE_URL}/gis/statmap-search?page=1&type=1&toukeiCode=00200521"
        f"&toukeiYear={year}&aggregateUnit=H&serveyId=H00200511{year}&statsId={stats_id}"
    )


#: Configured censuses, oldest first. Add an entry (plus fixtures) for a new census.
VINTAGES: tuple[CensusVintage, ...] = (
    CensusVintage(
        census_year=2015,
        census_date=datetime.date(2015, 10, 1),
        geodetic_datum="JGD2000",
        stats_id="T000847",
        population_source_column="T000847001",
        listing_url=_listing_url(2015, "T000847"),
        expected_file_count=151,
    ),
    CensusVintage(
        census_year=2020,
        census_date=datetime.date(2020, 10, 1),
        geodetic_datum="JGD2000",
        stats_id="T001101",
        population_source_column="T001101001",
        listing_url=_listing_url(2020, "T001101"),
        expected_file_count=151,
    ),
)


def vintage_for_year(census_year: int) -> CensusVintage:
    """Return the configured vintage of a census year.

    Raises
    ------
    KeyError
        If no vintage is configured for ``census_year``.
    """
    for vintage in VINTAGES:
        if vintage.census_year == census_year:
            return vintage
    raise KeyError(f"no census vintage configured for {census_year}")


def vintage_for_stats_id(stats_id: str) -> CensusVintage:
    """Return the configured vintage of an e-Stat statistics table id.

    Raises
    ------
    KeyError
        If no vintage is configured for ``stats_id``.
    """
    for vintage in VINTAGES:
        if vintage.stats_id == stats_id:
            return vintage
    raise KeyError(f"no census vintage configured for statsId {stats_id}")


# --------------------------------------------------------------------------- mesh codes

#: Nine-digit 500 m mesh code (JIS X 0410 4次メッシュ): AABB C D E F G — primary
#: mesh AABB, second-level row/column C, D in 0-7, third-level row/column E, F
#: in 0-9, quadrant G in 1-4.
MESH_CODE_RE = re.compile(r"^\d{4}[0-7]{2}\d{2}[1-4]$")


class MeshBounds(NamedTuple):
    """Bounding box and centroid of one 500 m mesh in decimal degrees.

    Attributes
    ----------
    south_latitude, north_latitude : float
        Southern / northern edge (north = south + 1/240 degree = 15").
    west_longitude, east_longitude : float
        Western / eastern edge (east = west + 1/160 degree = 22.5").
    centroid_latitude, centroid_longitude : float
        Midpoint of the box.
    """

    south_latitude: float
    north_latitude: float
    west_longitude: float
    east_longitude: float
    centroid_latitude: float
    centroid_longitude: float


def decode_mesh_code(mesh_code: str) -> MeshBounds:
    """Decode a nine-digit 500 m mesh code into its bounding box and centroid.

    For ``AABB C D E F G``: the lower-left corner is
    ``south = AA * 2/3 + C/12 + E/120`` and ``west = 100 + BB + D/8 + F/80``,
    shifted north by 1/240 for quadrants 3 and 4 and east by 1/160 for
    quadrants 2 and 4 (1 = SW, 2 = SE, 3 = NW, 4 = NE).

    Parameters
    ----------
    mesh_code : str
        Nine-digit 4次メッシュ code, e.g. ``"533946114"``.

    Returns
    -------
    MeshBounds

    Raises
    ------
    ValueError
        If ``mesh_code`` is not a structurally valid 500 m mesh code.
    """
    if MESH_CODE_RE.match(mesh_code) is None:
        raise ValueError(f"not a nine-digit 500 m mesh code: {mesh_code!r}")
    aa, bb = int(mesh_code[0:2]), int(mesh_code[2:4])
    c, d, e, f, g = (int(ch) for ch in mesh_code[4:9])
    south = aa * 2 / 3 + c / 12 + e / 120
    west = 100 + bb + d / 8 + f / 80
    if g in (3, 4):
        south += 1 / 240
    if g in (2, 4):
        west += 1 / 160
    north = south + 1 / 240
    east = west + 1 / 160
    return MeshBounds(
        south_latitude=south,
        north_latitude=north,
        west_longitude=west,
        east_longitude=east,
        centroid_latitude=(south + north) / 2,
        centroid_longitude=(west + east) / 2,
    )


# --------------------------------------------------------------------------- downloader


class EstatDownloadError(RuntimeError):
    """Raised when e-Stat returns something other than the expected listing or archive."""


#: A primary-mesh download link inside the listing's ``detail`` HTML fragment
#: (the JSON-decoded fragment uses plain ``&``; ``&amp;`` is accepted too).
_DOWNLOAD_LINK_RE = re.compile(
    r"/gis/statmap-search/data\?statsId=(?P<stats_id>[A-Za-z0-9]+)&(?:amp;)?"
    r"code=(?P<code>[^&\"']+)&(?:amp;)?downloadType=2"
)
#: ``N/Mページ`` in the listing's ``paginate`` HTML fragment (present on every page).
_PAGE_INDEX_RE = re.compile(r"(\d+)/(\d+)ページ")
_PRIMARY_MESH_CODE_RE = re.compile(r"^\d{4}$")


class EstatCensusMeshDownloader:
    """Download and extract the census 500 m population-mesh archives per vintage.

    Archives are cached: a primary mesh whose zip already exists under
    ``data_dir`` is not fetched again unless ``force=True``. Every archive is
    validated (a real zip holding exactly the one expected text member) before
    it is moved into place, and its member is extracted byte-for-byte to the
    ``txt/`` folder.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/estat/census_population_mesh"``
        Root directory; each vintage gets ``{census_year}/zip/`` and
        ``{census_year}/txt/`` underneath. Created on first download.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to issue ``get`` calls with; defaults to a fresh
        :class:`requests.Session`. Injected mainly for tests.
    request_interval : float, default 0.5
        Minimum seconds between consecutive HTTP requests (a full vintage is
        ~160 requests).

    Examples
    --------
    >>> downloader = EstatCensusMeshDownloader()
    >>> downloader.download_all(years=[2020])[:1]
    [PosixPath('data/estat/census_population_mesh/2020/txt/tblT001101H3622.txt')]
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/estat/census_population_mesh"),
        timeout: float = 60.0,
        session: requests.Session | None = None,
        request_interval: float = 0.5,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self.request_interval = request_interval
        self._last_request_at = -float("inf")

    # ----------------------------------------------------------------- paths

    def zip_dir(self, vintage: CensusVintage) -> Path:
        """Directory holding a vintage's downloaded zip archives."""
        return self.data_dir / str(vintage.census_year) / "zip"

    def txt_dir(self, vintage: CensusVintage) -> Path:
        """Directory holding a vintage's extracted text files."""
        return self.data_dir / str(vintage.census_year) / "txt"

    def zip_path_for(self, vintage: CensusVintage, primary_mesh_code: str) -> Path:
        """Return the local path of one primary mesh's zip archive."""
        return self.zip_dir(vintage) / vintage.zip_name(primary_mesh_code)

    def txt_path_for(self, vintage: CensusVintage, primary_mesh_code: str) -> Path:
        """Return the local path of one primary mesh's extracted text file."""
        return self.txt_dir(vintage) / vintage.member_name(primary_mesh_code)

    # ----------------------------------------------------------------- listing

    def discover_primary_mesh_codes(self, vintage: CensusVintage) -> list[str]:
        """Walk a vintage's listing pages and return its primary mesh codes.

        Parameters
        ----------
        vintage : CensusVintage
            Census whose 第１次地域区画 listing to read.

        Returns
        -------
        list of str
            Four-digit primary mesh codes in listing order, de-duplicated.

        Raises
        ------
        EstatDownloadError
            If a page is not the expected JSON, has no page index, links to
            another ``statsId``, contains a malformed code, or the
            de-duplicated count differs from ``vintage.expected_file_count``.
        requests.HTTPError
            If e-Stat responds with an error status.
        """
        codes: dict[str, None] = {}
        page, last_page = 1, 1
        while page <= last_page:
            detail, paginate = self._fetch_listing_page(vintage, page)
            match = _PAGE_INDEX_RE.search(paginate)
            if match is None:
                raise EstatDownloadError(
                    f"{vintage.listing_detail_url(page)}: no 'N/Mページ' page index in the "
                    f"paginate fragment {paginate[:200]!r} (did e-Stat change the markup?)"
                )
            last_page = int(match.group(2))
            for link in _DOWNLOAD_LINK_RE.finditer(detail):
                if link["stats_id"] != vintage.stats_id:
                    raise EstatDownloadError(
                        f"listing page {page} links to statsId {link['stats_id']}, "
                        f"expected {vintage.stats_id}"
                    )
                if _PRIMARY_MESH_CODE_RE.match(link["code"]) is None:
                    raise EstatDownloadError(
                        f"listing page {page}: malformed primary mesh code {link['code']!r}"
                    )
                if link["code"] in codes:
                    logger.warning("Primary mesh {} listed more than once", link["code"])
                codes[link["code"]] = None
            page += 1
        found = list(codes)
        if len(found) != vintage.expected_file_count:
            raise EstatDownloadError(
                f"census {vintage.census_year} ({vintage.stats_id}): expected "
                f"{vintage.expected_file_count} primary-mesh downloads, found {len(found)} "
                f"across {last_page} listing page(s) — update expected_file_count if e-Stat "
                "changed the publication"
            )
        logger.info(
            "Census {}: {} primary meshes across {} listing page(s)",
            vintage.census_year,
            len(found),
            last_page,
        )
        return found

    def _fetch_listing_page(self, vintage: CensusVintage, page: int) -> tuple[str, str]:
        url = vintage.listing_detail_url(page)
        logger.info("Fetching listing page {}: {}", page, url)
        response = self._get(url)
        try:
            payload = json.loads(response.content)
            return str(payload["detail"]), str(payload["paginate"])
        except (ValueError, KeyError, TypeError) as exc:
            raise EstatDownloadError(
                f"{url} did not return the listing JSON (not JSON or missing "
                f"detail/paginate): {response.content[:120]!r}"
            ) from exc

    # ----------------------------------------------------------------- archives

    def download(self, vintage: CensusVintage, primary_mesh_code: str, force: bool = False) -> Path:
        """Download one primary mesh's zip archive into ``zip_dir(vintage)``.

        Parameters
        ----------
        vintage : CensusVintage
            Census the archive belongs to.
        primary_mesh_code : str
            Four-digit 第１次地域区画 code, e.g. ``"5339"``.
        force : bool, default False
            Re-download even if the archive already exists locally.

        Returns
        -------
        pathlib.Path
            Path to the downloaded (or cached) zip archive.

        Raises
        ------
        EstatDownloadError
            If the response is not a zip archive or does not hold exactly the
            expected text member (nothing is written in that case).
        requests.HTTPError
            If e-Stat responds with an error status.
        """
        dest = self.zip_path_for(vintage, primary_mesh_code)
        if dest.exists() and not force:
            logger.info("Using cached archive: {}", dest)
            return dest
        url = vintage.download_url(primary_mesh_code)
        logger.info("Downloading {} -> {}", url, dest)
        response = self._get(url)
        content = response.content
        self._validate_archive(io.BytesIO(content), vintage.member_name(primary_mesh_code), url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated archive at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        partial.replace(dest)
        logger.info("Saved {} ({} bytes)", dest, dest.stat().st_size)
        return dest

    def extract(self, vintage: CensusVintage, primary_mesh_code: str) -> Path:
        """Extract the text member of a downloaded archive, unmodified.

        Parameters
        ----------
        vintage : CensusVintage
            Census the archive belongs to.
        primary_mesh_code : str
            Four-digit 第１次地域区画 code.

        Returns
        -------
        pathlib.Path
            Path of the extracted ``tbl{stats_id}H{code}.txt`` (CP932, CRLF,
            exactly the archived bytes).

        Raises
        ------
        FileNotFoundError
            If the archive has not been downloaded.
        EstatDownloadError
            If the archive does not hold exactly the expected member.
        """
        zip_path = self.zip_path_for(vintage, primary_mesh_code)
        if not zip_path.exists():
            raise FileNotFoundError(f"archive not downloaded: {zip_path}")
        member = vintage.member_name(primary_mesh_code)
        self._validate_archive(zip_path, member, str(zip_path))
        with zipfile.ZipFile(zip_path) as archive:
            content = archive.read(member)
        dest = self.txt_path_for(vintage, primary_mesh_code)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        partial.replace(dest)
        return dest

    @staticmethod
    def _validate_archive(source: Path | io.BytesIO, member: str, label: str) -> None:
        if not zipfile.is_zipfile(source):
            raise EstatDownloadError(f"{label} is not a zip archive")
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
        if len(names) != 1:
            raise EstatDownloadError(
                f"{label}: expected exactly one member named {member}, found {names!r}"
            )
        if names[0] != member:
            raise EstatDownloadError(f"{label}: expected member {member}, found {names[0]!r}")

    # ----------------------------------------------------------------- orchestration

    def download_vintage(self, vintage: CensusVintage, force: bool = False) -> list[Path]:
        """Discover, download (or reuse) and extract every primary mesh of a vintage.

        Parameters
        ----------
        vintage : CensusVintage
            Census to fetch.
        force : bool, default False
            Re-download archives that are already cached.

        Returns
        -------
        list of pathlib.Path
            Extracted text files in listing order.
        """
        codes = self.discover_primary_mesh_codes(vintage)
        paths = []
        for code in codes:
            self.download(vintage, code, force=force)
            paths.append(self.extract(vintage, code))
        logger.info(
            "Census {}: {} text file(s) in {}",
            vintage.census_year,
            len(paths),
            self.txt_dir(vintage),
        )
        return paths

    def download_all(self, years: list[int] | None = None, force: bool = False) -> list[Path]:
        """Fetch every configured vintage (or the given census years).

        Parameters
        ----------
        years : list of int, optional
            Census years to fetch; defaults to every entry of :data:`VINTAGES`.
        force : bool, default False
            Re-download archives that are already cached.

        Returns
        -------
        list of pathlib.Path
            Extracted text files of all requested vintages, oldest census first.

        Raises
        ------
        KeyError
            If a year has no configured vintage.
        """
        vintages = list(VINTAGES) if years is None else [vintage_for_year(y) for y in years]
        paths: list[Path] = []
        for vintage in vintages:
            paths.extend(self.download_vintage(vintage, force=force))
        return paths

    # ----------------------------------------------------------------- HTTP

    def _get(self, url: str) -> requests.Response:
        self._throttle()
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response

    def _throttle(self) -> None:
        """Sleep so consecutive HTTP requests are ``request_interval`` apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.monotonic()
