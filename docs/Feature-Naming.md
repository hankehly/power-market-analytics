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
   An offset in days keeps the time of day. An offset in minutes moves along
   the timeline and crosses midnight: `DIFF(x, 30m)` is `x` minus the period
   before, and the period before period 1 is period 48 of the day before.
   A suffix ` by <col>` means group-by.
5. **Window arguments** come in this order: `gap` (how far back the newest term
   is), `window` (how many terms), `step`, `halflife` (in steps), then `rank`
   (which nearest day), `k` (how many nearest days), `weight` and `holidays`.
   `center=true` centres the window on the term instead of a `gap`.
   `time=HH:MM-HH:MM` keeps the periods of a day inside that time window.
   - **A pool of several windows** writes `gap` and `window` as tuples, read in
     pairs: `gap=(2d, 335d), window=(30, 60)` is 30 days from 2 days back and
     60 days from 335 days back.
   - **`holidays=last_year`** means a holiday takes the same holiday last year,
     when that day lies in the year-ago window and its load is public by the
     issue time, instead of a ranked pick.
6. **Composition** reads outer to inner. The outermost primitive is the step
   applied last. Arithmetic between expressions is written infix:
   `EWA(…) - EWA(…)`, `SIMILAR_DAY(…) / 2`.
7. **The description holds the rest:** null handling, renormalising,
   complete-day rules.

A column passed through unchanged keeps its name as its expression
(`day_of_month`). So does a class the mart computes that no primitive describes
(`day_type`, `special_period`): its levels are in the description.

## Primitives in use

| Primitive | Meaning | Example |
|---|---|---|
| `LAG(x, n)` | `x` n before the delivery day. | `LAG(demand_kwh, 7d)` |
| `DIFF(x, n)` | `x` minus `x` n earlier. | `LAG(DIFF(demand_kwh, 7d), 2d)`, `LAG(DIFF(demand_kwh, 30m), 7d)` |
| `DAILY_MEAN` / `DAILY_MAX` / `DAILY_MIN` / `DAILY_RANGE` | Over one day's periods, or its 24 hours when `x` is hourly, or the periods inside `time`. Without a `LAG` the day is the delivery day, which only a forecast can fill. | `LAG(DAILY_MAX(demand_kwh), 2d)`, `LAG(DAILY_MEAN(demand_kwh, time=06:00-10:00), 2d)`, `DAILY_MAX(MEAN(forecast_temperature_c, weight=population))` |
| `DAILY_ARGMAX(x)` | The time code of the day's highest `x`, or its hour ending, 1-24, when `x` is hourly; the earliest on a tie. | `LAG(DAILY_ARGMAX(demand_kwh), 2d)`, `DAILY_ARGMAX(MEAN(forecast_temperature_c, weight=population))` |
| `DAILY_TREND(x, time)` | The least-squares slope of `x` against time over the periods inside `time`, per hour: `2 (n Σtx - Σt Σx) / (n Σt² - (Σt)²)`, `t` the time code. | `LAG(DAILY_TREND(demand_kwh, time=06:00-10:00), 2d)` |
| `ROLLING_MEAN(x, gap, window, step)` | Mean of `window` terms, `step` apart, the newest `gap` back; with `center=true`, the terms around the current one. A `step` of `1h` runs along the clock, across midnight. | `ROLLING_MEAN(demand_kwh, gap=7d, window=4, step=7d)`, `LAG(ROLLING_MEAN(demand_kwh, window=3, step=30m, center=true), 7d)`, `ROLLING_MEAN(MEAN(temperature_c, weight=population), gap=2d, window=24, step=1h)` |
| `ROLLING_MEDIAN` / `ROLLING_STD` | The same window's median and sample standard deviation (`n - 1` in the denominator). | `ROLLING_STD(demand_kwh, gap=7d, window=4, step=7d)` |
| `ROLLING_MIN` / `ROLLING_MAX` | The same window's lowest and highest, over the values present. | `(LAG(demand_kwh, 2d) - ROLLING_MIN(demand_kwh, gap=2d, window=28, step=1d)) / (ROLLING_MAX(demand_kwh, gap=2d, window=28, step=1d) - ROLLING_MIN(demand_kwh, gap=2d, window=28, step=1d))` |
| `ROLLING_TREND(x, gap, window, step)` | The least-squares slope of the same window against time, per `step`. | `ROLLING_TREND(demand_kwh, gap=7d, window=4, step=7d)` |
| `ROLLING_ZSCORE(x, n, gap, window, step)` | `(LAG(x, n) - ROLLING_MEAN(x, gap, window, step)) / ROLLING_STD(x, gap, window, step)`: how unusual `x` n back was against the window. | `ROLLING_ZSCORE(demand_kwh, 7d, gap=14d, window=3, step=7d)` |
| `EWA(x, gap, window, step, halflife)` | The same window, weights halving every `halflife` steps. | `EWA(temperature_c, gap=2d, window=7, step=1d, halflife=1)` |
| `EWSTD(x, gap, window, step, halflife)` | The standard deviation with the same weights `w`, with the reliability-weight correction of pandas `ewm().std()`: `sqrt(Σw(x - m)² / (V1 - V2 / V1))`, `m` the `EWA`, `V1 = Σw`, `V2 = Σw²`. With equal weights it is `ROLLING_STD`. | `EWSTD(demand_kwh, gap=2d, window=5, step=1d, halflife=1)` |
| `… by col` | The window runs over the days with the delivery day's value of `col`. | `ROLLING_MEAN(demand_kwh, gap=2d, window=4) by day_type` |
| `CUM_SUM(x) by col` | The running total of `x` in time order within each value of `col`, the current term included. It stops at the first missing term: from there on it is null. | `CUM_SUM(MEAN(forecast_solar_radiation_mjm2, weight=population)) by trade_date` |
| `MEAN(x, weight)` | Over the area's stations, weighted by `weight`. | `MEAN(forecast_temperature_c, weight=population)` |
| `DISCOMFORT_INDEX(t, h)` | The 不快指数 of a temperature `t`, C, and a relative humidity `h`, %: `0.81 t + 0.01 h (0.99 t - 14.3) + 46.3` (木内 2001, eq. A1; [papers](research/papers.md)). It is the U.S. Weather Bureau's temperature-humidity index `T - (0.55 - 0.0055 h)(T - 58)`, `T` in F, written for C: the two are equal. Below 14.4 C a higher humidity lowers it. | `DISCOMFORT_INDEX(MEAN(forecast_temperature_c, weight=population), MEAN(forecast_relative_humidity_pct, weight=population))` |
| `DAYS_SINCE(flag)` / `DAYS_UNTIL(flag)` | Calendar days to the nearest day the flag is true. | `DAYS_SINCE(is_holiday)` |
| `SIMILAR_DAY(x, gap, window, rank, holidays)` | `x` on the `rank`-th most similar day of the pool: `window` candidate days, the newest `gap` back. | `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=1, holidays=last_year) / 2` |
| `SIMILAR_DAY_MEAN(x, gap, window, k, weight, holidays)` | The mean of `x` over the `k` most similar days of the pool, weighted by `weight`. `weight=inverse_distance` weighs each day by one over its distance. It weighs days, unlike `MEAN(x, weight=population)`, which weighs stations. | `SIMILAR_DAY_MEAN(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), k=3, weight=inverse_distance, holidays=last_year) / 2` |

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
