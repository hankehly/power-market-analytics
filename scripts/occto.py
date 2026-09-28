"""OCCTO: download the two 翌々日 datasets from the 情報ダウンロード portal and load them into
pma_raw.

    just occto download demand_forecast_dad|area_reserve_rate_dad [--data-dir DIR]
    just occto load demand_forecast_dad|area_reserve_rate_dad [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark session;
the downloads run host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.loader import CsvLoader
from power_market_analytics.ingestion.occto import OcctoBulkDownloader

REPO_ROOT = Path(__file__).resolve().parents[1]


def download(args: argparse.Namespace) -> None:
    """Download an OCCTO day-after-next (翌々日) dataset's CSV: the whole history, every time.

    ``demand_forecast_dad``, the demand forecast: OCCTO serves the whole dataset
    (2024-03-13 onward, ~700 KB) in one file, so a refresh is a single
    three-request handshake.

    ``area_reserve_rate_dad``, the エリア・広域ブロック情報 (reserve rate) from
    2025-04-01: the bulk-download screen caps one download at 150,000 rows and
    this dataset has 480 rows per day (48 half-hours × 10 areas), so the
    downloader fetches it in 300-day windows and concatenates them into one file
    (~20 MB per year of history).
    """
    downloader = OcctoBulkDownloader(data_dir=args.data_dir)
    path = downloader.download(args.dataset)
    logger.info("Downloaded OCCTO {} to {}", args.dataset, path)


def load(args: argparse.Namespace) -> None:
    """Load a downloaded OCCTO 翌々日 CSV into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just occto load demand_forecast_dad`` or
    ``just occto load area_reserve_rate_dad``.
    """
    load_from_args(args, CsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``occto`` parser: ``download`` and ``load``, the two datasets under each."""
    parser = argparse.ArgumentParser(
        prog="occto", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's CSV from the OCCTO 情報ダウンロード portal"
    ).add_subparsers(dest="dataset", required=True)
    for name, summary in (
        ("demand_forecast_dad", "The 翌々日 demand forecast, one CSV of the whole history"),
        (
            "area_reserve_rate_dad",
            "The 翌々日 エリア・広域ブロック情報 reserve rate, in 300-day windows",
        ),
    ):
        dataset = add_subcommand(downloads, name, download, help=summary)
        dataset.add_argument(
            "--data-dir",
            type=Path,
            default=Path("data/occto"),
            help="Root directory where OCCTO CSV files are stored (one subdirectory per dataset).",
        )

    loads = verbs.add_parser(
        "load", help="Load a dataset's CSV into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    for name in ("demand_forecast_dad", "area_reserve_rate_dad"):
        dataset = add_subcommand(loads, name, load, help=f"The {name} CSV → pma_raw.occto_{name}")
        add_load_arguments(
            dataset,
            schema=REPO_ROOT / f"conf/schemas/occto_{name}.yaml",
            data=REPO_ROOT / f"data/occto/{name}",
            table=f"pma_raw.occto_{name}",
        )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
