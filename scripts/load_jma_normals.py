"""Load the extracted JMA daily normals files into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_jma_normals.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.jma.normals import JmaNormalsCsvLoader
from power_market_analytics.ingestion.loader import CsvTableSchema

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/jma/normals",
        help=(
            "Downloader root ({period end year}/csv/daily/*.csv underneath), a single daily "
            "file, or a glob pattern to load; the manifest is read two levels up."
        ),
    )
    parser.add_argument(
        "--table",
        default="pma_raw.jma_normal_surface_daily",
        help="Destination table (database.table).",
    )
    args = parser.parse_args(argv)

    schema = CsvTableSchema.from_yaml(args.schema)
    loader = JmaNormalsCsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)


if __name__ == "__main__":
    main()
