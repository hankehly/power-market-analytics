"""Load the downloaded OCCTO 翌々日 demand forecast CSV into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_occto_demand_forecast.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvLoader, CsvTableSchema

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/occto_demand_forecast_dad.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/occto/demand_forecast_dad",
        help="CSV file, directory of CSV files, or glob pattern to load.",
    )
    parser.add_argument(
        "--table",
        default="pma_raw.occto_demand_forecast_dad",
        help="Destination table (database.table).",
    )
    args = parser.parse_args()

    schema = CsvTableSchema.from_yaml(args.schema)
    loader = CsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)


if __name__ == "__main__":
    main()
