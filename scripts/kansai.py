"""Kansai (関西電力送配電): download the two datasets and load them into pma_raw.

    just kansai download area_demand_generation|power_usage [--data-dir DIR]
    just kansai load area_demand_generation|power_usage [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark session;
the downloads run host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.tso.kansai.area_demand_generation import (
    KansaiAreaCsvLoader,
    KansaiAreaDownloader,
)
from power_market_analytics.ingestion.tso.kansai.power_usage import (
    KansaiPowerUsageCsvLoader,
    KansaiPowerUsageDownloader,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def download_area_demand_generation(args: argparse.Namespace) -> None:
    """Download the 関西電力送配電 エリア需給・発電（実績） monthly archives and extract the daily CSVs.

    Always re-downloads every month from 2022-04 to the current month (~53 zips,
    ~2 MB in total): Kansai revises past days occasionally and refreshes the
    current month's archive daily, and re-fetching everything is the simplest way
    to stay consistent with the published history.
    """
    downloader = KansaiAreaDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} actuals file(s) into {}", len(paths), downloader.csv_dir)


def download_power_usage(args: argparse.Namespace) -> None:
    """Download the 関西電力送配電 でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

    Always re-downloads every monthly ``YYYYMM_jisseki.zip`` from 2016-04 to the
    current month (~126 zips, ~8.5 MB) — Kansai revises past days without notice
    — and extracts the daily members (``YYYYMMDD_juyo1_kansai.csv`` through
    2025-11, ``juyo_06_YYYYMMDD.csv`` from 2025-12) under ``csv/``. A settled
    month must hold every day except 2024-03-31, which Kansai never published.
    """
    downloader = KansaiPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} daily file(s) into {}", len(paths), downloader.csv_dir)


def load_area_demand_generation(args: argparse.Namespace) -> None:
    """Load the extracted Kansai area actuals CSVs into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just kansai load area_demand_generation``.
    """
    load_from_args(args, KansaiAreaCsvLoader)


def load_power_usage(args: argparse.Namespace) -> None:
    """Load the Kansai でんき予報 hourly 電力使用実績 files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just kansai load power_usage``.
    """
    load_from_args(args, KansaiPowerUsageCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``kansai`` parser: ``download`` and ``load``, the two datasets under each."""
    parser = argparse.ArgumentParser(
        prog="kansai", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's archives from 関西電力送配電 and extract them"
    ).add_subparsers(dest="dataset", required=True)
    area = add_subcommand(
        downloads,
        "area_demand_generation",
        download_area_demand_generation,
        help="The エリア需給・発電（実績） 30-minute actuals, every monthly zip since 2022-04",
    )
    area.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/area_demand_generation"),
        help="Root directory for Kansai area files (zip/ archives, csv/ extracted actuals).",
    )
    usage = add_subcommand(
        downloads,
        "power_usage",
        download_power_usage,
        help="The でんき予報 hourly 電力使用実績, every monthly zip since 2016-04",
    )
    usage.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ extracted daily files).",
    )

    loads = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    load_area = add_subcommand(
        loads,
        "area_demand_generation",
        load_area_demand_generation,
        help="The extracted actuals → pma_raw.kansai_area_demand_generation_actual",
    )
    add_load_arguments(
        load_area,
        schema=REPO_ROOT / "conf/schemas/kansai_area_demand_generation_actual.yaml",
        data=REPO_ROOT / "data/kansai/area_demand_generation/csv",
        table="pma_raw.kansai_area_demand_generation_actual",
    )
    load_usage = add_subcommand(
        loads,
        "power_usage",
        load_power_usage,
        help="The daily files → pma_raw.kansai_power_usage_hourly",
    )
    add_load_arguments(
        load_usage,
        schema=REPO_ROOT / "conf/schemas/kansai_power_usage_hourly.yaml",
        data=REPO_ROOT / "data/kansai/power_usage/csv",
        table="pma_raw.kansai_power_usage_hourly",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
