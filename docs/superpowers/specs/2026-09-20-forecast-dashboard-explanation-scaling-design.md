# Forecast dashboards: faster loads, and an Explanation tab that scales to hundreds of features

Date: 2026-09-20. Approved by the researcher the same day, with three choices:
Jinja pinning plus a summary mart, an aligned second chart for a feature's value,
and three sub-tabs.

## Why

The researcher reviewed the forecast dashboards after experiment #212 made `e212`,
a 104-feature preset, the Tokyo baseline. Two things broke.

**The views are slow.** Measured on run `34c506fb…` (`e212`):

| Query | Today | Run and day pushed into the scan |
|---|---|---|
| Accuracy KPI tile | 1.18 s | 0.40 s |
| Explanation chart, one day | 3.27 s | 0.56 s |
| Explanation chart, whole run | 10.5 s | 7.0 s |

The Explanation tab took 46 s to load in the browser. Superset's log shows every
chart averaging 7–14 s over three days, the worst 56 s, against gunicorn's 60 s
timeout. It is not a bug. Four things add up:

1. The Run and Day filters are on text the dataset SQL builds (`run_label`, a
   concat of four columns; `trade_date_label`, a formatted date). Spark cannot
   push a filter on built text into the parquet scan, so every chart reads the
   whole fact.
2. The contribution fact holds 20.1 M rows over 37 runs, and grows with features
   × runs. `e212` alone is 3.7 M rows (106 components × 35,000 periods).
3. A tab sends all its charts at once (Accuracy 19, Explanation 10) to a 4-core
   Spark. Nine explanation queries together took 13 s each against 3 s alone.
4. The run-level charts (mean |SHAP|, the waterfall with Day empty) add up 3.7 M
   rows on every view.

**The Explanation tab does not scale.** The waterfall draws 105 unlabeled bars,
"Contributions by period" stacks 105 series, the importance bars' long names
leave no room for the bars, and both tables and the waterfall carry
`row_limit: 100`, which already drops 6 of `e212`'s 106 components without saying so.

## What

Everything applies to both dashboards; they share their builders.

### 1. Pin the filters inside the dataset SQL

The analysis, explanation, comparison and explanation-comparison datasets read
the Run filter's value with Jinja `filter_values('run_label')` and add a
predicate on the fact's own column:

```sql
f.run_id like '<last 8 characters of the label>%'
```

`RUN_LABEL_SQL` ends with `substring(run_id, 1, 8)`, so the label's tail is the
run id's prefix; a test ties the two together. Spark pushes a prefix `like` into
the parquet scan. The explanation datasets pin the Day (`c.date_key = date '…'`)
and the Period (`c.time_code = …`) the same way.

Superset still applies its own filter on the label afterwards, so a pin can only
make the scan smaller; it never changes which rows come back. Without a filter
value there is no pin and the SQL reads everything, as today. The comparison
datasets keep `1 = 0` without a value, as today. A cascaded filter's option
query carries its parent's value in `queries[0].filters`, which is what
`filter_values` reads (checked in `superset/views/utils.py`), so the Day list is
pinned to the run too.

Left out: a result cache (stale after every `dbt build`), and partitioning the
facts (row-group statistics already give 0.56 s). The waterfall with Day empty
stays at about 7 s; Day defaults to the run's last day.

### 2. `fct_<task>_forecast_contribution_summary`

One aggregate fact per task, built from `fct_<task>_forecast_contribution`.

