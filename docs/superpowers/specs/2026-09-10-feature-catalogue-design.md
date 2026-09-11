# Feature catalogue: dbt feature marts, Feast retrieval, one strategy per task — design

Date: 2026-09-10. Status: **draft** for the researcher's review. Branch: none yet.

## 1. Goal

One master list of features. A model picks features by name. No feature can see data that
was not yet available when the forecast is issued. The values can be queried from Superset.
The same path serves the backtests now and a live daily forecast later. A task's forecast
can be a feature of another task (the demand forecast as a spot-price feature).

Today a feature set is a class. The demand module has 9 strategy classes and 9 eval-set
classes, one per feature subset. Adding a feature means a class, a column tuple, a branch in
`build_strategy` and a registry entry. Lookahead protection is by hand inside each builder.

## 2. Decisions

1. **dbt computes every feature.** A feature is a column of a feature mart. Python picks
   columns and trains; it builds no features.
2. **One clock: the issue time, 09:30 on D-1 for both tasks** (`TaskSpec.issue_offset`).
   The spot-price task moves from 09:55, which was chosen arbitrarily; existing spot runs
   keep 09:55 in their `forecast_issued_ts`. Every training row and target row is stamped
   with its own issue time.
3. **Every feature row carries `available_at`.** The source's standardized model computes
   it once, next to the typed time axis. Every model downstream passes it through and takes
   the greatest when it joins several inputs. An as-of join at the issue time is the only
   way a column reaches a model. A row available after the issue time is dropped by the join,
   not by review.
4. **Feast does the join and holds the list.** Spark offline store on the project's own
   session, no online store. Feature views are generated from the dbt manifest. Feature
   services are the presets. The spike in §9 is the go/no-go; the fallback is a `merge_asof`
   façade with the same interface.
5. **Fitted features are parameters fitted in Python plus SQL scoring.** No Python feature
   builders. dbt Python models are not available on the thrift connection: the dbt-spark
   adapter only submits Python models to Databricks.
6. **One LightGBM strategy class per task, parametrised by a preset.** The preset name is the
   `strategy` column, so dashboards, run labels and the compare scripts keep working.
7. **Superset reads a long fact** of feature values, an unpivot of the marts.
8. **Model outputs are feature sources.** `forecast_issued_ts` is their `available_at`;
   `published_at` breaks ties, newest wins.

## 3. The clock and the availability rules

The issue time of a row (area, D, time_code) is `D + issue_offset`. A feature row is usable
for that row when `available_at <= issue time`. Among usable rows with the same key the
newest `available_at` wins; with the same `available_at`, the newest `published_at` wins.
The `<=` matters: the demand forecast issued at 09:30 is usable by the spot-price forecast
issued at 09:30 for the same day. In the live path the demand job therefore runs first.

`available_at` is computed once in the standardized model of each source, from the source's
publication column or its rule, so a curated fact that unions or joins standardized models
reuses it without recomputing. Staging is untouched; dimensions and seeds are static and
carry no column. Curated facts and feature marts only pass it through, taking the greatest
across joined inputs.

| Source | `available_at` | Status |
|---|---|---|
| JMA hourly observations | `observed_at` + 1 h | bound; JMA posts within about 10 to 30 min, no per-row record |
| MSM forecast | `forecast_reference_at` + 4 h (12 UTC run = 21:00 JST, so 01:00 D-1) | bound; RISH distribution observed ~23:30 JST, 2.5 h after reference |
| TSO area actuals (A-1) | `greatest(file_updated_at, period end)` | daily files ~00:05 on D+1; the frozen 2025-06-14 TEPCO file is stamped 05:05 that day, hence the floor |
| でんき予報 hourly | `greatest(file_updated_at, hour end)`; TEPCO's yearly-file rows (2016-04 to 2022-03) get D+2 00:00 | daily files are last updated 23:55 on the day; the yearly files carry a much later update time |
| OCCTO daily and 30-min forecasts | D-2 18:00 | rule 17:30以降速やかに, observed 17:45 to 17:49, OCCTO's timeline says 18時頃 |
| JEPX spot prices for delivery day X | X-1 12:00 | bids close 10:00, results promptly after; 12:00 is the BG plan deadline that needs them |
| `dim_date`, seeds, census | no column | static |
| `pma_ml.<task>_forecast` | `forecast_issued_ts` | exposed as `available_at` by the `std_ml__*` models |
| Similar day (§5, written back by the fit-and-score job) | the day's MSM forecast vintage's; every candidate is at least 334 days older | `pma_ml.similar_day`, PR 7 |

