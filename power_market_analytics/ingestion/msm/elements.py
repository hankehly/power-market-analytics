"""The GRIB2 parameters this pipeline extracts, their units, and the extract's columns.

A message is identified by its ``(discipline, parameterCategory, parameterNumber)``
triple — never by its position in the file. :data:`RAW_CSV_COLUMNS` is the header
of the per-day ``csv.gz`` extract: the element keys plus the values derived and
converted from them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


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
