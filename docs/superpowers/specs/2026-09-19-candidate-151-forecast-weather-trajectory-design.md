# The forecast morning temperature trend (#151) — design

Date: 2026-09-19. Status: **approved by the researcher on 2026-09-19 with one change, and
implemented**: the temperature trend only (decision 1). Branch:
`feature/issue-151-morning-temperature-trend`. Feature candidate
[#151](https://github.com/hankehly/power-market-analytics/issues/151), suggested by Codex on
2026-09-13.

## 1. Goal

How fast the delivery day's morning warms: the trend of the population-weighted forecast
temperature over 06:00 to 10:00, from the MSM forecast for D.

The column only. No preset and no backtest: an experiment tests it later in a batch.

## 2. Decisions

1. **The trend only.** The issue also asked for the forecast solar radiation added up from
   midnight through the target hour. The researcher's ruling: approve
   `DAILY_TREND(MEAN(forecast_temperature_c, weight=population), time=06:00-10:00)` alone and
   remove the solar radiation from the scope. So `ftr_hour_msm` is not touched and `CUM_SUM`
   is not added to `docs/Feature-Naming.md`.
2. **The trend goes in `ftr_day_msm`, not `ftr_hour_msm`.** The issue names the hour mart
   and says the trend "may be repeated on every hour". It is one value per day, so it
   belongs at the day grain, next to #132's summaries. Feast joins a day feature onto all
   48 periods anyway, so a preset reads it the same way.
3. **Physical name, which never changes:** `morning_trend_popw_forecast_temperature_c`.
4. **06:00 to 10:00 means the four hours ending 07:00, 08:00, 09:00 and 10:00.** The hour
   mart labels an hour by its end. `ftr_day_actuals` uses the same window on the half-hourly
   load, time codes 13 to 20.
5. **The trend needs those four hours and no others.** Null unless all four have a
   temperature. #132's four summaries need the whole day; the trend does not, so a day
   without hour 5 has a trend and no maximum.

## 3. Definition

The least-squares slope of the temperature against the hour, °C per hour, over the four
hours. With `t` the hour ending and `x` the temperature, `n = 4`:

`(n Σtx − Σt Σx) / (n Σt² − (Σt)²)`

This is `DAILY_TREND` as `docs/Feature-Naming.md` defines it, without the factor 2 that turns
a slope per period into a slope per hour.

**It is computed over differences, a change from the draft of this spec.** With the hours
fixed at 7 to 10 the least-squares weights are `(t − 8.5) / 5`: −0.3, −0.1, 0.1, 0.3. So the
slope is

`(3 (x10 − x7) + (x9 − x8)) / 10`

The same number, and better conditioned: the textbook form subtracts two large, nearly
equal sums. On 24.3, 23.9, 23.1, 22.6 the exact slope is −0.59. The textbook form gives
`-0.5900000000000318`, off by 3e-14. The difference form gives `-0.5899999999999995`, within
one unit in the last place. It is arithmetic over four named values, so it has no order of
addition to fix. The draft had proposed the sums through `aggregate()` on a sorted array.

The row keeps the mart's `available_at`: the vintage's, reference + 4 h.

## 4. Measured on the marts, 9 areas, 2019-04-01 to 2026-09-20

Before the build, with Spark's own `regr_slope` as the reference:

| | |
|---|---|
| Area-days | 24,570, each with all four morning hours |
| Trend, °C per hour: min / 5 % / median / 95 % / max | −2.60 / 0.19 / 1.10 / 2.01 / 3.33 |
| Days with a falling morning, trend below 0 | 474, 2 % |

## 5. Build

- `ftr_day_msm`: one column, from the four morning hours of `ftr_hour_msm`.
- dbt unit tests, written first: 20, 22, 23, 27 °C gives `(3·7 + 1) / 10 = 2.2`; a flat
  morning gives 0; a falling one `-0.5899999999999995`; a morning with three hours gives
  null; a day without hour 5 has its trend while its four summaries are null.
- The check against the real data: the trend against `regr_slope` on every row, and no
  earlier column of the mart moved, against a copy taken before the build.
- `just feature-views`, the fixture's column in `tests/conftest.py`, `CLAUDE.md`.

## 6. Out of scope

The cumulative forecast solar radiation, by decision 1. The daily max, min, mean and hour of
the maximum are #132's and are built. A trend over any other window.
