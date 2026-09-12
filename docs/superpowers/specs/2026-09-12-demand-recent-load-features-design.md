# Recent load features for the demand task — design

Date: 2026-09-12. Status: **approved in chat**, awaiting the researcher's review of this
file. Branch: `feature/demand-recent-load-features`.

## 1. Goal

Add thirteen features built from the area's own recent demand to the feature catalogue,
so a demand preset can read them through Feast, and run one matched experiment with all
thirteen on top of the Tokyo baseline. With y(D, p) the demand on delivery day D at
half-hour period p, the researcher's list is:

| Bracket | Feature |
|---|---|
| Recent demand | y(D−2, p); y(D−3, p) |
| Additional weekly lags | y(D−14, p); y(D−21, p); y(D−28, p) |
| Typical weekly profile | mean of y(D−7, p), y(D−14, p), y(D−21, p), y(D−28, p); their exponentially weighted mean |
| Recent weekly change | y(D−2, p) − y(D−9, p) |
| Recent matching-day-type load | mean at p over the last four available days of D's day type; their exponentially weighted mean |
| Previous complete-day summaries | D−2's mean demand; D−2's maximum demand; D−2's maximum minus minimum |

Demand is `fct_area_demand_generation_actual.demand_kwh`, the 30-minute TSO actuals the
task forecasts. "Available" keeps the catalogue's meaning: public at the row's 09:30 D−1
issue time, decided by Feast's as-of join on `available_at`.

## 2. Decisions

The researcher's answers of 2026-09-12.

1. **Scope: marts, one preset, one matched run.** The columns land in the feature marts
   (so Feast, the Feast UI and `fct_feature_value` see them), one preset adds all
   thirteen to the Tokyo baseline, and one matched pair of runs feeds a new investigation,
   `demand/R-006`. Per-bracket presets are not built now; `--add` / `--drop` cover them.
2. **The exponentially weighted means halve per step**: weights 8, 4, 2, 1 from the most
   recent input to the oldest, normalised over the inputs present. The repo's precedent
   is `wavg_temperature_c`, which halves per day back.
3. **A mean over four inputs is the mean of the inputs present**, weights renormalised over
   them, null only when none is present. The precedent is the population-weighted
   forecast, renormalised over the stations with a value. So the matching-day-type window
   always takes the last four complete days of the type; an incomplete day is skipped, not
   counted as missing.
4. **Two marts, by grain.** The period-grain features extend `ftr_period_actuals`; the
   three D−2 day summaries are a new day-grain mart, `ftr_day_actuals`. One mart per
   source family and grain is the catalogue's rule.
5. **A row's `available_at` is the greatest over the rows it used** (the `available_at`
   macro's rule). The whole row, `lag_7d_demand_kwh` included, is usable only when its
   newest input is public. §6 gives the cost.
6. **The D−9 lag is a tagged column** because the mart carries it for the weekly change;
   it stays out of the preset, which takes exactly the thirteen above.
7. **Day type is the one of `ftr_day_calendar`** (0 Weekday, 1 Weekend, 2 Holiday, a
   holiday winning; research `demand/R-003`). The period mart joins that mart rather than
   repeating the expression, so there is one definition.
8. **A complete day has all 48 periods non-null.** The day summaries and the
   matching-day-type candidates use complete days only. The data has one incomplete day
   per area: TEPCO's frozen 2025-06-14 file (periods 11–48 null) and Kansai's 2025-10-12
   (22 blank periods).

## 3. The columns

`ftr_period_actuals`, grain `area_code × trade_date × time_code` (unchanged):

