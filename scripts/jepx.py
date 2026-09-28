"""JEPX: download the spot market CSVs and load them into pma_raw.

    just jepx download spot [--data-dir DIR] [--force-all]
    just jepx load spot [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The load needs the devcontainer's Spark
session; the download runs host-side too (``uv run python scripts/jepx.py download spot``).
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.jepx import JepxSpotDownloader, current_fiscal_year
from power_market_analytics.ingestion.loader import CsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]


def download_spot(args: argparse.Namespace) -> None:
    """Download JEPX spot price CSVs for every available fiscal year.

    Past fiscal years are immutable and served from the local cache; the two
    most recent fiscal years are always re-downloaded because JEPX appends rows
    to the current file daily (and a file cached mid-year would otherwise stay
    partial after the fiscal year rolls over).
    """
    downloader = JepxSpotDownloader(data_dir=args.data_dir)
    latest = current_fiscal_year()
    for fiscal_year in range(downloader.EARLIEST_FISCAL_YEAR, latest + 1):
        force = args.force_all or fiscal_year >= latest - 1
        downloader.download(fiscal_year, force=force)
    logger.info("Downloaded fiscal years {}..{}", downloader.EARLIEST_FISCAL_YEAR, latest)


def load_spot(args: argparse.Namespace) -> None:
    """Load the downloaded JEPX spot price CSVs into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jepx load spot``.
    """
    load_from_args(args, CsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``jepx`` parser: ``download`` and ``load``, the ``spot`` dataset under each."""
    parser = argparse.ArgumentParser(
        prog="jepx", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    download = verbs.add_parser("download", help="Fetch a dataset's files from JEPX")
    downloads = download.add_subparsers(dest="dataset", required=True)
    spot = add_subcommand(
        downloads, "spot", download_spot, help="The spot market CSVs, one per fiscal year"
    )
    spot.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jepx/spot"),
        help="Directory where CSV files are stored.",
    )
    spot.add_argument(
        "--force-all",
        action="store_true",
        help="Re-download every fiscal year, ignoring the cache.",
    )

    load = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    )
    loads = load.add_subparsers(dest="dataset", required=True)
    load_spot_parser = add_subcommand(
        loads, "spot", load_spot, help="The spot CSVs → pma_raw.jepx_spot"
    )
    add_load_arguments(
        load_spot_parser,
        schema=REPO_ROOT / "conf/schemas/jepx_spot.yaml",
        data=REPO_ROOT / "data/jepx/spot",
        table="pma_raw.jepx_spot",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
