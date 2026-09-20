# Forecast dashboards: faster loads and a scalable Explanation tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the forecast dashboards load in seconds and keep the Explanation tab readable with hundreds of features, without losing any feature's value or contribution.

**Architecture:** The Run, Day and Period filters are pinned inside the dataset SQL with Jinja so Spark prunes the parquet scan; a small aggregate fact serves the run-level charts; the Explanation tab is split into three sub-tabs whose overview charts show the selection's top ten features plus `Other features`, ranked inside the dataset SQL.

**Tech Stack:** `scripts/create_forecast_dashboard.py` (Superset REST, virtual datasets with Jinja), dbt on Spark, pytest with the in-memory `FakeSupersetSession`.

**Spec:** `docs/superpowers/specs/2026-09-20-forecast-dashboard-explanation-scaling-design.md`

## Global Constraints

- Both dashboards (spot price, demand) change together; they share every builder.
- A pin may only make a scan smaller. Superset's own filter on the label column stays, so no pin decides which rows come back.
- `TOP_COMPONENTS = 10`; the importance and mean |SHAP| bars show the top 20.
- Chart marks (bars, series, legend) use the **short label**; tables use the full expression. The same rank number ties the two.
- One y-axis per chart. No dual-axis charts.
- Every dbt model: enforced contract, a uniqueness test on its key.
- NumPy-style docstrings; `just test` stays at 100 % coverage; `just lint`, `just mypy`, `just docs-links` pass.
- The tests pin dataset SQL as exact strings: every SQL change updates the expected literal in `tests/test_create_forecast_dashboard.py`.
- Long-running jobs (`dbt build`) run as main-session background tasks, never inside a subagent.
- Commits follow Conventional Commits, scope `dashboard` or `dbt`.

---

### Task 1: The contribution summary marts

**Files:**
- Create: `dbt/models/curated/fct_demand_forecast_contribution_summary.sql`, `.yml`
- Create: `dbt/models/curated/fct_spot_price_forecast_contribution_summary.sql`, `.yml`
- Create: `dbt/dbt_tests/assert_fct_demand_forecast_contribution_summary_counts_every_period.sql`, and the `spot_price` twin

**Interfaces:**
- Produces: tables `pma_curated.fct_<task>_forecast_contribution_summary` with columns `run_id string`, `area_key int`, `strategy string`, `published_at timestamp`, `component string`, `component_order int`, `is_base boolean`, `feature_rank int` (null on the base row), `n_periods bigint`, `mean_contribution_<unit> double`, `mean_abs_contribution_<unit> double`; `<unit>` = `demand_kwh` / `price_jpy_kwh`.

- [ ] **Step 1: Write the demand model**

```sql
with
  contribution as (
  select
    *
  from
    {{ ref('fct_demand_forecast_contribution') }}
  ),

  summary as (
  select
    run_id,
    area_key,
    strategy,
    published_at,
    component,
    component_order,
    is_base,
    count(*) as n_periods,
    avg(contribution_demand_kwh) as mean_contribution_demand_kwh,
    avg(abs(contribution_demand_kwh)) as mean_abs_contribution_demand_kwh
  from
    contribution
  group by
    run_id, area_key, strategy, published_at, component, component_order, is_base
  ),

  final as (
  select
    run_id,
    area_key,
    strategy,
    published_at,
    component,
    component_order,
    is_base,
    case
      when not is_base
      then cast(row_number() over (
        partition by run_id, area_key, is_base
        order by mean_abs_contribution_demand_kwh desc, component
      ) as int)
    end as feature_rank,
    n_periods,
    mean_contribution_demand_kwh,
    mean_abs_contribution_demand_kwh
  from
    summary
  )

select * from final
```

The spot model is the same with `fct_spot_price_forecast_contribution` and `contribution_price_jpy_kwh` / `mean_contribution_price_jpy_kwh` / `mean_abs_contribution_price_jpy_kwh`.

