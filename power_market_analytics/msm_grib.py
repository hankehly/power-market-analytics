"""Decode JMA MSM surface GRIB2 files into per-station hourly forecast records.

One archive member (see :func:`power_market_analytics.msm.source_files_for`) is
a plain concatenation of GRIB2 messages, one per (element, forecast hour), each
carrying a full 505 x 481 grid of values. :func:`extract_station_records` walks
those messages with ecCodes, identifies each one by its metadata (never by its
position in the file), samples the grid at the point nearest every station and
returns one record per station and forecast hour, with the pipeline's unit
conversions already applied.

The decoder is deliberately strict — a silently short or mislabeled extract
would become a silently wrong forecast feature downstream:

* the run stamped on every message must equal the reference time the caller
  asked for, the edition must be GRIB2 and the data must be operational
  (``productionStatusOfProcessedData = 0``);
* an element's ``typeOfFirstFixedSurface`` must match
  :data:`~power_market_analytics.msm.MSM_SURFACE_ELEMENTS`, and the grid must
  scan i-fastest (``jPointsAreConsecutive = 0``);
* every configured element must be present for every used forecast hour — a
  missing message raises rather than yielding a record with a hole. Only a
  bitmap (an explicitly missing *value*) produces ``None``.

Messages the pipeline does not use — an unconfigured parameter, or a forecast
hour outside ``source_file.leads_used`` — are skipped, and only one message's
values are held in memory at a time.
"""

from __future__ import annotations

import csv
import datetime
import gzip
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import eccodes
import requests
from loguru import logger

from power_market_analytics.msm import (
    EARLIEST_DELIVERY_DATE,
    MSM_SURFACE_ELEMENTS,
    RAW_CSV_COLUMNS,
    MsmError,
    MsmGrid,
    MsmSourceFile,
    MsmStation,
    SelectedGridPoint,
    element_for,
    kelvin_to_celsius,
    pa_to_hpa,
    reference_at_for,
    select_grid_point,
    source_files_for,
    wind_speed,
    wm2_to_mjm2,
)

#: Only GRIB2 messages are decoded (the MSM archive is GRIB2 throughout).
GRIB_EDITION = 2
#: ``productionStatusOfProcessedData`` of operational data — the only status
#: this pipeline accepts (test/research runs must never reach the warehouse).
OPERATIONAL_PRODUCTION_STATUS = 0
#: Decimal places every extracted value is rounded to.
VALUE_PRECISION = 6

#: Keys of :attr:`StationHourRecord.values`, in order: exactly the value
#: columns of :data:`~power_market_analytics.msm.RAW_CSV_COLUMNS` (between the
#: nine identifying columns and ``source_file_name``), which the downloader
#: writes by name.
VALUE_COLUMNS: tuple[str, ...] = (
    "temperature_c",
    "relative_humidity_pct",
    "u_wind_ms",
    "v_wind_ms",
    "wind_speed_ms",
    "precipitation_mm",
    "surface_pressure_hpa",
    "sea_level_pressure_hpa",
    "shortwave_radiation_wm2",
    "solar_radiation_mjm2",
    "total_cloud_cover_pct",
    "high_cloud_cover_pct",
    "middle_cloud_cover_pct",
    "low_cloud_cover_pct",
)


class MsmExtractError(MsmError):
    """A GRIB2 file could not be decoded into a complete set of station records."""


