# TEPCO Area Demand & Generation Actuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load TEPCO's Tokyo-area 30-minute demand / generation / wind+solar actuals (2022-04-01 → yesterday) into the warehouse as `fct_tepco_area_demand_generation_actual`, joinable with `fct_jepx_spot_area_price` on (date_key, time_code, area_key).

**Architecture:** A downloader fetches the monthly `AREA_YYYYMM.zip` archives and extracts only the daily `AREA_JISEKI_*.csv` actuals files; a positional `CsvLoader` subclass (same pattern as `JmaHourlyCsvLoader`) full-reloads them into `pma_raw.tepco_area_demand_generation_actual`; dbt then builds `stg` (as-is) → `std` (typed time axis, measures rounded to bigint, unpublished-sentinel rows nulled) → `fct` (Kimball star, Tokyo via `dim_area`).

**Tech Stack:** Python 3.13 (`requests`, `zipfile`, `loguru`, `pyspark` 4.1), dbt-spark 1.11 with `dbt_utils`, `just` recipes, docsify docs.

**Spec:** `docs/superpowers/specs/2026-08-16-tepco-area-demand-generation-design.md`

## Global Constraints

- No pytest suite in this repo (CLAUDE.md): verify Python with `uv run ruff check .` and by running the `scripts/` entry points; verify dbt with `just dbt build` (contracts + tests). Do NOT add a test framework.
- Anything that creates a SparkSession MUST run in the devcontainer: `just python …`; dbt runs via `just dbt …`.
- Every dbt model: `config: contract: enforced: true`, a `data_type` for every column, and a uniqueness test on its primary key (`unique` or `dbt_utils.unique_combination_of_columns`). Generic-test args go under `arguments:`.
- NumPy-style docstrings (`Parameters` / `Returns` / `Raises` with underlined headers).
- Ruff line length 100; a PostToolUse hook auto-formats every `.py` you write — re-read before a follow-up Edit.
- Object naming (fixed): `data/tepco/area_demand_generation/{zip,csv}/`, `conf/schemas/tepco_area_demand_generation_actual.yaml`, `pma_raw.tepco_area_demand_generation_actual`, `stg_tepco__area_demand_generation_actual`, `std_tepco__area_demand_generation_actual`, `fct_tepco_area_demand_generation_actual`, `just refresh-tepco`.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File map

| Path | Responsibility |
|---|---|
| `power_market_analytics/tepco.py` (create) | `TepcoAreaDownloader`, `month_range`, `TepcoDownloadError` — HTTP + zip extraction only |
| `power_market_analytics/tepco_loader.py` (create) | `TepcoAreaCsvLoader(CsvLoader)` — positional read, header check, `__file_updated_at` injection |
| `conf/schemas/tepco_area_demand_generation_actual.yaml` (create) | Load contract (columns, types, grain) |
| `scripts/download_tepco_area_demand_generation.py` (create) | CLI wrapper: `download_all()` |
| `scripts/load_tepco_area_demand_generation.py` (create) | CLI wrapper: contract + loader → raw table |
| `dbt/models/raw/tepco.yml` (create) | Source definition + column docs |
| `dbt/models/staging/stg_tepco__area_demand_generation_actual.{sql,yml}` (create) | As-is staging |
| `dbt/models/standardized/std_tepco__area_demand_generation_actual.{sql,yml}` (create) | Typed time axis, bigint measures, sentinel → null |
| `dbt/models/curated/fct_tepco_area_demand_generation_actual.{sql,yml}` (create) | Star-schema fact |
| `justfile` (modify) | `refresh-tepco` recipe |
| `.claude/settings.json` (modify) | allow `Bash(just refresh-tepco *)` |
| `CLAUDE.md` (modify) | command, architecture bullet, gotcha |
| `docs/TEPCO-Area-Demand-Generation-Retrieval.md` (create), `docs/_sidebar.md`, `docs/README.md` (modify) | Retrieval doc, nav, star-schema list + ER diagram |

---

### Task 1: Downloader (`power_market_analytics/tepco.py` + download script)

**Files:**
- Create: `power_market_analytics/tepco.py`
- Create: `scripts/download_tepco_area_demand_generation.py`

**Interfaces:**
- Consumes: nothing project-specific (`requests`, `loguru` already in `pyproject.toml`).
- Produces: `month_range(start: tuple[int,int], end: tuple[int,int]) -> list[tuple[int,int]]`; `class TepcoDownloadError(RuntimeError)`; `class TepcoAreaDownloader(data_dir=Path("data/tepco/area_demand_generation"), timeout=60.0)` with properties `zip_dir`, `csv_dir`, methods `zip_path_for(year, month) -> Path`, `download(year, month) -> list[Path]`, `download_all(today=None) -> list[Path]`. Extracted CSVs land in `data_dir/csv/AREA_JISEKI_YYYYMMDD.csv` (Task 2 reads that directory).

- [ ] **Step 1: Write `power_market_analytics/tepco.py`**

