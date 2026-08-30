# CsvLoader Single-Scan Reads — Stage 2 (area actuals: TEPCO + Kansai) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AreaActualsCsvLoader` (TEPCO `TepcoAreaCsvLoader`, Kansai `KansaiAreaCsvLoader`; ~1,600 daily files each) read its files in one Spark scan instead of a per-file union, keeping every file's own `file_updated_at`.

**Architecture:** `AreaActualsCsvLoader._read_all` sniffs every file's metadata line in Python (`sniff_metadata`, already used by `_resolve_files`) into a `{file name: file_updated_at}` map, turns it into a tiny lookup DataFrame, reads all files with `CsvLoader._scan_positional(files, COLUMN_COUNT)` (stage 1), applies the existing data-row filter and `yyyy/mm/dd` normalisation, inner-joins the broadcast lookup on `SOURCE_FILE_COL`, and returns `_project(raw)`. `_read_file` is deleted. Nothing changes in the subclasses, contracts or scripts.

**Tech Stack:** Python 3.13 / PySpark 4.1.1, pytest with the repo's `spark` fixture, ruff, mypy, `just`, `docker compose`, beeline.

**Spec:** `docs/superpowers/specs/2026-08-30-csv-loader-single-scan-design.md` (stage 2 row)

## Global Constraints

