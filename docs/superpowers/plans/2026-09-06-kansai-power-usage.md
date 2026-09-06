# Kansai でんき予報 Hourly 電力使用実績 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load 関西電力送配電's でんき予報 hourly demand history (2016-04-01 → yesterday) into `pma_raw.kansai_power_usage_hourly` → stg → std → a `kansai` branch of `fct_area_power_usage_hourly`, the way TEPCO's is loaded.

**Architecture:** The TEPCO でんき予報 parser and loader move into a shared module `power_market_analytics/power_usage.py` (`PowerUsageSource`, `parse_hourly(file, source)`, `PowerUsageCsvLoader` with a per-file `_file_rows` hook) and gain two rules the Kansai archive needs: trailing commas are stripped from every line, and a `修正後` row corrects the row above it. `kansai.py` becomes the package `kansai/` (`area_demand_generation.py`, `power_usage.py`). `AreaActualsSource` gains `known_missing_days` so the settled month 2024-03 may lack the 31st. dbt copies TEPCO's stg/std pair and unions a second CTE into the fact.

**Tech Stack:** Python 3.13 / PySpark 4.1.1, pytest (100 % coverage gate), ruff, mypy, dbt 1.11 on the thriftserver, `just`, `docker compose`.

**Spec:** `docs/superpowers/specs/2026-09-05-kansai-power-usage-design.md` (approved 2026-09-06; both open points resolved as recommended: one PR, the `修正後` row applied).

## Global Constraints