| Column | Type | Definition |
|---|---|---|
| `lag_2d_demand_kwh` | bigint | y(D−2, p) |
| `lag_3d_demand_kwh` | bigint | y(D−3, p) |
| `lag_7d_demand_kwh` | bigint | y(D−7, p); existing, now nullable |
| `lag_9d_demand_kwh` | bigint | y(D−9, p); tagged, not in the preset |
| `lag_14d_demand_kwh` | bigint | y(D−14, p) |
| `lag_21d_demand_kwh` | bigint | y(D−21, p) |
| `lag_28d_demand_kwh` | bigint | y(D−28, p) |
| `mean_weekly_lags_demand_kwh` | double | mean of the D−7, D−14, D−21, D−28 lags present |
| `ewm_weekly_lags_demand_kwh` | double | (8·y₇ + 4·y₁₄ + 2·y₂₁ + 1·y₂₈) / (8 + 4 + 2 + 1), each term and weight dropped when its lag is absent |
| `change_2d_9d_demand_kwh` | bigint | y(D−2, p) − y(D−9, p); null when either is absent |
| `mean_daytype_4d_demand_kwh` | double | mean at p over the last four complete days of D's day type at or before D−2 |
| `ewm_daytype_4d_demand_kwh` | double | the same four days weighted 8, 4, 2, 1 from the most recent, renormalised over the days present |

Every one is tagged `feature: true`, `categorical: false`. A row exists wherever at least
one of the seven lags exists; a column is null where its input is absent. Today the row
exists only where D−7 exists, and Feast gives NaN for an absent row, so for the baseline
preset the two rules are the same thing.

`ftr_day_actuals`, new, grain `area_code × trade_date`:

| Column | Type | Definition |
|---|---|---|
| `lag_2d_mean_demand_kwh` | double | mean of the 48 `demand_kwh` values of D−2 (kWh per period) |
| `lag_2d_max_demand_kwh` | bigint | their maximum |
| `lag_2d_range_demand_kwh` | bigint | maximum minus minimum |

A row exists only where D−2 is complete. All three tagged, not categorical.

Units are kWh per half-hour period throughout, the fact's unit.

## 4. `ftr_period_actuals`

One union instead of seven joins. The actuals with a non-null demand, joined to
`dim_area` for `area_code`, are exploded over the lag list `(2, 3, 7, 9, 14, 21, 28)` to
rows `(area_code, trade_date = date_key + lag, time_code, lag_days, demand_kwh,
available_at)`, then grouped per `(area_code, trade_date, time_code)`: one `max(case
when lag_days = k …)` per lag column and `max(available_at)` as the lags' availability.
The group is the row spine: a row exists wherever any lag exists.