- [ ] **Step 2: Write the YAML** — model description (aggregate fact, grain `run_id × area_key × component`, why it exists: the dashboards' run-level charts), `config: contract: enforced: true`, every column with `data_type` and `not_null` (except `feature_rank`), `dbt_utils.unique_combination_of_columns` on `[run_id, area_key, component]`, `relationships` of `area_key` to `dim_area`. Copy the shape of `fct_demand_forecast_importance.yml`.

- [ ] **Step 3: Write the singular test** (rows returned = failures):

```sql
with
  fact as (
  select run_id, area_key, count(*) as n_periods
  from {{ ref('fct_demand_forecast_contribution') }}
  where is_base
  group by run_id, area_key
  ),

  summary as (
  select run_id, area_key, n_periods
  from {{ ref('fct_demand_forecast_contribution_summary') }}
  where is_base
  )

select
  coalesce(fact.run_id, summary.run_id) as run_id,
  fact.n_periods as fact_periods,
  summary.n_periods as summary_periods
from fact
full outer join summary
  on fact.run_id = summary.run_id and fact.area_key = summary.area_key
where fact.n_periods is null
  or summary.n_periods is null
  or fact.n_periods <> summary.n_periods
```

- [ ] **Step 4: Parse** — `cd dbt && uv run dbt deps && uv run dbt parse`. Expected: no error.
- [ ] **Step 5: Build (main session, background)** — `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt build --select fct_demand_forecast_contribution_summary fct_spot_price_forecast_contribution_summary`. Expected: all PASS. Then check `select count(*), count(distinct run_id) from pma_curated.fct_demand_forecast_contribution_summary`.
- [ ] **Step 6: Commit** — `feat(dbt): run-level contribution summary facts for the dashboards`.

---

### Task 2: Pin the Run filter in the analysis and comparison datasets

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`RUN_LABEL_SQL` block, `DATASET_SQL_TEMPLATE`, `COMPARISON_DATASET_SQL_TEMPLATE`, `EXPLANATION_COMPARISON_DATASET_SQL_TEMPLATE`, `DashboardSpec.dataset_sql`)
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces: `RUN_ID_PREFIX_LENGTH = 8`; `run_pin_sql(variable: str, column: str) -> str`, the Jinja expression `{column} like '{{ variable[0][-8:] | replace("'", "''") }}%'`; `JINJA_FILTER_VALUES_SQL`, the `{% set … %}` header lines.

- [ ] **Step 1: Failing tests**

```python
def test_run_pin_reads_the_labels_tail(self, script):
    assert script.run_pin_sql("run", "f.run_id") == (
        "f.run_id like '{{ run[0][-8:] | replace(\"'\", \"''\") }}%'"
    )

def test_the_label_ends_with_the_prefix_the_pin_reads(self, script):
    assert script.RUN_LABEL_SQL.rstrip().endswith(
        f"substring({{f}}.run_id, 1, {script.RUN_ID_PREFIX_LENGTH})\n  )"
    )

def test_analysis_sql_pins_the_run_when_the_filter_has_a_value(self, spec):
    sql = spec.dataset_sql
    assert sql.startswith("{% set run = filter_values('run_label') %}\n")
    assert sql.rstrip().endswith(
        "{% if run %}where f.run_id like '{{ run[0][-8:] | replace(\"'\", \"''\") }}%'{% endif %}"
    )
```

Plus, for both comparison datasets, that the `candidate` and `baseline` CTEs carry `and run_id like …` next to the label predicate.

- [ ] **Step 2: Run** `uv run pytest tests/test_create_forecast_dashboard.py -k "pin or prefix" -q` — expected FAIL (`run_pin_sql` missing).
- [ ] **Step 3: Implement.** `DATASET_SQL_TEMPLATE` has literal Jinja braces, so it becomes a `string.Template` like the comparison one (`$run_label_sql`, `$value_columns_sql`, `$accuracy_table`, `$run_pin`). In the comparison templates the two CTE predicates become `run_label = '…' and $candidate_pin` / `$baseline_pin` with `run_pin_sql("candidate", "run_id")` / `run_pin_sql("baseline", "run_id")`.
- [ ] **Step 4: Update the pinned SQL literals** in the tests (`SPOT_DATASET_SQL`, `DEMAND_DATASET_SQL`, `comparison_sql`, `explanation_comparison_sql`) and run the whole file. Expected: PASS.
- [ ] **Step 5: Commit** — `perf(dashboard): pin the run inside the analysis and comparison datasets`.

