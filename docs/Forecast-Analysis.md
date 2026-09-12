# Forecast analysis

`scripts/spot_price_backtest.py` backtests a forecasting strategy (day-ahead:
at 9:30 JST on D-1, forecast all 48 half-hour prices for delivery day D) and
records the results in two places, linked by the MLflow `run_id`:

- **MLflow** (`just open mlflow`, experiment `spot_price`) — params, metrics,
  SHAP plots, the permutation feature importance as a CSV and a bar plot, and
  CSV artifacts per run. It is the experiment record.
- **Warehouse** — row-level forecasts written to `pma_ml.spot_price_forecast`
  (partitioned by `run_id`; republishing a run replaces its rows), which dbt
  models into `fct_spot_price_forecast` and `fct_spot_price_forecast_accuracy`.

`scripts/demand_backtest.py` follows the same pattern for area demand
(MLflow experiment `demand`), writing to `fct_demand_forecast` and
`fct_demand_forecast_accuracy`.

## Walk-forward backtest

Both tasks run on one engine, `run_backtest`. It steps through the window one
delivery day at a time, hands the strategy the history dated on or before the
task's cutoff, and keeps the 48 forecasts it returns for day D.

That cutoff is a date, not a publication time, and the target and the features
are not held to the same standard. The target history is whatever the warehouse
now holds for those dates: `load_area_demand` reads the current value, so a day
the TSO revised later trains on the revised value. Feature values are retrieved
as of the issue time instead, because Feast joins them on `available_at` — which
is why the delivery days 2022-12-08 and 2022-12-09 get no D-7 lag from
`ftr_period_actuals`: TEPCO re-issued the files behind them on 2022-12-14, days
after those forecasts were due.

Both are issued at 09:30 JST on D-1, but they do not see the same history, so
every statement below names its task:

- **Demand** — history through **D-2** (`history_lead_days = 2`). The TSO 実績
  file for D-1 is not final at 09:30.
- **Spot price** — history through **D-1** (`history_lead_days = 1`). JEPX
  publishes the previous day's auction result before 09:30.

The LightGBM strategies of both tasks do not fit once: they refit every 7
**calendar** days, counted from the day that triggered the previous refit, on a
window that opens 730 calendar days before the target day and closes at that
task's cutoff — at most 729 delivery days for demand (D-730 … D-2), 730 for spot
price (D-730 … D-1). Between refits the cached model scores the delivery days
that fall inside that cadence, up to seven, and its newest training day reaches
`6 + history_lead_days` days old — 8 for demand, 7 for spot price.

![Walk-forward demand backtest: the 730-calendar-day training window, the unseen day D-1, and the up-to-seven delivery days each refit scores](img/demand-backtest-walk-forward.svg)

The figure is the **demand** task: 48 half-hourly periods per delivery day, the
D-2 cutoff and the unseen D-1. The spot-price loop has the same shape with that
gap closed, since its window runs to D-1.

A day the strategy cannot forecast — a missing feature raises
`ForecastUnavailableError` — is skipped and reported on
`BacktestRun.skipped_days`, and the rest of the window continues. The forecasts
are then joined one-to-one to actuals, and a forecast point with no actual is
dropped.

Read every number above as the complete-data case, which is what the figure
draws. Gaps only move them one way — fewer rows, fewer scored days, an older
model — and they enter at three points: a period with a null actual never enters
the demand history (a TSO hole like Tokyo 2025-06-14, which keeps 10 of its 48
periods), a training row missing any feature is dropped at fit, and a day that
cannot be forecast is skipped. The model's age follows the same rule: the fit
records `_trained_through` as the newest day that kept a complete row, so when
the cutoff day keeps none, the model is older than the figures above.

The numbers come from two places. Each task's `TaskSpec` fixes its cutoff and
issue time (`history_lead_days`, `issue_offset`); the strategy fixes the window
and the cadence, shared by both tasks (`DEFAULT_TRAIN_WINDOW_DAYS = 730` and
`refit_every_days = 7` on `SlidingWindowLightGbmStrategy`). `--train-start`
clips the window's left edge, which is how a baseline is matched to a candidate
whose feature only begins partway through the history. It aligns that boundary,
not the rows themselves: each strategy drops training rows on its own feature
list, so a candidate feature with scattered nulls still leaves the two fitted on
different rows.

## Strategies and feature experiments (spot price)

Three strategies: `previous_day` (naive), `lightgbm` (calendar and 1-day-lag
features) and `lightgbm_occto`. The last adds the OCCTO 翌々日 peak-demand
hour, peak demand and peak supply capacity for the delivery day, published
D-2 evening and so inside the information cutoff.

For a feature experiment, pin `--start-date`/`--end-date` and `--train-start`
identically for candidate and baseline. The OCCTO history starts 2024-04-01, so
`--train-start 2024-04-01` matches a `lightgbm` baseline to it. Then run
`just dbt build --select +fct_spot_price_forecast_accuracy`, and
`scripts/compare_spot_price_runs.py --baseline <run_id> --candidate <run_id>`
prints matched MAE/bias tables by day part, near the OCCTO peak hour, by month
and for high-price days. Experiments are written up under
[`research/spot_price/`](research/spot_price/README.md), with conventions in
[`research/`](research/README.md).

## Superset dashboards

Charting happens in Superset (`just open superset`), with one
forecast-analysis dashboard per task: **Spot Price Forecast Analysis** and
**Demand Forecast Analysis**.

`scripts/create_forecast_dashboard.py` builds both. No arguments rebuilds every
dashboard; `--task spot_price` or `--task demand` rebuilds one. It creates each
task's virtual dataset (`spot_price_forecast_analysis` /
`demand_forecast_analysis`, the accuracy mart joined to `dim_area`,
`dim_delivery_period` and `dim_date`), every chart, the sectioned layout and the
run filter. Rerunning it is safe, so it is how everything is rebuilt after a
`docker compose down -v`.

Each dashboard opens on the newest run with KPI tiles (MAE, bias, RMSE,
RMSE/MAE, WAPE, P90), error-structure heatmaps and day-type slices, calibration
and error-distribution views, a cross-run leaderboard, a worst-days drill list
and a zoomable 30-minute forecast-vs-actual detail. Clicking a row of the drill
list cross-filters the dashboard to that day.

An **Explanation** tab decomposes a day's forecast into per-feature SHAP
contributions. At its foot sits the run's **Feature importance**: the
permutation importance of each feature next to its mean |SHAP|. Permutation
importance is the MAE increase when that feature's column is shuffled across
the run, computed with scikit-learn over the walk-forward models.

A **Compare** tab puts the run against a **Baseline** run chosen in a second
filter, over the periods both runs scored. It holds delta tiles, diverging
ΔMAE % bars by segment, ΔMAE % heatmaps, daily ΔMAE, the cumulative error
reduction, most-improved / most-worsened day tables, the SHAP contribution
deltas per feature against the baseline, and a three-line detail.

The two dashboards are the same layout with the same chart names. Only the
quantity shows through, JPY/kWh against kWh, with demand values SI-formatted as
`1.098M`. "MAE by actual price band" becomes "… actual demand band" (fixed
2-GWh bins), and "Calibration: forecast vs actual price level" becomes "…
actual demand level" (rounded to 1 GWh).

![Spot Price Forecast Analysis dashboard](img/superset/forecast-dashboard.png)

![Demand Forecast Analysis dashboard](img/superset/demand-forecast-dashboard.png)

![Demand Forecast Analysis dashboard — Compare tab](img/superset/demand-forecast-dashboard-compare.png)
