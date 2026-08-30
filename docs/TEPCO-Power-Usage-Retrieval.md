# TEPCO でんき予報 過去の電力使用実績 (hourly 電力使用状況) — retrieval and format

How TEPCO Power Grid publishes the Tokyo-area demand history behind its
でんき予報 page, what the files look like, how the hourly series compares with
the A-1 series already in the warehouse, and how
`power_market_analytics.tepco.power_usage` brings the hourly table into
`pma_raw.tepco_power_usage_hourly`. Verified against a full capture on
2026-08-29 (yearly files 2016–2022 and every monthly archive 2022-04 → 2026-08).

## 1. What it is

- **Page**: <https://www.tepco.co.jp/forecast/html/download-j.html> (過去の電力使用実績データ);
  the post-2022-04 monthly archives are listed on the per-year page
  <https://www.tepco.co.jp/forecast/html/download_year-j.html>.
- **Family**: the でんき予報 電力使用状況 — the *リアルタイム需要実績（５分間値、１時間値）*
  item of the 資源エネルギー庁 系統情報公表の考え方 — not the インバランス料金
  系統需給情報 (A-1 …) behind `fct_area_demand_generation_actual`. It is a display
  product: values are integer 万kW (1 万kW = 10 MW), the hourly value is the
  1時間平均 of the hour, and TEPCO does not systematically revise past days
  (「過去の需要実績の修正をする場合に、実績修正のお知らせや修正前の需要実績の提供は
  いたしません」). TEPCO also warns that 端数処理の関係で1時間値と5分値の平均が一致しない.
