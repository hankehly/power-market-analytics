# Wind and solar generation lags (#135) — design

Date: 2026-09-19. Status: **approved by the researcher on 2026-09-19 with one change, and
implemented**: the observed solar radiation is out of scope (decision 1). Branch:
`feature/issue-135-wind-solar-generation-lags`. Feature candidate
[#135](https://github.com/hankehly/power-market-analytics/issues/135), suggested by Claude on
2026-09-15.

## 1. Goal

Two lag features, from the issue: the A-1 fact's wind and solar generation on D-2 and D-7
at the period.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions

1. **No observed solar radiation.** The issue also asked for the representative station's
   observed radiation on D-2 and D-7. The researcher's ruling: remove it from the scope,
   because it cannot be weighted appropriately. Measured afterwards, 2026-09-19: of the
   stations that carry an area's population weights, 7 of Tokyo's 21 and 3 of Kansai's 11
   observe solar radiation at all, and they hold 55 % of the weight in each area. The
   draft had proposed one station's value. So `ftr_hour_jma_obs` is not touched.
2. **Physical names, which never change**, after `lag_2d_demand_kwh`:
   `lag_2d_wind_solar_generation_kwh` and `lag_7d_wind_solar_generation_kwh`, bigint.
3. **They follow the demand lags' rules** in `ftr_period_actuals`: the same union of shifted
   actuals, a null where the input is absent, and the row's `available_at` already the
   greatest over the lags it holds.
4. **Demand alone makes the rows.** The mart drops a period without demand, so a generation
   lag can exist only where the demand lag of the same day does. A generation value missing
   on its own gives a null lag and no row.
5. **0 is a value**, no sun and no wind, not a hole.

## 3. Definition

Expressions, as the issue wrote them: `LAG(wind_solar_generation_kwh, 2d)` and
`LAG(wind_solar_generation_kwh, 7d)`. The period mart works on the same time code across
days, so no primitive is new.

## 4. Measured on the fact, from 2022-04-01

| | Tokyo | Kansai |
|---|---|---|
| Periods | 78,336 | 78,336 |
| Null generation and null demand together | 38 | 22 |
| Null generation with demand present, or the reverse | 0 | 0 |
| Negative generation | 0 | 0 |
| Generation, MWh per period: min / max | 0 / 8,992 | 0 / 3,197 |

The nulls are the two partial days the demand lags already meet: Tokyo 2025-06-14, 38
periods, and Kansai 2025-10-12, 22. Generation is missing exactly where demand is.

## 5. Build

- `ftr_period_actuals`: the generation rides the shifts the demand lags use.
- A dbt unit test, written first: a lag lands where the demand lag of its day does; a
  generation value missing on its own gives a null lag with the demand lag present; a 0
  stays 0.
- The check against the real data: each column equals the fact's value at the shifted day
  on every row; no earlier column, row or `available_at` moved, against a copy taken before
  the build.
- `just feature-views`, the fixture's two columns in `tests/conftest.py`, `CLAUDE.md`.

## 6. Out of scope

Observed radiation, by decision 1. Wind and solar split apart: the fact holds their sum.
Forecast radiation: `ftr_hour_msm` has it.
