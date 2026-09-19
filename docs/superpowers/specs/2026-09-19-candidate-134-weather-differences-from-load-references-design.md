# Weather differences from the load references (#134) — design

Date: 2026-09-19. Status: **for the researcher's review**. Feature candidate
[#134](https://github.com/hankehly/power-market-analytics/issues/134), suggested by Codex on
2026-09-13. Nothing is built.

## 1. Goal

The presets read the load of reference days: D-2, D-7 and the rank-1 similar day. The
issue asks for the weather of those days next to them, and for how D's forecast differs
from it. Four features, as the issue describes them:

- the population-weighted observed temperature at the same hour on D-2 and on D-7;
- D's population-weighted forecast temperature minus the observed one on D-7;
- the same minus the observed one on the rank-1 similar day.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Measured first

Tokyo, the 8,760 hours of 2025, none null. The forecast is `popw_forecast_temperature_c`;
the observed temperature is weighted the same way.

| D's forecast minus the observed temperature of | Mean absolute, °C | 95th percentile, °C |
|---|---|---|
| D-2 | 2.52 | — |
| D-7 | 3.10 | 8.24 |
| The rank-1 similar day | 1.06 | 2.99 |

- The similar day is much closer in temperature than D-7. It should be: the selector's
  distance includes the temperature.
- The two differences correlate at 0.29. They are not the same signal.

Facts for the experiment, not a verdict on the candidate.

## 3. Decisions for the researcher

1. **The area's observed hourly temperature becomes a curated fact.** #150 built the
   population-weighted observed temperature as a CTE inside `ftr_hour_jma_obs`. This
   candidate needs it in two more places. So it moves to a curated model,
   `fct_jma_weather_area_hourly`, grain `area_key × observed hour`, with a contract and a
   uniqueness test, and `ftr_hour_jma_obs` reads it. #150's three columns must not move by a
   bit; the build proves it.
2. **Three marts, by grain and by source, not the one new mart the issue names.**
   - The two lags go in `ftr_hour_jma_obs`: they are observations at the hour.
   - The D-7 difference goes in `ftr_hour_msm`: it is one value per hour and vintage of the
     forecast. `ftr_hour_msm` then reads the new fact, not another mart.
   - The similar-day difference goes in `ftr_period_similar_day`: it is one value per
     scoring run, since each run picks its own rank-1 day. That mart holds Tokyo only.

   A combined demand-weather mart, the issue's other option, would mix the hour grain with a
   per-run grain. Publishing the similar day's weather from the fit-and-score job, its third
   option, would need a new scoring run and a dropped `pma_ml.similar_day`. The reference
   date is in the mart already, so a join in dbt is enough, as #138's lag was.
3. **Physical names, which never change.**
   - `lag_2d_popw_temperature_c`, `lag_7d_popw_temperature_c`
   - `popw_forecast_temperature_minus_lag_7d_c`
   - `popw_forecast_temperature_minus_similar_day_rank1_c`
4. **On a same-holiday day the similar-day difference is defined.** The reference is the
   same holiday last year, and its weather is observed. Only the distance is null there.
5. **Temperature only**, as the issue says: humidity and solar radiation "can follow after
   temperature is tested".
6. **`available_at` is the greatest of the inputs**, as the issue lists them: the forecast's
   vintage, the reference day's observation and, for the similar-day one, the scoring row's
   own. The observation of D-7 or of a day a year back is long public, so the two forecast
   differences keep the vintage's instant. The D-2 lag is public one hour after its hour
   ends, the mart's present rule.

## 4. Definition

Expressions, as the issue wrote them:
`LAG(MEAN(temperature_c, weight=population), 2d)`, the same with `7d`,
`MEAN(forecast_temperature_c, weight=population) - LAG(MEAN(temperature_c, weight=population), 7d)`,
and `MEAN(forecast_temperature_c, weight=population) - SIMILAR_DAY(MEAN(temperature_c, weight=population), gap=(2d, 335d), window=(30, 60), rank=1, holidays=last_year)`.
No primitive is new. The same census vintage and station weights are on both sides of each
difference, as the issue asks: both come from the latest vintage.

Each difference is one subtraction of two values that are each added in a fixed order, so it
has no order of its own to fix. A period reads the hour that holds it, `(time_code + 1) // 2`.

## 5. Build

- `fct_jma_weather_area_hourly`, with its contract and tests, then `ftr_hour_jma_obs` on top
  of it.
- The four columns in the three marts.
- dbt unit tests, written first: each lag lands on D from its day; each difference is the
  hand-computed subtraction; a missing observation gives null; a same-holiday row has its
  difference; two scoring runs with different rank-1 days give different values.
- The check against the real data: the four columns against the computation of section 2;
  #150's three columns identical to the bit; no earlier column, row or `available_at` of the
  three marts moved, against copies taken before the build.
- `just feature-views`, the fixture's columns in `tests/conftest.py`, `CLAUDE.md`.

## 6. Out of scope

Humidity, rain and solar radiation differences, by decision 5. The similar day's ranks 2 and
3. A Kansai similar day: the selector runs for Tokyo only.