- **Why we load it**: it is the only public Tokyo-area demand history before
  2022-04-01 (A-1 starts with the imbalance regime), so it is the source of a
  year-ago load feature for the first year of the A-1 history. Only the hourly
  table is loaded; see [§7](#7-not-ingested-the-5-minute-table).
- **Coverage**: hourly 2016-04-01 → yesterday (the previous day is posted ~06:00
  and revised ~18:30); the 5-minute table exists from 2022-04-01 only.

## 2. Files and URLs

| Packaging | URL | Content | Period |
|---|---|---|---|
| Yearly file (immutable) | `https://www.tepco.co.jp/forecast/html/images/juyo-YYYY.csv`, YYYY = 2016 … 2022 | one CP932 CSV per **calendar** year: `DATE,TIME,実績(万kW)`, 8,760 hourly rows (2016 starts 2016-04-01) | 2016-04-01 → 2022-12-31 |
| Monthly archive | `https://www.tepco.co.jp/forecast/html/images/YYYYMM_power_usage.zip`, 2022-04 → current month (53 zips ≈ 4.3 MB as of 2026-08) | flat daily members `YYYYMMDD_power_usage.csv` (~9.5 KB each) in the multi-section layout of [§3](#3-daily-file-layout-2022-04-); the current month's zip holds the finished days through yesterday | 2022-04-01 → yesterday |

The yearly 2022 file overlaps the archives for April–December 2022; the loader
keeps yearly rows before **2022-04-01** only, so the daily files win
(`power_usage.DAILY_FILES_FROM`).

## 3. Daily file layout (2022-04 →)

CP932, CRLF, 345 lines; unpadded dates (`2022/4/1`) and hours (`9:00`).

| Lines | Section |
|---|---|
| 1 | `2022/4/1 23:55 UPDATE` — the file's stamp (read into `file_updated_at`) |
| 2–12 | ピーク時供給力 / 予想最大電力 / 使用率ピーク時 header blocks (headline numbers) |
| 14 | `DATE,TIME,当日実績(万kW),予測値(万kW),使用率(%),供給力(万kW)` — the **hourly table** header |
| 15–38 | 24 rows, `TIME` = `0:00` … `23:00` = the hour starting then |
| 39 | blank — ends the hourly table |
| 40–53 | 最大使用率 and 翌日のピーク時供給力 blocks |
| 55 | `DATE,TIME,当日実績(５分間隔値)(万kW),太陽光発電実績(５分間隔値)(万kW),太陽光発電量(電力使用量に対する割合)(%)` — the **5-minute table** header |
| 56–343 | 288 rows, `0:00` … `23:55` |

`当日実績(万kW)` is the hourly actual; `予測値(万kW)` is TEPCO's hourly demand
forecast *as of the stamp* — the day's last intraday revision, not a day-ahead
forecast (the same caveat as the archived A-2 files); `使用率(%)` and
`供給力(万kW)` are the day's usage rate and supply capacity per hour. TEPCO writes
`0` for hours not yet 確定; the archived files contain none.

The yearly files are three lines of preamble (`2018/1/1 18:10 UPDATE`, a blank
line, `DATE,TIME,実績(万kW)`) followed by the hourly rows — the same table with
the actual only.

## 4. Quirks

- **Two packagings, one series.** The hourly `実績` / `当日実績` value is the same
  measurement before and after 2022-04-01 (verified on the 2022 overlap: the
  yearly file's April–December rows equal the daily files'). TEPCO's note that
  pre-2022-04 data 「2022年4月1日以降の実績データとは異なっております」 refers
  to the *content* of the older packaging (demand only, no 予測値 / 供給力 / 5-minute
  table), not to a break in the series.
- **Hourly ≠ mean of the 5-minute values.** Over 2022-04 → 2026-08 the hourly
  value sits 3.4 万kW above the mean of its twelve 5-minute values on average
  (MAE 6.2 万kW) — more than rounding; the 5-minute series is a separate 速報
  measurement.
- **Not revised.** Past days are never re-issued, unlike A-1; the yearly files
  are immutable (their stamps are 2018-01-01 / 2024-01-01 generation dates).

## 5. Comparison with the A-1 series (2022-04-01 → 2026-08-27, 38,621 hours)

Hourly `当日実績` against `fct_area_demand_generation_actual` aggregated to the
hour (the two half-hours' kWh = mean kW over the hour; `h:00` = time codes
2h+1, 2h+2 — a ±1 h shift gives MAE 118 vs 2.5 万kW):

| FY | Hours | Bias | MAE | MAPE | Exact after rounding |
|---|---:|---:|---:|---:|---:|
| 2022 | 8,760 | −0.03 | 2.08 | 0.070 % | 26 % |
| 2023 | 8,784 | +0.07 | 0.41 | 0.014 % | 82 % |
| 2024 | 8,760 | +0.73 | 1.37 | 0.042 % | 35 % |
| 2025 | 8,741 | +0.97 | 2.22 | 0.069 % | 26 % |
| 2026 (to Aug) | 3,576 | +1.50 | 3.28 | 0.108 % | 21 % |

Overall MAE 1.68 万kW (0.053 % of the 3,179 万kW mean), bias +0.53, 58 % of
hours within ±1 万kW. Behind the headline:

- **2022-04-01 → 04-14**: A-1 runs 27–47 万kW *above* でんき予報 in every hour
  (174 on 04-14 h10) — the same days as A-1's scientific-notation files; A-1's
  opening fortnight is a different vintage.
- **From mid-2025, A-1 has an 18:00–19:00 defect**: time codes 37 and 38 carry
  the same offset (±60–190 万kW, 2–4 %, both signs, not a period shift) on 166
  of the 514 days from 2025-04 to 2026-08, rising from 1 day/month to 18–19.
  でんき予報's hourly and 5-minute values agree with each other there (median
  4 万kW apart) while A-1 is 37 万kW off — A-1 is the odd one.
- Excluding those, the two drift apart slowly: bias +0.04 (FY2023) → +1.26 万kW
  (FY2026), daytime hours +0.9–1.0, MAE 0.4 → 1.75 — ≤ 0.06 % of level.
- A flat ½/½ split of an hourly mean misses A-1's true half-hours by MAE
  30.9 万kW (0.97 %), p90 65, worst 08:00–09:00 (78); the 5-minute series
  reproduces the within-hour split to 2.6 万kW.

## 6. Downloading and loading with `power_market_analytics.tepco.power_usage`

```python
from power_market_analytics.tepco.power_usage import TepcoPowerUsageDownloader

downloader = TepcoPowerUsageDownloader()            # data/tepco/power_usage
paths = downloader.download_all()                   # yearly files (cached) + every monthly zip
```

`TepcoPowerUsageDownloader` extends the shared `AreaActualsDownloader` with
the yearly files: `download_yearly(year, force=False)` fetches
`csv/juyo-YYYY.csv` once — a response is cached only if it carries the hourly
header, parses, and covers every day of the year (2016 from 04-01) with 24
hours each, since a cached gap would survive every refresh without
`--force-yearly` — and `download_all(force_yearly=False)` runs the yearly
files then the monthly archives, whose daily members are extracted into the
same `csv/` folder (on the 1st of a month the running month is skipped: it
has no finished day yet; a settled month — last day before yesterday — must
hold a member for every day, the running month may be partial). `TepcoPowerUsageCsvLoader` (a `CsvLoader`) reads
each file with `parse_hourly` — the hourly table under the first accepted
header line, ending at the first blank line, so the 5-minute table is never
read; every day in the block must cover hours 0–23 exactly once and a daily
file exactly one date, so a truncated member fails the load instead of
publishing a day with missing hours — drops yearly rows on/after 2022-04-01,
and hands the contract
`conf/schemas/tepco_power_usage_hourly.yaml` string columns named
`__target_date`, `__hour_start`, `__demand_mankw`, `__forecast_mankw`,
`__usage_rate_pct`, `__supply_capacity_mankw`, `__file_updated_at`,
`__source_file`. Grain `(target_date, hour_start)` is enforced at load time;
an unknown header line fails the load. Entry points:
`scripts/download_tepco_power_usage.py` (`--force-yearly`) and
`scripts/load_tepco_power_usage.py`; `just refresh-tepco-power-usage` runs both
and `dbt build` (`just refresh-tepco` refreshes this and the A-1 actuals
together).

Warehouse path: `pma_raw.tepco_power_usage_hourly` →
`stg_tepco__power_usage_hourly` (as-is) → `std_tepco__power_usage_hourly`
(typed time axis — `delivery_date`, `hour_start` 0–23 as published,
`hour_ending` 1–24 for the JMA / MSM / OCCTO convention, `delivery_datetime`
= hour start, `fiscal_year` — and the four published measures as integer
万kW; the daily-file 予測値 / 使用率 / 供給力 are null before 2022-04-01, which
a test pins; `demand_mankw` is tested ≥ 1 rather than nulled like A-1's
sentinel, because TEPCO never re-issues a day and a zero would never
self-heal; the singular test
`assert_std_tepco__power_usage_hourly_calendar_complete` requires the
history to be gapless) → `fct_area_power_usage_hourly` (grain `date_key ×
hour_of_day × area_key`; `demand_kwh` = 万kW × 10,000, energy over the hour
in the A-1 fact's unit, additive; the other three measures stay in `std`).
The fact is this series alone — it is *not* stitched with the A-1 series
after 2022-04. Its `hour_of_day` references `dim_delivery_hour`, the 24-row
shrunken rollup of `dim_delivery_period`, so the two facts drill across:
`fct_area_demand_generation_actual` summed per
`dim_delivery_period.hour_of_day` is the hourly kWh comparable to this
fact's `demand_kwh` (the comparison in [§5](#5-comparison-with-the-a-1-series-2022-04-01--2026-08-27-38621-hours)
is exactly that join).

## 7. Not ingested: the 5-minute table

The 288-row block below the hourly table (`当日実績(５分間隔値)(万kW)`,
`太陽光発電実績(５分間隔値)(万kW)`, `太陽光発電量(電力使用量に対する割合)(%)`,
2022-04-01 →) is parsed past, not loaded. If it is ever needed — it gives a
true 30-minute shape from 2022-04 and the only public PV series for the area —
it should become its own raw table at 5-minute grain, not more columns here.
