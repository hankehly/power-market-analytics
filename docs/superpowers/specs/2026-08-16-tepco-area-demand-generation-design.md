# TEPCO エリア需要・発電情報 (area demand & generation actuals) — warehouse design

Date: 2026-08-16. Status: approved (brainstorming session, actuals-only scope).

## 1. Goal

Add TEPCO's published Tokyo-area 30-minute demand and generation actuals to the
warehouse as a new source pipeline (download → raw → staging → standardized →
curated fact), following the JEPX / JMA / OCCTO conventions, so the data is
joinable with `fct_jepx_spot_area_price` at the (date, time code, area) grain.

Source page: <https://www.tepco.co.jp/forecast/html/area-download-j.html>
(エリア需要・発電情報のダウンロード). Not the per-fuel エリア需給実績 page
(`area_data-j.html`), which was considered and dropped.

## 2. Scope

In scope:

- The 実績 (actuals) files `AREA_JISEKI_YYYYMMDD.csv` from the monthly archives
  `AREA_YYYYMM.zip`, 2022-04 through the current month.
- Downloader + loader + load contract + `just refresh-tepco`.
- dbt: raw source, `stg`, `std`, `fct` with enforced contracts and tests.
- Retrieval doc, docs index/README/ER diagram, CLAUDE.md updates.

Out of scope (deliberately):

- The 予測 (`AREA_YOSOKU_*`) and BG計画総計 (`AREA_BGKEI_*`) files. The
  archived copies are the last intraday revision (updated ~23:40 on the target
  day), not the day-ahead versions, so they are not clean day-ahead features.
  The `_actual` suffix on every object leaves room to add them later.
- The live current-day / next-day files (`AREA_JISEKI.csv`, `AREA_YOSOKU.csv`,
  `AREA_BGKEI.csv`, `AREA_ONCE_*.csv`). The current month's zip is refreshed
  daily and already contains every finalized day through yesterday; the live
  actuals file is partial (future periods are published as 0).

## 3. Source facts (verified 2026-08-16 by surveying all 1,598 daily files)

- Archive URL: `https://www4.tepco.co.jp/forecast/html/images/AREA_YYYYMM.zip`,
  months 2022-04 → current month; ~100 KB each, 53 zips ≈ 5 MB total. The current
  month's zip is refreshed daily (2026-08 held 2026-08-01..15 on 2026-08-16).
- Every zip holds three files per day (`AREA_JISEKI_`, `AREA_YOSOKU_`,
  `AREA_BGKEI_` + `YYYYMMDD.csv`). All are flat except `AREA_202403.zip`, whose
  members sit under an `AREA_202403/` subfolder — extraction must flatten.
- Coverage is complete: every day 2022-04-01 → yesterday has all three files.
- `AREA_JISEKI_*.csv` layout (identical across the entire history):
  - Encoding CP932, CRLF line endings.
  - Line 1: `ファイル更新日,ファイル更新時間,対象年月日`
  - Line 2: `yyyymmdd,HH:MM:SS,yyyymmdd` (file update date/time, target date;
    the target date always equals the file-name date).
  - Line 3: `日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量`
    (note the full-width underscore `＿`; the YOSOKU/BGKEI headers differ).
  - Lines 4–51: exactly 48 rows, `yyyymmdd,1..48,H:MM,H:MM,int,int,int`;
    time code 1 = 0:00–0:30, 48 = 23:30–0:00. Units 30分kWh (energy per
    30-minute period). No blank cells, no negatives.
- Update timing: actuals files are finalized at ~00:05 the next day. A few were
  revised later (2022-12-01/02 on 2022-12-14, 2024-03-11 on 2024-04-19), so
  past months are not immutable.
- Data quirks:
  1. 13 files (2022-04-01..13) contain scientific-notation values such as
     `1.66919e+07` (47 cells; precision lost to ~10 kWh). Spark's ANSI
     `cast(string as bigint)` throws on these.
  2. 2025-06-14 time codes 11–48 are all-zero across all three measures — TEPCO
     publishes 0 for future periods and this file froze mid-day; the archive
     never got a complete version.
  3. 2023-09-17 time code 11 has wind+solar = 0 with normal demand/generation
     (left as published).

## 4. Components

### 4.1 `power_market_analytics/tepco.py` — `TepcoAreaDownloader`

