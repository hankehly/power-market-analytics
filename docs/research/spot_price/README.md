# Spot price research

Research log for the JEPX day-ahead spot price task
(`power_market_analytics/tasks/spot_price/`). Shared conventions, ID rules and
statuses: [research README](research/README.md).

- Observations: [observation log](research/spot_price/observations.md)
- Investigations: `R-XXX-*.md` in this folder, indexed below
- Plots cited in conclusions: [`assets/`](research/spot_price/assets/README.md)

## Scope defaults

**Forecast target.** The JEPX spot area price (JPY/kWh) for each of the 48
delivery periods of day D in one area (`dim_area.area_code`; `--area`).

**Information cutoff.** D-1 at 09:55 JST, just before the 10:00 gate closure
(`TaskSpec.issue_offset`). Usable price history is delivery days ≤ D-1
(`history_lead_days = 1`).

**Baseline.** A strategy run in the `spot_price` MLflow experiment:
`previous_day`, `lightgbm` or `lightgbm_occto`
(`scripts/spot_price_backtest.py`). Pin `--start-date`, `--end-date` and
`--train-start` identically for a candidate and its baseline.

**Primary metric.** MAE (JPY/kWh).

**Segments reported by the tooling.** Two tools cover them, and an
investigation cites what it used rather than listing the whole set:

- `scripts/compare_spot_price_runs.py` — day part, periods near the OCCTO
  forecast peak hour, calendar month, high-price days and bias. It reports no
  uncertainty interval; the daily paired bootstrap exists only for demand
  (`compare_demand_runs.py`).
- Superset **Spot Price Forecast Analysis** — actual-price bands, the
  calibration curve, the error histogram and the per-day SHAP waterfall of the
  **Explanation (SHAP)** tab. Its **Compare** tab puts a run against a
  Baseline run over the periods both scored.

**Evaluation method.** Rolling out-of-sample backtest over identical delivery
dates and training rows for baseline and candidate. Accuracy rows land in
`fct_spot_price_forecast_accuracy` after
`just dbt build --select +fct_spot_price_forecast_accuracy`.

## Investigation index

| ID | Investigation | Status | Current conclusion |
|---|---|---|---|
| R-001 | [Supply and demand tightness signals](research/spot_price/R-001-supply-demand-tightness.md) | In progress | E-001 run: OCCTO peak-demand/supply features cut overall MAE 2.2 % and daytime MAE 5.6 % on the matched window, but Overnight/Evening worsen and months are mixed (8/17 better) — provisionally inconclusive. |
