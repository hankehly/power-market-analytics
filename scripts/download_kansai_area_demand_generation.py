"""Download the 関西電力送配電 エリア需給・発電（実績） monthly archives and extract the daily CSVs.

Always re-downloads every month from 2022-04 to the current month (~53 zips,
~2 MB in total): Kansai revises past days occasionally and refreshes the
current month's archive daily, and re-fetching everything is the simplest way
to stay consistent with the published history.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.kansai import KansaiAreaDownloader


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/area_demand_generation"),
        help="Root directory for Kansai area files (zip/ archives, csv/ extracted actuals).",
    )
    args = parser.parse_args(argv)

    downloader = KansaiAreaDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} actuals file(s) into {}", len(paths), downloader.csv_dir)


if __name__ == "__main__":
    main()
