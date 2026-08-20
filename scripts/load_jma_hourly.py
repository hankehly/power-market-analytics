"""Load downloaded JMA hourly CSVs into the warehouse (full reload).

The 7-element staffed-station scrape set (降水量+気温+風向・風速+日照時間+
積雪の深さ+相対湿度+全天日射量, codes 101-201-301-401-501-605-610) is a
single fixed 27-column layout, loaded through one contract into one raw
table. Files are matched by name:
``s{station}_101-201-301-401-501-605-610_{year}.csv``. The loader's
column-count check (contract vs. first data row) now guards against JMA
layout drift rather than station-class mixups.

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_jma_hourly.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.jma_loader import JmaHourlyCsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]

#: (schema file stem, file glob, destination table).
FORMATS = [
    (
        "jma_hourly_staffed",
        "s*_101-201-301-401-501-605-610_*.csv",
        "pma_raw.jma_hourly_staffed",
    ),
]


def main(argv: list[str] | None = None) -> None:
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
    args = parser.parse_args(argv)

    for schema_stem, pattern, table in FORMATS:
        schema = CsvTableSchema.from_yaml(args.schema_dir / f"{schema_stem}.yaml")
        loader = JmaHourlyCsvLoader(schema=schema, filepath=args.data_dir / pattern, table=table)
        n_rows = loader.load()
        logger.info("Loaded {} rows into {}", n_rows, table)


if __name__ == "__main__":
    main()
