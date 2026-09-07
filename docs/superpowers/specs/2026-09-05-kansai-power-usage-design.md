# Kansai でんき予報 過去の電力使用実績 (hourly 電力使用状況) — design

Date: 2026-09-05. Status: **approved 2026-09-06** (in chat; both open points resolved as
recommended — one PR, the `修正後` row applied). Implemented per
`docs/superpowers/plans/2026-09-06-kansai-power-usage.md` on branch `feature/kansai-power-usage`.

## 1. Goal

Load 関西電力送配電's でんき予報 demand history the way TEPCO's is loaded today: raw table,
staging, standardized model, and a second area in `fct_area_power_usage_hourly`. The
similar-day design (`2026-09-05-demand-similar-day-reference-design.md`, decision 2) needs
this fact for Kansai before its strategy can run there.

The source was profiled on 2026-09-05: every monthly zip since 2016-04 (126 zips, 8.5 MB,
3,808 daily files). The findings below are measured, not assumed.

## 2. Decisions

1. **Scope: the whole path in one PR** — raw, stg, std, the fact's `kansai` branch, the two
   scripts in `refresh-all`, a retrieval doc, README and CLAUDE.md rows. TEPCO's path was
   built in two PRs because the std/fact design did not exist yet. It does now, so one PR
   copies it. (Open point 1.)
2. **`power_market_analytics/kansai.py` becomes the package `kansai/`**, one module per
   dataset, as `tepco/` is: `kansai/area_demand_generation.py` (today's `kansai.py`,
   unchanged) and `kansai/power_usage.py` (new). `kansai/__init__.py` re-exports `KANSAI`,
   `KansaiAreaDownloader` and `KansaiAreaCsvLoader`, so no import site changes.
3. **The でんき予報 parser and loader become shared code** in a new module
   `power_market_analytics/power_usage.py`, next to `area_actuals.py`. TEPCO's
   `tepco/power_usage.py` keeps only what is TEPCO's: the source spec, the yearly files
   and the yearly-row drop. Both TSOs publish the same four hourly measures in the same
   multi-section file shape, so one parser serves both. Section 4 has the split.
4. **Lines are read with trailing commas removed. A line that is then empty is blank.**
   60 Kansai files were resaved from Excel and carry every line padded to six fields
   (`,,,,,` where a blank line should be, a header ending in a comma). Without the rule
   the parser reads past the hourly table. With it, a row whose field count still differs
   from the header's fails the load, naming the file. The rule applies to TEPCO's files
   too; none of them has a trailing comma, so nothing changes there.
5. **A `修正後` row corrects the row above it.** One file (2016-04-24) holds
   `修正後,,1212,,65,4/25 システム不具合による数値誤りのため修正` under the hour-0 row
   (1267 万kW, 68 %). The non-blank measures of the correction row replace the previous
   row's; blank ones keep the original. The warehouse holds 1212 万kW and 65 % for
   2016-04-24 00:00. Kansai's own note says the original was a system error, and the
   5-minute table carries the same correction. (Open point 2.)
6. **`AreaActualsSource` gets `known_missing_days: frozenset[datetime.date]`**, default
   empty. `_check_month_coverage` subtracts them from the expected days. Kansai's でんき予報
   spec lists 2024-03-31 (site maintenance that day; the page says so). When a listed day
   is present after all, the download logs it at INFO so the entry can be retired. Any
   other missing day in a settled month still fails the download.
7. **The raw contract is TEPCO's, column for column**: `target_date`, `hour_start`,
   `demand_mankw`, `forecast_mankw`, `usage_rate_pct`, `supply_capacity_mankw`,
   `file_updated_at`, `source_file`. `supply_capacity_mankw` is nullable: the column
   appears on 2019-09-12. Kansai calls the forecast 予想値 and the capacity
   供給力想定値 (供給力 from 2025-12-25); the warehouse names do not change per TSO.
8. **std tests pin what the profile showed**: `demand_mankw >= 1` (min 922 万kW, never 0 or
   blank), `supply_capacity_mankw >= demand_mankw` (holds on every row that has one),
   `supply_capacity_mankw is null` exactly when `delivery_date < 2019-09-12`,
   `forecast_mankw` and `usage_rate_pct` not null (present in every layout). A singular
   test requires the history to have no gaps from 2016-04-01 except 2024-03-31: row count
   = (span days − 1) × 24 and no row on 2024-03-31. If Kansai ever publishes that day the
   test fails on both clauses, and the fix is to drop the day from the spec and the test.
