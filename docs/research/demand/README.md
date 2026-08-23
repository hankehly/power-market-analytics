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
  `lightgbm` (`scripts/demand_backtest.py`); pin `--start-date`, `--end-date`
  and `--train-start` identically for a candidate and its baseline
- **Primary metric:** MAE (kWh)
- **Segments reported by the tooling:** Superset **Demand Forecast Analysis**
  (day part, day type, actual-demand bands, calibration curve, error
  histogram); there is no matched two-run compare script for this task yet —
  `scripts/compare_spot_price_runs.py` reads the spot-price accuracy fact only
- **Evaluation method:** rolling out-of-sample backtest over identical
  delivery dates and training rows for baseline and candidate; accuracy rows
  in `fct_demand_forecast_accuracy` after
  `just dbt build --select +fct_demand_forecast_accuracy`

## Investigation index

| ID | Investigation | Status | Current conclusion |
|---|---|---|---|
| R-001 | [Forecast temperature as a demand feature](research/demand/R-001-forecast-temperature.md) | In progress | E-001 run 2026-08-23: adding the MSM forecast temperature at 東京 s47662 (`lightgbm_msm`) cuts Tokyo MAE 32.4 % (1,103,392 → 745,695 kWh; MAPE 6.82 % → 4.62 %) on the matched 729-day window, lower in 25/25 months and every day part — provisionally Keep, researcher to confirm. |
| R-002 | [Population-weighted area temperature](research/demand/R-002-population-weighted-temperature.md) | In progress | E-001 run 2026-08-23: population-weighting the MSM forecast temperature over the Tokyo area's 21 stations (`lightgbm_msm_popw`) cuts MAE a further 2.3 % vs `lightgbm_msm` (745,695 → 728,573 kWh), all day parts lower, 17/25 months, CI over days excludes zero — but summer/autumn-only and small; provisionally Keep, researcher to confirm. |
