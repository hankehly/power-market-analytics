"""Download JMA's climatological normals (平年値): the daily file of every staffed station.

For each configured normals period (``power_market_analytics.ingestion.jma.normals.VINTAGES``:
1991–2020, in use since 2021-05-19) the version is read off the 平年値ダウンロード
page, ``normal_surface.zip`` (20 MB, one request) is downloaded and validated —
the station index plus exactly 157 daily files — and the daily files, the index
and a manifest are written under ``{data-dir}/{period end year}/``. The zip is
always re-downloaded: JMA replaces it under the same URL when a new version
comes out, and nothing inside names the version.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.jma.normals import VINTAGES, JmaNormalsDownloader

CONFIGURED_YEARS = [vintage.period_end_year for vintage in VINTAGES]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_YEARS,
        default=CONFIGURED_YEARS,
        metavar="YEAR",
        help=f"Period end years to fetch (configured: {CONFIGURED_YEARS}); defaults to all.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/normals"),
        help="Root directory; each period gets {year}/zip, {year}/csv and a manifest.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP request timeout in seconds.",
    )
    args = parser.parse_args(argv)

    downloader = JmaNormalsDownloader(data_dir=args.data_dir, timeout=args.timeout)
    paths = downloader.download_all(years=args.years)
    logger.info("Extracted {} daily file(s) under {}", len(paths), args.data_dir)


if __name__ == "__main__":
    main()
