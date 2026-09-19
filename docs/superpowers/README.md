# Design history

Every piece of work in this repository starts as a **design spec** — what is
being built and why, with the decisions the researcher made — and is carried
out through an **implementation plan**, a step-by-step script with the
verification output of each step. Both are written once and then left alone:
this folder is an archive, not documentation that is kept current. When a spec
and the code disagree, the code is right.

The table is one row per piece of work, newest first. A row with no spec was
small enough to plan directly; a row with no plan was a design the researcher
applied without one. A Plan cell that reads *Not built* is a design that was set
aside or put off: nothing was applied, and the spec is kept for what it measured.

| Date | Work | Spec | Plan |
|---|---|---|---|
| 2026-09-19 | Feature candidates #152 and #153, put off — heating and cooling exposure at each hour and as the day's degree-hours; not approved, needs more looking into; kept for the measured weekday demand against forecast temperature curve (floor at 17–18 °C, leaving it at about 14 and 21 °C) | [spec](superpowers/specs/2026-09-19-candidate-152-153-heating-cooling-exposure-design.md) | Not built |
| 2026-09-19 | Feature candidate #140, set aside — OCCTO's half-hourly forecast for D; the availability, from 2025-04-01, is too short to build a bias-correction model; kept for OCCTO's measured error against the actuals | [spec](superpowers/specs/2026-09-19-candidate-140-occto-half-hourly-forecast-design.md) | Not built |
| 2026-09-19 | Feature candidate #150 — the population-weighted observed temperature over the 24 and 72 hours ending at the target hour on D-2, and its 24-hour half-life weighted average, in `ftr_hour_jma_obs` | [spec](superpowers/specs/2026-09-19-candidate-150-accumulated-observed-temperature-design.md) | — |
| 2026-09-19 | Feature candidate #151 — the forecast morning temperature trend in `ftr_day_msm`, over differences; the cumulative solar radiation left out by the researcher | [spec](superpowers/specs/2026-09-19-candidate-151-forecast-weather-trajectory-design.md) | — |
| 2026-09-19 | Feature candidate #138 — the rank-1 similar day's distance and its lag in days, from columns the mart already had, with no new scoring run | [spec](superpowers/specs/2026-09-19-candidate-138-similar-day-distance-and-lag-design.md) | — |
| 2026-09-19 | Feature candidate #135 — wind and solar generation on D-2 and D-7 in `ftr_period_actuals`; the observed radiation left out by the researcher, because it cannot be weighted | [spec](superpowers/specs/2026-09-19-candidate-135-renewable-generation-lags-design.md) | — |
| 2026-09-19 | Feature candidates #137, #146, #147, #149 and #154 — six ratio and position columns over the recent load `ftr_period_actuals` already reads; fractions, not percentages | [spec](superpowers/specs/2026-09-19-candidate-137-146-147-149-154-recent-load-ratios-design.md) | — |
| 2026-09-19 | Presets as YAML files — one file per preset under `conf/presets/<task>/`, a full list or a base plus add/drop, loaded into the same `Preset`; the thirteen presets pinned | [spec](superpowers/specs/2026-09-19-yaml-presets-design.md) | [plan](superpowers/plans/2026-09-19-yaml-presets.md) |
| 2026-09-19 | Research on GitHub issues — the research ledger moved from `docs/research/` to issues of four kinds (observation, investigation, feature candidate, experiment), the 26 records migrated, family batches as one experiment | [spec](superpowers/specs/2026-09-19-research-on-issues-design.md) | [plan](superpowers/plans/2026-09-19-research-on-issues.md) |
| 2026-09-14 | Paper-style similar days — ranks 1-3, weighted mean, same-holiday references | [spec](superpowers/specs/2026-09-14-similar-day-top-k-design.md) | [plan](superpowers/plans/2026-09-14-similar-day-top-k.md) |
| 2026-09-12 | Recent load features — thirteen features from the area's last four weeks of demand in the feature marts, one preset and a matched run (demand R-006) | [spec](superpowers/specs/2026-09-12-demand-recent-load-features-design.md) | [plan](superpowers/plans/2026-09-12-demand-recent-load-features.md) |
| 2026-09-12 | Feature-value fact — every tagged mart column unpivoted to the period grain, generated from the dbt manifest, as the Superset surface of the feature catalogue | — | [plan](superpowers/plans/2026-09-12-feature-value-fact.md) |
| 2026-09-11 | The demand similar day moved out of the strategy into a walk-forward fit-and-score job that writes `pma_ml.similar_day` | — | [plan](superpowers/plans/2026-09-11-similar-day-feature.md) |
| 2026-09-11 | Demand presets — the nine demand strategies became named feature lists read through Feast, and the strategy classes were deleted | — | [plan](superpowers/plans/2026-09-11-demand-presets.md) |
| 2026-09-11 | Spot-price presets — the same for `lightgbm` and `lightgbm_occto` | — | [plan](superpowers/plans/2026-09-11-spot-price-presets.md) |
| 2026-09-10 | Feature catalogue — dbt feature marts at day / hour / period grain, Feast point-in-time retrieval, and `available_at` on every standardized model | [spec](superpowers/specs/2026-09-10-feature-catalogue-design.md) | [marts](superpowers/plans/2026-09-10-feature-marts.md) · [retrieval](superpowers/plans/2026-09-10-feast-retrieval.md) · [`available_at`](superpowers/plans/2026-09-10-available-at-standardized.md) |
| 2026-09-08 | Permutation feature importance — walk-forward ΔMAE per feature, published to `fct_<task>_forecast_importance` and shown on the Explanation tab | [spec](superpowers/specs/2026-09-08-permutation-feature-importance-design.md) | [plan](superpowers/plans/2026-09-08-permutation-feature-importance.md) |
| 2026-09-06 | Compare tab — a candidate run against a baseline run over the periods both scored, on both forecast dashboards | [spec](superpowers/specs/2026-09-06-forecast-dashboard-compare-tab-design.md) | [plan](superpowers/plans/2026-09-06-forecast-dashboard-compare-tab.md) |
| 2026-09-05 | Kansai でんき予報 過去の電力使用実績 — the hourly 電力使用状況 series for 関西電力送配電, on the shared power-usage parser | [spec](superpowers/specs/2026-09-05-kansai-power-usage-design.md) | [plan](superpowers/plans/2026-09-06-kansai-power-usage.md) |
| 2026-09-05 | Holiday degree on `dim_date` — the graded 休日度合い of patent JP 4448226 B2 in place of a 0/1 holiday flag | [spec](superpowers/specs/2026-09-05-dim-date-holiday-degree-design.md) | — |
| 2026-09-05 | Learned similar-day reference load — the weighted-distance selector of demand R-004 E-002, with least-squares weights | [spec](superpowers/specs/2026-09-05-demand-similar-day-reference-design.md) | [plan](superpowers/plans/2026-09-05-demand-similar-day-reference.md) |
| 2026-08-30 | CsvLoader header groups — group files by their first header line and let Spark verify every header, replacing the Python preflight | [spec](superpowers/specs/2026-08-30-csv-loader-spark-verified-header-groups-design.md) | [plan](superpowers/plans/2026-08-30-csv-loader-spark-verified-header-groups.md) |
| 2026-08-30 | CsvLoader single-scan reads — one FileScan per layout instead of one per file; the JMA load went from ~1 h 45 min to about a minute | [spec](superpowers/specs/2026-08-30-csv-loader-single-scan-design.md) | [1 JMA](superpowers/plans/2026-08-30-csv-loader-single-scan-stage1-jma.md) · [2 area actuals](superpowers/plans/2026-08-30-csv-loader-single-scan-stage2-area-actuals.md) · [3 e-Stat](superpowers/plans/2026-08-30-csv-loader-single-scan-stage3-estat.md) · [4 header default](superpowers/plans/2026-08-30-csv-loader-single-scan-stage4-header-default.md) |
| 2026-08-26 | SHAP explanation dashboard — per-period TreeSHAP contributions written back and rendered as the Explanation tab | [spec](superpowers/specs/2026-08-26-shap-explanation-dashboard-design.md) | [plan](superpowers/plans/2026-08-26-shap-explanation-dashboard.md) |
| 2026-08-21 | JMA MSM GPV point forecast — the 12 UTC D-2 surface run decoded from RISH GRIB2 into `fct_jma_msm_weather_forecast_hourly` | [spec](superpowers/specs/2026-08-21-jma-msm-gpv-point-forecast-ingestion-design.md) | [plan](superpowers/plans/2026-08-21-jma-msm-gpv-ingestion.md) |
| 2026-08-20 | JMA re-scope — staffed stations inside the JEPX areas only, more elements, one stitched file per station-year | [spec](superpowers/specs/2026-08-20-jma-s-station-rescope-design.md) | [plan](superpowers/plans/2026-08-20-jma-s-station-rescope.md) |
| 2026-08-18 | Demand task and the shared `forecasting/` framework both tasks sit on | [spec](superpowers/specs/2026-08-18-demand-forecasting-task-design.md) | [plan](superpowers/plans/2026-08-18-demand-forecasting-task.md) |
| 2026-08-16 | TEPCO エリア需要・発電情報 実績 — the first TSO actuals feed and the curated fact it lands in | [spec](superpowers/specs/2026-08-16-tepco-area-demand-generation-design.md) | [plan](superpowers/plans/2026-08-16-tepco-area-demand-generation.md) |

Adding a row is part of finishing the work: write the spec and the plan as
usual, then add one line here.
