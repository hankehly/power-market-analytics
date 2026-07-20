"""Load downloaded JEPX spot price CSVs into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_jepx_spot.py
"""

import argparse
import logging
from pathlib import Path

from power_market_analytics.csv_loader import CsvLoader, CsvTableSchema

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/jepx_spot.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/jepx/spot",
        help="CSV file, directory of CSV files, or glob pattern to load.",
    )
    parser.add_argument(
        "--table",
        default="pma_raw.jepx_spot",
        help="Destination table (database.table).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    schema = CsvTableSchema.from_yaml(args.schema)
    loader = CsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    print(f"Loaded {n_rows} rows into {args.table}")


if __name__ == "__main__":
    main()
