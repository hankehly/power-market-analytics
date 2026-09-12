"""Download the 関西電力送配電 でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

Always re-downloads every monthly ``YYYYMM_jisseki.zip`` from 2016-04 to the
current month (~126 zips, ~8.5 MB) — Kansai revises past days without notice
— and extracts the daily members (``YYYYMMDD_juyo1_kansai.csv`` through
2025-11, ``juyo_06_YYYYMMDD.csv`` from 2025-12) under ``csv/``. A settled
month must hold every day except 2024-03-31, which Kansai never published.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.tso.kansai.power_usage import KansaiPowerUsageDownloader


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ extracted daily files).",
    )
    args = parser.parse_args(argv)

    downloader = KansaiPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} daily file(s) into {}", len(paths), downloader.csv_dir)


if __name__ == "__main__":
    main()
