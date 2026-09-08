# Permutation feature importance for the forecast backtests

Status: proposed 2026-09-08, revised the same day to compute with scikit-learn at the
researcher's request. Awaiting review. Nothing is implemented.

## Objective

Show, for each backtest run, how much the model depends on each feature: the permutation
feature importance, measured as the increase in the run's MAE when that feature's column
is shuffled. Two surfaces, like the SHAP contributions today:

- **Superset**: a "Feature importance" section on each dashboard's Explanation tab.
- **MLflow**: a per-feature CSV and a bar plot logged to the run, next to `daily_errors.csv`
  and `shap_feature_importance_plot.png`.

Permutation importance answers a different question from the SHAP plots the runs already
carry. SHAP says how much each feature moved the forecast. Permutation importance says how
much the error grows without the feature. A feature can move the forecast a lot and still
be replaceable by a correlated one.

## Decisions (proposed)

1. **scikit-learn computes it.** `sklearn.inspection.permutation_importance` runs the
   shuffle-and-score loop. It gets a small estimator that routes each row to the model that
   forecast its day, so the importance is walk-forward and out of sample: the baseline is
   the run's own MAE on the rows the run scored. Five repeats, seed 0.
2. **Run grain in the warehouse.** One row per run × feature × repeat, scikit-learn's
   `importances` array as rows. This gives the ranking and the spread over repeats. It does
   not give by-month or by-day-type importance: scikit-learn returns run-level scores only.
3. **Both tasks.** The computation lives in the shared LightGBM base class and the dashboard
   builders are shared, so demand and spot price get it together. Demand is the one exercised.
4. **A new optional strategy hook**, `permutation_importance(run, …)`, like `contributions()`.
   A strategy with nothing to shuffle (spot `previous_day`) returns None and writes nothing.
5. **Superset section, not a tab.** The section sits at the bottom of the Explanation tab,
   outside the Day filter: importance describes a run, not a day. The tab is renamed from
   "Explanation (SHAP)" to "Explanation".
6. **MLflow gets a summary, not metrics.** `permutation_importance.csv`,
   `permutation_importance_repeats.csv` and `permutation_importance_plot.png`, plus two
   params. No per-feature metrics: feature sets differ across strategies, so metric columns
   would be sparse, and the Compare tab is the cross-run surface.
7. **No `std` model.** The table has no time axis, so staging feeds the fact directly.
8. **scikit-learn becomes a declared dependency.** It is installed today (1.9.0, pulled in by
   lightgbm, mlflow and shap) but not named in `pyproject.toml`; importing it directly means
   declaring it, as certifi and urllib3 were. It ships no type stubs, so `sklearn.*` joins
   the mypy ignore list next to `shap.*`.

## Background: what exists today

- `SlidingWindowLightGbmStrategy` (`forecasting/lgbm.py`) refits every 7 days on a 730-day
  window: about 52 models per 365-day run. Only the newest booster is kept. Each day's
  feature values are already recorded in `_shap_records`.
- MLflow logs `shap_feature_importance_plot.png` (mean |SHAP| over the eval rows) and the
  contributions are published per period. Nothing measures the error's dependence on a
  feature.
- The `contributions()` hook, `publish.py`, the `pma_ml` source, the `stg → std → fct`
  models and the explanation dataset are the pattern this design copies.
- scikit-learn 1.9.0, checked on 2026-09-08 with a two-model probe: `permutation_importance`
  rejects a bare object with only `predict` and wants a `BaseEstimator` subclass with `fit`
  (a no-op is enough); rows keep their order on every scoring call, so routing by row
  position is safe; `random_state` reproduces the result exactly; with
  `scoring="neg_mean_absolute_error"` its `importances` are ΔMAE in the task's unit. On the
  probe the planted signal scored 3.3 and a noise column 0.0.

## Scope

In scope: the routing estimator, the strategy hook, the frames, the write-back, the dbt
models and tests for both tasks, the MLflow artifacts, the Superset section and the tab
rename, the script flag, the dependency, unit tests, documentation, and the rollout on the
local stack.

Out of scope: importance by month or day type (needs a shuffle loop of our own that keeps
per-day errors; a follow-up if wanted); an "Importance vs baseline" section on the Compare
tab (the same self-join as the explanation comparison; a follow-up); importance on the
training window; any change to the compare scripts or the accuracy marts.

## Design

### 1. Method

Rows = the run's scored periods (those with an actual), the rows the accuracy mart holds.
`X` = their feature values as the models saw them, `y` = the actuals.

scikit-learn scores the estimator once on `X` (the baseline: the run's MAE, since every row
is predicted by the model that forecast it), then for each feature and each of the 5
repeats shuffles that one column across all rows, scores again, and records the
difference. The same permutations are used for every feature (its design, for
reproducibility). Output: `importances` (features × repeats) = permuted MAE − MAE,
`importances_mean` and `importances_std` per feature.

