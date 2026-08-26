# SHAP explanation section for the forecast-analysis dashboards

## Objective

Let a user of the Superset forecast-analysis dashboards select one delivery day of a
backtest run — and optionally one 30-minute period of that day — and see the forecast
decomposed into per-feature SHAP contributions as a waterfall chart, next to the feature
values, the model's base value, the forecast and the actual.

The feature applies to both forecasting tasks (`spot_price`, `demand`): the capture,
write-back and dashboard code is shared by the framework and parametrised per task, as
everything else in `power_market_analytics/forecasting/` and
`scripts/create_forecast_dashboard.py` is.

## Decisions (approved 2026-08-26)

1. **Day view = mean per period.** With a day selected and no period, every chart shows the
   mean over the day's 48 periods of each component's contribution. SHAP values are additive,
   so the per-period means are a valid decomposition of the day's mean forecast, on the same
   scale as the single-period view.
2. **The base value is a KPI tile; the waterfall shows feature contributions only.** The
   Superset waterfall's value axis always includes zero (no bounds control), so a base bar of
   ~15,000 MWh would squash ±500 MWh feature bars. The waterfall therefore starts at zero
   ("deviation from the base value") and its Total bar is `forecast − base`.
3. **Both tasks**, not demand only.
4. **Selection via native Day / Period filters** scoped to the new section; a cross-filter from
   the existing Worst-days table is an optional extra (§5.3).

## Background: what exists today

- `SlidingWindowLightGbmStrategy.predict` (`forecasting/lgbm.py`) already records exact
  TreeSHAP contributions for every predicted row (`_shap_records`, keyed by delivery day):
  the grain columns, the feature values (every `feature_cols` member except `time_code`, which
  is the key column), one `shap_<feature>` column per feature and `shap_expected_value`, from
  LightGBM's `predict(..., pred_contrib=True)`. Per row, `expected value + Σ shap_<feature>`
  equals the prediction exactly. Today they are only pooled for the two MLflow summary plots
  and then discarded.
- Only the final refit's booster is logged to MLflow; the per-day models exist only during the
  backtest. Explanations can therefore only be captured at predict time — nothing outside the
  backtest can reproduce the forecast that was actually published.
- Forecast write-back: `pma_ml.<task>_forecast` (parquet, partitioned by `run_id`, dynamic
  partition overwrite) → `stg_ml__` / `std_ml__` / `fct_<task>_forecast` /
  `fct_<task>_forecast_accuracy` → Superset dataset `<task>_forecast_analysis` → the
  dashboard, whose single native filter (Run, on `run_label`) applies to every chart except the
  leaderboard.
- Superset 6.1.0 (`docker/superset/Dockerfile`), verified against its source: the `waterfall`
  viz is bundled; it orders bars by the x-axis value ascending and has no sort or axis-bounds
  control; native filters apply to charts by explicit scope (`rootPath` / `excluded`), so a
  filter defined on one dataset also filters a chart on another dataset as long as that dataset
  has a column of the same name.

## Scope

In scope: capturing the contributions as a framework output, publishing them next to the
forecasts, dbt models and tests for both tasks, a second Superset dataset per dashboard, the
new dashboard section with its two filters, tests, documentation.

Out of scope: SHAP interaction values, logging per-day models, any change to the strategies'
features/parameters/metrics or to the existing MLflow artifacts, changes to the existing
dashboard sections beyond filter scoping, a Python/Plotly waterfall.

## Design

### 1. Framework: contributions as a first-class strategy output

