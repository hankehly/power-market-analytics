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

**Evaluation window.** Since 2026-09-27
([PR #223](https://github.com/hankehly/power-market-analytics/pull/223)) the
task pins its evaluation window, **2024-04-01 … 2026-03-31** (FY2024 and
FY2025, 730 days), and reserves a holdout, 2026-09-06 … 2027-03-31, for
confirmation runs. `scripts/demand_backtest.py` scores the window when it is
given no dates. The runs from before the pin scored 2024-08-18 … 2026-08-17,
so their numbers are not comparable with a run on the window; the experiments
decided on them keep the numbers as run.

**Baseline.** A strategy run in the `demand` MLflow experiment. The Tokyo
baseline is `e212` since 2026-09-20, when the researcher kept
[#212](https://github.com/hankehly/power-market-analytics/issues/212) for
lowering MAE 6.5 % (546,202 → 510,465 kWh over 2024-08-18 … 2026-08-17, CI
over days excluding zero; run `34c506fbb30d4c7eb4efdca973e49384` against
`32ecbdbc7c1a456d8f1411ba867c6d84`, a run of `e179` — the Tokyo baseline from
2026-09-19, when [R-006](https://github.com/hankehly/power-market-analytics/issues/165),
[R-007](https://github.com/hankehly/power-market-analytics/issues/166) and
[R-008](https://github.com/hankehly/power-market-analytics/issues/167) were
kept tentatively). Its reference run on the pinned window is
`264840a26c8f48ac83b2cfe4ebb1c16c` (MAE 518,070 kWh, MAPE 3.13 %), which the
batch of 2026-09-27 ([#230](https://github.com/hankehly/power-market-analytics/issues/230))
reproduced period by period. `e212` runs for Tokyo
only, because its similar-day mart needs the でんき予報 hourly load and a fit
of the weights (`scripts/fit_similar_day.py`). `scripts/demand_backtest.py`
keeps `e171` as its default and as the Kansai baseline
([R-003](https://github.com/hankehly/power-market-analytics/issues/162),
2026-08-26); its Kansai reference run is `53b62fe1856b40bdb8c6944cb3ec0b50`
(MAE 287,231 kWh, MAPE 3.52 %). Both reference runs are matched to the
marts built 2026-09-19 … 2026-09-21, and the Tokyo one also to the
similar-day partition `b18808c615184762b21621354f93bfb5` (`e171` reads no
similar-day feature). A candidate on the pinned window can use the baseline's
run as its baseline until the data is refreshed, after which re-run the
baseline first; a re-run of the similar-day job also retires the Tokyo one,
because the fit moves with the last bit of its inputs. Pin `--train-start`
identically for a candidate and its baseline; the window's dates need no
pinning. `e169`, `e170`, the similar-day presets `e173` … `e179` and the two
holiday variants `e219` and `e221` stay registered as reference presets.

**Reference runs on the pinned window.** Every preset was re-scored on the
window on 2026-09-27, in one batch with the script's defaults: 730 days, no
day skipped, the 13 presets for Tokyo and `e169` … `e171` for Kansai. The
runs are the MLflow runs tagged `batch = pinned-window-reeval-2026-09-27`; the
table of their MAE, MAPE and matched comparisons against the baselines is
[#230](https://github.com/hankehly/power-market-analytics/issues/230).
On 2026-09-29 the lag-window weather batches ran on the same window against
`e219`'s run `8fb1b358…`: `e243` (the 21 temperature columns, MAE −2.4 %, the
CI over days excluding zero), `e244` (the 19 radiation columns, −0.9 %, the CI
including zero) and `e245` (all 40, −3.3 %, excluding zero; −0.9 % against
`e243` with the CI including zero) — the numbers and the researcher's decision
are in [#243](https://github.com/hankehly/power-market-analytics/issues/243),
[#244](https://github.com/hankehly/power-market-analytics/issues/244) and
[#245](https://github.com/hankehly/power-market-analytics/issues/245); the
three presets are registered, and the baseline stays `e212` until a decision
moves it.

**Preset names.** Every demand preset is named after the experiment that tested
it, `e<issue number>` (the [research README](research/README.md)'s rule). Until
2026-09-27 the ten presets of 2026-09-19 carried chain names, and the runs, the
MLflow `feature_preset` params and the issues from before that day still do:

| Until 2026-09-27 | Since | Experiment |
|---|---|---|
| `lightgbm` | no file: its four references are written out in `e169` and `e170` | none (the first model, PR #5) |
| `lightgbm_msm` | `e169` | [#169](https://github.com/hankehly/power-market-analytics/issues/169) |
| `lightgbm_msm_popw` | `e170` | [#170](https://github.com/hankehly/power-market-analytics/issues/170) |
| `lightgbm_msm_popw_daytype` | `e171` | [#171](https://github.com/hankehly/power-market-analytics/issues/171) |
| `lightgbm_msm_popw_daytype_simday` | `e173` | [#173](https://github.com/hankehly/power-market-analytics/issues/173); its feature switched on 2026-09-14, [#180](https://github.com/hankehly/power-market-analytics/issues/180) |
| `lightgbm_msm_popw_daytype_simday_calendar` | `e174` | [#174](https://github.com/hankehly/power-market-analytics/issues/174) |
| `lightgbm_msm_popw_daytype_simday_holidaydegree` | `e175` | [#175](https://github.com/hankehly/power-market-analytics/issues/175) |
| `lightgbm_msm_popw_daytype_simday_holidaydistance` | `e176` | [#176](https://github.com/hankehly/power-market-analytics/issues/176) |
| `lightgbm_msm_popw_daytype_simday_calendarcounts` | `e177` | [#177](https://github.com/hankehly/power-market-analytics/issues/177) |
| `lightgbm_msm_popw_daytype_simday_lags` | `e178` | [#178](https://github.com/hankehly/power-market-analytics/issues/178) |
| `lightgbm_msm_popw_daytype_simday_lags_weather` | `e179` | [#179](https://github.com/hankehly/power-market-analytics/issues/179) |

Each renamed file's `description` also names its former name. The two
spot-price presets keep theirs.

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