Importance % = 100 × (permuted MAE − MAE) / MAE, computed by us from the same numbers.

Categorical features (`day_type`) are shuffled as codes like any column.

Caveat, to be stated on the dashboard and in the docs: correlated features share
importance. Shuffling the observed temperature while the forecast temperature stays puts
pairs before the model that never occur, and each of the two understates their joint value.
Read a number as "what the model loses without this column".

Rejected: importance on each refit's training window (in sample: memorised lags look
important); calling scikit-learn once per refit on its own 7 days (`month` and `day_type`
barely vary inside a week, so they would score near zero).

### 2. Computation and hook

- `SlidingWindowLightGbmStrategy` keeps every refit's booster (`_models`, a list) and the
  index of the model that scored each day (`_model_of_day`). About 52 boosters of ~1.5 MB
  per 365-day run.
- New module `forecasting/importance.py`:
  - `WalkForwardPredictor(RegressorMixin, BaseEstimator)`: holds the boosters and one model
    index per row; `fit` returns self; `predict(X)` scores each row, by position, with its
    model.
  - `permutation_importance(rows, models, feature_cols, *, n_repeats, seed)` →
    `PermutationImportance`: builds the estimator, calls scikit-learn with
    `scoring="neg_mean_absolute_error"` and `random_state=seed`, and shapes the result into
    the frame. Testable without a backtest.
- `ForecastStrategy.permutation_importance(run, *, n_repeats=5, seed=0)` →
  `PermutationImportance | None`, default None. The LightGBM base aligns the recorded rows
  to `run.result` (inner merge, one-to-one; a scored period without a record is an error, as
  for contributions), adds each row's model index, and calls the module.
- Frames (`forecasting/frames.py`):
  - `PermutationImportance`: grain (`feature`, `repeat_index`); columns `feature_order`
    (1-based position in `feature_cols`), `n_periods` (the scored rows), `mae` (the
    baseline) and `permuted_mae`. `n_periods` and `mae` repeat on every row. Checks:
    `n_periods` ≥ 1 and constant, `mae` ≥ 0 and constant, `permuted_mae` ≥ 0,
    `feature_order` constant per feature and covering 1 … n, every feature has repeats
    0 … n − 1.
  - `PermutationImportance.summary()`: one row per feature — `feature`, `feature_order`,
    `mae`, `permuted_mae` (mean over repeats), `importance_mae` (scikit-learn's
    `importances_mean`), `importance_std` (its `importances_std`, a population std),
    `importance_pct`, `n_repeats`.
  - `ForecastImportanceRecords`: the frame plus `run_id`, `strategy`, `area_code`,
    `published_at`.
- `TaskSpec` derives `importance_table` = `forecast_table + "_importance"` and the two
  measure names from the forecast column, the `contribution_col` rule: `mae_demand_kwh` /
  `permuted_mae_demand_kwh` (demand), `mae_price_jpy_kwh` / `permuted_mae_price_jpy_kwh`
  (spot).

### 3. Write-back

`publish.build_importance_records` and `publish_importance_records`: parquet partitioned by
`run_id`, dynamic partition overwrite, `published_at` reused from the forecast records, the
same mechanics as the contributions. Each backtest script calls the hook after publishing
the contributions and before `diagnostics`, sets the tag `importance_table`, logs the params
`permutation_repeats` and `permutation_seed`, and gains `--importance-repeats` (default 5).
The seed is fixed at 0.

### 4. dbt

- Source `pma_ml.<task>_forecast_importance` in `models/raw/ml.yml`.
- `stg_ml__<task>_forecast_importance`: as is, with the `REFRESH TABLE` pre-hook.
- `fct_<task>_forecast_importance`: `area_key`, `run_id`, `strategy`, `feature`,
  `feature_order`, `repeat_index`, `n_periods`, the two MAE columns, `published_at`. Grain
  run × area × feature × repeat. Enforced contract, unique combination test, relationship to
  `dim_area`, `feature_order` ≥ 1, `repeat_index` ≥ 0, `n_periods` ≥ 1, MAEs ≥ 0. The
  description says the measures are averages: aggregate over repeats within one feature,
  never across features or runs.
- Singular test `assert_fct_<task>_forecast_importance_reconciles_with_accuracy`: for every
  run, `n_periods` equals the accuracy mart's period count and `mae` its mean absolute
  error, within a relative 1e-9. The baseline is the run's own error, so this ties the new
  fact to the mart.
- After a run: `just dbt build --select +fct_<task>_forecast_accuracy
  +fct_<task>_forecast_contribution +fct_<task>_forecast_importance`.

### 5. MLflow artifacts

- `permutation_importance.csv`: the summary of §2, in the task's unit.
- `permutation_importance_repeats.csv`: the frame (one row per feature × repeat).
- `permutation_importance_plot.png`: horizontal bars of `importance_mae` sorted descending,
  error bars = `importance_std`, title `strategy, area`; a `forecasting/plots.py` function.