Terminology. A **component** is either one model feature or the **base** (the model's
expected value, TreeSHAP's intercept), named by the constant `BASE_COMPONENT = "base"`.
`component_order` is 0 for the base and `i + 1` for `feature_cols[i]`, so components keep the
model's feature order everywhere downstream. Contributions are in the task's forecast unit
and satisfy, for every period, `base + Σ feature contributions = forecast`.

`power_market_analytics/forecasting/frames.py` — two generic frames (not per task; unlike the
forecast column, the contribution column has one name in Python, `contribution`):

- `ForecastContributions`: schema `trade_date datetime64[ns]`, `time_code int64`,
  `component object`, `component_order int64`, `feature_value float64`,
  `contribution float64`; keys `(trade_date, time_code, component)`; non-null
  `component_order`, `contribution`. Extra validation: every period has exactly one base row;
  `component_order == 0` iff the component is the base; `feature_value` is null iff the
  component is the base (features are complete whenever a prediction exists, since a missing
  feature raises `ForecastUnavailableError`).
- `ForecastContributionRecords`: the write-back layout — `run_id`, `strategy`, `area_code`,
  `forecast_issued_ts datetime64[ns]`, `trade_date`, `time_code`, `component`,
  `component_order`, `feature_value`, `contribution`, `published_at datetime64[ns]`; keys
  `(run_id, area_code, trade_date, time_code, component)`; non-null `strategy`,
  `forecast_issued_ts`, `component_order`, `contribution`, `published_at`.

`power_market_analytics/forecasting/task.py` — two derived properties on `TaskSpec`, no new
fields:

- `contribution_table` = `f"{forecast_table}_contribution"` → `pma_ml.demand_forecast_contribution`,
  `pma_ml.spot_price_forecast_contribution`.
- `contribution_col` = the forecast column with its leading `forecast_` replaced by
  `contribution_` → `contribution_demand_kwh`, `contribution_price_jpy_kwh`. `__post_init__`
  rejects a forecast column that does not start with `forecast_`.

`power_market_analytics/forecasting/strategy.py` — a concrete (non-abstract) method
`ForecastStrategy.contributions(self) -> ForecastContributions | None` whose default returns
`None`: "this strategy cannot explain its forecasts". The spot task's `previous_day` strategy
keeps the default.

`power_market_analytics/forecasting/lgbm.py` — `SlidingWindowLightGbmStrategy.contributions()`
melts `_shap_records` (every day predicted so far; a re-predicted day has already overwritten
its record) into a `ForecastContributions`: per record and feature `i`, one row with
`component = feature_cols[i]`, `component_order = i + 1`, `feature_value` = the recorded
feature value (`time_code` read from the key column), `contribution = shap_<feature>`; plus one
base row per period (`component_order = 0`, `feature_value` NaN,
`contribution = shap_expected_value`). Raises `RuntimeError` when no day has been predicted
yet, like `evaluate`.

### 2. Write-back

`power_market_analytics/forecasting/publish.py`:

- `build_contribution_records(task, contributions, result, *, run_id, strategy, area_code,
  published_at) -> ForecastContributionRecords`. Keeps only the periods present in `result`
  (the backtest drops forecast points without an actual, and the forecast table stores only the
  remaining rows — the contribution table must be congruent with it so the two facts join 1:1
  per period); raises `ValueError` if any `result` period has no contributions. Stamps
  `run_id`, `strategy`, `area_code`, `forecast_issued_ts = trade_date + task.issue_offset` (as
  the forecast records do) and the given `published_at`. The scripts pass the forecast
  records' `published_at` so both tables carry the identical instant: the dashboards build
  `run_label` from `published_at`, and the label must be the same string in both datasets for
  the Run filter to select both.
- `publish_contribution_records(task, records, spark=None) -> int`, the same idiom as
  `publish_forecast_records`: `CREATE DATABASE IF NOT EXISTS`, explicit `CREATE TABLE IF NOT
  EXISTS` DDL — `strategy string, area_code string, forecast_issued_ts timestamp, trade_date
  date, time_code int, component string, component_order int, feature_value double,
  <task.contribution_col> double, published_at timestamp, run_id string`, `USING parquet
  PARTITIONED BY (run_id)` — a cast/rename select (`contribution` → `task.contribution_col`),
  and `insertInto` under dynamic partition overwrite. The DDL/write mechanics may be factored
  into a private helper shared with the forecast publisher; the forecast publisher's behaviour
  and table are unchanged.

Scripts — `scripts/demand_backtest.py` and `scripts/spot_price_backtest.py`, immediately after
`publish_forecast_records`:

```
contributions = strategy.contributions()
if contributions is None:
    log "…: strategy <name> produces no contributions; nothing published"
else:
    records = build_contribution_records(TASK, contributions, result, run_id=…, strategy=…,
                                         area_code=…, published_at=<forecast records' value>)
    publish_contribution_records(TASK, records)
    mlflow.set_tag("contribution_table", TASK.contribution_table)
```

No CSV artifact is logged: the warehouse is the store (a two-year demand run is ~280k rows), and
the MLflow run links to it through the tag, as `warehouse_table` does for the forecasts.

### 3. dbt

Per task — the demand names below; the spot models are identical with
`contribution_price_jpy_kwh` and the spot dimensions/facts. Every model has an enforced
contract with a `data_type` per column and a `dbt_utils.unique_combination_of_columns` test
on its grain.

- Source `ml.demand_forecast_contribution` in `dbt/models/raw/ml.yml`, columns documented as in
  §1 (`feature_value`: the feature as the model saw it, units vary per feature, null for the
  base; `contribution_demand_kwh`: per period `base + Σ features = forecast_demand_kwh`).
- `stg_ml__demand_forecast_contribution` — as-is, with the same `REFRESH TABLE` pre-hook as
  the forecast staging model (the table is written by another Spark application).
- `std_ml__demand_forecast_contribution` — adds `trade_datetime` (same construction as
  `std_ml__demand_forecast`) and `is_base boolean` (`component = 'base'`).
- `fct_demand_forecast_contribution` — grain: run × delivery period × area × component.
  Columns: `date_key`, `time_code`, `area_key`, `run_id`, `strategy`, `component`,
  `component_order`, `is_base`, `trade_datetime`, `forecast_issued_ts`, `feature_value`,
  `contribution_demand_kwh`, `published_at`. `component` is a degenerate dimension (like
  `strategy`); `feature_value` is a non-additive attribute; `contribution_demand_kwh` is
  additive across the components of one period only — never across runs. Tests: uniqueness of
  `(run_id, date_key, time_code, area_key, component)`, `not_null` on keys and measures,
  `relationships` to `dim_date`, `dim_delivery_period`, `dim_area`,
  `dbt_utils.accepted_range` `component_order >= 0`.
- Singular tests in `dbt/dbt_tests/`:
  `assert_fct_demand_forecast_contribution_has_one_base_per_period.sql` (periods whose base
  row count is not exactly 1) and `assert_fct_demand_forecast_contribution_sums_to_forecast.sql`
  (join to `fct_demand_forecast` on the grain; fail rows whose forecast row is missing or where
  `abs(sum(contribution) − forecast) > 1e-6 × greatest(abs(forecast), 1)`).

### 4. Superset explanation dataset

One virtual dataset per dashboard, `<task>_forecast_explanation`, built from a shared
template around a task-specific value block (the same pattern as the analysis dataset):

```sql
select
  c.date_key,
  date_format(c.date_key, 'yyyy-MM-dd') as trade_date_label,
  c.trade_datetime,
  c.time_code,
  concat(p.period_start_time, '-', p.period_end_time) as period_label,
  p.hour_of_day,
  p.day_part,
  d.day_name,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  a.area_code,
  a.area_name_en,
  c.run_id,
  concat(
    date_format(c.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(c.run_id, 1, 8)
  ) as run_label,
  c.strategy,
  c.published_at,
  c.component,
  c.component_order,
  concat(lpad(cast(c.component_order as string), 2, '0'), ' ', c.component) as component_label,
  c.is_base,
  c.feature_value,
{explanation_value_columns_sql}
from {contribution_table} c
join pma_curated.dim_area a on c.area_key = a.area_key
join pma_curated.dim_delivery_period p on c.time_code = p.time_code
join pma_curated.dim_date d on c.date_key = d.date_key
left join {accuracy_table} f
  on c.run_id = f.run_id
  and c.date_key = f.date_key
  and c.time_code = f.time_code
  and c.area_key = f.area_key
```

Value blocks — demand: `c.contribution_demand_kwh / 1000 as contribution_mwh`,
`f.forecast_demand_kwh / 1000 as forecast_demand_mwh`, `f.actual_demand_kwh / 1000 as
actual_demand_mwh` (MWh for display, like the analysis dataset); spot:
`c.contribution_price_jpy_kwh`, `f.forecast_price_jpy_kwh`, `f.actual_price_jpy_kwh` as-is.

- `run_label` is the same expression as in the analysis dataset and `published_at` is the
  same instant (§2), so the existing Run filter selects the matching rows here.
- `component_label` carries the zero-padded order prefix because the waterfall sorts by label.
- `trade_date_label` and `period_label` are strings so the select filters show readable,
  correctly sorted values (`2025-08-01`; `07:00-07:30` … `23:30-24:00`).
- The grain is one row per component, so a period's forecast and actual repeat across its
  components: only AVG-type metrics are valid on them (documented on the builders).
- `main_dttm_col = trade_datetime`; column metadata overridden on every rerun, as today.

`DashboardSpec` gains `explanation_dataset_name`, `contribution_table`,
`explanation_value_columns_sql`, `explanation_value_columns`, `contribution_col` (the dataset
column: `contribution_mwh` / `contribution_jpy_kwh`) and `contribution_format` (signed d3:
`+,.0f` / `+,.3f`), with properties `explanation_dataset_sql`, `explanation_dataset_columns`,
`contribution_metric` (`AVG(contribution_col)`, label `Contribution (<unit>)`),
`base_value_metric` (`avg(case when is_base then <contribution_col> end)`) and
`net_effect_metric` (`avg(<forecast_col>) - avg(case when is_base then <contribution_col> end)`
= the sum of the per-feature mean contributions = the waterfall's Total).

### 5. Dashboard section "Explanation (SHAP)"

#### 5.1 Charts

Appended as the last section of both dashboards; all charts read the explanation dataset and,
as today, share their names across the two dashboards.

Row 1 — four KPI tiles (`big_number_total`, width 3, height 24), each the mean per period of
the current selection: **Base value** (`base_value_metric`; subheader
`<unit>; model expected value`), **Forecast (selection)** (`AVG(forecast_col)`), **Actual
(selection)** (`AVG(actual_col)`), **Net feature effect** (`net_effect_metric`, signed format;
subheader `<unit>; forecast − base`).

Row 2 — **SHAP waterfall** (`waterfall`, width 8, height 46): `x_axis = component_label`,
`metric = contribution_metric`, adhoc SQL filter `not is_base`, `show_total = true` with
`total_label = "Net effect"`, `increase_label = "Pushes forecast up"`,
`decrease_label = "Pushes forecast down"`, `show_value = true`, `show_legend = true`,
`y_axis_format = axis_format`, `y_axis_label = unit`, `x_ticks_layout = auto`, `row_limit = 100`,
Superset's default increase/decrease/total colours. **Feature values & contributions**
(`table`, aggregate mode, width 4, height 46): group by `component_label`; metrics
`min(component_order)` as `Order` (also the sort metric, ascending), `avg(feature_value)` as
`Feature value` (format `,.2~f`), `contribution_metric` (format `contribution_format`). The base row (`00 base`, empty feature value) is
included, so the table's contribution column sums to the forecast.

Row 3 — **Contributions by period** (`echarts_timeseries_bar`, width 12, height 44):
`x_axis = time_code`, `groupby = [component_label]`, `metrics = [contribution_metric]`,
`stack = "Stack"`, filter `not is_base`, legend at the top, `y_axis_format = axis_format`,
`y_axis_title = unit`, `row_limit = 10000`, x axis ascending. Shows how each feature's push
varies over the selected day; it is excluded from the Period filter so it keeps the whole day
as context when one period is selected.

#### 5.2 Native filters

`build_native_filters` returns Run (unchanged), then Day, then Period; the two new filters
target the explanation dataset:

- **Day** — `filter_select` on `trade_date_label`, single-select, optional
  (`enableEmptyFilter = false`: no day = the run-wide mean decomposition),
  `cascadeParentIds = ["NATIVE_FILTER-run"]` so the options are the selected run's days,
  `sortAscending = false`. Explicit on-load default = the default run's last delivery day: the
  SQL Lab lookup that resolves the default run (`latest_run_label`) is generalised to
  `latest_run`, returning `(run_label, last_day)` with `last_day = max(date_key)` of that run
  formatted `yyyy-MM-dd`; when the lookup fails both filters fall back to `defaultToFirstItem`.
  Scope: `rootPath = ["ROOT_ID"]`, `excluded` = every chart outside the section.
- **Period** — `filter_select` on `period_label`, single-select, optional, no default,
  `sortAscending = true`. Scope: the section's charts except **Contributions by period**.

#### 5.3 Optional extra: cross-filter from Worst days

Add a `json_metadata.chart_configuration` entry scoping the Worst-days table's cross-filter to
the section's charts plus the 30-minute detail chart, so clicking a date row selects that day.
Implemented last and only kept if it behaves in the live dashboard. Known caveats to verify
there: a cross-filter and the Day native filter combine (AND) — if they disagree the section
shows no data until one is cleared — and the value a table emits for a `DATE` cell must filter
`date_key` correctly.

### 6. Testing

- pytest (100 % coverage gate, `just test`): frames (the validation rules of §1), `TaskSpec`
  (derived names, the `forecast_` prefix check), the default `contributions()` (`None`),
  `SlidingWindowLightGbmStrategy.contributions()` through the demand and spot LightGBM
  strategies (one base row per predicted period; feature rows in feature order with the
  recorded values, `time_code` included; additivity to the forecast within 1e-6; `RuntimeError`
  before any prediction; a re-predicted day appears once), publish (alignment to the result,
  the missing-period error, `published_at` pass-through, DDL and partition overwrite in the
  session's temp warehouse), the scripts (rows land in `pma_ml.<task>_forecast_contribution`
  with the `contribution_table` tag; spot `previous_day` publishes nothing and sets no tag),
  and the dashboard builder against `FakeSupersetSession` (explanation SQL and columns, every
  new param builder, the three filters' targets / scopes / cascade / defaults, `latest_run`,
  `build_dashboard` creating two datasets and the seven new charts in the new section,
  idempotent rebuild).
- `just lint`, `just mypy`, `just checkov` unchanged in scope.
- dbt: `just dbt build` (contracts, generic and singular tests) after a real run.
- End to end in the devcontainer: `just python scripts/demand_backtest.py --days 30`,
  `just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution`,
  `just python scripts/create_forecast_dashboard.py`, open the Demand dashboard, pick a day
  and a period, screenshot the section (Playwright): waterfall bars and Total, the four tiles
  (Base + Net effect = Forecast), the table, and the other sections unchanged by the new
  filters.

### 7. Documentation

- `CLAUDE.md`: the `create_forecast_dashboard.py` bullet (new section, Day/Period filters, two
  datasets per dashboard), the forecast write-back paragraphs (the contribution tables,
  `contributions()`, the `contribution_table` tag), the demand task paragraph.
- `docs/research/demand/README.md` and `docs/research/spot_price/README.md`, "Segments reported
  by the tooling": the per-day / per-period SHAP waterfall.
- This spec and its implementation plan under `docs/superpowers/`.

### 8. Rollout

Runs published before this change have no contributions, so the section is empty for them.
After merge: re-run the kept demand baseline (`lightgbm_msm_popw_daytype`, tokyo) over the
R-003 window with the same `--start-date` / `--end-date` / `--train-start` as its E-001 run,
and the spot `lightgbm` baseline; `just dbt build`; rebuild both dashboards.

## Risks and notes

- The waterfall orders by label, hence the zero-padded `component_label` prefix; the labels are
  the raw feature names (`popw_forecast_temperature_c`, …) — unambiguous and free; a
  friendly-name map can be added to `DashboardSpec` later if wanted.
- The waterfall's value axis includes zero, hence the base value as a tile (decision 2).
- Row multiplicity in the explanation dataset (one row per component) makes SUM-type metrics
  on the forecast/actual columns wrong; the builders only use AVG.
- Volume is small (~280k rows per two-year demand run, ~10 components per period at most);
  no partitioning beyond `run_id` is needed.