```python
"""Download TEPCO エリア需要・発電情報 (Tokyo-area demand & generation) archives.

TEPCO Power Grid publishes 30-minute Tokyo-area total demand, total
generation and wind+solar generation on
https://www.tepco.co.jp/forecast/html/area-download-j.html. The history is
served as one zip per month
(``https://www4.tepco.co.jp/forecast/html/images/AREA_YYYYMM.zip``, 2022-04
onward, ~100 KB each) holding three CP932 CSVs per day: 実績 actuals
(``AREA_JISEKI_YYYYMMDD.csv``), 予測 forecasts (``AREA_YOSOKU_``) and
BG計画総計 balancing-group plans (``AREA_BGKEI_``). Only the actuals files are
extracted. The archive layout, CSV format and data quirks are documented in
docs/TEPCO-Area-Demand-Generation-Retrieval.md.
"""

from __future__ import annotations

import datetime
import io
import re
import zipfile
from pathlib import Path

import requests
from loguru import logger

URL_TEMPLATE = "https://www4.tepco.co.jp/forecast/html/images/AREA_{year:04d}{month:02d}.zip"

#: First month published on the download page.
EARLIEST_MONTH = (2022, 4)

#: Zip members to extract: the daily actuals files. The archive also holds
#: AREA_YOSOKU_* (forecast) and AREA_BGKEI_* (BG plan) files, which are skipped.
ACTUALS_MEMBER_RE = re.compile(r"AREA_JISEKI_\d{8}\.csv$")


class TepcoDownloadError(RuntimeError):
    """Raised when TEPCO returns something other than the expected zip archive."""


def month_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Return every ``(year, month)`` from ``start`` through ``end`` inclusive.

    Parameters
    ----------
    start, end : tuple of (int, int)
        ``(year, month)`` bounds, both inclusive.

    Returns
    -------
    list of tuple of (int, int)
        Consecutive months in ascending order.

    Raises
    ------
    ValueError
        If ``start`` is after ``end``.
    """
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    months = []
    year, month = start
    while (year, month) <= end:
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


class TepcoAreaDownloader:
    """Download the monthly TEPCO area archives and extract the daily actuals CSVs.

    Every call re-downloads the requested month: TEPCO occasionally revises
    past days (e.g. 2022-12-01/02 were re-issued on 2022-12-14, 2024-03-11 on
    2024-04-19) and the current month's zip grows daily, and the whole history
    is only ~5 MB, so no caching is attempted.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/tepco/area_demand_generation"``
        Root directory. Zips are kept under ``zip/`` and the extracted
        ``AREA_JISEKI_YYYYMMDD.csv`` files under ``csv/``. Created on first
        download if it does not exist.
    timeout : float, default 60.0
        HTTP request timeout in seconds.

    Examples
    --------
    >>> downloader = TepcoAreaDownloader()
    >>> downloader.download(2025, 7)[:2]
    [PosixPath('data/tepco/area_demand_generation/csv/AREA_JISEKI_20250701.csv'),
     PosixPath('data/tepco/area_demand_generation/csv/AREA_JISEKI_20250702.csv')]
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/tepco/area_demand_generation"),
        timeout: float = 60.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout

    @property
    def zip_dir(self) -> Path:
        """Directory holding the downloaded monthly zip archives."""
        return self.data_dir / "zip"

    @property
    def csv_dir(self) -> Path:
        """Directory holding the extracted daily actuals CSV files."""
        return self.data_dir / "csv"

    def zip_path_for(self, year: int, month: int) -> Path:
        """Return the local path of a month's zip archive.

        Parameters
        ----------
        year, month : int
            Calendar year and month of the archive.

        Returns
        -------
        pathlib.Path
            Path to the (possibly not yet downloaded) zip file.
        """
        return self.zip_dir / f"AREA_{year:04d}{month:02d}.zip"

    def download(self, year: int, month: int) -> list[Path]:
        """Download one month's archive and extract its actuals files.

        Parameters
        ----------
        year, month : int
            Calendar year and month of the archive.

        Returns
        -------
        list of pathlib.Path
            Extracted ``AREA_JISEKI_YYYYMMDD.csv`` paths, sorted by name.

        Raises
        ------
        TepcoDownloadError
            If the response is not a zip archive or contains no actuals files.
        requests.HTTPError
            If TEPCO responds with an error status (e.g. 404 for a month that
            is not published).
        """
        url = URL_TEMPLATE.format(year=year, month=month)
        dest = self.zip_path_for(year, month)
        logger.info("Downloading {} -> {}", url, dest)
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        content = response.content
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise TepcoDownloadError(
                f"{url} did not return a zip archive "
                f"(Content-Type={response.headers.get('Content-Type')!r}); "
                f"body starts {content[:120]!r}"
            )

        self.zip_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename so an interrupted download never
        # leaves a truncated archive at the cached path.
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(content)
        partial.replace(dest)
        extracted = self._extract_actuals(dest)
        logger.info(
            "Saved {} ({} bytes); extracted {} actuals file(s) to {}",
            dest,
            dest.stat().st_size,
            len(extracted),
            self.csv_dir,
        )
        return extracted

    def download_all(self, today: datetime.date | None = None) -> list[Path]:
        """Download every month from ``EARLIEST_MONTH`` through the current month.

        Parameters
        ----------
        today : datetime.date, optional
            Date whose month is the last one downloaded. Defaults to the
            current local date.

        Returns
        -------
        list of pathlib.Path
            All extracted actuals CSV paths, in month then day order.
        """
        if today is None:
            today = datetime.date.today()
        extracted: list[Path] = []
        for year, month in month_range(EARLIEST_MONTH, (today.year, today.month)):
            extracted.extend(self.download(year, month))
        logger.info(
            "Downloaded {}-{:02d}..{}-{:02d}: {} actuals file(s)",
            EARLIEST_MONTH[0],
            EARLIEST_MONTH[1],
            today.year,
            today.month,
            len(extracted),
        )
        return extracted

    def _extract_actuals(self, zip_path: Path) -> list[Path]:
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        extracted = []
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not ACTUALS_MEMBER_RE.search(member.filename):
                    continue
                # Members are flat in every archive except AREA_202403.zip,
                # which nests them under AREA_202403/ — keep the base name only.
                target = self.csv_dir / Path(member.filename).name
                target.write_bytes(archive.read(member))
                extracted.append(target)
        if not extracted:
            raise TepcoDownloadError(f"{zip_path} contains no AREA_JISEKI_*.csv members")
        return sorted(extracted)
```

- [ ] **Step 2: Write `scripts/download_tepco_area_demand_generation.py`**

```python
"""Download the TEPCO エリア需要・発電情報 monthly archives and extract the actuals CSVs.

Always re-downloads every month from 2022-04 to the current month (~53 zips,
~5 MB in total): TEPCO revises past days occasionally and refreshes the current
month's archive daily, and re-fetching everything is the simplest way to stay
consistent with the published history.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.tepco import TepcoAreaDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/tepco/area_demand_generation"),
        help="Root directory for TEPCO area files (zip/ archives, csv/ extracted actuals).",
    )
    args = parser.parse_args()

    downloader = TepcoAreaDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} actuals file(s) into {}", len(paths), downloader.csv_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Lint**

Run: `uv run ruff check . && uv run ruff format --check power_market_analytics/tepco.py scripts/download_tepco_area_demand_generation.py`
Expected: `All checks passed!` (the PostToolUse hook already formatted the files).

- [ ] **Step 4: Verify `month_range` and the extraction path against a real zip (host-side, no Spark)**

Run:
```bash
uv run python -c "
import datetime, tempfile
from pathlib import Path
from power_market_analytics.tepco import TepcoAreaDownloader, month_range, EARLIEST_MONTH
assert month_range((2022, 4), (2022, 4)) == [(2022, 4)]
assert month_range((2025, 11), (2026, 2)) == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
assert len(month_range(EARLIEST_MONTH, (2026, 8))) == 53
try:
    month_range((2026, 1), (2025, 12)); raise SystemExit('expected ValueError')
except ValueError:
    pass
