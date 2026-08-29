"""Download the TEPCO でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

Fetches the yearly ``juyo-YYYY.csv`` files (2016 … 2022, immutable — cached
after the first run unless ``--force-yearly``) and always re-downloads every
monthly ``YYYYMM_power_usage.zip`` from 2022-04 to the current month (~53
zips, ~4 MB), extracting the daily ``YYYYMMDD_power_usage.csv`` members next
to the yearly files under ``csv/``.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.tepco.power_usage import TepcoPowerUsageDownloader


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/tepco/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ yearly + extracted daily files).",
    )
    parser.add_argument(
        "--force-yearly",
        action="store_true",
        help="Re-download the yearly juyo-YYYY.csv files even when cached.",
    )
    args = parser.parse_args(argv)

    downloader = TepcoPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all(force_yearly=args.force_yearly)
    logger.info("Downloaded {} source file(s) into {}", len(paths), downloader.csv_dir)


if __name__ == "__main__":
    main()