Each rule and its evidence are written in the standardized model's YAML;
`docs/superpowers/plans/2026-09-10-available-at-standardized.md` lists them with the
measured lags. Models that compute the column: `std_jma__hourly`, `std_jma__msm_surface_forecast`,
`std_tepco__area_demand_generation_actual`, `std_kansai__area_demand_generation_actual`,
`std_tepco__power_usage_hourly`, `std_kansai__power_usage_hourly`,
`std_occto__demand_forecast_dad`, `std_occto__area_reserve_rate_dad`, `std_jepx__spot`;
`std_ml__demand_forecast` and `std_ml__spot_price_forecast` expose it as
`available_at = forecast_issued_ts`. The seven curated facts built from them select it
through: `fct_jma_weather_hourly`, `fct_jma_msm_weather_forecast_hourly`,
`fct_area_demand_generation_actual`, `fct_area_power_usage_hourly`,
`fct_occto_demand_supply_forecast_daily`, `fct_occto_demand_supply_forecast_30m`,
`fct_jepx_spot_area_price`.

## 4. Feature marts (dbt)

- New folder `models/features/`, schema `pma_features`, models named `ftr_<grain>_<family>`.
  Three grains: day (area × trade_date), hour (area × trade_date × hour_ending), period
  (area × trade_date × time_code). One model per source family: calendar, jma_obs, msm,
  actuals, occto, jepx, forecasts, similar_day.
- Every mart has an enforced contract, a unique test on its keys plus `available_at`, and
  `available_at` not null. One macro builds `available_at` as the greatest of the inputs'
  values. A generic test on every mart guards against a missing column; the join is the
  guarantee.
- A feature column is tagged in YAML: `meta: {feature: true, categorical: false}` with a
  description. Keys and `available_at` are not tagged. The tagged columns are the master list.
- A source with several vintages gives several rows per key, one per vintage (the MSM view
  keeps one row per `forecast_reference_at`). The join picks the vintage.
- A weighted mean in a mart is added in a fixed order (the `ordered_weighted_mean` macro:
  the terms collected, sorted by their key and folded), never with a plain `sum()`, which
  adds in read order and can move a value by ~1e-14 between builds — enough to move
  LightGBM's histogram bins. Decided 2026-09-11 after PR 6's reproduction; the researcher
  chose the fixed order over rounding.
- Today's features move as follows.

| Feature | Mart | Grain |
|---|---|---|
| `month`, `day_of_week`, `day_type`, `holiday_degree`, the ten calendar columns | `ftr_day_calendar` | day |
| `max_demand_hour_ending`, `max_demand_mw`, `max_supply_capacity_mw` | `ftr_day_occto` | day |
| `wavg_temperature_c` (D-8..D-2, weights halving per day, renormalised) | `ftr_hour_jma_obs` | hour |
| `forecast_temperature_c`, `popw_forecast_temperature_c`, humidity, rain | `ftr_hour_msm` | hour |
| `lag_7d_demand_kwh` | `ftr_period_actuals` | period |
| `lag_1d_price` | `ftr_period_jepx` | period |
| `similar_day_demand_kwh` | `ftr_period_similar_day` | period |
| `time_code` | entity column, passed through | period |

## 5. Fitted features

