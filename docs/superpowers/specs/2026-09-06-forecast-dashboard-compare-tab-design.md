# Compare tab for the forecast-analysis dashboards

## Objective

Let the researcher compare a backtest run against a baseline run inside one Superset
dashboard, without opening two browser tabs and flipping between them. The dashboard
shows the difference between the two runs, by segment and by day, with the two runs
named by their roles: **Candidate** (the existing Run filter) and **Baseline** (a new
filter).

The feature applies to both forecast-analysis dashboards (`spot_price`, `demand`).
`scripts/create_forecast_dashboard.py` builds both from one set of chart builders, so a
demand-only tab would cost more than a shared one.

## Decisions (option B, approved 2026-09-06)

1. **A third top-level tab, "Compare".** Accuracy and Explanation (SHAP) stay as they are.
2. **A comparison dataset built with Jinja.** A third virtual dataset per dashboard
   self-joins the accuracy mart: the candidate run against the baseline run, on delivery
   day, time code and area. The baseline run comes from the Baseline filter through
   Superset's `filter_values()`. This needs `ENABLE_TEMPLATE_PROCESSING`, which is now on
   in `conf/superset/superset_config.py` (it was off; verified on 2026-09-06 with a probe
   dataset: the value of a filter in a chart-data request reaches the dataset SQL, the
   outer WHERE on the same column also applies, and the SQL renders without a filter).
3. **Only matched periods count.** The join is inner, so a segment compares the same
   periods for both runs, as `compare_<task>_runs.py` asserts. A coverage tile shows when
   the windows differ.
4. **Show the difference, not two levels.** Segment charts show ΔMAE % as diverging bars
   (better below zero, worse above); tiles show the candidate's delta with a colour;
   only the 30-minute detail shows both runs' levels.
5. **Roles get fixed colours.** Candidate, Baseline, Actual, Better and Worse are fixed
   series names with fixed colours (§5), so the same colour means the same thing for any
   pair of runs.
6. **The bootstrap CI over days stays in the compare scripts.** It is not a SQL aggregate.
   The tab shows what SQL can compute: the share of days the candidate is lower, the mean
   and median daily ΔMAE, and the running total of the error reduction.
7. **Run labels get the strategy.** `run_label` becomes
   `published_at | area | strategy | run_id prefix`, and its SQL lives in one constant.
8. **The leaderboard shows each run's window** (first day, last day, days), so a matched
   baseline is recognisable in the list.

## Background: what exists today

- The Run filter is single-select, required, and scopes every chart except the
  leaderboard. Every chart shows one run's level. The only cross-run element is the
  leaderboard: overall metrics, no segments, no window columns. The demand mart holds 12
  runs, 9 on the matched 729-day window (34,954 periods, 2024-08-18 to 2026-08-17) and 3
  short test runs (720 to 1,872 periods).
- `scripts/compare_<task>_runs.py` computes the matched comparison the researcher wants
  (overall, by segment, daily paired, bootstrap CI), but only as markdown.
- Superset 6.1.0 (`docker/superset/Dockerfile`), checked in the installed bundle: the Big
  Number tile has conditional formatting; the heatmap has `value_bounds` and the diverging
  scheme `blue_white_yellow`; the time-series line has the `cumsum` rolling function;
  the dashboard metadata takes `label_colors`. A native filter applies to a chart on
  another dataset when that dataset has a column of the same name (how the Run filter
  reaches the explanation dataset today).

## Scope

In scope: the Superset feature flag, the comparison dataset template and its column
metadata, the Baseline filter and its default, the Compare tab and its charts, fixed
label colours, the run-label change, the leaderboard columns, the `--baseline-run`
option, tests, documentation, the rollout on the local stack.

Out of scope: a SHAP comparison (a "versus baseline" waterfall of contribution deltas can
reuse the same self-join on the contribution fact later), the bootstrap CI, any change to
the compare scripts, to the marts, or to the backtest scripts.

## Design

### 1. Comparison dataset `<task>_forecast_comparison`

One row per matched period of the (candidate, baseline) pair. SQL template
`COMPARISON_DATASET_SQL_TEMPLATE`, formatted per task like the other two templates.

