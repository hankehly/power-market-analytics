# Forecast analysis

`scripts/spot_price_backtest.py` backtests a forecasting strategy (day-ahead:
at 9:30 JST on D-1, forecast all 48 half-hour prices for delivery day D) and
records the results in two places, linked by the MLflow `run_id`: **MLflow**
(`just open mlflow`, experiment `spot_price`) holds params, metrics, SHAP
plots, the permutation feature importance as a CSV and a bar plot, and CSV
artifacts per run — the experiment record. The **warehouse** gets row-level
forecasts written to `pma_ml.spot_price_forecast` (partitioned by `run_id`;
republishing a run replaces its rows), which dbt models into
`fct_spot_price_forecast` and `fct_spot_price_forecast_accuracy`.

`scripts/demand_backtest.py` follows the same pattern for area demand
(MLflow experiment `demand`), writing to `fct_demand_forecast` and
`fct_demand_forecast_accuracy`.

## Walk-forward backtest

Both tasks run on one engine, `run_backtest`, which steps through the window
one delivery day at a time, hands the strategy the history dated on or before
the task's cutoff, and keeps the 48 forecasts it returns for day D.

That cutoff is a date, not a publication time, and the target and the
features are not held to the same standard: the target history is whatever
the warehouse now holds for those dates (`load_area_demand` reads the current
value, so a day the TSO revised later trains on the revised value), while
features are retrieved as of the issue time, because Feast joins them on
`available_at`. For the TSO actuals that is 00:30 the day after the delivery
day, a rule rather than the daily file's own stamp: TEPCO replaced the
2022-12-01 and 12-02 files on 2022-12-14, and while the stamp was used (until
2026-09-13) it hid ten Tokyo delivery days of December 2022 (12-03 to 12-11
and 12-15) from `ftr_period_actuals`.

Both tasks issue at 09:30 JST on D-1 but see different history: **demand**
has history through **D-2** (`history_lead_days = 2`), because the TSO 実績
file for D-1 is not final at 09:30; **spot price** has history through
**D-1** (`history_lead_days = 1`), since JEPX publishes the previous day's
auction result before 09:30.

The LightGBM strategies of both tasks refit every 7 **calendar** days, counted
from the day that triggered the previous refit, on a window that opens 730
calendar days before the target day and closes at that task's cutoff — at
most 729 delivery days for demand (D-730 … D-2), 730 for spot price
(D-730 … D-1). Between refits the cached model scores the delivery days that
fall inside that cadence, up to seven, and its newest training day reaches
`6 + history_lead_days` days old — 8 for demand, 7 for spot price.

![Walk-forward demand backtest: the 730-calendar-day training window, the unseen day D-1, and the up-to-seven delivery days each refit scores](img/demand-backtest-walk-forward.svg)

The figure is the **demand** task; the spot-price loop has the same shape
with the D-1 gap closed, since its window runs to D-1.

A missing feature does not stop a forecast: LightGBM takes it as NaN and
sends it down each split's missing-value branch — the training rows with a
missing feature are what teach the trees that branch, since without them a
missing value would read as 0.0. Only a row with no feature value at all is
dropped, and a day whose periods are all like that raises
`ForecastUnavailableError`, skipped and reported on `BacktestRun.skipped_days`
while the rest of the window continues. Forecasts are joined one-to-one to
actuals, and a point with no actual is dropped.

Every number above is the complete-data case, which the figure draws. Gaps
only move them one way — fewer rows, fewer scored days, an older model —
entering at three points: a period with a null actual never enters the demand
history (a TSO hole like Tokyo 2025-06-14, which keeps 10 of its 48 periods),
a training row with no feature value is dropped at fit, and a day with none is
skipped. The model's age follows suit: the fit records `_trained_through` as
the newest day that kept a training row, so a cutoff day that keeps none
makes the model older than the figures above.