- `TepcoAreaDownloader(data_dir="data/tepco/area_demand_generation", timeout=60.0)`.
- Constants: `URL_TEMPLATE`, `EARLIEST_MONTH = (2022, 4)`, `ACTUALS_MEMBER_RE`
  (`AREA_JISEKI_\d{8}\.csv$`).
- `zip_path_for(year, month) -> Path` = `data_dir/zip/AREA_YYYYMM.zip`;
  `csv_dir` = `data_dir/csv/`.
- `download(year, month) -> list[Path]`: GET the zip (raise on HTTP error),
  verify the payload is a zip (`zipfile.is_zipfile` on the bytes / magic),
  write to `.part` then rename, extract only members matching
  `ACTUALS_MEMBER_RE` into `csv/` using the member **basename** (flattens the
  2024-03 subfolder), overwrite existing files. Returns the extracted paths.
  Raises `TepcoDownloadError` when the response is not a zip or contains no
  actuals members.
- `download_all(today=None) -> list[Path]`: iterate months from
  `EARLIEST_MONTH` to the current month (inclusive), calling `download` for
  each. Always re-downloads everything (5 MB) — past days are revised
  occasionally and this is the simplest way to pick that up. No cache flag.
- Month iteration is a small local helper (`month_range(start, end)`), no
  fiscal-year logic involved.

### 4.2 `power_market_analytics/tepco_loader.py` — `TepcoAreaCsvLoader(CsvLoader)`

Mirrors `JmaHourlyCsvLoader`:

- Contract `source` fields are positional `_c0`..`_c6` plus the injected
  `__file_updated_at`.
- `_read_file(file)`: Python-side sniff opens the file (cp932), asserts line 3
  equals `EXPECTED_HEADER` (else `ValueError` naming the file — layout drift
  fails loudly) and reads line 2 (`yyyymmdd,HH:MM:SS,yyyymmdd`) into a
  `"yyyyMMdd HH:mm:ss"` string. Then Spark reads the file headerless with a
  7-column string schema, keeps rows where `_c0` rlike `^\d{8}$` **and**
  `_c1` rlike `^\d{1,2}$` (line 2 also starts with a date), adds
  `__file_updated_at` as that string literal, and applies the contract casts
  via the inherited `_cast` (the contract's `format: yyyyMMdd HH:mm:ss`
  parses it to a timestamp).
- Everything else (validation, grain check, overwrite write) is inherited.

### 4.3 `conf/schemas/tepco_area_demand_generation_actual.yaml`

- `read_options.encoding: windows-31j`; `grain: [target_date, time_code]`.
- Columns (destination order):
  | name | source | type | notes |
  |---|---|---|---|
  | target_date | `_c0` | date, format `yyyyMMdd` | not null |
  | time_code | `_c1` | int | not null, 1–48 |
  | period_start_time | `_c2` | string | as published `H:MM` |
  | period_end_time | `_c3` | string | as published; 24:00 appears as `0:00` |
  | demand_kwh | `_c4` | double | not null; double because of quirk 1 |
  | generation_kwh | `_c5` | double | not null |
  | wind_solar_generation_kwh | `_c6` | double | not null |
  | file_updated_at | `__file_updated_at` | timestamp, format `yyyyMMdd HH:mm:ss` | not null |
- Description records the source, quirks, and why the measures are `double`.

### 4.4 Scripts and justfile

- `scripts/download_tepco_area_demand_generation.py` (`--data-dir`, default
  `data/tepco/area_demand_generation`): `TepcoAreaDownloader(...).download_all()`.
- `scripts/load_tepco_area_demand_generation.py` (`--schema`, `--data` default
  `data/tepco/area_demand_generation/csv`, `--table` default
  `pma_raw.tepco_area_demand_generation_actual`): `TepcoAreaCsvLoader(...).load()`.
- justfile: `refresh-tepco` = download → load → `just dbt build`, with a `[doc]`
  line like the other refresh recipes.

### 4.5 dbt models (all with `contract: enforced: true`, every column typed)

- `models/raw/tepco.yml`: source `tepco` (schema `pma_raw`), table
  `tepco_area_demand_generation_actual`, column docs + `not_null` tests on the
  key/measure columns; description points to the retrieval doc and contract.
- `stg_tepco__area_demand_generation_actual`: as-is select of the source
  columns. Test: `unique_combination_of_columns (target_date, time_code)`.
