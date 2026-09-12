"""Download the census 500 m population mesh from e-Stat, one zip per 第１次地域区画.

The listing rows come from the ``search_detail`` JSON endpoint rather than the
HTML page; each archive is generated server-side on request (~10 s), validated
before it is cached, and its single text member extracted byte for byte. The
protocol and the file format are documented in
``docs/eStat-Census-Population-Mesh-Retrieval.md``.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

import requests
from loguru import logger

from power_market_analytics.ingestion.estat.vintages import (
    VINTAGES,
    CensusVintage,
    vintage_for_year,
)


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