Each task's `TaskSpec` fixes its cutoff and issue time (`history_lead_days`,
`issue_offset`); the strategy fixes the window and cadence, shared by both
tasks (`DEFAULT_TRAIN_WINDOW_DAYS = 730`, `refit_every_days = 7` on
`SlidingWindowLightGbmStrategy`). `--train-start` clips the window's left
edge for a candidate that should not learn from the stretch before its
feature begins; without it, a candidate and its baseline still fit on the
same rows, since a feature with nulls no longer drops its rows.

## Strategies and feature experiments (spot price)

Three strategies: `previous_day` (naive), `lightgbm` (calendar and 1-day-lag
features) and `lightgbm_occto`, which adds the OCCTO 翌々日 peak-demand hour,
peak demand and peak supply capacity for the delivery day — published D-2
evening, inside the information cutoff.

For a feature experiment, pin `--start-date`/`--end-date` and `--train-start`
identically for candidate and baseline. The OCCTO history starts 2024-04-01,
before which `lightgbm_occto` trains with the three columns null;
`--train-start 2024-04-01` keeps both runs to the rows that have them. Run
`just dbt build --select +fct_spot_price_forecast_accuracy`, then
`scripts/compare_spot_price_runs.py --baseline <run_id> --candidate <run_id>`
to print matched MAE/bias tables by day part, near the OCCTO peak hour, by
month and for high-price days. Experiments are written up under
[`research/spot_price/`](research/spot_price/README.md), with conventions in
[`research/`](research/README.md).

## Superset dashboards

Charting happens in Superset (`just open superset`): one forecast-analysis
dashboard per task, **Spot Price Forecast Analysis** and **Demand Forecast
Analysis**, built by `scripts/create_forecast_dashboard.py` (no arguments
rebuilds every dashboard; `--task spot_price` or `--task demand` rebuilds
one). It creates each task's virtual dataset (`spot_price_forecast_analysis`
/ `demand_forecast_analysis`, the accuracy mart joined to `dim_area`,
`dim_delivery_period` and `dim_date`), every chart, the sectioned layout and
the run filter. Rerunning it is safe and is how everything is rebuilt after a
`docker compose down -v`.

Each dashboard opens on the newest run with KPI tiles (MAE, bias, RMSE,
RMSE/MAE, WAPE, P90), error-structure heatmaps, day-type slices, calibration
and error-distribution views, a cross-run leaderboard, a worst-days drill list
and a zoomable 30-minute forecast-vs-actual detail; clicking a drill-list row
cross-filters the dashboard to that day.

An **Explanation** tab decomposes a day's forecast into per-feature SHAP
contributions, with the run's **Feature importance** at its foot: each
feature's permutation importance (the MAE increase when its column is
shuffled across the run, computed with scikit-learn over the walk-forward
models) next to its mean |SHAP|. Every chart labels a feature by its
expression (`LAG(demand_kwh, 2d)`), read from `dim_feature`; stored rows keep
the column name. The **Feature Catalogue** dashboard lists every feature
under its expression — see [Feature naming](Feature-Naming.md) for the rules.

A **Compare** tab puts the run against a **Baseline** run chosen in a second
filter, over the periods both runs scored: delta tiles, diverging ΔMAE % bars
by segment, ΔMAE % heatmaps, daily ΔMAE, the cumulative error reduction,
most-improved / most-worsened day tables, the SHAP contribution deltas per
feature against the baseline, and a three-line detail.

Both dashboards share one layout and chart names; only the quantity shows
through — JPY/kWh against kWh, demand SI-formatted as `1.098M`. "MAE by
actual price band" becomes "… actual demand band" (fixed 2-GWh bins), and
"Calibration: forecast vs actual price level" becomes "… actual demand
level" (rounded to 1 GWh).

![Spot Price Forecast Analysis dashboard](img/superset/forecast-dashboard.png)

![Demand Forecast Analysis dashboard](img/superset/demand-forecast-dashboard.png)

![Demand Forecast Analysis dashboard — Compare tab](img/superset/demand-forecast-dashboard-compare.png)
