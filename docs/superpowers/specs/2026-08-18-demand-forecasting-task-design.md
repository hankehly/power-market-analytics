# Demand (load) forecasting task + shared forecasting framework — design

Date: 2026-08-18. Status: approved (brainstorming session).

## 1. Goal

Add a second modeling task, `power_market_analytics/tasks/demand/`, with a
LightGBM baseline that forecasts an area's 48 half-hourly demand values for
delivery day D at 09:30 JST on D-1, backtested and logged to MLflow the same
way as `spot_price` (same rolling engine, same metrics, same publish → dbt
accuracy-fact path). To avoid a second copy of the spot-price machinery, the
task-agnostic parts move into a new package `power_market_analytics/forecasting/`
and both tasks become thin configurations of it.

## 2. Scope

In scope:

- `power_market_analytics/forecasting/`: `TaskSpec`, generic frame bases, the
  strategy interface, the rolling backtest engine (with a gaps policy), lag
  features, the sliding-window LightGBM strategy base, warehouse publish, and
  the year × time-code error heatmaps.
- `spot_price` refactored onto that package with **no change** to its column
  names, MLflow artifacts, `pma_ml.spot_price_forecast` or its dbt models.
- `tasks/demand/`: task definition, frames, datasets, temperature feature,
  `lightgbm` strategy, registry, `scripts/demand_backtest.py`.
- Warehouse: `dim_area.representative_jma_station_id` (via the `jepx_areas`
  seed), `pma_ml.demand_forecast`, `stg/std_ml__demand_forecast`,
  `fct_demand_forecast`, `fct_demand_forecast_accuracy`.
- Tests for everything (100 % coverage gate), CLAUDE.md updates.

Out of scope (deliberately): a demand `compare` script, a naive/seasonal-naive
demand baseline, holidays as a feature, weather *forecasts* as a feed, Superset
dashboards, generalising `spot_price/compare.py`.

## 3. Task definition (demand)

- **Issue time:** 09:30 JST on D-1 (before the 10:00 JEPX day-ahead gate).
- **Target:** the 48 half-hourly `demand_kwh` values (30分kWh, as published) of
  delivery day D for one JEPX area, from `fct_area_demand_generation_actual`.
- **Usable demand history:** delivery days ≤ D-2. A TSO's file for day X is
  finalised shortly after midnight of X+1, so at 09:30 on D-1 the newest complete
  day is D-2 (D-1 is in progress). → `history_lead_days = 2`.
- **Usable weather:** JMA hourly observations through 09:00 D-1 exist, but the
  task uses complete observation days only (≤ D-2) so that every one of the 48
  periods is built from the same window.
- **Representative station:** one JMA 気象官署 per area (section 6.1). Tokyo →
  東京 s47662, Kansai → 大阪 s47772.
- **Baseline features (`lightgbm`):** `time_code`, `month`, `day_of_week`,
  `wavg_temperature_c` (section 5.4), `lag_7d_demand_kwh` (same time code, D-7;
  D-7 ≤ D-2 so it is always knowable).
- **Metrics:** MAE and MAPE (`common.metrics`), MLflow regressor metrics +
  `mape_excl_zero_actuals`, per-day errors, year × time-code heatmaps — the
  same set as spot_price.

## 4. `power_market_analytics/forecasting/` (generic)

### 4.1 `task.py` — `TaskSpec`

Frozen dataclass:

| field | spot_price | demand |
|---|---|---|
| `name` (MLflow experiment) | `spot_price` | `demand` |
| `unit` | `JPY/kWh` | `kWh` |
| `history_lead_days` | 1 | 2 |
| `issue_offset` (from D 00:00) | −1 day + 09:55 | −1 day + 09:30 |
| `forecast_table` | `pma_ml.spot_price_forecast` | `pma_ml.demand_forecast` |
| `history_cls / forecast_cls / result_cls / records_cls` | spot frames | demand frames |

Derived properties (no duplication): `value_col = history_cls.value_col`,
`forecast_col = forecast_cls.forecast_col`, `actual_col = result_cls.actual_col`.
`__post_init__` checks that `forecast_cls`, `result_cls` and `records_cls` agree
on `forecast_col`.
Methods: `history_cutoff(target_date) -> Timestamp` (= D − lead days; history
rows must satisfy `trade_date <= cutoff`), `issued_at(trade_date)`.

### 4.2 `frames.py` — generic frame bases