tmp = Path(tempfile.mkdtemp())
d = TepcoAreaDownloader(data_dir=tmp)
# 2024-03 is the archive whose members are nested in a subfolder.
paths = d.download(2024, 3)
assert len(paths) == 31, len(paths)
assert paths[0].name == 'AREA_JISEKI_20240301.csv' and paths[0].parent == tmp / 'csv', paths[0]
assert (tmp / 'zip' / 'AREA_202403.zip').exists()
assert not any(p.name.startswith(('AREA_YOSOKU', 'AREA_BGKEI')) for p in (tmp / 'csv').iterdir())
print('OK', tmp)
"
```
Expected: prints `OK /var/folders/…` (31 flat actuals files, no YOSOKU/BGKEI files, zip kept).

- [ ] **Step 5: Run the real download**

Run: `just python scripts/download_tepco_area_demand_generation.py`
Expected: 53 `Downloading …` log lines, ends with `Extracted N actuals file(s) into data/tepco/area_demand_generation/csv` where N = number of days from 2022-04-01 through yesterday (1,598 on 2026-08-16). Then:

Run: `ls data/tepco/area_demand_generation/zip | wc -l; ls data/tepco/area_demand_generation/csv | wc -l; ls data/tepco/area_demand_generation/csv | head -2; ls data/tepco/area_demand_generation/csv | tail -1`
Expected: `53`, `1598` (or more on a later date), `AREA_JISEKI_20220401.csv`, `AREA_JISEKI_20220402.csv`, and yesterday's file last. `data/` is gitignored, so nothing to commit there.

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/tepco.py scripts/download_tepco_area_demand_generation.py
git commit -m "Add TEPCO area demand/generation archive downloader

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Loader, load contract, and load script

**Files:**
- Create: `power_market_analytics/tepco_loader.py`
- Create: `conf/schemas/tepco_area_demand_generation_actual.yaml`
- Create: `scripts/load_tepco_area_demand_generation.py`

**Interfaces:**
- Consumes: `CsvLoader`, `CsvTableSchema` from `power_market_analytics/csv_loader.py` (constructor `CsvLoader(schema, filepath, table, spark=None)`, `load() -> int`, `_cast(raw, column)`, `schema.read_options`, `schema.columns`); the CSV directory produced by Task 1 (`data/tepco/area_demand_generation/csv/`).
- Produces: `class TepcoAreaCsvLoader(CsvLoader)` with `FILE_UPDATED_AT_SOURCE = "__file_updated_at"`, `EXPECTED_HEADER`, `COLUMN_COUNT = 7`; the raw table `pma_raw.tepco_area_demand_generation_actual` with columns `target_date date, time_code int, period_start_time string, period_end_time string, demand_kwh double, generation_kwh double, wind_solar_generation_kwh double, file_updated_at timestamp` (Task 3's source).

- [ ] **Step 1: Write `power_market_analytics/tepco_loader.py`**

```python
"""Loader for TEPCO エリア需要・発電情報 actuals CSVs into a raw warehouse table.

Each ``AREA_JISEKI_YYYYMMDD.csv`` (format in
docs/TEPCO-Area-Demand-Generation-Retrieval.md) opens with two metadata lines
— the header ``ファイル更新日,ファイル更新時間,対象年月日`` and its values — before
the real column header, so the header-name mapping of
:class:`~power_market_analytics.csv_loader.CsvLoader` cannot be used
directly. :class:`TepcoAreaCsvLoader` therefore reads files headerless — the
load contract addresses columns positionally via ``source: _c0`` .. ``_c6`` —
keeps only data rows (a ``yyyymmdd`` date followed by a time code), and
injects the file's update timestamp from line 2 (contract
``source: __file_updated_at``, a ``yyyyMMdd HH:mm:ss`` string the contract
parses).

Before reading, each file's column-header line is compared with the expected
text so a layout change fails the load instead of silently mis-mapping
columns.
"""

from __future__ import annotations

import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.csv_loader import CsvLoader

#: Python codec equivalent of the ``windows-31j`` Java charset used by the
#: Spark reader (TEPCO serves Shift_JIS with Windows extensions).
_SNIFF_ENCODING = "cp932"


class TepcoAreaCsvLoader(CsvLoader):
    """Positional full reload of TEPCO area actuals CSVs into a warehouse table.

    Works exactly like :class:`CsvLoader` (same constructor, validation and
    write behavior) except for how each file is read; see the module
    docstring. The contract's ``source`` fields must be ``_c<n>`` positions
    plus ``__file_updated_at`` for the injected update timestamp.
    """

    #: Contract ``source`` name for the file update timestamp parsed from line 2.
    FILE_UPDATED_AT_SOURCE = "__file_updated_at"

    #: Exact column-header line (line 3) of every actuals file since 2022-04.
    #: Note the full-width underscores in 時間帯＿自 / 時間帯＿至.
    EXPECTED_HEADER = (
        "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量"
    )

    #: Number of physical columns in the data rows.
    COLUMN_COUNT = 7

    _META_LINE_RE = re.compile(r"^(\d{8}),(\d{2}:\d{2}:\d{2}),(\d{8})")

    def _read_file(self, file: str) -> DataFrame:
        file_updated_at = self._sniff_metadata(file)
        spark_schema = StructType(
            [StructField(f"_c{i}", StringType()) for i in range(self.COLUMN_COUNT)]
        )
        raw = (
            self.spark.read.options(**self.schema.read_options)
            .schema(spark_schema)
            .csv(file)
            # Data rows start with a yyyymmdd date AND a numeric time code; the
            # metadata value line (line 2) also starts with a date, so both
            # conditions are needed.
            .filter(F.col("_c0").rlike(r"^\d{8}$") & F.col("_c1").rlike(r"^\d{1,2}$"))
            .withColumn(self.FILE_UPDATED_AT_SOURCE, F.lit(file_updated_at))
        )
        return raw.select([self._cast(raw, c) for c in self.schema.columns])

    @classmethod
    def _sniff_metadata(cls, file: str) -> str:
        """Check the three header lines and return the file update timestamp.

        Parameters
        ----------
        file : str
            Path to an ``AREA_JISEKI_YYYYMMDD.csv`` file.

        Returns
        -------
        str
            ``"yyyyMMdd HH:mm:ss"`` — ファイル更新日 and ファイル更新時間 from
            line 2, joined by a space, for the contract to parse.

        Raises
        ------
        ValueError
            If the column-header line (line 3) differs from
            ``EXPECTED_HEADER`` or line 2 is not
            ``yyyymmdd,HH:MM:SS,yyyymmdd``.
        """
        with open(file, encoding=_SNIFF_ENCODING) as f:
            lines = [f.readline().rstrip("\r\n") for _ in range(3)]
        if lines[2] != cls.EXPECTED_HEADER:
            raise ValueError(
                f"{file}: unexpected column header {lines[2]!r} — expected "
                f"{cls.EXPECTED_HEADER!r} (TEPCO changed the layout?)"
            )
        match = cls._META_LINE_RE.match(lines[1])
        if match is None:
            raise ValueError(f"{file}: cannot parse the update timestamp from line 2 {lines[1]!r}")
        return f"{match.group(1)} {match.group(2)}"
```

- [ ] **Step 2: Write `conf/schemas/tepco_area_demand_generation_actual.yaml`**

```yaml
description: >
  TEPCO Power Grid エリア需要・発電情報 実績 — Tokyo-area 30-minute actuals of
  total demand (エリア総需要量), total generation (エリア総発電量) and wind+solar
  generation (エリア風力・太陽光発電量) in 30分kWh, i.e. energy per 30-minute
  period. Source: the monthly archives AREA_YYYYMM.zip linked from
  https://www.tepco.co.jp/forecast/html/area-download-j.html (2022-04
  onward), one AREA_JISEKI_YYYYMMDD.csv per day; archive layout, CSV format
  and quirks in docs/TEPCO-Area-Demand-Generation-Retrieval.md. Files are
  read positionally (source _c0.._c6) by TepcoAreaCsvLoader because two
  metadata lines precede the column header; file_updated_at is injected from
  the second metadata line (ファイル更新日 + ファイル更新時間). The three
  measures are typed double, not bigint: 13 files in April 2022 hold
  scientific-notation values such as 1.66919e+07 that Spark's ANSI cast to
  bigint rejects — std rounds them back to bigint. Time code 48 ends at
  24:00, published as "0:00" in period_end_time. TEPCO writes 0 for periods
  not yet observed; the archived 2025-06-14 file froze mid-day, so time codes
  11-48 of that day are all-zero here (nulled in std).

read_options:
  # Java charset name for cp932 / Shift_JIS with Windows extensions
  encoding: windows-31j

grain: [target_date, time_code]