9. **`fct_area_power_usage_hourly` gets a `kansai` CTE unioned under `tokyo`**, joined to
   `dim_area` on `area_code = 'kansai'` (seed row 6, 関西). Same `demand_kwh` = 万kW ×
   10,000. The fact's "no gaps" claim becomes "no gaps except Kansai 2024-03-31".
10. **The hour convention is TEPCO's**: `TIME h:00` is the hour starting then. Verified
    against the Kansai A-1 files already on disk, summed per hour: MAE 0.5 万kW on
    2025-08-01 and 2026-01-15 (6.9 on 2022-04-01, the April-2022 A-1 vintage offset TEPCO
    also showed); shifted by one hour the MAE is 50 to 100 万kW.
11. **Names**: table `pma_raw.kansai_power_usage_hourly`, contract
    `conf/schemas/kansai_power_usage_hourly.yaml`, data dir `data/kansai/power_usage/{zip,csv}`,
    scripts `scripts/download_kansai_power_usage.py` and `scripts/load_kansai_power_usage.py`,
    models `stg_kansai__power_usage_hourly` and `std_kansai__power_usage_hourly`, doc
    `docs/Kansai-Power-Usage-Retrieval.md`. Branch `feature/kansai-power-usage`, commits
    `feat(kansai): …`, labels `enhancement` + `documentation`.
12. **Not loaded**: the 5-minute table (当日実績 from 2016-04, plus 太陽光発電実績 from
    2019-09-12) and the headline blocks. The 5-minute table gets a Candidates row in the
    README, as TEPCO's has.

## 3. The source, as measured

| Item | Finding |
|---|---|
| Archive | `https://www.kansai-td.co.jp/yamasou/YYYYMM_jisseki.zip`, listed in `https://www.kansai-td.co.jp/yamasou/jisseki.json`; 2016-04 → the current month; 57 to 78 KB per full month, 8.5 MB in all; plain GET, served to python-requests |
| Page | `https://www.kansai-td.co.jp/denkiyoho/download/` (過去の使用電力実績データダウンロード) |
| Members | `YYYYMMDD_juyo1_kansai.csv` through 2025-11, `juyo_06_YYYYMMDD.csv` from 2025-12 (the same month the A-1 feed renamed its members); flat; CP932, CRLF |
| Running month | Finished days only (the 2026-09 zip holds 09-01 to 09-04 on 09-05), so `archive_includes_current_day` stays False |
| Line 1 | `yyyy/M/d H:mm UPDATE`; D+1 01:10 on 3,807 files, D+1 00:40 on one |
| Hourly header | 2016-04-01 to 2019-09-11: `DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)`; 2019-09-12 to 2025-12-24: + `供給力想定値(万kW)`; 2025-12-25 →: + `供給力(万kW)` instead |
| Hourly rows | 24 per file, `0:00` … `23:00`; dates `yyyy/M/d`, zero-padded (`2025/05/07`) on 2025-05-07 and 05-08 |
| 5-minute header | `DATE,TIME,当日実績(５分間隔値)(万kW)`; + `太陽光発電実績(５分間隔値)(万kW)` from 2019-09-12 |
| Padded files | 60: 2016-04-24, 2022-06-22, 2023-09-28, 2024-03-30, 2024-09-08 and 2025-03-14 to 2025-05-08 except 04-05 |
| Correction row | 2016-04-24 only (decision 5) |
| Missing day | 2024-03-31 only |
| Values | 91,392 hourly rows = 3,808 files × 24; demand 922 万kW (2025-05-05 01:00) to 2,915 万kW (2020-08-21 14:00), never 0 or blank; no blank measure anywhere; 使用率 40 to 100 |
| 使用率 definition | Changed 2020-11-16 from 当日実績 ÷ ピーク時供給力 to 当日実績 ÷ 各時間帯の供給力想定値 (the page says so; after the change `round(demand / supply × 100)` reproduces 使用率 ± 1 on 50,831 of 50,832 rows, before it on 1,319 of 10,344) |
| Revisions | The page: 需要実績は、過去にさかのぼり修正させていただく場合があります, with no notice. Every zip is re-fetched on every run, which the shared downloader already does |

## 4. Code

### 4.1 `power_market_analytics/power_usage.py` (new, shared)

