# R-001 — Supply and demand tightness signals

- **Status:** In progress
- **Created:** 2026-08-16
- **Last updated:** 2026-08-16 (E-001 executed)
- **Triggering observation:** [O-001 — Daytime MAE is higher than other day parts](research/observations.md#o-001-daytime-mae-is-higher-than-other-day-parts)
- **Related investigations:** —

## Question

Do supply-and-demand tightness signals available at forecast time improve
Tokyo-area price forecasts?

## Triggering observation

The LightGBM model's daytime MAE is higher than its MAE during the other
predefined day parts. See
[O-001](research/observations.md#o-001-daytime-mae-is-higher-than-other-day-parts).

## Current predictive hypothesis

Adding supply and demand information available by the D-1 09:55 JST cutoff
will reduce the LightGBM model's out-of-sample MAE, both overall and during
daytime periods.

## Scope and constraints

- **Forecast target:** Tokyo-area JEPX spot price for each of the 48 delivery periods
- **Information cutoff:** D-1 at 09:55 JST
- **Baseline:** Current LightGBM strategy without OCCTO demand or supply features
- **Primary metric:** MAE
- **Important segments:** Daytime periods, periods near the forecast maximum-demand hour,
  high-price days, and calendar-month stability
- **Evaluation method:** Rolling out-of-sample backtest over identical delivery dates and
  training rows for the baseline and candidate

## E-001 — Add OCCTO maximum-demand and supply-capacity forecasts

### Why this experiment

OCCTO's day-after-next forecast is published before the information cutoff and
provides a direct view of the expected Tokyo-area demand peak, its timing, and
available supply capacity. These signals may explain price variation that the
current calendar and lag-price features do not capture, particularly during
the daytime periods where the current model has its highest MAE.

The experiment excludes the two minimum-demand fields because their published
meaning changes at 2025-04-01. It also excludes reserve rate so this first test
measures the incremental value of the underlying maximum-demand and supply
measurements without adding a ratio derived from them.

### Experiment hypothesis

Adding the following OCCTO features will reduce out-of-sample MAE, with the
largest improvement during daytime periods and periods near the forecast
maximum-demand hour:

- `max_demand_hour_ending`
- `max_demand_mw`
- `max_supply_capacity_mw`

### Change

Join the Tokyo row from `fct_occto_demand_forecast_dad` to each delivery day's
48 feature rows using the forecast target date, and add the three fields above
to the LightGBM feature set. Retain the existing model parameters, refit
cadence, and all other features so the OCCTO fields are the experiment's only
modeling change.

Exclude OCCTO trial-run rows dated before 2024-04-01. Compare the candidate
only with a baseline trained and evaluated on the same rows; do not interpret
a metric change caused by shortening the candidate's available history as a
feature effect.

### Expected evidence

- Lower overall out-of-sample MAE than the matched baseline
- A larger MAE reduction during daytime periods and periods near
  `max_demand_hour_ending`
- Improvement that is not confined to a single month or a small number of
  extreme-price days
- Little or no improvement would make the hypothesis that these daily OCCTO
  level-and-timing signals add predictive information less plausible

### Decision rule

Keep the features if the candidate lowers both overall and daytime MAE on the
matched evaluation window without a material deterioration in other day parts,
and the improvement is reasonably consistent across calendar months. Treat the
result as inconclusive if it depends mainly on a few extreme days or is smaller
than the variation across time segments. Otherwise reject the change.

### Execution

- **MLflow experiment:** `spot_price`
- **Baseline run:** `lightgbm-tokyo`
  [`6f75d296efc04f418241daff478c97cd`](http://localhost:5005/#/experiments/1/runs/6f75d296efc04f418241daff478c97cd)
  — strategy `lightgbm`, `--train-start 2024-04-01 --start-date 2025-04-01 --end-date 2026-08-16`
- **Candidate runs:** `lightgbm_occto-tokyo`
  [`2f23d462eddb4973ae31200824a672b0`](http://localhost:5005/#/experiments/1/runs/2f23d462eddb4973ae31200824a672b0)
  — strategy `lightgbm_occto`, same flags
- **Code or pull request:** `LightGbmOcctoStrategy` in
  `power_market_analytics/tasks/spot_price/strategies/lgbm.py` (feature join via
  `_join_daily_features`; `OcctoDemandForecast` frame + `load_occto_demand_forecast`);
  segment tables from `scripts/compare_spot_price_runs.py`
- **Matched window (answers the open question below):** OCCTO Tokyo rows are gap-free from
  2024-04-01 (the first non-trial day) through 2026-08-17; JEPX Tokyo actuals end 2026-08-16.
  Both runs use training rows from 2024-04-01 (the candidate drops rows without an OCCTO
  forecast anyway; the baseline is clipped to the same first day with `--train-start`) and
  evaluate the identical 503 delivery days 2025-04-01..2026-08-16 (24,144 points). Both
  made 72 weekly refits with identical training-row counts (17,520 rows growing to the
  35,040-row / 730-day cap from 2026-04-01). Model parameters, refit cadence and base
  features are unchanged; the three OCCTO columns are the only difference.
- **Segment definitions:** day parts follow `dim_delivery_period.day_part`
  (Daytime = 08:00–18:00, time codes 17–36). "Near the forecast maximum-demand hour" =
  periods whose hour of day is within ±1 h of the OCCTO `max_demand_hour_ending` hour
  (six 30-min periods per day). "High-price days" = the top 10 % of delivery days by mean
  actual price (daily mean ≥ 21.33 JPY/kWh, 51 days).

### Results

All values in JPY/kWh over the matched window (503 days, 24,144 points per run).

| Metric | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| Overall MAE | 2.446 | 2.393 | −0.053 | −2.2 % |
| Daytime MAE | 3.377 | 3.187 | −0.190 | −5.6 % |
| MAE near forecast maximum-demand hour (±1 h) | 3.282 | 3.111 | −0.171 | −5.2 % |
| MAE in all other periods | 2.327 | 2.290 | −0.037 | −1.6 % |
| Mean error / bias (forecast − actual), overall | −0.363 | −0.359 | +0.004 | — |
| Mean error / bias, daytime | −0.530 | −0.415 | +0.115 | — |

MAE by day part:

| Day part | n | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|---:|
| Overnight (00–06) | 6,036 | 1.191 | 1.269 | +0.079 | +6.6 % |
| Morning (06–08) | 2,012 | 1.793 | 1.709 | −0.084 | −4.7 % |
| Daytime (08–18) | 10,060 | 3.377 | 3.187 | −0.190 | −5.6 % |
| Evening (18–24) | 6,036 | 2.369 | 2.421 | +0.052 | +2.2 % |

MAE by calendar month (candidate lower in 8 of 17 months):

| Month | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| 2025-04 | 2.089 | 1.601 | −0.488 | −23.4 % |
| 2025-05 | 2.144 | 1.821 | −0.323 | −15.1 % |
| 2025-06 | 1.699 | 1.686 | −0.013 | −0.8 % |
| 2025-07 | 1.721 | 2.000 | +0.279 | +16.2 % |
| 2025-08 | 1.647 | 1.796 | +0.149 | +9.0 % |
| 2025-09 | 1.831 | 1.522 | −0.309 | −16.9 % |
| 2025-10 | 1.650 | 1.699 | +0.049 | +3.0 % |
| 2025-11 | 1.205 | 1.187 | −0.018 | −1.5 % |
| 2025-12 | 1.419 | 1.439 | +0.020 | +1.4 % |
| 2026-01 | 1.442 | 1.557 | +0.115 | +7.9 % |
| 2026-02 | 1.480 | 1.501 | +0.021 | +1.4 % |
| 2026-03 | 2.513 | 2.791 | +0.278 | +11.1 % |
| 2026-04 | 6.527 | 6.052 | −0.474 | −7.3 % |
| 2026-05 | 4.664 | 4.227 | −0.437 | −9.4 % |
| 2026-06 | 3.433 | 3.404 | −0.029 | −0.9 % |
| 2026-07 | 3.069 | 3.215 | +0.147 | +4.8 % |
| 2026-08 (to 16th) | 3.577 | 3.825 | +0.247 | +6.9 % |

MAE by price band:

| Days | n | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|---:|
| Top 10 % price days (daily mean ≥ 21.33) | 2,448 | 4.882 | 5.070 | +0.188 | +3.9 % |
| Other 90 % of days | 21,696 | 2.172 | 2.091 | −0.081 | −3.7 % |

MLflow also carries per-run SHAP importance/beeswarm plots and the year × time-code error
heatmaps for both runs.

### Interpretation

Read against the pre-registered expected evidence:

- **Lower overall MAE than the matched baseline:** yes, −0.053 JPY/kWh (−2.2 %).
- **Larger reduction during daytime and near the forecast maximum-demand hour:** yes.
  Daytime −5.6 % and near-peak −5.2 %, versus −1.6 % in all other periods; the daytime
  under-forecast bias shrinks from −0.530 to −0.415. Morning also improves (−4.7 %).
  Overnight (+6.6 %, +0.079 JPY/kWh) and Evening (+2.2 %, +0.052 JPY/kWh) get worse.
- **Improvement not confined to a single month or a few extreme-price days:** mixed. The
  gain does not come from extreme days — the top-10 % price days get worse (+3.9 %) while
  the other 90 % improve (−3.7 %). But it is not consistent across months either: the
  candidate is better in 8 of 17 months, and the monthly changes (−23 % to +16 %) are
  larger than the overall −2.2 %. The largest gains are April–May 2025, September 2025 and
  April–May 2026; the largest losses are July–August 2025, March 2026 and July–August 2026.

Segment sizes for the day-part and near-peak rows are large (thousands of points), but no
uncertainty interval has been computed for any of the differences.

### Decision

**Decision:** Inconclusive (provisional — applied mechanically from the decision rule;
researcher to confirm)

Applying the rule as written: overall and daytime MAE are both lower on the matched
window; Overnight and Evening deteriorate by +0.08 / +0.05 JPY/kWh; the improvement is
not consistent across calendar months (8 of 17) and is smaller than the variation across
months and day parts. The result does not depend on a few extreme days (those days get
worse). The rule's "inconclusive" branch therefore applies rather than "keep".

### Follow-up ideas

- Test whether the derived `reserve_rate` adds value beyond the demand and
  supply MW fields.
- Test the post-2025 true minimum-demand fields separately after defining a
  semantically consistent training window.

## Current conclusion

E-001 executed on 2026-08-16. On the matched window (train from 2024-04-01, evaluate
2025-04-01..2026-08-16), the three OCCTO peak-demand/supply features lower overall MAE
by 2.2 % and daytime MAE by 5.6 %, with the largest gain near the forecast peak hour and
a smaller daytime under-forecast bias, but Overnight/Evening MAE rise slightly and the
month-by-month effect is mixed (better in 8 of 17 months). Provisionally inconclusive
per the E-001 decision rule.

## Open questions

- ~~What common training and evaluation window provides enough history while
  keeping the baseline and candidate samples identical?~~ Resolved for E-001: training
  rows from 2024-04-01, evaluation 2025-04-01..2026-08-16 (see E-001 Execution).
- Does representing the maximum-demand hour relative to each delivery period
  improve on using the raw hour-ending field alone?

## Final disposition

**Investigation status:** In progress

**Recommended action:** Researcher to review the E-001 result and confirm or revise the
provisional decision.

**Superseded by:** —
