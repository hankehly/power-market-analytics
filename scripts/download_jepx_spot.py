"""Download JEPX spot price CSVs for every available fiscal year.

Past fiscal years are immutable and served from the local cache; the two
most recent fiscal years are always re-downloaded because JEPX appends rows
to the current file daily (and a file cached mid-year would otherwise stay
partial after the fiscal year rolls over).
"""

import argparse
import logging
from pathlib import Path

from power_market_analytics.jepx import JepxSpotDownloader, current_fiscal_year


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jepx/spot"),
        help="Directory where CSV files are stored.",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Re-download every fiscal year, ignoring the cache.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    downloader = JepxSpotDownloader(data_dir=args.data_dir)
    latest = current_fiscal_year()
    for fiscal_year in range(downloader.EARLIEST_FISCAL_YEAR, latest + 1):
        force = args.force_all or fiscal_year >= latest - 1
        downloader.download(fiscal_year, force=force)
    print(f"Downloaded fiscal years {downloader.EARLIEST_FISCAL_YEAR}..{latest}")


if __name__ == "__main__":
    main()