@dataclass(frozen=True)
class StationHourRecord:
    """One station's forecast for one forecast hour of one run.

    Attributes
    ----------
    station_id : str
        JMA station id the record was sampled for.
    station_latitude, station_longitude : float
        The station's own coordinates, in decimal degrees.
    grid_latitude, grid_longitude : float
        Coordinates of the MSM grid point actually read.
    grid_distance_km : float
        Distance from the station to that grid point (rounded to 3 decimals by
        :func:`~power_market_analytics.msm.select_grid_point`).
    forecast_reference_at : datetime.datetime
        Timezone-aware UTC issue time of the run.
    forecast_valid_at : datetime.datetime
        Timezone-aware UTC instant the forecast is valid at
        (``forecast_reference_at + forecast_lead_hours``).
    forecast_lead_hours : int
        Forecast hour (the message's ``endStep``).
    values : dict of str to float or None
        Converted values keyed by :data:`VALUE_COLUMNS`, in that order. A key
        is ``None`` only when the GRIB bitmap marks the grid point missing (or
        when an input of a derived value is).
    source_file_name : str
        Archive member the record was decoded from.
    """

    station_id: str
    station_latitude: float
    station_longitude: float
    grid_latitude: float
    grid_longitude: float
    grid_distance_km: float
    forecast_reference_at: datetime.datetime
    forecast_valid_at: datetime.datetime
    forecast_lead_hours: int
    values: dict[str, float | None]
    source_file_name: str


def extract_station_records(
    grib_path: Path,
    source_file: MsmSourceFile,
    reference_at: datetime.datetime,
    stations: Sequence[MsmStation],
) -> list[StationHourRecord]:
    """Decode one MSM archive member into per-station hourly records.

    Parameters
    ----------
    grib_path : pathlib.Path
        Downloaded GRIB2 file (a concatenation of messages).
    source_file : MsmSourceFile
        Spec of the member being decoded; ``leads_used`` selects the forecast
        hours to keep and ``file_name`` is stamped on every record.
    reference_at : datetime.datetime
        Timezone-aware UTC issue time every message must carry.
    stations : sequence of MsmStation
        Stations to sample; one record per station and used forecast hour.

    Returns
    -------
    list of StationHourRecord
        Sorted by ``(station_id, forecast_lead_hours)``.

    Raises
    ------
    MsmExtractError
        If a message is not operational GRIB2, carries a different run, uses an
        unexpected surface type or scan order, or if any (element, forecast
        hour) the pipeline needs has no message in the file.
    MsmError
        If a station falls outside the file's grid
        (:func:`~power_market_analytics.msm.select_grid_point`).

    Notes
    -----
    A forecast hour is always the message's ``endStep``: instantaneous elements
    are valid *at* that hour, statistical ones (precipitation, downward
    shortwave radiation) cover the hour *ending* at it, i.e. the interval
    ``(endStep - 1, endStep]``. Both land on the same record, which the
    pipeline reads as the hour ending at ``forecast_valid_at``.
    """
    leads_used = set(source_file.leads_used)
    # (station id, lead) -> element key -> value, filled message by message.
    element_values: dict[tuple[str, int], dict[str, float | None]] = {}
    # Grid point each station was sampled at; every message of a file shares
    # one grid, so this is written once per distinct grid, with the selection.
    station_points: dict[str, SelectedGridPoint] = {}
    # Grid point selection is the only per-station geodesy; cache it per grid.
    selections: dict[MsmGrid, tuple[SelectedGridPoint, ...]] = {}
    # (element key, lead) pairs an actual message supplied.
    decoded: set[tuple[str, int]] = set()
    message_count = 0

    with open(grib_path, "rb") as f:
        while (message := eccodes.codes_grib_new_from_file(f)) is not None:
            try:
                message_count += 1
                _check_message_identity(message, grib_path, reference_at)
                element = element_for(
                    eccodes.codes_get(message, "discipline", int),
                    eccodes.codes_get(message, "parameterCategory", int),
                    eccodes.codes_get(message, "parameterNumber", int),
                )
                if element is None:
                    continue
                lead_hours = eccodes.codes_get(message, "endStep", int)
                if lead_hours not in leads_used:
                    continue
                surface_type = eccodes.codes_get(message, "typeOfFirstFixedSurface", int)
                if surface_type != element.surface_type:
                    raise MsmExtractError(
                        f"{grib_path.name}: {element.key} (lead {lead_hours}) has "
                        f"typeOfFirstFixedSurface={surface_type}, expected "
                        f"{element.surface_type} — the MSM file format changed"
                    )
                grid = _read_grid(message, grib_path)
                points = selections.get(grid)
                if points is None:
                    points = tuple(
                        select_grid_point(grid, station.latitude, station.longitude)
                        for station in stations
                    )
                    selections[grid] = points
                    station_points = {
                        station.station_id: point
                        for station, point in zip(stations, points, strict=True)
                    }
                grid_values = eccodes.codes_get_values(message)
                missing_value = (
                    eccodes.codes_get(message, "missingValue", float)
                    if eccodes.codes_get(message, "bitmapPresent", int) == 1
                    else None
                )
                for station, point in zip(stations, points, strict=True):
                    value = float(grid_values[point.flat_index])
                    station_values = element_values.setdefault((station.station_id, lead_hours), {})
                    station_values[element.key] = None if value == missing_value else value
                decoded.add((element.key, lead_hours))
            finally:
                eccodes.codes_release(message)

    absent = [
        (element.key, lead_hours)
        for lead_hours in source_file.leads_used
        for element in MSM_SURFACE_ELEMENTS
        if (element.key, lead_hours) not in decoded
    ]
    if absent:
        raise MsmExtractError(
            f"{grib_path.name}: {len(absent)} of "
            f"{len(MSM_SURFACE_ELEMENTS) * len(source_file.leads_used)} expected messages are "
            f"absent — no (element, lead) message for {absent}"
        )

    records = [
        StationHourRecord(
            station_id=station.station_id,
            station_latitude=station.latitude,
            station_longitude=station.longitude,
            grid_latitude=station_points[station.station_id].latitude,
            grid_longitude=station_points[station.station_id].longitude,
            grid_distance_km=station_points[station.station_id].distance_km,
            forecast_reference_at=reference_at,
            forecast_valid_at=reference_at + datetime.timedelta(hours=lead_hours),
            forecast_lead_hours=lead_hours,
            values=_derived_values(element_values[(station.station_id, lead_hours)]),
            source_file_name=source_file.file_name,
        )
        for station in stations
        for lead_hours in source_file.leads_used
    ]
    records.sort(key=lambda record: (record.station_id, record.forecast_lead_hours))
    logger.debug(
        "{}: read {} messages -> {} records ({} stations x {} leads)",
        grib_path.name,
        message_count,
        len(records),
        len(stations),
        len(source_file.leads_used),
    )
    return records


