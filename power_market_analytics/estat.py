"""Download and load Japan's census 500 m population-mesh tables from e-Stat 統計GIS.

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

:class:`EstatCensusMeshCsvLoader` brings the downloaded text files into a raw
warehouse table. Each ``tbl{statsId}H{primary}.txt`` is a CP932
comma-separated table with two header rows — the source codes
(``KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847001,…``) and then Japanese labels
— followed by one row per nine-digit mesh. Only the total-population column
is loaded, and its header differs per census (``T000847001`` in 2015,
``T001101001`` in 2020), so the loader identifies each file's vintage from
its name (``statsId`` → :data:`VINTAGES`), reads the files of one vintage and
header layout in a single Spark scan, selects that vintage's population
column, injects the vintage attributes and each file's primary mesh code, and
validates every row per file in one grouped pass before casting: total population must be a non-negative
integer (it is never ``*``-suppressed), mesh codes must be well-formed and
lie inside the file's primary mesh, and ``HTKSYORI`` must be 0, 1 or 2.
Anything else fails the load before writing.
"""

from __future__ import annotations

import datetime
import glob
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from loguru import logger
from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvLoader, CsvTableSchema

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


# --------------------------------------------------------------------- loader

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
        :data:`VINTAGES`.
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

    def _read_all(self, files: list[str]) -> DataFrame:
        names = [Path(file).name for file in files]
        if len(set(names)) != len(names):
            clash = next(name for name in names if names.count(name) > 1)
            raise ValueError(
                f"{names.count(clash)} files share the file name {clash}: the primary mesh "
                "code is joined back on the name, so every file name must be unique"
            )
        # One scan per (vintage, exact header line): Spark applies the first
        # file's header to every file of a multi-path read, so files whose
        # columns are ordered differently must not share a scan.
        groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        vintages: dict[str, CensusVintage] = {}
        codes: dict[str, str] = {}
        for file in files:
            vintage, primary_mesh_code = self._identify(file)
            header = self._check_headers(file, vintage)
            groups.setdefault((vintage.stats_id, tuple(header)), []).append(file)
            vintages[vintage.stats_id] = vintage
            codes[Path(file).name] = primary_mesh_code
        frames = [
            self._read_group(vintages[stats_id], group, codes)
            for (stats_id, _), group in groups.items()
        ]
        return reduce(DataFrame.unionByName, frames)

    def _read_group(
        self, vintage: CensusVintage, files: list[str], codes: dict[str, str]
    ) -> DataFrame:
        """Read files that share ``vintage`` and a header line in one scan.

        Parameters
        ----------
        vintage : CensusVintage
            The census the files belong to (population column, attributes).
        files : list of str
            Paths with identical header lines.
        codes : dict of str to str
            File name → primary mesh code, for every file.

        Returns
        -------
        pyspark.sql.DataFrame
            Contract columns plus ``SOURCE_FILE_COL``, rows validated.
        """
        lookup = self.spark.createDataFrame(
            [(Path(file).name, codes[Path(file).name]) for file in files],
            f"{SOURCE_FILE_COL} string, {PRIMARY_MESH_CODE_SOURCE} string",
        )
        raw = (
            self.spark.read.options(header="true", **self.schema.read_options)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, F.col("_metadata.file_name"))
            .join(F.broadcast(lookup), SOURCE_FILE_COL, "inner")
        )
        self._check_rows(raw, vintage)
        data = (
            raw.filter(F.col(KEY_CODE).isNotNull())
            .withColumn(CENSUS_YEAR_SOURCE, F.lit(vintage.census_year))
            .withColumn(CENSUS_DATE_SOURCE, F.lit(vintage.census_date))
            .withColumn(GEODETIC_DATUM_SOURCE, F.lit(vintage.geodetic_datum))
            .withColumn(STATS_ID_SOURCE, F.lit(vintage.stats_id))
            .withColumn(POPULATION_SOURCE, F.col(vintage.population_source_column))
            .withColumn(SOURCE_FILE_SOURCE, F.col(SOURCE_FILE_COL))
        )
        return self._project(data)

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

    def _check_headers(self, file: str, vintage: CensusVintage) -> list[str]:
        """Verify the two header rows before Spark reads the file.

        Returns
        -------
        list of str
            The header row (source codes), for grouping files by layout.

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
        return header

    def _check_rows(self, raw: DataFrame, vintage: CensusVintage) -> None:
        """Validate every data row of a scan, reporting per file.

        Parameters
        ----------
        raw : pyspark.sql.DataFrame
            A header-based scan carrying ``SOURCE_FILE_COL`` and
            ``PRIMARY_MESH_CODE_SOURCE``.
        vintage : CensusVintage
            Supplies the population column to check.

        Raises
        ------
        ValueError
            Named after the first offending file: a malformed mesh code, a
            mesh code outside that file's primary mesh, a population that is
            not a non-negative integer literal (``*`` included), ``HTKSYORI``
            outside 0/1/2, or a number of rows without a ``KEY_CODE`` other
            than exactly one (the label row).
        """
        key = F.col(KEY_CODE)
        population = F.col(vintage.population_source_column)
        privacy = F.col(PRIVACY_CODE)
        checks: list[tuple[str, Column]] = [
            ("mesh code", key.isNotNull() & ~key.rlike(MESH_CODE_RE.pattern)),
            (
                "mesh code outside primary mesh {code}",
                key.isNotNull() & ~key.startswith(F.col(PRIMARY_MESH_CODE_SOURCE)),
            ),
            (
                f"population ({vintage.population_source_column}) not a non-negative integer",
                key.isNotNull() & (population.isNull() | ~population.rlike(_POPULATION_RE)),
            ),
            (
                f"{PRIVACY_CODE} not in {list(_ACCEPTED_PRIVACY_CODES)}",
                key.isNotNull() & ~privacy.isin(*_ACCEPTED_PRIVACY_CODES),
            ),
        ]
        counts = (
            raw.groupBy(SOURCE_FILE_COL, PRIMARY_MESH_CODE_SOURCE)
            .agg(
                F.count(F.when(key.isNull(), True)).alias("__label_rows"),
                *[
                    F.count(F.when(cond, True)).alias(f"__c{i}")
                    for i, (_, cond) in enumerate(checks)
                ],
            )
            .orderBy(SOURCE_FILE_COL)
            .collect()
        )
        for row in counts:
            file = row[SOURCE_FILE_COL]
            if row["__label_rows"] != 1:
                raise ValueError(
                    f"{file}: expected exactly one label row without a KEY_CODE, found "
                    f"{row['__label_rows']} rows with an empty KEY_CODE"
                )
            for i, (label, cond) in enumerate(checks):
                n_bad = row[f"__c{i}"]
                if n_bad:
                    examples = [
                        (r[KEY_CODE], r[PRIVACY_CODE], r[vintage.population_source_column])
                        for r in raw.filter((F.col(SOURCE_FILE_COL) == file) & cond)
                        .select(KEY_CODE, PRIVACY_CODE, vintage.population_source_column)
                        .limit(_EXAMPLE_LIMIT)
                        .collect()
                    ]
                    raise ValueError(
                        f"{file}: {n_bad} row(s) with "
                        f"{label.format(code=row[PRIMARY_MESH_CODE_SOURCE])}; first "
                        f"(KEY_CODE, HTKSYORI, population): {examples}"
                    )
        logger.debug("{}: header and row checks passed", [r[SOURCE_FILE_COL] for r in counts])
