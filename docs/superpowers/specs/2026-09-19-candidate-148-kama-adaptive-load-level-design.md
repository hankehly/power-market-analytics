# KAMA adaptive load level (#148) — design

Date: 2026-09-19. Status: **for the researcher's review**. Feature candidate
[#148](https://github.com/hankehly/power-market-analytics/issues/148), suggested by Codex on
2026-09-13. Nothing is built.

## 1. Goal

Kaufman's Adaptive Moving Average of the same-period demand through D-2, as the issue
describes it: an average whose smoothing moves toward a fast one when recent changes all
go one way, and toward a slow one when the series is noisy.

The column only. No preset and no backtest: an experiment tests it later in a batch.

## 2. Definition

With `x_t` the demand at the period on day `t`, and the issue's `window=10`, `fast=2`,
`slow=30`:

- Efficiency ratio: `ER_t = |x_t − x_{t−10}| / Σ_{i=t−9…t} |x_i − x_{i−1}|`, from 0, all
  noise, to 1, a straight run.
- Smoothing constant: `SC_t = (ER_t (2/3 − 2/31) + 2/31)²`. It runs from 0.00416 to 0.444.
- `KAMA_t = KAMA_{t−1} + SC_t (x_t − KAMA_{t−1})`, seeded with `x` on the first day `ER`
  exists.

This is the definition of the technical-analysis library the issue links. It uses only +,
−, ×, ÷ and an absolute value, so a fixed order of days gives the same double on every
build.

## 3. Measured first: what it does on this series

Tokyo, three periods (04:00, 14:00, 19:00), 2022-04-01 to 2026-09-17, about 1,630 days
each. Computed in pandas.

| | 04:00 | 14:00 | 19:00 |
|---|---|---|---|
| Efficiency ratio, median | 0.198 | 0.170 | 0.187 |
| Efficiency ratio, 90th percentile | 0.446 | 0.364 | 0.418 |
| Smoothing constant, median | 0.034 | 0.028 | 0.031 |
| Half-life that implies, median, days | 20 | 25 | 22 |
| Correlation with `ewm_5d_demand_kwh` | 0.858 | 0.800 | 0.822 |
| Correlation with `ewm_weekly_lags_demand_kwh` | 0.898 | 0.809 | 0.861 |

- **The series runs over consecutive days, so the weekly cycle is in it.** A weekend drop
  and its recovery count as noise. That is why the efficiency ratio is low and the average
  sits near its slow end: a half-life of 20 to 25 days most of the time, against 1.2 days
  at its fastest and 166 at its slowest.
- **It has a long memory.** The same recursion started 60 days before the last day, instead
  of at the first actual, ends 0.2 %, 3.3 % and 1.2 % away.

Facts for the experiment, and for decision 1. Not a verdict on the candidate.

## 4. Decisions for the researcher

1. **Consecutive days, as the issue says, or the days of D's day type?** The issue's
   "same-period demand through D-2" is consecutive days, which gives the behaviour in
   section 3. Run over D's day type, as `mean_daytype_4d_demand_kwh` is, the weekly cycle
   drops out and the ratio measures the trend the issue describes. It is a different
   feature. This spec follows the issue and takes consecutive days. The researcher may
   prefer the other, or both.
2. **The recursion starts at the first actual, not at a fixed look-back.** Section 3 shows
   a 60-day look-back moves the value by up to 3.3 %. A value that depends on where the
   window starts is not the issue's feature. The first weeks of the history are a warm-up.
3. **A missing day is skipped.** The average carries over a TSO hole, and the ratio runs
   over the last 11 values present. The issue asks to confirm this "null-reset behaviour"
   before the experiment: the alternative, starting again after a hole, would throw away
   the memory for one day's gap. Two partial days exist today.
4. **Physical name, which never changes:** `kama_10d_demand_kwh`. Expression, the issue's:
   `KAMA(demand_kwh, gap=2d, window=10, fast=2, slow=30)`, a new primitive in
   `docs/Feature-Naming.md`.
5. **`available_at` is the greatest over every day the recursion has read**, as the issue
   asks, so a day replaced after the issue time cannot leak in. Since 2026-09-13 the
   actuals' `available_at` is a rule, the next day at 00:30, and rises with the date. So on
   real data this is D-2's, and the mart's `available_at` does not move. In a unit test with
   a replaced file, every later row waits for it.

## 5. Build

- `ftr_period_actuals`: the ratio by window functions over each area and period in date
  order, then one `aggregate()` over the period's days sorted by date, carrying the
  average. It is read at D-2, like the 28-day range, and makes no row of its own.
- A dbt unit test, written first, from a Python reference with the same operations in the
  same order: a straight run gives a ratio of 1 and the fast constant; a zigzag gives a
  ratio near 0; a hole is skipped; the first ten days have no value.
- The check against the real data: the column against the pandas computation of section 3
  on the three Tokyo periods, and no earlier column, row or `available_at` moved, against a
  copy taken before the build.
- `just feature-views`, the fixture's column in `tests/conftest.py`,
  `docs/Feature-Naming.md`, `CLAUDE.md`.

## 6. Out of scope

Other windows or constants than the issue's. The same average of the hourly でんき予報
load.
