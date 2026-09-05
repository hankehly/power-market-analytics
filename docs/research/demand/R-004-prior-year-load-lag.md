# R-004 — Year-ago load from a prior-year reference day

- **Status:** In progress — E-001 (rule-chosen reference date) rejected by the
  researcher on 2026-09-05, its feature and `dim_date` column removed; E-002
  (learned similar-day selector) planned the same day, design spec
  `docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md`
- **Last updated:** 2026-09-05 (question broadened; E-002 planned)
- **Created:** 2026-08-31
- **Triggering observations:**
  [O-002 — The working day between 山の日 and お盆 is heavily over-forecast, driven by the D-7 lag](research/demand/observations.md#o-002-the-working-day-between-山の日-and-お盆-is-heavily-over-forecast-driven-by-the-d-7-lag),
  [O-003 — 建国記念の日 is heavily under-forecast, the day type outweighing the D-7 lag](research/demand/observations.md#o-003-建国記念の日-is-heavily-under-forecast-the-day-type-outweighing-the-d-7-lag)
- **Related investigations:**
  [R-003 — Day type as a categorical feature](research/demand/R-003-day-type-feature.md)
  (this investigation starts from its `lightgbm_msm_popw_daytype` candidate,
  the demand baseline)

## Question

Does giving the model the load of a reference day one year earlier — a day
chosen to stand for the target day — improve Tokyo-area day-ahead demand
forecasts, in particular on the days where the D-7 lag or the day type alone
misleads it?

Broadened on 2026-09-05 after E-001. As first written, the question named one
way of choosing the day: the warehouse's `dim_date.prior_year_reference_date`,
the same weekday or the same-named holiday by rule. E-001 rejected that rule,
not the year-ago load itself. E-002 chooses the day with a learned similar-day
selector instead.

## Motivation

The researcher's stated remedy for O-002 and O-003 is a same-day-previous-year
load feature:

- [O-002](research/demand/observations.md#o-002-the-working-day-between-山の日-and-お盆-is-heavily-over-forecast-driven-by-the-d-7-lag): on 2025-08-12 and
  2026-08-12 — a working day squeezed between a weekend and お盆 — the
  baseline over-forecasts by 3.9 M and 2.8 M kWh per period, and the SHAP
  decomposition attributes the excess to `lag_7d_demand_kwh`: the same
  weekday one week earlier was an ordinary working day. The same weekday one
  year earlier sits in the same position relative to お盆.
- [O-003](research/demand/observations.md#o-003-建国記念の日-is-heavily-under-forecast-the-day-type-outweighing-the-d-7-lag): on 建国記念の日 2026-02-11
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

> We believe that giving `lightgbm_msm_popw_daytype` the load of a reference
> day one year earlier, per period, will reduce out-of-sample MAE, because
> the two motivating error patterns (O-002, O-003) are days whose level one
> week earlier, or whose day-type average, does not represent them, while a
> well-chosen day one year earlier does.

How the reference day is chosen is each experiment's hypothesis: a calendar
rule in E-001, a learned similar-day selector in E-002.

## Scope and constraints

- **Forecast target:** the 48 half-hourly `demand_kwh` values of
  `fct_area_demand_generation_actual` for day D, Tokyo area (`--area tokyo`)
- **Information cutoff:** D-1 at 09:30 JST; usable demand history = delivery
  days ≤ D-2; observed-weather features use complete observation days ≤ D-2
  at 東京 s47662; forecast features use the MSM vintage referenced 21:00 JST
  D-2 population-weighted over the area's 21 weighted stations; the day type
  and the calendar attributes of D are known from the calendar; the year-ago
  load and, in E-002, the candidates' observed weather are at least 334 days
  old, and the selector's weights are fitted only on pairs whose target day
  precedes the first forecast day
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
- **Candidate run:** `lightgbm_msm_popw_daytype_lag1y-tokyo`
  [`88169a52e7ac43168f1bf9d1ca35e8cf`](http://localhost:5005/#/experiments/2/runs/88169a52e7ac43168f1bf9d1ca35e8cf)
  — run 2026-08-31 with the baseline's flags; `lgbm_feature_cols` = the
  baseline's + `lag_1y_demand_kwh`, `prior_year_reference_rules =
  same_weekday=3934,same_weekday_shifted=104,same_holiday=316,nearest_non_working_day=29`,
  `lag_1y_hourly_load_span = 2016-04-01..2026-08-28`, 2020 census weights,
  105 refits
- **Code or pull request:** branch `feature/demand-r004-lag-1y`, stacked on
  `feature/dim-date-prior-year-reference-date` (PR #29) —
  [PR #30](https://github.com/hankehly/power-market-analytics/pull/30). The
  segment tables, the daily paired comparison and the figure below are the
  output of `scripts/compare_demand_runs.py --baseline
  0a6b8a5560d445d5b9705bde99cf13ae --candidate 88169a52e7ac43168f1bf9d1ca35e8cf
  --mae-by-month-png …`; the motivating-day, worst-day, holiday-kind,
  reference-rule and SHAP tables were queried from
  `fct_demand_forecast_accuracy` / `fct_demand_forecast_contribution` ×
  `dim_date` the same way as R-003's.
- **Matched window:** 729 delivery days 2024-08-18..2026-08-17, the same
  training rows and refit schedule as the baseline (both skip 2025-06-21 for
  its D-7 lag in the 2025-06-14 TSO hole; the year-ago load itself is never
  missing on the window)

### Results

All values in kWh per 30-minute period over the matched window (729 days,
34,954 scored points per run; both runs skip 2025-06-21; MLflow holds the full
metric sets).

| Metric | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| Overall MAE | 594,325 | 594,639 | +315 | +0.1 % |
| Overall MAPE | 3.66 % | 3.67 % | +0.01 pp | +0.3 % |
| Mean error / bias, overall | −14,967 | −9,537 | +5,430 | — |
| Mean error / bias, daytime | +4,729 | −853 | −5,582 | — |
| Holiday MAE | 765,403 | 687,908 | −77,495 | −10.1 % |

Daily paired comparison: the candidate is lower on 51.6 % of days (376 of
729); mean daily-MAE difference +341 kWh, **95 % bootstrap CI over days
[−15,231, +15,754]** (10,000 resamples, seed 0); median daily-MAE difference
−4,352 kWh; lower in 13 of 25 calendar months
([figure](assets/R-004-E-001-mae-by-month.png)). Winter −3.4 %, autumn
−0.6 %, spring +2.5 %, summer +2.2 %; the top-10 % demand days −4.6 %, the
other 90 % +0.8 %.

By day type and day part:

| Segment | n | Baseline MAE | Candidate MAE | Relative change |
|---|---:|---:|---:|---:|
| Weekday | 22,752 | 590,209 | 594,069 | +0.7 % |
| Weekend | 9,322 | 551,514 | 567,216 | +2.8 % |
| Holiday | 2,880 | 765,403 | 687,908 | −10.1 % |
| Overnight | 8,746 | 374,014 | 395,103 | +5.6 % |
| Morning | 2,912 | 551,840 | 575,262 | +4.2 % |
| Daytime | 14,560 | 754,144 | 737,839 | −2.2 % |
| Evening | 8,736 | 562,684 | 562,198 | −0.1 % |

Holidays by kind (point-level MAE and bias over the kind's days):

| Kind | Days | Baseline MAE | Candidate MAE | Relative change | Baseline bias | Candidate bias |
|---|---:|---:|---:|---:|---:|---:|
| 元日 / 年末年始 | 10 | 881,279 | 727,220 | −17 % | +656,627 | +227,338 |
| National holiday on a weekday | 28 | 833,513 | 671,863 | −19 % | −176,848 | −69,653 |
| お盆 (8/13–16) | 8 | 755,520 | 925,903 | +23 % | +570,732 | +767,802 |
| ゴールデンウィーク (4/30–5/2) | 6 | 580,682 | 571,389 | −2 % | −449,736 | −59,902 |
| National holiday on a Saturday/Sunday | 8 | 530,599 | 544,322 | +3 % | +188,543 | +255,441 |

The four motivating days (daily MAE; bias in parentheses), with the
candidate's mean per-period SHAP contribution of `lag_1y_demand_kwh` on the
day and the reference it read:

| Day | Kind | Reference (rule) | Baseline | Candidate | Change | `lag_1y` contribution |
|---|---|---|---:|---:|---:|---:|
| 2025-08-12 Tue | working day before お盆 (O-002) | 2024-08-20 (`same_weekday_shifted`) | 3,858,649 (+) | 3,885,835 (+) | +1 % | +1,547,583 |
| 2026-08-12 Wed | working day before お盆 (O-002) | 2025-08-20 (`same_weekday_shifted`) | 2,837,390 (+) | 2,893,068 (+) | +2 % | +1,381,615 |
| 2025-02-11 Tue | 建国記念の日 (O-003) | 2024-02-11 Sun (`same_holiday`) | 1,534,427 (−) | 1,370,447 (−) | −11 % | −48,225 |
| 2026-02-11 Wed | 建国記念の日 (O-003) | 2025-02-11 Tue (`same_holiday`) | 3,071,435 (−) | 1,902,022 (−) | −38 % | +1,295,521 |

On the two 8/12s the reference is, by construction of the shifted rule, an
ordinary week (D−364 is お盆, so D−357 — one week *after* the holiday a year
earlier — is taken): the year-ago load is 19.8 M / 20.8 M kWh per period and
adds +1.5 M / +1.4 M to a day that is already over-forecast. On 2026-02-11
the reference is the same holiday on a Tuesday and the year-ago load
(18.0 M) offsets most of the day-type pull (−917,001); on 2025-02-11 the
reference is the same holiday on a *Sunday* and the feature contributes
almost nothing.

By the rule that chose the reference (days of the window):

| Rule | Days | Baseline MAE | Candidate MAE | Relative change |
|---|---:|---:|---:|---:|
| `same_weekday` | 652 | 561,521 | 569,223 | +1 % |
| `same_holiday` | 57 | 776,746 | 700,901 | −10 % |
| `same_weekday_shifted` | 17 | 1,247,100 | 1,239,078 | −1 % |
| `nearest_non_working_day` | 3 | 549,886 | 441,053 | −20 % |

Share of the mean absolute SHAP contribution over the window, candidate run:
`lag_1y_demand_kwh` 31.4 % (mean |contribution| 1,420,884), the
population-weighted forecast temperature 22.9 %, `time_code` 17.0 %,
`day_type` 9.1 %, `lag_7d_demand_kwh` 8.2 % (it carried the largest share of
the baseline's decomposition on the O-002 days), the observed temperature
5.4 %.

Worst days: the baseline's 20 worst days are lower in the candidate on 12 and
higher on 8; the candidate's top three are the baseline's (2025-08-12,
2026-08-12 and 2025-12-29, the Monday before 年末年始, all over-forecast and
1–7 % worse), while 2026-02-11 drops from #2 to #7 (−38 %), 2025-01-13
成人の日 from #5 to #86 (−61 %), 2024-12-26 from #14 to #188 (−56 %) and
2026-01-01 元日 from #19 to #178 (−53 %). New among the candidate's 20 worst:
2025-06-19 Thu (#99 → #11, 928,334 → 1,646,364, under-forecast) and
2025-02-23 天皇誕生日 on a Sunday (#288 → #20, 582,199 → 1,452,737,
over-forecast): its `same_holiday` reference 2024-02-23 was a Friday, so the
year-ago load of a weekday holiday was read for a Sunday holiday.

### Reading against the decision rule

Overall MAE is not lower and the interval over days includes zero — by the
pre-registered rule the result is **inconclusive** overall, with the
**refine** branch indicated: the holiday half of the hypothesis holds
(holidays −10 %, the O-003 day −38 %, 元日 / 年末年始 and weekday national
holidays −17 % / −19 %), the O-002 half does not — the shifted reference
avoids the holiday-adjacent week by design, so the year-ago load cannot
carry the "working day squeezed before お盆" effect (the bridge-day flag the
researcher deferred on 2026-08-31 addresses exactly that day) — and the
feature costs on weekends (+2.8 %), overnight (+5.6 %) and お盆 (+23 %). The
decision (keep / refine / reject) is the researcher's.

### Decision

**Decision:** Reject (the researcher, 2026-09-05)

The researcher's reasons, recorded as given: the approach works well for some
holidays (special days), but overall it does not contribute enough to warrant
its use; and its way of handling proximity days — days like 2026-08-10, a
Monday sandwiched between a weekend and 山の日 — is poor, because sometimes
there are no days in recent history that have the exact same calendar
characteristics. Resulting change
([PR #35](https://github.com/hankehly/power-market-analytics/pull/35),
2026-09-05): `dim_date` loses `prior_year_reference_date` and
`prior_year_reference_rule` (with their generic tests and the two singular
tests that pinned the worked examples), and the demand task loses
`lightgbm_msm_popw_daytype_lag1y` together with the inputs that existed only
for it (`PriorYearCalendar` / `load_prior_year_calendar`, `AreaHourlyLoad` /
`load_area_hourly_load`, `join_prior_year_load`). `fct_area_power_usage_hourly`
stays in the warehouse. The E-001 run, its forecasts, contributions and
accuracy rows remain in MLflow and the marts as the record of the experiment;
`lightgbm_msm_popw_daytype` remains the demand baseline and the script
default.

### Follow-up ideas

- None recorded with the decision. The bridge-day / holiday-window flag the
  researcher deferred on 2026-08-31 (their other idea from O-002 / O-003) is
  not part of it.
- Taken up as E-002: choose the reference day with a learned selector instead
  of a rule.

---

## E-002 — Year-ago load of a learned similar day

### Why this experiment

E-001 helped some holidays (−10 %) but its rule failed on proximity days:
history often has no day with the same calendar shape, so the rule fell back
to an ordinary week and O-002's days did not move. A learned selector always
returns the nearest day, and the data decide how much calendar shape, season
and weather matter (Park, Song and Kwon 2020, §2.2). Design:
`docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md`.

### Experiment hypothesis

Adding `similar_day_demand_kwh` — the でんき予報 hourly load of the nearest
day in D − 364 ± 30, at the hour containing the period, divided by 2 — to
`lightgbm_msm_popw_daytype` lowers out-of-sample MAE, especially on
proximity days and holidays.

### Change

Strategy `lightgbm_msm_popw_daytype_simday` = the baseline + the feature.
The reference day is the nearest of the 61 days in D − 364 ± 30 under a
weighted distance over seven parts (calendar days from D − 364; the target's
forecast against the candidate's observed temperature, humidity and rain;
days since and until a named holiday; holiday degree), the weights fitted
once per run on past pairs. Parts, fit and frames: the design spec.

### Expected evidence

- Overall MAE lower than the baseline's
- The retrieval check: the selected day's realised load difference below the
  plain D − 364 day's on most forecast days
- Proximity days (O-002) and holidays (O-003) improve; お盆 does not worsen
  as it did in E-001 (+23 %)
- Less plausible: the selector does not beat D − 364 on the retrieval check,
  or MAE rises

### Decision rule

Keep if overall MAE is lower, the bootstrap interval over days excludes zero,
and no day type gets materially worse. Refine if the retrieval check beats
D − 364 but the model does not improve (then top-K days, or the distance as a
feature). Inconclusive if the interval includes zero. Reject if MAE is higher
and the interval excludes zero.

### Execution

- **MLflow experiment:** `demand`
- **Baseline run:**
  [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae),
  compared as run, as in E-001
- **Candidate runs:** `lightgbm_msm_popw_daytype_simday --start-date
  2024-08-18 --end-date 2026-08-17 --area tokyo`, no `--train-start`; the
  weights fitted on targets 2019-04-01 to 2024-08-16 (about 1,960 days,
  120 k pairs)
- **Code or pull request:** branch `feature/demand-similar-day-reference` (PR number
  recorded when opened)

### Results

| Metric | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| Overall MAE | 594,325 | — | — | — |
| Important segment MAE | — | — | — | — |
| Mean error / bias | — | — | — | — |

Plus the retrieval check (selected vs D − 364 vs oracle load difference, and
the share of days the selector beats D − 364) and the fitted weights.

### Interpretation

Pending.

### Decision

**Decision:** Pending

### Follow-up ideas

- Weight variability over time: fit the weights on yearly or rolling windows
  of targets and compare
- Refit the weights during the backtest, at each LightGBM refit, if they move
- Top-K similar days; the distance itself as a feature; a blended curve

---

## Current conclusion

E-001 (2026-08-31) tested the researcher's remedy for O-002 / O-003 on the
matched window (Tokyo, 729 delivery days 2024-08-18..2026-08-17): overall MAE
+0.1 % with the interval over days including zero; holidays −10 %
(建国記念の日 2026-02-11 −38 %, 元日 / 年末年始 −17 %); the working days before
お盆 unchanged, お盆 +23 %, weekends +2.8 %, overnight +5.6 %. **Reject**,
decided by the researcher on 2026-09-05: the year-ago load on a rule-chosen
prior-year reference date helps some special days but not enough overall,
and the reference-date rules handle proximity days poorly when recent history
holds no day with the same calendar characteristics. The column, the strategy
and its inputs were removed.

E-002 (planned 2026-09-05) keeps the feature and replaces the rule with a
learned similar-day selector; the question was broadened accordingly.

## Open questions

- None recorded.

## Final disposition

**Investigation status:** In progress (E-001 rejected 2026-09-05; E-002 planned)

**Recommended action after E-001:** Done — `dim_date.prior_year_reference_date`,
`prior_year_reference_rule` and `lightgbm_msm_popw_daytype_lag1y` removed on
2026-09-05 ([PR #35](https://github.com/hankehly/power-market-analytics/pull/35));
`lightgbm_msm_popw_daytype` stays the demand baseline and the default of
`scripts/demand_backtest.py`.

**Superseded by:** —

**Next:** run E-002 once its implementation lands.