- `PowerUsageSource(AreaActualsSource)`, frozen dataclass. Adds
  `multi_day_headers: frozenset[str] = frozenset()`: headers whose files may hold many days
  (TEPCO's yearly files). A file under any other header must hold exactly one date.
- `HourlyRow`, `HourlyFile`: moved from `tepco/power_usage.py`, unchanged.
- `parse_hourly(file, source) -> HourlyFile`. Today's TEPCO parser plus decisions 4 and 5:

- every line is `rstrip("\r\n").rstrip(",")`;
- the stamp is line 1;
- the hourly table is the block under the first accepted header and ends at the
  first blank line;
- a row whose first field is `修正後` is recognised before the field-count
  check: its measure fields, positions 2 up to the header's last, merge into the
  previous row, and anything beyond (the note) is ignored;
- every other row must have the header's field count;
- every day covers hours 0 to 23 exactly once;
- a file whose header is not in `multi_day_headers` holds one date. Errors are `ValueError` naming the file, as today.
- The `__`-prefixed contract source names and `_SOURCE_COLUMNS`: moved here.
- `PowerUsageCsvLoader(CsvLoader)`: class attribute `source: PowerUsageSource`; `_read_all`
  parses every file with `parse_hourly(file, self.source)`, keeps `self._file_rows(parsed)`
  (default: all rows), and builds one string DataFrame that `_project` casts through the
  contract. This is today's `TepcoPowerUsageCsvLoader` with the yearly-row drop moved into
  the hook.

### 4.2 `power_market_analytics/tepco/power_usage.py` (shrinks)

Keeps `TEPCO_POWER_USAGE` (now a `PowerUsageSource` with
`multi_day_headers = {YEARLY_HEADER}`), `DAILY_HOURLY_HEADER`, `YEARLY_HEADER`,
`DAILY_FILES_FROM`, `YEARLY_URL_TEMPLATE`, `YEARLY_YEARS`, `HISTORY_START`,
`expected_yearly_dates`, `TepcoPowerUsageDownloader` (the yearly files) and
`TepcoPowerUsageCsvLoader(PowerUsageCsvLoader)` whose `_file_rows` drops yearly rows on or
after `DAILY_FILES_FROM`. It re-exports `HourlyRow`, `HourlyFile` and a one-line
`parse_hourly(file)` bound to `TEPCO_POWER_USAGE`, so `download_yearly` and
`tests/test_tepco_power_usage.py` keep working.

### 4.3 `power_market_analytics/kansai/power_usage.py` (new)

- `KANSAI_POWER_USAGE = PowerUsageSource(code="kansai_power_usage",
  url_template="https://www.kansai-td.co.jp/yamasou/{year:04d}{month:02d}_jisseki.zip",
  earliest_month=(2016, 4), member_re=r"(^|/)(\d{8}_juyo1_kansai|juyo_06_\d{8})\.csv$",
  accepted_headers={the three hourly headers}, default_data_dir="data/kansai/power_usage",
  known_missing_days={2024-03-31})`.
- `KansaiPowerUsageDownloader(AreaActualsDownloader)`: bound to the spec; `download_all()`
  as inherited (the shared range logic already ends at the month of yesterday).
- `KansaiPowerUsageCsvLoader(PowerUsageCsvLoader)`: `source = KANSAI_POWER_USAGE`, nothing
  else.

### 4.4 `power_market_analytics/area_actuals.py`

`AreaActualsSource.known_missing_days` (decision 6). `_check_month_coverage` computes
`expected − known_missing_days − found`; when `found ∩ known_missing_days` is non-empty it
logs the days at INFO.

### 4.5 Scripts and justfile

`scripts/download_kansai_power_usage.py` (`--data-dir`, default `data/kansai/power_usage`)
and `scripts/load_kansai_power_usage.py` (`--schema`, `--data`, `--table` with the decision
11 defaults), shaped like the Kansai A-1 scripts. `refresh-all` runs them right after the
Kansai A-1 pair.

### 4.6 Contract `conf/schemas/kansai_power_usage_hourly.yaml`

TEPCO's contract with a Kansai description: grain `[target_date, hour_start]`; the eight
columns of decision 7 with the same types and formats (`yyyyMMdd`, `yyyyMMdd HH:mm:ss`);
`forecast_mankw` and `usage_rate_pct` nullable in raw as in TEPCO's (the std tests pin them
not null).

## 5. dbt

- `models/raw/kansai.yml`: second table `kansai_power_usage_hourly`, columns documented as
  in `tepco.yml`, with the 予想値 / 供給力想定値 names and the three-layout history.
- `stg_kansai__power_usage_hourly`: as-is select; grain test; the TEPCO stg tests.
- `std_kansai__power_usage_hourly`: the columns of `std_tepco__power_usage_hourly`
  (`delivery_date`, `hour_start`, `hour_ending`, `delivery_datetime`, `fiscal_year`, the
  four measures as integer 万kW, `file_updated_at`, `source_file`); tests of decision 8;
  singular `dbt_tests/assert_std_kansai__power_usage_hourly_calendar_complete.sql`.
- `fct_area_power_usage_hourly`: decision 9. `area_key` description lists both areas.
- Every model keeps an enforced contract and a grain uniqueness test.

