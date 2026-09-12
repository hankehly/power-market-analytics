# Feature-Value Fact Implementation Plan (feature catalogue PR 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tagged feature column of every feature mart, queryable from Superset as one long fact, `pma_curated.fct_feature_value`, with two virtual datasets on top.

**Architecture:** The fact's SQL is generated from the dbt manifest by the script that already writes the Feast views, so a mart column tagged `feature` reaches Superset after `just feature-views` with real `ref` edges (a model cannot walk `graph` at parse time, so the spec's "macro" becomes a second generated file). Day and hour marts are broadcast to the 48 periods through `dim_delivery_period`; a mart with `published_at` keeps the newest published row per key and `available_at`, the rule Feast serves. `create_forecast_dashboard.py` registers `<task>_feature_values` (the as-of rows at the task's issue time) and `feature_values_all` (every vintage).

**Tech Stack:** dbt-spark (Spark SQL `stack()`), the manifest-driven generator, Superset REST datasets, pytest with the local Spark fixture marts.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §8 (Superset), §11 row 3.

## Global Constraints

- Every dbt model has an enforced contract and a uniqueness test on its key; the fact's key is `area_code × trade_date × time_code × feature_ref × available_at`.
- `feature_value` is `double`: `int`, `bigint`, `double` and `boolean` feature columns cast to it; any other tagged type makes the generator raise.
- The generated model is never edited by hand; `scripts/generate_feature_views.py --check` (the `dbt parse` CI job) fails when either output is stale.
- The Kimball guidance on measure-type dimensions (`docs/Kimball-Dimensional-Modeling-Techniques.md`, "Measure Type Dimensions") accepts the long shape when the facts run to hundreds and a query reads a handful — the catalogue's stated goal; the model description says so.
- Coverage 100 %; `just lint`, `just mypy`, `dbt parse` clean; `dbt build --select +fct_feature_value` green on the devcontainer.
- Branch `feature/feature-value-fact`, worktree `.claude/worktrees/feature-value-fact`; commit types `feat(dbt)`, `feat(features)`, `feat(dashboard)`, `docs`; PR labels `enhancement` + `documentation`.
- Plain writing everywhere (CLAUDE.md "Writing style").

---

### Task 1: The generator writes the fact

**Files:**
- Modify: `scripts/generate_feature_views.py`
- Test: `tests/test_generate_feature_views.py`

**Interfaces:**
- Produces: `render_fact(manifest: dict) -> str` (the model SQL), `DEFAULT_FACT_OUTPUT`, `main` writing and checking both outputs (`--fact-output`).

- [ ] **Step 1: Failing tests** in `TestRender` / `TestMain`: `test_fact_broadcasts_day_marts_joins_hour_marts_and_keeps_period_marts` (the fixture manifest: `ftr_day_y` cross-joins `periods`, `ftr_hour_z` joins on `p.hour_ending = m.hour_ending`, `ftr_period_x` joins nothing; each CTE stacks its tagged columns as `(feature_name, feature_value, is_categorical)` with `cast(m.x as double)` and the categorical literal; untagged `note` absent), `test_fact_dedupes_a_mart_with_published_at` (`ftr_hour_z` ranks by `published_at desc` per key and `available_at`, keeps `vintage_rank = 1`; the others carry `cast(null as timestamp) as published_at`), `test_fact_casts_booleans_and_rejects_other_types` (a `boolean` column → `cast(cast(m.b as int) as double)`; a `string` tagged column raises `ValueError` naming it), `test_main_writes_both_outputs_and_check_covers_both` (writes `views.py` and the fact file; `--check` passes; edit the fact file → `--check` returns 1 naming it), `test_the_checked_in_fact_is_current`.
- [ ] **Step 2: Run** `uv run pytest tests/test_generate_feature_views.py --no-cov -q` → FAIL (no `render_fact`).
- [ ] **Step 3: Implement.** `NUMERIC_CASTS = {"int", "bigint", "double"}` → `cast(m.<col> as double)`; `boolean` → `cast(cast(m.<col> as int) as double)`; else `ValueError(f"{name}.{col}: data type {t!r} has no numeric feature value")`. `render_fact` emits the header comment, `periods` CTE (`select time_code, hour_of_day + 1 as hour_ending from {{ ref('dim_delivery_period') }}`), one CTE per mart (`from {{ ref('<mart>') }} m` + `cross join periods p` for day, `join periods p on p.hour_ending = m.hour_ending` for hour; a mart with `published_at` wraps `m` in a ranked subquery), `unioned` (union all in mart-name order), and the final select with `concat(feature_view, ':', feature_name) as feature_ref`. `main` gains `--fact-output` (default `dbt/models/curated/fct_feature_value.sql`) and checks/writes both.
- [ ] **Step 4: Run** the tests → PASS; `uv run ruff format`, `ruff check`, `mypy`.
- [ ] **Step 5: Commit** `feat(features): generate fct_feature_value from the manifest beside the Feast views`.

### Task 2: The dbt model, its contract and tests

**Files:**
- Create: `dbt/models/curated/fct_feature_value.sql` (generated: `just feature-views`), `dbt/models/curated/fct_feature_value.yml`, `dbt/dbt_tests/assert_fct_feature_value_covers_every_tagged_column.sql`
- Test: `tests/test_feature_value_fact.py`

**Interfaces:**
- Produces: `pma_curated.fct_feature_value(area_code string, trade_date date, time_code int, feature_view string, feature_name string, feature_ref string, feature_value double, is_categorical boolean, available_at timestamp, published_at timestamp)`.

- [ ] **Step 1: Failing Python test** `tests/test_feature_value_fact.py`: under `feature_marts`, render the fact from `dbt/target/manifest.json` (skip when absent), replace every `{{ ref('x') }}` with `pma_features.x` / `pma_curated.dim_delivery_period`, run it on the local session; assert the row count = Σ over marts of rows × tagged columns × (48 day, 2 hour, 1 period); every mart cell equals the fact's `feature_value` at its periods (sample: one period mart row, one hour row at both periods, one day row at 48 periods); `is_categorical` true only for `ftr_day_calendar:day_type`; `available_at` equals the mart row's; the similar-day fixture's single run passes through; the key is unique.
- [ ] **Step 2: Run** → FAIL (no model file).
- [ ] **Step 3: Implement.** `just feature-views` writes the model; the YAML: contract with the ten columns, `dbt_utils.unique_combination_of_columns` on the five key columns, `not_null` on every column but `published_at`, `accepted_range` `time_code` 1..48, `relationships` of `time_code` to `dim_delivery_period`, description with the grain, the broadcast rule, the tie rule and the Kimball note. The singular test walks `graph.nodes` at execute time: every `features/` model's tagged column must appear as a `feature_ref` in the fact; one row per missing ref.
- [ ] **Step 4: Run** the Python test → PASS; host `dbt parse`; `generate_feature_views.py --check` passes.
- [ ] **Step 5: Commit** `feat(dbt): fct_feature_value, the feature marts unpivoted to the period grain`.

### Task 3: The Superset datasets

**Files:**
- Modify: `scripts/create_forecast_dashboard.py`
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces: `issue_time_sql(offset: pd.Timedelta) -> str` (`timestampadd(MINUTE, <minutes>, timestamp(f.trade_date))`), `DashboardSpec.feature_values_dataset_name`, `DashboardSpec.issue_time_sql`, `DashboardSpec.feature_values_sql`, `FEATURE_VALUES_ALL_SQL`, `FEATURE_VALUE_COLUMNS`; `build_dashboard` registers both datasets.

- [ ] **Step 1: Failing tests:** `issue_time_sql` of both tasks' `TASK.issue_offset` is `timestampadd(MINUTE, -870, timestamp(f.trade_date))`; the demand spec's `feature_values_dataset_name == "demand_feature_values"`, the spot spec's `"spot_price_feature_values"`; the as-of SQL keeps `available_at <= <issue time>` and ranks by `available_at desc, published_at desc` to `vintage_rank = 1`; `FEATURE_VALUES_ALL_SQL` has no filter; both select `trade_datetime` from `start_minute_of_day`; `build_dashboard` upserts a dataset named `<task>_feature_values` and one named `feature_values_all` (the fake's dataset rows), `main_dttm_col` `trade_datetime`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the templates, the spec fields (from `power_market_analytics.tasks.<task>.TASK.issue_offset`), the column tuple and the two `dataset(...)` calls in `build_dashboard`.
- [ ] **Step 4: Run** the dashboard tests → PASS; lint, mypy.
- [ ] **Step 5: Commit** `feat(dashboard): the feature-value datasets, as-of at the issue time and every vintage`.

### Task 4: Docs, verification and the PR

- [ ] **Step 1: Docs.** CLAUDE.md: the `just feature-views` bullet (two outputs), the dbt features bullet (the fact), the dashboard bullet (the two datasets), the "Feature retrieval" bullet unchanged; spec §8 rewritten to the generated model and the columns; this plan's results section.
- [ ] **Step 2: Verify.** `just test` (100 %), `just lint`, `just mypy`; in the devcontainer `dbt build --select +fct_feature_value` from the worktree, then `create_forecast_dashboard.py` for both tasks; one chart over `demand_feature_values` in Superset (the proof the spec asks for); row count and build time recorded below.
- [ ] **Step 3: PR** `feat(dbt): fct_feature_value and the feature-value Superset datasets` with Why / What / Proof, labels, the Codex loop, report ready.

## Results

(filled in as the tasks complete)
