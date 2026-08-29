"""Download JMA MSM GPV surface forecasts from the Kyoto University RISH GRIB2 mirror.

Each delivery day costs one archive of three GRIB2 files, roughly 157 MB
total (~54 GiB for a full year); downloads are sequential and throttled
(``power_market_analytics.msm.MsmDownloader``) out of politeness toward
RISH, an academic mirror with no published rate limit of its own — a full
historical backfill is correspondingly slow and should be run detached.

For every delivery day D in ``[--start-date, --end-date]`` (default:
``DEFAULT_BACKFILL_START`` through ``default_end_date()``, JST "today" + 1
day), downloads and decodes the three GRIB2 files covering D
(``power_market_analytics.msm.source_files_for``) into one gzip CSV extract
under ``--data-dir/csv/``, reusing an already-cached extract unless
``--force``. The three GRIB2 files are deleted after a successful extract
unless ``--keep-grib``.
"""

import argparse
import datetime
from pathlib import Path

from loguru import logger

from power_market_analytics.msm import (
    DEFAULT_BACKFILL_START,
    MsmDownloader,
    default_end_date,
    load_stations,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        type=datetime.date.fromisoformat,
        default=DEFAULT_BACKFILL_START,
        help="First delivery day to extract, inclusive (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=datetime.date.fromisoformat,
        default=default_end_date(),
        help="Last delivery day to extract, inclusive (YYYY-MM-DD); defaults to JST today + 1 day.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/msm_surface_forecast"),
        help="Root directory for GRIB2 downloads (grib/) and csv.gz extracts (csv/).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download every GRIB2 file and rebuild the extract, even for a cached day.",
    )
    parser.add_argument(
        "--keep-grib",
        action="store_true",
        help="Keep the downloaded GRIB2 files after extraction (deleted by default).",
    )
    args = parser.parse_args(argv)

    stations = load_stations(
        REPO_ROOT / "dbt/seeds/jma_stations.csv",
        REPO_ROOT / "dbt/seeds/jma_station_areas.csv",
    )
    downloader = MsmDownloader(data_dir=args.data_dir)
    paths = downloader.download_range(
        args.start_date,
        args.end_date,
        stations,
        force=args.force,
        keep_grib=args.keep_grib,
    )
    logger.info("Extracted {} delivery day(s) under {}", len(paths), args.data_dir)


if __name__ == "__main__":
    main()