Two forms. **Form A**: a script fits parameters in Python, a mart scores in SQL from them.
**Form B**: a job fits, scores and writes the feature values back to `pma_ml.<feature>`
with `available_at` and `published_at`, partitioned by its MLflow run like the forecast
tables; a guarded staging model reads the table, as the importance tables are read, and
the mart passes the rows through. The Feast join takes the newest run available at the
issue time and, among rows tied on `available_at`, the newest published (the view's
`created_timestamp_column`), so a refit-and-rescore replaces the feature wherever it
scored and the runs before it stay in the table. A refit is a new run, on the
researcher's decision.

Similar day is Form B, decided 2026-09-11 in PR 7's review. Form A was built first: a
SQL twin of the selector, 600 lines of model and contract, and a parameters table with
its own staging model. The researcher chose the write-back for the smaller change and the
single definition of the selector; that dbt does not score this feature was not a
concern. `scripts/fit_similar_day.py` fits the seven softmax weights
(`scipy.optimize.least_squares`, Park, Song and Kwon 2020 Eq. 1–3, in
`tasks/demand/similar_day.py`) on the pairs up to `--fit-through`, selects the similar
day of every scorable delivery day with them, and writes 48 rows per day to
`pma_ml.similar_day`: the chosen day's hourly load over the period's hour ÷ 2 as
`similar_day_demand_kwh`, the chosen day, its lag, its distance and the candidate count,
`available_at` = the day's MSM forecast vintage's (every candidate is at least 334 days
older). The run also logs the selection and the retrieval check (selected vs D − 364 vs
oracle) with the four `similar_day_*` metrics over the days after the fit. Proof,
2026-09-11: the fit through 2024-08-16 gives run `008868fe…`'s weights, and the written
rows are the Python selector's, which matched the run's selection on all 729 forecast days.

A Form A feature, should one come, keeps this section's original rule: parameters in
`pma_ml.<feature>_parameters` with `available_at` = the fit window's end, one mart row per
parameter vintage. Its open point, that a backtest's training rows lie before the fit
that serves its forecasts, is then to be settled.

## 6. Feast

- The definitions live in the package, `power_market_analytics/features/`, so tests and the
  coverage gate see them; the store config is `conf/feast/feature_store.yaml` (project `pma`,
  file registry `data/feast/registry.db`, gitignored, rebuilt by `open_store()`;
  `offline_store: type: spark`, which reuses the active SparkSession; views `online=False`,
  nothing materialised). No `feast` CLI is needed.
- Entities and join keys: `area_code` (string), `trade_date_key` (int `yyyymmdd`),
  `hour_ending` (int), `time_code` (int). The entity frame carries all four;
  `hour_ending = (time_code + 1) // 2`. A view joins on the keys of its grain, so a day
  feature repeats over the 48 periods of its day and an hour feature over its 2 periods,
  without code. Only coarse to fine: a summary of period values into a day feature is its
  own column in a day mart, computed in dbt, then broadcast like any other.
- `scripts/generate_feature_views.py` reads `dbt/target/manifest.json` and writes
  `power_market_analytics/features/views.py`: one `SparkSource(query=…, timestamp_field="available_at")`
  selecting the keys, `trade_date_key`, the tagged columns and `available_at` from the mart,
  and one `FeatureView` per mart on its grain's entities, the tagged columns as fields with
  their descriptions and a `categorical` tag. The file is checked in and never edited; the
  `dbt parse` CI job runs the generator with `--check` (`just feature-views` regenerates).
  A source with re-publication (forecasts, parameters) will set `created_timestamp_column="published_at"`
  when its view is added. No TTL.
- Presets live in `power_market_analytics/tasks/<task>/presets.py`: name → feature
  references. A preset does not say which features are categorical: a column is
  categorical when its view field carries the mart's `categorical` tag, read off the
  views at build time, so a feature added with `--add` is treated as its mart declares
  it. The same dict produces the `FeatureService`
  objects and the strategy's column list (PR 5). Every current strategy name becomes a preset:
  demand `lightgbm`, `lightgbm_msm`, `lightgbm_msm_popw`, `lightgbm_msm_popw_daytype`,
  `lightgbm_msm_popw_daytype_simday` and its four calendar variants; spot `lightgbm`,
  `lightgbm_occto`. `previous_day` stays a strategy with no features.