`N_PERIODS = 48`. Each base declares one ClassVar and assembles
`schema`/`keys`/`non_null_cols` in `__init_subclass__`; a subclass that does not
set the ClassVar raises `TypeError` at class-definition time.

- `HalfHourlySeries` — `value_col`; schema `trade_date: datetime64[ns]`,
  `time_code: int64`, `<value_col>: float64`; grain `(trade_date, time_code)`;
  value non-null.
- `DayAheadForecast` — `forecast_col`; single target day, exactly time codes
  1..48 (`_validate_extra` as today).
- `BacktestResult` — `actual_col`, `forecast_col`; both non-null.
- `ForecastRecords` — `forecast_col`; columns `run_id, strategy, area_code,
  forecast_issued_ts, trade_date, time_code, <forecast_col>, published_at`;
  grain `(run_id, area_code, trade_date, time_code)`.
- `MetricByYearTimeCode` — moved from spot_price unchanged.

### 4.3 `strategy.py`

`ForecastStrategy(ABC)`: `name: str`, `task: ClassVar[TaskSpec]`,
`predict(target_date, history) -> DayAheadForecast`,
`build_eval_set(history, start_date, end_date, result=None) -> DomainFrame`,
`evaluate(eval_set, **kwargs) -> EvaluationResult` — the current spot_price
interface with generic types. `FeaturesUnavailableError(ValueError)`: raised by
`predict` when the target day's features cannot be built.

### 4.4 `backtest.py` — engine and gaps policy

`run_backtest(strategy, history, start_date, end_date) -> BacktestRun`, where
`BacktestRun` is a frozen dataclass `(result: BacktestResult, skipped_days:
tuple[Timestamp, ...])`.

- Target days = the distinct `trade_date`s of `history` inside the window
  (a day absent from history is not forecast); empty → `ValueError`.
- For each D the strategy receives
  `history_cls.from_df(df[df.trade_date <= task.history_cutoff(D)])`.
- `FeaturesUnavailableError` from `predict` → the day is skipped with a
  WARNING log and appended to `skipped_days`. If every day is skipped →
  `ValueError` listing them.
- Result = inner join of actuals (value renamed to `actual_col`) and forecasts on
  `(trade_date, time_code)`. Forecast points without an actual (a null-demand
  hole) are dropped and their count logged. Every actual row of a forecast day
  must have exactly one forecast, else `ValueError` (unchanged strictness for
  the forecast side).
- `daily_metrics(result)` — one row per day with `mae`, `mape`; column names
  read from `type(result)`.

For spot_price's complete data both gap rules are no-ops; today's
"missing actual → error" becomes "missing actual → dropped + logged".

### 4.5 `features.py`

`join_lag(left, series, *, value_col, days, name)` — the current spot
`join_lag` with the value column parameterised.

### 4.6 `lgbm.py` — `SlidingWindowLightGbmStrategy`

The current `LightGbmStrategy` with the spot-specific parts factored out:

- `LGBM_PARAMS`, `CALENDAR_FEATURE_COLS = ("time_code", "month", "day_of_week")`.
- `LightGbmEvalSet(DomainFrame)` base: ClassVars `feature_cols`, `target_col`
  (= the task's `actual_col`), `forecast_col` (= the task's `forecast_col`);
  concrete subclasses declare their explicit `schema`;
  `to_eval_frame()` = `[*feature_cols, target_col, forecast_col]` as float64.
- Class attributes a concrete strategy sets: `task`, `name`, `feature_cols`,
  `eval_set_cls`, `lookback_days` (extra history days the training window's
  first row needs — spot 1, demand 8).
- `_features(points, history_df)` = calendar columns + `self._add_features(...)`
  (abstract hook: lags/exogenous). `_design_matrix` renames
  `task.value_col → eval_set_cls.target_col`.
- `predict`: rows = all 48 time codes of D (the D-1 template is dropped);
  any NaN feature → `FeaturesUnavailableError`; TreeSHAP recording, forecast
  frame via `task.forecast_cls`.
- `_ensure_fitted`, `build_eval_set`, `evaluate`, `_log_shap_plots`: as today,
  reading target/forecast columns from `eval_set_cls`; `evaluate` logs the
  `lgbm_*` params and calls a `_extra_params()` hook (default `{}`) so a
  subclass can add its own (demand logs the temperature window/half-life).
- Constructor: `train_window_days=730`, `refit_every_days=7`,
  `train_start_date=None` (unchanged).

### 4.7 `publish.py`