def _check_message_identity(message: int, grib_path: Path, reference_at: datetime.datetime) -> None:
    """Reject a message that is not operational GRIB2 from the expected run.

    Parameters
    ----------
    message : int
        Open ecCodes message handle.
    grib_path : pathlib.Path
        File the message came from (named in the error).
    reference_at : datetime.datetime
        Timezone-aware UTC issue time the message must carry.

    Raises
    ------
    MsmExtractError
        On a non-GRIB2 edition, a non-operational production status, or a
        ``dataDate``/``dataTime`` other than ``reference_at``.
    """
    edition = eccodes.codes_get(message, "editionNumber", int)
    if edition != GRIB_EDITION:
        raise MsmExtractError(
            f"{grib_path.name}: editionNumber={edition}, expected GRIB{GRIB_EDITION}"
        )
    production_status = eccodes.codes_get(message, "productionStatusOfProcessedData", int)
    if production_status != OPERATIONAL_PRODUCTION_STATUS:
        raise MsmExtractError(
            f"{grib_path.name}: productionStatusOfProcessedData={production_status}, expected "
            f"{OPERATIONAL_PRODUCTION_STATUS} (operational data)"
        )
    data_date = eccodes.codes_get(message, "dataDate", int)
    data_time = eccodes.codes_get(message, "dataTime", int)
    expected_date = int(reference_at.strftime("%Y%m%d"))
    expected_time = reference_at.hour * 100 + reference_at.minute
    if (data_date, data_time) != (expected_date, expected_time):
        raise MsmExtractError(
            f"{grib_path.name}: message run {data_date} {data_time:04d} is not the expected "
            f"{expected_date} {expected_time:04d}"
        )


