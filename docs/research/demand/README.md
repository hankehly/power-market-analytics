# Demand research

Research log for the area demand (load) task
(`power_market_analytics/tasks/demand/`). Shared conventions, ID rules and
statuses: [research README](research/README.md).

- Observations: [observation log](research/demand/observations.md)
- Investigations: `R-XXX-*.md` in this folder, indexed below
- Plots cited in conclusions: [`assets/`](research/demand/assets/README.md)

## Scope defaults

An investigation's *Scope and constraints* block should reference these and
record only what it changes.

**Forecast target.** The 48 half-hourly `demand_kwh` values of
`fct_area_demand_generation_actual` for day D in one area. `--area` takes
`tokyo` or `kansai`, the TSO feeds loaded so far.

**Information cutoff.** D-1 at 09:30 JST (`TaskSpec.issue_offset`). Usable
demand history is delivery days ≤ D-2, because TSO 実績 files finalise after
midnight (`history_lead_days = 2`). Weather features use complete observation
days ≤ D-2 at the area's representative JMA station
(`dim_area.representative_jma_station_id`).

**Baseline.** A strategy run in the `demand` MLflow experiment. The current
baseline is `lightgbm_msm_popw_daytype_simday`, kept since
[R-004](research/demand/R-004-prior-year-load-lag.md) E-002 on 2026-09-06; its
reference run is `008868fe59274abfb49f128e29aa28fe` (Tokyo, 2024-08-18 to
2026-08-17). It runs for Tokyo only until another TSO's でんき予報 is loaded.
So `scripts/demand_backtest.py` keeps `lightgbm_msm_popw_daytype` as its
default and as the Kansai baseline
([R-003](research/demand/R-003-day-type-feature.md), 2026-08-26). `lightgbm`,
`lightgbm_msm` and `lightgbm_msm_popw` stay registered as reference
strategies. Pin `--start-date`, `--end-date` and `--train-start` identically
for a candidate and its baseline.

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
  **Explanation (SHAP)** tab. Its **Compare** tab puts a run against a Baseline
  run over the periods both scored. Season and top-10 % demand days are in the
  compare script only.

**Evaluation method.** Rolling out-of-sample backtest over identical delivery
dates and training rows for baseline and candidate. Accuracy rows land in
`fct_demand_forecast_accuracy` after
`just dbt build --select +fct_demand_forecast_accuracy`.

## Investigation index

| ID | Investigation | Status | Current conclusion |
|---|---|---|---|
| R-001 | [Forecast temperature as a demand feature](research/demand/R-001-forecast-temperature.md) | In progress | E-001, 2026-08-23. The MSM forecast temperature at 東京 s47662 (`lightgbm_msm`) cuts Tokyo MAE 32.4 % (1,103,392 → 745,695 kWh; MAPE 6.82 % → 4.62 %). Lower in 25 of 25 months and every day part. Provisionally Keep, researcher to confirm. |
| R-002 | [Population-weighted area temperature](research/demand/R-002-population-weighted-temperature.md) | Supported | E-001, 2026-08-23. Population-weighting the MSM forecast temperature over the Tokyo area's 21 stations (`lightgbm_msm_popw`) cuts MAE a further 2.3 % (745,695 → 728,573 kWh). All day parts lower, 17 of 25 months, CI over days excludes zero. The gain is small and summer/autumn only. Keep, confirmed 2026-08-24. |
| R-003 | [Day type as a categorical feature](research/demand/R-003-day-type-feature.md) | Supported | E-001, 2026-08-25, triggered by O-001. The `dim_date` day type as a LightGBM categorical (`lightgbm_msm_popw_daytype`) cuts Tokyo MAE 18.4 % against run `2556e3f2…` (728,573 → 594,325 kWh; MAPE 4.52 % → 3.66 %). Holiday MAE −56.5 %, the holiday bias +1.69 M → +0.08 M kWh, CI over days excludes zero. Seven holidays and the weekday before a holiday get worse. Keep, confirmed 2026-08-26; now the script default. |
| R-004 | [Year-ago load from a prior-year reference day](research/demand/R-004-prior-year-load-lag.md) | Supported | Two experiments, triggered by O-002 / O-003. **E-001** (2026-08-31) took the year-ago load from a rule-chosen reference date: MAE +0.1 %, CI over days includes zero, holidays −10.1 % but お盆 +23 %. Reject, 2026-09-05 — good on some holidays, too little overall, and poor on proximity days; the strategy and `dim_date.prior_year_reference_date` were removed. **E-002** (2026-09-05) replaced the rule with a learned similar-day selector (`lightgbm_msm_popw_daytype_simday`, run `008868fe…`): MAE −1.5 % (594,325 → 585,362), CI over days still includes zero, but nine of the baseline's ten worst days improve and the 2026-08-10 proximity day −58 %. **Keep**, 2026-09-06, for the days the investigation set out to fix; now the Tokyo baseline. The script default stays `lightgbm_msm_popw_daytype`, which remains the Kansai baseline. |
| R-005 | [Calendar features from dim_date](research/demand/R-005-calendar-features.md) | Not supported | Four experiments, all run and rejected on 2026-09-06, all against reference run `008868fe…`. **E-001**, the ten `dim_date` calendar attributes together (`e3e3bd61…`): MAE +7.3 % (585,362 → 627,877), CI excludes zero, broadly worse but holidays −10.0 %. Three subsets then asked where that holiday gain sits. **E-002**, `holiday_degree` alone (`a8da46c5…`): +0.3 %, CI includes zero, no segment moves more than 2 % either way (holidays +1.8 %). **E-003**, the two holiday distances alone (`f7153839…`): +6.5 %, CI excludes zero, every day part, day type and season worse. **E-004**, the six calendar counts alone (`9182d469…`): +4.2 %, CI excludes zero, weekdays +7.7 % but holidays −13.4 % — the holiday gain comes with this subset. The baseline is unchanged; all four strategies stay registered as references. |