- Work in the git worktree `.claude/worktrees/kansai-power-usage` on branch `feature/kansai-power-usage` (from `main`); the main checkout stays on the researcher's branch. Every host command below runs from the worktree root; container commands set `-w`/`PYTHONPATH` to `/workspace/.claude/worktrees/kansai-power-usage` (`WT` below).
- Gates before the PR: `just test` (100 % coverage), `just lint`, `just mypy`, host-side `dbt parse`, a real download + load + full `dbt build` in the devcontainer.
- NumPy docstrings everywhere; every dbt model has an enforced contract and a grain uniqueness test; dbt generic-test args under `arguments:`.
- Commits `type(scope): description`, scopes `area-actuals`, `power-usage`, `tepco`, `kansai`, `dbt`, `docs`; trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. `git add` only named files.
- The PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` you write; re-read a file before editing it again.
- Long operations (download, load, `dbt build`) are main-session background tasks.
- Never write the Codex mention (the bot handle + `review`) anywhere in the diff or PR body.
- Writing style for docs and the PR body: short sentences, plain words, one idea each.

---

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/area_actuals.py` (modify) | `AreaActualsSource.known_missing_days`; `_check_month_coverage` becomes an instance method that subtracts them and logs a listed day that is present |
| `power_market_analytics/power_usage.py` (create) | `CORRECTION_MARKER`, `PowerUsageSource`, `HourlyRow`, `HourlyFile`, `parse_hourly(file, source)`, the `__` contract source names, `PowerUsageCsvLoader` |
| `power_market_analytics/tepco/power_usage.py` (shrink) | `TEPCO_POWER_USAGE` as a `PowerUsageSource`, yearly-file constants + downloader, `parse_hourly(file)` bound to TEPCO, `TepcoPowerUsageCsvLoader._file_rows` (yearly-row drop) |
| `power_market_analytics/kansai/__init__.py` (create), `kansai/area_demand_generation.py` (git mv of `kansai.py`) | package with one module per dataset, A-1 names re-exported |
| `power_market_analytics/kansai/power_usage.py` (create) | the three hourly headers, `KANSAI_POWER_USAGE`, `KansaiPowerUsageDownloader`, `KansaiPowerUsageCsvLoader` |
| `conf/schemas/kansai_power_usage_hourly.yaml` (create) | raw load contract (TEPCO's columns) |
| `scripts/download_kansai_power_usage.py`, `scripts/load_kansai_power_usage.py` (create); `justfile` (modify) | entry points; `refresh-all` runs them after the Kansai A-1 pair |
| `dbt/models/raw/kansai.yml` (modify), `dbt/models/staging/stg_kansai__power_usage_hourly.{sql,yml}`, `dbt/models/standardized/std_kansai__power_usage_hourly.{sql,yml}`, `dbt/dbt_tests/assert_std_kansai__power_usage_hourly_calendar_complete.sql` (create), `dbt/models/curated/fct_area_power_usage_hourly.{sql,yml}` (modify) | warehouse path |
| `tests/test_area_actuals.py`, `tests/test_power_usage.py` (create), `tests/test_tepco_power_usage.py`, `tests/test_kansai.py`, `tests/test_kansai_power_usage.py` (create), `tests/test_download_scripts.py`, `tests/test_load_scripts.py` | tests |
| `docs/Kansai-Power-Usage-Retrieval.md` (create), `docs/_sidebar.md`, `docs/README.md`, `docs/Kansai-Area-Demand-Generation-Retrieval.md`, `docs/TEPCO-Power-Usage-Retrieval.md`, `CLAUDE.md`, the spec | docs |

Names every task relies on: contract sources `__target_date`, `__hour_start`, `__demand_mankw`, `__forecast_mankw`, `__usage_rate_pct`, `__supply_capacity_mankw`, `__file_updated_at`, `__source_file`; hour convention `TIME h:00` = `hour_start h` (0–23); the Kansai headers

```
HOURLY_HEADER_2016 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"                      # 2016-04-01 → 2019-09-11
HOURLY_HEADER_2019 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力想定値(万kW)"   # 2019-09-12 → 2025-12-24
HOURLY_HEADER_2025 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力(万kW)"         # 2025-12-25 →
```

Real file facts (probed 2026-09-06 from `201604`, `202503`, `202509`, `202512` zips): line 1 `2016/4/2 1:10 UPDATE`; padded files end *every* line with commas (`2025/3/15 1:10 UPDATE,,,,,`, blank lines `,,,,,`, a 5-field header `…,使用率(%),`); the correction row is `修正後,,1212,,65,4/25 システム不具合による数値誤りのため修正` directly under `2016/4/24,0:00,1267,1230,68,`; the hourly table ends at the first blank line, the 5-minute table follows later.

---

### Task 0: Worktree, branch, spec status, plan

**Files:** `docs/superpowers/specs/2026-09-05-kansai-power-usage-design.md` (status line), this plan.

- [ ] **Step 1: Create the worktree** (superpowers:using-git-worktrees)

```bash
cd /Users/hankehly/Projects/power-market-analytics
git worktree add .claude/worktrees/kansai-power-usage -b feature/kansai-power-usage main
mv docs/superpowers/specs/2026-09-05-kansai-power-usage-design.md .claude/worktrees/kansai-power-usage/docs/superpowers/specs/
mv docs/superpowers/plans/2026-09-06-kansai-power-usage.md .claude/worktrees/kansai-power-usage/docs/superpowers/plans/
git status --short   # only the researcher's conf/superset/superset_config.py remains
cd .claude/worktrees/kansai-power-usage && uv sync --locked
```

- [ ] **Step 2: Mark the spec approved**

Replace line 3 of the spec with:

```
Date: 2026-09-05. Status: **approved 2026-09-06** (in chat; both open points resolved as
recommended — one PR, the `修正後` row applied). Implemented per
`docs/superpowers/plans/2026-09-06-kansai-power-usage.md` on branch `feature/kansai-power-usage`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-05-kansai-power-usage-design.md docs/superpowers/plans/2026-09-06-kansai-power-usage.md
git commit -m "docs(kansai): approve the でんき予報 design spec and add the implementation plan" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: `AreaActualsSource.known_missing_days`

**Files:**
- Modify: `power_market_analytics/area_actuals.py` (dataclass lines 65-99; `_check_month_coverage` lines 234-264)
- Test: `tests/test_area_actuals.py`

**Interfaces:**
- Produces: `AreaActualsSource.known_missing_days: frozenset[datetime.date] = frozenset()`; `AreaActualsDownloader._check_month_coverage(self, zip_path, year, month, extracted, today)` (instance method) raises only for missing days outside the set and logs at INFO the listed days that are present.

- [ ] **Step 1: Write the failing tests**

In `tests/test_area_actuals.py`, add to `TestAreaActualsSource`:

```python
    def test_known_missing_days_default_to_none(self):
        assert SOURCE.known_missing_days == frozenset()
```

Add `from loguru import logger  # noqa: E402` next to the downloader-section imports (after `import requests  # noqa: E402`) and append to `TestAreaActualsDownloader`:

```python
    def test_download_tolerates_a_known_missing_day_in_a_settled_month(self, tmp_path):
        # Kansai's でんき予報 never published 2024-03-31 (site maintenance); a
        # spec lists such days so a settled month may lack them.
        members = {f"DEMO_JISEKI_202507{d:02d}.csv": b"x" for d in range(1, 32) if d != 20}
        source = dataclasses.replace(
            SOURCE, known_missing_days=frozenset({datetime.date(2025, 7, 20)})
        )
        dl = AreaActualsDownloader(
            source, data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )

        assert len(dl.download(2025, 7, today=datetime.date(2025, 9, 1))) == 30

    def test_download_still_rejects_an_unlisted_missing_day(self, tmp_path):
        members = {
            f"DEMO_JISEKI_202507{d:02d}.csv": b"x" for d in range(1, 32) if d not in (20, 21)
        }
        source = dataclasses.replace(
            SOURCE, known_missing_days=frozenset({datetime.date(2025, 7, 20)})
        )
        dl = AreaActualsDownloader(
            source, data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )

        with pytest.raises(AreaActualsDownloadError, match="missing 1 day") as exc:
            dl.download(2025, 7, today=datetime.date(2025, 9, 1))
        assert "20250721" in str(exc.value)
        assert "20250720" not in str(exc.value)

    def test_download_logs_a_known_missing_day_that_is_published(self, tmp_path):
        members = {f"DEMO_JISEKI_202507{d:02d}.csv": b"x" for d in range(1, 32)}
        source = dataclasses.replace(
            SOURCE, known_missing_days=frozenset({datetime.date(2025, 7, 20)})
        )
        dl = AreaActualsDownloader(
            source, data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )
        messages: list[str] = []
        sink = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")
        try:
            assert len(dl.download(2025, 7, today=datetime.date(2025, 9, 1))) == 31
        finally:
            logger.remove(sink)

        assert any("20250720" in m and "known-missing" in m for m in messages)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_area_actuals.py -q -p no:cacheprovider`
Expected: 4 failures (`known_missing_days` unexpected keyword / attribute).

- [ ] **Step 3: Implement**

`power_market_analytics/area_actuals.py` — dataclass docstring, after the `archive_includes_current_day` entry:

```
    known_missing_days : frozenset of datetime.date, default empty
        Days the TSO never published (Kansai でんき予報 2024-03-31, a
        site-maintenance day). A settled month may lack their members
        without failing the download; a listed day that turns up after all
        is logged at INFO so the entry can be retired.
```

field after `archive_includes_current_day: bool = False`:

```python
    known_missing_days: frozenset[datetime.date] = frozenset()
```

`download` docstring Raises: change "or is a settled month missing a day" to "or is a settled month missing a day not in the source's ``known_missing_days``". Replace `_check_month_coverage`:

```python
    def _check_month_coverage(
        self, zip_path: Path, year: int, month: int, extracted: list[Path], today: datetime.date
    ) -> None:
        first = datetime.date(year, month, 1)
        month_end = (first.replace(day=28) + datetime.timedelta(days=4)).replace(
            day=1
        ) - datetime.timedelta(days=1)
        found: set[datetime.date] = set()
        for path in extracted:
            match = _MEMBER_DATE_RE.search(path.name)
            if match is None:
                raise AreaActualsDownloadError(
                    f"{zip_path}: member {path.name} has no yyyymmdd date"
                )
            day = datetime.datetime.strptime(match.group(0), "%Y%m%d").date()
            if not first <= day <= month_end:
                raise AreaActualsDownloadError(
                    f"{zip_path}: member {path.name} is dated {day:%Y%m%d}, outside {year}-{month:02d}"
                )
            found.add(day)
        known = self.source.known_missing_days
        published = sorted(found & known)
        if published:
            logger.info(
                "{}: known-missing day(s) {} are published after all; retire them from the "
                "{} spec's known_missing_days",
                zip_path,
                [d.strftime("%Y%m%d") for d in published],
                self.source.code,
            )
        if month_end < today - datetime.timedelta(days=1):
            expected = {
                first + datetime.timedelta(days=i) for i in range((month_end - first).days + 1)
            }
            missing = sorted(expected - known - found)
            if missing:
                raise AreaActualsDownloadError(
                    f"{zip_path}: settled month {year}-{month:02d} is missing {len(missing)} day(s): "
                    f"{[d.strftime('%Y%m%d') for d in missing[:5]]}"
                )
```

(the `@staticmethod` decorator goes away; the call site `self._check_month_coverage(dest, year, month, extracted, today or datetime.date.today())` is unchanged.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_area_actuals.py tests/test_tepco.py tests/test_kansai.py tests/test_tepco_power_usage.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/area_actuals.py tests/test_area_actuals.py
git commit -m "feat(area-actuals): known_missing_days on AreaActualsSource" -m "A settled month may lack the listed days (Kansai でんき予報 2024-03-31); a listed day that is published is logged so the entry can be retired." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Shared `power_market_analytics/power_usage.py`; TEPCO keeps only its own

**Files:**
- Create: `power_market_analytics/power_usage.py`
- Modify: `power_market_analytics/tepco/power_usage.py`
- Create: `tests/test_power_usage.py`
- Test (unchanged, regression): `tests/test_tepco_power_usage.py`

**Interfaces:**
- Consumes: `AreaActualsSource` (+ `known_missing_days`, Task 1), `CsvLoader`, `SOURCE_FILE_COL`, `CsvTableSchema`.
- Produces: `PowerUsageSource(AreaActualsSource)` with `multi_day_headers: frozenset[str] = frozenset()`; `CORRECTION_MARKER = "修正後"`; `HourlyRow`, `HourlyFile` (unchanged shapes); `parse_hourly(file: Path | str, source: PowerUsageSource) -> HourlyFile`; `PowerUsageCsvLoader(CsvLoader)` with class attribute `source: PowerUsageSource | None`, constructor `(schema, filepath, table, spark=None, source=None)`, hook `_file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]`; module constants `TARGET_DATE_SOURCE` … `SOURCE_FILE_SOURCE`, `_SOURCE_COLUMNS`. `tepco.power_usage` re-exports `HourlyFile`, `HourlyRow` and a one-argument `parse_hourly(file)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_power_usage.py`:

```python
"""Shared でんき予報 parser and loader (power_market_analytics/power_usage.py)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from power_market_analytics.area_actuals import AreaActualsSource
from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvTableSchema
from power_market_analytics.power_usage import (
    CORRECTION_MARKER,
    HourlyFile,
    HourlyRow,
    PowerUsageCsvLoader,
    PowerUsageSource,
    parse_hourly,
)

# --- a demo source and fixture builders -------------------------------------

HEADER_5 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"
HEADER_6 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力(万kW)"
MULTI_DAY_HEADER = "DATE,TIME,実績(万kW)"

DEMO = PowerUsageSource(
    code="demo_power_usage",
    url_template="https://example.test/{year:04d}{month:02d}.zip",
    earliest_month=(2016, 4),
    member_re=re.compile(r"\d{8}_demo\.csv$"),
    accepted_headers=frozenset({HEADER_5, HEADER_6, MULTI_DAY_HEADER}),
    default_data_dir="data/demo/power_usage",
    multi_day_headers=frozenset({MULTI_DAY_HEADER}),
)


def hourly_rows(date: str, header: str = HEADER_5, hours: int = 24, base: int = 1200) -> list[str]:
    """One day's hourly rows: demand base+h, forecast base-10+h, 使用率 60+h%5, supply 2000+h."""
    width = header.count(",") + 1
    return [
        ",".join(
            [
                date,
                f"{hour}:00",
                str(base + hour),
                str(base - 10 + hour),
                str(60 + hour % 5),
                str(2000 + hour),
            ][:width]
        )
        for hour in range(hours)
    ]


def daily_lines(
    date: str = "2016/4/1",
    updated: str = "2016/4/2 1:10",
    header: str = HEADER_5,
    rows: list[str] | None = None,
) -> list[str]:
    """A daily でんき予報 file in the multi-section layout, as a list of lines."""
    return [
        f"{updated} UPDATE",
        "ピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,ピーク時使用率(%)",
        "2060,9:00～10:00,4/1,9:30,88",
        "",
        "予想最大電力(万kW),時間帯,予想最大電力情報更新日,予想最大電力情報更新時刻",
        "1830,9:00～10:00,4/1,9:30",
        "",
        header,
        *(hourly_rows(date, header) if rows is None else rows),
        "",
        "翌日のピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,ピーク時使用率(%)",
        "1968,19:00～20:00,4/1,20:20,78",
        "",
        "DATE,TIME,当日実績(５分間隔値)(万kW)",
        f"{date},0:00,1310",
        f"{date},0:05,1300",
        "",
    ]


def padded(lines: list[str]) -> list[str]:
    """Every line padded with commas to the widest line, as Excel re-saves a CSV."""
    width = max(line.count(",") + 1 for line in lines)
    return [line + "," * (width - line.count(",") - 1) for line in lines]


def write_lines_cp932(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


# --- source --------------------------------------------------------------------


class TestPowerUsageSource:
    def test_is_an_area_actuals_source(self):
        assert isinstance(DEMO, AreaActualsSource)
        assert DEMO.zip_url(2016, 4) == "https://example.test/201604.zip"
        assert DEMO.is_actuals_member("20160401_demo.csv")

    def test_multi_day_headers_default_to_none(self):
        plain = PowerUsageSource(
            code="plain",
            url_template="https://example.test/{year}{month}.zip",
            earliest_month=(2016, 4),
            member_re=re.compile(r"\.csv$"),
            accepted_headers=frozenset({HEADER_5}),
            default_data_dir="data/plain",
        )
        assert plain.multi_day_headers == frozenset()
        assert plain.known_missing_days == frozenset()

    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            DEMO.multi_day_headers = frozenset()  # type: ignore[misc]


# --- parser -------------------------------------------------------------------


class TestParseHourly:
    def test_plain_file_yields_the_hourly_rows_only(self, tmp_path):
        file = write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines())

        parsed = parse_hourly(file, DEMO)

        assert isinstance(parsed, HourlyFile)
        assert parsed.file_updated_at == "20160402 01:10:00"
        assert parsed.header == HEADER_5
        assert len(parsed.rows) == 24
        assert parsed.rows[0] == HourlyRow("20160401", 0, "1200", "1190", "60", None)
        assert parsed.rows[23] == HourlyRow("20160401", 23, "1223", "1213", "63", None)

    def test_six_field_header_fills_the_supply_capacity(self, tmp_path):
        lines = daily_lines("2025/9/1", "2025/9/2 1:10", HEADER_6)
        file = write_lines_cp932(tmp_path / "20250901_demo.csv", lines)

        rows = parse_hourly(file, DEMO).rows

        assert rows[5] == HourlyRow("20250901", 5, "1205", "1195", "60", "2005")

    def test_padded_file_parses_like_the_plain_one(self, tmp_path):
        # 60 Kansai files were re-saved from Excel: every line padded to the
        # widest row, ",,,,," where a blank line belongs, the header ending in
        # a comma. Without the trailing-comma rule the table never ends.
        lines = daily_lines()
        lines[1] = "ピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,ピーク時予備率(%),ピーク時使用率(%)"
        lines[2] = "2039,9:00～10:00,3月15日,0:13,15,86"
        padded_lines = padded(lines)
        assert padded_lines[0].endswith(" UPDATE,,,,,")
        assert padded_lines[3] == ",,,,,"
        assert padded_lines[7] == HEADER_5 + ","
        plain = write_lines_cp932(tmp_path / "plain.csv", daily_lines())
        excel = write_lines_cp932(tmp_path / "excel.csv", padded_lines)

        assert parse_hourly(excel, DEMO) == parse_hourly(plain, DEMO)

    def test_correction_row_replaces_the_non_blank_measures_of_the_row_above(self, tmp_path):
        # Kansai 2016-04-24: 修正後,,1212,,65,<reason> under the hour-0 row
        # (1267 万kW, 68 %) — demand and 使用率 change, the forecast stays.
        rows = hourly_rows("2016/4/24")
        rows.insert(1, f"{CORRECTION_MARKER},,1212,,65,4/25 システム不具合による数値誤りのため修正")
        lines = padded(daily_lines("2016/4/24", "2016/4/25 1:10", rows=rows))
        file = write_lines_cp932(tmp_path / "20160424_demo.csv", lines)

        parsed = parse_hourly(file, DEMO).rows

        assert len(parsed) == 24
        assert parsed[0] == HourlyRow("20160424", 0, "1212", "1190", "65", None)
        assert parsed[1] == HourlyRow("20160424", 1, "1201", "1191", "61", None)

    def test_correction_row_shorter_than_the_header_keeps_the_other_measures(self, tmp_path):
        rows = hourly_rows("2016/4/24")
        rows.insert(1, f"{CORRECTION_MARKER},,1212")
        file = write_lines_cp932(
            tmp_path / "20160424_demo.csv", daily_lines("2016/4/24", "2016/4/25 1:10", rows=rows)
        )

        assert parse_hourly(file, DEMO).rows[0] == HourlyRow("20160424", 0, "1212", "1190", "60", None)

    def test_correction_row_under_the_header_raises(self, tmp_path):
        rows = [f"{CORRECTION_MARKER},,1212,,65", *hourly_rows("2016/4/24")]
        file = write_lines_cp932(
            tmp_path / "20160424_demo.csv", daily_lines("2016/4/24", "2016/4/25 1:10", rows=rows)
        )

        with pytest.raises(ValueError, match="no hourly row above"):
            parse_hourly(file, DEMO)

    def test_row_with_a_blank_last_measure_fails_the_field_count(self, tmp_path):
        # A trailing blank cell is indistinguishable from padding once the
        # trailing commas are stripped: fail loudly rather than load a shifted row.
        rows = hourly_rows("2016/4/1")
        rows[3] = "2016/4/1,3:00,1203,1193,"
        file = write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines(rows=rows))

        with pytest.raises(ValueError, match="4 fields, expected 5"):
            parse_hourly(file, DEMO)

    def test_daily_file_with_two_dates_raises(self, tmp_path):
        rows = [*hourly_rows("2016/4/1")[:23], *hourly_rows("2016/4/2")[23:]]
        file = write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines(rows=rows))

        with pytest.raises(ValueError, match="one target date"):
            parse_hourly(file, DEMO)

    def test_multi_day_header_may_hold_many_dates(self, tmp_path):
        rows = [
            *hourly_rows("2016/4/1", MULTI_DAY_HEADER),
            *hourly_rows("2016/4/2", MULTI_DAY_HEADER),
        ]
        lines = ["2018/1/1 18:10 UPDATE", "", MULTI_DAY_HEADER, *rows]
        file = write_lines_cp932(tmp_path / "juyo-2016.csv", lines)

        parsed = parse_hourly(file, DEMO)

        assert len(parsed.rows) == 48
        assert parsed.rows[24] == HourlyRow("20160402", 0, "1200", None, None, None)


# --- loader -------------------------------------------------------------------

CONTRACT = CsvTableSchema.model_validate(
    {
        "grain": ["target_date", "hour_start"],
        "columns": [
            {
                "name": "target_date",
                "source": "__target_date",
                "type": "date",
                "format": "yyyyMMdd",
                "nullable": False,
            },
            {"name": "hour_start", "source": "__hour_start", "type": "int", "nullable": False},
            {"name": "demand_mankw", "source": "__demand_mankw", "type": "double", "nullable": False},
            {"name": "forecast_mankw", "source": "__forecast_mankw", "type": "double"},
            {"name": "usage_rate_pct", "source": "__usage_rate_pct", "type": "double"},
            {
                "name": "supply_capacity_mankw",
                "source": "__supply_capacity_mankw",
                "type": "double",
            },
            {
                "name": "file_updated_at",
                "source": "__file_updated_at",
                "type": "timestamp",
                "format": "yyyyMMdd HH:mm:ss",
                "nullable": False,
            },
            {"name": "source_file", "source": "__source_file", "type": "string", "nullable": False},
        ],
    }
)


class TestPowerUsageCsvLoader:
    def test_requires_a_source(self, spark, tmp_path):
        with pytest.raises(ValueError, match="source"):
            PowerUsageCsvLoader(CONTRACT, tmp_path, "test_pu.none", spark=spark)

    def test_loads_every_file_into_one_relation(self, spark, tmp_path):
        write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines())
        write_lines_cp932(
            tmp_path / "20160402_demo.csv", daily_lines("2016/4/2", "2016/4/3 1:10", HEADER_6)
        )
        loader = PowerUsageCsvLoader(
            CONTRACT, tmp_path, "test_pu.two_days", spark=spark, source=DEMO
        )

        df = loader._read_all(loader._resolve_files())
        assert df.columns == [c.name for c in CONTRACT.columns] + [SOURCE_FILE_COL]
        assert "Union" not in df._jdf.queryExecution().analyzed().toString()

        assert loader.load() == 48
        rows = {
            (r.target_date.isoformat(), r.hour_start): r
            for r in spark.table("test_pu.two_days").collect()
        }
        first = rows[("2016-04-01", 0)]
        assert (
            first.demand_mankw,
            first.forecast_mankw,
            first.usage_rate_pct,
            first.supply_capacity_mankw,
        ) == (1200.0, 1190.0, 60.0, None)
        assert first.file_updated_at.isoformat() == "2016-04-02T01:10:00"
        assert first.source_file == "20160401_demo.csv"
        assert rows[("2016-04-02", 23)].supply_capacity_mankw == 2023.0

    def test_file_rows_hook_selects_the_rows_per_file(self, spark, tmp_path):
        class OddHoursOnly(PowerUsageCsvLoader):
            source = DEMO

            def _file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]:
                return [row for row in parsed.rows if row.hour_start % 2]

        write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines())

        assert OddHoursOnly(CONTRACT, tmp_path, "test_pu.hook", spark=spark).load() == 12
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_power_usage.py -q -p no:cacheprovider`
Expected: collection error — `power_market_analytics.power_usage` does not exist.

- [ ] **Step 3: Create `power_market_analytics/power_usage.py`**

```python
"""TSO でんき予報 過去の電力使用実績 (hourly 電力使用状況) — the shared parser and loader.

Every 一般送配電事業者 publishes its でんき予報 demand history as one zip per
month of daily multi-section CSVs (CP932): an ``UPDATE`` stamp, headline
blocks (ピーク時供給力, 予想最大電力, …), the 24-row **hourly** table — the
1時間平均 demand of each hour in 万kW with the TSO's forecast, 使用率 and,
in later layouts, supply capacity — and after it a 5-minute table. TEPCO and
Kansai publish the same four hourly measures in that shape; only the column
names (per TSO and per era, hence a source's ``accepted_headers``), the URLs
and extra packagings such as TEPCO's yearly files differ, and those live in
each TSO's :class:`PowerUsageSource` (``power_market_analytics.tepco.power_usage``,
``power_market_analytics.kansai.power_usage``). Only the hourly table is
ingested; the 5-minute table is parsed past.

Two rules cover the quirks seen in the archives. Every line is read with its
trailing commas removed: files re-saved from Excel pad every line to the
widest row (``,,,,,`` where a blank line belongs, a header ending in a comma)
and without the rule the parser would read past the hourly table. A row
whose first field is ``修正後`` corrects the row above it: its non-blank
measures replace the original's, blank ones keep it (Kansai 2016-04-24 00:00,
1267 → 1212 万kW, ``システム不具合による数値誤りのため修正``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.area_actuals import AreaActualsSource
from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvLoader, CsvTableSchema

__all__ = [
    "CORRECTION_MARKER",
    "HourlyFile",
    "HourlyRow",
    "PowerUsageCsvLoader",
    "PowerUsageSource",
    "parse_hourly",
]

#: First field of a row that corrects the hourly row above it.
CORRECTION_MARKER = "修正後"

_ENCODING = "cp932"
_UPDATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2}) UPDATE$")
_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class PowerUsageSource(AreaActualsSource):
    """Where and how a TSO publishes its でんき予報 hourly archive.

    Everything of :class:`~power_market_analytics.area_actuals.AreaActualsSource`
    applies — the monthly zips go through the shared
    :class:`~power_market_analytics.area_actuals.AreaActualsDownloader` — with
    ``accepted_headers`` naming the hourly table's header line in every
    layout the TSO has used.

    Attributes
    ----------
    multi_day_headers : frozenset of str, default empty
        Accepted headers whose files may hold many delivery dates (TEPCO's
        yearly ``juyo-YYYY.csv``). A file under any other header must hold
        exactly one date: a daily member spanning two dates is corrupt.
    """

    multi_day_headers: frozenset[str] = frozenset()


class HourlyRow(NamedTuple):
    """One hour of the hourly table, values still as published (strings).

    Attributes
    ----------
    target_date : str
        Delivery date as ``yyyyMMdd``.
    hour_start : int
        Hour the value covers, 0–23 (``TIME`` ``h:00`` = hour ``h``–``h+1``).
    demand : str
        ``実績`` / ``当日実績`` in 万kW (1時間平均).
    forecast, usage_rate, supply_capacity : str or None
        The TSO's forecast (万kW), ``使用率(%)`` and supply capacity (万kW);
        None where the layout has no such column (TEPCO's yearly files carry
        the actual only; Kansai adds the capacity on 2019-09-12).
    """

    target_date: str
    hour_start: int
    demand: str
    forecast: str | None
    usage_rate: str | None
    supply_capacity: str | None


class HourlyFile(NamedTuple):
    """The hourly table of one source file plus its metadata.

    Attributes
    ----------
    file_updated_at : str
        The ``UPDATE`` stamp as ``yyyyMMdd HH:mm:ss``.
    header : str
        The accepted column-header line the rows were read under.
    rows : list of HourlyRow
    """

    file_updated_at: str
    header: str
    rows: list[HourlyRow]


def _read_lines(file: Path | str) -> list[str]:
    """The file's lines without their terminators and trailing commas."""
    with open(file, encoding=_ENCODING) as f:
        return [line.rstrip("\r\n").rstrip(",") for line in f]


def _parse_update_stamp(file: Path | str, line: str) -> str:
    match = _UPDATE_RE.match(line)
    if match is None:
        raise ValueError(f"{file}: first line {line!r} is not a '<yyyy/M/d H:mm> UPDATE' stamp")
    year, month, day, hour, minute = match.groups()
    return f"{year}{int(month):02d}{int(day):02d} {int(hour):02d}:{minute}:00"


def _parse_row(file: Path | str, line: str, header: str) -> HourlyRow:
    fields = line.split(",")
    expected = header.count(",") + 1
    if len(fields) != expected:
        raise ValueError(f"{file}: row {line!r} has {len(fields)} fields, expected {expected}")
    date_match = _DATE_RE.match(fields[0])
    if date_match is None:
        raise ValueError(f"{file}: row {line!r} does not start with a yyyy/M/d date")
    year, month, day = date_match.groups()
    time_match = _TIME_RE.match(fields[1])
    if time_match is None or time_match.group(2) != "00" or not 0 <= int(time_match.group(1)) <= 23:
        raise ValueError(f"{file}: row {line!r} is not on the hour (TIME {fields[1]!r})")
    # Every layout starts DATE,TIME,<actual>; the forecast, 使用率 and supply
    # capacity follow where the layout has them.
    extras: list[str | None] = [field.strip() for field in fields[3:]]
    extras += [None] * (3 - len(extras))
    return HourlyRow(
        target_date=f"{year}{int(month):02d}{int(day):02d}",
        hour_start=int(time_match.group(1)),
        demand=fields[2].strip(),
        forecast=extras[0],
        usage_rate=extras[1],
        supply_capacity=extras[2],
    )


def _apply_correction(row: HourlyRow, line: str, header: str) -> HourlyRow:
    """Merge a ``修正後`` row into the hourly row above it.

    The correction row repeats the table's columns — blank ``DATE`` and
    ``TIME``, then a value under each measure that changes — and may carry
    the reason after the header's last column, which is ignored.
    """
    expected = header.count(",") + 1
    fields = [field.strip() for field in line.split(",")[2:expected]]
    fields += [""] * (expected - 2 - len(fields))
    demand, *extras = fields
    extras += [""] * (3 - len(extras))
    return HourlyRow(
        target_date=row.target_date,
        hour_start=row.hour_start,
        demand=demand or row.demand,
        forecast=extras[0] or row.forecast,
        usage_rate=extras[1] or row.usage_rate,
        supply_capacity=extras[2] or row.supply_capacity,
    )


def parse_hourly(file: Path | str, source: PowerUsageSource) -> HourlyFile:
    """Read the hourly table out of one でんき予報 file.

    Every line is read with its trailing commas removed. The first line must
    be the ``UPDATE`` stamp. The hourly table is the block under the first
    line that equals one of ``source.accepted_headers``; it ends at the first
    blank line, so the 5-minute table that follows it is never read. A row
    whose first field is :data:`CORRECTION_MARKER` corrects the row above it.

    Parameters
    ----------
    file : pathlib.Path or str
        Path to a daily member or a multi-day file (CP932).
    source : PowerUsageSource
        Supplies the accepted headers and the multi-day headers.

    Returns
    -------
    HourlyFile

    Raises
    ------
    ValueError
        If the stamp is missing, no accepted header is found, the block is
        empty, a row is malformed (field count after the trailing-comma
        strip, date, or a TIME that is not on the hour), a ``修正後`` row has
        no row above it, a day does not cover hours 0–23 exactly once, or a
        file under a header outside ``source.multi_day_headers`` holds more
        than one target date.
    """
    lines = _read_lines(file)
    if not lines:
        raise ValueError(f"{file}: empty file, expected an UPDATE stamp on the first line")
    file_updated_at = _parse_update_stamp(file, lines[0])
    accepted = source.accepted_headers
    header_index = next((i for i, line in enumerate(lines) if line in accepted), None)
    if header_index is None:
        raise ValueError(
            f"{file}: no accepted hourly header line found — expected one of "
            f"{sorted(accepted)!r} (did the TSO change the layout?)"
        )
    header = lines[header_index]
    rows: list[HourlyRow] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            break
        if line.split(",", 1)[0].strip() == CORRECTION_MARKER:
            if not rows:
                raise ValueError(f"{file}: correction row {line!r} has no hourly row above it")
            rows[-1] = _apply_correction(rows[-1], line, header)
            continue
        rows.append(_parse_row(file, line, header))
    if not rows:
        raise ValueError(f"{file}: no hourly rows under the header {header!r}")
    _check_complete(file, header, rows, source)
    return HourlyFile(file_updated_at=file_updated_at, header=header, rows=rows)


def _check_complete(
    file: Path | str, header: str, rows: list[HourlyRow], source: PowerUsageSource
) -> None:
    """Require every day in the block to carry hours 0–23 exactly once.

    A truncated file that still ends in a well-formed row and a blank line
    would otherwise load as a day with absent hours — a gap the grain
    uniqueness check downstream cannot see. A file whose header is not one
    of the source's multi-day headers must also hold a single target date.
    """
    hours_by_date: dict[str, list[int]] = {}
    for row in rows:
        hours_by_date.setdefault(row.target_date, []).append(row.hour_start)
    if header not in source.multi_day_headers and len(hours_by_date) != 1:
        raise ValueError(
            f"{file}: a daily file must hold exactly one target date, found {sorted(hours_by_date)}"
        )
    for target_date, hours in hours_by_date.items():
        if sorted(hours) != list(range(24)):
            raise ValueError(
                f"{file}: {target_date} does not cover hours 0-23 exactly once "
                f"(got {sorted(hours)}) — truncated or duplicated block?"
            )


#: Contract ``source`` names of the columns the loader hands to the contract
#: (``__``-prefixed: they are emitted by the parser, not read from a header).
TARGET_DATE_SOURCE = "__target_date"
HOUR_START_SOURCE = "__hour_start"
DEMAND_SOURCE = "__demand_mankw"
FORECAST_SOURCE = "__forecast_mankw"
USAGE_RATE_SOURCE = "__usage_rate_pct"
SUPPLY_CAPACITY_SOURCE = "__supply_capacity_mankw"
FILE_UPDATED_AT_SOURCE = "__file_updated_at"
SOURCE_FILE_SOURCE = "__source_file"
_SOURCE_COLUMNS = (
    TARGET_DATE_SOURCE,
    HOUR_START_SOURCE,
    DEMAND_SOURCE,
    FORECAST_SOURCE,
    USAGE_RATE_SOURCE,
    SUPPLY_CAPACITY_SOURCE,
    FILE_UPDATED_AT_SOURCE,
    SOURCE_FILE_SOURCE,
)


class PowerUsageCsvLoader(CsvLoader):
    """Full reload of でんき予報 hourly tables into a warehouse table.

    Works like :class:`~power_market_analytics.csv_loader.CsvLoader` (same
    validation and write behaviour) except for how each file is read: the
    files are multi-section, so :func:`parse_hourly` extracts the hourly
    table in Python and the contract addresses the parsed values by the
    ``__``-prefixed source names above. Every file's rows land in one
    ``createDataFrame`` (~1,600 daily files as separate frames unioned
    together gave Spark 16k tasks per action). A subclass may drop rows per
    file through :meth:`_file_rows` (TEPCO drops the yearly rows its daily
    files cover).

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`CsvLoader`; ``filepath`` is the ``csv/`` folder of the
        extracted daily files.
    source : PowerUsageSource, optional
        The TSO spec (accepted and multi-day headers). Subclasses may fix it
        via the ``source`` class attribute instead.
    """

    #: Default source for subclasses (e.g. ``KansaiPowerUsageCsvLoader.source``).
    source: PowerUsageSource | None = None

    def __init__(
        self,
        schema: CsvTableSchema,
        filepath: Path | str,
        table: str,
        spark: SparkSession | None = None,
        source: PowerUsageSource | None = None,
    ) -> None:
        resolved = source if source is not None else type(self).source
        if resolved is None:
            raise ValueError("PowerUsageCsvLoader needs a source (argument or class attribute)")
        self._source = resolved
        super().__init__(schema=schema, filepath=filepath, table=table, spark=spark)

    def _read_all(self, files: list[str]) -> DataFrame:
        return self._frame(self._rows(files))

    def _file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]:
        """The rows of one parsed file to load — all of them unless a subclass says otherwise.

        Parameters
        ----------
        file : str
            Path of the parsed file (for log messages).
        parsed : HourlyFile
            Its hourly table.

        Returns
        -------
        list of HourlyRow
        """
        return parsed.rows

    def _rows(self, files: list[str]) -> list[tuple[str | None, ...]]:
        """Parse the hourly tables of ``files`` into contract-source string tuples."""
        data: list[tuple[str | None, ...]] = []
        for file in files:
            parsed = parse_hourly(file, self._source)
            source_file = Path(file).name
            data.extend(
                (
                    row.target_date,
                    str(row.hour_start),
                    row.demand,
                    row.forecast,
                    row.usage_rate,
                    row.supply_capacity,
                    parsed.file_updated_at,
                    source_file,
                )
                for row in self._file_rows(file, parsed)
            )
        return data

    def _frame(self, data: list[tuple[str | None, ...]]) -> DataFrame:
        """Build the contract-typed DataFrame from parsed string tuples.

        The parsed file name doubles as the hidden ``SOURCE_FILE_COL`` so
        that validation failures name the offending files, as for the
        Spark-scanned loaders.
        """
        spark_schema = StructType([StructField(name, StringType()) for name in _SOURCE_COLUMNS])
        raw = self.spark.createDataFrame(data, spark_schema)
        return self._project(raw.withColumn(SOURCE_FILE_COL, F.col(SOURCE_FILE_SOURCE)))
```

- [ ] **Step 4: Shrink `power_market_analytics/tepco/power_usage.py`**

Replace the whole file with:

```python
"""TEPCO でんき予報 過去の電力使用実績 (Tokyo-area hourly 電力使用状況) archive spec.

TEPCO Power Grid publishes the でんき予報 demand history on
https://www.tepco.co.jp/forecast/html/download-j.html in two packagings:

* **2016-04-01 → 2022-03-31**: one CP932 CSV per calendar year
  (``juyo-YYYY.csv``, 2016 … 2022) holding only the hourly actual
  ``DATE,TIME,実績(万kW)`` — the 1時間平均 demand of each hour, in 万kW.
* **2022-04-01 → today**: one zip per month
  (``YYYYMM_power_usage.zip``) of daily multi-section CSVs
  (``YYYYMMDD_power_usage.csv``): an ``UPDATE`` stamp, headline blocks
  (ピーク時供給力, 予想最大電力, …), the 24-row **hourly** table
  ``DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)`` and, after it,
  the 288-row **5-minute** table (``当日実績(５分間隔値)``, 太陽光 columns).

Only the hourly table is ingested; the 5-minute table is a separate 速報
measurement and is skipped. The yearly 2022 file also carries April–December
2022, which the daily files cover too, so yearly rows on or after
:data:`DAILY_FILES_FROM` are dropped at load time and the daily files win.
The parser and loader are the shared :mod:`power_market_analytics.power_usage`;
this module holds what is TEPCO's — the source spec, the yearly files and the
yearly-row drop. Format, quirks and the comparison against the A-1 series
(``tepco_area_demand_generation_actual``) are documented in
docs/TEPCO-Power-Usage-Retrieval.md.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import requests
from loguru import logger

from power_market_analytics.area_actuals import AreaActualsDownloader, AreaActualsDownloadError
from power_market_analytics.power_usage import (
    HourlyFile,
    HourlyRow,
    PowerUsageCsvLoader,
    PowerUsageSource,
)
from power_market_analytics.power_usage import parse_hourly as _parse_hourly

__all__ = [
    "DAILY_FILES_FROM",
    "DAILY_HOURLY_HEADER",
    "TEPCO_POWER_USAGE",
    "YEARLY_HEADER",
    "YEARLY_URL_TEMPLATE",
    "YEARLY_YEARS",
    "HourlyFile",
    "HourlyRow",
    "TepcoPowerUsageCsvLoader",
    "TepcoPowerUsageDownloader",
    "parse_hourly",
]

#: Column-header line of the hourly table in the daily files (2022-04 →).
DAILY_HOURLY_HEADER = "DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)"
#: Column-header line of the yearly files (2016 … 2022).
YEARLY_HEADER = "DATE,TIME,実績(万kW)"

#: First delivery day covered by the daily files; yearly rows from this day
#: on are dropped so the two packagings never overlap in the warehouse.
DAILY_FILES_FROM = datetime.date(2022, 4, 1)
#: ``str.format`` template of a yearly file's URL (``{year}``).
YEARLY_URL_TEMPLATE = "https://www.tepco.co.jp/forecast/html/images/juyo-{year}.csv"
#: Calendar years published as yearly files (the 2022 file runs to December).
YEARLY_YEARS = range(2016, 2023)
#: First day of the published history (the 2016 file starts here, not on Jan 1).
HISTORY_START = datetime.date(2016, 4, 1)

_ENCODING = "cp932"


def expected_yearly_dates(year: int) -> list[str]:
    ...unchanged...


TEPCO_POWER_USAGE = PowerUsageSource(
    code="tepco_power_usage",
    url_template="https://www.tepco.co.jp/forecast/html/images/{year:04d}{month:02d}_power_usage.zip",
    #: First monthly archive; the daily files start with it.
    earliest_month=(2022, 4),
    #: One member per day; members are flat (the day's 5-minute rows live in
    #: the same file, below the hourly table).
    member_re=re.compile(r"\d{8}_power_usage\.csv$"),
    accepted_headers=frozenset({DAILY_HOURLY_HEADER, YEARLY_HEADER}),
    default_data_dir="data/tepco/power_usage",
    #: A yearly file holds a whole calendar year; a daily member one date.
    multi_day_headers=frozenset({YEARLY_HEADER}),
)


def parse_hourly(file: Path | str) -> HourlyFile:
    """Read the hourly table out of a yearly or daily TEPCO file.

    :func:`power_market_analytics.power_usage.parse_hourly` bound to
    :data:`TEPCO_POWER_USAGE`.

    Parameters
    ----------
    file : pathlib.Path or str
        Path to a ``juyo-YYYY.csv`` or ``YYYYMMDD_power_usage.csv`` (CP932).

    Returns
    -------
    HourlyFile

    Raises
    ------
    ValueError
        As the shared parser: a missing stamp, no accepted header, a
        malformed row, an incomplete day, or a daily file holding more than
        one target date.
    """
    return _parse_hourly(file, TEPCO_POWER_USAGE)


class TepcoPowerUsageDownloader(AreaActualsDownloader):
    ...unchanged (uses parse_hourly, _ENCODING, YEARLY_HEADER as today)...


class TepcoPowerUsageCsvLoader(PowerUsageCsvLoader):
    """Full reload of the TEPCO でんき予報 hourly tables into a warehouse table.

    The shared :class:`~power_market_analytics.power_usage.PowerUsageCsvLoader`
    bound to :data:`TEPCO_POWER_USAGE`, dropping yearly rows on or after
    :data:`DAILY_FILES_FROM` — those days come from the daily files — so the
    two packagings never collide on the grain.

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`~power_market_analytics.csv_loader.CsvLoader`;
        ``filepath`` is the ``csv/`` folder holding both ``juyo-YYYY.csv``
        and ``YYYYMMDD_power_usage.csv``.
    """

    source = TEPCO_POWER_USAGE

    def _file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]:
        if parsed.header != YEARLY_HEADER:
            return parsed.rows
        cutoff = DAILY_FILES_FROM.strftime("%Y%m%d")
        kept = [row for row in parsed.rows if row.target_date < cutoff]
        if len(kept) != len(parsed.rows):
            logger.info(
                "{}: dropped {} hourly row(s) on/after {} (covered by the daily files)",
                file,
                len(parsed.rows) - len(kept),
                DAILY_FILES_FROM,
            )
        return kept
```

(`expected_yearly_dates` and `TepcoPowerUsageDownloader` are kept verbatim from the current file; the deleted parts are `HourlyRow`, `HourlyFile`, the regexes, `_parse_update_stamp`, `_parse_row`, `_check_complete`, the old `parse_hourly` body, the `__` constants and the old loader body.)

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_power_usage.py tests/test_tepco_power_usage.py tests/test_load_scripts.py tests/test_download_scripts.py -q -p no:cacheprovider`
Expected: all pass (TEPCO's 60-odd tests unchanged).

- [ ] **Step 6: Add two TEPCO binding tests** to `tests/test_tepco_power_usage.py` (`TestTepcoPowerUsageSource`; import `PowerUsageSource` from `power_market_analytics.power_usage`):

```python
    def test_is_a_power_usage_source_whose_yearly_files_span_many_days(self):
        assert isinstance(TEPCO_POWER_USAGE, PowerUsageSource)
        assert TEPCO_POWER_USAGE.multi_day_headers == frozenset({YEARLY_HEADER})
        assert TEPCO_POWER_USAGE.known_missing_days == frozenset()

    def test_loader_is_bound_to_the_source(self):
        assert TepcoPowerUsageCsvLoader.source is TEPCO_POWER_USAGE
```

Run the same command; expected: pass.

- [ ] **Step 7: Commit**

```bash
git add power_market_analytics/power_usage.py power_market_analytics/tepco/power_usage.py tests/test_power_usage.py tests/test_tepco_power_usage.py
git commit -m "refactor(power-usage): shared でんき予報 parser and loader with the trailing-comma and 修正後 rules" -m "TEPCO keeps its spec, the yearly files and the yearly-row drop (a _file_rows hook). Lines are read with trailing commas removed and a 修正後 row corrects the row above it, which the Kansai archive needs; TEPCO's files carry neither." -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `kansai/` package

**Files:**
- Move: `power_market_analytics/kansai.py` → `power_market_analytics/kansai/area_demand_generation.py`
- Create: `power_market_analytics/kansai/__init__.py`
- Test: `tests/test_kansai.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_kansai.py` (add `from power_market_analytics.kansai import area_demand_generation` to the imports):

```python
class TestKansaiPackage:
    def test_package_re_exports_the_area_actuals_names(self):
        assert area_demand_generation.KANSAI is KANSAI
        assert area_demand_generation.KansaiAreaDownloader is KansaiAreaDownloader
        assert area_demand_generation.KansaiAreaCsvLoader is KansaiAreaCsvLoader
```

Run: `uv run pytest tests/test_kansai.py -q -p no:cacheprovider` — expected: ImportError.

- [ ] **Step 2: Move and re-export**

```bash
mkdir -p power_market_analytics/kansai
git mv power_market_analytics/kansai.py power_market_analytics/kansai/area_demand_generation.py
```

`power_market_analytics/kansai/__init__.py`:

```python
"""関西電力送配電 (Kansai Transmission and Distribution) public datasets, one module per dataset.

* :mod:`~power_market_analytics.kansai.area_demand_generation` — エリア需給・発電（実績）
  (30-minute A-1 / B-1 / B-4 actuals, 2022-04 →).
* :mod:`~power_market_analytics.kansai.power_usage` — でんき予報 過去の電力使用実績
  (hourly 電力使用状況, 2016-04 →).

The names of the first dataset are re-exported here so
``from power_market_analytics.kansai import KANSAI, KansaiAreaDownloader`` keeps
working; the shared download/load machinery lives in
:mod:`power_market_analytics.area_actuals` and :mod:`power_market_analytics.power_usage`.
"""

from power_market_analytics.kansai.area_demand_generation import (
    KANSAI,
    KansaiAreaCsvLoader,
    KansaiAreaDownloader,
)

__all__ = ["KANSAI", "KansaiAreaCsvLoader", "KansaiAreaDownloader"]
```

In `area_demand_generation.py` change the docstring's "this module only supplies the Kansai …" sentence to name the module and update `docs/Kansai-Area-Demand-Generation-Retrieval.md` §6 (line 117) to `power_market_analytics/kansai/area_demand_generation.py` (line 5 and the heading of §6 keep `power_market_analytics.kansai`, still the import path).

- [ ] **Step 3: Run** `uv run pytest tests/test_kansai.py tests/test_kansai_scripts.py tests/test_area_actuals.py -q -p no:cacheprovider` — expected: pass.

- [ ] **Step 4: Commit**

```bash
git add power_market_analytics/kansai tests/test_kansai.py docs/Kansai-Area-Demand-Generation-Retrieval.md
git commit -m "refactor(kansai): kansai becomes a package with one module per dataset" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `kansai/power_usage.py`, contract, scripts, justfile

**Files:**
- Create: `power_market_analytics/kansai/power_usage.py`, `conf/schemas/kansai_power_usage_hourly.yaml`, `scripts/download_kansai_power_usage.py`, `scripts/load_kansai_power_usage.py`
- Modify: `justfile` (`refresh-all`)
- Test: `tests/test_kansai_power_usage.py` (create), `tests/test_download_scripts.py`, `tests/test_load_scripts.py`

**Interfaces:**
- Produces: `HOURLY_HEADER_2016/2019/2025`, `SUPPLY_CAPACITY_FROM = date(2019, 9, 12)`, `MISSING_DAY = date(2024, 3, 31)`, `KANSAI_POWER_USAGE: PowerUsageSource` (code `kansai_power_usage`, `known_missing_days={MISSING_DAY}`), `KansaiPowerUsageDownloader(data_dir=None, timeout=60.0, session=None)`, `KansaiPowerUsageCsvLoader` (`source = KANSAI_POWER_USAGE`).

- [ ] **Step 1: Write the failing tests** — `tests/test_kansai_power_usage.py`:

```python
"""関西電力送配電 でんき予報 hourly 電力使用実績: spec, downloader, contract and loader."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from power_market_analytics.area_actuals import AreaActualsDownloader, AreaActualsDownloadError
from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.kansai.power_usage import (
    HOURLY_HEADER_2016,
    HOURLY_HEADER_2019,
    HOURLY_HEADER_2025,
    KANSAI_POWER_USAGE,
    MISSING_DAY,
    SUPPLY_CAPACITY_FROM,
    KansaiPowerUsageCsvLoader,
    KansaiPowerUsageDownloader,
)
from power_market_analytics.power_usage import (
    CORRECTION_MARKER,
    PowerUsageCsvLoader,
    PowerUsageSource,
)
from tests.support import REPO_ROOT
from tests.test_area_actuals import FakeResponse, FakeSession, make_zip
from tests.test_power_usage import daily_lines, hourly_rows, padded, write_lines_cp932

CONTRACT_PATH = REPO_ROOT / "conf/schemas/kansai_power_usage_hourly.yaml"


def member(date: str, updated: str, header: str = HOURLY_HEADER_2019) -> bytes:
    return ("\r\n".join(daily_lines(date, updated, header)) + "\r\n").encode("cp932")


class TestKansaiPowerUsageSource:
    def test_is_a_power_usage_source_for_the_yamasou_archive(self):
        assert isinstance(KANSAI_POWER_USAGE, PowerUsageSource)
        assert KANSAI_POWER_USAGE.code == "kansai_power_usage"
        assert (
            KANSAI_POWER_USAGE.zip_url(2016, 4)
            == "https://www.kansai-td.co.jp/yamasou/201604_jisseki.zip"
        )
        assert KANSAI_POWER_USAGE.zip_name(2026, 9) == "202609_jisseki.zip"
        assert KANSAI_POWER_USAGE.earliest_month == (2016, 4)
        assert KANSAI_POWER_USAGE.default_data_dir == "data/kansai/power_usage"

    def test_matches_both_member_naming_generations(self):
        assert KANSAI_POWER_USAGE.is_actuals_member("20160401_juyo1_kansai.csv")  # through 2025-11
        assert KANSAI_POWER_USAGE.is_actuals_member("nested/20251130_juyo1_kansai.csv")
        assert KANSAI_POWER_USAGE.is_actuals_member("juyo_06_20251201.csv")  # from 2025-12
        # The A-1 feed's members (its archives share the name YYYYMM_jisseki.zip).
        assert not KANSAI_POWER_USAGE.is_actuals_member("20250701_jisseki.csv")
        assert not KANSAI_POWER_USAGE.is_actuals_member("jukyu_jisseki_20251225_06.csv")

    def test_accepts_the_three_hourly_layouts(self):
        assert HOURLY_HEADER_2016 == "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"
        assert HOURLY_HEADER_2019 == HOURLY_HEADER_2016 + ",供給力想定値(万kW)"
        assert HOURLY_HEADER_2025 == HOURLY_HEADER_2016 + ",供給力(万kW)"
        assert KANSAI_POWER_USAGE.accepted_headers == frozenset(
            {HOURLY_HEADER_2016, HOURLY_HEADER_2019, HOURLY_HEADER_2025}
        )
        assert SUPPLY_CAPACITY_FROM == datetime.date(2019, 9, 12)

    def test_every_file_holds_one_date_and_archives_hold_finished_days_only(self):
        assert KANSAI_POWER_USAGE.multi_day_headers == frozenset()
        assert KANSAI_POWER_USAGE.archive_includes_current_day is False

    def test_the_one_known_missing_day(self):
        assert MISSING_DAY == datetime.date(2024, 3, 31)
        assert KANSAI_POWER_USAGE.known_missing_days == frozenset({MISSING_DAY})


class TestKansaiPowerUsageDownloader:
    def test_is_bound_to_the_source_and_its_data_dir(self, tmp_path):
        dl = KansaiPowerUsageDownloader()
        assert isinstance(dl, AreaActualsDownloader)
        assert dl.source is KANSAI_POWER_USAGE
        assert dl.data_dir == Path("data/kansai/power_usage")
        assert KansaiPowerUsageDownloader(data_dir=tmp_path).csv_dir == tmp_path / "csv"

    def test_download_extracts_members_of_either_generation(self, tmp_path):
        archive = make_zip(
            {
                "juyo_06_20251201.csv": member("2025/12/1", "2025/12/2 1:10"),
                "20251202_juyo1_kansai.csv": member("2025/12/2", "2025/12/3 1:10"),
                "readme.txt": b"skip",
            }
        )
        dl = KansaiPowerUsageDownloader(
            data_dir=tmp_path, timeout=9.0, session=FakeSession(FakeResponse(archive))
        )

        extracted = dl.download(2025, 12, today=datetime.date(2025, 12, 3))

        assert extracted == [
            tmp_path / "csv" / "20251202_juyo1_kansai.csv",
            tmp_path / "csv" / "juyo_06_20251201.csv",
        ]
        assert not (tmp_path / "csv" / "readme.txt").exists()

    def test_settled_march_2024_passes_without_the_31st(self, tmp_path):
        members = {
            f"202403{d:02d}_juyo1_kansai.csv": member(f"2024/3/{d}", f"2024/3/{d + 1} 1:10")
            for d in range(1, 31)
        }
        dl = KansaiPowerUsageDownloader(
            data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )

        assert len(dl.download(2024, 3, today=datetime.date(2024, 5, 1))) == 30

    def test_any_other_missing_day_in_a_settled_month_fails(self, tmp_path):
        members = {
            f"202403{d:02d}_juyo1_kansai.csv": member(f"2024/3/{d}", f"2024/3/{d + 1} 1:10")
            for d in range(1, 30)
        }
        dl = KansaiPowerUsageDownloader(
            data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )

        with pytest.raises(AreaActualsDownloadError, match="20240330"):
            dl.download(2024, 3, today=datetime.date(2024, 5, 1))

    def test_download_all_starts_at_2016_04(self, tmp_path):
        dl = KansaiPowerUsageDownloader(data_dir=tmp_path)
        calls: list[tuple[int, int]] = []
        dl.download = lambda year, month, today: calls.append((year, month)) or []  # type: ignore[method-assign]

        assert dl.download_all(today=datetime.date(2016, 6, 15)) == []
        assert calls == [(2016, 4), (2016, 5), (2016, 6)]


class TestKansaiContract:
    def test_grain_columns_and_nullability(self):
        schema = CsvTableSchema.from_yaml(CONTRACT_PATH)

        assert schema.grain == ["target_date", "hour_start"]
        assert [(c.name, c.source) for c in schema.columns] == [
            ("target_date", "__target_date"),
            ("hour_start", "__hour_start"),
            ("demand_mankw", "__demand_mankw"),
            ("forecast_mankw", "__forecast_mankw"),
            ("usage_rate_pct", "__usage_rate_pct"),
            ("supply_capacity_mankw", "__supply_capacity_mankw"),
            ("file_updated_at", "__file_updated_at"),
            ("source_file", "__source_file"),
        ]
        assert {c.name for c in schema.columns if not c.nullable} == {
            "target_date",
            "hour_start",
            "demand_mankw",
            "file_updated_at",
            "source_file",
        }

    def test_matches_the_tepco_contract_column_for_column(self):
        tepco = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/tepco_power_usage_hourly.yaml")
        kansai = CsvTableSchema.from_yaml(CONTRACT_PATH)
        assert kansai.grain == tepco.grain
        assert [c.model_dump() for c in kansai.columns] == [c.model_dump() for c in tepco.columns]


class TestKansaiPowerUsageCsvLoader:
    def test_is_bound_to_the_source(self):
        assert issubclass(KansaiPowerUsageCsvLoader, PowerUsageCsvLoader)
        assert KansaiPowerUsageCsvLoader.source is KANSAI_POWER_USAGE

    def test_loads_every_layout_and_the_padded_corrected_file(self, spark, tmp_path):
        write_lines_cp932(
            tmp_path / "20160401_juyo1_kansai.csv",
            daily_lines("2016/4/1", "2016/4/2 1:10", HOURLY_HEADER_2016),
        )
        corrected = hourly_rows("2016/4/24", HOURLY_HEADER_2016, base=1267)
        corrected.insert(
            1, f"{CORRECTION_MARKER},,1212,,65,4/25 システム不具合による数値誤りのため修正"
        )
        write_lines_cp932(
            tmp_path / "20160424_juyo1_kansai.csv",
            padded(daily_lines("2016/4/24", "2016/4/25 1:10", HOURLY_HEADER_2016, corrected)),
        )
        write_lines_cp932(
            tmp_path / "20190912_juyo1_kansai.csv",
            daily_lines("2019/9/12", "2019/9/13 1:10", HOURLY_HEADER_2019),
        )
        write_lines_cp932(
            tmp_path / "juyo_06_20251225.csv",
            daily_lines("2025/12/25", "2025/12/26 1:10", HOURLY_HEADER_2025),
        )
        loader = KansaiPowerUsageCsvLoader(
            CsvTableSchema.from_yaml(CONTRACT_PATH), tmp_path, "test_kansai.power_usage", spark=spark
        )

        assert loader.load() == 96

        table = spark.table("test_kansai.power_usage")
        assert dict(table.dtypes) == {
            "target_date": "date",
            "hour_start": "int",
            "demand_mankw": "double",
            "forecast_mankw": "double",
            "usage_rate_pct": "double",
            "supply_capacity_mankw": "double",
            "file_updated_at": "timestamp",
            "source_file": "string",
        }
        rows = {(r.target_date.isoformat(), r.hour_start): r for r in table.collect()}
        first = rows[("2016-04-01", 0)]
        assert (
            first.demand_mankw,
            first.forecast_mankw,
            first.usage_rate_pct,
            first.supply_capacity_mankw,
        ) == (1200.0, 1190.0, 60.0, None)
        assert first.file_updated_at.isoformat() == "2016-04-02T01:10:00"
        assert first.source_file == "20160401_juyo1_kansai.csv"
        fixed = rows[("2016-04-24", 0)]
        assert (fixed.demand_mankw, fixed.forecast_mankw, fixed.usage_rate_pct) == (1212.0, 1257.0, 65.0)
        assert rows[("2016-04-24", 1)].demand_mankw == 1268.0
        assert rows[("2019-09-12", 0)].supply_capacity_mankw == 2000.0
        latest = rows[("2025-12-25", 23)]
        assert latest.supply_capacity_mankw == 2023.0
        assert latest.source_file == "juyo_06_20251225.csv"
```

Add to `tests/test_download_scripts.py` after `TestDownloadTepcoPowerUsage`:

```python
class TestDownloadKansaiPowerUsage:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("download_kansai_power_usage")
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir
                self.csv_dir = Path(data_dir) / "csv"

            def download_all(self):
                seen["download_all"] = True
                return [self.csv_dir / "20160401_juyo1_kansai.csv"]

        monkeypatch.setattr(module, "KansaiPowerUsageDownloader", FakeDownloader)
        return module, seen

    def test_default_data_dir(self, fake):
        module, seen = fake
        module.main([])
        assert seen == {"data_dir": Path("data/kansai/power_usage"), "download_all": True}

    def test_data_dir_override(self, fake, tmp_path):
        module, seen = fake
        module.main(["--data-dir", str(tmp_path)])
        assert seen == {"data_dir": tmp_path, "download_all": True}
```

Add to `tests/test_load_scripts.py` `GENERIC_SCRIPTS`:

```python
    (
        "load_kansai_power_usage",
        "KansaiPowerUsageCsvLoader",
        "conf/schemas/kansai_power_usage_hourly.yaml",
        "data/kansai/power_usage/csv",
        "pma_raw.kansai_power_usage_hourly",
    ),
```

and to `CONTRACT_GRAINS`: `"conf/schemas/kansai_power_usage_hourly.yaml": ["target_date", "hour_start"],`.

Run: `uv run pytest tests/test_kansai_power_usage.py tests/test_download_scripts.py tests/test_load_scripts.py -q -p no:cacheprovider` — expected: import / file-not-found failures.

- [ ] **Step 2: `power_market_analytics/kansai/power_usage.py`**

```python
"""関西電力送配電 でんき予報 過去の電力使用実績 (Kansai-area hourly 電力使用状況) archive spec.

Kansai Transmission and Distribution publishes its でんき予報 demand history on
https://www.kansai-td.co.jp/denkiyoho/download/ as one zip per month,
``https://www.kansai-td.co.jp/yamasou/YYYYMM_jisseki.zip`` (2016-04 → the
current month, 57–78 KB each, ~8.5 MB in all, listed in
``…/yamasou/jisseki.json``), of daily multi-section CSVs —
``YYYYMMDD_juyo1_kansai.csv`` through 2025-11, ``juyo_06_YYYYMMDD.csv`` from
2025-12 — whose hourly table carries ``当日実績(万kW)``, ``予想値(万kW)``,
``使用率(%)`` and, from 2019-09-12, ``供給力想定値(万kW)`` (``供給力(万kW)``
from 2025-12-25). The archive holds finished days only (day D appears on D+1
at 01:10); 2024-03-31 was never published (site maintenance). Format, quirks
and the comparison against the A-1 series: docs/Kansai-Power-Usage-Retrieval.md.

The parser and loader are the shared :mod:`power_market_analytics.power_usage`;
this module only supplies the Kansai
:class:`~power_market_analytics.power_usage.PowerUsageSource` and convenience
subclasses bound to it.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import requests

from power_market_analytics.area_actuals import AreaActualsDownloader
from power_market_analytics.power_usage import PowerUsageCsvLoader, PowerUsageSource

__all__ = [
    "HOURLY_HEADER_2016",
    "HOURLY_HEADER_2019",
    "HOURLY_HEADER_2025",
    "KANSAI_POWER_USAGE",
    "MISSING_DAY",
    "SUPPLY_CAPACITY_FROM",
    "KansaiPowerUsageCsvLoader",
    "KansaiPowerUsageDownloader",
]

#: Hourly-table header 2016-04-01 → 2019-09-11: no supply-capacity column.
HOURLY_HEADER_2016 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"
#: 2019-09-12 → 2025-12-24: + 供給力想定値.
HOURLY_HEADER_2019 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力想定値(万kW)"
#: 2025-12-25 onward: 供給力 instead (the month the A-1 feed also renamed its members).
HOURLY_HEADER_2025 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力(万kW)"
#: First delivery day with a supply-capacity column (std pins the null pattern to it).
SUPPLY_CAPACITY_FROM = datetime.date(2019, 9, 12)
#: The one day Kansai never published (site maintenance that day, per the page).
MISSING_DAY = datetime.date(2024, 3, 31)

KANSAI_POWER_USAGE = PowerUsageSource(
    code="kansai_power_usage",
    url_template="https://www.kansai-td.co.jp/yamasou/{year:04d}{month:02d}_jisseki.zip",
    earliest_month=(2016, 4),
    #: Daily members in either naming generation; members are flat.
    member_re=re.compile(r"(^|/)(\d{8}_juyo1_kansai|juyo_06_\d{8})\.csv$"),
    accepted_headers=frozenset({HOURLY_HEADER_2016, HOURLY_HEADER_2019, HOURLY_HEADER_2025}),
    default_data_dir="data/kansai/power_usage",
    known_missing_days=frozenset({MISSING_DAY}),
)


class KansaiPowerUsageDownloader(AreaActualsDownloader):
    """Download the monthly Kansai でんき予報 archives and extract the daily members.

    Every call re-downloads the requested month: Kansai revises past days
    without notice (需要実績は、過去にさかのぼり修正させていただく場合があります)
    and the current month's zip grows daily; the whole history is ~8.5 MB.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/kansai/power_usage"``
        Root directory: ``zip/`` for the monthly archives, ``csv/`` for the
        extracted daily files.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to use; defaults to a fresh one.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(KANSAI_POWER_USAGE, data_dir=data_dir, timeout=timeout, session=session)


class KansaiPowerUsageCsvLoader(PowerUsageCsvLoader):
    """Full reload of the Kansai でんき予報 hourly tables into a warehouse table.

    The shared :class:`~power_market_analytics.power_usage.PowerUsageCsvLoader`
    bound to :data:`KANSAI_POWER_USAGE`: every file holds one date and all
    rows are kept (contract ``conf/schemas/kansai_power_usage_hourly.yaml``).

    Same constructor as :class:`~power_market_analytics.csv_loader.CsvLoader`
    (``schema``, ``filepath``, ``table``, optional ``spark``).
    """

    source = KANSAI_POWER_USAGE
```

- [ ] **Step 3: Contract `conf/schemas/kansai_power_usage_hourly.yaml`**

```yaml
description: >
  関西電力送配電 でんき予報 過去の電力使用実績 — Kansai-area hourly 電力使用状況:
  the 1時間平均 demand of each hour in 万kW (1 万kW = 10 MW), as published on
  https://www.kansai-td.co.jp/denkiyoho/download/. Source files: the daily
  members (YYYYMMDD_juyo1_kansai.csv through 2025-11, juyo_06_YYYYMMDD.csv
  from 2025-12) of the monthly https://www.kansai-td.co.jp/yamasou/YYYYMM_jisseki.zip
  archives, 2016-04 onward. Three hourly layouts feed one table:
  DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%) (2016-04-01 → 2019-09-11),
  + 供給力想定値(万kW) (2019-09-12 → 2025-12-24), + 供給力(万kW) instead
  (2025-12-25 →); the warehouse keeps TEPCO's column names (forecast_mankw =
  予想値, the day's last intraday forecast; supply_capacity_mankw = 供給力想定値
  / 供給力, null before 2019-09-12). Files are pre-parsed in Python by
  KansaiPowerUsageCsvLoader (the shared PowerUsageCsvLoader: multi-section
  layout, trailing commas stripped — 60 files were re-saved from Excel — and
  the one 修正後 correction row, 2016-04-24 00:00, applied); contract sources
  are the __-prefixed names it emits. file_updated_at is the file's UPDATE
  stamp (01:10 on target_date + 1). The daily files also hold a 5-minute
  table; it is not loaded. Kansai never published 2024-03-31 (site
  maintenance). Format, quirks and the comparison against the A-1 series:
  docs/Kansai-Power-Usage-Retrieval.md.

grain: [target_date, hour_start]

columns:
  - { name: target_date, source: __target_date, type: date, format: yyyyMMdd, nullable: false }
  - { name: hour_start, source: __hour_start, type: int, nullable: false }
  - { name: demand_mankw, source: __demand_mankw, type: double, nullable: false }
  - { name: forecast_mankw, source: __forecast_mankw, type: double }
  - { name: usage_rate_pct, source: __usage_rate_pct, type: double }
  - { name: supply_capacity_mankw, source: __supply_capacity_mankw, type: double }
  - { name: file_updated_at, source: __file_updated_at, type: timestamp, format: yyyyMMdd HH:mm:ss, nullable: false }
  - { name: source_file, source: __source_file, type: string, nullable: false }
```

- [ ] **Step 4: Scripts**

`scripts/download_kansai_power_usage.py`:

```python
"""Download the 関西電力送配電 でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

Always re-downloads every monthly ``YYYYMM_jisseki.zip`` from 2016-04 to the
current month (~126 zips, ~8.5 MB) — Kansai revises past days without notice
— and extracts the daily members (``YYYYMMDD_juyo1_kansai.csv`` through
2025-11, ``juyo_06_YYYYMMDD.csv`` from 2025-12) under ``csv/``. A settled
month must hold every day except 2024-03-31, which Kansai never published.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.kansai.power_usage import KansaiPowerUsageDownloader


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ extracted daily files).",
    )
    args = parser.parse_args(argv)

    downloader = KansaiPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} daily file(s) into {}", len(paths), downloader.csv_dir)


