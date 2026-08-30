# R-004 — Year-ago load on the prior-year reference date

- **Status:** In progress
- **Last updated:** 2026-08-31 (E-001 candidate run in progress)
- **Created:** 2026-08-31
- **Triggering observations:**
  [O-002 — Over-forecast on the working day before お盆](research/demand/observations.md#o-002),
  [O-003 — 建国記念の日 under-forecast](research/demand/observations.md#o-003)
- **Related investigations:**
  [R-003 — Day type as a categorical feature](research/demand/R-003-day-type-feature.md)
  (this investigation starts from its `lightgbm_msm_popw_daytype` candidate,
  the demand baseline)

## Question

Does giving the model the load of the same day one year earlier — the
warehouse's `dim_date.prior_year_reference_date`, aligned by weekday and
holiday rather than by calendar date — improve Tokyo-area day-ahead demand
forecasts, in particular on the days where the D-7 lag or the day type alone
misleads it?

## Motivation

The researcher's stated remedy for O-002 and O-003 is a same-day-previous-year
load feature:

- [O-002](research/demand/observations.md#o-002): on 2025-08-12 and
  2026-08-12 — a working day squeezed between a weekend and お盆 — the
  baseline over-forecasts by 3.9 M and 2.8 M kWh per period, and the SHAP
  decomposition attributes the excess to `lag_7d_demand_kwh`: the same
  weekday one week earlier was an ordinary working day. The same weekday one
  year earlier sits in the same position relative to お盆.
- [O-003](research/demand/observations.md#o-003): on 建国記念の日 2026-02-11
  (and 2025-02-11) the baseline under-forecasts by 3.1 M kWh per period with
  `day_type` the dominant negative contribution: the holiday category pulls
  the forecast toward the average holiday, which is lower than this one.
  The same holiday one year earlier carries that holiday's own level.

The inputs were built for it. `dim_date.prior_year_reference_date` (PR #29,
2026-08-31) names, for every day of the spine, the day one year earlier that
stands for it: the same weekday 52 weeks back for a working day (shifted to
D−357, then D−371, when that day is a holiday), D−364 for a weekend, the
same-named holiday within 14 days of the same calendar date a year earlier
for a holiday (else the nearest non-working day). The researcher's decisions
on 2026-08-31: the reference is never null (the spine's first year resolves
to 2015 dates), D−357 is tried before D−371, and a bridge-day flag is left to
its own investigation. `fct_area_power_usage_hourly` (PR #18, 2026-08-30)
holds the TSO でんき予報 hourly load from 2016-04-01 — the only public Tokyo
area demand before the A-1 series begins in 2022-04 — and the researcher's
decision is to take the year-ago load from that series **alone** for the
whole history, not to stitch it with the A-1 fact after 2022-04 (the two
differ by 0.05 % MAE over their overlap; see the [TEPCO power-usage
doc](TEPCO-Power-Usage-Retrieval.md#5-comparison-with-the-a-1-series-2022-04-01--2026-08-27-38621-hours)).
That is what gives the first year of A-1 training rows (2022-04 → 2023-03) a
year-ago value; with the 730-day training window the earliest window of the
matched backtest starts in 2022-08.

## Current predictive hypothesis

> We believe that adding the year-ago load — the hourly load on
> `dim_date.prior_year_reference_date` at the hour containing the period, per
> period — to `lightgbm_msm_popw_daytype` will reduce out-of-sample MAE,
> because the two motivating error patterns (O-002, O-003) are days whose
> level one week earlier, or whose day-type average, does not represent them,
> while the same weekday or the same holiday one year earlier does.

## Scope and constraints

- **Forecast target:** the 48 half-hourly `demand_kwh` values of
  `fct_area_demand_generation_actual` for day D, Tokyo area (`--area tokyo`)
- **Information cutoff:** D-1 at 09:30 JST; usable demand history = delivery
  days ≤ D-2; observed-weather features use complete observation days ≤ D-2
  at 東京 s47662; forecast features use the MSM vintage referenced 21:00 JST
  D-2 population-weighted over the area's 21 weighted stations; the day type
  and the prior-year reference of D are known from the calendar; the year-ago
  load is a year old
- **Baseline:** `lightgbm_msm_popw_daytype` — the R-003 E-001 candidate as
  re-run with SHAP contributions on 2026-08-26,
  [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae)
  (MAE 594,325 kWh, identical to the R-003 run `7ce89125…`), the demand
  baseline since 2026-08-26, compared as run (not re-run)
- **Primary metric:** MAE (kWh per 30-minute period)
- **Important segments:** the two motivating days and their kind — the
  working day before お盆 (2025-08-12, 2026-08-12) and 建国記念の日
  (2025-02-11, 2026-02-11); holidays by kind; the day types as no-harm
  checks; the worst days; day part and calendar month as consistency checks
- **Evaluation method:** rolling out-of-sample backtest over identical
  delivery dates and training rows for baseline and candidate
  (`--start-date 2024-08-18 --end-date 2026-08-17`, no `--train-start`,
  exactly the baseline run's flags); accuracy rows in
  `fct_demand_forecast_accuracy` after `just dbt build --select
  +fct_demand_forecast_accuracy +fct_demand_forecast_contribution`

## E-001 — Add the year-ago load on the prior-year reference date

### Why this experiment

It is the direct test of the researcher's remedy: the same model, the same
rows, the same refit schedule and one additional column whose values already
exist in the warehouse (`dim_date` × `fct_area_power_usage_hourly`). If the
year-ago day carries the information the two observations point at, this is
where it shows first.

### Experiment hypothesis

Adding `lag_1y_demand_kwh` to the `lightgbm_msm_popw_daytype` feature set
will lower the error on the working day before お盆 and on 建国記念の日, and
lower overall MAE on the matched window, without a material deterioration on
any day type.

### Change

- **Feature** — `lag_1y_demand_kwh`: the hourly `demand_kwh` of
  `fct_area_power_usage_hourly` on D's `dim_date.prior_year_reference_date`
  at the hour containing the period (`hour_ending = (time_code + 1) // 2`,
  the alignment of the temperature features), divided by 2 so the hour's
  energy is spread evenly over its two delivery periods and the value sits on
  the target's scale (kWh per 30-minute period). Loaded once for the whole
  history (`load_area_hourly_load` → `AreaHourlyLoad`, grain load day × hour;
  `load_prior_year_calendar` → `PriorYearCalendar`, grain day) and joined to
  every training and prediction row (`join_prior_year_load`); a row without a
  year-ago hour is dropped and a target day without one is unforecastable,
  like every other feature — for Tokyo that never happens, the hourly series
  being gapless from 2016-04-01.
- **Strategy** — `lightgbm_msm_popw_daytype_lag1y`
  (`LightGbmMsmPopWeightedDayTypeLag1yStrategy`): the baseline's features plus
  `lag_1y_demand_kwh`; model parameters, refit cadence, the population
  weights (2020 census), the temperature features and the categorical day
  type are unchanged, so the year-ago load is the only difference from the
  baseline. The run logs the calendar's rule mix
  (`prior_year_reference_rules`), the per-period scale
  (`lag_1y_periods_per_hour = 2`) and the hourly history's span
  (`lag_1y_hourly_load_span`).

### Expected evidence

- Lower error on 2025-08-12 / 2026-08-12 and on 2025-02-11 / 2026-02-11 than
  the baseline's (+3.86 M / +2.84 M and −3.07 M kWh per period)
- Lower overall MAE, with the 95 % bootstrap interval of the daily paired
  MAE difference excluding zero
- No material deterioration of any day type
- The candidate's SHAP decomposition on those days attributing a material
  share to `lag_1y_demand_kwh` in the corrective direction
- An overall MAE that is not lower, or a gain confined to a handful of days
  with the bulk of the window unchanged or worse, would make the hypothesis
  less plausible

### Decision rule

Keep the year-ago load if overall MAE is lower with the interval excluding
zero and no day type deteriorates materially. Refine if the motivating days
improve but overall MAE does not (a holiday-window or bridge-day flag — the
researcher's other idea, deferred on 2026-08-31 — would then be the next
experiment), or if the gain is concentrated in one season. Treat the result
as inconclusive if the interval includes zero, and reject the change if
overall MAE is higher with an interval that excludes zero.

### Execution

- **MLflow experiment:** `demand`
- **Baseline run:** `lightgbm_msm_popw_daytype-tokyo`
  [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae)
  — compared as run. The candidate runs on this investigation's code
  version; the only shared code that changed is additive (new frames,
  loaders and a strategy subclass), so the baseline's forecasts are
  unaffected.
- **Candidate run:** `lightgbm_msm_popw_daytype_lag1y-tokyo` — started
  2026-08-31 with the baseline's flags; run id recorded under *Results* when
  it completes
- **Code or pull request:** branch `feature/demand-r004-lag-1y`, stacked on
  `feature/dim-date-prior-year-reference-date` (PR #29)
- **Matched window:** 729 delivery days 2024-08-18..2026-08-17, the same
  training rows and refit schedule as the baseline (both skip 2025-06-21 for
  its D-7 lag in the 2025-06-14 TSO hole; the year-ago load itself is never
  missing on the window)

### Results

_Pending the candidate run._
