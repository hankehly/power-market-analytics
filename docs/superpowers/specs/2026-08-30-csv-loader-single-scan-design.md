# CsvLoader single-scan reads: one FileScan per layout instead of one per file

**Date:** 2026-08-30
**Status:** Approved design; delivered as one PR per stage (stage 1 = base protocol + JMA)

## Context and motivation

`just python scripts/load_jma_hourly.py` looked hung and was reported as "fails to run to
completion". Diagnosis on the real data (pyspark 4.1.1, 1,608 station-year files):

- `CsvLoader._read_all` builds the frame as `reduce(DataFrame.unionByName, one frame per file)`.
  Every `unionByName` re-runs the analyzer over the whole growing `Union`
  (`Union.duplicatesResolvedBetweenBranches`), so **~8 min pass before the first Spark job
  exists** — no progress bar, driver at 90 % CPU.
- The resulting plan ships as a **45 MiB task binary** that every one of the ~1,608 tasks in each
  of the load's six stages (count, null check, count, distinct, write…) deserialises: task
  deserialise median 1.1 s vs run 0.6 s, driver RSS 1.7 → 16.7 GB within a minute. A run that
  survives takes ~1 h 45 min (2026-08-20: 12:56 → 14:38; the 2026-08-29 table's metastore
  `Created Time 09:38:50 UTC` proves it does finish). From a `docker compose exec` client that
  does not live that long (Claude Code's 10-min Bash cap, a sleeping terminal) it never appears
  to complete, while the in-container process keeps going.
- This is the root cause of the "needs `SPARK_DRIVER_MEMORY=20g`, 8g OOMs in the write, 1g
  stalls" gotcha in CLAUDE.md / `docs/JMA-Weather-Data-Retrieval.md`.
- It is **file-count-driven, not JMA-specific**: `TepcoAreaCsvLoader` (1,610 files, 7 columns)
  measured 197.6 s of planning, a 6.8 MiB binary and 43 s for a `count()` of 77,280 rows.
  `KansaiAreaCsvLoader` (1,604 files) shares the class; `MsmForecastCsvLoader` (1,606 `csv.gz`
  files) uses the base header-based path; `EstatCensusMeshCsvLoader` (302 files) also runs a
  Spark action per file in `_check_rows`. JEPX (11 files) and OCCTO (1 file) are unaffected;
  `TepcoPowerUsageCsvLoader` already builds one frame from Python-parsed rows.
- Proven alternative (scratch experiment, same contract, same casts): **one multi-path
  `spark.read.csv(files)` scan** with the station id taken from Spark's `_metadata.file_name`
  column loads all 1,608 files in ~48 s (plan 5.4 s, count 19.6 s, validate 6.9 s, write 7.1 s,
  58 partitions) with output row-for-row identical to the production table (13,658,616 rows,
  147 stations, `EXCEPT` both ways = 0).

## Decisions (user-approved, 2026-08-30)

1. **Full refactor across all loaders**, delivered **one PR per stage** so every loader changes
   with its own real-data proof (see *Staging*).
2. **The mechanism lives in `CsvLoader`**: a positional single-scan helper for the headerless
   loaders (JMA, area actuals) **and** a new header-based default that groups files by header
   line and scans each group once (JEPX, OCCTO, MSM, e-Stat).
3. **Per-file error reporting**: when validation fails after the read (nulls in a non-nullable
   column, duplicated grain keys), the message names the offending files.
4. **Verification = scratch diff**, run by Claude as a main-session background task: the new
   loader writes to a scratch table, `EXCEPT` both ways against the current production table
   must be empty, then the scratch database is dropped and the real `scripts/` entry point is
   run end-to-end.
5. **Git**: Conventional Branch `fix/<loader>-single-scan`, Conventional Commits
   (`fix(<scope>): …` for the behaviour change, `test(…)`/`docs(…)`/`refactor(…)` for support),
   PR title in the same form; commit after every task. Per PR: wait for Codex's review, address
   every item, then request a Copilot review and address it; the researcher merges.
6. **`_read_file` is dropped** wherever `_read_all` no longer calls it (positional loaders in
   stage 1–2, the base class and the でんき予報 shim in stage 4).
7. Deferred (Claude's recommendations apply unless overridden): memory defaults stay as they
   are and only the gotcha wording changes; Spark's default partitioning is accepted (MSM
   coalescing decided at stage 4); e-Stat's row checks become one grouped aggregation per
   vintage at stage 3.

## Design

### Base protocol (`power_market_analytics/csv_loader.py`)

- `SOURCE_FILE_COL = "_source_file"` — a hidden per-row column holding the row's file name
  (no directory). Reads attach it (`F.col("_metadata.file_name")`), `_validate` reports per
  file with it, `load()` drops it before the write. It is never a contract column, but a
  contract may *source* a column from it (`source: _source_file`), which is how a loader
  exposes the file name in its table.
- `_scan_positional(files, column_count) -> DataFrame` — one headerless scan of all `files`
  with the contract's `read_options`, schema `_c0 … _c<n-1>` (string), plus `SOURCE_FILE_COL`.
  Header/metadata lines come back as rows; the caller filters them with a pattern on `_c0`.
- `_project(raw) -> DataFrame` — the contract's `_cast` projection in contract order, keeping
  `SOURCE_FILE_COL` (the former tail of every `_read_file`).
- `_validate(df)` — unchanged checks; on failure the message gains
  `; by file (first 10): {file: {column: nulls}}` or
  `; first 10 duplicated keys (key, rows, files): [...]` when `SOURCE_FILE_COL` is present
  (`REPORT_LIMIT = 10`). Existing messages are otherwise byte-identical (tests pin them).
- `load()` — `df = self._read_all(files)`; cache; validate on `df`; log and write
  `df.drop(SOURCE_FILE_COL)`.
- `_read_all(files)` default — **stage 1**: still the per-file union, docstring corrected
  (only fit for a handful of files). **Stage 4**: group files by their header line (Python
  sniff of line 1 with the contract's encoding — Java charset names such as `windows-31j` map
  to Python codecs — and `sep`; the per-file "missing required columns" check moves here), one
  `spark.read.options(header="true", …).csv(group)` + `SOURCE_FILE_COL` per distinct header,
  `_project` each, `unionByName` the handful of groups. `_read_file` is removed.

### Per-loader reads

- **JMA (`JmaHourlyCsvLoader`, stage 1)** — `_read_all`: `_check_file(file, expected)` for every
  file first (column-count sniff + file-name regex, same messages as today, so a bad file fails
  before any scan), then `_scan_positional(files, expected)`, keep rows with `_c0 rlike '^\d{4}/'`,
  `__station_id = regexp_extract(SOURCE_FILE_COL, _FILENAME_RE, 1)`, `_project`.
- **Area actuals (`AreaActualsCsvLoader`, stage 2)** — `_read_all`: `sniff_metadata` every file in
  Python (already done in `_resolve_files` for sources with `archive_includes_current_day`) into a
  `{file name: file_updated_at}` map, build a one-row-per-file lookup frame, `_scan_positional`
  (`COLUMN_COUNT = 7`), the existing data-row filter and `yyyy/mm/dd` normalisation, inner
  broadcast join on `SOURCE_FILE_COL` (exactly one lookup row per file by construction),
  `_project`. Covers TEPCO and Kansai in one class.
- **e-Stat (`EstatCensusMeshCsvLoader`, stage 3)** — `_read_all`: `_identify` + `_check_headers`
  per file in Python, group files by vintage, one header-based scan per vintage with
  `SOURCE_FILE_COL`, vintage attributes as literals, `__primary_mesh_code` from the file name,
  `__source_file = SOURCE_FILE_COL`, `_check_rows` once per vintage as a single aggregation
  grouped by `SOURCE_FILE_COL` (per-file counts; the "exactly one label row" rule per file),
  `unionByName` of ≤ 2 frames.
- **MSM / JEPX / OCCTO (stage 4)** — no loader code; they take the new base default. MSM's
  1,606 gzip files are not splittable (one partition each); whether to `coalesce` before the
  write is decided from the measured output at that stage.
- **でんき予報 (`TepcoPowerUsageCsvLoader`)** — keeps its Python-parsed single frame; its
  `_read_file` shim and test go in stage 4.

### Observable differences (none in content)

- Raw tables land as Spark's size-packed partitions (~58 parquet files for JMA) instead of one
  file per input.
- File-level failures raise before any scan (same messages); row-level failures now also name
  the files.
- Load time and memory: JMA ~1 h 45 min → ~1 min; measured per stage and written into the docs.

## Staging

| Stage | Branch / PR | Code | Real-data proof |
|---|---|---|---|
| 1 | `fix/jma-loader-single-scan` — `fix(jma): read the hourly files in one scan instead of a per-file union` | base protocol (`SOURCE_FILE_COL`, `_scan_positional`, `_project`, per-file reports, `load()` drop), JMA `_read_all` + `_check_file`, `_read_file` removed; CLAUDE.md gotcha, JMA doc | JMA scratch diff; timing; a run at `SPARK_DRIVER_MEMORY=4g`; `just python scripts/load_jma_hourly.py`; `just dbt build --select stg_jma__hourly_staffed` |
| 2 | `fix/area-actuals-loader-single-scan` | `AreaActualsCsvLoader._read_all` (+ lookup join), `_read_file` removed; TEPCO/Kansai docs | TEPCO + Kansai scratch diffs; both scripts; `stg_tepco__…`, `stg_kansai__…` builds |
| 3 | `fix/estat-loader-single-scan` | `EstatCensusMeshCsvLoader._read_all`, grouped `_check_rows`; e-Stat doc | e-Stat scratch diff; script; `stg_estat__…` build |
| 4 | `fix/csv-loader-header-grouped-scan` | base `_read_all` = header-grouped scan, base `_read_file` + でんき予報 shim removed, Java→Python codec map; CLAUDE.md gotcha (MSM), MSM doc §8 | JEPX, OCCTO ×2, MSM scratch diffs; scripts; MSM coalesce decision |

Each stage's plan is written when the previous PR is merged, so review feedback on the protocol
feeds forward.

## Verification protocol (every stage)

1. Host: `just test` (100 % coverage gate), `just lint`, `just mypy`.
2. Container, main-session background task: a piped snippet loads with the new loader into
   `pma_scratch.<table>`; beeline `select count(*) from (prod except scratch)` and the reverse
   must both be 0, plus row / station / span counts; `drop database pma_scratch cascade`.
3. Container: the real `scripts/load_*.py` entry point (wall time recorded), then
   `just dbt build --select <staging model>`.
4. Write the measured numbers into the docs touched by that stage.

## Non-goals

- No change to contracts, validation rules, overwrite semantics, dbt models, `just` recipes or
  script CLIs.
- No lowering of the compose memory defaults (`.env.template` table stays); only the gotcha
  wording changes to what is actually required.
- No change to `TepcoPowerUsageCsvLoader`'s parsing.