- Retrieval: `retrieval.entity_frame(area_code, days, issue_offset)` builds one row per
  delivery period stamped with its issue time; `retrieval.historical_features(store, entity_df,
  features)` calls `get_historical_features` and returns the frame in its row order with the
  features added.
- Time zones: Feast's Spark store renders the entity timestamps as UTC string literals in the
  SQL it generates while comparing rows as instants, so the Spark session must run in UTC.
  The devcontainer's does, and the warehouse's naive-JST convention is wall-clock values
  stored under it, so the entity frame stamps the naive JST issue time as UTC and retrieval
  refuses a session or a frame in another zone. The test fixture's Asia/Tokyo session is
  switched to UTC for the Feast tests.
- Cross-task: a view over `fct_demand_forecast` filtered to the champion preset with
  `timestamp_field="forecast_issued_ts"`, `created_timestamp_column="published_at"`. The
  spot run logs the upstream run id as a parameter.

## 7. The strategy

- `SlidingWindowLightGbmStrategy` keeps the sliding window, refit cadence, TreeSHAP,
  permutation importance and evaluation. `feature_cols`, `categorical_feature_cols` and
  the eval-set schema become instance attributes read from the preset; `lookback_days` and
  `_add_features` go away. Since PR 6 the base's one feature hook is `_features`; its
  calendar computation went with the demand classes. Since PR 7 every demand strategy is a
  preset: the similar-day classes and the Python feature join are gone.
- `build_strategy(preset, area, …)` builds the entity frame once per run, every (area, D,
  time_code) from the first training day to the last target day, calls Feast once, wraps the
  result in a `FeatureFrame` (grain area × trade_date × time_code, the preset's columns,
  NaN where no row was available) and hands it to the strategy. `predict` selects rows; the
  per-day loop makes no Feast call.
- A training row missing a feature is dropped; a target day missing one raises
  `ForecastUnavailableError`, as today. The backtest engine and its history cutoff for the
  target are unchanged.
- MLflow params: `feature_preset`, the existing `lgbm_feature_cols`, and any upstream
  forecast run id.
- CLI: `--strategy` keeps accepting preset names. New `--add` and `--drop` take feature
  references relative to the preset; a run with either must pass `--name`, which becomes the
  `strategy` column.
- The 18 demand classes and the spot `LightGbmOcctoStrategy` are deleted after the
  reproduction check in §10 passes.

## 8. Superset

- `fct_feature_value` in `pma_curated`: grain area_code × trade_date × time_code × feature
  × available_at, columns `feature_value` (double) and `is_categorical`. A macro lists the
  tagged columns from the dbt graph and unpivots every mart with Spark `stack()`, broadcasting
  day and hour marts to periods. About 5 M rows for today's features over two areas; tens of
  millions at hundreds of features, fine as Parquet.
- Two virtual datasets: `<task>_feature_values`, the as-of rows at the task's issue time
  (one row per period × feature), and `feature_values_all`, every vintage. Registered by
  `create_forecast_dashboard.py`. No new dashboard tab in this spec.

## 9. Spike: go or no-go for Feast

- `feast[spark]` 0.66.0 installs beside Python 3.13, PySpark 4.1.1 and pandas 2.3.3 (checked
  2026-09-10 in a throwaway venv; the Spark store imports).
- One view: the population-weighted MSM forecast at hour grain. Entity frame: Tokyo,
  2025-01-01 to 2025-12-31, all 48 periods, issue time 09:30 D-1.
- Pass when all hold: the values equal `load_area_temperature_forecast_population_weighted`'s
  at 0 difference and the same row count; the call takes under 2 minutes; a row whose
  `available_at` is one minute after the issue time is excluded; the call runs under pytest
  with the local Spark fixture; dbt unit tests run on the thrift connection.
- Fail: `forecasting/features.py` gets `as_of_join(entity_df, marts)` built on
  `pandas.merge_asof` per view. Same `FeatureFrame` out, nothing else changes.
