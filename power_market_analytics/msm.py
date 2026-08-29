"""JMA MSM GPV surface forecasts from the Kyoto University RISH archive: download, decode, load.

The MSM (メソ数値予報モデル) GPV is JMA's mesoscale numerical weather
prediction product, published on a Japan-region surface grid (505 rows x 481
columns, 0.05 deg latitude x 0.0625 deg longitude) several times a day at
different forecast horizons. Kyoto University's RISH mirrors the raw GRIB2
files at a stable, publicly reachable URL
(https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/), one
directory per issue date and one file per forecast-hour band.

This module ingests a single vintage per delivery day D: the 12 UTC run of
D-2, the latest run whose forecast horizon (FH51) still reaches every hour
of D and which is safely published before the demand-forecast model's
09:30 JST D-1 cutoff (:func:`issue_cutoff_for`) — using a later run would
leak information the demand model could not have seen. :func:`source_files_for`
maps a delivery day to the three GRIB2 files that cover it (forecast hours
28-51, i.e. hour-endings 01:00-24:00 JST); :func:`reference_at_for` and
:func:`valid_at_for` do the run/hour arithmetic; :func:`select_grid_point`
picks the nearest MSM grid point to a station.

One archive member is a plain concatenation of GRIB2 messages, one per
(element, forecast hour), each carrying a full 505 x 481 grid of values.
:func:`extract_station_records` walks those messages with ecCodes, identifies
each one by its metadata (never by its position in the file), samples the grid
at the point nearest every station and returns one record per station and
forecast hour, with the pipeline's unit conversions already applied. The
decoder is deliberately strict — a silently short or mislabeled extract would
become a silently wrong forecast feature downstream:

* the run stamped on every message must equal the reference time the caller
  asked for, the edition must be GRIB2 and the data must be operational
  (``productionStatusOfProcessedData = 0``);
* an element's ``typeOfFirstFixedSurface`` must match
  :data:`MSM_SURFACE_ELEMENTS`, and the grid must scan i-fastest
  (``jPointsAreConsecutive = 0``);
* every configured element must be present for every used forecast hour — a
  missing message raises rather than yielding a record with a hole. Only a
  bitmap (an explicitly missing *value*) produces ``None``.

Messages the pipeline does not use — an unconfigured parameter, or a forecast
hour outside ``source_file.leads_used`` — are skipped, and only one message's
values are held in memory at a time.

:class:`MsmDownloader` fetches each delivery day's three files from RISH
(sequentially, throttled, with bounded retries), decodes them and writes one
``csv.gz`` extract plus a JSON manifest per day; :class:`MsmForecastCsvLoader`
brings those extracts into a raw warehouse table. Protocol, file format and
the GRIB2 element/grid metadata this module trusts: docs/JMA-MSM-GPV-Retrieval.md.
"""

from __future__ import annotations

import csv
import datetime
import glob
import gzip
import hashlib
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import eccodes
import requests
from loguru import logger

from power_market_analytics.csv_loader import CsvLoader

BASE_URL = "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original"
#: 12 UTC runs only reach forecast hour 51 (needed for the full delivery day)
#: from this date; earlier archive members are incomplete for this pipeline.
EARLIEST_DELIVERY_DATE = datetime.date(2019, 4, 1)
#: Default start of a full historical backfill (matches the other refresh tasks).
DEFAULT_BACKFILL_START = datetime.date(2022, 4, 1)
#: Japan Standard Time: a fixed UTC+9 offset, no daylight saving.
JST = datetime.timezone(datetime.timedelta(hours=9))


def _now() -> datetime.datetime:
    """Return the current instant in JST.

    A seam so :func:`default_end_date` is testable: tests monkeypatch this
    function to freeze "now" rather than depending on the real clock.

    Returns
    -------
    datetime.datetime
        Timezone-aware JST.
    """
    return datetime.datetime.now(JST)


def default_end_date() -> datetime.date:
    """Return the default upper bound of an MSM download range.

    Returns
    -------
    datetime.date
        JST "today" (:func:`_now`) plus one day — the default
        ``--end-date`` of ``scripts/download_jma_msm_surface_forecast.py``.
    """
    return _now().date() + datetime.timedelta(days=1)