---

### Task 3: The explanation, period, summary and importance datasets

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`EXPLANATION_DATASET_SQL_TEMPLATE`, `COMMON_EXPLANATION_COLUMNS`, `IMPORTANCE_DATASET_SQL_TEMPLATE`, `COMMON_IMPORTANCE_COLUMNS`, `DashboardSpec`, the two spec instances, `build_dashboard`'s dataset block)
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Consumes: `run_pin_sql` (Task 2); the summary marts (Task 1).
- Produces: spec fields `summary_table`, `contribution_fact_col`, `explanation_period_dataset_name`, `summary_dataset_name`, `summary_value_columns_sql`, `summary_value_columns`, `mean_abs_contribution_col`; properties `explanation_period_dataset_sql`, `summary_dataset_sql`, `summary_dataset_columns`; constants `TOP_COMPONENTS = 10`, `OTHER_FEATURES = "Other features"`, `SHORT_LABEL_HEAD = 34`, `SHORT_LABEL_TAIL = 21`; `short_label_sql(expression: str) -> str`; dataset columns `period_label`, `selection_rank`, `component_group`, `component_group_label`, `feature_pick` (explanation), `feature_rank`, `feature_short`, `feature_pick` (summary), `importance_rank`, `feature_short`, `mean_abs_contribution` column (importance).

- [ ] **Step 1: The short label**

```python
def short_label_sql(expression: str) -> str:
    """``expression`` shortened for a chart mark: the head, an ellipsis, the tail."""
    limit = SHORT_LABEL_HEAD + SHORT_LABEL_TAIL + 1
    return (
        f"case when length({expression}) > {limit} "
        f"then concat(substring({expression}, 1, {SHORT_LABEL_HEAD}), '…', "
        f"substring({expression}, -{SHORT_LABEL_TAIL})) else {expression} end"
    )
```

Test: the exact string for `short_label_sql("x")`.

- [ ] **Step 2: The explanation SQL.** A `string.Template`; `$gate` is empty for the explanation dataset and `\n  and {% if period %}1 = 1{% else %}1 = 0{% endif %}` for the period dataset.

```sql
{% set run = filter_values('run_label') %}
{% set day = filter_values('trade_date_label') %}
{% set period = filter_values('period_label') %}
with pinned as (
select c.*
from $contribution_table c
where {% if run %}$run_pin{% else %}1 = 1{% endif %}
  {% if day %}and c.date_key = date '{{ day[0] | replace("'", "''") }}'{% endif %}
  {% if period %}and c.time_code = {{ period[0][:2] | int }}{% endif %}$gate
),
ranked as (
select
  k.component,
  row_number() over (order by avg(abs(k.$contribution_fact_col)) desc, k.component) as selection_rank
from pinned k
where not k.is_base
group by k.component
),
labelled as (
select
  r.component,
  r.selection_rank,
  $short_expression_sql as short_label
from ranked r
$ranked_feature_join_sql
),
grouped as (
select
  l.component,
  l.selection_rank,
  case
    when l.selection_rank > $top then '$other'
    when count(*) over (partition by l.short_label, l.selection_rank <= $top) > 1
      then concat(l.short_label, ' #', cast(l.selection_rank as string))
    else l.short_label
  end as component_group
from labelled l
)
select
  c.date_key,
  date_format(c.date_key, 'yyyy-MM-dd') as trade_date_label,
  c.trade_datetime,
  c.time_code,
  concat(lpad(cast(c.time_code as string), 2, '0'), ' ', p.period_start_time, '–', p.period_end_time) as period_label,
  …the existing context columns…
  c.component,
  $feature_expression_sql as feature_expression,
  c.component_order,
  c.is_base,
  g.selection_rank,
  case when c.is_base then 'base' else g.component_group end as component_group,
  case
    when c.is_base then '00 base'
    when g.selection_rank > $top then '$other_order $other'
    else concat(lpad(cast(g.selection_rank as string), 2, '0'), ' ', g.component_group)
  end as component_group_label,
  $feature_pick_sql as feature_pick,
  c.feature_value,
$explanation_value_columns_sql
from pinned c
join pma_curated.dim_area a on c.area_key = a.area_key
join pma_curated.dim_half_hour p on c.time_code = p.time_code
join pma_curated.dim_date d on c.date_key = d.date_key
left join grouped g on g.component = c.component
$feature_join_sql
left join $summary_table s
  on s.run_id = c.run_id
  and s.area_key = c.area_key
  and s.component = c.component
left join $accuracy_table f
  on …as today…
```

