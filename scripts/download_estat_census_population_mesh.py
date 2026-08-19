"""Download the e-Stat census 500 m population-mesh archives and extract the text files.

For every configured census vintage (power_market_analytics.estat.VINTAGES:
2015 = T000847, 2020 = T001101 JGD2000) the 第１次地域区画 listing is read
(151 primary-mesh archives each, ~1 MB apiece), every archive is downloaded
into ``{data-dir}/{year}/zip/`` unless it is already cached, and its single
text member is extracted unmodified into ``{data-dir}/{year}/txt/``. Census
tables never change once published, so a plain rerun only re-reads the listing
pages; pass ``--force`` to re-download the archives.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.estat import VINTAGES, EstatCensusMeshDownloader

CONFIGURED_YEARS = [vintage.census_year for vintage in VINTAGES]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_YEARS,
        default=CONFIGURED_YEARS,
        metavar="YEAR",
        help=f"Census years to fetch (configured: {CONFIGURED_YEARS}); defaults to all.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/estat/census_population_mesh"),
        help="Root directory for the per-year zip/ archives and txt/ extracts.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download archives that are already cached.",
    )
    args = parser.parse_args(argv)

    downloader = EstatCensusMeshDownloader(data_dir=args.data_dir)
    paths = downloader.download_all(years=args.years, force=args.force)
    logger.info("Extracted {} text file(s) under {}", len(paths), args.data_dir)


if __name__ == "__main__":
    main()