```sql
{% set candidate = filter_values('run_label') %}
{% set baseline = filter_values('baseline_run_label') %}
with runs as (
  select f.*, <RUN_LABEL_SQL> as run_label
  from <accuracy_table> f join pma_curated.dim_area a on f.area_key = a.area_key
),
candidate as (
  select *, count(*) over () as candidate_periods from runs
  where {% if candidate %} run_label = '<escaped candidate[0]>' {% else %} 1 = 0 {% endif %}
),
baseline as (
  select * from runs
  where {% if baseline %} run_label = '<escaped baseline[0]>' {% else %} 1 = 0 {% endif %}
)
select
  <the shared calendar / period / area context of the analysis dataset>,
  c.run_id, c.run_label, c.strategy, c.published_at,
  b.run_id as baseline_run_id, b.run_label as baseline_run_label,
  b.strategy as baseline_strategy,
  c.candidate_periods,
  <task value block: forecast, baseline_forecast, actual, error, baseline_error,
   abs_error, baseline_abs_error, delta_abs_error = abs_error − baseline_abs_error,
   in the display unit>,
  avg(abs_error)          over (partition by c.date_key) as daily_abs_error,
  avg(baseline_abs_error) over (partition by c.date_key) as daily_baseline_abs_error,
  avg(delta_abs_error)    over (partition by c.date_key) as daily_delta_abs_error
from candidate c
join baseline b on b.date_key = c.date_key and b.time_code = c.time_code
  and b.area_key = c.area_key
join pma_curated.dim_area a ... join dim_delivery_period p ... join dim_date d ...
```

- Both sides are pinned inside the SQL, so the join touches two runs. Superset also
  applies the two filters as an outer WHERE on `run_label` and `baseline_run_label`; both
  match, and the dataset exposes both columns so no filter lands on an unknown column.
- Without a value on either side the SQL yields no rows (`1 = 0`). The charts then show
  "No data" rather than a misleading zero delta. Quotes in a label are doubled before
  interpolation.
- `candidate_periods` = the candidate's period count before the join, so a tile can show
  the share of them the baseline also scored.
- The daily columns are window averages over the day's matched periods, one constant
  per day, so tiles and tables can aggregate per day without a second dataset.
- `main_dttm_col` = `trade_datetime`, as for the other datasets. Column metadata is
  written explicitly like today (`COMMON_COMPARISON_COLUMNS` + a per-task
  `comparison_value_columns`), so Superset never has to run the SQL to learn the columns.

### 2. Baseline filter and its default

- `NATIVE_FILTER-baseline`, name "Baseline", single-select, required, sorted newest
  first, reads its options from a new alias column `baseline_run_label` on the analysis
  dataset (the same label expression as `run_label`). Scope: the Compare tab's charts
  only, through the existing `excluded` mechanism (the Day filter is scoped the same way).
- Description: "Reference run; the Compare tab shows the Run (candidate) against it over
  the periods both runs scored".
- Default on load: the newest *other* run with the same area, first day, last day and
  period count as the default Run. `run_defaults()` runs one query listing the mart's
  runs (label, area, first / last day, period count, newest first) and applies the rule
  and the `--baseline-run` override in Python, returning a `RunDefaults` (run label, last
  day, baseline label or None); a `--baseline-run <run_id or 8-char prefix>` that matches
  no run keeps the rule and logs a warning. With no matching run the filter has no
  default and the tab shows "No data" until one is picked.
- Choosing the candidate itself is allowed; every delta is then zero.

### 3. Metrics (properties on `DashboardSpec`)

All on the comparison dataset, `a` = `abs_error`, `b` = `baseline_abs_error`:

