"""Regenerate the JMA station master dbt seed (staffed stations only).

Scrapes the station master (id, name, kana, prefecture, coordinates,
elevation, observed-element mask, end-of-observation date) from the JMA
obsdl per-prefecture station pages, keeps only staffed stations (気象官署,
s-prefixed ids — the 2026-08 re-scope; see
docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md) inside a
JEPX area (dropping Okinawa, Antarctica and 南鳥島, so every station maps to
a dim_area row) and rewrites dbt/seeds/jma_stations.csv as UTF-8 with ISO
dates. Roughly 60 requests at polite spacing, so expect ~5 minutes.
dim_jma_station is built from this seed joined to the jma_station_areas
seed, which must have a row for every station id written here.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.jma import JmaStationMasterDownloader

SEED_PATH = Path(__file__).resolve().parents[1] / "dbt/seeds/jma_stations.csv"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=SEED_PATH,
        help="Where to write the station master CSV (default: the dbt seed).",
    )
    args = parser.parse_args(argv)

    downloader = JmaStationMasterDownloader(dest=args.dest, staffed_only=True, jepx_areas_only=True)
    # Always refresh: the point of this script is to pick up new stations and
    # discontinuations, so the cached copy must never be served.
    path = downloader.download(force=True)
    logger.info("Station master seed written to {}", path)


if __name__ == "__main__":
    main()