columns:
  - { name: target_date, source: _c0, type: date, format: yyyyMMdd, nullable: false }
  - { name: time_code, source: _c1, type: int, nullable: false }
  - { name: period_start_time, source: _c2, type: string, nullable: false }
  - { name: period_end_time, source: _c3, type: string, nullable: false }
  - { name: demand_kwh, source: _c4, type: double, nullable: false }
  - { name: generation_kwh, source: _c5, type: double, nullable: false }
  - { name: wind_solar_generation_kwh, source: _c6, type: double, nullable: false }
  - { name: file_updated_at, source: __file_updated_at, type: timestamp, format: yyyyMMdd HH:mm:ss, nullable: false }
```

- [ ] **Step 3: Write `scripts/load_tepco_area_demand_generation.py`**

```python
"""Load the extracted TEPCO area actuals CSVs into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_tepco_area_demand_generation.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.tepco_loader import TepcoAreaCsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/tepco_area_demand_generation_actual.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/tepco/area_demand_generation/csv",
        help="CSV file, directory of CSV files, or glob pattern to load.",
    )
    parser.add_argument(
        "--table",
        default="pma_raw.tepco_area_demand_generation_actual",
        help="Destination table (database.table).",
    )
    args = parser.parse_args()

    schema = CsvTableSchema.from_yaml(args.schema)
    loader = TepcoAreaCsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lint and verify the sniff logic host-side (no Spark)**

Run: `uv run ruff check .`
Expected: `All checks passed!`

Run:
```bash
uv run python -c "
from power_market_analytics.tepco_loader import TepcoAreaCsvLoader as L
csv = 'data/tepco/area_demand_generation/csv/'
assert L._sniff_metadata(csv + 'AREA_JISEKI_20250715.csv') == '20250716 00:05:04'
assert L._sniff_metadata(csv + 'AREA_JISEKI_20221201.csv') == '20221214 10:17:01'  # revised later
import tempfile, pathlib
bad = pathlib.Path(tempfile.mkdtemp()) / 'AREA_JISEKI_20990101.csv'
bad.write_bytes('ファイル更新日,ファイル更新時間,対象年月日\r\n20990102,00:05:00,20990101\r\n日付,時間コマ,X\r\n'.encode('cp932'))
try:
    L._sniff_metadata(str(bad)); raise SystemExit('expected ValueError')
except ValueError as e:
    assert 'unexpected column header' in str(e), e
print('OK')
"
```
Expected: `OK`.

- [ ] **Step 5: Run the load in the devcontainer**

Run: `just python scripts/load_tepco_area_demand_generation.py`
Expected: `Loading 1598 file(s) into pma_raw.tepco_area_demand_generation_actual …` (count = files in `csv/`), a `Read shape=(76704, 8)` line with schema `target_date:date, time_code:int, period_start_time:string, period_end_time:string, demand_kwh:double, generation_kwh:double, wind_solar_generation_kwh:double, file_updated_at:timestamp`, and finally `Loaded 76704 rows into pma_raw.tepco_area_demand_generation_actual` (76,704 = 1,598 days × 48 as of 2026-08-16; scale with the day count).

- [ ] **Step 6: Spot-check the raw table**

Run:
```bash
just dbt show --inline "select count(*) as n, count(distinct target_date) as days, min(target_date) as first_day, max(target_date) as last_day, min(time_code) as min_tc, max(time_code) as max_tc from pma_raw.tepco_area_demand_generation_actual" --limit 5
just dbt show --inline "select target_date, time_code, period_start_time, period_end_time, demand_kwh, generation_kwh, wind_solar_generation_kwh, file_updated_at from pma_raw.tepco_area_demand_generation_actual where (target_date = date '2022-04-01' and time_code = 29) or (target_date = date '2025-06-14' and time_code in (10, 11)) or (target_date = date '2025-07-15' and time_code = 48) order by target_date, time_code" --limit 10
```
Expected: row 1 — `n` = days × 48, `days` = 1598 (as of 2026-08-16), `first_day` 2022-04-01, `last_day` = yesterday, `min_tc` 1, `max_tc` 48. Row set 2 — 2022-04-01/29 has `generation_kwh` 16691900.0 (parsed from `1.66919e+07`); 2025-06-14/10 has non-zero values and 2025-06-14/11 has 0.0/0.0/0.0; 2025-07-15/48 has `period_end_time` `0:00` and `file_updated_at` `2025-07-16 00:05:04`.

- [ ] **Step 7: Commit**

```bash
git add power_market_analytics/tepco_loader.py conf/schemas/tepco_area_demand_generation_actual.yaml scripts/load_tepco_area_demand_generation.py
git commit -m "Add TEPCO area actuals load contract and positional loader

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: dbt raw source, staging and standardized models

**Files:**
- Create: `dbt/models/raw/tepco.yml`
- Create: `dbt/models/staging/stg_tepco__area_demand_generation_actual.sql`
- Create: `dbt/models/staging/stg_tepco__area_demand_generation_actual.yml`
- Create: `dbt/models/standardized/std_tepco__area_demand_generation_actual.sql`
- Create: `dbt/models/standardized/std_tepco__area_demand_generation_actual.yml`

**Interfaces:**
- Consumes: `pma_raw.tepco_area_demand_generation_actual` (Task 2 columns).
- Produces: `std_tepco__area_demand_generation_actual` with columns `delivery_date date, time_code int, delivery_datetime timestamp, fiscal_year int, demand_kwh bigint, generation_kwh bigint, wind_solar_generation_kwh bigint, file_updated_at timestamp` (Task 4's input).

- [ ] **Step 1: Write `dbt/models/raw/tepco.yml`**

```yaml
sources:
  - name: tepco
    schema: pma_raw
    description: >
      TEPCO Power Grid (東京電力パワーグリッド) public エリア需要・発電情報 for the
      Tokyo service area, downloaded from the monthly archives linked on
      https://www.tepco.co.jp/forecast/html/area-download-j.html by
      scripts/download_tepco_area_demand_generation.py and loaded by
      scripts/load_tepco_area_demand_generation.py (full reload). Archive
      layout, CSV format and quirks are documented in
      docs/TEPCO-Area-Demand-Generation-Retrieval.md.
    tables:
      - name: tepco_area_demand_generation_actual
        description: >
          実績 (actuals): Tokyo-area total demand, total generation and
          wind+solar generation per 30-minute period, in 30分kWh (energy per
          period). One row per target date and JEPX time code (1-48); grain
          (target_date, time_code) is enforced at load time. History starts
          2022-04-01 and runs through the last finalized day (actuals files
          are finalized ~00:05 the next day). Load contract:
          conf/schemas/tepco_area_demand_generation_actual.yaml.
        columns:
          - name: target_date
            description: Delivery date (日付).
            data_tests:
              - not_null
          - name: time_code
            description: >
              30-minute period (時間コマ): 1 = 0:00-0:30 .. 48 = 23:30-24:00,
              the JEPX time code.
            data_tests:
              - not_null
          - name: period_start_time
            description: Period start as published (時間帯＿自, "H:MM").
          - name: period_end_time
            description: >
              Period end as published (時間帯＿至, "H:MM"); the last period's
              24:00 appears as "0:00".
          - name: demand_kwh
            description: >
              Area total demand (エリア総需要量) in kWh over the 30-minute
              period. Typed double because 13 files in April 2022 use
              scientific notation (e.g. 1.66919e+07); std rounds to bigint.
            data_tests:
              - not_null
          - name: generation_kwh
            description: Area total generation (エリア総発電量) in kWh over the period.
            data_tests:
              - not_null
          - name: wind_solar_generation_kwh
            description: >
              Wind + solar share of area generation (エリア風力・太陽光発電量) in
              kWh over the period.
            data_tests:
              - not_null
          - name: file_updated_at
            description: >
              File creation timestamp from the file's metadata line
              (ファイル更新日 + ファイル更新時間); ~00:05 on target_date + 1 for
              nearly every day, later for the few days TEPCO re-issued.
            data_tests:
              - not_null
```

- [ ] **Step 2: Write `dbt/models/staging/stg_tepco__area_demand_generation_actual.sql`**

```sql
with
  source as (
  select
    target_date,
    time_code,
    period_start_time,
    period_end_time,
    demand_kwh,
    generation_kwh,
    wind_solar_generation_kwh,
    file_updated_at
  from
    {{ source('tepco', 'tepco_area_demand_generation_actual') }}
  )

select * from source
```

- [ ] **Step 3: Write `dbt/models/staging/stg_tepco__area_demand_generation_actual.yml`**

```yaml
models:
  - name: stg_tepco__area_demand_generation_actual
    config:
      contract:
        enforced: true
    description: >
      As-is representation of pma_raw.tepco_area_demand_generation_actual
      (TEPCO Tokyo-area 30-minute demand / generation actuals). One row per
      target_date and time_code. Column documentation lives on the source
      (models/raw/tepco.yml).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - target_date
              - time_code
    columns:
      - name: target_date
        data_type: date
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 48
      - name: period_start_time
        data_type: string
        data_tests:
          - not_null
      - name: period_end_time
        data_type: string
        data_tests:
          - not_null
      - name: demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: generation_kwh
        data_type: double
        data_tests:
          - not_null
      - name: wind_solar_generation_kwh
        data_type: double
        data_tests:
          - not_null
      - name: file_updated_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 4: Write `dbt/models/standardized/std_tepco__area_demand_generation_actual.sql`**

```sql
with
  staging as (
  select
    *
  from
    {{ ref('stg_tepco__area_demand_generation_actual') }}
  ),

  flagged as (
  select
    *,
    -- TEPCO writes 0 for periods not yet observed. A row where all three
    -- measures are 0 is that sentinel (the archived 2025-06-14 file froze
    -- mid-day: time codes 11-48), not an observation — Tokyo demand is never
    -- 0 — so those measures become null below.
    demand_kwh = 0 and generation_kwh = 0 and wind_solar_generation_kwh = 0 as is_unpublished
  from
    staging
  ),

  final as (
  select
    target_date as delivery_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(target_date as timestamp)) as delivery_datetime,
    case when month(target_date) >= 4 then year(target_date) else year(target_date) - 1 end as fiscal_year,
    -- round(): 13 files in April 2022 carry scientific-notation floats
    -- (1.66919e+07), so raw stores double; every other value is an integer.
    case when not is_unpublished then cast(round(demand_kwh) as bigint) end as demand_kwh,
    case when not is_unpublished then cast(round(generation_kwh) as bigint) end as generation_kwh,
    case when not is_unpublished then cast(round(wind_solar_generation_kwh) as bigint) end as wind_solar_generation_kwh,
    file_updated_at
  from
    flagged
  )

select * from final
```

- [ ] **Step 5: Write `dbt/models/standardized/std_tepco__area_demand_generation_actual.yml`**

```yaml
models:
  - name: std_tepco__area_demand_generation_actual
    config:
      contract:
        enforced: true
    description: >
      Standardized TEPCO Tokyo-area 30-minute actuals:
      stg_tepco__area_demand_generation_actual with a typed time axis
      (delivery_date, time_code, delivery_datetime = period start, Japanese
      fiscal_year) and the three measures rounded to bigint kWh (raw stores
      double because 13 April-2022 files use scientific notation). One row
      per delivery_date and time_code. Rows where all three published values
      are 0 — TEPCO's "not yet observed" sentinel, which survived in the
      archive for 2025-06-14 time codes 11-48 — have all three measures set
      to null; the row is kept so the grain stays dense. The published
      period-label strings are dropped (dim_delivery_period carries them).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - delivery_date
              - time_code
      - dbt_utils.expression_is_true:
          name: std_tepco_area_actual_wind_solar_lte_generation
          arguments:
            expression: "wind_solar_generation_kwh <= generation_kwh"
    columns:
      - name: delivery_date
        data_type: date
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 48
      - name: delivery_datetime
        data_type: timestamp
        description: Start of the 30-minute period (delivery_date + (time_code - 1) * 30 min).
        data_tests:
          - not_null
      - name: fiscal_year
        data_type: int
        description: Japanese fiscal year (April-March) of delivery_date.
        data_tests:
          - not_null
      - name: demand_kwh
        data_type: bigint
        description: Area total demand in kWh over the period; null for the unpublished sentinel rows.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: generation_kwh
        data_type: bigint
        description: Area total generation in kWh over the period; null for the unpublished sentinel rows.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: wind_solar_generation_kwh
        data_type: bigint
        description: Wind + solar generation in kWh over the period; null for the unpublished sentinel rows.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: file_updated_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 6: Build and test the three layers**

Run: `just dbt build --select source:tepco+ --exclude fct_tepco_area_demand_generation_actual`
(If dbt rejects the `--exclude` of a not-yet-existing node, use `just dbt build --select stg_tepco__area_demand_generation_actual std_tepco__area_demand_generation_actual`.)
Expected: `stg_tepco__area_demand_generation_actual` and `std_tepco__area_demand_generation_actual` build (`OK created sql table model`), all `PASS`, `Done. PASS=… ERROR=0`. Source tests on `pma_raw.tepco_area_demand_generation_actual` also PASS.

- [ ] **Step 7: Verify the standardization rules**

Run:
```bash
just dbt show --inline "select delivery_date, time_code, delivery_datetime, fiscal_year, demand_kwh, generation_kwh, wind_solar_generation_kwh from {{ ref('std_tepco__area_demand_generation_actual') }} where (delivery_date = date '2022-04-01' and time_code = 29) or (delivery_date = date '2025-06-14' and time_code in (10, 11, 48)) or (delivery_date = date '2024-03-31' and time_code = 48) order by delivery_date, time_code" --limit 10
just dbt show --inline "select count(*) as null_rows from {{ ref('std_tepco__area_demand_generation_actual') }} where demand_kwh is null" --limit 5
```
Expected: 2022-04-01/29 → `generation_kwh` 16691900 (bigint, no decimals), fiscal_year 2022; 2025-06-14/10 has values, /11 and /48 have null measures; 2024-03-31/48 → `delivery_datetime` `2024-03-31 23:30:00`, fiscal_year 2023. Second query: `null_rows` = 38.

- [ ] **Step 8: Commit**

```bash
git add dbt/models/raw/tepco.yml dbt/models/staging/stg_tepco__area_demand_generation_actual.sql dbt/models/staging/stg_tepco__area_demand_generation_actual.yml dbt/models/standardized/std_tepco__area_demand_generation_actual.sql dbt/models/standardized/std_tepco__area_demand_generation_actual.yml
git commit -m "Add TEPCO area actuals raw source, staging and standardized models

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Curated fact `fct_tepco_area_demand_generation_actual` + README star schema

**Files:**
- Create: `dbt/models/curated/fct_tepco_area_demand_generation_actual.sql`
- Create: `dbt/models/curated/fct_tepco_area_demand_generation_actual.yml`
- Modify: `docs/README.md` (fact list near the top; mermaid ER diagram: edges, entity block, `class … fact` line)

**Interfaces:**
- Consumes: `std_tepco__area_demand_generation_actual` (Task 3), `dim_area` (`area_key int`, `area_code string`; Tokyo is `area_code = 'tokyo'`), `dim_date.date_key`, `dim_delivery_period.time_code`.
- Produces: `fct_tepco_area_demand_generation_actual (date_key date, time_code int, area_key int, delivery_datetime timestamp, demand_kwh bigint, generation_kwh bigint, wind_solar_generation_kwh bigint)`.

- [ ] **Step 1: Write `dbt/models/curated/fct_tepco_area_demand_generation_actual.sql`**

```sql
with
  actuals as (
  select
    *
  from
    {{ ref('std_tepco__area_demand_generation_actual') }}
  ),

  -- TEPCO publishes only its own service area. The area dimension is still
  -- part of the grain so the fact conforms with fct_jepx_spot_area_price and
  -- other TSOs' area actuals can be added later.
  tokyo as (
  select
    area_key
  from
    {{ ref('dim_area') }}
  where
    area_code = 'tokyo'
  ),

  final as (
  select
    actuals.delivery_date as date_key,
    actuals.time_code,
    tokyo.area_key,
    actuals.delivery_datetime,
    actuals.demand_kwh,
    actuals.generation_kwh,
    actuals.wind_solar_generation_kwh
  from
    actuals
    cross join tokyo
  )

select * from final
```

- [ ] **Step 2: Write `dbt/models/curated/fct_tepco_area_demand_generation_actual.yml`**

```yaml
models:
  - name: fct_tepco_area_demand_generation_actual
    config:
      contract:
        enforced: true
    description: >
      TEPCO Power Grid エリア需要・発電情報 実績: Tokyo-area total demand, total
      generation and wind+solar generation actuals. Grain: one row per
      delivery period (date_key x 30-minute time_code) per area — the same
      grain as fct_jepx_spot_area_price, so the two join on (date_key,
      time_code, area_key). Only area_code 'tokyo' exists (TEPCO publishes
      its own service area only); the area dimension is kept so the fact is
      conformed and other TSOs can be added later. Covers 2022-04-01 through
      the last finalized day (actuals are finalized ~00:05 the next day and
      the archive is re-downloaded on every refresh, picking up TEPCO's
      occasional revisions). Measures are energy per 30-minute period in kWh
      (30分kWh, as published) and ADDITIVE across periods, days and — once
      more areas exist — areas; divide by 0.5 h for average MW.
      wind_solar_generation_kwh is the wind + solar share of generation_kwh.
      For 2025-06-14 time codes 11-48 all three measures are null: TEPCO's
      archived file for that day froze mid-day with its "not yet observed"
      zeros, which std_tepco__area_demand_generation_actual nulls; the rows
      are kept so the grain stays dense. delivery_datetime is a standalone
      period-start timestamp for time-series work, not a dimension key. Data
      quirks and retrieval details: docs/TEPCO-Area-Demand-Generation-Retrieval.md.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - date_key
              - time_code
              - area_key
      - dbt_utils.expression_is_true:
          name: fct_tepco_area_actual_wind_solar_lte_generation
          arguments:
            expression: "wind_solar_generation_kwh <= generation_kwh"
    columns:
      - name: date_key
        data_type: date
        description: Delivery date; 2022-04-01 onward.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: "date '2022-04-01'"
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_key
      - name: time_code
        data_type: int
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_delivery_period')
                field: time_code
      - name: area_key
        data_type: int
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_area')
                field: area_key
      - name: delivery_datetime
        data_type: timestamp
        data_tests:
          - not_null
      - name: demand_kwh
        data_type: bigint
        description: Area total demand over the 30-minute period, kWh; additive.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: generation_kwh
        data_type: bigint
        description: Area total generation over the 30-minute period, kWh; additive.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: wind_solar_generation_kwh
        data_type: bigint
        description: Wind + solar generation over the 30-minute period, kWh; additive.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
```

- [ ] **Step 3: Build and test the fact**

Run: `just dbt build --select fct_tepco_area_demand_generation_actual`
Expected: model builds, all tests `PASS` (unique combination, not_null ×4, relationships ×3, accepted_range ×4, expression_is_true), `ERROR=0`.

- [ ] **Step 4: Verify the JEPX join and coverage**

Run:
```bash
just dbt show --inline "select count(*) as fact_rows, count(distinct date_key) as days, min(date_key) as first_day, max(date_key) as last_day, count(distinct area_key) as areas from {{ ref('fct_tepco_area_demand_generation_actual') }}" --limit 5
just dbt show --inline "select count(*) as joined_rows from {{ ref('fct_tepco_area_demand_generation_actual') }} as t inner join {{ ref('fct_jepx_spot_area_price') }} as p on p.date_key = t.date_key and p.time_code = t.time_code and p.area_key = t.area_key where t.date_key <= (select max(date_key) from {{ ref('fct_jepx_spot_area_price') }})" --limit 5
```
Expected: query 1 — `fact_rows` = days × 48 (76,704 on 2026-08-16), `first_day` 2022-04-01, `areas` 1. Query 2 — `joined_rows` equals `fact_rows` restricted to dates the JEPX fact already covers (i.e. every TEPCO period up to the JEPX max date matches exactly one price row; if JEPX has been refreshed through yesterday, `joined_rows` = `fact_rows`).

- [ ] **Step 5: Update `docs/README.md`**

(a) In the "Curated star schema" intro change `six fact tables across four subject areas` to `seven fact tables across five subject areas`.

(b) Append this bullet after the `fct_occto_demand_forecast_dad` bullet (before the ```mermaid fence):

```markdown
- `fct_tepco_area_demand_generation_actual` — TEPCO Power Grid Tokyo-area
  actuals: total demand, total generation and wind+solar generation per
  30-minute delivery period (energy in kWh, additive), one row per delivery
  period per area (Tokyo only today; same grain as
  `fct_jepx_spot_area_price`). Covers 2022-04-01 onward; the archive's
  "not yet observed" zeros for 2025-06-14 time codes 11-48 are null.
```

(c) In the mermaid `erDiagram`, add these three edge lines directly after the `dim_area ||--o{ fct_occto_demand_forecast_dad : "area_key"` line:

```
    dim_date ||--o{ fct_tepco_area_demand_generation_actual : "date_key"
    dim_delivery_period ||--o{ fct_tepco_area_demand_generation_actual : "time_code"
    dim_area ||--o{ fct_tepco_area_demand_generation_actual : "area_key"
```

(d) Add this entity block directly after the closing `}` of the `fct_occto_demand_forecast_dad {…}` block (before the `classDef dim` line):

```
    fct_tepco_area_demand_generation_actual {
        date date_key PK, FK
        int time_code PK, FK
        int area_key PK, FK
        timestamp delivery_datetime
        bigint demand_kwh
        bigint generation_kwh
        bigint wind_solar_generation_kwh
    }
```

(e) Append `,fct_tepco_area_demand_generation_actual` to the end of the `class fct_jepx_spot_market,…,fct_occto_demand_forecast_dad fact` line (keep it one line, no spaces after commas).

- [ ] **Step 6: Check the README renders**

Run: `grep -c 'fct_tepco_area_demand_generation_actual' docs/README.md`
Expected: `7` (bullet, 3 edges, entity block, class line — the entity name appears once in each of those 6 places plus once in the bullet's backticks = 7; any count ≥ 6 with the five edits present is fine). Optionally `just open docsify` and eyeball the diagram.

- [ ] **Step 7: Commit**

```bash
git add dbt/models/curated/fct_tepco_area_demand_generation_actual.sql dbt/models/curated/fct_tepco_area_demand_generation_actual.yml docs/README.md
git commit -m "Add fct_tepco_area_demand_generation_actual to the curated star schema

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `just refresh-tepco`, settings allowlist, CLAUDE.md

**Files:**
- Modify: `justfile` (append after the `refresh-occto` recipe)
- Modify: `.claude/settings.json` (`permissions.allow` array)
- Modify: `CLAUDE.md` (Commands list, Architecture list, Gotchas list)

**Interfaces:**
- Consumes: the two scripts from Tasks 1–2.
- Produces: `just refresh-tepco`.

- [ ] **Step 1: Append the recipe to `justfile`** (after the `refresh-occto` recipe, separated by a blank line):

```just
[doc("Refresh TEPCO Tokyo-area demand/generation actuals: redownload all monthly archives, reload raw, rebuild + test dbt")]
refresh-tepco:
    just python scripts/download_tepco_area_demand_generation.py
    just python scripts/load_tepco_area_demand_generation.py
    just dbt build
```

- [ ] **Step 2: Allow the recipe in `.claude/settings.json`**

Insert `"Bash(just refresh-tepco *)",` into the `permissions.allow` array right after the `"Bash(just refresh-occto *)",` entry (the list is ASCII-sorted; the SessionStart hook re-sorts anyway).

- [ ] **Step 3: Update `CLAUDE.md`**

(a) Commands — insert after the `just refresh-occto` bullet:

```markdown
- `just refresh-tepco` — TEPCO Tokyo-area demand/generation actuals refresh: redownload every
  monthly archive (`AREA_YYYYMM.zip`, 2022-04 → now, ~5 MB total), reload `raw`, `dbt build`.
```

(b) Architecture (data flow) — insert after the OCCTO bullet:

```markdown
- TEPCO エリア需要・発電情報 (Tokyo-area 30-min actuals): `scripts/download_tepco_area_demand_generation.py`
  (`TepcoAreaDownloader` in `power_market_analytics/tepco.py`, always re-downloads every monthly
  zip and extracts only the `AREA_JISEKI_*.csv` actuals) → `data/tepco/area_demand_generation/{zip,csv}/`
  → `scripts/load_tepco_area_demand_generation.py` (`TepcoAreaCsvLoader`, positional contract
  `conf/schemas/tepco_area_demand_generation_actual.yaml`) → `pma_raw.tepco_area_demand_generation_actual`
  → `stg/std_tepco__area_demand_generation_actual` → `fct_tepco_area_demand_generation_actual`
  (grain date × time_code × area, Tokyo only; joins `fct_jepx_spot_area_price` 1:1). Format +
  quirks: [docs/TEPCO-Area-Demand-Generation-Retrieval.md](docs/TEPCO-Area-Demand-Generation-Retrieval.md).
```

(c) Gotchas — insert after the OCCTO 翌々日 gotcha bullet:

```markdown
- TEPCO actuals: 13 April-2022 files hold scientific-notation values (`1.66919e+07`) that Spark's
  ANSI `cast(... as bigint)` rejects, so the raw measures are `double` and `std` rounds to
  `bigint`; TEPCO writes 0 for not-yet-observed periods and the archived 2025-06-14 file froze
  mid-day (time codes 11–48 all-zero) → those measures are null from `std` onward. Past days are
  occasionally re-issued, hence the always-re-download policy.
```

- [ ] **Step 4: Verify**

Run: `just --list | grep refresh-tepco && python3 -c "import json; a=json.load(open('.claude/settings.json'))['permissions']['allow']; assert 'Bash(just refresh-tepco *)' in a; print('allow OK')" && grep -c 'refresh-tepco\|TEPCO' CLAUDE.md`
Expected: the recipe line with its doc string, `allow OK`, and a CLAUDE.md count ≥ 3.

- [ ] **Step 5: Commit**

```bash
git add justfile .claude/settings.json CLAUDE.md
git commit -m "Add just refresh-tepco and document the TEPCO pipeline in CLAUDE.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Retrieval doc + docs navigation

**Files:**
- Create: `docs/TEPCO-Area-Demand-Generation-Retrieval.md`
- Modify: `docs/_sidebar.md`

**Interfaces:**
- Consumes: facts established in the spec §3 and the module/script names from Tasks 1–2.
- Produces: the document every description above links to.

- [ ] **Step 1: Write `docs/TEPCO-Area-Demand-Generation-Retrieval.md`**

````markdown
# TEPCO エリア需要・発電情報 (Area Demand & Generation) Data Retrieval

How TEPCO Power Grid publishes the Tokyo-area 30-minute demand / generation
actuals, what the files look like, and how `power_market_analytics.tepco` /
`power_market_analytics.tepco_loader` bring them into the warehouse.

Verified against a full capture on 2026-08-16 (every daily file 2022-04-01 →
2026-08-15).

## 1. Overview

- **Publisher**: 東京電力パワーグリッド (TEPCO Power Grid), under METI's
  「系統情報の公表の考え方」.
- **Page**: <https://www.tepco.co.jp/forecast/html/area-download-j.html>
  (エリア需要・発電情報のダウンロード). A different page,
  `area_data-j.html` (エリア需給実績, per-fuel generation), exists but is not
  used here.
- **Content**: per 30-minute period, エリア総需要量 (area total demand),
  エリア総発電量 (area total generation) and エリア風力・太陽光発電量 (wind +
  solar generation), all in **30分kWh** — energy over the period. Three
  series per day: 実績 (actuals), 予測 (TEPCO's forecast) and BG計画総計
  (balancing-group plan total). **Only 実績 is loaded.**
- **Coverage**: 2022-04-01 → yesterday, complete (no missing days).

## 2. Files and URLs

| What | URL | Notes |
|---|---|---|
| Monthly archive (history) | `https://www4.tepco.co.jp/forecast/html/images/AREA_YYYYMM.zip` | 2022-04 → current month; ~100 KB each, 53 zips ≈ 5 MB (2026-08). Current month's zip is regenerated daily and contains all finalized days through yesterday. |
| Today's actuals (live) | `…/images/AREA_JISEKI.csv` | Partial: periods not yet observed are 0. Not used. |
| Today's forecast / BG plan (live) | `…/images/AREA_YOSOKU.csv`, `…/images/AREA_BGKEI.csv` | Revised during the day. Not used. |
| Tomorrow's forecast / BG plan | `…/images/AREA_ONCE_YOSOKU.csv`, `…/images/AREA_ONCE_BGKEI.csv` | Published in the evening; header only before that. Not used. |

Each zip holds three files per day — `AREA_JISEKI_YYYYMMDD.csv`,
`AREA_YOSOKU_YYYYMMDD.csv`, `AREA_BGKEI_YYYYMMDD.csv` — as flat members,
**except `AREA_202403.zip`, whose members sit under an `AREA_202403/`
subfolder**. The downloader flattens on extraction. Plain `GET`, no session
or token; the response is `application/zip`.

## 3. `AREA_JISEKI_YYYYMMDD.csv` format

- **Encoding**: CP932 (Shift_JIS). **Line endings**: CRLF.
- **Layout** (identical in all 1,598 files captured):

```
ファイル更新日,ファイル更新時間,対象年月日                      ← line 1: metadata header
20250716,00:05:04,20250715                                   ← line 2: file update date/time, target date
日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量   ← line 3: column header
20250715,1,0:00,0:30,15059000,12421000,141000                ← lines 4-51: 48 data rows
…
20250715,48,23:30,0:00,15531000,12507000,131000
```

| # | Column | Meaning | Notes |
|---|---|---|---|
| 1 | 日付 | Delivery date | `yyyymmdd`; equals the file-name date on every row |
| 2 | 時間コマ | 30-minute period | 1 = 0:00–0:30 … 48 = 23:30–24:00 (= JEPX time code) |
| 3 | 時間帯＿自 | Period start | `H:MM` (full-width underscore in the header) |
| 4 | 時間帯＿至 | Period end | `H:MM`; 24:00 is written `0:00` |
| 5 | エリア総需要量 | Area total demand | kWh over the period (30分kWh) |
| 6 | エリア総発電量 | Area total generation | kWh over the period |
| 7 | エリア風力・太陽光発電量 | Wind + solar generation | kWh over the period; ≤ column 6 on every row |

Exactly 48 rows per file, 7 fields per row, no blank cells, no negatives.
The 予測 / BG計画 files use a slightly different header
(`時間帯_自(HH:MI)`, unit suffixes `[30分kWh]`, times as `HHMM`) — another
reason the loader checks the header text of every file it reads.

**Grain**: one row per (日付, 時間コマ).

## 4. Data quirks (verified)

1. **Scientific notation** — 13 files, 2022-04-01 … 2022-04-13, contain 47
   cells like `1.66919e+07` (precision lost to ~10 kWh). Spark's ANSI
   `cast(string as bigint)` throws on these, so the load contract types the
   three measures as `double`; `std_tepco__area_demand_generation_actual`
   rounds back to `bigint`.
2. **"Not yet observed" zeros** — TEPCO writes 0 for future periods in the
   live file. The archived `AREA_JISEKI_20250614.csv` froze mid-day: time
   codes 11–48 are 0/0/0. `std` nulls all three measures on rows where all
   three are 0 (Tokyo demand is never 0); the rows are kept.
3. **Isolated zero** — 2023-09-17 time code 11 has wind+solar = 0 with normal
   demand/generation. Left as published.
4. **Revisions** — actuals files are normally created at ~00:05 on
   target date + 1 (`ファイル更新日/時間`), but a few were re-issued later
   (2022-12-01 and 12-02 on 2022-12-14 10:17; 2024-03-11 on 2024-04-19).
   Because past months are therefore not immutable and the whole history is
   ~5 MB, the downloader re-fetches every zip on every run.

## 5. Publication timing

- 実績: 「対象となる時間帯が終了後、すみやかに公表」 — the live file updates
  every 30 minutes; the day's file is finalized at ~00:05 the next morning
  and appears in that month's zip the same day.
- 予測: 「前日夕方に公表」 (next-day file in the evening), then revised
  intraday; the archived copy is the **last revision (~23:40 on the target
  day)**, not the day-ahead version — which is why 予測 / BG計画 are out of
  scope as day-ahead features.
- BG計画総計: published after the next-day and same-day plans are fixed;
  archived copy = last revision, same caveat.

## 6. Downloading and loading with `power_market_analytics.tepco`

```python
from power_market_analytics.tepco import TepcoAreaDownloader

downloader = TepcoAreaDownloader()          # data/tepco/area_demand_generation
downloader.download(2025, 7)                 # one month -> 31 csv/ files
downloader.download_all()                    # 2022-04 .. current month
# zips  -> data/tepco/area_demand_generation/zip/AREA_YYYYMM.zip
# csvs  -> data/tepco/area_demand_generation/csv/AREA_JISEKI_YYYYMMDD.csv
```

The loader (`TepcoAreaCsvLoader` in `power_market_analytics/tepco_loader.py`)
reads the CSVs positionally (`_c0`..`_c6`, contract
`conf/schemas/tepco_area_demand_generation_actual.yaml`), verifies each
file's column-header line, filters to the 48 data rows, injects
`file_updated_at` from line 2, and full-reloads
`pma_raw.tepco_area_demand_generation_actual`. End to end:

```bash
just refresh-tepco
# = just python scripts/download_tepco_area_demand_generation.py
#   just python scripts/load_tepco_area_demand_generation.py
#   just dbt build
```

Warehouse path: `pma_raw.tepco_area_demand_generation_actual` →
`stg_tepco__area_demand_generation_actual` →
`std_tepco__area_demand_generation_actual` (typed time axis, bigint kWh,
sentinel rows nulled) → `fct_tepco_area_demand_generation_actual`
(date_key × time_code × area_key, Tokyo).

## 7. Extending

- **予測 / BG計画**: add `AREA_YOSOKU_` / `AREA_BGKEI_` to the extraction
  regex (`ACTUALS_MEMBER_RE`), give each its own contract (their header text
  and `HHMM` time labels differ) and raw table
  (`tepco_area_demand_generation_forecast` / `_bg_plan`), and remember the
  archived copies are last-intraday revisions.
- **Live files**: `AREA_JISEKI.csv` (today, partial) could feed an intraday
  view; it uses the same layout as the archived actuals.
````

- [ ] **Step 2: Add the doc to `docs/_sidebar.md`**

Insert after the `- [OCCTO Demand Forecast Retrieval](OCCTO-Demand-Forecast-Retrieval.md)` line:

```markdown
- [TEPCO Area Demand & Generation Retrieval](TEPCO-Area-Demand-Generation-Retrieval.md)
```

- [ ] **Step 3: Verify the docs**

Run: `grep -n 'TEPCO' docs/_sidebar.md && grep -c '' docs/TEPCO-Area-Demand-Generation-Retrieval.md && grep -rn 'TEPCO-Area-Demand-Generation-Retrieval.md' CLAUDE.md conf/schemas dbt/models | wc -l`
Expected: the sidebar line, a line count > 100, and ≥ 3 references to the doc from CLAUDE.md / the contract / dbt yml files (they all point at this file name).

- [ ] **Step 4: Commit**

```bash
git add docs/TEPCO-Area-Demand-Generation-Retrieval.md docs/_sidebar.md
git commit -m "Document TEPCO area demand/generation retrieval and CSV format

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full-build verification

**Files:** none (verification only).

- [ ] **Step 1: Full dbt build**

Run: `just dbt build`
Expected: every model builds and every test passes (`Done. PASS=… WARN=0 ERROR=0 SKIP=0`).

- [ ] **Step 2: Lint everything**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!` and no files listed as needing formatting.

- [ ] **Step 3: Confirm the working tree is clean and list the commits**

Run: `git status --short && git log --oneline bc57188..HEAD`
Expected: no uncommitted changes; six commits (Tasks 1–6) on top of the spec commit `bc57188`.