`build_forecast_records(task, result, *, run_id, strategy, area_code) ->
ForecastRecords` (issue timestamp = `task.issued_at`) and
`publish_forecast_records(task, records, spark=None) -> int` (DDL and table from
the spec; parquet partitioned by `run_id`, dynamic partition overwrite).

### 4.8 `plots.py`

`metric_by_year_time_code(result, metric)` and `error_heatmaps(task, result,
title)` (MAE panel labelled with `task.unit`); palette constants move with it.

## 5. Task packages

### 5.1 `spot_price` after the refactor (behaviour-preserving)

- `__init__.py`: `TASK` (lead 1, 09:55, `pma_ml.spot_price_forecast`, spot
  frames); `MLFLOW_EXPERIMENT = TASK.name`.
- `frames.py`: `SpotPrices(HalfHourlySeries)` `value_col="price_jpy_kwh"`;
  `SpotPriceForecast(DayAheadForecast)` `forecast_col="forecast_price_jpy_kwh"`;
  `SpotPriceBacktestResult(BacktestResult)` `actual_col="actual_price_jpy_kwh"`;
  `SpotPriceForecastRecords(ForecastRecords)`; `OcctoDemandForecast` unchanged.
- Deleted (replaced by `forecasting/`): `backtest.py`, `features.py`,
  `publish.py`, `plots.py`, `strategies/base.py`.
- `strategies/naive.py`: `PreviousDayStrategy` on the generic base
  (`task = TASK`, `join_lag(value_col=...)`, raises `FeaturesUnavailableError`
  when D-1 is missing).
- `strategies/lgbm.py`: `LightGbmStrategy(SlidingWindowLightGbmStrategy)` with
  `lookback_days = 1`, `_add_features` = 1-day lag then the existing
  `_join_daily_features` hook; `LightGbmOcctoStrategy` unchanged; the two eval
  sets subclass the generic `LightGbmEvalSet` with their explicit schemas.
- Unchanged: `datasets.py`, `compare.py`, `strategies/__init__.py`.
- `scripts/spot_price_backtest.py`: imports the generic engine/publish/plots
  with `TASK`; unpacks `BacktestRun`; logs `n_days_skipped`.

### 5.2 `demand/__init__.py`

Task-definition docstring (section 3), `TASK`, `MLFLOW_EXPERIMENT = TASK.name`.

### 5.3 `demand/frames.py`, `demand/datasets.py`

- `AreaDemand(HalfHourlySeries)` `value_col="demand_kwh"`;
  `DemandForecast` `forecast_col="forecast_demand_kwh"`;
  `DemandBacktestResult` `actual_col="actual_demand_kwh"`;
  `DemandForecastRecords`.
- `AreaTemperature(DomainFrame)`: `obs_date: datetime64[ns]`,
  `hour_ending: int64` (1..24), `temperature_c: float64` (nullable); grain
  `(obs_date, hour_ending)`.
- `AREA_CODES = ("tokyo", "kansai")` — the areas whose TSO feed is loaded
  (extend together with the `fct_area_demand_generation_actual` union).
- `load_area_demand(area_code, spark=None) -> AreaDemand`: `date_key,
  time_code, demand_kwh` from `fct_area_demand_generation_actual ⋈ dim_area`;
  rows with null `demand_kwh` are dropped (count logged); kWh cast to float64.
- `load_area_temperature(area_code, spark=None) -> AreaTemperature`:
  `date_key, hour(observed_hour_start_at)+1 as hour_ending, temperature_c` from
  `fct_jma_weather_hourly ⋈ dim_area on station_id =
  representative_jma_station_id`; empty result → `ValueError`.

### 5.4 `demand/features.py` — recency-weighted temperature

`recency_weighted_temperature(points, temperature, *, lag_days=(2, …, 8),
half_life_days=1.0, name="wavg_temperature_c")`:

- Period → hour: `hour_ending = (time_code + 1) // 2` (the observation hour
  containing the period start, as `fct_jma_weather_hourly` documents).
- For each lag k, join `temperature_c` at `(trade_date − k days, hour_ending)`.
- Weight `w_k = 0.5 ** ((k − min(lag_days)) / half_life_days)` (D-2 → 1,
  D-3 → ½, …, D-8 → 1/64), renormalised over the non-missing lags; NaN only
  when all seven are missing.

Constants `TEMPERATURE_LAG_DAYS`, `TEMPERATURE_HALF_LIFE_DAYS` live here and are
logged as run params by the strategy.

### 5.5 `demand/strategies/`

