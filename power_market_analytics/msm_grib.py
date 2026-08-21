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

import datetime
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import eccodes
from loguru import logger

from power_market_analytics.msm import (
    MSM_SURFACE_ELEMENTS,
    MsmError,
    MsmGrid,
    MsmSourceFile,
    MsmStation,
    SelectedGridPoint,
    element_for,
    kelvin_to_celsius,
    pa_to_hpa,
    select_grid_point,
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