- Result, 2026-09-10: passed. 17,520 rows in 6.2 s, every value equal to the loaders, the row
  one minute before its `available_at` excluded and the row at the instant included, the
  same checks under pytest on the local session, dbt unit tests on thrift (PR #61). One
  finding: the local session had to be UTC, the rule in §6.

## 10. Tests and verification

- dbt: contracts and unique keys on every mart; the generic `available_at` test; a YAML unit
  test per feature model with fixture rows; the existing singular tests on the forecast facts.
- Python: the generated views file is current; the entity-frame builder; preset →
  columns, categoricals and dtypes; the strategy on a synthetic `FeatureFrame`, with the
  `curated_warehouse` fixture extended by feature marts; coverage stays at 100 %.
- Reproduction: run the demand baseline preset through the new path and compare with run
  `008868fe…`; the features must match exactly and the forecasts to floating-point
  tolerance, period by period. Same for the spot `lightgbm_occto` run. A difference is a
  feature-definition bug, fixed before any class is deleted.

## 11. Rollout: one PR per step

Each PR is green on its own, keeps every existing run reproducible, and goes through the
Codex review. Old strategy classes stay until the PR that reproduces their runs. Features
must match exactly; forecasts must match to floating-point tolerance, since LightGBM sums
in a different order when rows arrive in a different order.

| PR | Branch | Delivers | Proof | Needs |
|---|---|---|---|---|
| 0 | `feature/spot-price-issue-time-0930` | issue time 09:30 for both tasks | tests, lint, parse; PR #59 | none |
| 1 | `feature/available-at-standardized` | this spec; `available_at` in the nine standardized models and the two forecast ones; the seven facts carry it through; the lags in §3 confirmed and documented | `dbt build` green; per source, the smallest and largest lag from event time to `available_at`; done 2026-09-10 | 0 |
| 2 | `feature/feature-marts` | `models/features/` for today's features except similar day; column tags; the `available_at` macro and generic test; dbt unit tests | every mart column equals today's Python builder's output for Tokyo over one year; done 2026-09-10 | 1 |
| 3 | `feature/feature-value-fact` | `fct_feature_value` and the two Superset datasets | `dbt build` green; one chart in Superset | 2 |
| 4 | `feature/feast-retrieval` | the spike (§9), then the Feast repo, generated views, staleness test and dependency; the façade instead if the spike fails | the spike's pass criteria; done 2026-09-10 | 2 |
| 5 | `feature/spot-price-presets` | presets, `FeatureFrame`, `build_strategy` through Feast, one spot strategy, `--add`, `--drop`, `--name`; delete `LightGbmOcctoStrategy` | the spot `lightgbm_occto` run reproduced; done 2026-09-11 | 4 |
| 6 | `feature/demand-presets` | the demand presets without similar day, one demand strategy; delete their classes | the kept R-003 Tokyo run reproduced; done 2026-09-11 | 5 |
| 7 | `feature/similar-day-feature` | the fit-and-score script, `pma_ml.similar_day`, `ftr_period_similar_day` (a pass-through), the five similar-day presets; delete the last classes | run `008868fe…`'s fit and selection reproduced, the old and new code identical period by period; done 2026-09-11 | 6 |

PR 3 can run beside 4 to 7. The live path, the selection loop and Form B for features made
by other ML models each get their own spec later.

## 12. Not in scope

Online store and materialisation; a feature tab in the dashboards; new features; Kansai
presets beyond the current baseline; changing `previous_day`; the forward/backward feature
selection loop, its own topic.

## 13. Open questions for the researcher

1. The availability lags marked "to confirm" in §3.
2. Registry: a file under `data/` or the Postgres already in compose.
3. How often the similar-day weights are refit. Since PR 7 a refit is one run of
   `scripts/fit_similar_day.py`, which re-scores every day it can and wins wherever it
   scored (§5). The cadence is still the researcher's call.
- Resolved 2026-09-11: the weighted-mean marts sum in a fixed order (§4). Rounding was the
  alternative; it only makes a binning flip unlikely (two values 1e-14 apart round differently
  about once per 10^(d-14) values at d decimals), so the researcher chose the fixed order.