| Label | SQL |
|---|---|
| Baseline MAE | `avg(b)` |
| Candidate MAE | `avg(a)` |
| ΔMAE | `avg(a) − avg(b)` |
| ΔMAE % | `100 × (avg(a) − avg(b)) / avg(b)` |
| Better / Worse | `least(x, 0)` / `greatest(x, 0)`, two series of one stacked bar; `x` = ΔMAE % on the segment bars, ΔMAE on the daily bars |
| Δ\|bias\| | `abs(avg(error)) − abs(avg(baseline_error))` |
| ΔWAPE | `sum(a) / sum(actual) − sum(b) / sum(actual)` |
| Matched coverage | `count(*) / max(candidate_periods)` |
| Matched days | `count(distinct date_key)` |
| Days candidate lower | `count(distinct case when daily_delta_abs_error < 0 then date_key end) / count(distinct date_key)` |
| Median daily ΔMAE | `percentile(daily_delta_abs_error, 0.5)` |
| Error reduction | `sum(b) − sum(a)` (per day; the running total is the chart's `cumsum`) |

Formats: the spec's `number_format` / `signed_number_format` for unit values, `+.1f` with
a `%` subheader for ΔMAE %, `.1%` for shares.

### 4. The Compare tab

Every chart reads the comparison dataset. Titles say what the number is relative to.

1. **KPI row 1** (six tiles): Baseline MAE, Candidate MAE, ΔMAE, ΔMAE %, Δ|bias|, ΔWAPE.
   The four delta tiles are coloured by sign with Big Number conditional formatting:
   Better colour below zero, Worse colour above.
2. **KPI row 2** (four tiles): Matched coverage (subheader "share of the candidate's
   periods the baseline also scored"), Matched days, Days candidate lower, Median daily
   ΔMAE.
3. **Where the candidate wins** (section): stacked bars of Better / Worse (ΔMAE %) by
   time code (full width); by day part, day type and day of week (three across); by the
   spec's actual band and by year (two across). The tooltip shows the delta; the two
   MAE levels are on the tiles and the day tables. Then two heatmaps of ΔMAE %, year ×
   month and year × time code, on `blue_white_yellow` with `value_bounds` ±30 so white
   is "no change" and blue is better.
4. **Day by day** (section): daily ΔMAE as Better / Worse bars over the window
   (x = `date_key`, zoomable, full width); the running total of the error reduction as a
   line (`rolling_type: cumsum` over the daily `Error reduction`, full width): a steady
   slope is a broad gain, a few steps is a gain concentrated in a few days. Then two
   tables side by side, **Most improved days** and **Most worsened days**: date, day of
   week, day type, holiday name (`dim_date.holiday_name_ja`, added to the shared context
   of the comparison dataset), Baseline MAE, Candidate MAE, ΔMAE, ΔMAE %, ten rows each,
   sorted by ΔMAE ascending and descending.
5. **Detail** (section): forecast vs actual at the 30-minute grain with three lines,
   Actual, Candidate, Baseline (zoomable, full width).

Cross-filters: the two day tables emit to the Compare detail chart and the Explanation
tab's charts, like Worst days; Worst days also gains the Compare detail chart as a
target. `build_chart_configuration` takes a mapping of emitter to targets instead of one
emitter.

### 5. Fixed colours

`json_metadata.label_colors` on both dashboards, from Superset's default scheme so the
existing charts keep their look:

| Series | Colour | Meaning |
|---|---|---|
| Candidate | `#1FA8C9` (blue) | the run under test |
| Baseline | `#B2B2B2` (grey) | context |
| Actual | `#222222` (ink) | the truth |
| Better | `#1FA8C9` (blue) | ΔMAE below zero |
| Worse | `#FF7F44` (orange) | ΔMAE above zero |

Blue and orange are a cool / warm diverging pair that survives colour-vision deficiency;
the delta tiles use the same two colours; the heatmaps' blue-white-yellow scheme puts
"better" on the same blue pole. No red / green.

### 6. Builder changes

- `RUN_LABEL_SQL` (one constant, formatted with the fact and area aliases) replaces the
  three copies of the label expression; the label gains the strategy after the area.
  The tests' pinned SQL fixtures change accordingly.
- Leaderboard: three metrics `date_format(min(date_key), 'yyyy-MM-dd')` "First day",
  `date_format(max(date_key), 'yyyy-MM-dd')` "Last day" and `count(distinct date_key)`
  "Days", after "Periods".
- New builders: `comparison_kpi_params` (a Big Number with conditional formatting),
  `delta_bar_params`, `delta_heatmap_params`, `daily_delta_bar_params`,
  `cumulative_reduction_params`, `ranked_days_params(direction)`,
  `comparison_detail_params`. Existing builders are untouched except the leaderboard.
- `build_native_filters` gains the Baseline filter (keyword-only parameters);
  `run_defaults()` runs one query listing the mart's runs (label, area, first / last day,
  period count, newest first) and applies the rule and the `--baseline-run` override in
  Python, returning a `RunDefaults` (run label, last day, baseline label or None); `main`
  gains `--baseline-run`.
- `build_position_json` is unchanged: the tab is a third entry in `tabs`.

### 7. Superset configuration

`conf/superset/superset_config.py`: `FEATURE_FLAGS = {"ENABLE_TEMPLATE_PROCESSING": True}`,
with a comment naming the consumer. A running stack needs
`docker compose restart superset superset-mcp` (both mount the file). Jinja then also
works in SQL Lab; the stack is local-only with admin users, so no further hardening.

## Verification

- Unit tests (`tests/test_create_forecast_dashboard.py`, the fake Superset session): the
  comparison SQL pinned per task, the column metadata in select order, every new metric's
  SQL, the Baseline filter (targets, scope, default, required), the default-baseline
  query and `--baseline-run` override and its warning, the cross-filter mapping, the
  label colours, the leaderboard metrics, the tab layout (three tabs, chart ids in the
  right sections, widths summing to 12). Coverage stays at 100 %.
- `just lint`, `just mypy`, `just test`.
- Live: `just python scripts/create_forecast_dashboard.py`, then in the browser
  (Playwright) on the demand dashboard: Run = `008868fe…` (R-004 E-002), Baseline =
  `0a6b8a55…` (R-003 baseline). The tiles must show Baseline MAE 594.3 MWh, Candidate
  MAE 585.4 MWh, ΔMAE % −1.5, Matched coverage 100 %, and the day-type bars weekday
  −4.8 %, weekend +6.5 %: the numbers in
  `docs/research/demand/R-004-prior-year-load-lag.md`.
  Also: an empty Baseline shows "No data"; a short-window baseline (`cd8f65c1…`) shows a
  coverage below 100 %; the spot dashboard renders the tab.

## Documentation

- `CLAUDE.md`: the `create_forecast_dashboard.py` bullet (three tabs, the Baseline filter,
  the flag, `--baseline-run`), and the run-label format.
- `docs/README.md` dashboard section and `docs/research/{demand,spot_price}/README.md`
  "Segments reported by the tooling": the Compare tab next to the compare scripts.
- The rollout also republishes both dashboards.

## Rollout

1. Restart Superset with the flag (done on the local stack on 2026-09-06).
2. `just python scripts/create_forecast_dashboard.py` (both dashboards).
3. Live check as above; screenshots for the PR's Proof section.

## Follow-ups (second PR, 2026-09-06)

Taken after PR #49 merged, at the researcher's request.

1. **Explanation vs baseline.** A fourth dataset, `<task>_forecast_explanation_comparison`:
   the contribution fact self-joined like §1, on the periods both runs explained (one base
   row per period per run), one row per period × component of either run. A component only
   one run has keeps that run's contribution and gets 0 on the other side, so its whole
   contribution is the delta; feature values stay null where the run lacks the feature.
   Baseline-only components sort after the candidate's (`component_order` + 100, a
   three-digit label prefix). The filters' values are echoed as constant `run_label` /
   `baseline_run_label` columns because Superset's outer WHERE would otherwise drop the rows
   of a component the candidate lacks. A new Compare-tab section shows Δ base value, Δ net
   feature effect (Σ feature deltas per period) and Δ forecast tiles, a waterfall of
   per-component contribution deltas (candidate − baseline, mean per period, base row
   excluded) and a table with both runs' contributions. These charts are in the Day filter's
   scope and are targets of the day tables' cross-filters, like the Explanation tab.
2. **Tab builders.** `build_dashboard` delegates each tab to `build_accuracy_tab`,
   `build_explanation_tab` and `build_compare_tab`; each returns a `DashboardTab` (charts by
   name in creation order, layout sections). Creation order is unchanged, so the chart ids the
   tests pin only shift by the extra dataset.
3. **Day tables** are stacked full width, so the ΔMAE % column is no longer clipped.
4. **Null holiday names** render blank (`coalesce(holiday_name_ja, '')`) instead of
   Superset's "Not Applicable".