- `std_tepco__area_demand_generation_actual`: typed time axis and cleaned
  measures:
  - `delivery_date` (= target_date), `time_code`,
    `delivery_datetime = timestampadd(minute, (time_code-1)*30, cast(delivery_date as timestamp))`,
    `fiscal_year` (Apr–Mar, same expression as `std_jepx__spot`).
  - `demand_kwh`, `generation_kwh`, `wind_solar_generation_kwh` as
    `cast(round(x) as bigint)`; when all three published values are 0 on the
    same row (the unpublished sentinel, quirk 2) all three become null.
  - `file_updated_at` carried through.
  - Drops `period_start_time` / `period_end_time` (redundant with
    `dim_delivery_period`).
  - Tests: unique combination (delivery_date, time_code); not_null on keys;
    `accepted_range` 1–48 on time_code; `accepted_range min 0` on measures.
- `fct_tepco_area_demand_generation_actual` (curated):
  - Grain: one row per delivery period per area — (`date_key`, `time_code`,
    `area_key`). Only Tokyo (`dim_area.area_code = 'tokyo'`) exists today; the
    area dimension is included so the grain conforms with
    `fct_jepx_spot_area_price` and other TSOs can be added later.
  - Columns: `date_key` date, `time_code` int, `area_key` int,
    `delivery_datetime` timestamp, `demand_kwh` bigint, `generation_kwh`
    bigint, `wind_solar_generation_kwh` bigint. Measures are additive energy
    in the published unit (30-minute kWh). `file_updated_at` stays in std.
  - Tests: `unique_combination_of_columns` on the three keys; `not_null` on
    keys and `delivery_datetime`; `relationships` to `dim_date.date_key`,
    `dim_delivery_period.time_code`, `dim_area.area_key`; `accepted_range
    min_value: 0` on the measures (null allowed for the sentinel rows).
  - Description explains the grain, unit, additivity, Tokyo-only coverage,
    2022-04-01 start, and the nulled 2025-06-14 periods.

### 4.6 Documentation

- New `docs/TEPCO-Area-Demand-Generation-Retrieval.md` modeled on the OCCTO
  doc: overview, URLs and archive layout, CSV format table, verified
  completeness and quirks, publication timing, how the downloader/loader are
  run, and how to extend to 予測/BG計画 or the live files.
- `docs/_sidebar.md`: add the new doc. `docs/README.md`: add the fact to the
  list ("seven fact tables") and to the mermaid ER diagram (dim_date,
  dim_delivery_period, dim_area edges + entity block).
- `CLAUDE.md`: `just refresh-tepco` command line, an Architecture data-flow
  bullet, and a Gotchas bullet (sci-notation → double in raw; 2025-06-14
  sentinel zeros → null in std; re-download-all policy).

## 5. Data flow summary

`AREA_YYYYMM.zip` → `data/tepco/area_demand_generation/{zip,csv}/` →
`scripts/load_tepco_area_demand_generation.py` (`TepcoAreaCsvLoader`, contract
`conf/schemas/tepco_area_demand_generation_actual.yaml`) →
`pma_raw.tepco_area_demand_generation_actual` →
`stg_tepco__area_demand_generation_actual` →
`std_tepco__area_demand_generation_actual` →
`fct_tepco_area_demand_generation_actual` (joins `dim_area` on `'tokyo'`).

## 6. Error handling

- Downloader: HTTP errors propagate (`raise_for_status`); non-zip payloads or
  zips without actuals members raise `TepcoDownloadError`; partial writes never
  replace a good zip (`.part` + rename).
- Loader: header mismatch, missing files, nulls in non-nullable columns, or a
  non-unique grain fail the load with a message naming the file/columns.
- dbt: contracts and tests catch type drift, duplicate keys, out-of-range or
  orphaned keys.

## 7. Verification

- `just python scripts/download_tepco_area_demand_generation.py` — 53 zips,
  1,598 CSVs in `csv/` (as of 2026-08-16).
- `just python scripts/load_tepco_area_demand_generation.py` — 76,704 rows
  (1,598 × 48).
- `just dbt build` — all models build, all tests pass.
- `uv run ruff check .` clean.
- Spot checks via `just dbt show --inline`: row count per day = 48;
  2025-06-14 codes 11–48 null in std/fct; 2022-04-01 code 29 generation_kwh =
  16691900; an inner join to `fct_jepx_spot_area_price` on (date_key,
  time_code, area_key) returns exactly the fact's row count for delivery dates
  the JEPX data already covers.
