# The forecast weather trajectory: morning trend and cumulative radiation (#151) — design

Date: 2026-09-19. Status: **approved by the researcher on 2026-09-19 and implemented, in two
steps**. The temperature trend went first (`feature/issue-151-morning-temperature-trend`),
with the cumulative solar radiation left out. The researcher then corrected the reason for
leaving it out, and it was built too (`feature/issue-151-cumulative-forecast-solar-radiation`).
See decision 1. Feature candidate
[#151](https://github.com/hankehly/power-market-analytics/issues/151), suggested by Codex on
2026-09-13.

## 1. Goal

Two features from the MSM forecast for the delivery day D, as the issue describes them:

- how fast the morning warms: the trend of the population-weighted forecast temperature
  over 06:00 to 10:00;
- how much sun has fallen so far: the population-weighted forecast solar radiation added up
  from midnight through the target hour.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions

1. **Both features, after a correction.** The researcher first approved the trend alone and
   removed the solar radiation from the scope, for lack of data to weight it by population.
   That holds for the *observed* radiation: 7 of Tokyo's 21 and 3 of Kansai's 11 weighted
   stations observe it. It does not hold for the *forecast*: every station has the MSM
   radiation, and `popw_forecast_solar_radiation_mjm2` has no null in 589,680 rows. The
   researcher, the same day: "it looks like I made a mistake about the forecasted solar
   radiation. If we have data enough to weight by population properly, please go back and add
   the features I excluded from the scope." So the cumulative radiation was built. #135's
   observed radiation stays out, by the same test.
2. **The trend goes in `ftr_day_msm`, not `ftr_hour_msm`.** The issue names the hour mart
   and says the trend "may be repeated on every hour". It is one value per day, so it
   belongs at the day grain, next to #132's summaries. Feast joins a day feature onto all
   48 periods anyway, so a preset reads it the same way.
3. **Physical names, which never change:** `ftr_day_msm.morning_trend_popw_forecast_temperature_c`
   and `ftr_hour_msm.cum_popw_forecast_solar_radiation_mjm2`. The running total is one value
   per hour, so it stays in the hour mart.
4. **06:00 to 10:00 means the four hours ending 07:00, 08:00, 09:00 and 10:00.** The hour
   mart labels an hour by its end. `ftr_day_actuals` uses the same window on the half-hourly
   load, time codes 13 to 20.
5. **The trend needs those four hours and no others.** Null unless all four have a
   temperature. #132's four summaries need the whole day; the trend does not, so a day
   without hour 5 has a trend and no maximum.

6. **The running total stops at the first missing hour.** From that hour on it is null for
   the rest of the day, a missing row included, so a gap cannot make a total look smaller
   than it is. No hour is missing today.
7. **`CUM_SUM` is a new primitive** in `docs/Feature-Naming.md`: `CUM_SUM(x) by col`, the
   running total of `x` in time order within each value of `col`, the current term included.

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

**Cumulative radiation.** `sum(x) over (partition by area, day, vintage order by hour_ending)`,
MJ/m², kept only while the count of values so far equals the hour. The `order by` fixes the
order of addition: 0.1, 0.2 and 0.3 give `0.6000000000000001` at hour 3, where the same three
added in reverse give 0.6. Expression, as the issue wrote it:
`CUM_SUM(MEAN(forecast_solar_radiation_mjm2, weight=population)) by trade_date`.

Both rows keep their mart's `available_at`: the vintage's, reference + 4 h. The whole of D
is forecast in one run, so the total through hour h uses nothing published later.

## 4. Measured on the marts, 9 areas, 2019-04-01 to 2026-09-20

Before the build, with Spark's own `regr_slope` as the reference:

| | |
|---|---|
| Area-days | 24,570, each with all four morning hours |
| Trend, °C per hour: min / 5 % / median / 95 % / max | −2.60 / 0.19 / 1.10 / 2.01 / 3.33 |
| Days with a falling morning, trend below 0 | 474, 2 % |
| Hourly radiation, MJ/m²: nulls / min / max | 0 / 0.0 / 3.61 |
| Daily total, MJ/m²: min / mean / max | 0.61 / 14.10 / 29.59 |
| Tokyo, mean total through hour 6 / 9 / 12 / 15 / 18 / 24 | 0.07 / 2.25 / 7.79 / 12.89 / 14.39 / 14.41 |

## 5. Build

- `ftr_day_msm`: one column, from the four morning hours of `ftr_hour_msm`.
- dbt unit tests, written first: 20, 22, 23, 27 °C gives `(3·7 + 1) / 10 = 2.2`; a flat
  morning gives 0; a falling one `-0.5899999999999995`; a morning with three hours gives
  null; a day without hour 5 has its trend while its four summaries are null.
- The check against the real data: the trend against `regr_slope` on every row, and no
  earlier column of the mart moved, against a copy taken before the build.
- `just feature-views`, the fixture's column in `tests/conftest.py`, `CLAUDE.md`.

## 6. Out of scope

The daily max, min, mean and hour of
the maximum are #132's and are built. A trend over any other window.
