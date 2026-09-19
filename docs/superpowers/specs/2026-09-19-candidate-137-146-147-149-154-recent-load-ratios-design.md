# Recent-load ratios and positions (#137, #146, #147, #149, #154) — design

Date: 2026-09-19. Status: **approved by the researcher on 2026-09-19 with one change, and
implemented**: #146 and #147 are fractions, not percentages (decision 2). Branch:
`feature/issue-137-recent-load-ratios`. Feature candidates
[#137](https://github.com/hankehly/power-market-analytics/issues/137) (Claude, 2026-09-15,
with a D-2 ratio from Codex, 2026-09-13),
[#146](https://github.com/hankehly/power-market-analytics/issues/146),
[#147](https://github.com/hankehly/power-market-analytics/issues/147),
[#149](https://github.com/hankehly/power-market-analytics/issues/149) and
[#154](https://github.com/hankehly/power-market-analytics/issues/154) (Codex, 2026-09-13).

One spec for the five: each is arithmetic in `ftr_period_actuals` over load the mart already
reads, and all follow from demand/R-006. #148, the adaptive average, is recursive and has
its own spec.

## 1. Goal

Six columns in `ftr_period_actuals`, as the issues describe them:

| Issue | What | Built from |
|---|---|---|
| #137 | D-2's and D-7's load at the period over that day's mean load: the shape without the level | `lag_2d_demand_kwh`, `lag_7d_demand_kwh`, and the two days' means |
| #146 | The fast average minus the weekly average, as a fraction of the weekly one | `ewm_5d_demand_kwh`, `ewm_weekly_lags_demand_kwh` |
| #147 | The week-on-week change from D-9 to D-2, as a fraction of D-9 | `change_2d_9d_demand_kwh`, `lag_9d_demand_kwh` |
| #149 | Where D-2's load sits between the lowest and highest of D-2 … D-29, 0 to 1 | a 28-day window, new |
| #154 | D-7's load minus the median of the four weekly lags | `lag_7d_demand_kwh`, `median_weekly_lags_demand_kwh` |

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions for the researcher

1. **Physical names, which never change.** No unit suffix on a ratio or a fraction.
   - `lag_2d_over_daily_mean_demand`, `lag_7d_over_daily_mean_demand` (#137)
   - `rel_ewm_5d_minus_ewm_weekly_lags_demand` (#146), after the existing
     `ewm_5d_minus_ewm_weekly_lags_demand_kwh`
   - `rel_change_2d_9d_demand` (#147), after the existing `change_2d_9d_demand_kwh`

   The draft had `pct_` on both. With decision 2 that would mislead for good, and the
   researcher chose `rel_`.
   - `lag_2d_position_28d_demand` (#149)
   - `lag_7d_minus_median_weekly_lags_demand_kwh` (#154)
2. **No × 100.** The issues wrote #146 and #147 as percentages. The researcher's ruling:
   no need to multiply by 100 unless necessary, and a tree does not need it. So all six
   are plain ratios, and the two expressions drop their `* 100`.
3. **#137's daily mean is over a complete day only**, all 48 periods, as `ftr_day_actuals`
   defines D-2's mean. The ratio is null otherwise. The D-7 mean is computed inside the
   period mart: the issue does not ask for it as a feature of its own.
4. **#149's window is D-2 … D-29 and includes D-2**, as its expression says, so the position
   is always within 0 to 1. It runs over the values present, the mart's rule for its other
   windows. Null when D-2 is absent or the highest equals the lowest.
5. **A zero denominator gives null**, as the issues say. None occurs today.
6. **Two new primitives** in `docs/Feature-Naming.md`, as #149 proposes: `ROLLING_MIN` and
   `ROLLING_MAX`, the same window's lowest and highest.
7. **`available_at` does not change.** Every input is D-2 or older, and the row already
   waits for its newest lag.

## 3. Definition

Expressions, as the issues wrote them.

- #137: `LAG(demand_kwh, 2d) / LAG(DAILY_MEAN(demand_kwh), 2d)` and the same with `7d`.
- #146: `(EWA(demand_kwh, gap=2d, window=5, step=1d, halflife=1) - EWA(demand_kwh, gap=7d,
  window=4, step=7d, halflife=1)) / EWA(demand_kwh, gap=7d, window=4, step=7d, halflife=1)`.
- #147: `LAG(DIFF(demand_kwh, 7d), 2d) / LAG(demand_kwh, 9d)`.
- #149: `(LAG(demand_kwh, 2d) - ROLLING_MIN(demand_kwh, gap=2d, window=28, step=1d)) /
  (ROLLING_MAX(demand_kwh, gap=2d, window=28, step=1d) - ROLLING_MIN(demand_kwh, gap=2d,
  window=28, step=1d))`.
- #154: `LAG(demand_kwh, 7d) - ROLLING_MEDIAN(demand_kwh, gap=7d, window=4, step=7d)`.

Demand is a whole number of kWh, so every sum, minimum, maximum and median here is exact.
Only the divisions are doubles, and a single division has no order to fix.

## 4. Measured, Tokyo and Kansai, 2022-06-01 to 2026-08-31, 149,088 rows

Computed before the build from the mart's columns and the actuals. #146 and #147 are shown
as measured then, in percent; the built columns are these over 100.

| Column | Null | Min | Median | Max | Std |
|---|---|---|---|---|---|
| #146 fast against weekly, % | 0 | −36.2 | −0.51 | 70.1 | 13.05 |
| #147 week on week, % | 120 | −45.1 | 0.37 | 89.9 | 11.51 |
| #154 D-7 minus the weekly median, MWh | 60 | −10,748 | 22.6 | 9,270 | 1,203 |
| #137 D-2 over its day's mean | 96 | 0.619 | 1.026 | 1.346 | 0.145 |
| #137 D-7 over its day's mean | 96 | 0.619 | 1.026 | 1.346 | 0.145 |
| #149 position in 28 days | 60 | 0.000 | 0.551 | 1.000 | 0.307 |

- No denominator is zero: not the weekly average, not D-9, not a day's mean.
- Every 28-day window holds at least 27 days.
- The two #137 rows agree to three decimals because they draw on the same days, five apart.
- Every null comes from two partial days: Tokyo 2025-06-14, 38 periods without demand, and
  Kansai 2025-10-12, 22. So 60 = 38 + 22 where one lag is read; 120 for #147, which reads
  two; and 96 = 48 × 2 for #137, which drops the whole incomplete day. #146 has none: its
  averages run over the values present.
- So no 28-day window has an equal highest and lowest: all 60 of #149's nulls are accounted
  for.

## 5. Build

- `ftr_period_actuals`: four of the six are arithmetic in the final select over columns the
  mart has. #137 adds the two days' means over complete days. #149 adds the lowest and
  highest over the lags 2 to 29.
- The mart's row set must not change: it has a row wherever a lag exists, and #149's window
  reaches one day further back than D-28. The build keeps today's row rule and lets the
  window read lag 29 without it making a row.
- dbt unit tests, written first, on the mart's present fixture days: each column
  hand-computed; a zero denominator and a missing input give null; D-2 at the window's
  lowest gives 0 and at its highest gives 1; a day of 47 periods gives a null ratio.
- The check against the real data: each column against the arithmetic above on the mart's
  own columns, on every row; and no earlier column, row or `available_at` moved, against a
  copy taken before the build.
- `just feature-views`, the fixture's six columns in `tests/conftest.py`,
  `docs/Feature-Naming.md`, `CLAUDE.md`.

## 6. Out of scope

#148, the adaptive average. D-7's ramp, which #137 mentions: it exists as
`lag_7d_ramp_demand_kwh`. The same transforms of the hourly でんき予報 load.