`$other_order` = `TOP_COMPONENTS + 1` (`11`). `component_label` is dropped (no chart reads model order any more). `FEATURE_PICK_SQL = "concat(lpad(cast({s}.feature_rank as string), 3, '0'), ' ', {expression})"`, one definition used by the explanation and summary datasets.

Tests: the exact SQL for both tasks; the period dataset differs from the explanation dataset by the gate line only; the columns tuple matches the select list in order.

- [ ] **Step 3: The summary dataset**

```sql
{% set run = filter_values('run_label') %}
select
  a.area_code,
  a.area_name_en,
  s.run_id,
  $run_label_sql as run_label,
  s.strategy,
  s.published_at,
  s.component,
  $feature_expression_sql as feature_expression,
  s.feature_rank,
  concat(lpad(cast(s.feature_rank as string), 3, '0'), ' ', $short_expression_sql) as feature_short,
  $feature_pick_sql as feature_pick,
  s.n_periods,
$summary_value_columns_sql
from $summary_table s
join pma_curated.dim_area a on s.area_key = a.area_key
$feature_join_sql
where not s.is_base
  {% if run %}and $run_pin{% endif %}
```

`feature_short` carries the rank prefix, so two features never share a bar. `main_dttm_col="published_at"`.

- [ ] **Step 4: The importance dataset** gains a CTE ranking the features of each run by ΔMAE and a join to the summary:

```sql
with delta as (
select
  i.run_id, i.area_key, i.feature,
  row_number() over (
    partition by i.run_id, i.area_key
    order by avg(i.$permuted_fact_col) - avg(i.$mae_fact_col) desc, i.feature
  ) as importance_rank
from $importance_table i
group by i.run_id, i.area_key, i.feature
)
select … i.feature_order,
  r.importance_rank,
  concat(lpad(cast(r.importance_rank as string), 3, '0'), ' ', $short_expression_sql) as feature_short,
  …,
$importance_value_columns_sql,
$importance_summary_columns_sql
from $importance_table i
join delta r on r.run_id = i.run_id and r.area_key = i.area_key and r.feature = i.feature
join pma_curated.dim_area a on i.area_key = a.area_key
$feature_join_sql
left join $summary_table s on s.run_id = i.run_id and s.area_key = i.area_key and s.component = i.feature
```

`feature_label` (the order-prefixed label) is dropped with the Order column.

- [ ] **Step 5: Register the two new datasets in `build_dashboard`** (`explanation_period_id`, `summary_id`) and pass them to `build_explanation_tab`. Update `TestUpsertDataset` / `build_dashboard` tests for seven datasets per dashboard.
- [ ] **Step 6: Run the file; commit** — `perf(dashboard): pinned, ranked explanation datasets and the summary dataset`.

---

### Task 4: Accuracy tab and the Explain link

**Files:** `scripts/create_forecast_dashboard.py` (`leaderboard_params` removed, `detail_params`, `worst_days_params`, `ranked_days_params`, the analysis and comparison dataset SQL, `build_accuracy_tab`, `build_dashboard`), tests.

