"""Download the OCCTO day-after-next (翌々日) エリア・広域ブロック情報 (reserve-rate) CSV.

Always re-downloads the full history (2025-04-01 onward). The bulk-download
screen caps one download at 150,000 rows and this dataset has 480 rows per day
(48 half-hours × 10 areas), so the downloader fetches it in 300-day windows and
concatenates them into one file (~20 MB per year of history).
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.occto import OcctoBulkDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/occto"),
        help="Root directory where OCCTO CSV files are stored (one subdirectory per dataset).",
    )
    args = parser.parse_args()

    downloader = OcctoBulkDownloader(data_dir=args.data_dir)
    path = downloader.download("area_reserve_rate_dad")
    logger.info("Downloaded OCCTO area_reserve_rate_dad to {}", path)


if __name__ == "__main__":
    main()
