# Demand research

Research log for the area demand (load) task
(`power_market_analytics/tasks/demand/`). Shared conventions, ID rules and
statuses: [research README](research/README.md).

- Records: GitHub issues, see [Research](#research) below
- Plots cited in conclusions: [`assets/`](research/demand/assets/README.md)

## Scope defaults

**Forecast target.** The 48 half-hourly `demand_kwh` values of
`fct_area_demand_generation_actual` for day D in one area. `--area` takes
`tokyo` or `kansai`, the TSO feeds loaded so far.

**Information cutoff.** D-1 at 09:30 JST (`TaskSpec.issue_offset`). Usable
demand history is delivery days ≤ D-2, because TSO 実績 files finalise after
midnight (`history_lead_days = 2`). Weather features use complete observation
days ≤ D-2 at the area's representative JMA station
(`dim_area.representative_jma_station_id`).

**Baseline.** A strategy run in the `demand` MLflow experiment. The Tokyo
baseline is `e212` since 2026-09-20, when the researcher kept
[#212](https://github.com/hankehly/power-market-analytics/issues/212) for
lowering MAE 6.5 % (546,202 → 510,465 kWh over 2024-08-18 … 2026-08-17, CI over
days excluding zero). Its reference run is `34c506fbb30d4c7eb4efdca973e49384`
and the baseline it beat is `32ecbdbc7c1a456d8f1411ba867c6d84`, a run of
`lightgbm_msm_popw_daytype_simday_lags_weather` — the Tokyo baseline from
2026-09-19, when [R-006](https://github.com/hankehly/power-market-analytics/issues/165),
[R-007](https://github.com/hankehly/power-market-analytics/issues/166) and [R-008](https://github.com/hankehly/power-market-analytics/issues/167)
were kept tentatively — on the same window. Both runs are matched to the marts
of 2026-09-20, so a candidate on that window can use `34c506fb…` as its
baseline instead of a fresh run; a candidate on any other window still needs
one. `e212` runs for Tokyo only, because its similar-day mart needs the
でんき予報 hourly load and a fit of the weights (`scripts/fit_similar_day.py`).
`scripts/demand_backtest.py` keeps `lightgbm_msm_popw_daytype`
as its default and as the Kansai baseline ([R-003](https://github.com/hankehly/power-market-analytics/issues/162),
2026-08-26). `lightgbm`, `lightgbm_msm`, `lightgbm_msm_popw` and the chain-named
similar-day presets stay registered as reference presets. Pin `--start-date`,
`--end-date` and `--train-start` identically for a candidate and its baseline.

**Primary metric.** MAE (kWh).

**Segments reported by the tooling.** Two tools cover them, and an
investigation cites what it used rather than listing the whole set:

- `scripts/compare_demand_runs.py --baseline <run_id> --candidate <run_id>` —
  matched two-run tables by day part, day type, calendar month, season,
  2,000-MWh actual-demand band and top-10 % demand days. It also reports
  overall MAE / MAPE / bias and the daily paired comparison with its seeded
  bootstrap CI over days. `--mae-by-month-png` writes the by-month figure. The
  bootstrap CI is only here, not in Superset.
- Superset **Demand Forecast Analysis** — year, time code, calendar month, day
  of week, day part, day type and 2,000-MWh actual-demand band, plus the
  calibration curve, the error histogram and the per-day SHAP waterfall of the
  **Explanation** tab. That tab also carries the run's Feature importance:
  permutation ΔMAE per feature, next to the mean |SHAP|. Its **Compare** tab
  puts a run against a Baseline run over the periods both scored. Season and
  top-10 % demand days are in the compare script only.

**Evaluation method.** Rolling out-of-sample backtest over identical delivery
dates and training rows for baseline and candidate. Accuracy rows land in
`fct_demand_forecast_accuracy` after
`just dbt build --select +fct_demand_forecast_accuracy`.

## Research

The demand records are GitHub issues, ranked in the
[Load Forecasting](https://github.com/users/hankehly/projects/3) Project under
Task `demand`: [observations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aobservation),
[investigations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Ainvestigation),
[feature candidates](https://github.com/hankehly/power-market-analytics/issues?q=label%3A%22feature+candidate%22)
and [experiments](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aexperiment),
open and closed. Each search lists both tasks' records, because Task is a
Project field, not a label: in the Project, filter `task:demand`. How the
ledger works, the kinds of record and the Project's
fields: the [research README](research/README.md). The records written before
2026-09-19 keep their `O-XXX` / `R-XXX` / `E-XXX` IDs in their titles
(`demand/R-006 — Recent load features`).