**Interfaces:**
- Produces: `EXPLANATION_TAB_KEY = "TAB-1"`; `explain_link_sql(slug: str, run_label: str, day_label: str) -> str`, a SQL expression giving `<a href="…">Explain</a>`; dataset column `explain_link` (analysis, comparison); spec property `error_metric`.

- [ ] **Step 1: The link.** The rison, with `{run}` and `{day}` the escaped values:

```
(NATIVE_FILTER-run:(extraFormData:(filters:!((col:run_label,op:IN,val:!('{run}')))),filterState:(label:'{run}',validateStatus:!f,value:!('{run}')),id:NATIVE_FILTER-run,ownState:()),NATIVE_FILTER-day:(extraFormData:(filters:!((col:trade_date_label,op:IN,val:!('{day}')))),filterState:(label:'{day}',validateStatus:!f,value:!('{day}')),id:NATIVE_FILTER-day,ownState:()))
```

In SQL the two values are escaped for rison with `replace(replace(x, '!', '!!'), '''', '!''')`, the whole rison goes through `url_encode(...)`, and the expression is `concat('<a href="/superset/dashboard/<slug>/?native_filters=', url_encode(concat(…)), '#TAB-1">Explain</a>')`. Tests: the generated SQL for a slug; that the tab key equals the Explanation tab's key in `build_position_json`'s output.

- [ ] **Step 2: `worst_days_params` / `ranked_days_params`**: `"groupby": ["explain_link", "date_key", …]`, `"allow_render_html": True`.
- [ ] **Step 3: `detail_params`**: a third metric `spec.error_metric` = `avg_metric(spec.error_col, "Error (forecast − actual)")`; `LABEL_COLORS["Error (forecast − actual)"] = "#FF7F44"`.
- [ ] **Step 4: Remove the leaderboard**: the chart, `leaderboard_params`, its layout row (the section becomes `Drilldown`), its tests; `run_excluded=[]`.
- [ ] **Step 5: Cross-filter scopes**: `worst_days: [detail, cmp_detail]`; `cmp_improved` / `cmp_worsened`: `[cmp_detail, *explanation-vs-baseline charts]`. The Day filter's description loses its "clear Day" sentence.
- [ ] **Step 6: Run the file; commit** — `feat(dashboard): explain links, the error in the detail popup, no leaderboard`.

---

### Task 5: The Explanation tab's sub-tabs, charts and filters

