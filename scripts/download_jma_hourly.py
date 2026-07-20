"""Download JMA hourly observation CSVs for a station, one file per year.

All requested elements are fetched in a single request per year (bounded by
JMA's per-request data-volume cap; wind counts as two value columns). Past
years are immutable and served from the local cache; the current year is
always re-downloaded because JMA appends new observations daily.
"""

import argparse
import datetime
import logging
from pathlib import Path

from power_market_analytics.jma import HOURLY_ELEMENTS, JmaHourlyDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--station",
        default="s47662",
        help="JMA station id (default: s47662, 東京).",
    )
    parser.add_argument(
        "--elements",
        nargs="+",
        default=["temperature", "precipitation", "sunshine", "wind"],
        choices=sorted(HOURLY_ELEMENTS),
        metavar="ELEMENT",
        help=(
            "Observation elements to download in one request per year "
            f"(choices: {', '.join(sorted(HOURLY_ELEMENTS))})."
        ),
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=JmaHourlyDownloader.EARLIEST_YEAR,
        help="First calendar year to download.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=datetime.date.today().year,
        help="Last calendar year to download.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/hourly"),
        help="Directory where CSV files are stored.",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Re-download every year, ignoring the cache.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    downloader = JmaHourlyDownloader(data_dir=args.data_dir)
    current_year = datetime.date.today().year
    for year in range(args.start_year, args.end_year + 1):
        force = args.force_all or year == current_year
        downloader.download(args.station, args.elements, year, force=force)
    print(
        f"Downloaded {args.station} {sorted(args.elements)} "
        f"{args.start_year}..{args.end_year}"
    )


if __name__ == "__main__":
    main()
