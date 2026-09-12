"""Fetch a delivery day's GRIB2 files from the RISH archive and write its extract.

Kyoto University's RISH mirrors JMA's raw GRIB2 files at a stable, publicly
reachable URL, one directory per issue date and one file per forecast-hour band.
Each delivery day needs three of them. They are fetched sequentially, throttled,
with bounded retries, decoded, and written as one ``csv.gz`` extract plus a JSON
manifest; the GRIB2 files are deleted after a successful extract by default.

RISH has served a stale intermediate certificate since its leaf was renewed on
2026-05-28, which certifi cannot complete into a chain, so the missing CA is
vendored next to this module and trusted on top of certifi. Drop both once RISH
fixes the chain — the check is in ``docs/JMA-MSM-GPV-Retrieval.md`` §8.4.
"""

from __future__ import annotations

import csv
import datetime
import gzip
import hashlib
import json
import ssl
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import certifi
import requests
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from power_market_analytics.ingestion.msm.elements import RAW_CSV_COLUMNS
from power_market_analytics.ingestion.msm.errors import MsmDownloadError, MsmExtractError
from power_market_analytics.ingestion.msm.grib import (
    VALUE_COLUMNS,
    StationHourRecord,
    extract_station_records,
)
from power_market_analytics.ingestion.msm.stations import MsmStation
from power_market_analytics.ingestion.msm.vintage import (
    EARLIEST_DELIVERY_DATE,
    MsmSourceFile,
    reference_at_for,
    source_files_for,
)

#: Bytes streamed per GRIB2 download chunk (1 MiB).
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
#: Every GRIB2 file (edition 1 or 2) begins with this 4-byte marker; the
#: cheapest possible check that a download is a GRIB archive and not, say, an
#: HTML error page RISH served with a 200 status.
GRIB_MAGIC = b"GRIB"
#: Hours a delivery day always has (one lead each) — the sanity check on a
#: completed extract's row count.
HOURS_PER_DELIVERY_DAY = 24
#: The intermediate CA that issues RISH's TLS certificate ("NII Open Domain CA - G8 RSA",
#: SECOM Trust Systems; valid to 2040-08-21; the leaf's AIA URL is
#: http://repo1.secomtrust.net/sppca/nii/odca4/nii-odca4g8rsa.cer, sha256 fingerprint
#: 7A:4A:D9:E1:BA:2D:FB:08:F7:52:A1:24:03:2F:70:58:86:80:62:E9:84:17:85:62:3E:B4:13:67:83:A5:3F:FC).
#: Since a 2026-05-28 renewal the server sends the *previous* intermediate (G7) with its
#: G8-issued leaf — an incomplete chain that certifi's roots cannot complete, so a plain
#: ``requests`` call fails with ``unable to get local issuer certificate``. Trusting G8
#: directly (a trust anchor, which Python's partial-chain verification accepts) closes the
#: gap; see docs/JMA-MSM-GPV-Retrieval.md §8.4.
RISH_INTERMEDIATE_CA_PEM = Path(__file__).with_name("certs") / "nii-open-domain-ca-g8-rsa.pem"