**Files:** `scripts/create_forecast_dashboard.py` (`build_position_json`, `DashboardTab`, the explanation param builders, `build_explanation_tab`, `build_native_filters`, `_select_filter`, `build_dashboard`, the Compare tab's two `row_limit`s, the module docstring), tests.

**Interfaces:**
- Produces: `DashboardTab.subtabs: list[dict] | None`; layout dict `{"title", "subtabs": [{"title", "sections"}]}`; `grouped_contribution_metric` (`sum(col) / count(distinct trade_datetime)`); `feature_value_by_period_params`, `feature_contribution_by_period_params`, `all_features_table_params`; filters `NATIVE_FILTER-period`, `NATIVE_FILTER-feature`.

- [ ] **Step 1: Layout.** A tab with `subtabs` gets one child `TABS-<t>` whose children are `TAB-<t>-<u>`; rows are keyed `ROW-<t>-<u>-<s>-<r>` and carry the full `parents` chain `["ROOT_ID", "TABS-0", "TAB-<t>", "TABS-<t>", "TAB-<t>-<u>"]`. Test the exact structure for a two-sub-tab input, and that a plain tab's output is unchanged.
- [ ] **Step 2: Charts.**
  - `waterfall_params`: `x_axis="component_group_label"`, `metric=spec.grouped_contribution_metric`, `row_limit` 1000.
  - `contribution_by_period_params`: `groupby=["component_group"]`, the grouped metric; `LABEL_COLORS["Other features"] = "#B2B2B2"`.
  - `feature_value_by_period_params`: `echarts_timeseries_line`, `x_axis="time_code"`, metric `avg(feature_value)` labelled `Feature value`, markers on, no legend.
  - `feature_contribution_by_period_params`: `echarts_timeseries_bar`, `x_axis="time_code"`, `spec.contribution_metric`, no legend.
  - `all_features_table_params`: `groupby=["feature_expression"]`, metrics `min(selection_rank)` as `Rank`, the contribution, `avg(abs(col))` as `Mean |contribution|`; `percent_metrics` = the mean |contribution|; `NOT_BASE_FILTER`; `include_search: True`; `page_length: 25`; `row_limit: 1000`; sorted by rank ascending.
  - `feature_table_params`: `groupby=["feature_expression"]`, metrics value and contribution, sorted by `max(abs(col))` descending, `row_limit: 1000`, `include_search: True`.
  - `importance_bar_params` / `mean_abs_shap_params`: `x_axis="feature_short"`, ad-hoc filter `importance_rank <= 20` / `feature_rank <= 20`; the mean |SHAP| bars move to the summary dataset with `avg(mean_abs_contribution col)`.
  - `importance_table_params`: `groupby=["feature_expression"]`, no Order, plus `Mean |SHAP|`, sorted by ΔMAE descending, `include_search`, `row_limit: 1000`.
  - `delta_waterfall_params`, `delta_feature_table_params`: `row_limit: 1000`.
- [ ] **Step 3: `build_explanation_tab(chart, spec, explanation_id, period_id, summary_id, importance_id)`** returns a `DashboardTab` with three sub-tabs: `Day overview` (waterfall 12×46; contributions by period 12×44; feature value 12×28; feature contribution 12×28; all features 12×50), `Single period` (four tiles; the table 12×60), `Feature importance` (header; permutation bars 12×60; mean |SHAP| bars 12×60; table 12×50).
- [ ] **Step 4: Filters.** `_select_filter` gains `search_all_options: bool = False`. Period: column `period_label` on the explanation dataset, optional, no default, ascending, cascades from Run and Day, scope = Day overview + Single period charts. Feature: column `feature_pick` on the summary dataset, required, `default_to_first=True`, ascending, cascades from Run, scope = the two feature-by-period charts. The Day filter's scope = Day overview + Single period + the Compare section.
- [ ] **Step 5: Run the file, then `just test`, `just lint`, `just mypy`.** Expected: PASS at 100 %.
- [ ] **Step 6: Commit** — `feat(dashboard): an Explanation tab in three sub-tabs that scales to hundreds of features`.

---

### Task 6: Roll out and verify in the browser

- [ ] `SUPERSET_URL=http://localhost:8088 uv run python scripts/create_forecast_dashboard.py` (or in the devcontainer from the worktree path). Expected: both dashboards and the catalogue rebuilt, no HTTP error.
- [ ] In the browser, run `34c506fb…`: time each tab's load; follow an Explain link from Worst days and from Most improved days; check the Period gate (empty without a Period, filled with one); check the Feature filter opens on rank 001; check the detail popup lists the error; screenshot each sub-tab into `scratch/2026-09-20-dashboard-feedback/`.
- [ ] Re-time the probe queries through the new dataset SQL.
- [ ] Anything Superset renders differently from the plan (the HTML link, sub-tab scoping, `percent_metrics`) is fixed here with its test, or falls back as the spec says.

### Task 7: Docs and the pull request

- [ ] `CLAUDE.md`: the dashboard bullet (datasets, sub-tabs, filters, links, pins), the post-backtest build selector (`+fct_<task>_forecast_contribution_summary`), the script's module docstring, `docs/superpowers/README.md` index row.
- [ ] `just docs-links`, `just test`, `just lint`, `just mypy`, `cd dbt && uv run dbt parse`.
- [ ] Push, open the PR (`feat(dashboard): …`, Why / What / Proof with the measured numbers), labels `enhancement`, `dashboard`; drive the Codex review loop.
