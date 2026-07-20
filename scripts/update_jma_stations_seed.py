"""Regenerate the JMA station master dbt seed.

Scrapes the station master (id, name, kana, prefecture, coordinates,
elevation, observed-element mask, end-of-observation date) from the JMA
obsdl per-prefecture station pages and rewrites dbt/seeds/jma_stations.csv
as UTF-8 with ISO dates. Roughly 60 requests at polite spacing, so expect
~5 minutes. dim_jma_station is built from this seed.
"""

import logging
from pathlib import Path

from power_market_analytics.jma import JmaStationMasterDownloader

SEED_PATH = Path(__file__).resolve().parents[1] / "dbt/seeds/jma_stations.csv"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    downloader = JmaStationMasterDownloader(dest=SEED_PATH)
    path = downloader.download(force=True)
    print(f"Station master seed written to {path}")


if __name__ == "__main__":
    main()
