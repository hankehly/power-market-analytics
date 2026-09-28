"""JMA: download the hourly observations, the normals, the MSM forecast and the station
master, and load the first three into pma_raw.

    just jma download hourly|normals|msm_surface_forecast|stations [flags]
    just jma load hourly|normals|msm_surface_forecast [flags]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark
session; the downloads run host-side too (``uv run python scripts/jma.py download …``).
"""

import argparse
import csv
import datetime
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.jma.hourly import SCRAPE_ELEMENTS, JmaHourlyDownloader
from power_market_analytics.ingestion.jma.load import JmaHourlyCsvLoader
from power_market_analytics.ingestion.jma.normals import (
    VINTAGES,
    JmaNormalsCsvLoader,
    JmaNormalsDownloader,
)
from power_market_analytics.ingestion.jma.stations import JmaStationMasterDownloader
from power_market_analytics.ingestion.msm.load import MsmForecastCsvLoader
from power_market_analytics.ingestion.msm.stations import load_stations
from power_market_analytics.ingestion.msm.vintage import DEFAULT_BACKFILL_START, default_end_date

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "dbt/seeds/jma_stations.csv"
CONFIGURED_NORMALS_YEARS = [vintage.period_end_year for vintage in VINTAGES]

#: Consecutive failures after which the hourly download aborts (server refusing us).
MAX_CONSECUTIVE_FAILURES = 10


# --------------------------------------------------------------------------- download hourly