The two weekly aggregates and the change are plain arithmetic over the named lag columns
in the order written: integer sums of at most four `bigint` values and one division, so
the value is exact and the same on every build; the `ordered_weighted_mean` macro is for
aggregates over rows and is not needed here. The weights are integer literals (a decimal
literal would make the division decimal, the repo's Spark gotcha).

The matching-day-type window, in three steps:

1. **Candidate periods.** Complete days (`count(*) = 48` over non-null periods, with
   `max(available_at)` as the day's availability) joined to `ftr_day_calendar` for their
   `day_type`, then per period the three previous values of the same
   `(area_code, day_type, time_code)` by `lag(demand_kwh, 1..3) over (partition by
   area_code, day_type, time_code order by date_key)` and the day availability's
   `max(...)` over the frame `rows between 3 preceding and current row`. Because every
   candidate is complete, each period sees the same four days.
2. **Lookup.** For every `(area_code, D)` the mart has (the group spine) with D's
   `day_type` from `ftr_day_calendar`, the newest candidate day d with the same
   `day_type` and d ≤ D−2. Built with one window over a union spine: target rows at D and
   candidate rows at d + 2 (the first D they serve), ordered by that date with candidates
   before targets on ties, and `last_value(candidate_date, true)` (ignore nulls) over
   `rows between unbounded preceding and current row`. No distance bound: the fourth most
   recent holiday can lie four months back and is still found.
3. **The two columns** from the candidate period at (d, p): the plain mean over the value
   and the up to three predecessors present, and the 8, 4, 2, 1 weighted mean over the
   same, both renormalised over the present terms in the order written; the window's
   availability rides along.

`final` left-joins the day-type columns to the spine and takes
`greatest(lags_available_at, daytype_available_at)` as `available_at` (Spark's `greatest`
skips nulls). Contract, tests: `unique_combination_of_columns` on the grain, `not_null`
on the keys and `available_at`, `accepted_range` 1–48 on `time_code`, `not_null` dropped
from `lag_7d_demand_kwh`, `expression_is_true` that at least one lag is not null. Unit
tests (dbt `unit_tests`) cover: the shift of each lag with its `available_at`; a hole
producing a null lag, not a missing row; the weekly mean over the lags present; the
change null when one side is absent; the day-type window taking the last four complete
same-type days at or before D−2, skipping an incomplete day and a day of another type;
and `available_at` as the greatest of the rows used.

## 5. `ftr_day_actuals`

The actuals with a non-null demand joined to `dim_area`, grouped by `(area_code,
date_key)` with `having count(*) = 48`, shifted to `trade_date = date_key + 2`:
`avg(demand_kwh)`, `max(demand_kwh)`, `max(demand_kwh) − min(demand_kwh)`,
`max(available_at)`. The 48 values are integers below 2⁵³ in total, so `avg`'s
double sum is exact in any order. Contract and tests as above on the day grain; a unit
test covers a complete day, an incomplete one (no row) and the shift.

`scripts/generate_feature_views.py` picks the mart up from its path and grain
(`day` → entities `area_code`, `trade_date_key`), so `just feature-views` regenerates
`features/views.py` (a new `SparkSource` + `FeatureView`) and
`fct_feature_value.sql` (the day mart broadcast to the 48 periods) with no generator
change.

## 6. Availability and the baseline

Measured 2026-09-12 on the warehouse, per lag: the delivery days whose lagged file was
public only after 09:30 D−1.

| Area | Lag (days) | Delivery days | Late |
|---|---|---|---|
| kansai | 2, 3, 7, 9, 14, 21, 28 | 1,619 | 0 |
| tokyo | 2, 3, 7, 9 | 1,619 | 2 each |
| tokyo | 14 | 1,619 | 1 |
| tokyo | 21, 28 | 1,619 | 0 |

The daily files land about 00:05 on D+1, so y(D−2, p) is public at 00:05 on D−1, before
the issue time. The late Tokyo files are the 2022-12-01 and 12-02 files, re-issued
2022-12-14 (CLAUDE.md, Gotchas). Under decision 5 they hide the whole
`ftr_period_actuals` row, `lag_7d_demand_kwh` included, for the Tokyo delivery days that
use them through any lag or through the weekday window: ten days by hand (2022-12-03 to 12-11 and 12-15; the build's spot check confirms the
count), where today they hide two (2022-12-08 and 12-09). These are
training rows for a run whose 730-day window reaches December 2022, which the Tokyo
window (targets from 2024-08-18) does for its first months. So the experiment's baseline
is a fresh run of `lightgbm_msm_popw_daytype_simday` on the new mart, and the PR reports
that run against the newest baseline run on the old mart, `429eca36…` (2026-09-11, MAE
583,561 kWh, 729 days), to show the size of the shift.

A second, smaller cost: a target day is skipped when any of its 48 rows lacks a feature
(`ForecastUnavailableError`, the existing rule). The TEPCO hole of 2025-06-14 removes
one target day from the baseline (through D−7) and up to seven from the candidate
(D−2, D−3, D−7, D−9, D−14, D−21, D−28 after it). The compare script and the Compare tab
count only the periods both runs scored, so the comparison stays matched; the
investigation records the day counts.

## 7. Preset and the experiment

`tasks/demand/presets.py` gains `RECENT_LOAD_FEATURES`, the thirteen references in the
bracket order of §1:

```
ftr_period_actuals:lag_2d_demand_kwh, ftr_period_actuals:lag_3d_demand_kwh,
ftr_period_actuals:lag_14d_demand_kwh, ftr_period_actuals:lag_21d_demand_kwh,
ftr_period_actuals:lag_28d_demand_kwh,
ftr_period_actuals:mean_weekly_lags_demand_kwh, ftr_period_actuals:ewm_weekly_lags_demand_kwh,
ftr_period_actuals:change_2d_9d_demand_kwh,
ftr_period_actuals:mean_daytype_4d_demand_kwh, ftr_period_actuals:ewm_daytype_4d_demand_kwh,
ftr_day_actuals:lag_2d_mean_demand_kwh, ftr_day_actuals:lag_2d_max_demand_kwh,
ftr_day_actuals:lag_2d_range_demand_kwh
```

and the preset `lightgbm_msm_popw_daytype_simday_lags` =
`LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.with_changes(add=RECENT_LOAD_FEATURES)`, registered in
`PRESETS`. No new categorical. Tokyo-only, like its base.

Runs, in the devcontainer, both with the reference window and no `--train-start`
(run `008868fe…`'s and `429eca36…`'s settings):

```
just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday --area tokyo --start-date 2024-08-18 --end-date 2026-08-17
just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday_lags --area tokyo --start-date 2024-08-18 --end-date 2026-08-17
just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution +fct_demand_forecast_importance
just python scripts/compare_demand_runs.py --baseline <fresh baseline> --candidate <candidate> --mae-by-month-png docs/research/demand/assets/R-006-E-001-mae-by-month.png
```

plus `compare_demand_runs.py --baseline 429eca36… --candidate <fresh baseline>` for §6.

## 8. Research record

`docs/research/demand/R-006-recent-load-features.md` from the investigation template:
triggering observation "None — modeling idea", the feature list of §1 as the change, the
scope block citing the task README's defaults, E-001 with the two runs, the compare
script's tables, the by-month figure, the permutation importance and mean |SHAP| of the
candidate. The hypothesis, interpretation and decision are the researcher's: the
document carries their words or is left for them to fill, never a hypothesis written on
their behalf (the research README's rule). The task README's index gets the R-006 row,
`docs/_sidebar.md` the page.

## 9. Tests and verification

- dbt: the contracts and tests of §4 and §5; `just dbt build` of both marts and
  everything downstream (`fct_feature_value`, whose singular test checks every tagged
  column is unpivoted); a spot check through `dbt show` that a hidden Tokyo day of
  December 2022 carries an `available_at` after its issue time and a normal day one at
  about 00:05 D−1.
- Python: `tests/conftest.py`'s synthetic `ftr_period_actuals` gains the new columns,
  computed in pandas from the fixture's actuals and calendar, and a synthetic
  `ftr_day_actuals`; the three view-name lists in the feature tests gain the day mart;
  `test_demand_presets.py` covers the new preset (name, base, feature order, no
  categorical); `just test` stays at 100 % coverage; `just lint`, `just mypy`.
- Generated files: `just feature-views`, then the CI `--check`.
- Retrieval: a devcontainer check that Feast returns the new columns for a few Tokyo days
  equal to the mart's rows, and NaN on a hidden December-2022 day.
- The runs of §7, their MLflow pages, the Explanation tab of the candidate.

## 10. Docs and follow-through

CLAUDE.md: the marts list (eight, `ftr_day_actuals` added, `ftr_period_actuals`'s
description), the Demand task bullet (the preset and its features), the `feature_marts`
fixture note. `docs/superpowers/README.md` gets this spec's row. The PR body's *Proof*
carries the §6 and §7 numbers. Labels: `enhancement`, `forecasting`, `research`.

## 11. Out of scope

- Per-bracket presets and runs (decision 1).
- Kansai runs: the base preset is Tokyo-only.
- Changing the skip rule for a target day with one missing feature.
- Per-column availability inside a mart row.
