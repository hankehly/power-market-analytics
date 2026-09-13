# Feature naming

Every feature has two names.

| Name | Example | Used by |
|---|---|---|
| **Physical name** | `lag_2d_demand_kwh` | The mart column. Code, presets, `--add` / `--drop`, Feast, LightGBM, MLflow params and the stored contribution and importance rows. |
| **Expression** | `LAG(demand_kwh, 2d)` | People. Charts, dashboards, the feature catalogue and the MLflow plots. |

The physical name never changes: stored runs refer to it. The expression can be
edited at any time, and every label follows on the next build.

The expression is written once, as `meta.expression` in the mart's YAML. The
generator (`just feature-views`) copies it into the Feast field's `expression`
tag and into `dim_feature`, the feature dimension every label reads.

## Rules for expressions

1. **Base value.** Use the column of the fact or dimension the mart reads.
   - Prefix it with `forecast_` when it comes from a forecast fact
     (`forecast_temperature_c`, `forecast_max_demand_mw`).
   - Qualify it when two facts use the same column name for different series:
     `power_usage_demand_kwh` is the でんき予報 hourly load, `demand_kwh` the
     A-1 actuals.
2. **The mart's grain is implied.** A period mart works on the same `time_code`
   across days, and an hour mart on the same hour. Weather without a qualifier
   means the area's representative station.
3. **Primitives** use UPPER_SNAKE names, taken from featuretools where one exists.
4. **Lag and difference offsets are positional:** `LAG(x, 2d)`, `DIFF(x, 7d)`.
   A suffix ` by <col>` means group-by.
5. **Window arguments** come in this order: `gap` (how far back the newest term
   is), `window` (how many terms), `step`, `halflife` (in steps).
6. **Composition** reads outer to inner. The outermost primitive is the step
   applied last. Arithmetic between expressions is written infix:
   `EWA(…) - EWA(…)`, `SIMILAR_DAY(…) / 2`.
7. **The description holds the rest:** null handling, renormalising,
   complete-day rules.

A column passed through unchanged keeps its name as its expression
(`day_of_month`).

## Primitives in use

| Primitive | Meaning | Example |
|---|---|---|
| `LAG(x, n)` | `x` n before the delivery day. | `LAG(demand_kwh, 7d)` |
| `DIFF(x, n)` | `x` minus `x` n earlier. | `LAG(DIFF(demand_kwh, 7d), 2d)` |
| `DAILY_MEAN` / `DAILY_MAX` / `DAILY_MIN` / `DAILY_RANGE` | Over one day's periods. | `LAG(DAILY_MAX(demand_kwh), 2d)` |
| `ROLLING_MEAN(x, gap, window, step)` | Mean of `window` terms, `step` apart, the newest `gap` back. | `ROLLING_MEAN(demand_kwh, gap=7d, window=4, step=7d)` |
| `ROLLING_MEDIAN` / `ROLLING_STD` | The same window's median and sample standard deviation (`n - 1` in the denominator). | `ROLLING_STD(demand_kwh, gap=7d, window=4, step=7d)` |
| `ROLLING_TREND(x, gap, window, step)` | The least-squares slope of the same window against time, per `step`. | `ROLLING_TREND(demand_kwh, gap=7d, window=4, step=7d)` |
| `ROLLING_ZSCORE(x, n, gap, window, step)` | `(LAG(x, n) - ROLLING_MEAN(x, gap, window, step)) / ROLLING_STD(x, gap, window, step)`: how unusual `x` n back was against the window. | `ROLLING_ZSCORE(demand_kwh, 7d, gap=14d, window=3, step=7d)` |
| `EWA(x, gap, window, step, halflife)` | The same window, weights halving every `halflife` steps. | `EWA(temperature_c, gap=2d, window=7, step=1d, halflife=1)` |
| `… by col` | The window runs over the days with the delivery day's value of `col`. | `ROLLING_MEAN(demand_kwh, gap=2d, window=4) by day_type` |
| `MEAN(x, weight)` | Over the area's stations, weighted by `weight`. | `MEAN(forecast_temperature_c, weight=population)` |
| `DAYS_SINCE(flag)` / `DAYS_UNTIL(flag)` | Calendar days to the nearest day the flag is true. | `DAYS_SINCE(is_holiday)` |
| `SIMILAR_DAY(x, gap, window)` | `x` on the most similar of `window` candidate days, the newest `gap` back. | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61) / 2` |

## Adding or changing a feature

1. Add the column to its mart with `meta.feature`, `meta.categorical` and
   `meta.expression`.
2. Run `just feature-views`. It rewrites `views.py`, `fct_feature_value.sql` and
   `dim_feature.sql`, and refuses a tagged column without an expression, or a
   name or an expression another feature already has.
3. Build the mart and `dim_feature` (`just dbt build --select <mart> dim_feature`).
   The dashboards join `dim_feature` at query time, so they need no rebuild.
   Restart `just feast-ui` to see the new tag there.

The singular test `assert_dim_feature_covers_every_tagged_column` fails the build
when step 2 was skipped.

## Retiring a feature

Remove the column from its mart, then add a row to the seed
`dbt/seeds/retired_features.csv` with its physical name, expression and
description. Old runs still name the feature, and the row keeps their labels.

## Where the expression shows

- **Feature Catalogue** dashboard in Superset: every feature, searchable, with
  its view, physical name, grain, type and description. One line per feature;
  hover a cut-off cell to read all of it.
- **Forecast dashboards:** the SHAP waterfall, the component tables,
  Contributions by period, the Explanation-vs-baseline section and the Feature
  importance section.
- **Feature-value datasets** (`<task>_feature_values`, `feature_values_all`): a
  `feature_expression` column.
- **MLflow runs:** the SHAP beeswarm and bar plots, the permutation importance
  plot, and a `feature_expression` column in `permutation_importance.csv`. Runs
  logged before this convention keep physical names in their images.
- **Feast UI:** the `expression` tag on each feature's page. Its lists show only
  physical names.