if __name__ == "__main__":
    main()
```

`scripts/load_kansai_power_usage.py`:

```python
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
```

`justfile` `refresh-all`, after the Kansai A-1 pair:

```
    just python scripts/download_kansai_power_usage.py
    just python scripts/load_kansai_power_usage.py
```

and the recipe's `[doc(...)]`: "TEPCO, Kansai" → "TEPCO (both datasets), Kansai (both datasets)".

- [ ] **Step 5: Run** `uv run pytest tests/test_kansai_power_usage.py tests/test_download_scripts.py tests/test_load_scripts.py tests/test_power_usage.py -q -p no:cacheprovider` — expected: pass.

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/kansai/power_usage.py conf/schemas/kansai_power_usage_hourly.yaml scripts/download_kansai_power_usage.py scripts/load_kansai_power_usage.py justfile tests/test_kansai_power_usage.py tests/test_download_scripts.py tests/test_load_scripts.py
git commit -m "feat(kansai): でんき予報 hourly 電力使用実績 spec, downloader, loader, contract and scripts" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: dbt — raw source, stg, std, singular test, fact branch

**Files:**
- Modify: `dbt/models/raw/kansai.yml`, `dbt/models/curated/fct_area_power_usage_hourly.sql`, `.yml`
- Create: `dbt/models/staging/stg_kansai__power_usage_hourly.sql`, `.yml`, `dbt/models/standardized/std_kansai__power_usage_hourly.sql`, `.yml`, `dbt/dbt_tests/assert_std_kansai__power_usage_hourly_calendar_complete.sql`

- [ ] **Step 1: `dbt/models/raw/kansai.yml`** — source description becomes:

```yaml
    description: >
      関西電力送配電 (Kansai Transmission and Distribution) public datasets for the
      Kansai service area, one table per dataset: the エリア需給・発電（実績）
      30-minute actuals — the インバランス料金関連 「系統の需給に関する情報」
      disclosure (items A-1 / B-1 / B-4) — downloaded from the monthly archives
      linked on https://www.kansai-td.co.jp/denkiyoho/imbalance/past.html by
      scripts/download_kansai_area_demand_generation.py and loaded by
      scripts/load_kansai_area_demand_generation.py (full reload; archive
      layout, both CSV layouts and quirks in
      docs/Kansai-Area-Demand-Generation-Retrieval.md), and the でんき予報
      hourly 電力使用実績 from https://www.kansai-td.co.jp/denkiyoho/download/
      (scripts/download_kansai_power_usage.py / load_kansai_power_usage.py;
      docs/Kansai-Power-Usage-Retrieval.md).
