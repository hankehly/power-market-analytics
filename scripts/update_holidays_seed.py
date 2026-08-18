"""Regenerate the Japanese national holidays dbt seed.

Downloads the official Cabinet Office holiday CSV (Shift_JIS, published
annually with coverage through the end of the next calendar year) and
rewrites dbt/seeds/jpn_national_holidays.csv as UTF-8 with ISO dates.

The dim_date spine derives its end date from this seed, so rebuilding dbt
(just dbt build) after a refresh extends the calendar automatically.
"""

import argparse
import csv
import datetime
import io
from pathlib import Path

import requests
from loguru import logger

SOURCE_URL = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"
SEED_PATH = Path(__file__).resolve().parents[1] / "dbt/seeds/jpn_national_holidays.csv"

#: Header line the Cabinet Office CSV must carry (anything else means the
#: download is not the holiday file).
SOURCE_HEADER = ["国民の祝日・休日月日", "国民の祝日・休日名称"]

#: Header line of the dbt seed.
SEED_HEADER = ["holiday_date", "holiday_name_ja"]


def parse_holidays(content: bytes) -> list[tuple[str, str]]:
    """Parse the Cabinet Office holiday CSV into ``(iso_date, name)`` rows.

    Parameters
    ----------
    content : bytes
        Raw Shift_JIS bytes of ``syukujitsu.csv``.

    Returns
    -------
    list of tuple of (str, str)
        One ``(holiday_date, holiday_name_ja)`` pair per source row, in source
        order, with the date converted from ``yyyy/mm/dd`` to ISO ``yyyy-mm-dd``.

    Raises
    ------
    ValueError
        If the header line is not the expected one or the file holds no
        holiday rows.
    """
    reader = csv.reader(io.StringIO(content.decode("shift_jis")))
    header = next(reader, None)
    if header != SOURCE_HEADER:
        raise ValueError(f"Unexpected source header: {header}")

    rows = []
    for date_str, name in reader:
        date = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        rows.append((date.isoformat(), name))
    if not rows:
        raise ValueError("Source CSV contained no holiday rows")
    return rows


def write_seed(rows: list[tuple[str, str]], dest: Path) -> None:
    """Write the holiday rows as the UTF-8 dbt seed CSV, sorted by date.

    Parameters
    ----------
    rows : list of tuple of (str, str)
        ``(holiday_date, holiday_name_ja)`` pairs as returned by
        :func:`parse_holidays`.
    dest : pathlib.Path
        Seed file to (over)write.
    """
    with open(dest, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SEED_HEADER)
        writer.writerows(sorted(rows))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=SEED_PATH,
        help="Seed CSV to write.",
    )
    parser.add_argument(
        "--source-url",
        default=SOURCE_URL,
        help="URL of the Cabinet Office holiday CSV.",
    )
    args = parser.parse_args(argv)

    response = requests.get(args.source_url, timeout=60)
    response.raise_for_status()

    rows = parse_holidays(response.content)
    write_seed(rows, args.dest)
    logger.info("Wrote {} holidays ({}..{}) to {}", len(rows), rows[0][0], rows[-1][0], args.dest)


if __name__ == "__main__":
    main()
