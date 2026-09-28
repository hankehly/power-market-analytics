"""TEPCO: download the two Tokyo-area datasets and load them into pma_raw.

    just tepco download area_demand_generation [--data-dir DIR]
    just tepco download power_usage [--data-dir DIR] [--force-yearly]
    just tepco load area_demand_generation|power_usage [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark session;
the downloads run host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.tso.tepco.area_demand_generation import (
    TepcoAreaCsvLoader,
    TepcoAreaDownloader,
)
from power_market_analytics.ingestion.tso.tepco.power_usage import (
    TepcoPowerUsageCsvLoader,
    TepcoPowerUsageDownloader,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def download_area_demand_generation(args: argparse.Namespace) -> None:
    """Download the TEPCO エリア需要・発電情報 monthly archives and extract the actuals CSVs.

    Always re-downloads every month from 2022-04 to the current month (~53 zips,
    ~5 MB in total): TEPCO revises past days occasionally and refreshes the current
    month's archive daily, and re-fetching everything is the simplest way to stay
    consistent with the published history.
    """
    downloader = TepcoAreaDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} actuals file(s) into {}", len(paths), downloader.csv_dir)


def download_power_usage(args: argparse.Namespace) -> None:
    """Download the TEPCO でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

    Fetches the yearly ``juyo-YYYY.csv`` files (2016 … 2022, immutable — cached
    after the first run unless ``--force-yearly``) and always re-downloads every
    monthly ``YYYYMM_power_usage.zip`` from 2022-04 to the current month (~53
    zips, ~4 MB), extracting the daily ``YYYYMMDD_power_usage.csv`` members next
    to the yearly files under ``csv/``.
    """
    downloader = TepcoPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all(force_yearly=args.force_yearly)
    logger.info("Downloaded {} source file(s) into {}", len(paths), downloader.csv_dir)


def load_area_demand_generation(args: argparse.Namespace) -> None:
    """Load the extracted TEPCO area actuals CSVs into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just tepco load area_demand_generation``.
    """
    load_from_args(args, TepcoAreaCsvLoader)


def load_power_usage(args: argparse.Namespace) -> None:
    """Load the TEPCO でんき予報 hourly 電力使用実績 files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just tepco load power_usage``.
    """
    load_from_args(args, TepcoPowerUsageCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``tepco`` parser: ``download`` and ``load``, the two datasets under each."""
    parser = argparse.ArgumentParser(
        prog="tepco", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's archives from TEPCO and extract them"
    ).add_subparsers(dest="dataset", required=True)
    area = add_subcommand(
        downloads,
        "area_demand_generation",
        download_area_demand_generation,
        help="The エリア需要・発電情報 30-minute actuals, every monthly zip since 2022-04",
    )
    area.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/tepco/area_demand_generation"),
        help="Root directory for TEPCO area files (zip/ archives, csv/ extracted actuals).",
    )
    usage = add_subcommand(
        downloads,
        "power_usage",
        download_power_usage,
        help="The でんき予報 hourly 電力使用実績: yearly files 2016 … 2022, monthly zips since 2022-04",
    )
    usage.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/tepco/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ yearly + extracted daily files).",
    )
    usage.add_argument(
        "--force-yearly",
        action="store_true",
        help="Re-download the yearly juyo-YYYY.csv files even when cached.",
    )

    loads = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    load_area = add_subcommand(
        loads,
        "area_demand_generation",
        load_area_demand_generation,
        help="The extracted actuals → pma_raw.tepco_area_demand_generation_actual",
    )
    add_load_arguments(
        load_area,
        schema=REPO_ROOT / "conf/schemas/tepco_area_demand_generation_actual.yaml",
        data=REPO_ROOT / "data/tepco/area_demand_generation/csv",
        table="pma_raw.tepco_area_demand_generation_actual",
    )
    load_usage = add_subcommand(
        loads,
        "power_usage",
        load_power_usage,
        help="The yearly and daily files → pma_raw.tepco_power_usage_hourly",
    )
    add_load_arguments(
        load_usage,
        schema=REPO_ROOT / "conf/schemas/tepco_power_usage_hourly.yaml",
        data=REPO_ROOT / "data/tepco/power_usage/csv",
        table="pma_raw.tepco_power_usage_hourly",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