```

and a second table:

```yaml
      - name: kansai_power_usage_hourly
        description: >
          でんき予報 過去の電力使用実績: Kansai-area hourly 電力使用状況 — the
          1時間平均 demand of each hour in 万kW (1 万kW = 10 MW) — from the
          hourly table of the daily members of the monthly YYYYMM_jisseki.zip
          archives under https://www.kansai-td.co.jp/yamasou/ (downloaded by
          scripts/download_kansai_power_usage.py, loaded by
          scripts/load_kansai_power_usage.py, full reload). Three hourly
          layouts: 当日実績 / 予想値 / 使用率 from 2016-04-01, + 供給力想定値 from
          2019-09-12, renamed 供給力 on 2025-12-25; the column names are
          TEPCO's. The daily files also hold a 5-minute table; it is not
          loaded. One row per target date and hour (0-23); grain enforced at
          load time. History starts 2016-04-01 and runs through yesterday,
          except 2024-03-31 (never published). The one 修正後 correction row
          (2016-04-24 00:00, 1267 → 1212 万kW) is applied at load. Load
          contract: conf/schemas/kansai_power_usage_hourly.yaml; format,
          quirks and the A-1 comparison in docs/Kansai-Power-Usage-Retrieval.md.
        columns:
          - name: target_date
            description: Delivery date (DATE).
            data_tests:
              - not_null
          - name: hour_start
            description: >
              Hour the value covers, 0-23: TIME "h:00" is the hour from h:00
              to h+1:00 (JEPX time codes 2h+1 and 2h+2).
            data_tests:
              - not_null
          - name: demand_mankw
            description: Area demand averaged over the hour (当日実績), in 万kW; never 0 or blank.
            data_tests:
              - not_null
          - name: forecast_mankw
            description: >
              Kansai's hourly demand forecast (予想値) as of the file's UPDATE
              stamp — the day's last intraday revision, not a day-ahead
              forecast; present in every layout.
          - name: usage_rate_pct
            description: >
              使用率(%) for the hour; present in every layout. Defined as
              当日実績 / ピーク時供給力 until 2020-11-15 and as 当日実績 / the
              hour's 供給力想定値 from 2020-11-16.
          - name: supply_capacity_mankw
            description: >
              供給力想定値(万kW) (2019-09-12 → 2025-12-24) / 供給力(万kW)
              (2025-12-25 →) for the hour; null before 2019-09-12.
          - name: file_updated_at
            description: The file's UPDATE stamp (first line), 01:10 on target_date + 1.
            data_tests:
              - not_null
          - name: source_file
            description: File the row was read from (YYYYMMDD_juyo1_kansai.csv or juyo_06_YYYYMMDD.csv).
            data_tests:
              - not_null