def build_plan(
    stations_csv: Path,
    start_year: int,
    end_year: int,
    limit: int | None,
    prefectures: list[int] | None = None,
    stations: list[str] | None = None,
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
        prefecture and station filters) — for test runs.
    prefectures : list of int, optional
        Keep only stations in these prefecture (``pd``) codes, e.g. ``[44]``
        for 東京 (docs/JMA-Weather-Data-Retrieval.md Appendix A). ``None``
        keeps every station.
    stations : list of str, optional
        Keep only these station ids, e.g. ``["s47662"]``, applied after
        ``prefectures``. ``None`` keeps every station.

    Returns
    -------
    list of (str, int)
        One entry per station and year, station-major. Stations whose
        observations ended before ``start_year`` are excluded; discontinued
        stations only contribute years up to their end date.

    Raises
    ------
    ValueError
        If ``prefectures`` or ``stations`` matches no station (e.g. a typo'd code
        or id).
    """
    with open(stations_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if prefectures is not None:
        rows = [s for s in rows if int(s["prefecture_code"]) in prefectures]
        if not rows:
            raise ValueError(
                f"No stations in prefecture codes {prefectures}; see "
                "docs/JMA-Weather-Data-Retrieval.md Appendix A for valid codes"
            )
    if stations is not None:
        rows = [s for s in rows if s["station_id"] in stations]
        if not rows:
            raise ValueError(
                f"No stations with ids {stations}; ids are the station_id column of {stations_csv}"
            )
    if limit is not None:
        rows = rows[:limit]

    plan: list[tuple[str, int]] = []
    skipped = 0
    for station in rows:
        last_year = end_year
        if station["observation_ended_on"]:
            ended = datetime.date.fromisoformat(station["observation_ended_on"])
            if ended.year < start_year:
                skipped += 1
                continue
            last_year = min(last_year, ended.year)
        plan.extend((station["station_id"], year) for year in range(start_year, last_year + 1))
    logger.info(
        "Planned {} station-years across {} stations ({} stations ended "
        "before {} and were skipped)",
        len(plan),
        len(rows) - skipped,
        skipped,
        start_year,
    )
    return plan


def download_hourly(args: argparse.Namespace) -> None:
    """Download JMA hourly weather CSVs for every staffed station in the master.

    Walks the station master (downloading it first if absent, restricted to
    staffed stations only — 気象官署, ``s``-prefixed ids; the 2026-08 re-scope,
    docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md — inside
    a JEPX area, i.e. excluding Okinawa, Antarctica and 南鳥島), plans
    one request-set per station and calendar year for the 7-element scrape set
    (``SCRAPE_ELEMENTS``: 気温・降水量・風向風速・日照時間・積雪の深さ・相対湿
    度・全天日射量, docs/JMA-Weather-Data-Retrieval.md §6.3) — 8 value columns,
    which exceeds JMA's per-request data-volume budget, so each station-year is
    fetched as 2 request windows stitched into one file — and downloads each
    missing file. ``--prefecture`` and ``--station`` narrow the plan. Stations
    whose observations ended before the window are skipped, and discontinued
    stations only get years up to their end date.

    The scrape is resumable: existing year files are served from the cache, so
    re-running after an interruption continues where it left off. A current-year
    file is re-downloaded only when it was written before today; to refetch any
    other file, delete it. Failures are logged and skipped (the next run retries
    them, since no file is written), but ten consecutive failures abort the run —
    that pattern means JMA is refusing us, and hammering on regardless would be
    impolite.

    The full staffed network (~149 stations x 11 years x 2 windows/station-year
    ≈ 3,450 requests) takes roughly 14 hours cold at the observed ~15-second
    per-request pace (server response time, ~10 s per file, dominates the 5-second
    spacing floor), so run it detached, e.g. host-side:

        nohup uv run python scripts/jma.py download hourly > jma_scrape.log 2>&1 &
    """
    JmaStationMasterDownloader(
        dest=args.stations_csv, staffed_only=True, jepx_areas_only=True
    ).download()
    plan = build_plan(
        args.stations_csv,
        args.start_year,
        args.end_year,
        args.limit,
        prefectures=args.prefecture,
        stations=args.station,
    )

    downloader = JmaHourlyDownloader(data_dir=args.data_dir, request_interval=args.request_interval)
    today = datetime.date.today()
    to_fetch = sum(
        1
        for station_id, year in plan
        if not downloader.path_for(station_id, SCRAPE_ELEMENTS, year).exists()
    )
    # Server response time (~10 s per file) usually dominates the request
    # interval, so the spacing-based figure is a lower bound.
    requests_per_year = downloader.window_count(SCRAPE_ELEMENTS, today.year)
    logger.info(
        "{} of {} station-years not yet downloaded (~{} requests); at least "
        "{:.1f} h at {:.0f} s spacing (~{:.0f} h at the observed ~15 s/request)",
        to_fetch,
        len(plan),
        to_fetch * requests_per_year,
        to_fetch * requests_per_year * args.request_interval / 3600,
        args.request_interval,
        to_fetch * requests_per_year * 15 / 3600,
    )
    if args.dry_run:
        logger.info("Dry run: would download {} of {} station-years", to_fetch, len(plan))
        return

    failures: list[tuple[str, int, str]] = []
    consecutive_failures = 0
    for i, (station_id, year) in enumerate(plan, start=1):
        dest = downloader.path_for(station_id, SCRAPE_ELEMENTS, year)
        # Refresh a current-year file only if it predates today; past years
        # are immutable and always served from the cache.
        force = (
            year == today.year
            and dest.exists()
            and datetime.date.fromtimestamp(dest.stat().st_mtime) < today
        )
        try:
            downloader.download(station_id, SCRAPE_ELEMENTS, year, force=force)
            consecutive_failures = 0
        except Exception as exc:
            failures.append((station_id, year, str(exc)))
            consecutive_failures += 1
            logger.error("FAILED {} {}: {}", station_id, year, exc)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "{} consecutive failures — aborting; JMA appears to be "
                    "refusing requests. Re-run later to resume.",
                    consecutive_failures,
                )
                break
        if i % 100 == 0:
            logger.info("Progress: {}/{} station-years", i, len(plan))

    logger.info("Done: {}/{} station-years ok", len(plan) - len(failures), len(plan))
    if failures:
        logger.error("{} failures (re-run to retry):", len(failures))
        for station_id, year, message in failures[:20]:
            logger.error("  {} {}: {}", station_id, year, message)
        raise SystemExit(1)


# --------------------------------------------------------------------------- download normals


def download_normals(args: argparse.Namespace) -> None:
    """Download JMA's climatological normals (平年値): the daily file of every staffed station.

    For each configured normals period (``power_market_analytics.ingestion.jma.normals.VINTAGES``:
    1991–2020, in use since 2021-05-19) the version is read off the 平年値ダウンロード
    page, ``normal_surface.zip`` (20 MB, one request) is downloaded and validated —
    the station index plus exactly 157 daily files — and the daily files, the index
    and a manifest are written under ``{data-dir}/{period end year}/``. The zip is
    always re-downloaded: JMA replaces it under the same URL when a new version
    comes out, and nothing inside names the version.
    """
    downloader = JmaNormalsDownloader(data_dir=args.data_dir, timeout=args.timeout)
    paths = downloader.download_all(years=args.years)
    logger.info("Extracted {} daily file(s) under {}", len(paths), args.data_dir)


# --------------------------------------------------------------------------- download msm_surface_forecast


def download_msm_surface_forecast(args: argparse.Namespace) -> None:
    """Download JMA MSM GPV surface forecasts from the Kyoto University RISH GRIB2 mirror.

    Each delivery day costs one archive of three GRIB2 files, roughly 157 MB
    total (~54 GiB for a full year); downloads are sequential and throttled
    (``power_market_analytics.ingestion.msm.download.MsmDownloader``) out of politeness toward
    RISH, an academic mirror with no published rate limit of its own — a full
    historical backfill is correspondingly slow and should be run detached.

    For every delivery day D in ``[--start-date, --end-date]`` (default:
    ``DEFAULT_BACKFILL_START`` through ``default_end_date()``, JST "today" + 1
    day), downloads and decodes the three GRIB2 files covering D
    (``power_market_analytics.ingestion.msm.vintage.source_files_for``) into one gzip CSV extract
    under ``--data-dir/csv/``, reusing an already-cached extract unless
    ``--force``. The three GRIB2 files are deleted after a successful extract
    unless ``--keep-grib``.

    TLS needs no setup: RISH has sent an incomplete certificate chain since
    2026-05-28, and the downloader's default session trusts the missing
    intermediate CA directly (``power_market_analytics.ingestion.msm.download.default_session``).
    """
    # Imported here, not at the top: msm.download reaches eccodes through msm.grib,
    # and no other jma subcommand needs it — the loader least of all (spec decision 8;
    # tests/test_jma_scripts.py::TestMsmDownloaderImportIsLazy pins it).
    from power_market_analytics.ingestion.msm.download import MsmDownloader

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


# --------------------------------------------------------------------------- download stations


def download_stations(args: argparse.Namespace) -> None:
    """Regenerate the JMA station master dbt seed (staffed stations only).

    Scrapes the station master (id, name, kana, prefecture, coordinates,
    elevation, observed-element mask, end-of-observation date) from the JMA
    obsdl per-prefecture station pages, keeps only staffed stations (気象官署,
    s-prefixed ids — the 2026-08 re-scope; see
    docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md) inside a
    JEPX area (dropping Okinawa, Antarctica and 南鳥島, so every station maps to
    a dim_area row) and rewrites dbt/seeds/jma_stations.csv as UTF-8 with ISO
    dates. Roughly 60 requests at polite spacing, so expect ~5 minutes.
    dim_jma_station is built from this seed joined to the jma_station_areas
    seed, which must have a row for every station id written here.
    """
    downloader = JmaStationMasterDownloader(dest=args.dest, staffed_only=True, jepx_areas_only=True)
    # Always refresh: the point of this command is to pick up new stations and
    # discontinuations, so the cached copy must never be served.
    path = downloader.download(force=True)
    logger.info("Station master seed written to {}", path)


# --------------------------------------------------------------------------- load


def load_hourly(args: argparse.Namespace) -> None:
    """Load the downloaded JMA hourly CSVs into the warehouse (full reload).

    The 7-element staffed-station scrape set (降水量+気温+風向・風速+日照時間+
    積雪の深さ+相対湿度+全天日射量, codes 101-201-301-401-501-605-610) is a
    single fixed 27-column layout, loaded through one contract into one raw
    table. Files are matched by name:
    ``s{station}_101-201-301-401-501-605-610_{year}.csv``. The loader's
    column-count check (contract vs. first data row) guards against JMA
    layout drift.

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jma load hourly``.
    """
    load_from_args(args, JmaHourlyCsvLoader)


def load_normals(args: argparse.Namespace) -> None:
    """Load the extracted JMA daily normals files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jma load normals``.
    """
    load_from_args(args, JmaNormalsCsvLoader)


def load_msm_surface_forecast(args: argparse.Namespace) -> None:
    """Load the extracted MSM GPV surface-forecast csv.gz files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jma load msm_surface_forecast``. eccodes is not
    needed: the loader imports ``ingestion.loader`` alone.
    """
    load_from_args(args, MsmForecastCsvLoader)


# --------------------------------------------------------------------------- the parser


def build_parser() -> argparse.ArgumentParser:
    """Build the ``jma`` parser: ``download`` and ``load``, a subcommand per dataset under each."""
    parser = argparse.ArgumentParser(
        prog="jma", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    download = verbs.add_parser(
        "download", help="Fetch a dataset's files from JMA (RISH's mirror for the MSM forecast)"
    )
    downloads = download.add_subparsers(dest="dataset", required=True)

    hourly = add_subcommand(
        downloads,
        "hourly",
        download_hourly,
        help="Hourly observations of every staffed station, one stitched file per station-year",
    )
    hourly.add_argument(
        "--stations-csv",
        type=Path,
        default=Path("dbt/seeds/jma_stations.csv"),
        help=(
            "Station master CSV (the dbt seed; downloaded automatically if "
            "absent, refreshed by `jma download stations`)."
        ),
    )
    hourly.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/hourly"),
        help="Directory where hourly CSV files are stored.",
    )
    hourly.add_argument(
        "--start-year",
        type=int,
        default=JmaHourlyDownloader.EARLIEST_YEAR,
        help="First calendar year to download.",
    )
    hourly.add_argument(
        "--end-year",
        type=int,
        default=datetime.date.today().year,
        help="Last calendar year to download.",
    )
    hourly.add_argument(
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
    hourly.add_argument(
        "--station",
        nargs="+",
        default=None,
        metavar="ID",
        help="Only these station ids, e.g. --station s47662 for 東京; applied after --prefecture.",
    )
    hourly.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N stations of the master (for testing).",
    )
    hourly.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and report what would be downloaded, without downloading.",
    )
    hourly.add_argument(
        "--request-interval",
        type=float,
        default=5.0,
        help="Minimum seconds between consecutive HTTP requests.",
    )

    normals = add_subcommand(
        downloads,
        "normals",
        download_normals,
        help="The 1991–2020 climatological normals (平年値): the daily file of every staffed station",
    )
    normals.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_NORMALS_YEARS,
        default=CONFIGURED_NORMALS_YEARS,
        metavar="YEAR",
        help=f"Period end years to fetch (configured: {CONFIGURED_NORMALS_YEARS}); defaults to all.",
    )
    normals.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/normals"),
        help="Root directory; each period gets {year}/zip, {year}/csv and a manifest.",
    )
    normals.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP request timeout in seconds.",
    )

    msm = add_subcommand(
        downloads,
        "msm_surface_forecast",
        download_msm_surface_forecast,
        help="The MSM GPV surface forecast per delivery day, decoded from RISH's GRIB2 mirror",
    )
    msm.add_argument(
        "--start-date",
        type=datetime.date.fromisoformat,
        default=DEFAULT_BACKFILL_START,
        help="First delivery day to extract, inclusive (YYYY-MM-DD).",
    )
    msm.add_argument(
        "--end-date",
        type=datetime.date.fromisoformat,
        default=default_end_date(),
        help="Last delivery day to extract, inclusive (YYYY-MM-DD); defaults to JST today + 1 day.",
    )
    msm.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/msm_surface_forecast"),
        help="Root directory for GRIB2 downloads (grib/) and csv.gz extracts (csv/).",
    )
    msm.add_argument(
        "--force",
        action="store_true",
        help="Re-download every GRIB2 file and rebuild the extract, even for a cached day.",
    )
    msm.add_argument(
        "--keep-grib",
        action="store_true",
        help="Keep the downloaded GRIB2 files after extraction (deleted by default).",
    )

    stations = add_subcommand(
        downloads,
        "stations",
        download_stations,
        help="The station master, rewritten as the dbt seed jma_stations.csv (~60 requests, ~5 min)",
    )
    stations.add_argument(
        "--dest",
        type=Path,
        default=SEED_PATH,
        help="Where to write the station master CSV (default: the dbt seed).",
    )

    load = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    )
    loads = load.add_subparsers(dest="dataset", required=True)

    load_hourly_parser = add_subcommand(
        loads,
        "hourly",
        load_hourly,
        help="The stitched 7-element files → pma_raw.jma_hourly_staffed",
    )
    add_load_arguments(
        load_hourly_parser,
        schema=REPO_ROOT / "conf/schemas/jma_hourly_staffed.yaml",
        data=REPO_ROOT / "data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv",
        table="pma_raw.jma_hourly_staffed",
        data_help="The downloaded hourly files: a glob pattern, a single file or a directory.",
    )

    load_normals_parser = add_subcommand(
        loads,
        "normals",
        load_normals,
        help="The extracted daily normals files → pma_raw.jma_normal_surface_daily",
    )
    add_load_arguments(
        load_normals_parser,
        schema=REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml",
        data=REPO_ROOT / "data/jma/normals",
        table="pma_raw.jma_normal_surface_daily",
        data_help=(
            "Downloader root ({period end year}/csv/daily/*.csv underneath), a single daily "
            "file, or a glob pattern to load; the manifest is read two levels up."
        ),
    )

    load_msm_parser = add_subcommand(
        loads,
        "msm_surface_forecast",
        load_msm_surface_forecast,
        help="The csv.gz extracts → pma_raw.jma_msm_surface_forecast",
    )
    add_load_arguments(
        load_msm_parser,
        schema=REPO_ROOT / "conf/schemas/jma_msm_surface_forecast.yaml",
        data=REPO_ROOT / "data/jma/msm_surface_forecast/csv",
        table="pma_raw.jma_msm_surface_forecast",
        data_help="Downloader csv/ directory, a single csv.gz file, or a glob pattern to load.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