def _read_grid(message: int, grib_path: Path) -> MsmGrid:
    """Read a message's regular lat/lon geometry, with scan-direction signs applied.

    Parameters
    ----------
    message : int
        Open ecCodes message handle.
    grib_path : pathlib.Path
        File the message came from (named in the error).

    Returns
    -------
    MsmGrid
        ``latitude_step`` is positive only when ``jScansPositively`` and
        ``longitude_step`` negative when ``iScansNegatively``, so grid index
        arithmetic follows the order the values are stored in.

    Raises
    ------
    MsmExtractError
        If the grid scans j-fastest (``jPointsAreConsecutive = 1``), which
        would invalidate the flat ``j * ni + i`` indexing.
    """
    j_points_are_consecutive = eccodes.codes_get(message, "jPointsAreConsecutive", int)
    if j_points_are_consecutive != 0:
        raise MsmExtractError(
            f"{grib_path.name}: jPointsAreConsecutive={j_points_are_consecutive}; only "
            "i-fastest (row-major) MSM grids are supported"
        )
    latitude_increment = eccodes.codes_get(message, "jDirectionIncrementInDegrees", float)
    longitude_increment = eccodes.codes_get(message, "iDirectionIncrementInDegrees", float)
    scans_north = eccodes.codes_get(message, "jScansPositively", int) == 1
    scans_west = eccodes.codes_get(message, "iScansNegatively", int) == 1
    return MsmGrid(
        ni=eccodes.codes_get(message, "Ni", int),
        nj=eccodes.codes_get(message, "Nj", int),
        first_latitude=eccodes.codes_get(message, "latitudeOfFirstGridPointInDegrees", float),
        first_longitude=eccodes.codes_get(message, "longitudeOfFirstGridPointInDegrees", float),
        latitude_step=latitude_increment if scans_north else -latitude_increment,
        longitude_step=-longitude_increment if scans_west else longitude_increment,
    )


def _derived_values(element_values: dict[str, float | None]) -> dict[str, float | None]:
    """Convert one station-hour's raw element values into the record's value columns.

    Parameters
    ----------
    element_values : dict of str to float or None
        Values keyed by :attr:`~power_market_analytics.msm.MsmElement.key`, in
        their GRIB units; ``None`` where the bitmap marked the point missing.

    Returns
    -------
    dict of str to float or None
        Keyed by :data:`VALUE_COLUMNS` in that order, rounded to
        :data:`VALUE_PRECISION` decimals. A derived value is ``None`` when any
        of its inputs is.
    """
    u_wind = element_values["u_wind_ms"]
    v_wind = element_values["v_wind_ms"]
    shortwave_radiation = element_values["shortwave_radiation_wm2"]
    return {
        "temperature_c": _converted(element_values["temperature_k"], kelvin_to_celsius),
        "relative_humidity_pct": _rounded(element_values["relative_humidity_pct"]),
        "u_wind_ms": _rounded(u_wind),
        "v_wind_ms": _rounded(v_wind),
        "wind_speed_ms": (
            None if u_wind is None or v_wind is None else _rounded(wind_speed(u_wind, v_wind))
        ),
        "precipitation_mm": _rounded(element_values["precipitation_mm"]),
        "surface_pressure_hpa": _converted(element_values["surface_pressure_pa"], pa_to_hpa),
        "sea_level_pressure_hpa": _converted(element_values["sea_level_pressure_pa"], pa_to_hpa),
        "shortwave_radiation_wm2": _rounded(shortwave_radiation),
        "solar_radiation_mjm2": _converted(shortwave_radiation, wm2_to_mjm2),
        "total_cloud_cover_pct": _rounded(element_values["total_cloud_cover_pct"]),
        "high_cloud_cover_pct": _rounded(element_values["high_cloud_cover_pct"]),
        "middle_cloud_cover_pct": _rounded(element_values["middle_cloud_cover_pct"]),
        "low_cloud_cover_pct": _rounded(element_values["low_cloud_cover_pct"]),
    }