class _TrustStoreAdapter(HTTPAdapter):
    """An :class:`~requests.adapters.HTTPAdapter` that verifies servers against one
    fixed :class:`ssl.SSLContext`.

    :meth:`build_connection_pool_key_attributes` is the seam requests documents for a
    custom context. The context is set whenever verification is on; the other keys
    requests derives from ``verify`` are kept, so a user CA bundle (``verify=<path>``,
    which is what ``REQUESTS_CA_BUNDLE`` becomes) is still loaded into the context by
    urllib3, and ``verify=False`` still turns verification off.
    """

    def __init__(self, ssl_context: ssl.SSLContext, **kwargs: Any) -> None:
        self.ssl_context = ssl_context
        super().__init__(**kwargs)

    def build_connection_pool_key_attributes(
        self,
        request: requests.PreparedRequest,
        verify: bool | str,
        cert: str | tuple[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        if verify is not False:
            pool_kwargs["ssl_context"] = self.ssl_context
        return host_params, pool_kwargs


def default_session() -> requests.Session:
    """Return a :class:`requests.Session` that trusts RISH's intermediate CA as well.

    The HTTPS adapter verifies against requests' own default trust store — a
    urllib3 context loaded with certifi's roots — plus :data:`RISH_INTERMEDIATE_CA_PEM`,
    so downloads verify although the server's chain is incomplete, with no
    ``REQUESTS_CA_BUNDLE`` to set. Everything else is a stock session.

    Returns
    -------
    requests.Session
        A new session; :class:`MsmDownloader` uses one when no ``session`` is injected.
    """
    context = create_urllib3_context()
    context.load_verify_locations(cafile=certifi.where())
    context.load_verify_locations(cafile=str(RISH_INTERMEDIATE_CA_PEM))
    session = requests.Session()
    session.mount("https://", _TrustStoreAdapter(context))
    return session


class MsmDownloader:
    """Download RISH MSM GRIB2 archives and extract one csv.gz per delivery day.

    Downloads are sequential and throttled — politeness toward the RISH
    archive, an academic mirror with no published rate limit of its own: one
    HTTP request in flight at a time, at least ``request_interval`` seconds
    apart, with bounded retries on transient failures. A GRIB2 archive member
    RISH has not (yet) published is a **completeness failure**
    (:class:`MsmDownloadError` naming the URL, on HTTP 404) — the pipeline
    never silently produces a forecast with fewer than the expected
    station-hours.

    Each delivery day D reads the three GRIB2 files
    :func:`source_files_for` names, decoding them
    one at a time — never more than one file's messages in memory, see
    :func:`extract_station_records` — and writes one gzip CSV extract plus a
    JSON manifest recording every source file's URL, sha256 and size. Both
    are written atomically (a ``.part`` file, then
    :meth:`~pathlib.Path.replace`), so an interrupted run never leaves a
    truncated extract at its final path.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``Path("data/jma/msm_surface_forecast")``
        Root directory; GRIB2 downloads land in ``data_dir/"grib"``, csv.gz
        extracts and manifests in ``data_dir/"csv"``.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to issue ``get`` calls with; defaults to
        :func:`default_session` (a fresh session that also trusts RISH's
        intermediate CA). Injected mainly for tests.
    request_interval : float, default 1.0
        Minimum seconds between consecutive HTTP requests, and the base unit
        of the retry backoff (the n-th retry waits ``request_interval * n``
        seconds).
    max_attempts : int, default 3
        Total attempts (including the first) per file before a transient
        failure (a non-404 HTTP error or ``requests.RequestException``) is
        re-raised.
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/jma/msm_surface_forecast"),
        timeout: float = 60.0,
        session: requests.Session | None = None,
        request_interval: float = 1.0,
        max_attempts: int = 3,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.grib_dir = self.data_dir / "grib"
        self.csv_dir = self.data_dir / "csv"
        self.timeout = timeout
        self.session = session if session is not None else default_session()
        self.request_interval = request_interval
        self.max_attempts = max_attempts
        self._last_request_at = -float("inf")

    # ----------------------------------------------------------------- paths

    def csv_path_for(self, delivery_date: datetime.date) -> Path:
        """Return the path of a delivery day's csv.gz extract."""
        return self.csv_dir / f"msm_surface_{delivery_date.strftime('%Y%m%d')}.csv.gz"

    def manifest_path_for(self, delivery_date: datetime.date) -> Path:
        """Return the path of a delivery day's manifest JSON."""
        return self.csv_dir / f"msm_surface_{delivery_date.strftime('%Y%m%d')}.json"

    def grib_path_for(self, source_file: MsmSourceFile) -> Path:
        """Return the local path a source file's GRIB2 download is cached at."""
        return self.grib_dir / source_file.file_name

    # ----------------------------------------------------------------- download

    def download_file(self, source_file: MsmSourceFile, force: bool = False) -> tuple[Path, str]:
        """Download (or reuse) one GRIB2 archive member.

        Parameters
        ----------
        source_file : MsmSourceFile
            Archive member to fetch (name + URL).
        force : bool, default False
            Re-download even if the file already exists locally.

        Returns
        -------
        tuple of (pathlib.Path, str)
            Local path and the sha256 hex digest of its contents (recomputed
            from the cached file when reused, computed while streaming
            otherwise).

        Raises
        ------
        MsmDownloadError
            On HTTP 404 (the archive member is absent — a RISH publication
            gap or a run not yet published, never silently treated as an
            empty forecast; nothing is written) or if a completed attempt's
            content is empty or does not start with the GRIB2 magic bytes
            (the ``.part`` file written so far is deleted). Neither case is
            retried.
        requests.RequestException
            If every attempt (``max_attempts``) still fails with a
            transport-level error (connection error, timeout, a failure
            while streaming the body, ...).
        """
        dest = self.grib_path_for(source_file)
        if dest.exists() and not force:
            sha256_hex = hashlib.sha256(dest.read_bytes()).hexdigest()
            logger.info("Using cached GRIB: {} (sha256={})", dest, sha256_hex)
            return dest, sha256_hex

        logger.info("Downloading {} -> {}", source_file.url, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        sha256_hex = self._stream_to_file(source_file.url, partial)
        partial.replace(dest)
        logger.info("Saved {} ({} bytes, sha256={})", dest, dest.stat().st_size, sha256_hex)
        return dest, sha256_hex

    def _stream_to_file(self, url: str, partial: Path) -> str:
        """GET a URL and stream its body to ``partial``, retrying whole attempts.

        Every attempt — the GET, streaming the body and validating it — is
        made inside the bounded retry scope, so a transport-level failure at
        any point during an attempt (not just on the initial GET) is
        retried; each retried attempt starts ``partial`` over from empty and
        recomputes its sha256 from scratch.

        Parameters
        ----------
        url : str
            URL to GET.
        partial : pathlib.Path
            Destination the streamed body is written to.

        Returns
        -------
        str
            sha256 hex digest of the downloaded content.

        Raises
        ------
        MsmDownloadError
            On HTTP 404, or if a completed attempt's content is empty or
            does not start with the GRIB2 magic bytes — neither is retried,
            and ``partial`` is deleted before the error is raised.
        requests.RequestException
            If a transport-level failure occurs on every attempt
            (``max_attempts``); each such failure deletes ``partial`` and,
            unless it was the last attempt, retries after a
            ``request_interval * attempt`` backoff.
        """
        attempt = 1
        while True:
            self._throttle()
            try:
                response = self.session.get(url, timeout=self.timeout, stream=True)
                if response.status_code == 404:
                    raise MsmDownloadError(
                        f"{url}: HTTP 404 — archive file is absent (a RISH publication gap "
                        "or a run not yet published), not an empty forecast"
                    )
                response.raise_for_status()
                return self._write_body(response, url, partial)
            except MsmDownloadError:
                raise
            except requests.RequestException as exc:
                partial.unlink(missing_ok=True)
                if attempt >= self.max_attempts:
                    raise
                wait = self.request_interval * attempt
                logger.warning(
                    "{}: {} (attempt {}/{}); retrying in {:.1f}s",
                    url,
                    exc,
                    attempt,
                    self.max_attempts,
                    wait,
                )
                time.sleep(wait)
                attempt += 1

    @staticmethod
    def _write_body(response: requests.Response, url: str, partial: Path) -> str:
        """Stream one response's body to ``partial`` and validate it as GRIB2.

        Parameters
        ----------
        response : requests.Response
            Streaming response whose body is consumed via ``iter_content``.
        url : str
            Source URL, named in a validation-failure error.
        partial : pathlib.Path
            File the body is written to (opened fresh, so a retried attempt
            never mixes bytes with an earlier failed one).

        Returns
        -------
        str
            sha256 hex digest of the written content.

        Raises
        ------
        MsmDownloadError
            If the content is empty or does not start with the GRIB2 magic
            bytes (``partial`` is deleted first).
        requests.RequestException
            Propagated as-is if ``iter_content`` fails mid-stream; ``partial``
            is left in place with whatever was written so far — the caller
            deletes it before retrying.
        """
        digest = hashlib.sha256()
        size = 0
        head = b""
        with open(partial, "wb") as f:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                if not head:
                    head = chunk[:4]
                digest.update(chunk)
                size += len(chunk)
                f.write(chunk)
        if size == 0 or not head.startswith(GRIB_MAGIC):
            partial.unlink(missing_ok=True)
            raise MsmDownloadError(
                f"{url}: downloaded content is not a GRIB2 file "
                f"({size} bytes, starts with {head!r})"
            )
        return digest.hexdigest()

    def _throttle(self) -> None:
        """Sleep so consecutive HTTP requests are ``request_interval`` apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_at = time.monotonic()

    # ----------------------------------------------------------------- extraction

    def extract_day(
        self,
        delivery_date: datetime.date,
        stations: Sequence[MsmStation],
        force: bool = False,
        keep_grib: bool = False,
    ) -> Path:
        """Download, decode and write one delivery day's csv.gz extract.

        Parameters
        ----------
        delivery_date : datetime.date
            Delivery day D.
        stations : sequence of MsmStation
            Stations to sample.
        force : bool, default False
            Re-download every GRIB2 file and rebuild the extract even if a
            cached csv.gz already exists.
        keep_grib : bool, default False
            Keep the three downloaded GRIB2 files after a successful extract
            (they are deleted by default to bound disk usage across a
            backfill).

        Returns
        -------
        pathlib.Path
            Path of the csv.gz extract (:meth:`csv_path_for`).

        Raises
        ------
        MsmDownloadError
            If a source file cannot be downloaded (see :meth:`download_file`).
        MsmExtractError
            If a source file cannot be decoded (see
            :func:`extract_station_records`) or the day's total record count
            is not ``len(stations) * 24``.

        Notes
        -----
        On any failure — download, decode, the record-count check, or either
        write — nothing is ever left at :meth:`csv_path_for`'s path (the
        manifest is written first and the csv.gz last, so the csv commit is
        the sole "this day is done" signal the cache check above trusts) and
        the GRIB2 files downloaded so far are left in place for inspection.
        A later non-``force`` call therefore always re-attempts a day that
        previously failed partway, rather than silently treating it as done.
        A ``force`` rebuild removes the day's existing csv.gz and manifest
        first, for the same reason: a forced rebuild that fails must not leave
        the stale extract it was meant to replace looking complete.
        """
        csv_path = self.csv_path_for(delivery_date)
        if csv_path.exists() and not force:
            logger.info("Using cached extract: {}", csv_path)
            return csv_path
        if force:
            # A forced rebuild exists to replace a possibly-bad extract, so the
            # old csv.gz (the cache signal) and manifest go BEFORE the network is
            # touched: a rebuild that fails partway then leaves the day visibly
            # incomplete instead of a stale "done" a later non-force run trusts.
            csv_path.unlink(missing_ok=True)
            self.manifest_path_for(delivery_date).unlink(missing_ok=True)

        reference_at = reference_at_for(delivery_date)
        source_files = source_files_for(delivery_date)
        records: list[StationHourRecord] = []
        grib_paths: list[Path] = []
        manifest_files: list[dict[str, object]] = []
        for source_file in source_files:
            grib_path, sha256_hex = self.download_file(source_file, force=force)
            grib_paths.append(grib_path)
            records.extend(extract_station_records(grib_path, source_file, reference_at, stations))
            manifest_files.append(
                {
                    "file_name": source_file.file_name,
                    "url": source_file.url,
                    "sha256": sha256_hex,
                    "size_bytes": grib_path.stat().st_size,
                }
            )

        expected = len(stations) * HOURS_PER_DELIVERY_DAY
        if len(records) != expected:
            raise MsmExtractError(
                f"{delivery_date}: expected {expected} records "
                f"({len(stations)} stations x {HOURS_PER_DELIVERY_DAY} hours), got {len(records)}"
            )
        records.sort(key=lambda r: (r.station_id, r.forecast_lead_hours))

        # The manifest is written first and the csv.gz last: csv_path.exists()
        # is the sole "this day is done" cache check above, so the csv commit
        # must be the final, unlocking step. If either write fails, both are
        # atomic on their own (.part -> replace, cleaned up on error), so a
        # failure here never leaves a file at csv_path — a later non-force
        # call always re-attempts instead of silently trusting a half-written
        # day.
        self._write_manifest(
            self.manifest_path_for(delivery_date), delivery_date, reference_at, manifest_files
        )
        self._write_csv(csv_path, records)

        if not keep_grib:
            for grib_path in grib_paths:
                grib_path.unlink(missing_ok=True)
        logger.info("Extracted {}: {} records -> {}", delivery_date, len(records), csv_path)
        return csv_path

    @staticmethod
    def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
        """Write to ``path`` atomically: ``write`` fills a ``.part`` file, then replace.

        Parameters
        ----------
        path : pathlib.Path
            Final destination.
        write : callable
            ``write(partial_path)`` fills the temporary file's contents.

        Notes
        -----
        On any exception from ``write`` (or from the replace itself), the
        partial file is deleted and the exception re-raised; nothing is ever
        left at ``path`` unless the write fully succeeded, and a prior file
        at ``path`` is untouched until the replace.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        try:
            write(partial)
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _write_csv(self, path: Path, records: list[StationHourRecord]) -> None:
        """Write one delivery day's records as a gzip CSV, atomically."""

        def write(partial: Path) -> None:
            with gzip.open(partial, "wt", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(RAW_CSV_COLUMNS)
                for record in records:
                    writer.writerow(self._csv_row(record))

        self._atomic_write(path, write)

    @staticmethod
    def _csv_row(record: StationHourRecord) -> list[str | int]:
        row: list[str | int] = [
            record.station_id,
            _format_float(record.station_latitude),
            _format_float(record.station_longitude),
            _format_float(record.grid_latitude),
            _format_float(record.grid_longitude),
            _format_float(record.grid_distance_km),
            record.forecast_reference_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            record.forecast_valid_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            record.forecast_lead_hours,
        ]
        row.extend(_format_float(record.values[column]) for column in VALUE_COLUMNS)
        row.append(record.source_file_name)
        return row

    def _write_manifest(
        self,
        path: Path,
        delivery_date: datetime.date,
        reference_at: datetime.datetime,
        files: list[dict[str, object]],
    ) -> None:
        """Write a delivery day's source-file manifest as JSON, atomically."""
        payload = {
            "delivery_date": delivery_date.isoformat(),
            "reference_at_utc": reference_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": files,
        }

        def write(partial: Path) -> None:
            partial.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        self._atomic_write(path, write)

    # ----------------------------------------------------------------- backfill

    def download_range(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        stations: Sequence[MsmStation],
        force: bool = False,
        keep_grib: bool = False,
    ) -> list[Path]:
        """Extract every delivery day in an inclusive date range.

        Parameters
        ----------
        start_date, end_date : datetime.date
            Inclusive delivery-day range.
        stations : sequence of MsmStation
            Stations to sample, forwarded to :meth:`extract_day`.
        force, keep_grib : bool, default False
            Forwarded to :meth:`extract_day`.

        Returns
        -------
        list of pathlib.Path
            csv.gz extract paths in date order.

        Raises
        ------
        ValueError
            If ``start_date > end_date`` or ``start_date`` is before
            :data:`EARLIEST_DELIVERY_DATE`.
        """
        if start_date > end_date:
            raise ValueError(f"start_date {start_date} is after end_date {end_date}")
        if start_date < EARLIEST_DELIVERY_DATE:
            raise ValueError(
                f"start_date {start_date} is before EARLIEST_DELIVERY_DATE {EARLIEST_DELIVERY_DATE}"
            )
        paths = []
        current = start_date
        while current <= end_date:
            logger.info("MSM extract: {}", current)
            paths.append(self.extract_day(current, stations, force=force, keep_grib=keep_grib))
            current += datetime.timedelta(days=1)
        return paths


def _format_float(value: float | None) -> str:
    """Render a nullable double for the csv extract: '' for None, else str(round(v, 6))."""
    return "" if value is None else str(round(value, 6))
