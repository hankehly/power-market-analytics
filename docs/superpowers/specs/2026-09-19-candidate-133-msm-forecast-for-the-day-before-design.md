# The day before delivery: the MSM forecast for D-1 (#133) — design

Date: 2026-09-19. Status: **for the researcher's review, with a cost decision first**
(section 2). Feature candidate
[#133](https://github.com/hankehly/power-market-analytics/issues/133), suggested by Claude on
2026-09-15. Nothing is built.

## 1. Goal

The population-weighted forecast temperature for D-1 at the same hour, from the same MSM run
that forecasts D, as the issue describes it. That run is the 12 UTC D-2 run. Its leads 16 to
27 are the hours ending 13:00 to 24:00 on D-1.

The column only. No preset and no backtest: an experiment tests it later in a batch.

## 2. The decision that comes first: the history

**What the code already does**, read on 2026-09-19 in `ingestion/msm/vintage.py`. For each
delivery day the downloader fetches three files of the run. The first, `FH16-33`, holds
leads 16 to 33, and the pipeline reads 28 to 33 of them. So the 12 leads this candidate
needs are in a file that is already fetched every day. The GRIB files are deleted after the
extract, and the cached extracts hold leads 28 to 51 only.

| | New data only | Backfill from 2022-04-01 | Backfill from 2019-04-01 |
|---|---|---|---|
| Extra download from now on | none | none | none |
| Files to fetch again | none | `FH16-33` of 1,630 days | `FH16-33` of 2,730 days |
| Size, at 78.7 MB a file | 0 | about 128 GB | about 215 GB |
| Time at 3.5 MB/s, the pace of 2026-08 | 0 | about 10 h | about 17 h |
| Time at 30 MB/s, the pace of 2026-09 | 0 | about 1.2 h | about 2 h |
| The feature's first day | the day it ships | 2022-04-01, the demand history's | 2019-04-01, the archive's floor |

- The issue's note put the cost at all three files of a day, 157 MB, about 54 GiB a year.
  Only `FH16-33` is needed, half of that.
- **The downloader cannot fetch one file of a day today.** `--force` fetches all three. A
  backfill needs a small option for it. That is part of this build if a backfill is chosen.
- With new data only, a 730-day training window holds the feature on every row about two
  years after it ships. Until then most training rows are null, which LightGBM takes as
  NaN since 2026-09-13.

Disk, time and the wait are the researcher's to weigh. Claude's suggestion, 2026-09-19:
backfill from 2022-04-01. It is the demand task's own history, and earlier days cannot
train a demand model.

## 3. Other decisions for the researcher

1. **Hours 1 to 12 are null.** The run's leads 16 to 27 start at 13:00 on D-1. The morning of
   D-1 is leads 4 to 15, in a fourth file, `FH00-15`, that the pipeline has never fetched.
   The issue asks for "from 13:00 JST", so this spec leaves the morning null. Fetching
   `FH00-15` too would double the backfill and add a download every day.
2. **Physical name, which never changes:** `lag_1d_popw_forecast_temperature_c`. Expression,
   the issue's: `LAG(MEAN(forecast_temperature_c, weight=population), 1d)`.
3. **The existing columns must not see the new rows.** The new leads are rows of the fact
   with a valid date of D-1 under D's vintage. Left alone, `ftr_hour_msm` would gain a
   second, half-day vintage for every afternoon, and `ftr_day_msm` a row of nulls for each.
   So `ftr_hour_msm` keeps reading leads 28 to 51 for its present columns, and only the
   new column reads leads 16 to 27. The marts' rows do not change.
4. **`available_at` is the vintage's**, reference + 4 h, about 01:00 on D-1: the same run
   as D's forecast, so the row's `available_at` does not move.

## 4. Build

- `ingestion/msm/vintage.py`: `FH16-33` reads leads 16 to 33. The extract and the load
  contract gain nothing but rows.
- If a backfill is chosen: a downloader option to fetch one band of a day, and the run.
- `fct_jma_msm_weather_forecast_hourly`: the lead, or a flag, so a model can tell the two
  kinds of row apart.
- `ftr_hour_msm`: the present columns filtered to leads 28 to 51; the new column from leads
  16 to 27 of D's vintage, shifted onto D at the same hour, weighted as the others are.
- Tests first. Python: the vintage's leads and the one-band option, with the injected
  session, no real HTTP. dbt unit test: hours 13 to 24 carry D-1's value from D's vintage,
  hours 1 to 12 are null, and the present columns ignore the new rows.
- The check against the real data: no earlier column, row or `available_at` of
  `ftr_hour_msm` and `ftr_day_msm` moved, against copies taken before the build.
- `docs/JMA-MSM-GPV-Retrieval.md`, `CLAUDE.md`.

## 5. Out of scope

Humidity, rain and radiation for D-1: the same twelve leads hold them, and they can follow
the temperature. The morning of D-1, by decision 1. The observed weather of D-1, which
stops at 09:30: a different source.