def _rounded(value: float | None) -> float | None:
    """Round a value to :data:`VALUE_PRECISION` decimals, passing ``None`` through."""
    return None if value is None else round(value, VALUE_PRECISION)


def _converted(value: float | None, convert: Callable[[float], float]) -> float | None:
    """Apply a unit conversion then :func:`_rounded`, passing ``None`` through."""
    return None if value is None else round(convert(value), VALUE_PRECISION)


# --------------------------------------------------------------------------- downloader

#: Bytes streamed per GRIB2 download chunk (1 MiB).
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
#: Every GRIB2 file (edition 1 or 2) begins with this 4-byte marker; the
#: cheapest possible check that a download is a GRIB archive and not, say, an
#: HTML error page RISH served with a 200 status.
GRIB_MAGIC = b"GRIB"
#: Hours a delivery day always has (one lead each) — the sanity check on a
#: completed extract's row count.
HOURS_PER_DELIVERY_DAY = 24


class MsmDownloadError(MsmError):
    """A GRIB2 file could not be downloaded, or a delivery day's extraction
    did not produce a complete csv.gz extract."""


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
    :func:`~power_market_analytics.msm.source_files_for` names, decoding them
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
        HTTP session to issue ``get`` calls with; defaults to a fresh
        :class:`requests.Session`. Injected mainly for tests.
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
        self.session = session if session is not None else requests.Session()
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
            empty forecast; nothing is written) or if the downloaded content
            is empty or does not start with the GRIB2 magic bytes (the
            ``.part`` file written so far is deleted).
        requests.RequestException
            If every attempt (``max_attempts``) fails for another reason.
        """
        dest = self.grib_path_for(source_file)
        if dest.exists() and not force:
            sha256_hex = hashlib.sha256(dest.read_bytes()).hexdigest()
            logger.info("Using cached GRIB: {} (sha256={})", dest, sha256_hex)
            return dest, sha256_hex

        logger.info("Downloading {} -> {}", source_file.url, dest)
        response = self._get_streaming(source_file.url)

        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
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
                f"{source_file.url}: downloaded content is not a GRIB2 file "
                f"({size} bytes, starts with {head!r})"
            )
        partial.replace(dest)
        sha256_hex = digest.hexdigest()
        logger.info("Saved {} ({} bytes, sha256={})", dest, size, sha256_hex)
        return dest, sha256_hex

    def _get_streaming(self, url: str) -> requests.Response:
        """GET a URL with streaming enabled, retrying transient failures.

        Raises
        ------
        MsmDownloadError
            On HTTP 404 — not retried, since a missing archive member is a
            completeness failure rather than a transient one.
        requests.RequestException
            If every attempt (``max_attempts``) still fails.
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
                return response
            except MsmDownloadError:
                raise
            except requests.RequestException as exc:
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
            is not ``len(stations) * 24``. Nothing is written at the final
            csv path and the GRIB2 files downloaded so far are left in place
            for inspection.
        """
        csv_path = self.csv_path_for(delivery_date)
        if csv_path.exists() and not force:
            logger.info("Using cached extract: {}", csv_path)
            return csv_path

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

        self._write_csv(csv_path, records)
        self._write_manifest(
            self.manifest_path_for(delivery_date), delivery_date, reference_at, manifest_files
        )

        if not keep_grib:
            for grib_path in grib_paths:
                grib_path.unlink(missing_ok=True)
        logger.info("Extracted {}: {} records -> {}", delivery_date, len(records), csv_path)
        return csv_path

    def _write_csv(self, path: Path, records: list[StationHourRecord]) -> None:
        """Write one delivery day's records as a gzip CSV, atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        try:
            with gzip.open(partial, "wt", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(RAW_CSV_COLUMNS)
                for record in records:
                    writer.writerow(self._csv_row(record))
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

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
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        payload = {
            "delivery_date": delivery_date.isoformat(),
            "reference_at_utc": reference_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": files,
        }
        try:
            partial.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

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
            :data:`~power_market_analytics.msm.EARLIEST_DELIVERY_DATE`.
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
