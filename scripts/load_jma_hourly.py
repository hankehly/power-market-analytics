"""Load downloaded JMA hourly CSVs into the warehouse (full reload).

The core element set (温度/降水/風/日照, codes 101-201-301-401) comes in two
fixed layouts — 15 columns at AMeDAS stations, 17 at staffed stations (extra
現象なし情報 columns) — so each loads through its own contract into its own
raw table. Files are matched by name: ``{station}_{codes}_{year}.csv`` with
an ``a``/``s`` station prefix.

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_jma_hourly.py
"""

import argparse
import logging
from pathlib import Path

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.jma_loader import JmaHourlyCsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]

#: (schema file stem, file glob, destination table) per format.
FORMATS = [
    ("jma_hourly_amedas", "a*_101-201-301-401_*.csv", "raw.jma_hourly_amedas"),
    ("jma_hourly_staffed", "s*_101-201-301-401_*.csv", "raw.jma_hourly_staffed"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data/jma/hourly",
        help="Directory containing the downloaded JMA hourly CSV files.",
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=REPO_ROOT / "conf/schemas",
        help="Directory containing the YAML schema definitions.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    for schema_stem, pattern, table in FORMATS:
        schema = CsvTableSchema.from_yaml(args.schema_dir / f"{schema_stem}.yaml")
        loader = JmaHourlyCsvLoader(
            schema=schema, filepath=args.data_dir / pattern, table=table
        )
        n_rows = loader.load()
        print(f"Loaded {n_rows} rows into {table}")


if __name__ == "__main__":
    main()
