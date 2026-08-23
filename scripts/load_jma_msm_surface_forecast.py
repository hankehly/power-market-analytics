"""Load extracted JMA MSM GPV surface-forecast csv.gz files into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_jma_msm_surface_forecast.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.msm import MsmForecastCsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/jma_msm_surface_forecast.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/jma/msm_surface_forecast/csv",
        help="Downloader csv/ directory, a single csv.gz file, or a glob pattern to load.",
    )
    parser.add_argument(
        "--table",
        default="pma_raw.jma_msm_surface_forecast",
        help="Destination table (database.table).",
    )
    args = parser.parse_args(argv)

    schema = CsvTableSchema.from_yaml(args.schema)
    loader = MsmForecastCsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)


if __name__ == "__main__":
    main()
