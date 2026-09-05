# Demand research

Research log for the area demand (load) task
(`power_market_analytics/tasks/demand/`). Shared conventions, ID rules and
statuses: [research README](research/README.md).

- Observations: [observation log](research/demand/observations.md)
- Investigations: `R-XXX-*.md` in this folder, indexed below
- Plots cited in conclusions: [`assets/`](research/demand/assets/README.md)

## Scope defaults

Copy these into a new investigation's *Scope and constraints* block and
narrow them as the question requires.

- **Forecast target:** the 48 half-hourly `demand_kwh` values of
  `fct_area_demand_generation_actual` for day D in one area (`--area`:
  `tokyo`, `kansai` — the TSO feeds loaded so far)
- **Information cutoff:** D-1 at 09:30 JST (`TaskSpec.issue_offset`); usable
  demand history = delivery days ≤ D-2 (`history_lead_days = 2`, TSO 実績
  files finalise after midnight); weather features use complete observation
  days ≤ D-2 at the area's representative JMA station
  (`dim_area.representative_jma_station_id`)
- **Baseline:** a strategy run in the `demand` MLflow experiment —
  `lightgbm_msm_popw_daytype` (the kept baseline since
  [R-003](research/demand/R-003-day-type-feature.md), 2026-08-26, and the
  default of `scripts/demand_backtest.py`; `lightgbm`, `lightgbm_msm` and
  `lightgbm_msm_popw` remain as reference strategies); pin `--start-date`,
  `--end-date` and `--train-start` identically for a candidate and its baseline
- **Primary metric:** MAE (kWh)
- **Segments reported by the tooling:** `scripts/compare_demand_runs.py
  --baseline <run_id> --candidate <run_id>` (matched two-run tables: overall
  MAE / MAPE / bias, day part, day type, calendar month, season, 2,000-MWh
  actual-demand bands, top-10 % demand days, and the daily paired comparison
  with its seeded bootstrap CI over days; `--mae-by-month-png` writes the
  by-month figure) and Superset **Demand Forecast Analysis** (day part, day
  type, actual-demand bands, calibration curve, error histogram, and the
  per-day SHAP waterfall (mean per period) of the **Explanation (SHAP)** tab
  (Day filter))
- **Evaluation method:** rolling out-of-sample backtest over identical
  delivery dates and training rows for baseline and candidate; accuracy rows
  in `fct_demand_forecast_accuracy` after
  `just dbt build --select +fct_demand_forecast_accuracy`

## Investigation index

| ID | Investigation | Status | Current conclusion |
|---|---|---|---|
| R-001 | [Forecast temperature as a demand feature](research/demand/R-001-forecast-temperature.md) | In progress | E-001 run 2026-08-23: adding the MSM forecast temperature at 東京 s47662 (`lightgbm_msm`) cuts Tokyo MAE 32.4 % (1,103,392 → 745,695 kWh; MAPE 6.82 % → 4.62 %) on the matched 729-day window, lower in 25/25 months and every day part — provisionally Keep, researcher to confirm. |
| R-002 | [Population-weighted area temperature](research/demand/R-002-population-weighted-temperature.md) | Supported | E-001 run 2026-08-23: population-weighting the MSM forecast temperature over the Tokyo area's 21 stations (`lightgbm_msm_popw`) cuts MAE a further 2.3 % vs `lightgbm_msm` (745,695 → 728,573 kWh), all day parts lower, 17/25 months, CI over days excludes zero, summer/autumn-only and small — Keep, confirmed 2026-08-24; `lightgbm_msm_popw` is now the demand baseline. |
| R-003 | [Day type as a categorical feature](research/demand/R-003-day-type-feature.md) | Supported | E-001 run 2026-08-25 (triggered by O-001: 15 of the 20 worst days are holidays, over-forecast): adding the `dim_date` day type (Weekday / Weekend / Holiday) as a LightGBM categorical (`lightgbm_msm_popw_daytype`) cuts Tokyo MAE 18.4 % vs the R-002 candidate run `2556e3f2…` (728,573 → 594,325 kWh; MAPE 4.52 % → 3.66 %) on the matched 729-day window — holiday MAE −56.5 % with the holiday bias +1.69 M → +0.08 M kWh, the 15 holidays among the baseline's 20 worst days −55 % to −90 %, all day parts and 21/25 months lower, CI over days excludes zero; seven holidays (now under-forecast) and the weekday before a holiday get worse — Keep, confirmed 2026-08-26; `lightgbm_msm_popw_daytype` is now the demand baseline and the script default. |
| R-004 | [Year-ago load from a prior-year reference day](research/demand/R-004-prior-year-load-lag.md) | In progress | E-001 run 2026-08-31 (triggered by O-002 / O-003): `lightgbm_msm_popw_daytype_lag1y` = the baseline + `lag_1y_demand_kwh` (the でんき予報 hourly load on `dim_date.prior_year_reference_date`, per period) vs run `0a6b8a55…` on the matched 729-day window — overall MAE 594,325 → 594,639 (+0.1 %), CI over days [−15,231, +15,754]: inconclusive; holidays −10.1 % (建国記念の日 2026-02-11 −38 %, 元日/年末年始 −17 %) but お盆 +23 %, weekends +2.8 %, overnight +5.6 %; the working days before お盆 (O-002) unchanged — their D−357 reference is an ordinary week; `lag_1y` takes 31 % of the SHAP mass — Reject, decided by the researcher on 2026-09-05: the approach works well for some holidays (special days) but overall does not contribute enough to warrant use, and its handling of proximity days (2026-08-10, sandwiched between a weekend and 山の日) is poor because recent history sometimes has no day with the exact same calendar characteristics; the strategy and `dim_date.prior_year_reference_date` were removed.; **E-002 run 2026-09-05** (`lightgbm_msm_popw_daytype_simday`: the same feature from a learned similar-day selector, the nearest day in D − 364 ± 30 by a seven-part weighted distance fitted on 119,865 past pairs; run `008868fe…` vs the same baseline): overall MAE 594,325 → 585,362 (−1.5 %), CI over days [−26,812, +8,548] — inconclusive by the rule; weekdays −4.8 %, evenings −7.5 %, top-10 % demand days −7.2 %, the working day before お盆 −46 % (2025) / −20 % (2026); weekends +6.5 %, overnight +3.0 %; the selector beats D − 364 on 59 % of days (load difference 0.048 vs 0.075, oracle 0.021); decision pending |