### 6. Superset

Dataset `<task>_forecast_importance`: the fact joined to `dim_area` (`run_label` through
`RUN_LABEL_SQL`, `area_code`). Columns `feature`, `feature_order`, `feature_label`
(`01 time_code`, the `component_label` prefix, so the table keeps the model's order),
`repeat_index`, `n_periods`, and the two MAEs in the display unit (demand: ÷ 1000 → MWh).
The Run filter reaches it through `run_label`.

Metrics, each grouped by feature: MAE `avg(mae)`, Permuted MAE `avg(permuted_mae)`,
Importance (ΔMAE) `avg(permuted_mae) − avg(mae)`, Importance %
`100 × (avg(permuted_mae) − avg(mae)) / avg(mae)`, Std over repeats
`stddev_pop(permuted_mae)`.

Section "Feature importance" at the bottom of the Explanation tab:

| Row | Chart | Dataset | What it shows |
|---|---|---|---|
| 1 | **Permutation importance** (6) | importance | bars of ΔMAE per feature, sorted descending; tooltip Importance % |
| 1 | **Mean \|SHAP\| by feature** (6) | explanation | bars of `avg(abs(contribution))` per component, base excluded: attribution next to dependence |
| 2 | **Feature importance table** (12) | importance | `feature_label`, MAE, Permuted MAE, ΔMAE, Std over repeats, Importance % |

All three are outside the Day filter and are not targets of the day tables' cross-filters.
The section's description carries the correlated-features caveat in one sentence.

Runs published before this change show an empty section until they are re-run, as with the
contributions.

### 7. Builder changes

- `DashboardSpec` gains `importance_dataset_name`, `importance_table`,
  `importance_value_columns_sql`, `importance_value_columns` and the five metric
  properties; `IMPORTANCE_DATASET_SQL_TEMPLATE` and `COMMON_IMPORTANCE_COLUMNS` follow the
  explanation template.
- New param builders: `importance_bar_params`, `importance_table_params`,
  `mean_abs_shap_params`.
- `build_explanation_tab` takes the importance dataset id and appends the section;
  `build_dashboard` creates the fifth dataset and excludes the section's charts
  (`RUN_LEVEL_CHART_NAMES`) from the Day filter and the cross-filter targets.

### 8. Cost

Per 365-day run: 1 + features × 5 scoring passes over ~17,500 rows, each routed through
~52 boosters: seconds. Memory: ~80 MB of boosters. Table: features × 5 rows per run.

## Verification

- Unit tests, coverage 100 %: the module on a planted signal (a target driven by one feature
  plus noise: that feature's importance is large, a noise feature's is zero, as on the
  probe); the router's `fit` and positional `predict`; the frame's `mae` equals the
  backtest's MAE exactly; the same seed reproduces the frame and another seed changes the
  permuted MAEs; frame validation; the publish round-trip on the local Spark fixture; both
  scripts with the fake strategy (artifacts logged, table written, the flag, the None
  branch); the dashboard (pinned dataset SQL and columns, the metrics' SQL, the section
  layout, the Day-filter exclusions, the tab title).
- `just lint`, `just mypy`, `just test`, `just dbt parse`, then `just dbt build` on the local
  stack: the reconciliation test passes.
- Live: rerun the Tokyo baseline strategy (`lightgbm_msm_popw_daytype_simday`) on the
  matched window, rebuild the three marts, rebuild the dashboards, and check in the browser
  that the section's MAE equals the run's MAE on the Accuracy tab and that the CSV's
  `importance_mae` matches the bars. Screenshots for the PR's Proof section.

## Documentation

- `CLAUDE.md`: the two backtest bullets (the flag, the third build selector), the dashboard
  bullet (the section, the tab title), an "Importance" paragraph after "Explanations" in
  the architecture section, and the scikit-learn line in Gotchas next to scipy's.
- `docs/README.md` forecast analysis; `docs/research/{demand,spot_price}/README.md`
  "Segments reported by the tooling".

## Rollout

1. Merge; rerun the Tokyo and Kansai demand baselines (older runs have no importance rows).
2. `just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution
   +fct_demand_forecast_importance`.
3. `just python scripts/create_forecast_dashboard.py` (both dashboards).

## For the researcher's review

The decisions I am least sure of, each with the default the spec takes:

1. Segment views. By-month and by-day-type importance are out (decision 2), because
   scikit-learn returns run-level scores only. A shuffle loop of our own that keeps per-day
   errors would give them; say so if you want them and I will bring that variant back.
2. Both tasks (decision 3) against demand only. Demand only needs an optional-dataset branch
   in the dashboard builder instead of six copied dbt files.
3. The mean |SHAP| chart (§6, row 1). It reuses the explanation dataset; drop it if the
   side-by-side is not wanted.
