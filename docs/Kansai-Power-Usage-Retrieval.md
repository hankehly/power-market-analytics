# Kansai でんき予報 過去の電力使用実績 (hourly 電力使用状況) — retrieval and format

How 関西電力送配電 publishes the Kansai-area demand history behind its でんき予報
page, what the files look like, how the hourly series compares with the Kansai
A-1 series already in the warehouse, and how
`power_market_analytics.ingestion.tso.kansai.power_usage` brings the hourly table into
`pma_raw.kansai_power_usage_hourly`. It is the Kansai counterpart of
[TEPCO's feed](TEPCO-Power-Usage-Retrieval.md); the two share the parser and
loader (`power_market_analytics/ingestion/tso/power_usage.py`) and land in the same curated
fact. Verified against a full capture on 2026-09-06 (every monthly archive
2016-04 → 2026-09: 126 zips, 3,809 daily files).

## 1. What it is

- **Page**: <https://www.kansai-td.co.jp/denkiyoho/download/> (過去の使用電力実績データダウンロード).
- **Family**: the でんき予報 電力使用状況, a display product like TEPCO's: integer
  万kW (1 万kW = 10 MW), the hourly value is the 1時間平均 of the hour. Not the
  インバランス料金 系統需給情報 (A-1 …) behind `fct_area_demand_generation_actual`.
- **Why we load it**: the similar-day feature of the demand task reads the
  でんき予報 hourly load (`fct_area_power_usage_hourly`); the Kansai series lets
  the strategy run for the Kansai area ([demand/R-004](research/demand/R-004-prior-year-load-lag.md)
  E-002, the Tokyo baseline; R-005 plans the Kansai run). It is also the only
  public Kansai-area demand before 2022-04-01. Only the hourly table is loaded;
  see [§7](#7-not-ingested-the-5-minute-table).
- **Coverage**: hourly 2016-04-01 → yesterday, except **2024-03-31**, which Kansai
  never published (the page notes site maintenance that day).

## 2. Files and URLs

| What | URL | Notes |
|---|---|---|
| Monthly archive | `https://www.kansai-td.co.jp/yamasou/YYYYMM_jisseki.zip` | 2016-04 → the current month; 57–78 KB per full month, 8.5 MB in all; plain `GET`. Listed in `https://www.kansai-td.co.jp/yamasou/jisseki.json` (the downloader does not need the listing: it walks the months from 2016-04 to yesterday's). The name is the same as the A-1 feed's archive (`…/interchange/denkiyoho/imbalance/YYYYMM_jisseki.zip`); they live in different data dirs. |
| Daily members | `YYYYMMDD_juyo1_kansai.csv` through 2025-11, `juyo_06_YYYYMMDD.csv` from 2025-12 | Flat; CP932, CRLF, 335–347 lines. The rename is the same month the A-1 feed renamed its members. |

The running month's zip holds finished days only (day D appears on D+1 at 01:10),
so the current day is never in the archive.

## 3. Daily file layout

| Lines | Section |
|---|---|
| 1 | `2016/4/2 1:10 UPDATE` — the file's stamp (read into `file_updated_at`); D+1 01:10 on all but one of the 3,809 files (D+1 00:40 on the other) |
| 2 … | ピーク時供給力 / 予想最大電力 / (from 2025) 使用率ピーク時供給力 / 最大使用率 headline blocks |
| — | the **hourly table** header, one of three (below), then 24 rows `0:00` … `23:00` = the hour starting then; dates `yyyy/M/d` (zero-padded `2025/05/07` on 2025-05-07 and 05-08) |
| — | blank — ends the hourly table |
| — | 翌日のピーク時供給力 / 翌日の予想最大電力 / 予想気温 blocks |
| — | `DATE,TIME,当日実績(５分間隔値)(万kW)` (+ `太陽光発電実績(５分間隔値)(万kW)` from 2019-09-12) — the **5-minute table**, 288 rows |

The hourly header changed twice:

| Delivery days | Header |
|---|---|
| 2016-04-01 → 2019-09-11 | `DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)` |
| 2019-09-12 → 2025-12-24 | `DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力想定値(万kW)` |
| 2025-12-25 → | `DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力(万kW)` |

`当日実績(万kW)` is the hourly actual. `予想値(万kW)` is Kansai's hourly demand
forecast as of the stamp: the day's last intraday revision, not a day-ahead
forecast. `使用率(%)` is the usage rate, whose definition changed
([§4](#4-quirks)). `供給力想定値` / `供給力` is the hour's supply capacity. The warehouse
keeps TEPCO's column names (`forecast_mankw`, `usage_rate_pct`,
`supply_capacity_mankw`, the last null before 2019-09-12). No measure is ever
blank or 0: demand runs from 922 万kW (2025-05-05 01:00) to 2,915 万kW
(2020-08-21 14:00), 使用率 from 40 to 100.

## 4. Quirks

- **60 files were re-saved from Excel.** Every line is padded with commas to the
  widest row: `2025/3/15 1:10 UPDATE,,,,,`, `,,,,,` where a blank line belongs,
  a 5-field header ending in a comma. The dates: 2016-04-24, 2022-06-22,
  2023-09-28, 2024-03-30, 2024-09-08 and 2025-03-14 → 2025-05-08 except 04-05.
  The parser reads every line with its trailing commas removed, so the hourly
  table still ends at the (now empty) blank line; a row whose field count still
  differs from the header's fails the load, naming the file.
- **One correction row.** 2016-04-24 holds
  `修正後,,1212,,65,4/25 システム不具合による数値誤りのため修正` directly under the
  hour-0 row (`2016/4/24,0:00,1267,1230,68,`). The parser applies it: the
  non-blank measures of the `修正後` row replace the row above's, blank ones keep
  the original — the warehouse holds 1212 万kW and 65 % for 2016-04-24 00:00 and
  the published 1230 万kW forecast. Kansai's own note calls the original a system
  error, and the 5-minute table carries the same correction.
- **2024-03-31 is missing** from the 2024-03 archive (30 members). The spec's
  `known_missing_days` lets the settled month through; the singular dbt test
  requires the hole. Should Kansai publish the day, the download logs it and the
  test fails on purpose — drop the day from both.
- **使用率 changed definition on 2020-11-16**, from 当日実績 ÷ ピーク時供給力 to
  当日実績 ÷ the hour's 供給力想定値 (the page says so). After the change
  `round(demand / supply × 100)` reproduces 使用率 within ±1 on 50,831 of 50,832
  rows; before it on 1,319 of 10,344.
- **Revisions.** The page says 需要実績は、過去にさかのぼり修正させていただく場合が
  あります, with no notice. Every zip is re-fetched on every run, which the
  shared downloader already does; unlike TEPCO's, the series is not immutable.

## 5. Comparison with the Kansai A-1 series (2022-04-01 → 2026-08-30, 38,676 hours)

Hourly `当日実績` against `fct_area_demand_generation_actual` (Kansai) aggregated to
the hour (the two half-hours' kWh ÷ 10,000 = mean 万kW over the hour;
`h:00` = time codes 2h+1, 2h+2, i.e. `dim_delivery_period.hour_of_day`). The
hour convention is TEPCO's: shifted by one hour either way the MAE is 58 万kW
instead of 1.5.

| FY | Hours | Bias | MAE | MAPE | Within ±0.5 万kW |
|---|---:|---:|---:|---:|---:|
| 2022 | 8,760 | +3.18 | 4.99 | 0.307 % | 23 % |
| 2023 | 8,760 | −0.46 | 0.47 | 0.030 % | 60 % |
| 2024 | 8,760 | −0.40 | 0.42 | 0.027 % | 60 % |
| 2025 | 8,748 | −0.40 | 0.42 | 0.027 % | 60 % |
| 2026 (to Aug) | 3,648 | −0.40 | 0.42 | 0.028 % | 60 % |

Overall MAE 1.46 万kW (0.09 %), bias +0.40. Two regimes:

- **2022-04 → 2023-02: the two series disagree in daylight.** The でんき予報 value
  sits above A-1 by +4 to +10 万kW per month on average, with bias +9.4 in
  2022-06 and +9.6 in 2022-09. Almost all of it falls between 08:00 and 16:00:
  FY2022's hourly bias peaks at +11.4 万kW at 12:00 and stays under +1.4 at
  night. The gap shrinks from 2022-10 (bias −0.4, MAE 7.2) and is gone by
  2023-03; 294 days of FY2022 have an hour more than 3 万kW apart, 8 days of
  FY2023 (the last on 2023-07-31), none after. A-1's opening fortnight
  (2022-04-01 → 14, the scientific-notation vintage) is the worst stretch: bias
  +3.6, MAE 7.4.
- **2023-03 onward: the integer display of one measurement.** Bias −0.40 to
  −0.48 in every hour of the day, MAE 0.42, and 60 % of hours within ±0.5 万kW.
  From 2023-08 no hour is more than 3 万kW apart. The でんき予報 integer runs 0.4 万kW
  below the A-1 hourly mean, nothing more. Unlike Tokyo, Kansai's A-1 shows no
  18:00–19:00 defect (hours 17–19 sit at the same −0.47 bias as the rest).

The FY2022 daylight gap is a property of the published series (both are as
Kansai publishes them today; April 2022 A-1 was re-issued in 2023-09). It is
recorded here so a reader of either fact knows the two disagree before
2023-03; which series is closer to the metered load is not established.

## 6. Downloading and loading with `power_market_analytics.ingestion.tso.kansai.power_usage`

```python
from power_market_analytics.ingestion.tso.kansai.power_usage import KansaiPowerUsageDownloader

downloader = KansaiPowerUsageDownloader()          # data/kansai/power_usage
downloader.download(2025, 7)                        # one month -> 31 csv/ files
downloader.download_all()                           # 2016-04 .. yesterday's month
# zips  -> data/kansai/power_usage/zip/YYYYMM_jisseki.zip
# csvs  -> data/kansai/power_usage/csv/<daily member name>
```

`KANSAI_POWER_USAGE` is a `PowerUsageSource` (an `AreaActualsSource` with
`multi_day_headers`, empty here: every file holds one date): the URL template,
2016-04, both member-name generations, the three hourly headers and
`known_missing_days = {2024-03-31}`. `KansaiPowerUsageDownloader` is the shared `AreaActualsDownloader` bound to it.
Every zip is re-downloaded and the daily members extracted into `csv/`. A
settled month must hold a member for every day except the listed one. The
running month may be partial, and on the 1st it is skipped. `KansaiPowerUsageCsvLoader` is the shared `PowerUsageCsvLoader`
bound to the spec: `parse_hourly` reads each file's hourly table: trailing commas removed, the
`修正後` row applied, hours 0–23 exactly once, one date per file. All ~3,800
files land in one `createDataFrame`. The contract
`conf/schemas/kansai_power_usage_hourly.yaml`, TEPCO's column for column, casts
the `__`-prefixed string columns. Grain `(target_date, hour_start)` is
enforced at load time; an unknown header fails the load. End to end:

```bash
just python scripts/download_kansai_power_usage.py   # 126 zips, ~8.5 MB
just python scripts/load_kansai_power_usage.py       # 3,809 files, 91,416 rows, ~10 s
just dbt build
```

(`just refresh-all` runs them after the Kansai A-1 pair.)

Warehouse path: `pma_raw.kansai_power_usage_hourly` → `stg_kansai__power_usage_hourly` (as-is)
→ `std_kansai__power_usage_hourly` → the `kansai` branch of
`fct_area_power_usage_hourly`.

`std` types the time axis: `delivery_date`, `hour_start` 0–23 as published,
`hour_ending` 1–24, `delivery_datetime` = hour start, and `fiscal_year`. The
four measures stay integer 万kW.

Tests pin `demand_mankw` ≥ 1, `forecast_mankw` and `usage_rate_pct` not null,
and `supply_capacity_mankw` null exactly before 2019-09-12 and ≥ demand. The
singular test `assert_std_kansai__power_usage_hourly_calendar_complete`
requires the history to have no gaps from 2016-04-01 except 2024-03-31.

The fact has grain `date_key × hour_of_day × area_key`, with `demand_kwh` =
万kW × 10,000. The fact is this series alone — not stitched
with the A-1 series.

Unit tests: `tests/test_power_usage.py` (the shared parser and loader: padded
files, the correction row, the one-date rule), `tests/test_kansai_power_usage.py`
(the spec, the downloader with the missing day, the contract, the loader on all
three layouts), `tests/test_area_actuals.py` (`known_missing_days`),
`tests/test_download_scripts.py` / `tests/test_load_scripts.py` (the scripts) —
`just test`.

## 7. Not ingested: the 5-minute table

The 288-row block below the hourly table (`当日実績(５分間隔値)(万kW)` from
2016-04, plus `太陽光発電実績(５分間隔値)(万kW)` from 2019-09-12) is parsed
past, not loaded. If it is ever needed it should join TEPCO's in a raw table at
5-minute grain, not become more columns here.