- Grain: `run_id × area_key × component`.
- Columns: `run_id`, `area_key`, `strategy`, `published_at`, `component`,
  `component_order`, `is_base`, `n_periods` (bigint),
  `mean_contribution_<unit>`, `mean_abs_contribution_<unit>` (`demand_kwh` /
  `price_jpy_kwh`, the contribution fact's unit).
- Enforced contract; `dbt_utils.unique_combination_of_columns` on the grain;
  `not_null` on every column; `relationships` to `dim_area`.
- A singular test per task: the summary's `n_periods` of the base row equals the
  count of base rows of the contribution fact, per run and area.

After a backtest the documented build becomes `+fct_<task>_forecast_accuracy
+fct_<task>_forecast_contribution_summary +fct_<task>_forecast_importance`; the
summary's `+` rebuilds the contribution fact.

A Superset dataset `<task>_forecast_contribution_summary` reads it, base row left
out: run label, feature expression, `feature_rank` (`row_number()` by mean
|contribution| within the run, ties to the name), `feature_pick` =
`concat(lpad(feature_rank, 3, '0'), ' ', expression)`, and the two means in the
display unit. It feeds the mean |SHAP| bars and the Feature filter's options. The
importance dataset left-joins the summary for a `mean_abs_contribution` column
and gains `importance_rank` (`row_number()` by ΔMAE within the run).

### 3. Accuracy tab

- **Run leaderboard**: removed, with `leaderboard_params`. The Run filter then
  applies to every chart.
- **Forecast vs actual (30-min detail)**: a third series, `Error (forecast −
  actual)`, on the same axis. The axis already starts at zero
  (`truncateYAxis: False`) and the unit is the same, so nothing is rescaled, and
  the hover popup lists all three. The proposal said a right-hand axis; one axis
  is simpler and avoids reading meaning into where two scales cross.
- **Worst days**: a new first column, **Explain**, a link that opens the
  Explanation tab with that Run and Day set:
  `/superset/dashboard/<slug>/?native_filters=(<Run and Day as rison>)#TAB-1`,
  built in the dataset SQL (`url_encode`; `!` and `'` escaped for rison). The URL
  form was checked in our Superset 6.1: the anchor opens the tab and both filters
  take their values. **Most improved days** and **Most worsened days** get the
  same column. If the table does not render the link as HTML, the column is
  dropped and the click-to-filter below stays as it is.
- The three day tables stop cross-filtering the Explanation tab. They still
  cross-filter the 30-minute detail charts and the Compare tab's
  explanation-vs-baseline section. So on the Explanation tab the Day filter is
  the only way a day is chosen, and the "clear Day first" rule goes away there.
  It has to: the top-10 ranking below runs inside the SQL over the pinned Day,
  and a cross-filter arrives after it.

### 4. Explanation tab: three sub-tabs

Only the open sub-tab runs its queries. `build_position_json` learns a tab with
`subtabs` instead of `sections`.

**Day overview**

- **SHAP waterfall**: the 10 features with the largest mean |contribution| in the
  selection (Run, Day, Period as pinned), largest first, then one `Other
  features` bar, then the net effect. `TOP_COMPONENTS = 10`.
- **Contributions by period**: the same ten plus `Other features`, stacked, with
  the `Forecast − base` and `Actual − base` lines as now.
- The ranking is a CTE of the explanation dataset over the pinned rows: per
  component `avg(abs(contribution))`, `row_number()` descending with ties to the
  name. Columns: `selection_rank`, `component_group` (the expression, or `Other
  features`; the stacked chart's series, so a feature keeps its colour when its
  rank changes), `component_group_label` (rank-prefixed, `01 …`, `11 Other
  features`; the waterfall sorts by label). `Other features` is pinned grey in
  `LABEL_COLORS`.
- A group of several features needs a sum, so both charts use
  `sum(contribution) / count(distinct trade_datetime)`: the mean per period of
  the group's total. For one feature it equals today's `avg`.
- **Feature value by period** and **Feature contribution by period**: two charts
  at the full width under Contributions by period, so the time codes line up:
  the chosen feature's value as a line, and its contribution as bars. The
  proposal drew both in one chart on two axes; two charts on one axis each say
  the same without inviting a reading of where unrelated scales cross.
- A **Feature** filter picks the feature: single-select, searchable, required,
  options `feature_pick` from the summary dataset (cascading from Run), sorted
  ascending and defaulting to the first, so it opens on the run's feature with
  the largest mean |SHAP|. The explanation dataset carries the same
  `feature_pick` (a join to the summary on run, area and component), so the
  filter applies to the two charts as a plain WHERE. Its scope is those two
  charts only.
- **All features**: a table of every feature of the selection, base row left
  out: rank in the selection, expression, mean contribution, mean |contribution|
  and its share of the column total (`percent_metrics`). Search box, 25 rows a
  page, `row_limit` 1000. This is where what `Other features` hides is read.

**Single period**

- The four tiles and **Feature values & contributions** (expression, value,
  contribution; no Order column; sorted by |contribution|, base row kept so the
  column sums to the forecast; `row_limit` 1000).
- They read a second dataset, `<task>_forecast_explanation_period`: the same SQL
  with `where 1 = 0` unless a Period is picked. Superset cannot hide a chart on a
  condition; an empty chart on its own sub-tab is the nearest it offers.
- A new **Period** filter: single-select, optional, no default, options
  `period_label` (`27 13:00–13:30`, from `dim_half_hour`), cascading from Run
  and Day, scoped to the Day overview and Single period charts.

**Feature importance**

- **Permutation importance** and **Mean |SHAP| by feature**: the top 20 each
  (`importance_rank <= 20` / `feature_rank <= 20` as ad-hoc filters, so the cut
  does not depend on how Superset orders a limited query), full width, one above
  the other, 60 grid units high.
- **Feature importance table**: no Order column; a Mean |SHAP| column; search
  box; sorted by ΔMAE, largest first; `row_limit` 1000. Every feature.

### Not in this change

The Compare tab's delta waterfall and its table list every component of either
run and have the same problem. They keep their shape; only their `row_limit`
rises to 1000 so nothing is dropped silently.

## Testing

- Unit tests for every new or changed builder, dataset SQL and filter, in
  `tests/test_create_forecast_dashboard.py`; the 100 % coverage gate holds.
- `just dbt build --select +fct_demand_forecast_contribution_summary
  +fct_spot_price_forecast_contribution_summary`, contracts and tests green.
- The script run against the local Superset; then, in the browser on run
  `34c506fb…`: the load time of each tab, the link from Worst days, the Period
  gate, the Feature filter's default, and a screenshot of each sub-tab.
- The same probe queries as above, re-timed through the new datasets.
