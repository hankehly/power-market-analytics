"""Download JMA hourly core-element CSVs for every station in the master.

Walks the station master (downloading it first if absent), plans one
request per station and calendar year — the core element set 気温+降水量+
風向・風速+日照時間 fits a single request (docs/JMA-Weather-Data-Retrieval.md
§6.3) — and downloads each missing file. Stations whose observations ended
before the window are skipped, and discontinued stations only get years up
to their end date.

The scrape is resumable: existing year files are served from the cache, so
re-running after an interruption continues where it left off. A current-year
file is re-downloaded only when it was written before today. Failures are
logged and skipped (the next run retries them, since no file is written),
but ten consecutive failures abort the run — that pattern means JMA is
refusing us, and hammering on regardless would be impolite.

The full network (~1,300 active stations x 11 years at 5-second spacing)
takes roughly a day; run it detached, e.g.:

    nohup python scripts/download_jma_hourly_all.py > jma_scrape.log 2>&1 &
"""

import argparse
import csv
import datetime
import logging
from pathlib import Path

from power_market_analytics.jma import (
    JmaHourlyDownloader,
    JmaStationMasterDownloader,
)

logger = logging.getLogger("download_jma_hourly_all")

CORE_ELEMENTS = ["temperature", "precipitation", "sunshine", "wind"]

#: Consecutive failures after which the run aborts (server refusing us).
MAX_CONSECUTIVE_FAILURES = 10


def build_plan(
    stations_csv: Path,
    start_year: int,
    end_year: int,
    limit: int | None,
    prefectures: list[int] | None = None,
) -> list[tuple[str, int]]:
    """Plan the (station_id, year) downloads from the station master.

    Parameters
    ----------
    stations_csv : pathlib.Path
        Station master CSV written by ``JmaStationMasterDownloader``.
    start_year : int
        First calendar year to download.
    end_year : int
        Last calendar year to download.
    limit : int, optional
        Keep only the first ``limit`` stations (in file order, after the
        prefecture filter) — for test runs.
    prefectures : list of int, optional
        Keep only stations in these prefecture (``pd``) codes, e.g. ``[44]``
        for 東京 (docs/JMA-Weather-Data-Retrieval.md Appendix A). ``None``
        keeps every station.

    Returns
    -------
    list of (str, int)
        One entry per station and year, station-major. Stations whose
        observations ended before ``start_year`` are excluded; discontinued
        stations only contribute years up to their end date.

    Raises
    ------
    ValueError
        If ``prefectures`` matches no stations (e.g. a typo'd code).
    """
    with open(stations_csv, encoding="utf-8") as f:
        stations = list(csv.DictReader(f))
    if prefectures is not None:
        stations = [s for s in stations if int(s["prefecture_code"]) in prefectures]
        if not stations:
            raise ValueError(
                f"No stations in prefecture codes {prefectures}; see "
                "docs/JMA-Weather-Data-Retrieval.md Appendix A for valid codes"
            )
    if limit is not None:
        stations = stations[:limit]

    plan: list[tuple[str, int]] = []
    skipped = 0
    for station in stations:
        last_year = end_year
        if station["observation_ended_on"]:
            ended = datetime.date.fromisoformat(station["observation_ended_on"])
            if ended.year < start_year:
                skipped += 1
                continue
            last_year = min(last_year, ended.year)
        plan.extend(
            (station["station_id"], year)
            for year in range(start_year, last_year + 1)
        )
    logger.info(
        "Planned %d station-years across %d stations (%d stations ended "
        "before %d and were skipped)",
        len(plan),
        len(stations) - skipped,
        skipped,
        start_year,
    )
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stations-csv",
        type=Path,
        default=Path("dbt/seeds/jma_stations.csv"),
        help=(
            "Station master CSV (the dbt seed; downloaded automatically if "
            "absent, refresh with scripts/update_jma_stations_seed.py)."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/hourly"),
        help="Directory where hourly CSV files are stored.",
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
        "--prefecture",
        type=int,
        nargs="+",
        default=None,
        metavar="PD",
        help=(
            "Only stations in these prefecture codes, e.g. --prefecture 44 "
            "for 東京 (codes: docs/JMA-Weather-Data-Retrieval.md Appendix A)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N stations of the master (for testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and report what would be downloaded, without downloading.",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=5.0,
        help="Minimum seconds between consecutive HTTP requests.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    JmaStationMasterDownloader(dest=args.stations_csv).download()
    plan = build_plan(
        args.stations_csv,
        args.start_year,
        args.end_year,
        args.limit,
        prefectures=args.prefecture,
    )

    downloader = JmaHourlyDownloader(
        data_dir=args.data_dir, request_interval=args.request_interval
    )
    today = datetime.date.today()
    to_fetch = sum(
        1
        for station_id, year in plan
        if not downloader.path_for(station_id, CORE_ELEMENTS, year).exists()
    )
    # Server response time (~10 s per file) usually dominates the request
    # interval, so the spacing-based figure is a lower bound.
    logger.info(
        "%d of %d station-years not yet downloaded; at least %.1f h at "
        "%.0f s spacing (~%.0f h at the observed ~15 s/request)",
        to_fetch,
        len(plan),
        to_fetch * args.request_interval / 3600,
        args.request_interval,
        to_fetch * 15 / 3600,
    )
    if args.dry_run:
        print(f"Dry run: would download {to_fetch} of {len(plan)} station-years")
        return

    failures: list[tuple[str, int, str]] = []
    consecutive_failures = 0
    for i, (station_id, year) in enumerate(plan, start=1):
        dest = downloader.path_for(station_id, CORE_ELEMENTS, year)
        # Refresh a current-year file only if it predates today; past years
        # are immutable and always served from the cache.
        force = (
            year == today.year
            and dest.exists()
            and datetime.date.fromtimestamp(dest.stat().st_mtime) < today
        )
        try:
            downloader.download(station_id, CORE_ELEMENTS, year, force=force)
            consecutive_failures = 0
        except Exception as exc:
            failures.append((station_id, year, str(exc)))
            consecutive_failures += 1
            logger.error("FAILED %s %d: %s", station_id, year, exc)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "%d consecutive failures — aborting; JMA appears to be "
                    "refusing requests. Re-run later to resume.",
                    consecutive_failures,
                )
                break
        if i % 100 == 0:
            logger.info("Progress: %d/%d station-years", i, len(plan))

    print(f"Done: {len(plan) - len(failures)}/{len(plan)} station-years ok")
    if failures:
        print(f"{len(failures)} failures (re-run to retry):")
        for station_id, year, message in failures[:20]:
            print(f"  {station_id} {year}: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
