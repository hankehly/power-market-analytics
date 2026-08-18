"""Download the OCCTO day-after-next (翌々日) demand forecast CSV.

Always re-downloads the full history: OCCTO serves the whole dataset (2024-03-13
onward, ~700 KB) in one file, so a refresh is a single three-request handshake.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.occto import OcctoBulkDownloader


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/occto"),
        help="Root directory where OCCTO CSV files are stored (one subdirectory per dataset).",
    )
    args = parser.parse_args(argv)

    downloader = OcctoBulkDownloader(data_dir=args.data_dir)
    path = downloader.download("demand_forecast_dad")
    logger.info("Downloaded OCCTO demand_forecast_dad to {}", path)


if __name__ == "__main__":
    main()
