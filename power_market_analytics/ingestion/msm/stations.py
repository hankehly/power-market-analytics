"""The stations sampled out of the MSM grid, read from the dbt seeds.

The seeds are the same two the ``dim_jma_station`` model builds from, so the
extract covers exactly the staffed stations inside a JEPX area.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from power_market_analytics.ingestion.msm.errors import MsmError


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
