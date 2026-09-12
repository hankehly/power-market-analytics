"""500 m mesh codes (JIS X 0410 4次メッシュ): validation and geometry.

Pure arithmetic on the code itself — no I/O and no Spark. The standardized dbt
model decodes the same bounds in SQL; this is the reference implementation.
"""

from __future__ import annotations

import re
from typing import NamedTuple

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
