"""e-Stat: download the census 500 m population-mesh archives and load them into pma_raw.

    just estat download census_population_mesh [--years YEAR …] [--data-dir DIR] [--force]
    just estat load census_population_mesh [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The load needs the devcontainer's Spark session;
the download runs host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.estat.download import EstatCensusMeshDownloader
from power_market_analytics.ingestion.estat.load import EstatCensusMeshCsvLoader
from power_market_analytics.ingestion.estat.vintages import VINTAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGURED_YEARS = [vintage.census_year for vintage in VINTAGES]


def download_census_population_mesh(args: argparse.Namespace) -> None:
    """Download the e-Stat census 500 m population-mesh archives and extract the text files.

    For every configured census vintage (power_market_analytics.ingestion.estat.vintages.VINTAGES:
    2015 = T000847, 2020 = T001101 JGD2000) the 第１次地域区画 listing is read
    (151 primary-mesh archives each, ~1 MB apiece), every archive is downloaded
    into ``{data-dir}/{year}/zip/`` unless it is already cached, and its single
    text member is extracted unmodified into ``{data-dir}/{year}/txt/``. Census
    tables never change once published, so a plain rerun only re-reads the listing
    pages; pass ``--force`` to re-download the archives.
    """
    downloader = EstatCensusMeshDownloader(data_dir=args.data_dir)
    paths = downloader.download_all(years=args.years, force=args.force)
    logger.info("Extracted {} text file(s) under {}", len(paths), args.data_dir)


def load_census_population_mesh(args: argparse.Namespace) -> None:
    """Load the extracted e-Stat census population-mesh text files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just estat load census_population_mesh``.
    """
    load_from_args(args, EstatCensusMeshCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``estat`` parser: ``download`` and ``load``, the one dataset under each."""
    parser = argparse.ArgumentParser(
        prog="estat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's archives from e-Stat and extract them"
    ).add_subparsers(dest="dataset", required=True)
    mesh = add_subcommand(
        downloads,
        "census_population_mesh",
        download_census_population_mesh,
        help="The census 4次メッシュ population tables, 151 primary-mesh archives per vintage",
    )
    mesh.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_YEARS,
        default=CONFIGURED_YEARS,
        metavar="YEAR",
        help=f"Census years to fetch (configured: {CONFIGURED_YEARS}); defaults to all.",
    )
    mesh.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/estat/census_population_mesh"),
        help="Root directory for the per-year zip/ archives and txt/ extracts.",
    )
    mesh.add_argument(
        "--force",
        action="store_true",
        help="Re-download archives that are already cached.",
    )

    loads = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    load_mesh = add_subcommand(
        loads,
        "census_population_mesh",
        load_census_population_mesh,
        help="The extracted text files → pma_raw.estat_census_population_mesh",
    )
    add_load_arguments(
        load_mesh,
        schema=REPO_ROOT / "conf/schemas/estat_census_population_mesh.yaml",
        data=REPO_ROOT / "data/estat/census_population_mesh",
        table="pma_raw.estat_census_population_mesh",
        data_help=(
            "Downloader root ({year}/txt/*.txt underneath), a single text file, "
            "or a glob pattern to load."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
