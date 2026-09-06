"""Load the Kansai でんき予報 hourly 電力使用実績 files into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_kansai_power_usage.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.kansai.power_usage import KansaiPowerUsageCsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/kansai_power_usage_hourly.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/kansai/power_usage/csv",
        help="CSV file, directory of CSV files, or glob pattern to load.",
    )
    parser.add_argument(
        "--table",
        default="pma_raw.kansai_power_usage_hourly",
        help="Destination table (database.table).",
    )
    args = parser.parse_args(argv)

    schema = CsvTableSchema.from_yaml(args.schema)
    loader = KansaiPowerUsageCsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)


if __name__ == "__main__":
    main()
