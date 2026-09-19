# Spot price research

Research log for the JEPX day-ahead spot price task
(`power_market_analytics/tasks/spot_price/`). Shared conventions, ID rules and
statuses: [research README](research/README.md).

- Records: GitHub issues, see [Research](#research) below
- Plots cited in conclusions: [`assets/`](research/spot_price/assets/README.md)

## Scope defaults

**Forecast target.** The JEPX spot area price (JPY/kWh) for each of the 48
delivery periods of day D in one area (`dim_area.area_code`; `--area`).

**Information cutoff.** D-1 at 09:30 JST, before the 10:00 gate closure
(`TaskSpec.issue_offset`). Usable price history is delivery days ≤ D-1
(`history_lead_days = 1`).

**Baseline.** A strategy run in the `spot_price` MLflow experiment
(`scripts/spot_price_backtest.py`). The baseline preset is `lightgbm`: calendar
and the previous day's area price. `previous_day` is the naive reference, and
`lightgbm_occto` is spot_price/R-001 E-001's candidate, provisionally
inconclusive, so it is not the baseline. Pin `--start-date`, `--end-date` and
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
  **Explanation** tab. That tab also carries the run's Feature importance:
  permutation ΔMAE per feature, next to the mean |SHAP|. Its **Compare** tab
  puts a run against a Baseline run over the periods both scored.

**Evaluation method.** Rolling out-of-sample backtest over identical delivery
dates and training rows for baseline and candidate. Accuracy rows land in
`fct_spot_price_forecast_accuracy` after
`just dbt build --select +fct_spot_price_forecast_accuracy`.

## Research

The spot-price records are GitHub issues, ranked in the same
[Load Forecasting](https://github.com/users/hankehly/projects/3) Project as the
demand task's under Task `spot_price`, until the spot task has a Project of its
own: [observations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aobservation),
[investigations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Ainvestigation),
[feature candidates](https://github.com/hankehly/power-market-analytics/issues?q=label%3A%22feature+candidate%22)
and [experiments](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aexperiment),
open and closed. Each search lists both tasks' records, because Task is a
Project field, not a label: in the Project, filter `task:spot_price`. How the
ledger works: the [research README](research/README.md).
The records written before 2026-09-19 keep their IDs in their titles
(`spot_price/R-001 — Supply and demand tightness signals`).
