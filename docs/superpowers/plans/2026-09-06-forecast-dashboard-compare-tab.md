# Forecast Dashboard Compare Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third top-level tab, **Compare**, to both Superset forecast-analysis dashboards, showing the difference between the Run (candidate) and a new Baseline filter's run by segment and by day, so the researcher no longer flips between two browser tabs.

**Architecture:** A third virtual dataset per dashboard (`<task>_forecast_comparison`) self-joins the accuracy mart on delivery day × time code × area; both runs are pinned inside its SQL with Superset Jinja (`filter_values()`), the candidate from the existing Run filter and the baseline from a new single-select Baseline filter scoped to the new tab. Twenty-three charts on the comparison dataset show deltas (diverging Better / Worse bars, delta heatmaps, delta tiles with sign colouring, daily delta bars, a cumulative error-reduction line, most-improved / most-worsened day tables and a three-line 30-minute detail). Everything is built by the existing `scripts/create_forecast_dashboard.py` patterns: `DashboardSpec` fields and metric properties, chart param builders, the tab layout, native filters and cross-filters.

**Tech Stack:** Python 3.13 (`string.Template` for the Jinja-bearing SQL template), Superset 6.1.0 REST API (`viz_type` `big_number_total` with `conditional_formatting`, `echarts_timeseries_bar` with `stack: "Stack"`, `heatmap_v2` with `value_bounds` + `blue_white_yellow`, `echarts_timeseries_line` with `rolling_type: "cumsum"`, `table`), Superset Jinja (`ENABLE_TEMPLATE_PROCESSING`, already on and committed in `conf/superset/superset_config.py`), pytest with `FakeSupersetSession` (`tests/test_create_forecast_dashboard.py`), Playwright for the live check.

**Spec:** `docs/superpowers/specs/2026-09-06-forecast-dashboard-compare-tab-design.md`

## Global Constraints

- NumPy-style docstrings (`Parameters` / `Returns` / `Raises`, underlined headers) on every new function and class; module-level constants get a comment.
- `just test` has a **100 % coverage gate**; every new line must be exercised. `just lint` (ruff) and `just mypy` must pass. A PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` you edit; re-read a file before editing it again.
- The only test double is `FakeSupersetSession` (in-memory Superset REST); the script's client, specs, builders, `build_dashboard` and `main` always run for real against it. Existing SQL is pinned byte-for-byte in the tests; keep that convention for the new dataset.
- Names fixed by the spec: dataset `<task>_forecast_comparison`; filter id `NATIVE_FILTER-baseline`, name `Baseline`, column `baseline_run_label`; tab title `Compare`; run label `published_at | area | strategy | run_id prefix`; series names `Candidate`, `Baseline`, `Actual`, `Better`, `Worse` with colours `#1FA8C9`, `#B2B2B2`, `#222222`, `#1FA8C9`, `#FF7F44`; heatmap bounds ±30 (ΔMAE %); chart names as listed in Task 9.
- Superset facts verified on 2026-09-06 against the installed 6.1.0 bundle and a live probe: a native filter's value reaches `filter_values('<column>')` in a virtual dataset's SQL and the outer `WHERE <column> IN (...)` still applies; a dataset whose SQL starts with `{% set %}` lines and a `with` clause works under Superset's subquery wrapping on Spark; conditional-formatting entries are `{"colorScheme", "column", "operator" (">" / "<"), "targetValue"}`; the bar chart's stack control takes `"Stack"`; `rolling_type` accepts `"cumsum"`; `label_colors` is dashboard metadata.
- The comparison dataset probe reproduced research `demand/R-004` E-002 exactly (Run `008868fe…` vs Baseline `0a6b8a55…`: MAE 594.32 → 585.36 MWh, −1.51 %; weekday −4.8 %, weekend +6.5 %; coverage 1.0; 729 days; 53.4 % of days lower; median daily ΔMAE −14.6 MWh). The live check in Task 10 must show the same numbers.
- Commit after every task on the branch `feature/dashboard-compare-tab` (already created, in the main checkout because the compose stack mounts its Superset config); messages `feat(dashboard): …` / `test(dashboard): …` / `docs(dashboard): …`, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never write the Codex bot's mention anywhere.
- Long-running steps (the live rebuild, screenshots) run from the main session, never backgrounded by a subagent.

---

## File structure

| File | Responsibility |
|---|---|
| `conf/superset/superset_config.py` | `FEATURE_FLAGS = {"ENABLE_TEMPLATE_PROCESSING": True}` (done, commit `631debc`) |
| `scripts/create_forecast_dashboard.py` | `RUN_LABEL_SQL`; `COMPARISON_DATASET_SQL_TEMPLATE` + `COMMON_COMPARISON_COLUMNS`; `DashboardSpec` comparison fields, derived column names and metrics; `LABEL_COLORS`; `RunDefaults` / `run_defaults`; seven new chart builders; Baseline filter; multi-emitter cross-filters; the Compare tab; `--baseline-run` |
| `tests/test_create_forecast_dashboard.py` | every change above, byte-for-byte SQL pins, layout / filter / cross-filter / id arithmetic |
| `docs/img/superset/demand-forecast-dashboard.png` (refreshed = before), `docs/img/superset/demand-forecast-dashboard-compare.png` (after) | the PR's before / after images and the docs' screenshots |
| `CLAUDE.md`, `docs/README.md`, `docs/research/{demand,spot_price}/README.md`, the spec | documentation |

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 (all unit-tested against the fake) → 10 (live rollout + screenshots) → 11 (docs) → 12 (finish).

Id arithmetic used by the tests from Task 9 on (the fake allocates ids from 10, datasets before charts): analysis dataset 10, explanation 11, comparison 12; charts 13–61 (19 analysis 13–31, 7 explanation 32–38, 23 comparison 39–61); dashboard 62. A second dashboard in the same fake: datasets 63–65, charts 66–114, dashboard 115.

---

### Task 1: One run-label definition, with the strategy, and the `baseline_run_label` alias

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`DATASET_SQL_TEMPLATE`, `COMMON_DATASET_COLUMNS`, `EXPLANATION_DATASET_SQL_TEMPLATE`, `DashboardSpec.dataset_sql`, `DashboardSpec.explanation_dataset_sql`, `latest_run`)
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces: `RUN_LABEL_SQL: str`, a format string with `{f}` (accuracy/contribution fact alias) and `{a}` (dim_area alias) placeholders whose rendering is the `concat(...)` expression; the analysis dataset gains a `baseline_run_label` column (same expression as `run_label`) right after `run_label`.

- [ ] **Step 1: Update the pinned fixtures and add the failing tests**

In `tests/test_create_forecast_dashboard.py`:

1. Change `DEFAULT_LABEL = "2026-08-18 09:00 | tokyo | abcdef12"` to `DEFAULT_LABEL = "2026-08-18 09:00 | tokyo | lightgbm | abcdef12"`.
2. In **both** `SPOT_DATASET_SQL` and `DEMAND_DATASET_SQL`, replace

```
  f.run_id,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  f.strategy,
```

with

```
  f.run_id,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as baseline_run_label,
  f.strategy,
```

3. In `COMMON_COLUMNS_HEAD`, insert `("baseline_run_label", "STRING", False),` right after `("run_label", "STRING", False),`.
4. In `EXPLANATION_SQL_HEAD`, insert the line `    ' | ', c.strategy,` between `    ' | ', a.area_code,` and `    ' | ', substring(c.run_id, 1, 8)`.
5. In `TestLatestRun.test_returns_newest_label_and_last_day_via_sqllab`, change the group-by assertion to `assert "group by f.run_id, f.strategy, f.published_at, a.area_code" in payload["sql"]`.
6. Add to `TestDashboardSpecs`:

```python
    def test_run_label_has_one_definition(self, script, spec):
        label = script.RUN_LABEL_SQL.format(f="f", a="a")
        assert label == (
            "concat(\n"
            "    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),\n"
            "    ' | ', a.area_code,\n"
            "    ' | ', f.strategy,\n"
            "    ' | ', substring(f.run_id, 1, 8)\n"
            "  )"
        )
        assert spec.dataset_sql.count(f"  {label} as run_label,\n") == 1
        assert spec.dataset_sql.count(f"  {label} as baseline_run_label,\n") == 1
        assert f"  {script.RUN_LABEL_SQL.format(f='c', a='a')} as run_label,\n" in (
            spec.explanation_dataset_sql
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -x --no-cov`
Expected: FAIL — `AttributeError: module has no attribute 'RUN_LABEL_SQL'` (and the SQL pins differ).

- [ ] **Step 3: Implement**

In `scripts/create_forecast_dashboard.py`, right above `DATASET_SQL_TEMPLATE`:

```python
# The Run / Baseline filters' option text and the datasets' run_label:
# published_at | area | strategy | run_id prefix (newest first when sorted
# descending). One definition, formatted with the fact's alias ``f`` and
# dim_area's alias ``a``, so every dataset and the default-run query build the
# label identically — a filter value only matches a chart's rows when they do.
RUN_LABEL_SQL = """\
concat(
    date_format({f}.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', {a}.area_code,
    ' | ', {f}.strategy,
    ' | ', substring({f}.run_id, 1, 8)
  )"""
```

In `DATASET_SQL_TEMPLATE` replace the `concat(...) as run_label,` block (six lines) with:

```
  {run_label_sql} as run_label,
  {run_label_sql} as baseline_run_label,
```

In `COMMON_DATASET_COLUMNS` insert `("baseline_run_label", "STRING", False),` after the `run_label` entry. In `EXPLANATION_DATASET_SQL_TEMPLATE` replace its `concat(...) as run_label,` block with `  {run_label_sql} as run_label,`. Update the two properties:

```python
    @property
    def dataset_sql(self) -> str:
        """The virtual dataset's SQL: the shared template around this task's columns."""
        return DATASET_SQL_TEMPLATE.format(
            value_columns_sql=self.value_columns_sql,
            accuracy_table=self.accuracy_table,
            run_label_sql=RUN_LABEL_SQL.format(f="f", a="a"),
        )
```

```python
    @property
    def explanation_dataset_sql(self) -> str:
        """The explanation dataset's SQL: the shared template around this task's value block."""
        return EXPLANATION_DATASET_SQL_TEMPLATE.format(
            explanation_value_columns_sql=self.explanation_value_columns_sql,
            contribution_table=self.contribution_table,
            accuracy_table=self.accuracy_table,
            run_label_sql=RUN_LABEL_SQL.format(f="c", a="a"),
        )
```

In `latest_run`, replace the `concat(...) as run_label,` block of its SQL with `  {RUN_LABEL_SQL.format(f='f', a='a')} as run_label,` (single quotes inside the f-string expression) and the group by with `group by f.run_id, f.strategy, f.published_at, a.area_code`. Update the `DATASET_SQL_TEMPLATE` comment above `COMMON_DATASET_COLUMNS` to mention the alias: "`baseline_run_label` repeats the label so the Baseline native filter (Task 8) can list the runs from this dataset".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): one run-label definition, with the strategy, plus a baseline alias

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Leaderboard window columns

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`leaderboard_params`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestChartParams.test_leaderboard`)

**Interfaces:**
- Produces: the leaderboard's metrics in order `Periods, First day, Last day, Days, MAE (<unit>), Bias, RMSE, WAPE`.

- [ ] **Step 1: Update the failing test**

Replace the body of `TestChartParams.test_leaderboard` with:

```python
    def test_leaderboard(self, script, spec):
        p = script.leaderboard_params(spec, 7)
        mae_label = f"MAE ({spec.unit})"
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["run_label", "strategy"]
        assert [m["label"] for m in p["metrics"]] == [
            "Periods",
            "First day",
            "Last day",
            "Days",
            mae_label,
            "Bias",
            "RMSE",
            "WAPE",
        ]
        assert p["metrics"][0]["sqlExpression"] == "count(*)"
        assert p["metrics"][0]["optionName"] == "metric_periods"
        assert p["metrics"][1]["sqlExpression"] == "date_format(min(date_key), 'yyyy-MM-dd')"
        assert p["metrics"][2]["sqlExpression"] == "date_format(max(date_key), 'yyyy-MM-dd')"
        assert p["metrics"][3]["sqlExpression"] == "count(distinct date_key)"
        assert p["metrics"][4] == spec.mae_metric
        assert p["metrics"][6] == spec.rmse_metric
        assert p["metrics"][7] == spec.wape_metric
        assert p["timeseries_limit_metric"] == spec.mae_metric
        assert p["order_desc"] is False  # best MAE first
        assert p["row_limit"] == 100
        assert p["column_config"] == {
            "Periods": {"d3NumberFormat": ",d"},
            "Days": {"d3NumberFormat": ",d"},
            mae_label: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "RMSE": {"d3NumberFormat": spec.number_format},
            "WAPE": {"d3NumberFormat": ".1%"},
        }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k test_leaderboard`
Expected: FAIL on the labels list.

- [ ] **Step 3: Implement**

In `leaderboard_params`, change the metrics list and `column_config`:

```python
        "metrics": [
            sql_metric("count(*)", "Periods"),
            sql_metric("date_format(min(date_key), 'yyyy-MM-dd')", "First day"),
            sql_metric("date_format(max(date_key), 'yyyy-MM-dd')", "Last day"),
            sql_metric("count(distinct date_key)", "Days"),
            mae,
            spec.bias_metric,
            spec.rmse_metric,
            spec.wape_metric,
        ],
```

```python
        "column_config": {
            "Periods": {"d3NumberFormat": ",d"},
            "Days": {"d3NumberFormat": ",d"},
            mae["label"]: {"d3NumberFormat": spec.number_format},
            "Bias": {"d3NumberFormat": spec.signed_number_format},
            "RMSE": {"d3NumberFormat": spec.number_format},
            "WAPE": {"d3NumberFormat": ".1%"},
        },
