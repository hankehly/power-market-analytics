"""Regenerate the Japanese national holidays dbt seed.

Downloads the official Cabinet Office holiday CSV (Shift_JIS, published
annually with coverage through the end of the next calendar year) and
rewrites dbt/seeds/jpn_national_holidays.csv as UTF-8 with ISO dates.

After refreshing, extend the dim_date spine end date to match the new
coverage and run: just dbt seed && just dbt build --select dim_date
"""

import csv
import datetime
import io
from pathlib import Path

import requests

SOURCE_URL = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"
SEED_PATH = Path(__file__).resolve().parents[1] / "dbt/seeds/jpn_national_holidays.csv"


def main() -> None:
    response = requests.get(SOURCE_URL, timeout=60)
    response.raise_for_status()

    reader = csv.reader(io.StringIO(response.content.decode("shift_jis")))
    header = next(reader)
    if header != ["国民の祝日・休日月日", "国民の祝日・休日名称"]:
        raise ValueError(f"Unexpected source header: {header}")

    rows = []
    for date_str, name in reader:
        date = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        rows.append((date.isoformat(), name))
    if not rows:
        raise ValueError("Source CSV contained no holiday rows")

    with open(SEED_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["holiday_date", "holiday_name_ja"])
        writer.writerows(sorted(rows))

    print(f"Wrote {len(rows)} holidays ({rows[0][0]}..{rows[-1][0]}) to {SEED_PATH}")


if __name__ == "__main__":
    main()