```

- [ ] **Step 2: staging**

`stg_kansai__power_usage_hourly.sql`: the TEPCO stg SQL with `{{ source('kansai', 'kansai_power_usage_hourly') }}`.

`stg_kansai__power_usage_hourly.yml`: the TEPCO stg yml with the name and description
"As-is representation of pma_raw.kansai_power_usage_hourly (Kansai でんき予報 hourly 電力使用状況: Kansai-area 1時間平均 demand in 万kW, 2016-04-01 →). One row per target_date and hour_start. Column documentation lives on the source (models/raw/kansai.yml)." — same tests (grain unique, hour 0-23, demand ≥ 0, not-nulls).

- [ ] **Step 3: standardized**

`std_kansai__power_usage_hourly.sql`:

```sql
with
  staging as (
  select
    *
  from
    {{ ref('stg_kansai__power_usage_hourly') }}
  ),

  final as (
  select
    target_date as delivery_date,
    hour_start,
    hour_start + 1 as hour_ending,
    timestampadd(hour, hour_start, cast(target_date as timestamp)) as delivery_datetime,
    case when month(target_date) >= 4 then year(target_date) else year(target_date) - 1 end as fiscal_year,
    -- Published as integer 万kW (1 万kW = 10 MW); raw stores double only
    -- defensively — every loaded value is integral.
    cast(round(demand_mankw) as int) as demand_mankw,
    cast(round(forecast_mankw) as int) as forecast_mankw,
    cast(round(usage_rate_pct) as int) as usage_rate_pct,
    -- 供給力想定値 (2019-09-12 → 2025-12-24) / 供給力 (2025-12-25 →); the
    -- column does not exist before 2019-09-12.
    cast(round(supply_capacity_mankw) as int) as supply_capacity_mankw,
    file_updated_at,
    source_file
  from
    staging
  )