```

Update the docstring's first line: "Params for the cross-run leaderboard table (best MAE first) with each run's window, so a matched baseline is recognisable."

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): first day, last day and day count on the run leaderboard

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `run_defaults` — the newest run, its last day and the default baseline

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (replace `latest_run`; `build_dashboard` call site)
- Test: `tests/test_create_forecast_dashboard.py` (`FakeSupersetSession._post`, new constants, `TestLatestRun` → `TestRunDefaults`)

**Interfaces:**
- Produces: `RUNS_SQL_TEMPLATE: str`; `@dataclass(frozen=True) class RunDefaults(run_label: str, last_day: str, baseline_run_label: str | None)`; `run_defaults(client: SupersetClient, database_id: int, spec: DashboardSpec, baseline_run: str | None = None) -> RunDefaults | None`; `_default_baseline(rows: list[dict], baseline_run: str | None) -> str | None`. `latest_run` is removed.
- Consumes: `RUN_LABEL_SQL` (Task 1).

- [ ] **Step 1: Update the fake and write the failing tests**

Near `DEFAULT_LABEL` in the test module add:

```python
DEFAULT_RUN_ID = "abcdef1234567890abcdef1234567890"
BASELINE_RUN_ID = "0123456789abcdef0123456789abcdef"
SHORT_RUN_ID = "ffffffff00000000ffffffff00000000"
BASELINE_LABEL = "2026-08-17 09:00 | tokyo | lightgbm | 01234567"
SHORT_LABEL = "2026-08-17 18:00 | tokyo | lightgbm | ffffffff"
# What the fake SQL Lab returns for the runs query: newest first; the second
# row is a short test window, the third the newest run with the same window.
RUN_ROWS = [
    {
        "run_id": DEFAULT_RUN_ID,
        "run_label": DEFAULT_LABEL,
        "area_code": "tokyo",
        "first_day": "2024-08-18",
        "last_day": DEFAULT_LAST_DAY,
        "periods": 34954,
    },
    {
        "run_id": SHORT_RUN_ID,
        "run_label": SHORT_LABEL,
        "area_code": "tokyo",
        "first_day": "2026-07-19",
        "last_day": DEFAULT_LAST_DAY,
        "periods": 1440,
    },
    {
        "run_id": BASELINE_RUN_ID,
        "run_label": BASELINE_LABEL,
        "area_code": "tokyo",
        "first_day": "2024-08-18",
        "last_day": DEFAULT_LAST_DAY,
        "periods": 34954,
    },
]
```

In `FakeSupersetSession._post`, change the SQL Lab branch to `return FakeResponse({"data": RUN_ROWS})`.

Replace the whole `TestLatestRun` class with:

```python
class TestRunDefaults:
    def test_newest_run_its_last_day_and_the_matched_window_baseline(self, script, fake, spec):
        client = make_client(script, fake)

        defaults = script.run_defaults(client, 3, spec)

        assert defaults == script.RunDefaults(DEFAULT_LABEL, DEFAULT_LAST_DAY, BASELINE_LABEL)
        (call,) = fake.calls_after_login()
        method, url, payload, params = call
        assert (method, url, params) == ("POST", f"{BASE}/api/v1/sqllab/execute/", None)
        assert payload["database_id"] == 3
        assert payload["runAsync"] is False
        assert f"from {spec.accuracy_table} f" in payload["sql"]
        assert f"  {script.RUN_LABEL_SQL.format(f='f', a='a')} as run_label," in payload["sql"]
        assert "date_format(min(f.date_key), 'yyyy-MM-dd') as first_day" in payload["sql"]
        assert "date_format(max(f.date_key), 'yyyy-MM-dd') as last_day" in payload["sql"]
        assert "count(*) as periods" in payload["sql"]
        assert "group by f.run_id, f.strategy, f.published_at, a.area_code" in payload["sql"]
        assert "order by f.published_at desc" in payload["sql"]
        assert "limit 100" in payload["sql"]

    def test_baseline_run_override_matches_a_run_id_prefix(self, script, fake, spot):
        client = make_client(script, fake)
        assert script.run_defaults(client, 3, spot, baseline_run="ffff").baseline_run_label == (
            SHORT_LABEL
        )
        assert script.run_defaults(client, 3, spot, baseline_run=BASELINE_RUN_ID) == (
            script.RunDefaults(DEFAULT_LABEL, DEFAULT_LAST_DAY, BASELINE_LABEL)
        )

    def test_unknown_baseline_run_warns_and_keeps_the_rule(self, script, fake, spot, monkeypatch):
        warnings: list[tuple] = []
        monkeypatch.setattr(script.logger, "warning", lambda *args, **kwargs: warnings.append(args))
        client = make_client(script, fake)

        defaults = script.run_defaults(client, 3, spot, baseline_run="nope")

        assert defaults.baseline_run_label == BASELINE_LABEL
        assert len(warnings) == 1
        assert warnings[0][1] == "nope"

    def test_no_run_shares_the_newest_window(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": RUN_ROWS[:2]}))
        defaults = script.run_defaults(make_client(script, fake), 3, spot)
        assert defaults == script.RunDefaults(DEFAULT_LABEL, DEFAULT_LAST_DAY, None)

    def test_none_on_http_error(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"message": "boom"}, 500))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None

    def test_none_when_mart_is_empty(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": []}))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None

    def test_none_when_response_has_no_data_key(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"result": "no data here"}))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None

    def test_none_when_a_row_lacks_a_column(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": [{"run_label": DEFAULT_LABEL}]}))
        assert script.run_defaults(make_client(script, fake), 3, spot) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k TestRunDefaults`
Expected: FAIL — `AttributeError: ... 'run_defaults'`.

- [ ] **Step 3: Implement**

Replace `latest_run` (function and docstring) with:

```python
# One row per run of the task's mart, newest first, with the window that makes
# a pair comparable (area, first / last delivery day, period count).
RUNS_SQL_TEMPLATE = """\
select
  f.run_id,
  {run_label_sql} as run_label,
  a.area_code,
  date_format(min(f.date_key), 'yyyy-MM-dd') as first_day,
  date_format(max(f.date_key), 'yyyy-MM-dd') as last_day,
  count(*) as periods
from {accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
group by f.run_id, f.strategy, f.published_at, a.area_code
order by f.published_at desc
limit 100
"""


@dataclass(frozen=True)
class RunDefaults:
    """The native filters' on-load values.

    Parameters
    ----------
    run_label : str
        The newest run's label — the Run filter's default.
    last_day : str
        That run's last delivery day, ``yyyy-MM-dd`` — the Day filter's default.
    baseline_run_label : str or None
        The Baseline filter's default; None when no run qualifies.
    """

    run_label: str
    last_day: str
    baseline_run_label: str | None


def run_defaults(
    client: SupersetClient,
    database_id: int,
    spec: DashboardSpec,
    baseline_run: str | None = None,
) -> RunDefaults | None:
    """The filters' on-load defaults from the mart's runs.

    ``defaultToFirstItem`` only stages a value (charts render unfiltered
    until Apply is clicked); an explicit default applies on page load. The
    labels are built by ``RUN_LABEL_SQL`` exactly like the datasets'
    ``run_label``; the day like the explanation dataset's ``trade_date_label``.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
    spec : DashboardSpec
    baseline_run : str, optional
        ``run_id`` (or a prefix of it) of the run the Baseline filter opens
        on. Without it, or when no run matches (logged as a warning), the
        default is the newest *other* run with the same area, first day, last
        day and period count as the newest run.

    Returns
    -------
    RunDefaults or None
        None when the query fails or the mart is empty (every filter then
        falls back to its ``defaultToFirstItem`` setting).
    """
    sql = RUNS_SQL_TEMPLATE.format(
        run_label_sql=RUN_LABEL_SQL.format(f="f", a="a"), accuracy_table=spec.accuracy_table
    )
    try:
        rows = client._post_json(
            "/api/v1/sqllab/execute/",
            {"database_id": database_id, "sql": sql, "runAsync": False},
        )["data"]
        newest = rows[0]
        return RunDefaults(
            newest["run_label"], newest["last_day"], _default_baseline(rows, baseline_run)
        )
    except (requests.HTTPError, KeyError, IndexError):
        return None


def _default_baseline(rows: list[dict], baseline_run: str | None) -> str | None:
    """The Baseline filter's default label from the runs query's rows.

    Parameters
    ----------
    rows : list of dict
        The runs, newest first (``RUNS_SQL_TEMPLATE`` columns).
    baseline_run : str or None
        Requested ``run_id`` or prefix; None for the matched-window rule.

    Returns
    -------
    str or None
        The requested run's label; else the newest other run with the newest
        run's area and window; None when there is none.
    """
    if baseline_run is not None:
        for row in rows:
            if row["run_id"].startswith(baseline_run):
                return row["run_label"]
        logger.warning(
            "--baseline-run {}: no such run in the mart; using the matched-window rule",
            baseline_run,
        )
    newest = rows[0]
    window = (newest["area_code"], newest["first_day"], newest["last_day"], newest["periods"])
    for row in rows[1:]:
        if (row["area_code"], row["first_day"], row["last_day"], row["periods"]) == window:
            return row["run_label"]
    return None
```

In `build_dashboard`, replace

```python
    latest = latest_run(client, database_id, spec)
    default_run, default_day = (None, None) if latest is None else latest
```

with

```python
    defaults = run_defaults(client, database_id, spec)
    default_run = None if defaults is None else defaults.run_label
    default_day = None if defaults is None else defaults.last_day
```

(The baseline default is wired into the filters in Task 9.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS (the `build_dashboard` tests still see `DEFAULT_LABEL` / `DEFAULT_LAST_DAY` from `RUN_ROWS[0]`).

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): run_defaults picks the newest run and its matched-window baseline

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The comparison dataset

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (imports; `COMPARISON_DATASET_SQL_TEMPLATE`, `COMMON_COMPARISON_COLUMNS`; `DashboardSpec` fields, docstring, derived column names, `comparison_dataset_sql`, `comparison_dataset_columns`; `SPOT_PRICE` and `DEMAND` values)
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces on `DashboardSpec`: fields `comparison_dataset_name: str`, `comparison_value_columns_sql: str`, `comparison_value_columns: tuple[tuple[str, str, bool], ...]`; properties `baseline_forecast_col`, `baseline_error_col`, `baseline_abs_error_col`, `delta_abs_error_col`, `daily_abs_error_col`, `daily_baseline_abs_error_col`, `daily_delta_abs_error_col` (all `str`), `comparison_dataset_sql: str`, `comparison_dataset_columns: list[tuple[str, str, bool]]`. Module constants `COMPARISON_DATASET_SQL_TEMPLATE: string.Template`, `COMMON_COMPARISON_COLUMNS: tuple`.

- [ ] **Step 1: Write the pinned SQL and the failing tests**

Add after `DEMAND_EXPLANATION_COLUMNS` in the test module:

```python
COMPARISON_SQL_HEAD = """\
{{% set candidate = filter_values('run_label') %}}
{{% set baseline = filter_values('baseline_run_label') %}}
with runs as (
select
  f.*,
  a.area_code,
  a.area_name_en,
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', f.strategy,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label
from {accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
),
candidate as (
select *, count(*) over () as candidate_periods
from runs
where {{% if candidate %}}run_label = '{{{{ candidate[0] | replace("'", "''") }}}}'{{% else %}}1 = 0{{% endif %}}
),
baseline as (
select *
from runs
where {{% if baseline %}}run_label = '{{{{ baseline[0] | replace("'", "''") }}}}'{{% else %}}1 = 0{{% endif %}}
),
matched as (
select
  c.date_key,
  c.trade_datetime,
  c.time_code,
  c.area_key,
  c.area_code,
  c.area_name_en,
  c.run_id,
  c.run_label,
  c.strategy,
  c.published_at,
  b.run_id as baseline_run_id,
  b.run_label as baseline_run_label,
  b.strategy as baseline_strategy,
  c.candidate_periods,
"""
COMPARISON_SQL_TAIL = """\
from candidate c
join baseline b
  on b.date_key = c.date_key
  and b.time_code = c.time_code
  and b.area_key = c.area_key
)
select
  m.date_key,
  m.trade_datetime,
  year(m.date_key) as year,
  month(m.date_key) as month,
  m.time_code,
  p.hour_of_day,
  p.day_part,
  d.day_name,
  concat(d.day_of_week_iso, ' ', substring(d.day_name, 1, 3)) as day_of_week,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  d.holiday_name_ja,
  m.area_code,
  m.area_name_en,
  m.run_id,
  m.run_label,
  m.strategy,
  m.published_at,
  m.baseline_run_id,
  m.baseline_run_label,
  m.baseline_strategy,
  m.candidate_periods,
{value_select}
  avg(m.{abs}) over (partition by m.date_key) as daily_{abs},
  avg(m.baseline_{abs}) over (partition by m.date_key) as daily_baseline_{abs},
  avg(m.delta_{abs}) over (partition by m.date_key) as daily_delta_{abs}
from matched m
join pma_curated.dim_delivery_period p on m.time_code = p.time_code
join pma_curated.dim_date d on m.date_key = d.date_key
"""
SPOT_COMPARISON_VALUES = """\
  c.forecast_price_jpy_kwh,
  b.forecast_price_jpy_kwh as baseline_forecast_price_jpy_kwh,
  c.actual_price_jpy_kwh,
  case
    when c.actual_price_jpy_kwh is null then null
    when c.actual_price_jpy_kwh < 5 then '00-05'
    when c.actual_price_jpy_kwh < 10 then '05-10'
    when c.actual_price_jpy_kwh < 15 then '10-15'
    when c.actual_price_jpy_kwh < 20 then '15-20'
    when c.actual_price_jpy_kwh < 30 then '20-30'
    when c.actual_price_jpy_kwh < 50 then '30-50'
    else '50+'
  end as actual_price_band,
  c.error_jpy_kwh,
  b.error_jpy_kwh as baseline_error_jpy_kwh,
  c.abs_error_jpy_kwh,
  b.abs_error_jpy_kwh as baseline_abs_error_jpy_kwh,
  c.abs_error_jpy_kwh - b.abs_error_jpy_kwh as delta_abs_error_jpy_kwh
"""
DEMAND_COMPARISON_VALUES = """\
  c.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  b.forecast_demand_kwh / 1000 as baseline_forecast_demand_mwh,
  c.actual_demand_kwh / 1000 as actual_demand_mwh,
  case
    when c.actual_demand_kwh is null then null
    else concat(
      lpad(cast(cast(floor(c.actual_demand_kwh / 2000000) * 2000 as int) as string), 5, '0'),
      '-',
      lpad(cast(cast(floor(c.actual_demand_kwh / 2000000) * 2000 + 2000 as int) as string), 5, '0')
    )
  end as actual_demand_band,
  c.error_kwh / 1000 as error_mwh,
  b.error_kwh / 1000 as baseline_error_mwh,
  c.abs_error_kwh / 1000 as abs_error_mwh,
  b.abs_error_kwh / 1000 as baseline_abs_error_mwh,
  (c.abs_error_kwh - b.abs_error_kwh) / 1000 as delta_abs_error_mwh
"""
SPOT_COMPARISON_VALUE_NAMES = [
    "forecast_price_jpy_kwh",
    "baseline_forecast_price_jpy_kwh",
    "actual_price_jpy_kwh",
    "actual_price_band",
    "error_jpy_kwh",
    "baseline_error_jpy_kwh",
    "abs_error_jpy_kwh",
    "baseline_abs_error_jpy_kwh",
    "delta_abs_error_jpy_kwh",
]
DEMAND_COMPARISON_VALUE_NAMES = [
    "forecast_demand_mwh",
    "baseline_forecast_demand_mwh",
    "actual_demand_mwh",
    "actual_demand_band",
    "error_mwh",
    "baseline_error_mwh",
    "abs_error_mwh",
    "baseline_abs_error_mwh",
    "delta_abs_error_mwh",
]


def comparison_sql(accuracy_table: str, values: str, names: list[str], abs_col: str) -> str:
    return (
        COMPARISON_SQL_HEAD.format(accuracy_table=accuracy_table)
        + values
        + COMPARISON_SQL_TAIL.format(
            value_select="\n".join(f"  m.{name}," for name in names), abs=abs_col
        )
    )


SPOT_COMPARISON_SQL = comparison_sql(
    "pma_curated.fct_spot_price_forecast_accuracy",
    SPOT_COMPARISON_VALUES,
    SPOT_COMPARISON_VALUE_NAMES,
    "abs_error_jpy_kwh",
)
DEMAND_COMPARISON_SQL = comparison_sql(
    "pma_curated.fct_demand_forecast_accuracy",
    DEMAND_COMPARISON_VALUES,
    DEMAND_COMPARISON_VALUE_NAMES,
    "abs_error_mwh",
)
COMPARISON_COLUMNS_HEAD = [
    ("date_key", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_of_week", "STRING", False),
    ("day_type", "STRING", False),
    ("holiday_name_ja", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("baseline_run_id", "STRING", False),
    ("baseline_run_label", "STRING", False),
    ("baseline_strategy", "STRING", False),
    ("candidate_periods", "BIGINT", False),
]
SPOT_COMPARISON_COLUMNS = (
    COMPARISON_COLUMNS_HEAD
    + [(n, "STRING" if n == "actual_price_band" else "DOUBLE", False) for n in SPOT_COMPARISON_VALUE_NAMES]
    + [
        ("daily_abs_error_jpy_kwh", "DOUBLE", False),
        ("daily_baseline_abs_error_jpy_kwh", "DOUBLE", False),
        ("daily_delta_abs_error_jpy_kwh", "DOUBLE", False),
    ]
)
DEMAND_COMPARISON_COLUMNS = (
    COMPARISON_COLUMNS_HEAD
    + [(n, "STRING" if n == "actual_demand_band" else "DOUBLE", False) for n in DEMAND_COMPARISON_VALUE_NAMES]
    + [
        ("daily_abs_error_mwh", "DOUBLE", False),
        ("daily_baseline_abs_error_mwh", "DOUBLE", False),
        ("daily_delta_abs_error_mwh", "DOUBLE", False),
    ]
)
```

(`COMPARISON_SQL_HEAD` is a `str.format` template, hence the doubled braces around the Jinja tags; after formatting they are single.)

Add to `TestDashboardSpecs`:

```python
    def test_comparison_identity(self, spot, demand):
        assert spot.comparison_dataset_name == "spot_price_forecast_comparison"
        assert demand.comparison_dataset_name == "demand_forecast_comparison"
        assert spot.baseline_forecast_col == "baseline_forecast_price_jpy_kwh"
        assert spot.baseline_error_col == "baseline_error_jpy_kwh"
        assert spot.baseline_abs_error_col == "baseline_abs_error_jpy_kwh"
        assert spot.delta_abs_error_col == "delta_abs_error_jpy_kwh"
        assert spot.daily_abs_error_col == "daily_abs_error_jpy_kwh"
        assert spot.daily_baseline_abs_error_col == "daily_baseline_abs_error_jpy_kwh"
        assert spot.daily_delta_abs_error_col == "daily_delta_abs_error_jpy_kwh"
        assert demand.baseline_abs_error_col == "baseline_abs_error_mwh"
        assert demand.delta_abs_error_col == "delta_abs_error_mwh"
        assert demand.daily_delta_abs_error_col == "daily_delta_abs_error_mwh"

    def test_comparison_dataset_sql(self, spot, demand):
        assert spot.comparison_dataset_sql == SPOT_COMPARISON_SQL
        assert demand.comparison_dataset_sql == DEMAND_COMPARISON_SQL

    def test_comparison_columns_follow_the_sql(self, spot, demand):
        assert spot.comparison_dataset_columns == SPOT_COMPARISON_COLUMNS
        assert demand.comparison_dataset_columns == DEMAND_COMPARISON_COLUMNS

    def test_comparison_columns_match_the_final_select_list_in_order(self, spec):
        final_select = spec.comparison_dataset_sql.rsplit("\nselect\n", 1)[1]
        select_list = final_select.split("\nfrom matched m", 1)[0].splitlines()
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[mpd]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.comparison_dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.comparison_dataset_columns if is_dttm] == [
            "date_key",
            "trade_datetime",
            "published_at",
        ]

    def test_comparison_value_columns_carry_the_derived_names(self, spec):
        names = [name for name, _, _ in spec.comparison_value_columns]
        assert names == [
            spec.forecast_col,
            spec.baseline_forecast_col,
            spec.actual_col,
            spec.band_col,
            spec.error_col,
            spec.baseline_error_col,
            spec.abs_error_col,
            spec.baseline_abs_error_col,
            spec.delta_abs_error_col,
        ]
        for name in names:
            # every column is either `<expr> as <name>` or the candidate's own `c.<name>`
            assert re.search(
                rf"(\bas {name}|^  c\.{name}),?$", spec.comparison_value_columns_sql, re.M
            ), name

    def test_comparison_sql_pins_both_runs_inside_the_dataset(self, spec):
        sql = spec.comparison_dataset_sql
        assert sql.startswith("{% set candidate = filter_values('run_label') %}\n")
        assert "{% set baseline = filter_values('baseline_run_label') %}" in sql
        assert sql.count("{% else %}1 = 0{% endif %}") == 2
        assert "count(*) over () as candidate_periods" in sql
        assert "join baseline b\n  on b.date_key = c.date_key" in sql
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k comparison`
Expected: FAIL — `AttributeError` / `TypeError: __init__() missing ... 'comparison_dataset_name'`.

- [ ] **Step 3: Implement**

Add `import string` to the imports. After `COMMON_EXPLANATION_COLUMNS` add:

```python
# Shared skeleton of every task's comparison dataset: the accuracy mart
# self-joined — the Run filter's run (candidate) against the Baseline filter's
# run — on delivery day x time code x area, one row per matched period.
#
# Superset renders this as Jinja (ENABLE_TEMPLATE_PROCESSING) per chart query:
# both runs are pinned inside the SQL from the native filters' values
# (filter_values), so the join touches two runs; Superset also applies the
# same two filters as an outer WHERE on run_label / baseline_run_label, which
# the rows satisfy. Without a value on either side the SQL yields no rows
# ("No data" on every chart rather than a misleading zero delta).
# candidate_periods = the candidate's count before the join (for the coverage
# tile); the daily_* columns are window averages over the day's matched
# periods, one constant per day, so tiles and tables can aggregate per day.
# A string.Template ($name) because the SQL carries Jinja braces.
COMPARISON_DATASET_SQL_TEMPLATE = string.Template("""\
{% set candidate = filter_values('run_label') %}
{% set baseline = filter_values('baseline_run_label') %}
with runs as (
select
  f.*,
  a.area_code,
  a.area_name_en,
  $run_label_sql as run_label
from $accuracy_table f
join pma_curated.dim_area a on f.area_key = a.area_key
),
candidate as (
select *, count(*) over () as candidate_periods
from runs
where {% if candidate %}run_label = '{{ candidate[0] | replace("'", "''") }}'{% else %}1 = 0{% endif %}
),
baseline as (
select *
from runs
where {% if baseline %}run_label = '{{ baseline[0] | replace("'", "''") }}'{% else %}1 = 0{% endif %}
),
matched as (
select
  c.date_key,
  c.trade_datetime,
  c.time_code,
  c.area_key,
  c.area_code,
  c.area_name_en,
  c.run_id,
  c.run_label,
  c.strategy,
  c.published_at,
  b.run_id as baseline_run_id,
  b.run_label as baseline_run_label,
  b.strategy as baseline_strategy,
  c.candidate_periods,
$comparison_value_columns_sql
from candidate c
join baseline b
  on b.date_key = c.date_key
  and b.time_code = c.time_code
  and b.area_key = c.area_key
)
select
  m.date_key,
  m.trade_datetime,
  year(m.date_key) as year,
  month(m.date_key) as month,
  m.time_code,
  p.hour_of_day,
  p.day_part,
  d.day_name,
  concat(d.day_of_week_iso, ' ', substring(d.day_name, 1, 3)) as day_of_week,
  case
    when d.is_holiday then 'Holiday'
    when d.is_weekend then 'Weekend'
    else 'Weekday'
  end as day_type,
  d.holiday_name_ja,
  m.area_code,
  m.area_name_en,
  m.run_id,
  m.run_label,
  m.strategy,
  m.published_at,
  m.baseline_run_id,
  m.baseline_run_label,
  m.baseline_strategy,
  m.candidate_periods,
$value_select_sql
  avg(m.$abs_error_col) over (partition by m.date_key) as $daily_abs_error_col,
  avg(m.$baseline_abs_error_col) over (partition by m.date_key) as $daily_baseline_abs_error_col,
  avg(m.$delta_abs_error_col) over (partition by m.date_key) as $daily_delta_abs_error_col
from matched m
join pma_curated.dim_delivery_period p on m.time_code = p.time_code
join pma_curated.dim_date d on m.date_key = d.date_key
""")

COMMON_COMPARISON_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_datetime", "TIMESTAMP", True),
    ("year", "BIGINT", False),
    ("month", "BIGINT", False),
    ("time_code", "INT", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_of_week", "STRING", False),
    ("day_type", "STRING", False),
    ("holiday_name_ja", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("baseline_run_id", "STRING", False),
    ("baseline_run_label", "STRING", False),
    ("baseline_strategy", "STRING", False),
    ("candidate_periods", "BIGINT", False),
)
```

Extend `DashboardSpec`: add to the docstring

```
    comparison_dataset_name : str
        The comparison dataset (the accuracy mart self-joined, candidate vs baseline).
    comparison_value_columns_sql : str
        The ``matched`` CTE's value block — the candidate's (``c.``) and the
        baseline's (``b.``) forecast, the actual, the actual band, both signed
        errors, both absolute errors and their difference, in the display
        unit, named ``forecast_col`` / ``baseline_forecast_col`` / … /
        ``delta_abs_error_col``; two-space indented, last line without a
        trailing comma.
    comparison_value_columns : tuple of (str, str, bool)
        Column metadata for that block, in select order.
```

add the three fields after `explanation_value_columns`:

```python
    comparison_dataset_name: str
    comparison_value_columns_sql: str
    comparison_value_columns: tuple[tuple[str, str, bool], ...]
```

and these properties after `explanation_dataset_columns`:

```python
    @property
    def baseline_forecast_col(self) -> str:
        """The baseline run's forecast column of the comparison dataset."""
        return f"baseline_{self.forecast_col}"

    @property
    def baseline_error_col(self) -> str:
        """The baseline run's signed error column of the comparison dataset."""
        return f"baseline_{self.error_col}"

    @property
    def baseline_abs_error_col(self) -> str:
        """The baseline run's absolute error column of the comparison dataset."""
        return f"baseline_{self.abs_error_col}"

    @property
    def delta_abs_error_col(self) -> str:
        """Candidate absolute error − baseline absolute error, per period."""
        return f"delta_{self.abs_error_col}"

    @property
    def daily_abs_error_col(self) -> str:
        """The candidate's daily MAE, repeated on each of the day's rows."""
        return f"daily_{self.abs_error_col}"

    @property
    def daily_baseline_abs_error_col(self) -> str:
        """The baseline's daily MAE, repeated on each of the day's rows."""
        return f"daily_baseline_{self.abs_error_col}"

    @property
    def daily_delta_abs_error_col(self) -> str:
        """Candidate daily MAE − baseline daily MAE, repeated on each of the day's rows."""
        return f"daily_delta_{self.abs_error_col}"

    @property
    def comparison_dataset_sql(self) -> str:
        """The comparison dataset's SQL: the shared Jinja template around this task's block."""
        return COMPARISON_DATASET_SQL_TEMPLATE.substitute(
            run_label_sql=RUN_LABEL_SQL.format(f="f", a="a"),
            accuracy_table=self.accuracy_table,
            comparison_value_columns_sql=self.comparison_value_columns_sql,
            value_select_sql="\n".join(f"  m.{name}," for name, _, _ in self.comparison_value_columns),
            abs_error_col=self.abs_error_col,
            baseline_abs_error_col=self.baseline_abs_error_col,
            delta_abs_error_col=self.delta_abs_error_col,
            daily_abs_error_col=self.daily_abs_error_col,
            daily_baseline_abs_error_col=self.daily_baseline_abs_error_col,
            daily_delta_abs_error_col=self.daily_delta_abs_error_col,
        )

    @property
    def comparison_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every comparison column, in select order."""
        return [
            *COMMON_COMPARISON_COLUMNS,
            *self.comparison_value_columns,
            (self.daily_abs_error_col, "DOUBLE", False),
            (self.daily_baseline_abs_error_col, "DOUBLE", False),
            (self.daily_delta_abs_error_col, "DOUBLE", False),
        ]
```

Add to `SPOT_PRICE` (after `explanation_value_columns`):

```python
    comparison_dataset_name="spot_price_forecast_comparison",
    comparison_value_columns_sql="""\
  c.forecast_price_jpy_kwh,
  b.forecast_price_jpy_kwh as baseline_forecast_price_jpy_kwh,
  c.actual_price_jpy_kwh,
  case
    when c.actual_price_jpy_kwh is null then null
    when c.actual_price_jpy_kwh < 5 then '00-05'
    when c.actual_price_jpy_kwh < 10 then '05-10'
    when c.actual_price_jpy_kwh < 15 then '10-15'
    when c.actual_price_jpy_kwh < 20 then '15-20'
    when c.actual_price_jpy_kwh < 30 then '20-30'
    when c.actual_price_jpy_kwh < 50 then '30-50'
    else '50+'
  end as actual_price_band,
  c.error_jpy_kwh,
  b.error_jpy_kwh as baseline_error_jpy_kwh,
  c.abs_error_jpy_kwh,
  b.abs_error_jpy_kwh as baseline_abs_error_jpy_kwh,
  c.abs_error_jpy_kwh - b.abs_error_jpy_kwh as delta_abs_error_jpy_kwh""",
    comparison_value_columns=(
        ("forecast_price_jpy_kwh", "DOUBLE", False),
        ("baseline_forecast_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_band", "STRING", False),
        ("error_jpy_kwh", "DOUBLE", False),
        ("baseline_error_jpy_kwh", "DOUBLE", False),
        ("abs_error_jpy_kwh", "DOUBLE", False),
        ("baseline_abs_error_jpy_kwh", "DOUBLE", False),
        ("delta_abs_error_jpy_kwh", "DOUBLE", False),
    ),
```

and to `DEMAND`:

```python
    comparison_dataset_name="demand_forecast_comparison",
    comparison_value_columns_sql="""\
  c.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  b.forecast_demand_kwh / 1000 as baseline_forecast_demand_mwh,
  c.actual_demand_kwh / 1000 as actual_demand_mwh,
  case
    when c.actual_demand_kwh is null then null
    else concat(
      lpad(cast(cast(floor(c.actual_demand_kwh / 2000000) * 2000 as int) as string), 5, '0'),
      '-',
      lpad(cast(cast(floor(c.actual_demand_kwh / 2000000) * 2000 + 2000 as int) as string), 5, '0')
    )
  end as actual_demand_band,
  c.error_kwh / 1000 as error_mwh,
  b.error_kwh / 1000 as baseline_error_mwh,
  c.abs_error_kwh / 1000 as abs_error_mwh,
  b.abs_error_kwh / 1000 as baseline_abs_error_mwh,
  (c.abs_error_kwh - b.abs_error_kwh) / 1000 as delta_abs_error_mwh""",
    comparison_value_columns=(
        ("forecast_demand_mwh", "DOUBLE", False),
        ("baseline_forecast_demand_mwh", "DOUBLE", False),
        ("actual_demand_mwh", "DOUBLE", False),
        ("actual_demand_band", "STRING", False),
        ("error_mwh", "DOUBLE", False),
        ("baseline_error_mwh", "DOUBLE", False),
        ("abs_error_mwh", "DOUBLE", False),
        ("baseline_abs_error_mwh", "DOUBLE", False),
        ("delta_abs_error_mwh", "DOUBLE", False),
    ),
```

Note the block ends with the `matched` CTE's last line and no trailing newline: the template puts the newline before `from candidate c`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS. If the pinned SQL differs, diff `spec.comparison_dataset_sql` against `DEMAND_COMPARISON_SQL` and fix whichever side departs from the template in the spec (the template above is the contract).

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): comparison dataset — the accuracy mart self-joined candidate vs baseline

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Comparison metrics on `DashboardSpec`

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`sql_metric`; `DashboardSpec` properties)
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces: `sql_metric(expression, label, option_name: str | None = None)`; on `DashboardSpec`: `delta_mae_sql: str`, `delta_mae_pct_sql: str`, `baseline_mae_metric`, `candidate_mae_metric`, `delta_mae_metric`, `delta_mae_pct_metric`, `delta_abs_bias_metric`, `delta_wape_metric`, `matched_coverage_metric`, `matched_days_metric`, `days_candidate_lower_metric`, `median_daily_delta_metric`, `error_reduction_metric` (all `dict`), `better_worse_metrics(expression: str) -> list[dict]`, `delta_band_chart_title: str`.

- [ ] **Step 1: Write the failing tests**

Add to `TestMetrics`:

```python
    def test_sql_metric_takes_an_explicit_option_name(self, script):
        m = script.sql_metric("avg(a) - avg(b)", "ΔMAE", option_name="delta_mae")
        assert m["optionName"] == "metric_delta_mae"
        assert m["label"] == "ΔMAE"
```

Add to `TestDashboardSpecs`:

```python
    def test_comparison_metrics(self, script, spot, demand):
        a, b = "abs_error_mwh", "baseline_abs_error_mwh"
        assert demand.delta_mae_sql == f"avg({a}) - avg({b})"
        assert demand.delta_mae_pct_sql == f"100 * (avg({a}) - avg({b})) / avg({b})"
        assert demand.baseline_mae_metric == script.avg_metric(b, "Baseline MAE (MWh)")
        assert demand.candidate_mae_metric == script.avg_metric(a, "Candidate MAE (MWh)")
        assert demand.delta_mae_metric == script.sql_metric(
            demand.delta_mae_sql, "ΔMAE", option_name="delta_mae"
        )
        assert demand.delta_mae_pct_metric == script.sql_metric(
            demand.delta_mae_pct_sql, "ΔMAE %", option_name="delta_mae_pct"
        )
        assert demand.delta_abs_bias_metric == script.sql_metric(
            "abs(avg(error_mwh)) - abs(avg(baseline_error_mwh))",
            "Δ|bias|",
            option_name="delta_abs_bias",
        )
        assert demand.delta_wape_metric == script.sql_metric(
            f"sum({a}) / sum(actual_demand_mwh) - sum({b}) / sum(actual_demand_mwh)",
            "ΔWAPE",
            option_name="delta_wape",
        )
        assert demand.matched_coverage_metric == script.sql_metric(
            "count(*) / max(candidate_periods)", "Matched coverage"
        )
        assert demand.matched_days_metric == script.sql_metric(
            "count(distinct date_key)", "Matched days"
        )
        assert demand.days_candidate_lower_metric == script.sql_metric(
            "count(distinct case when daily_delta_abs_error_mwh < 0 then date_key end)"
            " / count(distinct date_key)",
            "Days candidate lower",
        )
        assert demand.median_daily_delta_metric == script.sql_metric(
            "percentile(daily_delta_abs_error_mwh, 0.5)",
            "Median daily ΔMAE",
            option_name="median_daily_delta_mae",
        )
        assert demand.error_reduction_metric == script.sql_metric(
            f"sum({b}) - sum({a})", "Error reduction"
        )
        assert demand.better_worse_metrics("x") == [
            script.sql_metric("least(x, 0)", "Better"),
            script.sql_metric("greatest(x, 0)", "Worse"),
        ]
        assert spot.baseline_mae_metric["column"]["column_name"] == "baseline_abs_error_jpy_kwh"
        assert spot.baseline_mae_metric["label"] == "Baseline MAE (JPY/kWh)"
        assert spot.delta_wape_metric["sqlExpression"] == (
            "sum(abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)"
            " - sum(baseline_abs_error_jpy_kwh) / sum(actual_price_jpy_kwh)"
        )
        assert spot.days_candidate_lower_metric["sqlExpression"].startswith(
            "count(distinct case when daily_delta_abs_error_jpy_kwh < 0"
        )

    def test_delta_band_chart_title(self, spot, demand):
        assert spot.delta_band_chart_title == "ΔMAE % by actual price band"
        assert demand.delta_band_chart_title == "ΔMAE % by actual demand band"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k "comparison_metrics or option_name or delta_band"`
Expected: FAIL — `TypeError: sql_metric() got an unexpected keyword argument 'option_name'`.

- [ ] **Step 3: Implement**

Change `sql_metric`:

```python
def sql_metric(expression: str, label: str, option_name: str | None = None) -> dict:
    """Ad-hoc SQL-expression metric definition for chart params.

    Parameters
    ----------
    expression : str
        Aggregate Spark SQL expression, e.g. ``sqrt(avg(power(x, 2)))``.
    label : str
        Display label.
    option_name : str, optional
        Identifier suffix (``metric_<option_name>``); defaults to the label's
        slug, which a label without ASCII letters — ``ΔMAE %`` — cannot supply
        uniquely.

    Returns
    -------
    dict
    """
    return {
        "expressionType": "SQL",
        "sqlExpression": expression,
        "label": label,
        "optionName": f"metric_{option_name or _slug(label)}",
    }
```

Add these properties/methods to `DashboardSpec` after `actual_minus_base_metric`:

```python
    # -- comparison dataset (candidate = the Run filter's run, baseline = the Baseline filter's)

    @property
    def delta_band_chart_title(self) -> str:
        """The band chart's title on the Compare tab (ΔMAE % instead of MAE)."""
        return self.band_chart_title.replace("MAE by", "ΔMAE % by", 1)

    @property
    def delta_mae_sql(self) -> str:
        """Candidate MAE − baseline MAE over the selection (aggregate expression)."""
        return f"avg({self.abs_error_col}) - avg({self.baseline_abs_error_col})"

    @property
    def delta_mae_pct_sql(self) -> str:
        """The MAE change relative to the baseline, in percent (aggregate expression)."""
        return f"100 * ({self.delta_mae_sql}) / avg({self.baseline_abs_error_col})"

    @property
    def baseline_mae_metric(self) -> dict:
        return avg_metric(self.baseline_abs_error_col, f"Baseline MAE ({self.unit})")

    @property
    def candidate_mae_metric(self) -> dict:
        return avg_metric(self.abs_error_col, f"Candidate MAE ({self.unit})")

    @property
    def delta_mae_metric(self) -> dict:
        return sql_metric(self.delta_mae_sql, "ΔMAE", option_name="delta_mae")

    @property
    def delta_mae_pct_metric(self) -> dict:
        return sql_metric(self.delta_mae_pct_sql, "ΔMAE %", option_name="delta_mae_pct")

    @property
    def delta_abs_bias_metric(self) -> dict:
        """|candidate bias| − |baseline bias|: negative = the candidate is less biased."""
        return sql_metric(
            f"abs(avg({self.error_col})) - abs(avg({self.baseline_error_col}))",
            "Δ|bias|",
            option_name="delta_abs_bias",
        )

    @property
    def delta_wape_metric(self) -> dict:
        return sql_metric(
            f"sum({self.abs_error_col}) / sum({self.actual_col})"
            f" - sum({self.baseline_abs_error_col}) / sum({self.actual_col})",
            "ΔWAPE",
            option_name="delta_wape",
        )

    @property
    def matched_coverage_metric(self) -> dict:
        """Share of the candidate's periods the baseline also scored (1 = same window)."""
        return sql_metric("count(*) / max(candidate_periods)", "Matched coverage")

    @property
    def matched_days_metric(self) -> dict:
        return sql_metric("count(distinct date_key)", "Matched days")

    @property
    def days_candidate_lower_metric(self) -> dict:
        """Share of matched days on which the candidate's daily MAE is lower."""
        return sql_metric(
            f"count(distinct case when {self.daily_delta_abs_error_col} < 0 then date_key end)"
            " / count(distinct date_key)",
            "Days candidate lower",
        )

    @property
    def median_daily_delta_metric(self) -> dict:
        return sql_metric(
            f"percentile({self.daily_delta_abs_error_col}, 0.5)",
            "Median daily ΔMAE",
            option_name="median_daily_delta_mae",
        )

    @property
    def error_reduction_metric(self) -> dict:
        """Σ baseline |error| − Σ candidate |error| over the selection (positive = gain)."""
        return sql_metric(
            f"sum({self.baseline_abs_error_col}) - sum({self.abs_error_col})", "Error reduction"
        )

    def better_worse_metrics(self, expression: str) -> list[dict]:
        """The two series of a diverging bar: ``expression`` split by sign.

        Stacked, the negative part ("Better") and the positive part ("Worse")
        draw one bar per x value below or above zero, each in its own colour
        (``LABEL_COLORS``).

        Parameters
        ----------
        expression : str
            Signed aggregate expression, e.g. ``delta_mae_pct_sql``.

        Returns
        -------
        list of dict
            ``[Better, Worse]`` metric definitions.
        """
        return [
            sql_metric(f"least({expression}, 0)", "Better"),
            sql_metric(f"greatest({expression}, 0)", "Worse"),
        ]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): comparison metrics — deltas, coverage and the daily paired shares

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Chart builders, part 1 — label colours, delta tiles, delta bars, delta heatmaps

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (after `NOT_BASE_FILTER` … before `waterfall_params`, or after `detail_params`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestChartParams`)

**Interfaces:**
- Produces: `LABEL_COLORS: dict[str, str]`; `DELTA_HEATMAP_BOUND_PCT = 30`; `delta_big_number_params(dataset_id: int, metric: dict, subheader: str, number_format: str) -> dict`; `delta_bar_params(spec, dataset_id, x_axis) -> dict` (ΔMAE % by segment); `daily_delta_bar_params(spec, dataset_id) -> dict` (ΔMAE in the unit by `date_key`, zoomable); `delta_heatmap_params(spec, dataset_id, x_axis) -> dict`.
- Consumes: `better_worse_metrics`, `delta_mae_sql`, `delta_mae_pct_sql`, `delta_mae_pct_metric` (Task 5), `big_number_params`.

- [ ] **Step 1: Write the failing tests**

Add to `TestChartParams`:

```python
    def test_label_colors_name_the_roles(self, script):
        assert script.LABEL_COLORS == {
            "Candidate": "#1FA8C9",
            "Baseline": "#B2B2B2",
            "Actual": "#222222",
            "Better": "#1FA8C9",
            "Worse": "#FF7F44",
        }

    def test_delta_big_number_colours_the_value_by_sign(self, script, demand):
        p = script.delta_big_number_params(7, demand.delta_mae_metric, "MWh", "+,.1f")
        plain = script.big_number_params(7, demand.delta_mae_metric, "MWh", "+,.1f")
        assert {k: v for k, v in p.items() if k != "conditional_formatting"} == plain
        assert p["conditional_formatting"] == [
            {"colorScheme": "#1FA8C9", "column": "ΔMAE", "operator": "<", "targetValue": 0},
            {"colorScheme": "#FF7F44", "column": "ΔMAE", "operator": ">", "targetValue": 0},
        ]

    def test_delta_bar_is_a_stacked_better_worse_bar_of_mae_pct(self, script, spec):
        p = script.delta_bar_params(spec, 7, "day_part")
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["x_axis"] == "day_part"
        assert p["x_axis_sort"] == "day_part"
        assert p["x_axis_sort_asc"] is True
        assert p["time_grain_sqla"] is None
        assert p["metrics"] == spec.better_worse_metrics(spec.delta_mae_pct_sql)
        assert p["stack"] == "Stack"
        assert p["show_legend"] is True
        assert p["zoomable"] is False
        assert p["row_limit"] == 10000
        assert p["y_axis_format"] == "+,.1f"
        assert p["y_axis_title"] == "ΔMAE % vs baseline"
        assert p["truncateYAxis"] is False
        assert p["rich_tooltip"] is True

    def test_daily_delta_bar_is_the_unit_delta_by_day_and_zoomable(self, script, spec):
        p = script.daily_delta_bar_params(spec, 7)
        segment = script.delta_bar_params(spec, 7, "date_key")
        assert p["x_axis"] == "date_key"
        assert p["metrics"] == spec.better_worse_metrics(spec.delta_mae_sql)
        assert p["zoomable"] is True
        assert p["y_axis_format"] == "+" + spec.axis_format
        assert p["y_axis_title"] == f"Daily ΔMAE ({spec.unit}) vs baseline"
        for key in ("viz_type", "stack", "show_legend", "row_limit", "x_axis_sort_asc"):
            assert p[key] == segment[key]

    def test_delta_heatmap_is_diverging_with_symmetric_bounds(self, script, spec):
        p = script.delta_heatmap_params(spec, 7, "month")
        plain = script.heatmap_params(spec, 7, "month")
        assert p["metric"] == spec.delta_mae_pct_metric
        assert p["linear_color_scheme"] == "blue_white_yellow"
        assert p["value_bounds"] == [-30, 30]
        assert p["y_axis_format"] == "+,.1f"
        assert {
            k: v for k, v in p.items()
            if k not in ("metric", "linear_color_scheme", "value_bounds", "y_axis_format")
        } == {
            k: v for k, v in plain.items()
            if k not in ("metric", "linear_color_scheme", "value_bounds", "y_axis_format")
        }
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k "label_colors or delta_"`
Expected: FAIL — `AttributeError: ... 'LABEL_COLORS'`.

- [ ] **Step 3: Implement**

After `NOT_BASE_FILTER` (before `waterfall_params`) add:

```python
# Fixed series colours (dashboard ``label_colors``): the comparison charts
# name roles, not runs, so the same colour means the same thing for any pair.
# Blue / orange is a cool-warm pair that survives colour-vision deficiency; the
# delta tiles and the delta heatmaps' blue-white-yellow scheme put "better" on
# the same blue pole. "Actual" also recolours the Accuracy tab's detail line.
LABEL_COLORS = {
    "Candidate": "#1FA8C9",
    "Baseline": "#B2B2B2",
    "Actual": "#222222",
    "Better": "#1FA8C9",
    "Worse": "#FF7F44",
}

# ΔMAE % colour-scale bounds of the delta heatmaps, symmetric so white = no change.
DELTA_HEATMAP_BOUND_PCT = 30


def delta_big_number_params(
    dataset_id: int, metric: dict, subheader: str, number_format: str
) -> dict:
    """Params for a signed-delta KPI tile: the value turns blue below zero, orange above.

    Parameters
    ----------
    dataset_id : int
    metric : dict
        Ad-hoc metric definition; its label names the result column the
        conditional formatting reads.
    subheader : str
        Small caption under the number (include units and the sign's meaning).
    number_format : str
        d3 number format, e.g. ``+,.1f``.

    Returns
    -------
    dict
    """
    params = big_number_params(dataset_id, metric, subheader, number_format)
    params["conditional_formatting"] = [
        {
            "colorScheme": LABEL_COLORS["Better"],
            "column": metric["label"],
            "operator": "<",
            "targetValue": 0,
        },
        {
            "colorScheme": LABEL_COLORS["Worse"],
            "column": metric["label"],
            "operator": ">",
            "targetValue": 0,
        },
    ]
    return params


def _delta_bar_params(
    spec: DashboardSpec,
    dataset_id: int,
    x_axis: str,
    *,
    expression: str,
    y_axis_title: str,
    y_axis_format: str,
    zoomable: bool,
) -> dict:
    """Params for a diverging bar of a signed delta over ``x_axis``.

    Two stacked series, Better (≤ 0) and Worse (≥ 0), so each bar hangs
    below or rises above zero in its own colour; the legend names them.
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_bar",
        "x_axis": x_axis,
        "time_grain_sqla": None,
        "x_axis_sort": x_axis,
        "x_axis_sort_asc": True,
        "metrics": spec.better_worse_metrics(expression),
        "groupby": [],
        "adhoc_filters": [],
        "stack": "Stack",
        "zoomable": zoomable,
        "order_desc": False,
        "row_limit": 10000,
        "show_legend": True,
        "legendType": "scroll",
        "legendOrientation": "top",
        "rich_tooltip": True,
        "tooltipTimeFormat": "smart_date",
        "y_axis_format": y_axis_format,
        "y_axis_title": y_axis_title,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def delta_bar_params(spec: DashboardSpec, dataset_id: int, x_axis: str) -> dict:
    """Params for the ΔMAE % (candidate vs baseline) diverging bar over a segment axis.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    x_axis : str
        Comparison-dataset column for the x axis (``day_part``, ``time_code``, …).

    Returns
    -------
    dict
    """
    return _delta_bar_params(
        spec,
        dataset_id,
        x_axis,
        expression=spec.delta_mae_pct_sql,
        y_axis_title="ΔMAE % vs baseline",
        y_axis_format="+,.1f",
        zoomable=False,
    )


def daily_delta_bar_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the daily ΔMAE (in the unit) diverging bar over the window, zoomable.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.

    Returns
    -------
    dict
    """
    return _delta_bar_params(
        spec,
        dataset_id,
        "date_key",
        expression=spec.delta_mae_sql,
        y_axis_title=f"Daily ΔMAE ({spec.unit}) vs baseline",
        y_axis_format="+" + spec.axis_format,
        zoomable=True,
    )


def delta_heatmap_params(spec: DashboardSpec, dataset_id: int, x_axis: str) -> dict:
    """Params for a ΔMAE % heatmap (year on y, ``x_axis`` on x) on a diverging scale.

    ``blue_white_yellow`` with bounds ±``DELTA_HEATMAP_BOUND_PCT``: white is
    "no change", blue better, yellow worse.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    x_axis : str
        ``time_code`` or ``month``.

    Returns
    -------
    dict
    """
    return {
        **heatmap_params(spec, dataset_id, x_axis),
        "metric": spec.delta_mae_pct_metric,
        "linear_color_scheme": "blue_white_yellow",
        "value_bounds": [-DELTA_HEATMAP_BOUND_PCT, DELTA_HEATMAP_BOUND_PCT],
        "y_axis_format": "+,.1f",
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): delta tiles, diverging delta bars and delta heatmaps

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Chart builders, part 2 — cumulative reduction line, ranked-day tables, three-line detail

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (after `delta_heatmap_params`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestChartParams`)

**Interfaces:**
- Produces: `cumulative_reduction_params(spec, dataset_id) -> dict`; `ranked_days_params(spec, dataset_id, *, improved: bool) -> dict`; `comparison_detail_params(spec, dataset_id) -> dict`.
- Consumes: `error_reduction_metric`, `baseline_mae_metric`, `candidate_mae_metric`, `delta_mae_metric`, `delta_mae_pct_metric`, `baseline_forecast_col` (Tasks 4–5), `detail_params`.

- [ ] **Step 1: Write the failing tests**

Add to `TestChartParams`:

```python
    def test_cumulative_reduction_is_a_cumsum_line_by_day(self, script, spec):
        p = script.cumulative_reduction_params(spec, 7)
        plain = script.detail_params(spec, 7)
        assert p["viz_type"] == "echarts_timeseries_line"
        assert p["x_axis"] == "date_key"
        assert p["time_grain_sqla"] is None
        assert p["metrics"] == [spec.error_reduction_metric]
        assert p["rolling_type"] == "cumsum"
        assert p["zoomable"] is True
        assert p["show_legend"] is False
        assert p["row_limit"] == 10000
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == f"{spec.unit}; Σ (baseline |error| − candidate |error|)"
        for key in ("seriesType", "opacity", "markerEnabled", "time_range", "comparison_type"):
            assert p[key] == plain[key]

    @pytest.mark.parametrize("improved, order_desc", [(True, False), (False, True)])
    def test_ranked_days_tables(self, script, spec, improved, order_desc):
        p = script.ranked_days_params(spec, 7, improved=improved)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["date_key", "day_of_week", "day_type", "holiday_name_ja"]
        assert p["metrics"] == [
            spec.baseline_mae_metric,
            spec.candidate_mae_metric,
            spec.delta_mae_metric,
            spec.delta_mae_pct_metric,
        ]
        assert p["timeseries_limit_metric"] == spec.delta_mae_metric
        assert p["order_desc"] is order_desc
        assert p["row_limit"] == 10
        assert p["server_page_length"] == 10
        assert p["table_timestamp_format"] == "%Y-%m-%d"
        assert p["column_config"] == {
            f"Baseline MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            f"Candidate MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            "ΔMAE": {"d3NumberFormat": spec.signed_number_format},
            "ΔMAE %": {"d3NumberFormat": "+,.1f"},
        }

    def test_comparison_detail_has_three_lines(self, script, spec):
        p = script.comparison_detail_params(spec, 7)
        plain = script.detail_params(spec, 7)
        assert [m["label"] for m in p["metrics"]] == ["Actual", "Candidate", "Baseline"]
        assert [m["column"]["column_name"] for m in p["metrics"]] == [
            spec.actual_col,
            spec.forecast_col,
            spec.baseline_forecast_col,
        ]
        assert {k: v for k, v in p.items() if k != "metrics"} == {
            k: v for k, v in plain.items() if k != "metrics"
        }
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k "cumulative or ranked_days or comparison_detail"`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

```python
def cumulative_reduction_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the running total of the error reduction over the window.

    Per day, Σ baseline |error| − Σ candidate |error|, accumulated
    (``rolling_type: cumsum``): a steady slope is a broad gain, a few steps a
    gain concentrated in a few days.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.

    Returns
    -------
    dict
    """
    return {
        **detail_params(spec, dataset_id),
        "x_axis": "date_key",
        "metrics": [spec.error_reduction_metric],
        "rolling_type": "cumsum",
        "row_limit": 10000,
        "show_legend": False,
        "y_axis_format": spec.axis_format,
        "y_axis_title": f"{spec.unit}; Σ (baseline |error| − candidate |error|)",
    }


def ranked_days_params(spec: DashboardSpec, dataset_id: int, *, improved: bool) -> dict:
    """Params for the most-improved (or most-worsened) days table.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.
    improved : bool
        True = lowest daily ΔMAE first (the candidate's biggest gains);
        False = highest first.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["date_key", "day_of_week", "day_type", "holiday_name_ja"],
        "metrics": [
            spec.baseline_mae_metric,
            spec.candidate_mae_metric,
            spec.delta_mae_metric,
            spec.delta_mae_pct_metric,
        ],
        "adhoc_filters": [],
        "timeseries_limit_metric": spec.delta_mae_metric,
        "order_desc": not improved,
        "row_limit": 10,
        "server_page_length": 10,
        "table_timestamp_format": "%Y-%m-%d",
        "column_config": {
            spec.baseline_mae_metric["label"]: {"d3NumberFormat": spec.number_format},
            spec.candidate_mae_metric["label"]: {"d3NumberFormat": spec.number_format},
            "ΔMAE": {"d3NumberFormat": spec.signed_number_format},
            "ΔMAE %": {"d3NumberFormat": "+,.1f"},
        },
        "extra_form_data": {},
    }


def comparison_detail_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the 30-minute detail with the actual, the candidate and the baseline.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The comparison dataset.

    Returns
    -------
    dict
    """
    return {
        **detail_params(spec, dataset_id),
        "metrics": [
            avg_metric(spec.actual_col, "Actual"),
            avg_metric(spec.forecast_col, "Candidate"),
            avg_metric(spec.baseline_forecast_col, "Baseline"),
        ],
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): cumulative error-reduction line, ranked-day tables, three-line detail

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The Baseline filter, multi-emitter cross-filters and the label colours on the dashboard

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`build_native_filters`, `build_chart_configuration`, `upsert_dashboard`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestBuildNativeFilters`, `TestUpsertDashboard`, new `TestBuildChartConfiguration`)

**Interfaces:**
- Produces: `build_native_filters(*, dataset_id, run_excluded, default_run_label, explanation_dataset_id, day_excluded, default_day_label, baseline_excluded, default_baseline_label) -> list[dict]` returning `[run, day, baseline]`; `build_chart_configuration(emitters: dict[int, list[int]], all_charts: list[int]) -> dict`; `upsert_dashboard` writes `LABEL_COLORS` as `label_colors`.
- Consumes: `LABEL_COLORS` (Task 6).

- [ ] **Step 1: Update the failing tests**

In `TestBuildNativeFilters.test_explicit_defaults_apply_on_load` replace the call with

```python
        run, day, baseline = script.build_native_filters(
            dataset_id=10,
            run_excluded=[27],
            default_run_label=DEFAULT_LABEL,
            explanation_dataset_id=11,
            day_excluded=[12, 13],
            default_day_label=DEFAULT_LAST_DAY,
            baseline_excluded=[12, 13, 14],
            default_baseline_label=BASELINE_LABEL,
        )
        assert [f["type"] for f in (run, day, baseline)] == ["NATIVE_FILTER"] * 3
        assert [f["filterType"] for f in (run, day, baseline)] == ["filter_select"] * 3
```

and append to that test:

```python
        assert baseline["id"] == "NATIVE_FILTER-baseline"
        assert baseline["name"] == "Baseline"
        assert baseline["targets"] == [{"column": {"name": "baseline_run_label"}, "datasetId": 10}]
        assert baseline["defaultDataMask"] == {
            "extraFormData": {
                "filters": [{"col": "baseline_run_label", "op": "IN", "val": [BASELINE_LABEL]}]
            },
            "filterState": {"value": [BASELINE_LABEL], "label": BASELINE_LABEL},
        }
        assert baseline["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": True,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": False,
        }
        assert baseline["cascadeParentIds"] == []
        assert baseline["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [12, 13, 14]}
        assert "Compare tab" in baseline["description"]
```

Replace `test_no_defaults_fall_back_to_first_item` with:

```python
    def test_no_defaults_fall_back_to_first_item_except_baseline(self, script):
        run, day, baseline = script.build_native_filters(
            dataset_id=10,
            run_excluded=[],
            default_run_label=None,
            explanation_dataset_id=11,
            day_excluded=[],
            default_day_label=None,
            baseline_excluded=[],
            default_baseline_label=None,
        )
        assert run["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert run["controlValues"]["defaultToFirstItem"] is True
        assert run["scope"] == {"rootPath": ["ROOT_ID"], "excluded": []}
        assert day["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert day["controlValues"]["defaultToFirstItem"] is True
        # No baseline: the Compare tab stays on "No data" until one is picked
        assert baseline["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert baseline["controlValues"]["defaultToFirstItem"] is False
```

In `TestUpsertDashboard.test_creates_then_writes_layout_and_metadata` replace the `filters = ...` line with the keyword form (`dataset_id=10, run_excluded=[27], default_run_label=None, explanation_dataset_id=11, day_excluded=[], default_day_label=None, baseline_excluded=[], default_baseline_label=None`) and add `assert metadata["label_colors"] == script.LABEL_COLORS` after the `color_scheme` assertion.

Add a new class before `TestUpsertDashboard`:

```python
class TestBuildChartConfiguration:
    def test_each_emitter_scopes_its_targets_and_excludes_the_rest(self, script):
        configuration = script.build_chart_configuration(
            {30: [31, 32, 61], 59: [61, 32]}, [29, 30, 31, 32, 59, 60, 61]
        )
        assert list(configuration) == ["30", "59"]
        assert configuration["30"] == {
            "id": 30,
            "crossFilters": {
                "scope": {"rootPath": ["ROOT_ID"], "excluded": [29, 30, 59, 60]},
                "chartsInScope": [31, 32, 61],
            },
        }
        assert configuration["59"]["crossFilters"]["chartsInScope"] == [61, 32]
        assert configuration["59"]["crossFilters"]["scope"]["excluded"] == [29, 30, 31, 59, 60]

    def test_no_emitters_no_configuration(self, script):
        assert script.build_chart_configuration({}, [1, 2]) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k "NativeFilters or UpsertDashboard or ChartConfiguration"`
Expected: FAIL — `TypeError: build_native_filters() got an unexpected keyword argument`.

- [ ] **Step 3: Implement**

Replace `build_native_filters`:

```python
def build_native_filters(
    *,
    dataset_id: int,
    run_excluded: list[int],
    default_run_label: str | None,
    explanation_dataset_id: int,
    day_excluded: list[int],
    default_day_label: str | None,
    baseline_excluded: list[int],
    default_baseline_label: str | None,
) -> list[dict]:
    """Native filter configuration: Run (whole dashboard), Day (the
    Explanation tab only), Baseline (the Compare tab only).

    Parameters
    ----------
    dataset_id : int
        Analysis dataset the Run and Baseline filters read their options
        from (``run_label`` / its alias ``baseline_run_label``).
    run_excluded : list of int
        Charts the Run filter must NOT apply to (the cross-run leaderboard).
    default_run_label : str or None
        Explicit on-load run; None falls back to ``defaultToFirstItem``.
    explanation_dataset_id : int
        Explanation dataset the Day filter reads its values from.
    day_excluded : list of int
        Charts outside the Day filter's scope (everything but the
        Explanation tab).
    default_day_label : str or None
        Explicit on-load day (the default run's last delivery day).
    baseline_excluded : list of int
        Charts outside the Baseline filter's scope (everything but the
        Compare tab — on the analysis dataset the alias column would
        otherwise empty every chart).
    default_baseline_label : str or None
        Explicit on-load baseline; None leaves the filter empty (no first-item
        fallback: the Compare tab shows "No data" until a baseline is picked).

    Returns
    -------
    list of dict
        ``[run, day, baseline]``.
    """
    return [
        _select_filter(
            "NATIVE_FILTER-run",
            "Run",
            "run_label",
            dataset_id,
            excluded=run_excluded,
            default=default_run_label,
            default_to_first=True,
            required=True,
            sort_ascending=False,
            cascade_parent_ids=[],
            description="published_at | area | strategy | run_id prefix (newest first)",
        ),
        _select_filter(
            "NATIVE_FILTER-day",
            "Day",
            "trade_date_label",
            explanation_dataset_id,
            excluded=day_excluded,
            default=default_day_label,
            default_to_first=True,
            required=False,
            sort_ascending=False,
            cascade_parent_ids=["NATIVE_FILTER-run"],
            description=(
                "Delivery day explained (empty = the run's mean decomposition); clear Day, "
                "or pick the same day, before following a Worst days click — the two filters "
                "combine"
            ),
        ),
        _select_filter(
            "NATIVE_FILTER-baseline",
            "Baseline",
            "baseline_run_label",
            dataset_id,
            excluded=baseline_excluded,
            default=default_baseline_label,
            default_to_first=False,
            required=True,
            sort_ascending=False,
            cascade_parent_ids=[],
            description=(
                "Reference run; the Compare tab shows the Run (candidate) against it over "
                "the periods both runs scored"
            ),
        ),
    ]
```

Replace `build_chart_configuration`:

```python
def build_chart_configuration(emitters: dict[int, list[int]], all_charts: list[int]) -> dict:
    """Per-chart cross-filter scopes: clicking a row of an emitter table
    selects that day on its target charts only.

    Parameters
    ----------
    emitters : dict of int to list of int
        Emitter chart id → the charts that receive its cross-filter.
    all_charts : list of int
        Every chart on the dashboard; those not targeted (the emitter
        included) are excluded from its scope.

    Returns
    -------
    dict
        ``json_metadata["chart_configuration"]``.
    """
    return {
        str(emitter): {
            "id": emitter,
            "crossFilters": {
                "scope": {
                    "rootPath": ["ROOT_ID"],
                    "excluded": [c for c in all_charts if c not in targets],
                },
                "chartsInScope": targets,
            },
        }
        for emitter, targets in emitters.items()
    }
```

In `upsert_dashboard`, change `"label_colors": {},` to `"label_colors": LABEL_COLORS,` and add to its docstring: "``label_colors`` pins the comparison roles' colours (``LABEL_COLORS``)."

Temporarily keep `build_dashboard` compiling: change its two call sites to the new signatures —

```python
    chart_configuration = build_chart_configuration(
        {worst_days: cross_filter_targets}, all_charts
    )
```

and

```python
        build_native_filters(
            dataset_id=dataset_id,
            run_excluded=[leaderboard],
            default_run_label=default_run,
            explanation_dataset_id=explanation_id,
            day_excluded=analysis_charts,
            default_day_label=default_day,
            baseline_excluded=all_charts,
            default_baseline_label=None,
        ),
```

(Task 9 wires the real scopes and default.) In `TestBuildDashboard` the filter unpacking `run_filter, day_filter = ...` (three places) becomes `run_filter, day_filter, _ = ...`, and `test_worst_days_cross_filter_is_scoped_to_the_explanation_tab` keeps passing unchanged (the excluded list is the same set).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): Baseline native filter, multi-emitter cross-filters, fixed label colours

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: The Compare tab in `build_dashboard` and `--baseline-run`

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`build_dashboard`, `main`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestBuildDashboard`, `TestMain`, chart-name constants)

**Interfaces:**
- Produces: `build_dashboard(client, database_id, spec, baseline_run: str | None = None) -> int`; `main` option `--baseline-run`; chart names on the comparison dataset, in creation order: `Baseline MAE`, `Candidate MAE`, `ΔMAE vs baseline`, `ΔMAE % vs baseline`, `Δ|bias| vs baseline`, `ΔWAPE vs baseline`, `Matched coverage`, `Matched days`, `Days candidate lower`, `Median daily ΔMAE`, `ΔMAE % by time code`, `ΔMAE % by day part`, `ΔMAE % by day type`, `ΔMAE % by day of week`, `<spec.delta_band_chart_title>`, `ΔMAE % by year`, `ΔMAE % by year and month`, `ΔMAE % by year and time code`, `Daily ΔMAE`, `Cumulative error reduction`, `Most improved days`, `Most worsened days`, `Candidate vs baseline vs actual (30-min detail)`; tab title `Compare`; section headers `Where the candidate wins (ΔMAE % vs baseline)`, `Day by day`, `Detail`.
- Consumes: everything from Tasks 3–8.

- [ ] **Step 1: Update the expectations and add the failing tests**

Add after `EXPLANATION_CHART_NAMES`:

```python
COMPARISON_CHART_NAMES_HEAD = [
    "Baseline MAE",
    "Candidate MAE",
    "ΔMAE vs baseline",
    "ΔMAE % vs baseline",
    "Δ|bias| vs baseline",
    "ΔWAPE vs baseline",
    "Matched coverage",
    "Matched days",
    "Days candidate lower",
    "Median daily ΔMAE",
    "ΔMAE % by time code",
    "ΔMAE % by day part",
    "ΔMAE % by day type",
    "ΔMAE % by day of week",
]
COMPARISON_CHART_NAMES_TAIL = [
    "ΔMAE % by year",
    "ΔMAE % by year and month",
    "ΔMAE % by year and time code",
    "Daily ΔMAE",
    "Cumulative error reduction",
    "Most improved days",
    "Most worsened days",
    "Candidate vs baseline vs actual (30-min detail)",
]
SPOT_COMPARISON_CHART_NAMES = (
    COMPARISON_CHART_NAMES_HEAD + ["ΔMAE % by actual price band"] + COMPARISON_CHART_NAMES_TAIL
)
DEMAND_COMPARISON_CHART_NAMES = (
    COMPARISON_CHART_NAMES_HEAD + ["ΔMAE % by actual demand band"] + COMPARISON_CHART_NAMES_TAIL
)
EXPECTED_COMPARE_TAB_CHILDREN = [
    "ROW-2-0-0",
    "ROW-2-0-1",
    "HEADER-2-1",
    "ROW-2-1-0",
    "ROW-2-1-1",
    "ROW-2-1-2",
    "ROW-2-1-3",
    "ROW-2-1-4",
    "HEADER-2-2",
    "ROW-2-2-0",
    "ROW-2-2-1",
    "ROW-2-2-2",
    "HEADER-2-3",
    "ROW-2-3-0",
]
```

Rewrite `TestBuildDashboard.test_builds_dataset_charts_and_dashboard` with the new id arithmetic (datasets 10 / 11 / 12; charts 13–61; dashboard 62):

```python
    def test_builds_dataset_charts_and_dashboard(self, script, superset, demand):
        client = make_client(script, superset)

        dashboard_id = script.build_dashboard(client, 3, demand)

        # datasets: analysis, explanation, comparison
        analysis, explanation, comparison = superset.rows["dataset"].values()
        assert (analysis["id"], explanation["id"], comparison["id"]) == (10, 11, 12)
        assert analysis["table_name"] == "demand_forecast_analysis"
        assert analysis["sql"] == DEMAND_DATASET_SQL
        assert analysis["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in analysis["columns"]] == (
            DEMAND_COLUMNS
        )
        assert explanation["table_name"] == "demand_forecast_explanation"
        assert explanation["sql"] == DEMAND_EXPLANATION_SQL
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in explanation["columns"]] == (
            DEMAND_EXPLANATION_COLUMNS
        )
        assert comparison["table_name"] == "demand_forecast_comparison"
        assert comparison["sql"] == DEMAND_COMPARISON_SQL
        assert comparison["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in comparison["columns"]] == (
            DEMAND_COMPARISON_COLUMNS
        )
        dataset_puts = [
            c
            for c in superset.calls
            if c[0] == "PUT" and c[1] in {f"{BASE}/api/v1/dataset/{i}" for i in (10, 11, 12)}
        ]
        assert [c[3] for c in dataset_puts] == [{"override_columns": "true"}] * 3

        # charts, in creation order: 19 analysis, 7 explanation, 23 comparison
        charts = list(superset.rows["chart"].values())
        assert [c["slice_name"] for c in charts] == (
            EXPECTED_DEMAND_CHART_NAMES + EXPLANATION_CHART_NAMES + DEMAND_COMPARISON_CHART_NAMES
        )
        assert [c["id"] for c in charts] == list(range(13, 62))
        for c, dataset_id in zip(charts, [10] * 19 + [11] * 7 + [12] * 23, strict=True):
            assert c["datasource_id"] == dataset_id
            assert json.loads(c["params"])["datasource"] == f"{dataset_id}__table"
            assert c["datasource_type"] == "table"
            assert json.loads(c["params"])["viz_type"] == c["viz_type"]
        by_name = {c["slice_name"]: json.loads(c["params"]) for c in charts}
        assert by_name["Overall MAE"]["viz_type"] == "big_number_total"
        assert by_name["Overall MAE"]["metric"] == demand.mae_metric
        assert by_name["Overall MAE"]["subheader"] == "MWh"
        assert by_name["Bias (mean error)"]["y_axis_format"] == "+,.1f"
        assert by_name["WAPE"]["y_axis_format"] == ".1%"
        assert by_name["P90 abs error"]["metric"] == demand.p90_metric
        assert by_name["MAE by year and month"]["x_axis"] == "month"
        assert by_name["MAE by actual demand band"]["x_axis"] == "actual_demand_band"
        assert by_name["Error distribution"]["column"] == "error_mwh"
        assert by_name["Run leaderboard"]["viz_type"] == "table"
        assert by_name["Base value"]["metric"] == demand.base_value_metric
        assert by_name["Contributions by period"]["viz_type"] == "mixed_timeseries"
        # the Compare tab
        assert by_name["Baseline MAE"]["metric"] == demand.baseline_mae_metric
        assert by_name["Baseline MAE"]["subheader"] == "MWh; the Baseline run, matched periods"
        assert by_name["Candidate MAE"]["metric"] == demand.candidate_mae_metric
        assert by_name["Candidate MAE"]["subheader"] == "MWh; the Run, matched periods"
        assert by_name["ΔMAE vs baseline"]["metric"] == demand.delta_mae_metric
        assert by_name["ΔMAE vs baseline"]["subheader"] == "MWh; − = candidate better"
        assert by_name["ΔMAE vs baseline"]["y_axis_format"] == "+,.1f"
        assert by_name["ΔMAE vs baseline"]["conditional_formatting"][0]["column"] == "ΔMAE"
        assert by_name["ΔMAE % vs baseline"]["metric"] == demand.delta_mae_pct_metric
        assert by_name["ΔMAE % vs baseline"]["subheader"] == "%; − = candidate better"
        assert by_name["ΔMAE % vs baseline"]["y_axis_format"] == "+,.1f"
        assert by_name["Δ|bias| vs baseline"]["metric"] == demand.delta_abs_bias_metric
        assert by_name["Δ|bias| vs baseline"]["subheader"] == "MWh; − = candidate less biased"
        assert by_name["ΔWAPE vs baseline"]["metric"] == demand.delta_wape_metric
        assert by_name["ΔWAPE vs baseline"]["y_axis_format"] == "+.2%"
        assert by_name["Matched coverage"]["metric"] == demand.matched_coverage_metric
        assert by_name["Matched coverage"]["y_axis_format"] == ".1%"
        assert by_name["Matched coverage"]["subheader"] == (
            "share of the candidate's periods the baseline also scored"
        )
        assert "conditional_formatting" not in by_name["Matched coverage"]
        assert by_name["Matched days"]["metric"] == demand.matched_days_metric
        assert by_name["Matched days"]["y_axis_format"] == ",d"
        assert by_name["Days candidate lower"]["metric"] == demand.days_candidate_lower_metric
        assert by_name["Days candidate lower"]["y_axis_format"] == ".1%"
        assert by_name["Median daily ΔMAE"]["metric"] == demand.median_daily_delta_metric
        assert by_name["Median daily ΔMAE"]["conditional_formatting"][1]["operator"] == ">"
        assert by_name["ΔMAE % by time code"]["x_axis"] == "time_code"
        assert by_name["ΔMAE % by day part"]["x_axis"] == "day_part"
        assert by_name["ΔMAE % by day type"]["x_axis"] == "day_type"
        assert by_name["ΔMAE % by day of week"]["x_axis"] == "day_of_week"
        assert by_name["ΔMAE % by actual demand band"]["x_axis"] == "actual_demand_band"
        assert by_name["ΔMAE % by year"]["x_axis"] == "year"
        assert by_name["ΔMAE % by year and month"]["viz_type"] == "heatmap_v2"
        assert by_name["ΔMAE % by year and month"]["x_axis"] == "month"
        assert by_name["ΔMAE % by year and time code"]["x_axis"] == "time_code"
        assert by_name["Daily ΔMAE"]["x_axis"] == "date_key"
        assert by_name["Daily ΔMAE"]["zoomable"] is True
        assert by_name["Cumulative error reduction"]["rolling_type"] == "cumsum"
        assert by_name["Most improved days"]["order_desc"] is False
        assert by_name["Most worsened days"]["order_desc"] is True
        assert [
            m["label"] for m in by_name["Candidate vs baseline vs actual (30-min detail)"]["metrics"]
        ] == ["Actual", "Candidate", "Baseline"]

        # dashboard
        (dashboard,) = superset.rows["dashboard"].values()
        assert dashboard_id == dashboard["id"] == 62
        assert dashboard["dashboard_title"] == "Demand Forecast Analysis"
        assert dashboard["slug"] == "demand-forecast-analysis"
        assert dashboard["published"] is True
        metadata = json.loads(dashboard["json_metadata"])
        assert set(metadata) == EXPECTED_JSON_METADATA_KEYS
        assert metadata["label_colors"] == script.LABEL_COLORS
        run_filter, day_filter, baseline_filter = metadata["native_filter_configuration"]
        analysis_ids = list(range(13, 32))
        explanation_ids = list(range(32, 39))
        comparison_ids = list(range(39, 62))
        leaderboard_id = superset.id_of("chart", "slice_name", "Run leaderboard")
        assert leaderboard_id == 29
        assert run_filter["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        assert run_filter["scope"]["excluded"] == [leaderboard_id]
        assert run_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LABEL]
        assert run_filter["controlValues"]["defaultToFirstItem"] is False
        # Day applies to the Explanation tab only
        assert day_filter["targets"] == [{"column": {"name": "trade_date_label"}, "datasetId": 11}]
        assert day_filter["scope"]["excluded"] == analysis_ids + comparison_ids
        assert day_filter["cascadeParentIds"] == ["NATIVE_FILTER-run"]
        assert day_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LAST_DAY]
        # Baseline applies to the Compare tab only, reading its options off the analysis dataset
        assert baseline_filter["targets"] == [
            {"column": {"name": "baseline_run_label"}, "datasetId": 10}
        ]
        assert baseline_filter["scope"]["excluded"] == analysis_ids + explanation_ids
        assert baseline_filter["defaultDataMask"]["filterState"]["value"] == [BASELINE_LABEL]
        assert baseline_filter["controlValues"]["enableEmptyFilter"] is True

        position = json.loads(dashboard["position_json"])
        assert position["HEADER_ID"]["meta"]["text"] == "Demand Forecast Analysis"
        chart_keys = sorted(k for k in position if k.startswith("CHART-"))
        assert chart_keys == sorted(f"CHART-{i}" for i in range(13, 62))
        assert position["ROOT_ID"]["children"] == ["TABS-0"]
        assert position["TABS-0"]["children"] == ["TAB-0", "TAB-1", "TAB-2"]
        assert [position[t]["meta"]["text"] for t in ("TAB-0", "TAB-1", "TAB-2")] == [
            "Accuracy",
            "Explanation (SHAP)",
            "Compare",
        ]
        assert position["TAB-0"]["children"] == EXPECTED_ACCURACY_TAB_CHILDREN
        assert position["TAB-1"]["children"] == EXPECTED_EXPLANATION_TAB_CHILDREN
        assert position["TAB-2"]["children"] == EXPECTED_COMPARE_TAB_CHILDREN
        assert [
            position[h]["meta"]["text"] for h in ("HEADER-0-1", "HEADER-0-2", "HEADER-0-3")
        ] == ["Error structure", "Calibration & distribution", "Runs & drilldown"]
        assert [
            position[h]["meta"]["text"] for h in ("HEADER-2-1", "HEADER-2-2", "HEADER-2-3")
        ] == ["Where the candidate wins (ΔMAE % vs baseline)", "Day by day", "Detail"]
        assert position["ROW-0-0-0"]["children"] == [f"CHART-{i}" for i in range(13, 19)]
        assert position["CHART-13"]["meta"] == {
            "chartId": 13,
            "width": 2,
            "height": 24,
            "sliceName": "Overall MAE",
        }
        assert position["ROW-0-2-0"]["children"] == ["CHART-26", "CHART-27"]
        assert (position["CHART-26"]["meta"]["width"], position["CHART-27"]["meta"]["width"]) == (
            5,
            7,
        )
        assert position["ROW-0-3-0"]["children"] == [f"CHART-{leaderboard_id}"]
        assert position["CHART-31"]["meta"]["height"] == 60  # 30-min detail
        assert position["ROW-1-0-0"]["children"] == [f"CHART-{i}" for i in range(32, 36)]
        assert position["ROW-1-0-1"]["children"] == ["CHART-36", "CHART-37"]
        assert position["ROW-1-0-2"]["children"] == ["CHART-38"]
        assert position["CHART-38"]["parents"] == ["ROOT_ID", "TABS-0", "TAB-1", "ROW-1-0-2"]
        # Compare tab rows
        assert position["ROW-2-0-0"]["children"] == [f"CHART-{i}" for i in range(39, 45)]
        assert position["CHART-39"]["meta"] == {
            "chartId": 39,
            "width": 2,
            "height": 24,
            "sliceName": "Baseline MAE",
        }
        assert position["ROW-2-0-1"]["children"] == [f"CHART-{i}" for i in range(45, 49)]
        assert position["CHART-45"]["meta"]["width"] == 3
        assert position["ROW-2-1-0"]["children"] == ["CHART-49"]
        assert (position["CHART-49"]["meta"]["width"], position["CHART-49"]["meta"]["height"]) == (
            12,
            36,
        )
        assert position["ROW-2-1-1"]["children"] == ["CHART-50", "CHART-51", "CHART-52"]
        assert position["CHART-50"]["meta"]["width"] == 4
        assert position["ROW-2-1-2"]["children"] == ["CHART-53", "CHART-54"]
        assert position["CHART-53"]["meta"] == {
            "chartId": 53,
            "width": 6,
            "height": 36,
            "sliceName": "ΔMAE % by actual demand band",
        }
        assert position["ROW-2-1-3"]["children"] == ["CHART-55"]
        assert position["CHART-55"]["meta"]["height"] == 46
        assert position["ROW-2-1-4"]["children"] == ["CHART-56"]
        assert position["CHART-56"]["meta"]["height"] == 50
        assert position["ROW-2-2-0"]["children"] == ["CHART-57"]
        assert position["CHART-57"]["meta"]["height"] == 44
        assert position["ROW-2-2-1"]["children"] == ["CHART-58"]
        assert position["CHART-58"]["meta"]["height"] == 40
        assert position["ROW-2-2-2"]["children"] == ["CHART-59", "CHART-60"]
        assert (position["CHART-59"]["meta"]["width"], position["CHART-59"]["meta"]["height"]) == (
            6,
            40,
        )
        assert position["ROW-2-3-0"]["children"] == ["CHART-61"]
        assert position["CHART-61"]["meta"]["height"] == 60
        assert position["CHART-61"]["parents"] == ["ROOT_ID", "TABS-0", "TAB-2", "ROW-2-3-0"]

        # every chart is linked to the dashboard
        assert all(c["dashboards"] == [62] for c in charts)
        assert method_counts(superset.calls, "dataset") == {"GET": 3, "POST": 3, "PUT": 3}
        assert method_counts(superset.calls, "chart") == {"GET": 49, "POST": 49, "PUT": 49}
        assert method_counts(superset.calls, "dashboard") == {"GET": 1, "POST": 1, "PUT": 1}
```

Update the other `TestBuildDashboard` tests:

- `test_spot_price_dashboard_keeps_its_names_layout_and_formats`: `analysis, explanation, comparison = superset.rows["dataset"].values()`, add `assert comparison["table_name"] == "spot_price_forecast_comparison"` and `assert comparison["sql"] == SPOT_COMPARISON_SQL`; chart names `EXPECTED_SPOT_CHART_NAMES + EXPLANATION_CHART_NAMES + SPOT_COMPARISON_CHART_NAMES`; add `assert by_name["ΔMAE vs baseline"]["y_axis_format"] == "+,.3f"` and `assert by_name["Daily ΔMAE"]["y_axis_format"] == "+,.2f"`; `TABS-0` children `["TAB-0", "TAB-1", "TAB-2"]`; `TAB-2` children `EXPECTED_COMPARE_TAB_CHILDREN`; `ROW-0-3-0` = `["CHART-29"]`; `run_filter, _, baseline_filter = ...`; `run_filter["scope"]["excluded"] == [29]`; `baseline_filter["scope"]["excluded"] == list(range(13, 39))`.
- `test_two_dashboards_coexist_with_their_own_datasets_and_charts`: `(spot_id, demand_id) == (62, 115)` with the comment `# 3 datasets + 49 charts + dashboard, twice`; add `spot_cmp = superset.id_of("dataset", "table_name", "spot_price_forecast_comparison")` and `demand_cmp = ... "demand_forecast_comparison"`; `(spot_ds, spot_ex, spot_cmp, demand_ds, demand_ex, demand_cmp) == (10, 11, 12, 63, 64, 65)`; `len(superset.rows["chart"]) == 98`; `charts_of(superset, spot_cmp)` names `== SPOT_COMPARISON_CHART_NAMES`, `demand_cmp` `== DEMAND_COMPARISON_CHART_NAMES`; the loop's dataset triples `(spot_ds, spot_ex, spot_cmp)` / `(demand_ds, demand_ex, demand_cmp)`; unpack `run_filter, day_filter, baseline_filter`; add `assert baseline_filter["targets"][0]["datasetId"] == dataset_ids[0]`.
- `test_second_build_is_idempotent_and_rewrites_metadata`: dataset counts `{"GET": 3, "PUT": 3}`, chart `{"GET": 49, "PUT": 98}`; unpack three filters; `run_filter["scope"]["excluded"] == [29]`; add `assert baseline_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}` and `assert baseline_filter["controlValues"]["defaultToFirstItem"] is False`; `all(c["dashboards"] == [62] ...)`.
- Replace `test_worst_days_cross_filter_is_scoped_to_the_explanation_tab` with:

```python
    def test_day_tables_cross_filter_the_detail_charts_and_the_explanation_tab(
        self, script, superset, demand
    ):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand)
        (dashboard,) = superset.rows["dashboard"].values()
        configuration = json.loads(dashboard["json_metadata"])["chart_configuration"]
        worst_days = superset.id_of("chart", "slice_name", "Worst days")
        improved = superset.id_of("chart", "slice_name", "Most improved days")
        worsened = superset.id_of("chart", "slice_name", "Most worsened days")
        detail = superset.id_of("chart", "slice_name", "Forecast vs actual (30-min detail)")
        compare_detail = superset.id_of(
            "chart", "slice_name", "Candidate vs baseline vs actual (30-min detail)"
        )
        explanation = list(range(32, 39))
        assert (worst_days, improved, worsened, detail, compare_detail) == (30, 59, 60, 31, 61)
        assert list(configuration) == ["30", "59", "60"]
        assert configuration["30"]["crossFilters"]["chartsInScope"] == [
            detail,
            *explanation,
            compare_detail,
        ]
        assert configuration["30"]["crossFilters"]["scope"]["excluded"] == [
            i for i in range(13, 62) if i not in (detail, *explanation, compare_detail)
        ]
        for emitter in ("59", "60"):
            assert configuration[emitter]["crossFilters"]["chartsInScope"] == [
                compare_detail,
                *explanation,
            ]
            assert configuration[emitter]["crossFilters"]["scope"]["excluded"] == [
                i for i in range(13, 62) if i not in (compare_detail, *explanation)
            ]

    def test_baseline_run_override_reaches_the_baseline_filter(self, script, superset, demand):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand, baseline_run="ffff")
        (dashboard,) = superset.rows["dashboard"].values()
        _, _, baseline_filter = json.loads(dashboard["json_metadata"])["native_filter_configuration"]
        assert baseline_filter["defaultDataMask"]["filterState"]["value"] == [SHORT_LABEL]
```

Update `TestMain`: dataset name lists gain `"spot_price_forecast_comparison"` after the spot explanation and `"demand_forecast_comparison"` after the demand explanation; chart counts `52 → 98` (both dashboards) and `26 → 49` (one), `method_counts(... "chart") == {"GET": 98, "POST": 98, "PUT": 98}`; the demand-only chart names `EXPECTED_DEMAND_CHART_NAMES + EXPLANATION_CHART_NAMES + DEMAND_COMPARISON_CHART_NAMES`. Add:

```python
    def test_baseline_run_flag_is_passed_to_every_dashboard(self, script, superset, monkeypatch):
        run_main(
            script,
            superset,
            monkeypatch,
            ["--url", BASE, "--task", "demand", "--baseline-run", "ffffffff"],
        )
        (dashboard,) = superset.rows["dashboard"].values()
        _, _, baseline_filter = json.loads(dashboard["json_metadata"])["native_filter_configuration"]
        assert baseline_filter["defaultDataMask"]["filterState"]["value"] == [SHORT_LABEL]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q --no-cov -k "BuildDashboard or TestMain"`
Expected: FAIL (dataset count, chart names, `baseline_run` keyword).

- [ ] **Step 3: Implement**

`build_dashboard(client, database_id, spec, baseline_run: str | None = None)` — add to the docstring a `baseline_run : str, optional` entry ("``run_id`` or prefix for the Baseline filter's default; see ``run_defaults``"). After the explanation dataset:

```python
    comparison_id = upsert_dataset(
        client,
        database_id,
        spec.comparison_dataset_name,
        spec.comparison_dataset_sql,
        spec.comparison_dataset_columns,
    )
    logger.info("dataset {}: id={}", spec.comparison_dataset_name, comparison_id)
```

After the explanation charts (before `accuracy_sections`):

```python
    # Compare: the comparison dataset, filtered by Run (candidate) + Baseline
    signed = spec.signed_number_format
    cmp_base_mae = chart(
        "Baseline MAE",
        big_number_params(
            comparison_id, spec.baseline_mae_metric, f"{unit}; the Baseline run, matched periods", fmt
        ),
        comparison_id,
    )
    cmp_cand_mae = chart(
        "Candidate MAE",
        big_number_params(
            comparison_id, spec.candidate_mae_metric, f"{unit}; the Run, matched periods", fmt
        ),
        comparison_id,
    )
    cmp_delta_mae = chart(
        "ΔMAE vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_mae_metric, f"{unit}; − = candidate better", signed
        ),
        comparison_id,
    )
    cmp_delta_pct = chart(
        "ΔMAE % vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_mae_pct_metric, "%; − = candidate better", "+,.1f"
        ),
        comparison_id,
    )
    cmp_delta_bias = chart(
        "Δ|bias| vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_abs_bias_metric, f"{unit}; − = candidate less biased", signed
        ),
        comparison_id,
    )
    cmp_delta_wape = chart(
        "ΔWAPE vs baseline",
        delta_big_number_params(
            comparison_id, spec.delta_wape_metric, "− = candidate better", "+.2%"
        ),
        comparison_id,
    )
    cmp_coverage = chart(
        "Matched coverage",
        big_number_params(
            comparison_id,
            spec.matched_coverage_metric,
            "share of the candidate's periods the baseline also scored",
            ".1%",
        ),
        comparison_id,
    )
    cmp_days = chart(
        "Matched days",
        big_number_params(
            comparison_id, spec.matched_days_metric, "delivery days both runs scored", ",d"
        ),
        comparison_id,
    )
    cmp_days_lower = chart(
        "Days candidate lower",
        big_number_params(
            comparison_id,
            spec.days_candidate_lower_metric,
            "share of matched days with a lower daily MAE",
            ".1%",
        ),
        comparison_id,
    )
    cmp_median = chart(
        "Median daily ΔMAE",
        delta_big_number_params(
            comparison_id, spec.median_daily_delta_metric, f"{unit}; − = candidate better", signed
        ),
        comparison_id,
    )
    cmp_tc = chart("ΔMAE % by time code", delta_bar_params(spec, comparison_id, "time_code"), comparison_id)
    cmp_daypart = chart("ΔMAE % by day part", delta_bar_params(spec, comparison_id, "day_part"), comparison_id)
    cmp_daytype = chart("ΔMAE % by day type", delta_bar_params(spec, comparison_id, "day_type"), comparison_id)
    cmp_dow = chart(
        "ΔMAE % by day of week", delta_bar_params(spec, comparison_id, "day_of_week"), comparison_id
    )
    cmp_band = chart(
        spec.delta_band_chart_title, delta_bar_params(spec, comparison_id, spec.band_col), comparison_id
    )
    cmp_year = chart("ΔMAE % by year", delta_bar_params(spec, comparison_id, "year"), comparison_id)
    cmp_heat_month = chart(
        "ΔMAE % by year and month", delta_heatmap_params(spec, comparison_id, "month"), comparison_id
    )
    cmp_heat_tc = chart(
        "ΔMAE % by year and time code",
        delta_heatmap_params(spec, comparison_id, "time_code"),
        comparison_id,
    )
    cmp_daily = chart("Daily ΔMAE", daily_delta_bar_params(spec, comparison_id), comparison_id)
    cmp_cumulative = chart(
        "Cumulative error reduction", cumulative_reduction_params(spec, comparison_id), comparison_id
    )
    cmp_improved = chart(
        "Most improved days", ranked_days_params(spec, comparison_id, improved=True), comparison_id
    )
    cmp_worsened = chart(
        "Most worsened days", ranked_days_params(spec, comparison_id, improved=False), comparison_id
    )
    cmp_detail = chart(
        "Candidate vs baseline vs actual (30-min detail)",
        comparison_detail_params(spec, comparison_id),
        comparison_id,
    )
```

After `explanation_sections`:

```python
    compare_sections: list[dict[str, Any]] = [
        {
            "header": None,
            "rows": [
                [
                    (cmp_base_mae, "Baseline MAE", 2, 24),
                    (cmp_cand_mae, "Candidate MAE", 2, 24),
                    (cmp_delta_mae, "ΔMAE vs baseline", 2, 24),
                    (cmp_delta_pct, "ΔMAE % vs baseline", 2, 24),
                    (cmp_delta_bias, "Δ|bias| vs baseline", 2, 24),
                    (cmp_delta_wape, "ΔWAPE vs baseline", 2, 24),
                ],
                [
                    (cmp_coverage, "Matched coverage", 3, 24),
                    (cmp_days, "Matched days", 3, 24),
                    (cmp_days_lower, "Days candidate lower", 3, 24),
                    (cmp_median, "Median daily ΔMAE", 3, 24),
                ],
            ],
        },
        {
            "header": "Where the candidate wins (ΔMAE % vs baseline)",
            "rows": [
                [(cmp_tc, "ΔMAE % by time code", 12, 36)],
                [
                    (cmp_daypart, "ΔMAE % by day part", 4, 36),
                    (cmp_daytype, "ΔMAE % by day type", 4, 36),
                    (cmp_dow, "ΔMAE % by day of week", 4, 36),
                ],
                [
                    (cmp_band, spec.delta_band_chart_title, 6, 36),
                    (cmp_year, "ΔMAE % by year", 6, 36),
                ],
                [(cmp_heat_month, "ΔMAE % by year and month", 12, 46)],
                [(cmp_heat_tc, "ΔMAE % by year and time code", 12, 50)],
            ],
        },
        {
            "header": "Day by day",
            "rows": [
                [(cmp_daily, "Daily ΔMAE", 12, 44)],
                [(cmp_cumulative, "Cumulative error reduction", 12, 40)],
                [
                    (cmp_improved, "Most improved days", 6, 40),
                    (cmp_worsened, "Most worsened days", 6, 40),
                ],
            ],
        },
        {
            "header": "Detail",
            "rows": [[(cmp_detail, "Candidate vs baseline vs actual (30-min detail)", 12, 60)]],
        },
    ]
    tabs = [
        {"title": "Accuracy", "sections": accuracy_sections},
        {"title": "Explanation (SHAP)", "sections": explanation_sections},
        {"title": "Compare", "sections": compare_sections},
    ]
```

Add the chart list and wire everything:

```python
    comparison_charts = [
        cmp_base_mae, cmp_cand_mae, cmp_delta_mae, cmp_delta_pct, cmp_delta_bias, cmp_delta_wape,
        cmp_coverage, cmp_days, cmp_days_lower, cmp_median,
        cmp_tc, cmp_daypart, cmp_daytype, cmp_dow, cmp_band, cmp_year, cmp_heat_month, cmp_heat_tc,
        cmp_daily, cmp_cumulative, cmp_improved, cmp_worsened, cmp_detail,
    ]
    all_charts = [*analysis_charts, *explanation_charts, *comparison_charts]
    chart_configuration = build_chart_configuration(
        {
            worst_days: [detail, *explanation_charts, cmp_detail],
            cmp_improved: [cmp_detail, *explanation_charts],
            cmp_worsened: [cmp_detail, *explanation_charts],
        },
        all_charts,
    )

    defaults = run_defaults(client, database_id, spec, baseline_run)
    default_run = None if defaults is None else defaults.run_label
    default_day = None if defaults is None else defaults.last_day
    default_baseline = None if defaults is None else defaults.baseline_run_label
    logger.info(
        "defaults: run {} (last day {}), baseline {}", default_run, default_day, default_baseline
    )
    dashboard_id = upsert_dashboard(
        client,
        spec,
        build_position_json(spec, tabs),
        build_native_filters(
            dataset_id=dataset_id,
            run_excluded=[leaderboard],
            default_run_label=default_run,
            explanation_dataset_id=explanation_id,
            day_excluded=[*analysis_charts, *comparison_charts],
            default_day_label=default_day,
            baseline_excluded=[*analysis_charts, *explanation_charts],
            default_baseline_label=default_baseline,
        ),
        chart_configuration,
    )
```

(The ruff formatter will reflow the `comparison_charts` list one item per line.) Remove the old `cross_filter_targets` variable. In `main`:

```python
    parser.add_argument(
        "--baseline-run",
        default=None,
        help=(
            "run_id (or prefix) the Compare tab's Baseline filter opens on; default: the newest "
            "other run with the same area and window as the newest run"
        ),
    )
    ...
    for task in args.task or list(DASHBOARDS):
        build_dashboard(client, database_id, DASHBOARDS[task], baseline_run=args.baseline_run)
```

- [ ] **Step 4: Run the full check**

Run: `just test` then `just lint` then `just mypy`
Expected: all pass, coverage 100 % (a missed branch will be the `defaults is None` path — the idempotent test covers it — or a builder kwarg; add a test rather than a pragma).

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): Compare tab — candidate vs baseline by segment and by day

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Live rollout, verification and the before / after images

**Files:**
- Create: `docs/img/superset/demand-forecast-dashboard-compare.png` (after)
- Replace: `docs/img/superset/demand-forecast-dashboard.png` (before — the current dashboard, captured *before* the rebuild)

Runs from the main session (the stack is up; Superset already has the flag). Log in at `http://localhost:8088/login/` as `admin` / `admin` (the `.env` does not override the password).

- [ ] **Step 1: Before image.** With Playwright, open `http://localhost:8088/superset/dashboard/demand-forecast-analysis/` (viewport 1600 × 1000, wait for the charts), Accuracy tab, Run = the newest run, and save a full-page screenshot as `docs/img/superset/demand-forecast-dashboard.png`, overwriting the 2026-08-18 capture. This is the "one run at a time" state the PR fixes.

- [ ] **Step 2: Rebuild both dashboards.**

Run: `just python scripts/create_forecast_dashboard.py`
Expected: three datasets and 49 charts per dashboard logged; `defaults: run 2026-09-05 17:53 | tokyo | lightgbm_msm_popw_daytype_simday | 008868fe (last day 2026-08-17), baseline 2026-08-31 … | 88169a52` for demand (the matched-window rule picks the newest same-window run; the researcher chooses the R-003 baseline in the filter).

- [ ] **Step 3: Verify the numbers.** On the demand dashboard, Compare tab, set Baseline = `2026-08-26 13:37 | tokyo | lightgbm_msm_popw_daytype | 0a6b8a55` (Run stays `008868fe`). Check, against the probe of 2026-09-06:

| Tile / chart | Expected |
|---|---|
| Baseline MAE | 594.3 MWh |
| Candidate MAE | 585.4 MWh |
| ΔMAE vs baseline | −9.0 MWh, blue |
| ΔMAE % vs baseline | −1.5, blue |
| Matched coverage | 100.0% |
| Matched days | 729 |
| Days candidate lower | 53.4% |
| Median daily ΔMAE | −14.6 MWh, blue |
| ΔMAE % by day type | Weekday −4.8 (blue, below zero), Weekend +6.5 (orange), Holiday ≈ 0.0 |

Then: clear Baseline → every Compare chart shows "No data"; Baseline = `cd8f65c1…` (the 30-day run) → Matched coverage well below 100 %; click a row of Most improved days → the 30-minute detail and the Explanation tab follow that day; the spot-price dashboard's Compare tab renders with its newest two runs. If any chart errors, read the error through the chart's "View query" / the Superset log (`docker compose logs superset --tail 200`) and fix the builder; a Jinja rendering error names the dataset.

- [ ] **Step 4: After image.** Full-page screenshot of the Compare tab in the state of Step 3 (Run `008868fe`, Baseline `0a6b8a55`) as `docs/img/superset/demand-forecast-dashboard-compare.png`.

- [ ] **Step 5: Commit the images**

```bash
git add docs/img/superset/demand-forecast-dashboard.png docs/img/superset/demand-forecast-dashboard-compare.png
git commit -m "docs(dashboard): demand dashboard screenshots before and after the Compare tab

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Note the commit SHA: the PR body embeds both images by their raw URL at that SHA (`https://raw.githubusercontent.com/hankehly/power-market-analytics/<sha>/docs/img/superset/<file>`).

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/README.md`, `docs/research/demand/README.md`, `docs/research/spot_price/README.md`, `scripts/create_forecast_dashboard.py` (module docstring), `docs/superpowers/specs/2026-09-06-forecast-dashboard-compare-tab-design.md`

- [ ] **Step 1: `CLAUDE.md`.** In the `create_forecast_dashboard.py` bullet: change "and two top-level tabs" to "and three top-level tabs"; after the Explanation (SHAP) sentence that ends "so the chart's query B is unfiltered." insert:

> **Compare** — a third virtual dataset `<task>_forecast_comparison` (the accuracy mart self-joined on day × time code × area: the Run filter's run as the candidate against a **Baseline** native filter's run, both pinned inside the dataset SQL with Superset Jinja `filter_values()`, so `conf/superset/superset_config.py` sets `ENABLE_TEMPLATE_PROCESSING`; inner join, so only periods both runs scored count) drives delta tiles coloured by sign (ΔMAE, ΔMAE %, Δ|bias|, ΔWAPE; blue = candidate better, orange = worse), matched coverage / days / share of days lower / median daily ΔMAE, diverging Better / Worse bars of ΔMAE % by time code, day part, day type, day of week, actual band and year, ΔMAE % heatmaps (blue-white-yellow, ±30 %), daily ΔMAE bars, the cumulative error reduction, Most improved / Most worsened days tables (cross-filtering the detail and the Explanation tab) and a three-line 30-minute detail. The Baseline filter is scoped to that tab, reads its options from the analysis dataset's `baseline_run_label` alias, and opens on the newest other run with the same area and window as the newest run (`--baseline-run <run_id or prefix>` overrides); the bootstrap CI over days stays in `compare_<task>_runs.py`. Run labels are `published_at | area | strategy | run_id prefix` (`RUN_LABEL_SQL`, one definition); the leaderboard shows each run's first / last day and day count.

- [ ] **Step 2: `docs/README.md`.** In the Superset paragraph, after "(click a row to cross-filter the dashboard to that day), and a zoomable 30-minute forecast-vs-actual detail." add: "A **Compare** tab puts the run against a **Baseline** run chosen in a second filter: delta tiles, diverging ΔMAE % bars by segment, ΔMAE % heatmaps, daily ΔMAE, the cumulative error reduction, most-improved / most-worsened day tables and a three-line detail, over the periods both runs scored." After the demand dashboard image add:

```
![Demand Forecast Analysis dashboard — Compare tab](img/superset/demand-forecast-dashboard-compare.png)
```

- [ ] **Step 3: Research READMEs.** In `docs/research/demand/README.md` "Segments reported by the tooling", after "(Day filter))" append: "; its **Compare** tab shows a run against a Baseline run (matched periods only): ΔMAE by day part, day type, day of week, month, band and time code, the share of days lower, the median daily ΔMAE and the most improved / worsened days — the bootstrap CI stays in the compare script". Same sentence in `docs/research/spot_price/README.md` after "(Superset **Spot Price Forecast Analysis**)" with the spot segments (day part, day of week, month, price band, time code).

- [ ] **Step 4: Module docstring.** In `scripts/create_forecast_dashboard.py`'s docstring: "two virtual datasets" → "three virtual datasets" with a third dash item for `<task>_forecast_comparison` ("the accuracy mart self-joined, the Run filter's run against the Baseline filter's, both pinned in the SQL with Jinja"); "charts on two tabs" → "charts on three tabs" and a **Compare** item listing the sections; the filter sentence gains "plus a required single-select Baseline filter scoped to the Compare tab"; add `--baseline-run` to the usage lines.

- [ ] **Step 5: Spec amendments.** In the spec's §4 item 3, replace "Tooltips carry both MAEs through the rich tooltip." with "The tooltip shows the delta; the two MAE levels are on the tiles and the day tables." (a Superset bar chart cannot carry extra tooltip metrics without adding them as series). In §2 and §6, replace the `latest_run` three-tuple wording with: "`run_defaults()` runs one query listing the mart's runs (label, area, first / last day, period count, newest first) and applies the rule and the `--baseline-run` override in Python, returning a `RunDefaults` (run label, last day, baseline label or None)."

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/README.md docs/research/demand/README.md docs/research/spot_price/README.md scripts/create_forecast_dashboard.py docs/superpowers/specs/2026-09-06-forecast-dashboard-compare-tab-design.md
git commit -m "docs(dashboard): document the Compare tab, the Baseline filter and the run label

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Finish

- [ ] **Step 1:** `just test`, `just lint`, `just mypy`, `just checkov` — all green; `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse` unaffected (no dbt change) but run once.
- [ ] **Step 2:** Invoke `superpowers:finishing-a-development-branch`. Push the branch and open the PR with `gh pr create` — title `feat(dashboard): Compare tab for candidate-vs-baseline run comparison`; body sections *Why* (the two-browser-tabs pain), *What* (the dataset, filter, tab, label, leaderboard, flag), *Proof* (the numbers table of Task 10 Step 3, `just test` coverage line) and a **Before / after** section embedding the two images by raw URL at the Task 10 commit SHA:

```markdown
## Before / after

**Before** — one run at a time; comparing against a baseline meant two browser tabs:

![Before](https://raw.githubusercontent.com/hankehly/power-market-analytics/<sha>/docs/img/superset/demand-forecast-dashboard.png)

**After** — the Compare tab, Run `008868fe` (R-004 E-002) against Baseline `0a6b8a55` (R-003):

![After](https://raw.githubusercontent.com/hankehly/power-market-analytics/<sha>/docs/img/superset/demand-forecast-dashboard-compare.png)
```

Then `gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label documentation`, and run the review loop from `CLAUDE.md` (Codex automatically, then Copilot on request; never spell the Codex mention). Report the PR as ready; the researcher merges.