- `lgbm.py`: `DemandLightGbmEvalSet(LightGbmEvalSet)` (schema: grain, `month`,
  `day_of_week`, `wavg_temperature_c`, `lag_7d_demand_kwh`,
  `actual_demand_kwh`, `forecast_demand_kwh`); `LightGbmStrategy` with
  `task = TASK`, `name = "lightgbm"`, `feature_cols = (*CALENDAR_FEATURE_COLS,
  "wavg_temperature_c", "lag_7d_demand_kwh")`, `lookback_days = 8`,
  `__init__(temperature: AreaTemperature, **kwargs)`, `_add_features` =
  `join_lag(days=7)` + `recency_weighted_temperature`, `_extra_params` =
  temperature window/half-life.
- `__init__.py`: `STRATEGIES = {"lightgbm": LightGbmStrategy}`;
  `build_strategy(name, *, area_code, train_start_date=None, spark=None)`
  loads the area's temperature and forwards `train_start_date`.

### 5.6 `scripts/demand_backtest.py`

Same CLI as the spot script (`--strategy`, `--area`, `--days` default 365,
`--start-date`, `--end-date`, `--train-start`, `--shap-nsamples`); MLflow run
under `demand` tagged `strategy`/`area`; logs params (incl. `n_days_skipped`),
`daily_errors.csv`, `predictions.csv`, heatmaps, evaluation; publishes to
`pma_ml.demand_forecast` and tags `warehouse_table`.

## 6. Warehouse / dbt

### 6.1 `dim_area.representative_jma_station_id`

New column in seed `dbt/seeds/jepx_areas.csv` and `dim_area` (type-1 attribute,
`string`, nullable — `system` has none; `relationships` test to
`dim_jma_station.station_id`): hokkaido s47412 札幌, tohoku s47590 仙台, tokyo
s47662 東京, chubu s47636 名古屋, hokuriku s47607 富山, kansai s47772 大阪,
chugoku s47765 広島, shikoku s47891 高松, kyushu s47807 福岡.

### 6.2 Forecast write-back models (contracts enforced, composite uniqueness)

- Source `ml.demand_forecast` in `models/raw/ml.yml` (columns as
  `spot_price_forecast` with `forecast_demand_kwh double`).
- `stg_ml__demand_forecast` (same `REFRESH TABLE` pre-hook) →
  `std_ml__demand_forecast` (adds `trade_datetime`) → `fct_demand_forecast`
  (`date_key, time_code, area_key, run_id, strategy, trade_datetime,
  forecast_issued_ts, horizon_hours, forecast_demand_kwh, published_at`) →
  `fct_demand_forecast_accuracy` (left-joins
  `fct_area_demand_generation_actual.demand_kwh` as `actual_demand_kwh`;
  `error_kwh`, `abs_error_kwh`, `pct_error`, `abs_pct_error`; nulls where the
  actual is null).

## 7. Error handling

- Frame contracts fail fast (`from_df`), including the new class-definition
  checks for the generic bases.
- `TaskSpec.__post_init__` rejects inconsistent frame classes.
- Engine: skipped days are warnings + `BacktestRun.skipped_days`; all-skipped
  and empty windows raise; forecast rows without an actual are dropped and
  counted; an actual without a forecast raises.
- Loaders raise on empty results (unknown area, area without a station, no
  weather rows).
- Publish: idempotent per `run_id` (dynamic partition overwrite), as today.

## 8. Testing & verification

- TDD; `just test` (100 % coverage) and `uv run ruff check .` green.
- `tests/conftest.py`: `curated_warehouse` gains synthetic
  `fct_area_demand_generation_actual` (Tokyo, `PRICE_DAYS` × 48, with one
  partial-day null hole) and `fct_jma_weather_hourly` (s47662 hourly with a few
  null hours) plus `dim_area.representative_jma_station_id`.
- New/moved tests: `tests/test_forecasting_{task,frames,backtest,features,
  lgbm,publish,plots}.py`, `tests/test_demand_{frames,datasets,features,lgbm,
  strategies,scripts}.py`; spot tests updated for the new imports/frame names.
- Devcontainer: `just dbt build`; `just python scripts/demand_backtest.py
  --area tokyo` and `--area kansai`; `just dbt build --select
  +fct_demand_forecast_accuracy`; a `spot_price` `previous_day` run to confirm
  the refactor left it working.
- CLAUDE.md: Commands (demand backtest), Architecture (`forecasting/` package,
  demand flow, `dim_area` station column).