- Same as stage 1: NumPy docstrings; `just test` 100 % coverage gate, `just lint`, `just mypy`; Bash-driven edits must be followed by `uv run ruff format` + `ruff check --fix` on the file.
- Stage 1 is not merged yet (PR #19), so this branch is **stacked**: `fix/area-actuals-loader-single-scan` off `fix/jma-loader-single-scan`, PR base `fix/jma-loader-single-scan` (GitHub retargets it to `main` when #19 merges). Never rebase or force-push.
- Conventional Commits with scope `area-actuals` (the shared module) — `fix(area-actuals): …`, `test(area-actuals): …`, `docs(…)`; every message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. `git add` only the named files (the tree carries unrelated uncommitted `docs/research/…` edits and `.codex/`).
- Real-warehouse runs (`docker compose exec …`, `just python …`, `just dbt build`) are main-session background tasks.
- Do not touch `data/`, contracts, dbt models, `justfile`, the subclasses' `source` bindings, or `_resolve_files`.

---

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/area_actuals.py` | `AreaActualsCsvLoader._read_all` (one scan + stamp lookup join), `_read_file` deleted, class docstring updated, unused `pyspark.sql.types` import removed |
| `tests/test_area_actuals.py` | + duplicate-file-name guard test, per-file null report test, no-`_read_file` test |
| `CLAUDE.md` | architecture bullet ("reads positionally … in one scan"), "Many-file raw reloads" gotcha lists area actuals as converted |
| `docs/TEPCO-Area-Demand-Generation-Retrieval.md`, `docs/Kansai-Area-Demand-Generation-Retrieval.md` | one sentence each under the `just refresh-…` block: single scan + measured load time |

---

### Task 0: Branch

- [ ] **Step 1**

```bash
git checkout -b fix/area-actuals-loader-single-scan fix/jma-loader-single-scan
git status --short   # only the pre-existing docs/research + .codex entries (+ this plan file)
```

---

### Task 1: `AreaActualsCsvLoader._read_all` — one scan, per-file stamps via a lookup join

**Files:**
- Modify: `power_market_analytics/area_actuals.py` (imports lines 25-29; class docstring lines 427-449; `_read_file` lines 484-499 → replaced)
- Test: `tests/test_area_actuals.py` (loader section, after `test_only_running_day_files_raises_when_source_archives_current_day`)

**Interfaces:**
- Consumes (stage 1): `csv_loader.SOURCE_FILE_COL`, `CsvLoader._scan_positional(files, column_count)`, `CsvLoader._project(raw)`; module constants `COLUMN_COUNT`, `FILE_UPDATED_AT_SOURCE`, `_DATE_RE`, and `sniff_metadata(file, accepted_headers).file_updated_at`.
- Produces: `AreaActualsCsvLoader._read_all(self, files: list[str]) -> DataFrame` (contract columns + `_source_file`); raises `ValueError` when two files share a base name. `_read_file` no longer exists on the class.

- [ ] **Step 1: Write the failing tests**

Change the loader-section import in `tests/test_area_actuals.py` to
`from power_market_analytics.csv_loader import REPORT_LIMIT, CsvTableSchema  # noqa: E402`
and append to `TestAreaActualsCsvLoader`:

```python
    def test_every_file_keeps_its_own_update_stamp_in_one_scan(self, spark, tmp_path):
        write_cp932(tmp_path / "AREA_JISEKI_20250715.csv", TEPCO_FILE)
        later = list(TEPCO_FILE)
        later[1] = "20250717,00:05:09,20250716"
        later[3] = "20250716,1,0:00,0:30,15000000,12000000,140000"
        later[4] = "20250716,2,0:30,1:00,14000000,12000000,120000"
        write_cp932(tmp_path / "AREA_JISEKI_20250716.csv", later)
        loader = AreaActualsCsvLoader(
            CONTRACT, tmp_path, "test_area.stamps", spark=spark, source=LOADER_SOURCE
        )

        assert loader.load() == 4

        stamps = {
            (r.target_date.isoformat(), r.time_code): r.file_updated_at.isoformat()
            for r in spark.table("test_area.stamps").collect()
        }
        assert stamps == {
            ("2025-07-15", 1): "2025-07-16T00:05:04",
            ("2025-07-15", 2): "2025-07-16T00:05:04",
            ("2025-07-16", 1): "2025-07-17T00:05:09",
            ("2025-07-16", 2): "2025-07-17T00:05:09",
        }

    def test_two_files_with_the_same_name_are_rejected(self, spark, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        write_cp932(tmp_path / "a" / "AREA_JISEKI_20250715.csv", TEPCO_FILE)
        write_cp932(tmp_path / "b" / "AREA_JISEKI_20250715.csv", TEPCO_FILE)
        loader = AreaActualsCsvLoader(
            CONTRACT, tmp_path / "*" / "*.csv", "test_area.dupnames", spark=spark, source=LOADER_SOURCE
        )

        with pytest.raises(ValueError, match="share the file name AREA_JISEKI_20250715.csv"):
            loader.load()
        assert not spark.catalog.tableExists("test_area.dupnames")

    def test_null_report_names_the_offending_file(self, spark, tmp_path):
        write_cp932(tmp_path / "20250701_jisseki.csv", KANSAI_OLD_FILE)
        holes = list(TEPCO_FILE)
        holes[4] = "20250715,2,0:30,1:00,,12300000,120000"
        write_cp932(tmp_path / "AREA_JISEKI_20250715.csv", holes)
        loader = AreaActualsCsvLoader(
            CONTRACT, tmp_path, "test_area.nullfile", spark=spark, source=LOADER_SOURCE
        )

        with pytest.raises(ValueError) as exc:
            loader.load()

        assert str(exc.value).endswith(
            f"; by file (first {REPORT_LIMIT}): {{'AREA_JISEKI_20250715.csv': {{'demand_kwh': 1}}}}"
        )

    def test_loader_has_no_per_file_read(self):
        assert "_read_file" not in AreaActualsCsvLoader.__dict__
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_area_actuals.py::TestAreaActualsCsvLoader -q -p no:cacheprovider`
Expected: `test_two_files_with_the_same_name_are_rejected` fails (today the two files load — same stamps, same rows — and then the *grain* check fails with a different message), `test_null_report_names_the_offending_file` fails (no suffix yet), `test_loader_has_no_per_file_read` fails; the stamps test passes already (guards the new path).

- [ ] **Step 3: Implement**

`power_market_analytics/area_actuals.py` — imports: replace

```python
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from power_market_analytics.csv_loader import CsvLoader, CsvTableSchema
```

with

```python
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvLoader, CsvTableSchema
```

Class docstring — replace the first paragraph (lines 427-441)

```
    """Positional full reload of daily area-actuals CSVs into a warehouse table.

    Works exactly like :class:`~power_market_analytics.csv_loader.CsvLoader`
    (same validation and write behaviour) except for how each file is read:
    the files open with metadata lines before the real column header, so they
    are read headerless and the load contract addresses columns positionally
    (``source: _c0`` .. ``_c6``) plus ``__file_updated_at`` for the update
    timestamp taken from the metadata line. Only data rows (a date followed
    by a numeric time code) are kept, and ``yyyy/mm/dd`` dates are normalised
    to ``yyyymmdd`` so one contract format serves every layout. Each file's
    column-header line must be one of the source's ``accepted_headers``, and
    when the source's archive carries the running day
    (``archive_includes_current_day``) files that are not yet final are
    skipped.
```

with

```
    """Positional full reload of daily area-actuals CSVs into a warehouse table.

    Works exactly like :class:`~power_market_analytics.csv_loader.CsvLoader`
    (same validation and write behaviour) except for how the files are read:
    they open with metadata lines before the real column header, so all of
    them are read headerless in one scan (:meth:`CsvLoader._scan_positional`)
    and the load contract addresses columns positionally (``source: _c0`` ..
    ``_c6``) plus ``__file_updated_at`` for the update timestamp, which is
    sniffed from every file's metadata line in Python and joined back on the
    row's file name. Only data rows (a date followed by a numeric time code)
    are kept, and ``yyyy/mm/dd`` dates are normalised to ``yyyymmdd`` so one
    contract format serves every layout. Each file's column-header line must
    be one of the source's ``accepted_headers``, and when the source's
    archive carries the running day (``archive_includes_current_day``) files
    that are not yet final are skipped.
```

Replace `_read_file` (lines 484-499) with:

```python
    def _read_all(self, files: list[str]) -> DataFrame:
        stamps = {
            Path(file).name: sniff_metadata(file, self._source.accepted_headers).file_updated_at
            for file in files
        }
        if len(stamps) != len(files):
            names = [Path(file).name for file in files]
            clash = next(name for name in names if names.count(name) > 1)
            raise ValueError(
                f"{len(files) - len(stamps) + 1} files share the file name {clash}: the "
                "update stamp is joined back on the name, so every file must be unique"
            )
        lookup = self.spark.createDataFrame(
            list(stamps.items()), f"{SOURCE_FILE_COL} string, {FILE_UPDATED_AT_SOURCE} string"
        )
        raw = (
            self._scan_positional(files, COLUMN_COUNT)
            # Data rows start with a date AND a numeric time code; the metadata
            # value line also starts with a date, so both conditions are needed.
            .filter(F.col("_c0").rlike(f"^{_DATE_RE}$") & F.col("_c1").rlike(r"^\d{1,2}$"))
            .withColumn("_c0", F.regexp_replace(F.col("_c0"), "/", ""))
            # One lookup row per file by construction (dict keyed by name).
            .join(F.broadcast(lookup), SOURCE_FILE_COL, "inner")
        )
        return self._project(raw)
```

Then `uv run ruff format power_market_analytics/area_actuals.py && uv run ruff check --fix power_market_analytics/area_actuals.py` (removes nothing else; verify `pyspark.sql.types` is gone).

- [ ] **Step 4: Run the module, then the suite**

Run: `uv run pytest tests/test_area_actuals.py tests/test_tepco.py tests/test_kansai.py tests/test_load_scripts.py -q -p no:cacheprovider`
Expected: all PASS.
Run: `just test` → 100 % coverage; `just lint`; `just mypy` → clean.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/area_actuals.py tests/test_area_actuals.py
git commit -m "$(cat <<'EOF'
fix(area-actuals): read the daily files in one scan with a per-file stamp lookup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Real-data verification (main session, devcontainer)

- [ ] **Step 1: Scratch loads (background, one script for both TSOs)**

`scratchpad/area_actuals_stage2_scratch_load.py`:

```python
"""Load the TEPCO and Kansai daily files with the branch's loader into scratch tables."""

import time
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.kansai import KansaiAreaCsvLoader
from power_market_analytics.tepco import TepcoAreaCsvLoader

REPO = Path("/workspace")
for cls, stem, folder in (
    (TepcoAreaCsvLoader, "tepco_area_demand_generation_actual", "tepco/area_demand_generation/csv"),
    (KansaiAreaCsvLoader, "kansai_area_demand_generation_actual", "kansai/area_demand_generation/csv"),
):
    schema = CsvTableSchema.from_yaml(REPO / f"conf/schemas/{stem}.yaml")
    loader = cls(schema=schema, filepath=REPO / "data" / folder, table=f"pma_scratch.{stem}")
    t0 = time.perf_counter()
    n = loader.load()
    logger.info("scratch load {}: {} rows in {:.1f}s", stem, n, time.perf_counter() - t0)
```

Run: `docker compose exec -T -e PYTHONPATH=/workspace devcontainer python - < scratchpad/area_actuals_stage2_scratch_load.py > scratchpad/area_actuals_stage2_scratch_load.log 2>&1; echo "EXIT=$?" >> …` (background, timeout 600000). Expected: `EXIT=0`, each load well under a minute, no `Broadcasting large task binary`.

- [ ] **Step 2: Diff both against production**

For `T` in `tepco_area_demand_generation_actual`, `kansai_area_demand_generation_actual`:

```sql
select 'prod' t, count(*) n, min(target_date) min_d, max(target_date) max_d from pma_raw.T
union all
select 'scratch', count(*), min(target_date), max(target_date) from pma_scratch.T;
select count(*) only_in_prod from (select * from pma_raw.T except select * from pma_scratch.T);
select count(*) only_in_scratch from (select * from pma_scratch.T except select * from pma_raw.T);
```

Expected: identical counts/spans, both `EXCEPT` counts 0. (If production is stale relative to `data/`, say so and compare after Step 4 instead.)

- [ ] **Step 3: Drop scratch, run both real entry points (background), then dbt**

```bash
… beeline … -e "drop database if exists pma_scratch cascade"
just python scripts/load_tepco_area_demand_generation.py   # background, record WALL
just python scripts/load_kansai_area_demand_generation.py  # background, record WALL
just dbt build --select stg_tepco__area_demand_generation_actual stg_kansai__area_demand_generation_actual
```

Expected: both scripts `EXIT=0` in well under a minute; dbt all PASS.

---

### Task 3: Docs

- [ ] **Step 1: CLAUDE.md** — architecture bullet line 194-195: change "the loader reads positionally, sniffs the metadata line for `file_updated_at`, normalises `yyyy/mm/dd` dates and skips not-yet-final files." to "the loader reads every daily file positionally in one scan, sniffs each file's metadata line for `file_updated_at` (joined back on the file name), normalises `yyyy/mm/dd` dates and skips not-yet-final files." Gotcha line 385-386: after "JMA hourly since 2026-08-30 — … `SPARK_DRIVER_MEMORY=4g`)" add "; TEPCO/Kansai area actuals since 2026-08-30 — ~1,600 daily files each in ~<MEASURED> s".

- [ ] **Step 2: TEPCO + Kansai docs** — after each `just refresh-…` code block add the paragraph: "The loader reads all ~1,600 daily files in a single Spark scan (`CsvLoader._scan_positional`), sniffing each file's `ファイル更新日` line in Python and joining the stamp back on the file name — a full reload takes ~<MEASURED> s (before 2026-08-30 the per-file union spent ~3 min planning and ~40 s per Spark action)."

- [ ] **Step 3: Commit** — `docs(area-actuals): describe the single-scan load` (files: `CLAUDE.md`, both retrieval docs).

---

### Task 4: PR and reviews

- [ ] Push (`git push -u origin fix/area-actuals-loader-single-scan`), `gh pr create --base fix/jma-loader-single-scan --title "fix(area-actuals): read the daily files in one scan instead of a per-file union"` with the Why / What / Proof body (measured numbers), then assign the researcher and add labels; then the review loop from CLAUDE.md "Code review": wait for Codex (unbounded), address findings, `@codex review` re-rounds until clean, request Copilot, address, report.
