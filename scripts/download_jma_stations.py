"""Download the JMA station master (all prefectures) into one CSV.

One row per station — id, name, kana, prefecture, coordinates, elevation,
observed-element mask, and end-of-observation date for discontinued
stations. Roughly 60 requests at polite spacing, so expect ~5 minutes on a
fresh download; the result is cached until --force is passed.
"""

import argparse
import logging
from pathlib import Path

from power_market_analytics.jma import JmaStationMasterDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("data/jma/stations.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the file already exists.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    downloader = JmaStationMasterDownloader(dest=args.dest)
    path = downloader.download(force=args.force)
    print(f"Station master written to {path}")


if __name__ == "__main__":
    main()