select * from final
```

`std_kansai__power_usage_hourly.yml`:

```yaml
models:
  - name: std_kansai__power_usage_hourly
    config:
      contract:
        enforced: true
    description: >
      Standardized Kansai でんき予報 hourly 電力使用状況
      (stg_kansai__power_usage_hourly) with a typed time axis: delivery_date;
      hour_start (0-23, the hour starting then — Kansai's own label, equal to
      dim_delivery_period.hour_of_day; verified against the A-1 series summed
      per hour, MAE 0.5 万kW, vs 50-100 shifted by one hour); hour_ending
      (1-24, the JMA / MSM / OCCTO convention); delivery_datetime = hour start;
      Japanese fiscal_year. One row per delivery_date and hour_start, gapless
      from 2016-04-01 except 2024-03-31, which Kansai never published (singular
      test assert_std_kansai__power_usage_hourly_calendar_complete). Measures
      stay in the published unit, integer 万kW: demand_mankw is the hour's
      1時間平均 demand (min 922, never 0 or blank; tested >= 1 — Kansai revises
      without notice but a zero would still be wrong); forecast_mankw (予想値,
      the day's last intraday revision) and usage_rate_pct are in every layout
      (tested not null); supply_capacity_mankw (供給力想定値, 供給力 from
      2025-12-25) exists from 2019-09-12 and is null exactly before it
      (tested) and always >= demand (tested). 使用率 changed definition on
      2020-11-16, from 当日実績 / ピーク時供給力 to 当日実績 / the hour's
      供給力想定値. file_updated_at is the file's UPDATE stamp, 01:10 the next
      day. source_file marks the member-name generation.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - delivery_date
              - hour_start
      - dbt_utils.expression_is_true:
          name: std_kansai_power_usage_supply_gte_demand
          arguments:
            expression: "supply_capacity_mankw >= demand_mankw"
      - dbt_utils.expression_is_true:
          name: std_kansai_power_usage_supply_capacity_from_2019_09_12
          arguments:
            expression: "(supply_capacity_mankw is null) = (delivery_date < date '2019-09-12')"
    columns:
      - name: delivery_date
        data_type: date
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: "date '2016-04-01'"
      - name: hour_start
        data_type: int
        description: Hour of delivery_date the value covers, 0-23 = the hour starting then.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 23
      - name: hour_ending
        data_type: int
        description: hour_start + 1, the JMA / MSM / OCCTO hour-ending label (1-24).
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 24
      - name: delivery_datetime
        data_type: timestamp
        description: Start of the hour (delivery_date + hour_start hours).
        data_tests:
          - not_null
      - name: fiscal_year
        data_type: int
        description: Japanese fiscal year (April-March) of delivery_date.
        data_tests:
          - not_null
      - name: demand_mankw
        data_type: int
        description: Hourly-average area demand in 万kW (1 万kW = 10 MW), as published.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
      - name: forecast_mankw
        data_type: int
        description: >
          Kansai's hourly demand forecast (予想値) in 万kW as of the file's
          stamp — the day's last intraday revision, not a day-ahead forecast.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: usage_rate_pct
        data_type: int
        description: 使用率 in percent; denominator changed on 2020-11-16 (see the model description).
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 100
      - name: supply_capacity_mankw
        data_type: int
        description: 供給力想定値 / 供給力 in 万kW for the hour. Null before 2019-09-12.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: file_updated_at
        data_type: timestamp
        data_tests:
          - not_null
      - name: source_file
        data_type: string
        data_tests:
          - not_null
```

`dbt/dbt_tests/assert_std_kansai__power_usage_hourly_calendar_complete.sql`:

```sql
-- The hourly history must be gapless and start where the source starts,
-- with one known hole: Kansai never published 2024-03-31 (site maintenance
-- that day; the downloader's known_missing_days lets the settled month
-- through). Because the (delivery_date, hour_start) grain is unique, the
-- row count equals (span days - 1) * 24 only when no other day or hour is
-- missing AND the hole is still there; pinning the first day to 2016-04-01
-- catches a reload that lost a leading month, and an empty relation fails
-- outright. Should Kansai ever publish 2024-03-31, both the count clause
-- and the last clause fail: drop the day from the kansai.power_usage spec
-- and from this test. The last day is not pinned (see the TEPCO test).
select
  count(*) as n_rows,
  min(delivery_date) as first_day,
  datediff(max(delivery_date), min(delivery_date)) * 24 as n_expected,
  sum(case when delivery_date = date '2024-03-31' then 1 else 0 end) as n_rows_on_missing_day
from {{ ref('std_kansai__power_usage_hourly') }}
having
  count(*) = 0
  or min(delivery_date) != date '2016-04-01'
  or count(*) != datediff(max(delivery_date), min(delivery_date)) * 24
  or sum(case when delivery_date = date '2024-03-31' then 1 else 0 end) != 0
```

- [ ] **Step 4: fact**

`fct_area_power_usage_hourly.sql` — replace the `tokyo` CTE comment and add the union:

```sql
  -- One standardized model per TSO でんき予報 feed, each publishing only its
  -- own service area; union another branch here when a third TSO's series
  -- is loaded (as fct_area_demand_generation_actual does).
  tokyo as (
  select
    delivery_date,
    hour_start,
    delivery_datetime,
    demand_mankw,
    'tokyo' as area_code
  from
    {{ ref('std_tepco__power_usage_hourly') }}
  ),

  kansai as (
  select
    delivery_date,
    hour_start,
    delivery_datetime,
    demand_mankw,
    'kansai' as area_code
  from
    {{ ref('std_kansai__power_usage_hourly') }}
  ),

  feeds as (
  select * from tokyo
  union all
  select * from kansai
  ),

  final as (
  select
    feeds.delivery_date as date_key,
    feeds.hour_start as hour_of_day,
    areas.area_key,
    feeds.delivery_datetime,
    -- The published 1時間平均 in 万kW over one hour is 万kWh; x 10,000 gives
    -- kWh, the unit of fct_area_demand_generation_actual.
    cast(feeds.demand_mankw as bigint) * 10000 as demand_kwh
  from
    feeds
    inner join areas
      on areas.area_code = feeds.area_code
  )
```

`fct_area_power_usage_hourly.yml` description — replace "for every JEPX area whose TSO publishes it and is loaded: Tokyo (TEPCO Power Grid, std_tepco__power_usage_hourly)." with "for every JEPX area whose TSO publishes it and is loaded: Tokyo (TEPCO Power Grid, std_tepco__power_usage_hourly) and Kansai (関西電力送配電, std_kansai__power_usage_hourly)."; replace "Covers 2016-04-01 — the only public Tokyo-area demand before A-1 begins — through yesterday, gapless." with "Covers 2016-04-01 — the only public area demand before A-1 begins — through yesterday, gapless except Kansai 2024-03-31 (never published)."; replace "(docs/TEPCO-Power-Usage-Retrieval.md §5)" with "(docs/TEPCO-Power-Usage-Retrieval.md §5; Kansai: docs/Kansai-Power-Usage-Retrieval.md §5)"; the last sentence "The daily files' 予測値 / 使用率 / 供給力 columns stay in the standardized model" → "stay in the standardized models". `area_key` description: `JEPX area (dim_area): 'tokyo' and 'kansai'.`

- [ ] **Step 5: Parse host-side**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse && cd ..`
Expected: no errors (contracts, refs, test args).

- [ ] **Step 6: Commit**

```bash
git add dbt/models/raw/kansai.yml dbt/models/staging/stg_kansai__power_usage_hourly.sql dbt/models/staging/stg_kansai__power_usage_hourly.yml dbt/models/standardized/std_kansai__power_usage_hourly.sql dbt/models/standardized/std_kansai__power_usage_hourly.yml dbt/dbt_tests/assert_std_kansai__power_usage_hourly_calendar_complete.sql dbt/models/curated/fct_area_power_usage_hourly.sql dbt/models/curated/fct_area_power_usage_hourly.yml
git commit -m "feat(dbt): raw, stg, std and the fact branch for the Kansai でんき予報 hourly series" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Real run in the devcontainer, proof numbers, A-1 comparison

Background tasks, from the main checkout (compose project), `WT=/workspace/.claude/worktrees/kansai-power-usage`. The data lands in the main checkout's `data/` (shared cache, gitignored).

- [ ] **Step 1: Download**

```bash
docker compose exec -T -w $WT -e PYTHONPATH=$WT devcontainer python scripts/download_kansai_power_usage.py --data-dir /workspace/data/kansai/power_usage
```

Expected: 126 zips (2016-04 → 2026-09), ~3,810 daily files, no coverage error; the 2024-03 month passes with 30 members. Record the counts.

- [ ] **Step 2: Load**

```bash
docker compose exec -T -w $WT -e PYTHONPATH=$WT devcontainer python scripts/load_kansai_power_usage.py --data /workspace/data/kansai/power_usage/csv
```

Expected: `Loaded N rows` with N = (days 2016-04-01 → yesterday − 1) × 24. Record N and the wall time.

- [ ] **Step 3: dbt**

```bash
docker compose exec -T -w $WT/dbt devcontainer dbt deps
docker compose exec -T -w $WT/dbt devcontainer dbt build
```

Expected: every model and test passes, including the new singular test.

- [ ] **Step 4: Proof queries** (via `docker compose exec -T -w $WT/dbt devcontainer dbt show --inline "<sql>" --limit 30`):

```sql
select count(*) n, min(delivery_date) first_day, max(delivery_date) last_day,
       sum(case when delivery_date = date '2024-03-31' then 1 else 0 end) on_missing_day
from pma_standardized.std_kansai__power_usage_hourly
```
```sql
select delivery_date, hour_start, demand_mankw, forecast_mankw, usage_rate_pct, source_file
from pma_standardized.std_kansai__power_usage_hourly
where (delivery_date = date '2016-04-24' and hour_start = 0)
   or (delivery_date in (date '2025-03-14', date '2025-05-07') and hour_start = 12)
order by delivery_date
```
```sql
select min(case when supply_capacity_mankw is not null then delivery_date end) first_supply,
       max(case when supply_capacity_mankw is null then delivery_date end) last_null_supply,
       min(demand_mankw) min_demand, max(demand_mankw) max_demand
from pma_standardized.std_kansai__power_usage_hourly
```

Expected: 2016-04-24 00:00 = 1212 / 65; the padded and zero-padded days present; first_supply 2019-09-12, last_null_supply 2019-09-11; min 922 (2025-05-05 01:00), max 2915.

- [ ] **Step 5: A-1 comparison** (fills doc §5 and the PR Proof), run as one `dbt show --inline` per query:

```sql
with juyo as (
  select p.date_key, p.hour_of_day, p.demand_kwh / 10000 as juyo_mankw
  from pma_curated.fct_area_power_usage_hourly p
  join pma_curated.dim_area a on a.area_key = p.area_key and a.area_code = 'kansai'
),
a1 as (
  select f.date_key, d.hour_of_day, sum(f.demand_kwh) / 10000.0 as a1_mankw
  from pma_curated.fct_area_demand_generation_actual f
  join pma_curated.dim_area a on a.area_key = f.area_key and a.area_code = 'kansai'
  join pma_curated.dim_delivery_period d on d.time_code = f.time_code
  group by f.date_key, d.hour_of_day
  having count(f.demand_kwh) = 2
),
j as (
  select j.date_key, j.hour_of_day, j.juyo_mankw, a1.a1_mankw, j.juyo_mankw - a1.a1_mankw as diff,
         case when month(j.date_key) >= 4 then year(j.date_key) else year(j.date_key) - 1 end as fy
  from juyo j join a1 on a1.date_key = j.date_key and a1.hour_of_day = j.hour_of_day
)
select fy, count(*) hours, round(avg(diff), 2) bias, round(avg(abs(diff)), 2) mae,
       round(avg(abs(diff) / a1_mankw) * 100, 3) mape_pct,
       round(avg(case when abs(diff) <= 0.5 then 1.0 else 0.0 end) * 100, 0) within_half_pct
from j group by fy order by fy
```

then the same CTEs with `select count(*), round(avg(diff),2), round(avg(abs(diff)),2), round(avg(abs(diff)/a1_mankw)*100,3), min(date_key), max(date_key) from j` (overall), with `where j.date_key between date '2022-04-01' and date '2022-04-14'` (the April-2022 vintage), and the shift check — join `a1.hour_of_day = j.hour_of_day + 1` and `- 1` — reporting MAE for each. Record every number for Task 7.

---

### Task 7: Docs

**Files:** create `docs/Kansai-Power-Usage-Retrieval.md`; modify `docs/_sidebar.md`, `docs/README.md`, `docs/TEPCO-Power-Usage-Retrieval.md` §6, `CLAUDE.md`.

- [ ] **Step 1: `docs/Kansai-Power-Usage-Retrieval.md`** — TEPCO's outline, in this order: `# Kansai でんき予報 過去の電力使用実績 (hourly 電力使用状況) — retrieval and format`; intro (verified against a full capture on 2026-09-06: every monthly archive 2016-04 → 2026-09); §1 What it is (page, family, why — R-005 needs `fct_area_power_usage_hourly` for Kansai —, coverage 2016-04-01 → yesterday except 2024-03-31); §2 Files and URLs (table: monthly archive URL + `jisseki.json` listing; members; running month holds finished days only; the zip name collides with the A-1 archive's but lives in `data/kansai/power_usage/zip/`); §3 Daily file layout (line 1 stamp `yyyy/M/d H:mm UPDATE` — D+1 01:10 on 3,807 of 3,808 files; the three hourly headers with their date ranges; 24 rows `0:00`…`23:00`; dates `yyyy/M/d`, zero-padded on 2025-05-07 and 05-08; the blank line ends the table; the 5-minute header `DATE,TIME,当日実績(５分間隔値)(万kW)` + `太陽光発電実績(５分間隔値)(万kW)` from 2019-09-12); §4 Quirks (padded files — the 60 dates; the correction row and how it is applied; the missing day; the 使用率 definition change 2020-11-16 with the 50,831/50,832 vs 1,319/10,344 reproduction; the member rename 2025-12; the revision policy → every zip re-fetched); §5 Comparison with the A-1 series (the Task 6 step 5 tables and the shift check; the April-2022 vintage remark); §6 Downloading and loading with `power_market_analytics.kansai.power_usage` (code sample, the shared module, the warehouse path, entry points, tests); §7 Not ingested: the 5-minute table. Every number comes from the spec §3 or Task 6.

- [ ] **Step 2: `docs/_sidebar.md`** — after the Kansai A-1 line: `- [Kansai でんき予報 Power Usage Retrieval](Kansai-Power-Usage-Retrieval.md)`.

- [ ] **Step 3: `docs/README.md`** — Loaded row after the Kansai A-1 row:

```
| 関西電力送配電 | [過去の電力使用実績データ（でんき予報）](https://www.kansai-td.co.jp/denkiyoho/download/) — monthly `YYYYMM_jisseki.zip` of daily files under `…/yamasou/` | <ul><li>date</li><li>hour (1時間平均)</li><li>Kansai area</li></ul> | **Hourly table only.** `DATE, TIME, 当日実績(万kW), 予想値(万kW), 使用率(%)`, + `供給力想定値(万kW)` from 2019-09-12 (renamed `供給力(万kW)` 2025-12-25); 使用率 redefined 2020-11-16; 60 Excel-padded files and one `修正後` correction (2016-04-24 00:00) handled at load; the same files carry a 5-minute table that is parsed past (Candidates). Differs from A-1 by MAE <Task 6> 万kW over 2022-04 → 2026-09 ([doc](Kansai-Power-Usage-Retrieval.md)) | 2016-04-01 ~ yesterday, except 2024-03-31 | `pma_raw.kansai_power_usage_hourly` |
```

Candidates row after TEPCO's 5-minute row:

```
| 関西電力送配電 | [過去の電力使用実績データ（でんき予報）— 5-minute table](https://www.kansai-td.co.jp/denkiyoho/download/) — the block below the hourly table in the same daily files | <ul><li>date</li><li>5 min</li><li>Kansai area</li></ul> | `当日実績(５分間隔値)(万kW)` from 2016-04, + `太陽光発電実績(５分間隔値)(万kW)` from 2019-09-12 | 2016-04-01 ~ yesterday | Not ingested: the hourly loader skips this block. Would join TEPCO's in a 5-min-grain raw table |
```

The `fct_area_power_usage_hourly` bullet: "(Tokyo, TEPCO Power Grid today)" → "(Tokyo — TEPCO Power Grid — and Kansai — 関西電力送配電)"; "Covers 2016-04-01 — the only public Tokyo-area demand before A-1 begins — through yesterday, gapless." → "Covers 2016-04-01 — the only public area demand before A-1 begins — through yesterday, gapless except Kansai 2024-03-31."; "stay in `std_tepco__power_usage_hourly`" → "stay in the `std_<tso>__power_usage_hourly` models".

- [ ] **Step 4: `docs/TEPCO-Power-Usage-Retrieval.md` §6** — "`TepcoPowerUsageCsvLoader` (a `CsvLoader`) reads each file with `parse_hourly`" → "`TepcoPowerUsageCsvLoader` (the shared `PowerUsageCsvLoader` of `power_market_analytics/power_usage.py`, which Kansai's loader also extends) reads each file with `parse_hourly`", and add after "so a truncated member fails the load instead of publishing a day with missing hours": ", reads every line with trailing commas removed and applies a `修正後` correction row — rules Kansai's archive needs; no TEPCO hourly table has either".

- [ ] **Step 5: `CLAUDE.md`**
  - `refresh-all` bullet: "TEPCO (both datasets), Kansai, e-Stat" → "TEPCO (both datasets), Kansai (both datasets), e-Stat".
  - Kansai command bullet: "Kansai: the same as TEPCO's actuals for 関西電力送配電 (…): `download_kansai_area_demand_generation.py`, `load_kansai_area_demand_generation.py`." → add "and でんき予報 hourly 電力使用実績 (`download_kansai_power_usage.py` redownloads every monthly `YYYYMM_jisseki.zip` under `…/yamasou/`, 2016-04 → now, ~8.5 MB; `load_kansai_power_usage.py`)."
  - Architecture, Kansai A-1 bullet: `power_market_analytics/kansai.py` → `power_market_analytics/kansai/area_demand_generation.py` (`KANSAI`, `KansaiAreaDownloader`; the `kansai/` package holds one module per Kansai dataset and re-exports these names).
  - Architecture, the でんき予報 bullet: rename it "TSO でんき予報 過去の電力使用実績 (hourly 電力使用状況 …)" and say: the parser/loader are the shared `power_market_analytics/power_usage.py` (`PowerUsageSource` = `AreaActualsSource` + `multi_day_headers`; `parse_hourly(file, source)` reads every line with trailing commas removed and applies a `修正後` row to the row above; `PowerUsageCsvLoader` with a per-file `_file_rows` hook); TEPCO's `tepco/power_usage.py` keeps the spec, yearly files and yearly-row drop; Kansai's `kansai/power_usage.py` (`KANSAI_POWER_USAGE`: three hourly headers switching 2019-09-12 / 2025-12-25, members `YYYYMMDD_juyo1_kansai.csv` → `juyo_06_YYYYMMDD.csv` from 2025-12, `known_missing_days = {2024-03-31}`) → `scripts/download_kansai_power_usage.py` → `data/kansai/power_usage/{zip,csv}/` → `scripts/load_kansai_power_usage.py` (contract `conf/schemas/kansai_power_usage_hourly.yaml`) → `pma_raw.kansai_power_usage_hourly` → `stg/std_kansai__power_usage_hourly` (supply capacity null exactly before 2019-09-12; singular test = gapless except 2024-03-31) → the `kansai` branch of `fct_area_power_usage_hourly`. `AreaActualsSource.known_missing_days` goes into the TSO area-actuals bullet ("`known_missing_days` — settled-month coverage tolerates them, a published one is logged").
  - Demand task bullet: "Tokyo-only until another TSO's series is loaded" → "Tokyo and Kansai since 2026-09-06 (PR of this plan)".

- [ ] **Step 6: Commit**

```bash
git add docs/Kansai-Power-Usage-Retrieval.md docs/_sidebar.md docs/README.md docs/TEPCO-Power-Usage-Retrieval.md CLAUDE.md
git commit -m "docs(kansai): でんき予報 retrieval doc, README rows and CLAUDE.md for the hourly series" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Gates, PR, review loop

- [ ] **Step 1:** `just test` (100 %), `just lint`, `just mypy`, `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse` — all clean; fix anything that is not, commit.
- [ ] **Step 2:** `git push -u origin feature/kansai-power-usage`; `gh pr create` with title `feat(kansai): でんき予報 hourly 電力使用実績 through fct_area_power_usage_hourly`, body sections *Why* / *What* / *Proof* (Task 6 numbers); `gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label documentation`.
- [ ] **Step 3:** Codex wait / address / resolve loop, then Copilot, per CLAUDE.md; CI green on the final head.
- [ ] **Step 4:** Update memory `kansai-power-usage-spec.md` (implemented, PR number) and report the PR as ready.
