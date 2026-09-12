"""The MSM surface grid, and picking the point nearest a station.

Values are the nearest grid point's, not interpolated and not station-specific:
the Japan-region surface grid is 505 rows x 481 columns (0.05 deg latitude x
0.0625 deg longitude), so a grid point is at most ~3.5 km from any station.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from power_market_analytics.ingestion.msm.errors import MsmError

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