## 6. Docs

- `docs/Kansai-Power-Usage-Retrieval.md`: TEPCO's outline: what it is, files
  and URLs, and the daily layout with the three headers. Then the quirks —
  padded files, the correction row, the missing day, the 2020-11-16 使用率 change,
  the member rename, the revision policy. Then the comparison with the Kansai
  A-1 series over 2022-04-01 → the last loaded day, whose numbers come from the
  real load (section 8). Then downloading and loading, and the 5-minute table
  not being ingested. Sidebar entry after the Kansai A-1 doc.
- `docs/README.md`: a Loaded row (関西電力送配電, でんき予報, 2016-04-01 ~ yesterday, one
  known hole) and a Candidates row (its 5-minute table, 太陽光 from 2019-09-12); the
  `fct_area_power_usage_hourly` bullet says Tokyo and Kansai.
- `CLAUDE.md`: the Kansai command bullet gains the two scripts; the でんき予報 architecture
  bullet names the shared module, the `kansai/` package and the Kansai branch;
  `refresh-all` mentions both Kansai datasets. `docs/Kansai-Area-Demand-Generation-Retrieval.md`
  §6 says `kansai/area_demand_generation.py` instead of `kansai.py`.

## 7. Errors

- Download: a response that is not a zip, a zip with no matching member, a member dated
  outside its month, or a settled month missing a day not in `known_missing_days` raise
  `AreaActualsDownloadError`; HTTP errors propagate. `refresh-all` stops before the load.
- Parse. Any of these raises `ValueError` with the file name: a missing or
  malformed stamp, no accepted header, an empty block, a row whose field count
  differs from the header's after the trailing-comma strip, a `TIME` not on the
  hour, a day not covering hours 0 to 23 exactly once, or a single-day file
  holding two dates. A `修正後` row with a non-numeric measure fails the contract
  cast, naming the file.
- Load: grain duplicates and nulls in non-nullable columns fail as for every `CsvLoader`,
  naming the offending files.

## 8. Tests and verification

- `tests/test_power_usage.py` (new): the shared parser on a plain file, a padded file, the
  correction row (applied; blanks keep the original; a correction with no row above
  fails), the one-date rule with and without `multi_day_headers`, and the loader base with
  the `_file_rows` hook.
- `tests/test_kansai_power_usage.py` (new) covers three things. The spec: URL,
  names, both member patterns, the three headers, the missing day. The
  downloader through the shared fakes: a month archive with both member
  generations, the settled month 2024-03 passing without the 31st, and another
  absent day failing. The loader end to end on the local Spark fixture with the
  three layouts and a padded file: values, nulls before 2019-09-12,
  `source_file`, `file_updated_at`.
- `tests/test_tepco_power_usage.py`: unchanged behaviour; only imports move if any.
- `tests/test_area_actuals.py`: `known_missing_days` on `_check_month_coverage`.
- Script tests: `download_kansai_power_usage` next to the TEPCO one in
  `test_download_scripts.py`; `load_kansai_power_usage` in `GENERIC_SCRIPTS` and
  `CONTRACT_GRAINS` of `test_load_scripts.py`.
- Gates: `just test` at 100 % coverage, `just lint`, `just mypy`, `just dbt build` green.
- Proof for the PR body, from the real run in the devcontainer:

- every zip and daily file downloaded — 126 and 3,808 as of 2026-09-05;
- (days − 1) × 24 rows loaded — 91,392 for 2016-04-01 → 2026-09-04, plus 24 per
  later day;
- 2016-04-24 00:00 = 1212 万kW;
- 2025-03-14 (padded) and 2025-05-07 (zero-padded date) present;
- no row on 2024-03-31;
- supply null exactly before 2019-09-12;
- the fact against the Kansai A-1 fact summed per
  `dim_delivery_period.hour_of_day` (bias, MAE, MAE %), which also fills the
  doc's comparison section.
- Review loop per CLAUDE.md: Codex, then Copilot, then report ready.

## 9. Out of scope

- The 5-minute table and the headline blocks (decision 12).
- Stitching this series with the A-1 series; the fact stays the でんき予報 series alone.
- Running the similar-day strategy for Kansai; that is R-005's experiment.
- Chubu's でんき予報 archive (`YYYYMM_power_usage.zip`, 2019-04 →) — the shared module is
  meant to take it as a third spec later, nothing is built for it now.

## 10. Open points

1. Scope (decision 1): one PR through the fact, or raw + stg first as TEPCO's was.
2. The 2016-04-24 correction (decision 5): apply the 修正後 values (recommended) or load
   the row as originally published and only document the note.