class MsmError(RuntimeError):
    """Base error for MSM download, extraction and lookup failures."""


@dataclass(frozen=True)
class MsmStation:
    """A JMA staffed station whose location is used to sample the MSM grid.

    Attributes
    ----------
    station_id : str
        JMA station id, e.g. ``"s47662"``.
    latitude, longitude : float
        Station coordinates in decimal degrees.
    """

    station_id: str
    latitude: float
    longitude: float


def load_stations(stations_csv: Path | str, station_areas_csv: Path | str) -> list[MsmStation]:
    """Load every JMA staffed station mapped to a JEPX area, sorted by id.

    Every station in ``stations_csv`` is kept, active or discontinued — the
    MSM forecast is extracted for all of them so a later re-scope of the
    demand task can use any of them without a re-backfill.

    Parameters
    ----------
    stations_csv : pathlib.Path or str
        Path to ``dbt/seeds/jma_stations.csv`` (UTF-8, header includes
        ``station_id``, ``latitude``, ``longitude``).
    station_areas_csv : pathlib.Path or str
        Path to ``dbt/seeds/jma_station_areas.csv`` (UTF-8, header includes
        ``station_id``); extra rows with no matching station are ignored.

    Returns
    -------
    list of MsmStation
        Sorted by ``station_id``.

    Raises
    ------
    MsmError
        If any station in ``stations_csv`` has no mapping row in
        ``station_areas_csv`` or has an empty latitude/longitude, naming the
        offending station ids.
    """
    with open(station_areas_csv, encoding="utf-8", newline="") as f:
        mapped_station_ids = {row["station_id"] for row in csv.DictReader(f)}
    with open(stations_csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    missing_mapping = sorted(
        row["station_id"] for row in rows if row["station_id"] not in mapped_station_ids
    )
    missing_coordinates = sorted(
        row["station_id"]
        for row in rows
        if not row["latitude"].strip() or not row["longitude"].strip()
    )
    if missing_mapping or missing_coordinates:
        problems = []
        if missing_mapping:
            problems.append(f"no jma_station_areas.csv mapping row: {missing_mapping}")
        if missing_coordinates:
            problems.append(f"empty latitude/longitude: {missing_coordinates}")
        raise MsmError(f"{stations_csv}: " + "; ".join(problems))

    stations = [
        MsmStation(
            station_id=row["station_id"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
        )
        for row in rows
    ]
    return sorted(stations, key=lambda s: s.station_id)


def reference_at_for(delivery_date: datetime.date) -> datetime.datetime:
    """Return the ingested forecast run's issue time for a delivery day.

    Parameters
    ----------
    delivery_date : datetime.date
        Day D whose 24 hourly forecasts are wanted.

    Returns
    -------
    datetime.datetime
        Timezone-aware UTC: 12:00 UTC on D-2 (the 21:00 JST D-2 run).
    """
    reference_date = delivery_date - datetime.timedelta(days=2)
    return datetime.datetime(
        reference_date.year,
        reference_date.month,
        reference_date.day,
        12,
        0,
        tzinfo=datetime.timezone.utc,
    )


def issue_cutoff_for(delivery_date: datetime.date) -> datetime.datetime:
    """Return the demand-forecast model's leakage cutoff for a delivery day.

    Parameters
    ----------
    delivery_date : datetime.date
        Day D being forecast.

    Returns
    -------
    datetime.datetime
        Timezone-aware JST: 09:30 JST on D-1 — the same cutoff the demand
        task issues its forecast at, so any MSM run used as a feature must be
        available (:func:`reference_at_for`) strictly before this instant.
    """
    cutoff_date = delivery_date - datetime.timedelta(days=1)
    return datetime.datetime(
        cutoff_date.year, cutoff_date.month, cutoff_date.day, 9, 30, tzinfo=JST
    )


@dataclass(frozen=True)
class MsmSourceFile:
    """One RISH GRIB2 archive member covering a band of forecast hours.

    Attributes
    ----------
    file_name : str
        Archive member name, e.g.
        ``"Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin"``.
    url : str
        Full download URL (``BASE_URL`` + the reference date's ``YYYY/MM/DD``
        directory + ``file_name``).
    leads_used : range
        Forecast-hour leads this pipeline reads from the file (a subset of
        the leads the file physically contains — the FH16-33 file's leads
        16-27 are never used).
    """

    file_name: str
    url: str
    leads_used: range


#: (forecast-hour band label, leads actually used from that file), in the
#: order the files must be processed.
_SOURCE_FILE_BANDS: tuple[tuple[str, range], ...] = (
    ("FH16-33", range(28, 34)),
    ("FH34-39", range(34, 40)),
    ("FH40-51", range(40, 52)),
)


def source_files_for(
    delivery_date: datetime.date,
) -> tuple[MsmSourceFile, MsmSourceFile, MsmSourceFile]:
    """Return the three GRIB2 files that cover a delivery day's 24 hours.

    Parameters
    ----------
    delivery_date : datetime.date
        Day D whose forecasts are wanted.

    Returns
    -------
    tuple of MsmSourceFile
        Exactly three, in processing order: FH16-33 (leads 28-33), FH34-39
        (leads 34-39), FH40-51 (leads 40-51).
    """
    reference_at = reference_at_for(delivery_date)
    reference_stamp = reference_at.strftime("%Y%m%d")
    directory = reference_at.strftime("%Y/%m/%d")

    def _file(band: str, leads_used: range) -> MsmSourceFile:
        file_name = f"Z__C_RJTD_{reference_stamp}120000_MSM_GPV_Rjp_Lsurf_{band}_grib2.bin"
        return MsmSourceFile(
            file_name=file_name, url=f"{BASE_URL}/{directory}/{file_name}", leads_used=leads_used
        )

    fh16_33, fh34_39, fh40_51 = _SOURCE_FILE_BANDS
    return _file(*fh16_33), _file(*fh34_39), _file(*fh40_51)


def valid_at_for(delivery_date: datetime.date, lead_hours: int) -> datetime.datetime:
    """Return the UTC instant a forecast lead represents.

    Parameters
    ----------
    delivery_date : datetime.date
        Delivery day D the lead belongs to.
    lead_hours : int
        Forecast lead in hours from the run's reference time (28-51 for the
        leads this pipeline uses).

    Returns
    -------
    datetime.datetime
        Timezone-aware UTC: ``reference_at_for(delivery_date) + lead_hours``.
    """
    return reference_at_for(delivery_date) + datetime.timedelta(hours=lead_hours)


def hour_ending_for(lead_hours: int) -> int:
    """Return the JST hour-ending (1-24) a forecast lead represents.

    Parameters
    ----------
    lead_hours : int
        Forecast lead in hours (28-51).

    Returns
    -------
    int
        1-24 (lead 28 -> 1, lead 51 -> 24).

    Raises
    ------
    ValueError
        If ``lead_hours`` is outside 28-51.
    """
    if not 28 <= lead_hours <= 51:
        raise ValueError(f"lead_hours must be 28..51 (got {lead_hours})")
    return lead_hours - 27


def time_codes_for(hour_ending: int) -> tuple[int, int]:
    """Return the pair of JEPX 30-minute time codes an hour-ending covers.

    Parameters
    ----------
    hour_ending : int
        JST hour-ending, 1-24.

    Returns
    -------
    tuple of int
        ``(2 * hour_ending - 1, 2 * hour_ending)``; downstream reverses this
        with ``hour_ending = (time_code + 1) // 2``.

    Raises
    ------
    ValueError
        If ``hour_ending`` is outside 1-24.
    """
    if not 1 <= hour_ending <= 24:
        raise ValueError(f"hour_ending must be 1..24 (got {hour_ending})")
    return (2 * hour_ending - 1, 2 * hour_ending)


def kelvin_to_celsius(v: float) -> float:
    """Convert a temperature from kelvin to degrees Celsius."""
    return v - 273.15


def pa_to_hpa(v: float) -> float:
    """Convert a pressure from pascals to hectopascals."""
    return v / 100


def wind_speed(u: float, v: float) -> float:
    """Return wind speed (m/s) from its u/v components (m/s)."""
    return math.sqrt(u**2 + v**2)


def wm2_to_mjm2(v: float) -> float:
    """Convert an hourly-mean flux from W/m^2 to a per-hour total in MJ/m^2."""
    return v * 3600 / 1e6


#: Earth radius (km) used for haversine distances, matching the WGS84 mean
#: radius convention used elsewhere in the repo's geo code.
EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two points in kilometers.

    Parameters
    ----------
    lat1, lon1, lat2, lon2 : float
        Coordinates in decimal degrees.

    Returns
    -------
    float
        Distance in kilometers (``EARTH_RADIUS_KM = 6371.0088``).
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


@dataclass(frozen=True)
class MsmGrid:
    """Geometry of one GRIB message's regular lat/lon grid, as read from the file.

    Attributes
    ----------
    ni : int
        Points per row (longitude direction).
    nj : int
        Number of rows (latitude direction).
    first_latitude, first_longitude : float
        Coordinates of grid index (i=0, j=0), in decimal degrees.
    latitude_step : float
        Signed degrees between consecutive rows; negative when the grid
        scans north to south.
    longitude_step : float
        Signed degrees between consecutive columns; negative when
        ``iScansNegatively``.
    """

    ni: int
    nj: int
    first_latitude: float
    first_longitude: float
    latitude_step: float
    longitude_step: float


@dataclass(frozen=True)
class SelectedGridPoint:
    """The MSM grid point nearest a queried location.

    Attributes
    ----------
    latitude, longitude : float
        Coordinates of the selected grid point.
    flat_index : int
        ``j * grid.ni + i`` — the position of this point in a row-major,
        i-fastest flattened value array (the scan order GRIB values use).
    distance_km : float
        Haversine distance from the query point, rounded to 3 decimals.
    """

    latitude: float
    longitude: float
    flat_index: int
    distance_km: float


#: Floating-point slack for the domain-boundary check (see the function
#: docstring's Notes).
_DOMAIN_EPSILON = 1e-9


def select_grid_point(grid: MsmGrid, latitude: float, longitude: float) -> SelectedGridPoint:
    """Return the MSM grid point nearest a station location.

    Parameters
    ----------
    grid : MsmGrid
        Grid geometry read from the GRIB message.
    latitude, longitude : float
        Query location in decimal degrees.

    Returns
    -------
    SelectedGridPoint

    Raises
    ------
    MsmError
        If the query location falls outside the grid's extent (inclusive of
        its four corners).

    Notes
    -----
    Ties (an exact half-step fraction) resolve toward the lower index on
    each axis — the point encountered first in the grid's scan order. The
    domain bounds tolerate a tiny (``1e-9``) floating-point slack so an exact
    corner is never rejected merely because ``grid.latitude_step`` /
    ``grid.longitude_step`` (e.g. 0.05) is not exactly representable in
    binary floating point.
    """
    j_exact = (latitude - grid.first_latitude) / grid.latitude_step
    i_exact = (longitude - grid.first_longitude) / grid.longitude_step
    if not (-_DOMAIN_EPSILON <= j_exact <= grid.nj - 1 + _DOMAIN_EPSILON) or not (
        -_DOMAIN_EPSILON <= i_exact <= grid.ni - 1 + _DOMAIN_EPSILON
    ):
        raise MsmError(
            f"({latitude}, {longitude}) is outside the MSM domain "
            f"(nj={grid.nj}, ni={grid.ni}, first_latitude={grid.first_latitude}, "
            f"first_longitude={grid.first_longitude}, latitude_step={grid.latitude_step}, "
            f"longitude_step={grid.longitude_step})"
        )
    j = _nearest_index(j_exact)
    i = _nearest_index(i_exact)
    selected_latitude = grid.first_latitude + j * grid.latitude_step
    selected_longitude = grid.first_longitude + i * grid.longitude_step
    return SelectedGridPoint(
        latitude=selected_latitude,
        longitude=selected_longitude,
        flat_index=j * grid.ni + i,
        distance_km=round(
            haversine_km(latitude, longitude, selected_latitude, selected_longitude), 3
        ),
    )


def _nearest_index(exact: float) -> int:
    """Round a fractional grid index to the nearest integer, ties toward the lower index."""
    low = math.floor(exact)
    return low if exact - low <= 0.5 else low + 1


@dataclass(frozen=True)
class MsmElement:
    """One MSM surface GRIB2 parameter this pipeline extracts.

    Attributes
    ----------
    key : str
        Canonical stem naming the record field this element fills (e.g.
        ``"temperature_k"``).
    discipline, parameter_category, parameter_number : int
        GRIB2 parameter identification (``discipline``/``parameterCategory``/
        ``parameterNumber``).
    surface_type : int
        Expected ``typeOfFirstFixedSurface``.
    statistical : bool
        True for a 1-hour accumulation/average (precipitation, shortwave
        radiation); False for an instantaneous value valid at the lead hour.
    """

    key: str
    discipline: int
    parameter_category: int
    parameter_number: int
    surface_type: int
    statistical: bool


#: The 12 surface elements this pipeline extracts from every MSM message,
#: keyed by (discipline, parameterCategory, parameterNumber). Surface types
#: are asserted when decoding (:func:`extract_station_records`); if the real
#: files disagree with a value here, fix the constant, not the decoder's assertion.
MSM_SURFACE_ELEMENTS: tuple[MsmElement, ...] = (
    MsmElement("surface_pressure_pa", 0, 3, 0, 1, False),
    MsmElement("sea_level_pressure_pa", 0, 3, 1, 101, False),
    MsmElement("u_wind_ms", 0, 2, 2, 103, False),
    MsmElement("v_wind_ms", 0, 2, 3, 103, False),
    MsmElement("temperature_k", 0, 0, 0, 103, False),
    MsmElement("relative_humidity_pct", 0, 1, 1, 103, False),
    MsmElement("precipitation_mm", 0, 1, 8, 1, True),
    MsmElement("shortwave_radiation_wm2", 0, 4, 7, 1, True),
    MsmElement("total_cloud_cover_pct", 0, 6, 1, 1, False),
    MsmElement("low_cloud_cover_pct", 0, 6, 3, 1, False),
    MsmElement("middle_cloud_cover_pct", 0, 6, 4, 1, False),
    MsmElement("high_cloud_cover_pct", 0, 6, 5, 1, False),
)

_ELEMENTS_BY_PARAMETER: dict[tuple[int, int, int], MsmElement] = {
    (element.discipline, element.parameter_category, element.parameter_number): element
    for element in MSM_SURFACE_ELEMENTS
}


def element_for(
    discipline: int, parameter_category: int, parameter_number: int
) -> MsmElement | None:
    """Return the configured element for a GRIB2 parameter triple, if any.

    Parameters
    ----------
    discipline, parameter_category, parameter_number : int
        GRIB2 parameter identification read from a message.

    Returns
    -------
    MsmElement or None
        None if the triple is not one of :data:`MSM_SURFACE_ELEMENTS`
        (:func:`extract_station_records` skips the message).
    """
    return _ELEMENTS_BY_PARAMETER.get((discipline, parameter_category, parameter_number))


#: Exact header order of the per-day extract (``csv.gz``): written by
#: :meth:`MsmDownloader._write_csv`, read by name through the raw load contract.
RAW_CSV_COLUMNS: tuple[str, ...] = (
    "station_id",
    "station_latitude",
    "station_longitude",
    "grid_latitude",
    "grid_longitude",
    "grid_distance_km",
    "forecast_reference_at_utc",
    "forecast_valid_at_utc",
    "forecast_lead_hours",
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
    "source_file_name",
)


# --------------------------------------------------------------------------- GRIB2 decoding

#: Only GRIB2 messages are decoded (the MSM archive is GRIB2 throughout).
GRIB_EDITION = 2
#: ``productionStatusOfProcessedData`` of operational data — the only status
#: this pipeline accepts (test/research runs must never reach the warehouse).
OPERATIONAL_PRODUCTION_STATUS = 0
#: GRIB2 product definition templates the MSM surface members use: 0 for an
#: instantaneous field, 8 for a field statistically processed over a time
#: interval (the 1-hour precipitation accumulation and shortwave mean flux).
INSTANTANEOUS_TEMPLATE = 0
STATISTICAL_TEMPLATE = 8
#: ecCodes ``stepUnits`` code for hours — pinned before any step key is read so
#: leads come back in hours whatever unit a member codes them in.
HOUR_STEP_UNIT = 1
#: Decimal places every extracted value is rounded to.
VALUE_PRECISION = 6

#: Keys of :attr:`StationHourRecord.values`, in order: exactly the value
#: columns of :data:`RAW_CSV_COLUMNS` (between the
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
        :func:`select_grid_point`).
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
        (:func:`select_grid_point`).

    Notes
    -----
    A forecast hour is always the message's ``endStep``: instantaneous elements
    are valid *at* that hour, statistical ones (precipitation, downward
    shortwave radiation) cover the hour *ending* at it, i.e. the interval
    ``(endStep - 1, endStep]``. The decoder asserts that encoding per message
    (:func:`_check_step_encoding`) — template 8 over exactly
    ``(endStep - 1, endStep]`` for a statistical element, template 0 at
    ``endStep`` for an instantaneous one — so a cumulative or re-templated field
    is rejected rather than published as hourly. Both land on the same record,
    which the pipeline reads as the hour ending at ``forecast_valid_at``.

    JMA packs many fields into one GRIB2 message envelope — a whole archive
    member is a *single* envelope (the FH16-33 file holds 12 elements x 18
    forecast hours = 216 fields in it) — so ecCodes' multi-field support has
    to be on or the message loop sees only the first field of each envelope.
    It is process-global ecCodes state that other code (ecCodes' own multi
    writer, for one) can flip, so it is re-asserted on every call; the call is
    idempotent and costs nothing.
    """
    eccodes.codes_grib_multi_support_on()
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
                # Step keys are read in hours whatever unit the member codes them in.
                eccodes.codes_set(message, "stepUnits", HOUR_STEP_UNIT)
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
                _check_step_encoding(message, grib_path, element, lead_hours)
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


def _check_step_encoding(
    message: int, grib_path: Path, element: MsmElement, lead_hours: int
) -> None:
    """Reject a message whose product template or step interval does not fit its element.

    Selecting a message by parameter triple and ``endStep`` alone would also
    accept a cumulative ``(0, lead]`` accumulation, or an instantaneous field
    re-templated as an interval, and publish it as the one-hour value the
    warehouse promises. The template and the interval start are therefore
    asserted against what :class:`MsmElement`
    declares.

    Parameters
    ----------
    message : int
        Open ecCodes message handle (step keys already pinned to hours).
    grib_path : pathlib.Path
        File the message came from (named in the error).
    element : MsmElement
        The matched element (supplies ``statistical``).
    lead_hours : int
        The message's ``endStep``.

    Raises
    ------
    MsmExtractError
        If a statistical element is not product definition template 8 over
        exactly ``(lead_hours - 1, lead_hours]``, or an instantaneous element
        is not template 0 at ``lead_hours``.
    """
    template = eccodes.codes_get(message, "productDefinitionTemplateNumber", int)
    start_step = eccodes.codes_get(message, "startStep", int)
    if element.statistical:
        expected_template, expected_start = STATISTICAL_TEMPLATE, lead_hours - 1
        kind = "a one-hour statistical interval"
    else:
        expected_template, expected_start = INSTANTANEOUS_TEMPLATE, lead_hours
        kind = "an instantaneous value"
    if template != expected_template or start_step != expected_start:
        raise MsmExtractError(
            f"{grib_path.name}: {element.key} (lead {lead_hours}) is encoded with "
            f"productDefinitionTemplateNumber={template} over steps {start_step}-{lead_hours}, "
            f"expected template {expected_template} over steps {expected_start}-{lead_hours} "
            f"({kind}) — a cumulative or re-templated field must not be published as hourly"
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
        Values keyed by :attr:`MsmElement.key`, in
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
    """A GRIB2 archive member could not be downloaded: HTTP 404 (the file is
    absent) or a completed download's content failed validation (empty body,
    or missing the GRIB2 magic bytes)."""


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


# --------------------------------------------------------------------------- raw loader


class MsmForecastCsvLoader(CsvLoader):
    """Full reload of extracted MSM forecast ``csv.gz`` files into a warehouse table."""

    def _resolve_files(self) -> list[str]:
        if self.filepath.is_dir():
            files = sorted(str(p) for p in self.filepath.glob("*.csv.gz"))
        else:
            files = sorted(glob.glob(str(self.filepath)))
        if not files:
            raise FileNotFoundError(f"No MSM forecast csv.gz files found at {self.filepath}")
        return files
