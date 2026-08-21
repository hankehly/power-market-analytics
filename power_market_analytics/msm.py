"""Pure logic for JMA MSM GPV surface forecasts sourced from the Kyoto University RISH archive.

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

:class:`MsmForecastCsvLoader` brings the per-day ``csv.gz`` extracts (built
by the downloader in a later module) into a raw warehouse table. Protocol,
file format and the GRIB decoding this module's grid/element metadata feeds:
docs/JMA-MSM-GPV-Retrieval.md.
"""

from __future__ import annotations

import csv
import datetime
import glob
import math
from dataclasses import dataclass
from pathlib import Path

from power_market_analytics.csv_loader import CsvLoader

BASE_URL = "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original"
#: 12 UTC runs only reach forecast hour 51 (needed for the full delivery day)
#: from this date; earlier archive members are incomplete for this pipeline.
EARLIEST_DELIVERY_DATE = datetime.date(2019, 4, 1)
#: Default start of a full historical backfill (matches the other refresh tasks).
DEFAULT_BACKFILL_START = datetime.date(2022, 4, 1)
#: Japan Standard Time: a fixed UTC+9 offset, no daylight saving.
JST = datetime.timezone(datetime.timedelta(hours=9))


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
#: are asserted when decoding (Task 2/6); if the real files disagree with a
#: value here, fix the constant, not the decoder's assertion.
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
        (the message is skipped by the decode layer).
    """
    return _ELEMENTS_BY_PARAMETER.get((discipline, parameter_category, parameter_number))


#: Exact header order of the per-day extract (``csv.gz``) — see the GRIB
#: decode layer (writes rows) and the raw load contract (reads by name).
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
