# SHAP Explanation Dashboard Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the per-row TreeSHAP contributions the LightGBM strategies already compute, model them in dbt, and add an "Explanation (SHAP)" section with Day / Period filters and a waterfall chart to both Superset forecast-analysis dashboards.

**Architecture:** A new strategy output (`contributions()`, a long frame: one row per delivery period × component, base + features, summing to the forecast) is published next to the forecasts to `pma_ml.<task>_forecast_contribution` (parquet, partitioned by `run_id`) → `stg/std/fct_<task>_forecast_contribution` (dbt, contracts + additivity tests) → a second virtual dataset `<task>_forecast_explanation` per dashboard → seven charts in a new last section, driven by two native filters scoped to that section. No model or metric changes.

**Tech Stack:** Python 3.12 / pandas / PySpark / LightGBM / MLflow (framework), dbt 1.11 on the Spark thriftserver (models), Superset 6.1.0 REST API (`viz_type` `waterfall`, `echarts_timeseries_bar`, `table`, `big_number_total`), pytest with the repo's local-Spark fixtures and `FakeSupersetSession`.

**Spec:** `docs/superpowers/specs/2026-08-26-shap-explanation-dashboard-design.md`

## Global Constraints

- Every new Python module/function gets NumPy-style docstrings (`Parameters` / `Returns` / `Raises` with underlined headers).
- pandas frames cross function boundaries as `DomainFrame` wrappers built with `from_df`; merges state `how=`, `on=`, `validate=`.
- `just test` runs pytest with a **100 % coverage gate** (`fail_under`); every new line must be exercised. `just lint` (ruff) and `just mypy` must pass. A PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` you edit — re-read a file before editing it again.
- Every dbt model: `config: contract: enforced: true` with a `data_type` on every column, and a uniqueness test on its grain (`dbt_utils.unique_combination_of_columns`); test args under `arguments:`. Double arithmetic in Spark SQL must be written `cast(1 as double) / n`, never `1.0 / n`.
- Anything that creates a SparkSession against the real warehouse runs in the devcontainer (`just python …`, `just dbt …`); host-side dbt works with `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`. Long jobs run in the foreground with a generous timeout or as **main-session** background tasks — never backgrounded by a subagent.
- Component naming is fixed by the spec: `BASE_COMPONENT = "base"`, `component_order` 0 for the base and `i + 1` for `feature_cols[i]`; warehouse column names `contribution_demand_kwh` / `contribution_price_jpy_kwh`; tables `pma_ml.demand_forecast_contribution` / `pma_ml.spot_price_forecast_contribution`; datasets `demand_forecast_explanation` / `spot_price_forecast_explanation`; section title `Explanation (SHAP)`; chart names `Base value`, `Forecast (selection)`, `Actual (selection)`, `Net feature effect`, `SHAP waterfall`, `Feature values & contributions`, `Contributions by period`; filter ids `NATIVE_FILTER-day`, `NATIVE_FILTER-period`.
- Commit after every task (feature branch `shap-explanation-dashboard`, already created); commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/forecasting/frames.py` | + `BASE_COMPONENT`, `ForecastContributions` (strategy output), `ForecastContributionRecords` (write-back layout) |
| `power_market_analytics/forecasting/task.py` | + derived `contribution_table` / `contribution_col`, `forecast_` prefix check |
| `power_market_analytics/forecasting/strategy.py` | + `contributions()` default (`None`) |
| `power_market_analytics/forecasting/lgbm.py` | + `contributions()` melting `_shap_records` |
| `power_market_analytics/forecasting/publish.py` | + `build_contribution_records`, `publish_contribution_records`; DDL/write helpers shared with the forecast publisher |
| `scripts/demand_backtest.py`, `scripts/spot_price_backtest.py` | publish contributions after the forecasts, tag the run |
| `dbt/models/raw/ml.yml` | + two sources |
| `dbt/models/{staging,standardized,curated}/*_forecast_contribution.{sql,yml}` | stg / std / fct per task |
| `dbt/dbt_tests/assert_fct_<task>_forecast_contribution_*.sql` | one-base-per-period, sums-to-forecast |
| `scripts/create_forecast_dashboard.py` | explanation dataset template + spec fields, three chart builders, Day/Period filters, `latest_run`, new section |
| `tests/…` | one test module per touched module (listed per task) |
| `CLAUDE.md`, `docs/research/{demand,spot_price}/README.md` | documentation |

Task order matters: Tasks 1–6 (Python) → Task 7 (real runs, so the warehouse tables exist) → Tasks 8–9 (dbt) → Tasks 10–12 (dashboard) → Task 13 (optional cross-filter) → Task 14 (live check) → Task 15 (docs) → Task 16 (finish).

---

### Task 1: Contribution frames

**Files:**
- Modify: `power_market_analytics/forecasting/frames.py` (append after `ForecastRecords`, before `MetricByYearTimeCode`)
- Test: `tests/test_forecasting_frames.py`

**Interfaces:**
- Produces: `BASE_COMPONENT: str = "base"`; `ForecastContributions(DomainFrame)` with schema `trade_date datetime64[ns], time_code int64, component object, component_order int64, feature_value float64, contribution float64`, keys `["trade_date", "time_code", "component"]`; `ForecastContributionRecords(DomainFrame)` with schema `run_id object, strategy object, area_code object, forecast_issued_ts datetime64[ns], trade_date datetime64[ns], time_code int64, component object, component_order int64, feature_value float64, contribution float64, published_at datetime64[ns]`, keys `["run_id", "area_code", "trade_date", "time_code", "component"]`.

- [ ] **Step 1: Write the failing tests**

Add to the import block of `tests/test_forecasting_frames.py`: `BASE_COMPONENT`, `ForecastContributionRecords`, `ForecastContributions`. Append at the end of the file:

```python
def contributions_df() -> pd.DataFrame:
    """Two periods of D1, each a base row plus two feature rows."""
    rows = []
    for tc in (1, 2):
        rows.append(
            {"trade_date": D1, "time_code": tc, "component": "base", "component_order": 0,
             "feature_value": np.nan, "contribution": 10.0}
        )
        rows.append(
            {"trade_date": D1, "time_code": tc, "component": "time_code", "component_order": 1,
             "feature_value": float(tc), "contribution": 0.5}
        )
        rows.append(
            {"trade_date": D1, "time_code": tc, "component": "x", "component_order": 2,
             "feature_value": 3.0, "contribution": -0.25}
        )
    return pd.DataFrame(rows).astype({"time_code": "int64", "component_order": "int64"})


class TestForecastContributions:
    def test_grain_schema_and_base_constant(self):
        assert BASE_COMPONENT == "base"
        assert ForecastContributions.keys == ["trade_date", "time_code", "component"]
        assert list(ForecastContributions.schema) == [
            "trade_date", "time_code", "component", "component_order", "feature_value", "contribution",
        ]
        assert ForecastContributions.non_null_cols == ["component_order", "contribution"]
        assert len(ForecastContributions.from_df(contributions_df())) == 6

    def test_period_without_a_base_row_rejected(self):
        df = contributions_df()
        df = df[~((df["time_code"] == 2) & (df["component"] == "base"))]
        with pytest.raises(ValueError, match=r"1 period\(s\) without a 'base' row"):
            ForecastContributions.from_df(df)

    def test_component_order_zero_exactly_on_the_base(self):
        df = contributions_df()
        df.loc[df["component"] == "x", "component_order"] = 0
        with pytest.raises(ValueError, match="component_order must be 0 exactly on the base rows"):
            ForecastContributions.from_df(df)

    def test_feature_value_null_exactly_on_the_base(self):
        df = contributions_df()
        df.loc[(df["component"] == "x") & (df["time_code"] == 1), "feature_value"] = np.nan
        with pytest.raises(ValueError, match="feature_value must be null exactly on the base rows"):
            ForecastContributions.from_df(df)

    def test_null_contribution_rejected(self):
        df = contributions_df()
        df.loc[0, "contribution"] = np.nan
        with pytest.raises(ValueError, match="'contribution' has 1 null values"):
            ForecastContributions.from_df(df)


def contribution_records_df() -> pd.DataFrame:
    return contributions_df().assign(
        run_id="r",
        strategy="s",
        area_code="tokyo",
        forecast_issued_ts=pd.Timestamp("2023-12-31 09:30"),
        published_at=pd.Timestamp("2024-01-05 12:00"),
    )


class TestForecastContributionRecords:
    def test_grain_and_schema(self):
        assert ForecastContributionRecords.keys == [
            "run_id", "area_code", "trade_date", "time_code", "component",
        ]
        assert list(ForecastContributionRecords.schema) == [
            "run_id", "strategy", "area_code", "forecast_issued_ts", "trade_date", "time_code",
            "component", "component_order", "feature_value", "contribution", "published_at",
        ]
        assert ForecastContributionRecords.non_null_cols == [
            "strategy", "forecast_issued_ts", "component_order", "contribution", "published_at",
        ]
        records = ForecastContributionRecords.from_df(contribution_records_df())
        assert len(records) == 6
        assert list(records.df.columns) == list(ForecastContributionRecords.schema)

    def test_same_component_twice_in_a_period_rejected(self):
        df = contribution_records_df()
        df = pd.concat([df, df.iloc[[1]]], ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique"):
            ForecastContributionRecords.from_df(df)

    def test_null_strategy_rejected(self):
        df = contribution_records_df()
        df.loc[0, "strategy"] = None
        with pytest.raises(ValueError, match="'strategy' has 1 null values"):
            ForecastContributionRecords.from_df(df)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_frames.py -q -p no:cacheprovider --no-cov`
Expected: ImportError (`BASE_COMPONENT` not found).

- [ ] **Step 3: Implement the frames**

Insert into `power_market_analytics/forecasting/frames.py` between `ForecastRecords` and `MetricByYearTimeCode`:

```python
#: The base component of a forecast decomposition: the model's expected value
#: (TreeSHAP's intercept), to which the per-feature contributions add.
BASE_COMPONENT = "base"


class ForecastContributions(DomainFrame):
    """Additive per-component decomposition of every forecast a strategy has made.

    One row per delivery period and *component* — a model feature, or the
    base (:data:`BASE_COMPONENT`, the model's expected value). Per period the
    contributions sum to the forecast: ``base + Σ features = forecast``.
    ``component_order`` is 0 for the base and ``i + 1`` for the strategy's
    ``feature_cols[i]``, so consumers can keep the model's feature order.
    ``feature_value`` is the feature as the model saw it, null on the base
    row only (a prediction exists only when every feature does).
    Contributions are in the task's forecast unit.

    Grain: (trade_date, time_code, component).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "component": "object",
        "component_order": "int64",
        "feature_value": "float64",
        "contribution": "float64",
    }
    keys = ["trade_date", "time_code", "component"]
    non_null_cols = ["component_order", "contribution"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        is_base = df["component"] == BASE_COMPONENT
        # The grain is unique per component, so a period has at most one base
        # row: the base-row count is the number of periods that have one.
        n_periods = df.groupby(GRAIN_COLS).ngroups
        n_missing = n_periods - int(is_base.sum())
        if n_missing:
            raise ValueError(
                f"{cls.__name__}: {n_missing} period(s) without a {BASE_COMPONENT!r} row"
            )
        if (is_base != (df["component_order"] == 0)).any():
            raise ValueError(
                f"{cls.__name__}: component_order must be 0 exactly on the base rows"
            )
        if (is_base != df["feature_value"].isna()).any():
            raise ValueError(
                f"{cls.__name__}: feature_value must be null exactly on the base rows"
            )


class ForecastContributionRecords(DomainFrame):
    """One backtest run's forecast contributions shaped for the task's
    contribution write-back table.

    Grain: (run_id, area_code, trade_date, time_code, component). The
    contribution column keeps its generic name here; the publisher writes it
    under the task's unit-suffixed ``TaskSpec.contribution_col``.
    """

    schema = {
        "run_id": "object",
        "strategy": "object",
        "area_code": "object",
        "forecast_issued_ts": "datetime64[ns]",
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "component": "object",
        "component_order": "int64",
        "feature_value": "float64",
        "contribution": "float64",
        "published_at": "datetime64[ns]",
    }
    keys = ["run_id", "area_code", "trade_date", "time_code", "component"]
    non_null_cols = ["strategy", "forecast_issued_ts", "component_order", "contribution", "published_at"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_frames.py -q -p no:cacheprovider --no-cov`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/frames.py tests/test_forecasting_frames.py
git commit -m "forecasting: add ForecastContributions / ForecastContributionRecords frames

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: TaskSpec derived contribution names

**Files:**
- Modify: `power_market_analytics/forecasting/task.py`
- Test: `tests/test_forecasting_task.py`, `tests/test_demand_task.py`, `tests/test_spot_price_task.py`

**Interfaces:**
- Produces: `TaskSpec.contribution_table -> str` (`forecast_table + "_contribution"`), `TaskSpec.contribution_col -> str` (`forecast_col` with `forecast_` → `contribution_`); `__post_init__` raises `ValueError` when `forecast_col` lacks the `forecast_` prefix.

- [ ] **Step 1: Write the failing tests**

Append to `class TestTaskSpec` in `tests/test_forecasting_task.py`:

```python
    def test_contribution_table_and_column_derive_from_the_forecast_ones(self):
        spec = make_spec()
        assert spec.contribution_table == "pma_ml.load_forecast_contribution"
        assert spec.contribution_col == "contribution_load_mw"

    def test_forecast_column_must_carry_the_forecast_prefix(self):
        class BareForecast(DayAheadForecast):
            forecast_col = "yhat_load_mw"

        class BareResult(BacktestResult):
            actual_col = "actual_load_mw"
            forecast_col = "yhat_load_mw"

        class BareRecords(ForecastRecords):
            forecast_col = "yhat_load_mw"

        with pytest.raises(
            ValueError, match="load: forecast column 'yhat_load_mw' must start with 'forecast_'"
        ):
            make_spec(forecast_cls=BareForecast, result_cls=BareResult, records_cls=BareRecords)
```

Append to `class TestDemandTask` in `tests/test_demand_task.py`:

```python
    def test_contribution_names(self):
        assert TASK.contribution_table == "pma_ml.demand_forecast_contribution"
        assert TASK.contribution_col == "contribution_demand_kwh"
```

Append to `class TestSpotPriceTask` in `tests/test_spot_price_task.py`:

```python
    def test_contribution_names(self):
        assert TASK.contribution_table == "pma_ml.spot_price_forecast_contribution"
        assert TASK.contribution_col == "contribution_price_jpy_kwh"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_task.py tests/test_demand_task.py tests/test_spot_price_task.py -q -p no:cacheprovider --no-cov`
Expected: 4 failures (`AttributeError: contribution_table`, and the prefix test not raising).

- [ ] **Step 3: Implement**

In `power_market_analytics/forecasting/task.py`, extend `__post_init__` (after the existing forecast-column agreement check) and add two properties after `actual_col`:

```python
        if not self.forecast_col.startswith("forecast_"):
            raise ValueError(
                f"{self.name}: forecast column {self.forecast_col!r} must start with "
                "'forecast_' (the contribution column is derived from it)"
            )
```

```python
    @property
    def contribution_table(self) -> str:
        """Warehouse table the run's forecast contributions are published to.

        ``forecast_table`` with a ``_contribution`` suffix, e.g.
        ``pma_ml.demand_forecast_contribution``.
        """
        return f"{self.forecast_table}_contribution"

    @property
    def contribution_col(self) -> str:
        """Warehouse column of a component's contribution to the forecast.

        ``forecast_col`` with its ``forecast_`` prefix swapped for
        ``contribution_``, e.g. ``contribution_demand_kwh`` — same unit as the
        forecast.
        """
        return "contribution_" + self.forecast_col.removeprefix("forecast_")
```

Also extend the class docstring's attribute list with one line: "The contribution table and column are derived (`contribution_table`, `contribution_col`) rather than stored."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_task.py tests/test_demand_task.py tests/test_spot_price_task.py -q -p no:cacheprovider --no-cov`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/task.py tests/test_forecasting_task.py tests/test_demand_task.py tests/test_spot_price_task.py
git commit -m "forecasting: derive the contribution table and column from the TaskSpec

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `contributions()` on the strategy interface

**Files:**
- Modify: `power_market_analytics/forecasting/strategy.py`
- Test: `tests/test_forecasting_strategy.py`

**Interfaces:**
- Produces: `ForecastStrategy.contributions(self) -> ForecastContributions | None`, concrete, default `None`.

- [ ] **Step 1: Write the failing test**

Append to `class TestForecastStrategy` in `tests/test_forecasting_strategy.py`:

```python
    def test_contributions_default_to_none(self):
        class Done(ForecastStrategy):
            name = "done"

            def predict(self, target_date, history):
                raise NotImplementedError

            def build_eval_set(self, history, start_date, end_date, run=None):
                raise NotImplementedError

            def evaluate(self, eval_set, **kwargs):
                raise NotImplementedError

        assert Done().contributions() is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_forecasting_strategy.py -q -p no:cacheprovider --no-cov`
Expected: FAIL with `AttributeError: 'Done' object has no attribute 'contributions'`.

- [ ] **Step 3: Implement**

In `power_market_analytics/forecasting/strategy.py`, import `ForecastContributions` from `power_market_analytics.forecasting.frames` (extend the existing import) and add after `evaluate`:

```python
    def contributions(self) -> ForecastContributions | None:
        """Additive per-component decomposition of every forecast made so far.

        Optional. A strategy that can attribute its forecasts to its inputs
        (a LightGBM model, via TreeSHAP) returns one row per predicted
        delivery period and component — the base plus each feature — summing
        to that period's forecast. The default, for strategies with nothing
        to attribute (a naive rule), is ``None``: the backtest scripts then
        publish no contributions for the run.

        Returns
        -------
        ForecastContributions or None
        """
        return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_forecasting_strategy.py -q -p no:cacheprovider --no-cov`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/strategy.py tests/test_forecasting_strategy.py
git commit -m "forecasting: optional contributions() on the strategy interface

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `SlidingWindowLightGbmStrategy.contributions()`

**Files:**
- Modify: `power_market_analytics/forecasting/lgbm.py` (imports; new method after `build_eval_set`)
- Test: `tests/test_forecasting_lgbm.py` (the guard), `tests/test_demand_lgbm.py` (the melt, through the real demand strategy)

**Interfaces:**
- Consumes: `_shap_records: dict[Timestamp, DataFrame]` (columns `trade_date, time_code, <features except time_code>, shap_<feature>…, shap_expected_value`), `feature_cols`, `shap_cols`; `ForecastContributions`, `BASE_COMPONENT` (Task 1).
- Produces: `contributions() -> ForecastContributions`, rows sorted by `(trade_date, time_code, component_order)`; raises `RuntimeError` before any prediction.

- [ ] **Step 1: Write the failing tests**

Append to `class TestSlidingWindowLightGbmStrategy` in `tests/test_forecasting_lgbm.py`:

```python
    def test_contributions_need_a_prediction_first(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _add_features(self, featured, history_df):
                return featured

        with pytest.raises(RuntimeError, match="m: no recorded contributions; run the backtest first"):
            Minimal().contributions()
```

In `tests/test_demand_lgbm.py` add `from power_market_analytics.forecasting.frames import BASE_COMPONENT, ForecastContributions` to the imports and append to `class TestPredict`:

```python
    def test_contributions_melt_the_records_into_one_row_per_component(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        forecast = strategy.predict(D, visible(demand, D))

        contributions = strategy.contributions()

        assert isinstance(contributions, ForecastContributions)
        df = contributions.df
        assert len(df) == 48 * (1 + len(FEATURE_COLS))
        assert df["trade_date"].eq(D).all()
        # Sorted by period, then the base and the features in feature order.
        first_period = df[df["time_code"] == 1]
        assert first_period["component"].tolist() == [BASE_COMPONENT, *FEATURE_COLS]
        assert first_period["component_order"].tolist() == list(range(len(FEATURE_COLS) + 1))
        record = strategy._shap_records[pd.Timestamp(D).as_unit("ns")]
        # Feature values are the recorded features, time_code (a key column) included.
        assert df.loc[df["component"] == "time_code", "feature_value"].tolist() == [
            float(tc) for tc in range(1, 49)
        ]
        np.testing.assert_allclose(
            df.loc[df["component"] == DEMAND_LAG_FEATURE, "feature_value"].to_numpy(),
            record[DEMAND_LAG_FEATURE].to_numpy(),
        )
        base = df[df["component"] == BASE_COMPONENT]
        assert base["feature_value"].isna().all()
        np.testing.assert_allclose(
            base["contribution"].to_numpy(), record["shap_expected_value"].to_numpy()
        )
        # Additivity: per period the components sum to the forecast.
        np.testing.assert_allclose(
            df.groupby("time_code")["contribution"].sum().to_numpy(),
            forecast.df["forecast_demand_kwh"].to_numpy(),
            atol=1e-3,
        )

    def test_contributions_cover_every_predicted_day_once(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        next_day = D + pd.Timedelta(days=1)
        strategy.predict(D, visible(demand, D))
        strategy.predict(next_day, visible(demand, next_day))
        strategy.predict(D, visible(demand, D))  # a re-predicted day overwrites its record

        df = strategy.contributions().df

        assert sorted(df["trade_date"].unique()) == [D, next_day]
        assert df.groupby("trade_date").size().eq(48 * (1 + len(FEATURE_COLS))).all()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_lgbm.py tests/test_demand_lgbm.py -q -p no:cacheprovider --no-cov -k contributions`
Expected: 3 failures with `AttributeError: … has no attribute 'contributions'`.

- [ ] **Step 3: Implement**

In `power_market_analytics/forecasting/lgbm.py` extend the frames import to `GRAIN_COLS, N_PERIODS, BASE_COMPONENT, DayAheadForecast, ForecastContributions, HalfHourlySeries` and add after `build_eval_set`:

```python
    def contributions(self) -> ForecastContributions:
        """The TreeSHAP decomposition of every forecast made so far.

        Melts the per-day records of :meth:`predict` into one row per period
        and component: the base (``shap_expected_value``, order 0, no feature
        value) and each feature in ``feature_cols`` order (its value as the
        model saw it and its ``shap_<feature>`` contribution). Per period the
        rows sum to the published forecast — LightGBM's ``pred_contrib`` is
        exact.

        Returns
        -------
        ForecastContributions
            Sorted by period, then ``component_order``.

        Raises
        ------
        RuntimeError
            If no day has been predicted yet.
        """
        if not self._shap_records:
            raise RuntimeError(f"{self.name}: no recorded contributions; run the backtest first")
        pooled = pd.concat(self._shap_records.values(), ignore_index=True)
        parts = [
            pooled[GRAIN_COLS].assign(
                component=BASE_COMPONENT,
                component_order=0,
                feature_value=np.nan,
                contribution=pooled["shap_expected_value"],
            )
        ]
        for order, feature in enumerate(self.feature_cols, start=1):
            parts.append(
                pooled[GRAIN_COLS].assign(
                    component=feature,
                    component_order=order,
                    feature_value=pooled[feature].astype("float64"),
                    contribution=pooled[f"shap_{feature}"],
                )
            )
        melted = (
            pd.concat(parts, ignore_index=True)
            .astype({"component_order": "int64"})
            .sort_values([*GRAIN_COLS, "component_order"], ignore_index=True)
        )
        return ForecastContributions.from_df(melted)
```

Update the module docstring's list ("TreeSHAP recording per forecast day") to mention "and their melt into `contributions()` for the warehouse".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_lgbm.py tests/test_demand_lgbm.py -q -p no:cacheprovider --no-cov`
Expected: all pass (the demand module takes ~1 min: real LightGBM fits).

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/lgbm.py tests/test_forecasting_lgbm.py tests/test_demand_lgbm.py
git commit -m "forecasting: melt the recorded TreeSHAP values into contributions()

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Contribution records builder and publisher

**Files:**
- Modify: `power_market_analytics/forecasting/publish.py`
- Test: `tests/test_forecasting_publish.py`

**Interfaces:**
- Consumes: `ForecastContributions`, `ForecastContributionRecords`, `BASE_COMPONENT`, `GRAIN_COLS` (frames); `TaskSpec.contribution_table` / `contribution_col` / `issue_offset` (Task 2).
- Produces: `build_contribution_records(task, contributions, result, *, run_id, strategy, area_code, published_at) -> ForecastContributionRecords`; `publish_contribution_records(task, records, spark=None) -> int`; private helpers `_create_run_partitioned_table(spark, table, columns_ddl)` and `_overwrite_run_partitions(spark, table, sdf)` also used by `publish_forecast_records` (behaviour unchanged).

- [ ] **Step 1: Write the failing tests**

In `tests/test_forecasting_publish.py` add `import numpy as np`, `import pytest`, and extend the imports:

```python
from power_market_analytics.forecasting.frames import (
    BASE_COMPONENT,
    ForecastContributionRecords,
    ForecastContributions,
)
from power_market_analytics.forecasting.publish import (
    build_contribution_records,
    build_forecast_records,
    publish_contribution_records,
    publish_forecast_records,
)
```

Append at the end of the file:

```python
CONTRIBUTION_TABLE = TASK.contribution_table  # pma_ml.spot_price_forecast_contribution
PUBLISHED_AT = pd.Timestamp("2026-08-26 10:00:00")


def make_contributions(days: list[str], time_codes: list[int], base: float = 10.0) -> ForecastContributions:
    """Contributions matching ``make_result``: the base plus one feature contributing tc / 10."""
    rows = []
    for day in days:
        for tc in time_codes:
            rows.append(
                {"trade_date": pd.Timestamp(day), "time_code": tc, "component": BASE_COMPONENT,
                 "component_order": 0, "feature_value": np.nan, "contribution": base}
            )
            rows.append(
                {"trade_date": pd.Timestamp(day), "time_code": tc, "component": "time_code",
                 "component_order": 1, "feature_value": float(tc), "contribution": tc / 10}
            )
    return ForecastContributions.from_df(
        pd.DataFrame(rows).astype({"time_code": "int64", "component_order": "int64"})
    )


class TestBuildContributionRecords:
    def test_keeps_the_scored_periods_and_stamps_the_run(self):
        result = make_result(["2024-04-10"], [1, 2])
        # More predicted periods than scored ones: only the scored survive.
        contributions = make_contributions(["2024-04-10", "2024-04-11"], [1, 2, 3])

        records = build_contribution_records(
            TASK, contributions, result, run_id="run-123", strategy="lightgbm",
            area_code="tokyo", published_at=PUBLISHED_AT,
        )

        assert isinstance(records, ForecastContributionRecords)
        assert list(records.df.columns) == list(ForecastContributionRecords.schema)
        assert len(records) == 4  # 2 periods x (base + 1 feature)
        assert set(zip(records.df["trade_date"], records.df["time_code"])) == {
            (pd.Timestamp("2024-04-10"), 1),
            (pd.Timestamp("2024-04-10"), 2),
        }
        assert records.df["run_id"].eq("run-123").all()
        assert records.df["strategy"].eq("lightgbm").all()
        assert records.df["area_code"].eq("tokyo").all()
        assert records.df["forecast_issued_ts"].eq(pd.Timestamp("2024-04-09 09:55")).all()
        assert records.df["published_at"].eq(PUBLISHED_AT).all()
        assert records.df["published_at"].dtype == "datetime64[ns]"
        sums = records.df.groupby("time_code")["contribution"].sum()
        assert sums.to_dict() == pytest.approx({1: 10.1, 2: 10.2})

    def test_a_scored_period_without_contributions_is_an_error(self):
        result = make_result(["2024-04-10"], [1, 2, 48])
        contributions = make_contributions(["2024-04-10"], [1, 2])
        with pytest.raises(
            ValueError,
            match=r"spot_price: 1 scored period\(s\) have no contributions, "
            r"e\.g\. 2024-04-10 time_code 48",
        ):
            build_contribution_records(
                TASK, contributions, result, run_id="r", strategy="s", area_code="tokyo",
                published_at=PUBLISHED_AT,
            )


def published_contribution_rows(spark, run_id: str) -> pd.DataFrame:
    """Rows of ``CONTRIBUTION_TABLE`` for one run, timestamps rendered in the session tz."""
    return (
        spark.sql(
            f"""
            select
              strategy, area_code,
              date_format(forecast_issued_ts, 'yyyy-MM-dd HH:mm') as forecast_issued_ts,
              cast(trade_date as string) as trade_date,
              time_code, component, component_order, feature_value,
              contribution_price_jpy_kwh,
              date_format(published_at, 'yyyy-MM-dd HH:mm:ss') as published_at,
              run_id
            from {CONTRIBUTION_TABLE}
            where run_id = '{run_id}'
            order by trade_date, time_code, component_order
            """
        )
        .toPandas()
        .reset_index(drop=True)
    )


def contribution_records(days, time_codes, *, run_id, base=10.0, strategy="lightgbm"):
    return build_contribution_records(
        TASK,
        make_contributions(days, time_codes, base=base),
        make_result(days, time_codes, base=base),
        run_id=run_id,
        strategy=strategy,
        area_code="tokyo",
        published_at=PUBLISHED_AT,
    )


class TestPublishContributionRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        records = contribution_records(["2024-04-10"], [1, 2], run_id="contrib-create")

        assert publish_contribution_records(TASK, records, spark=spark) == 4

        assert spark.catalog.tableExists(CONTRIBUTION_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(CONTRIBUTION_TABLE)}
        assert {name: c.dataType for name, c in columns.items()} == {
            "strategy": "string",
            "area_code": "string",
            "forecast_issued_ts": "timestamp",
            "trade_date": "date",
            "time_code": "int",
            "component": "string",
            "component_order": "int",
            "feature_value": "double",
            "contribution_price_jpy_kwh": "double",
            "published_at": "timestamp",
            "run_id": "string",
        }
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        rows = published_contribution_rows(spark, "contrib-create")
        assert rows[["trade_date", "time_code", "component", "component_order"]].values.tolist() == [
            ["2024-04-10", 1, "base", 0],
            ["2024-04-10", 1, "time_code", 1],
            ["2024-04-10", 2, "base", 0],
            ["2024-04-10", 2, "time_code", 1],
        ]
        assert rows["contribution_price_jpy_kwh"].tolist() == [10.0, 0.1, 10.0, 0.2]
        assert rows["feature_value"].tolist()[1::2] == [1.0, 2.0]
        # The base row's feature value is a SQL null, not a NaN double.
        n_null = spark.sql(
            f"select count(*) as n from {CONTRIBUTION_TABLE} "
            "where run_id = 'contrib-create' and feature_value is null"
        ).collect()[0]["n"]
        assert n_null == 2
        assert rows["forecast_issued_ts"].eq("2024-04-09 09:55").all()
        assert rows["published_at"].eq("2026-08-26 10:00:00").all()
        assert rows["strategy"].eq("lightgbm").all()
        assert rows["area_code"].eq("tokyo").all()

    def test_republishing_a_run_replaces_only_that_runs_partition(self, spark):
        keep = contribution_records(["2024-04-10"], [1], run_id="contrib-keep", base=20.0)
        first = contribution_records(["2024-04-10", "2024-04-11"], [1], run_id="contrib-replace")
        publish_contribution_records(TASK, keep, spark=spark)
        assert publish_contribution_records(TASK, first, spark=spark) == 4

        second = contribution_records(["2024-04-12"], [1], run_id="contrib-replace", base=30.0)
        assert publish_contribution_records(TASK, second, spark=spark) == 2

        replaced = published_contribution_rows(spark, "contrib-replace")
        assert replaced[["trade_date", "component", "contribution_price_jpy_kwh"]].values.tolist() == [
            ["2024-04-12", "base", 30.0],
            ["2024-04-12", "time_code", 0.1],
        ]
        kept = published_contribution_rows(spark, "contrib-keep")
        assert kept["contribution_price_jpy_kwh"].tolist() == [20.0, 0.1]

    def test_defaults_to_the_active_spark_session(self, spark):
        records = contribution_records(["2024-04-10"], [7], run_id="contrib-default-session")
        assert publish_contribution_records(TASK, records) == 2
        assert published_contribution_rows(spark, "contrib-default-session")["time_code"].tolist() == [7, 7]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_publish.py -q -p no:cacheprovider --no-cov`
Expected: ImportError (`build_contribution_records`).

- [ ] **Step 3: Implement**

Rewrite `power_market_analytics/forecasting/publish.py` so that the forecast publisher uses two private helpers and the contribution pair is added. Imports become:

```python
import pandas as pd
from loguru import logger
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from power_market_analytics.forecasting.frames import (
    GRAIN_COLS,
    BacktestResult,
    ForecastContributionRecords,
    ForecastContributions,
    ForecastRecords,
)
from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.spark import get_spark_session
```

Helpers (module level, before the public functions):

```python
def _create_run_partitioned_table(spark: SparkSession, table: str, columns_ddl: str) -> None:
    """Create ``table`` (parquet, partitioned by ``run_id``) and its database if absent.

    Explicit DDL rather than ``saveAsTable`` schema inference, so the table
    schema is stable across writers; the partition column comes last to line
    up with ``insertInto``'s positional semantics.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession
    table : str
        Fully qualified ``database.table``.
    columns_ddl : str
        Comma-separated ``name type`` list of the non-partition columns.
    """
    database = table.split(".")[0]
    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
          {columns_ddl},
          run_id string
        )
        USING parquet
        PARTITIONED BY (run_id)
        """
    )


def _overwrite_run_partitions(spark: SparkSession, table: str, sdf: DataFrame) -> None:
    """Insert ``sdf`` into ``table``, replacing only the partitions it carries.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession
    table : str
    sdf : pyspark.sql.DataFrame
        Columns in the table's positional order, ``run_id`` last.
    """
    # "dynamic" scopes the overwrite to the partitions being written; the
    # default ("static") would truncate every other run's partition too.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    sdf.write.mode("overwrite").insertInto(table)
```

`publish_forecast_records` keeps its signature/docstring; its body becomes:

```python
    spark = spark if spark is not None else get_spark_session()
    table = task.forecast_table
    _create_run_partitioned_table(
        spark,
        table,
        f"""strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          {task.forecast_col} double,
          published_at timestamp""",
    )
    sdf = spark.createDataFrame(records.df).select(
        F.col("strategy").cast("string"),
        F.col("area_code").cast("string"),
        F.col("forecast_issued_ts").cast("timestamp"),
        F.col("trade_date").cast("date"),
        F.col("time_code").cast("int"),
        F.col(task.forecast_col).cast("double"),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    _overwrite_run_partitions(spark, table, sdf)
    logger.info(
        "Published {} rows to {} (run_id={})", len(records), table, records.df["run_id"].iloc[0]
    )
    return len(records)
```

New public functions:

```python
def build_contribution_records(
    task: TaskSpec,
    contributions: ForecastContributions,
    result: BacktestResult,
    *,
    run_id: str,
    strategy: str,
    area_code: str,
    published_at: pd.Timestamp,
) -> ForecastContributionRecords:
    """Shape a strategy's contributions into warehouse write-back records.

    Keeps the periods of ``result`` only: the backtest drops forecast points
    without an actual and the forecast table holds just the remaining rows,
    so the contribution table stays congruent with it (one forecast row per
    explained period, joinable 1:1). Stamps the run identity like
    :func:`build_forecast_records`.

    Parameters
    ----------
    task : TaskSpec
        Task the contributions belong to; supplies the issue offset.
    contributions : ForecastContributions
        Every period the strategy predicted (``strategy.contributions()``).
    result : BacktestResult
        The backtest result being published; fixes the periods to keep.
    run_id, strategy, area_code : str
        As for :func:`build_forecast_records`.
    published_at : pandas.Timestamp
        The instant stamped on the run's forecast records (naive JST), reused
        so both tables label the run identically.

    Returns
    -------
    ForecastContributionRecords

    Raises
    ------
    ValueError
        If a period of ``result`` has no contributions.
    """
    aligned = result.df[GRAIN_COLS].merge(
        contributions.df, how="left", on=GRAIN_COLS, validate="one_to_many", indicator=True
    )
    missing = aligned.loc[aligned["_merge"] == "left_only", GRAIN_COLS]
    if not missing.empty:
        first = missing.iloc[0]
        raise ValueError(
            f"{task.name}: {len(missing)} scored period(s) have no contributions, e.g. "
            f"{first['trade_date'].date()} time_code {first['time_code']}"
        )
    df = (
        aligned.drop(columns="_merge")
        .assign(
            run_id=run_id,
            strategy=strategy,
            area_code=area_code,
            forecast_issued_ts=lambda d: d["trade_date"] + task.issue_offset,
            published_at=pd.Timestamp(published_at),
        )
        .astype({"published_at": "datetime64[ns]"})
    )
    return ForecastContributionRecords.from_df(df)


def publish_contribution_records(
    task: TaskSpec, records: ForecastContributionRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's forecast contributions to ``task.contribution_table``.

    Same mechanics as :func:`publish_forecast_records`: the table (parquet,
    partitioned by ``run_id``) is created on first use and only the run's
    partition is overwritten. The generic ``contribution`` column is written
    as the task's unit-suffixed ``contribution_col``; a NaN feature value (the
    base rows) is written as a SQL null.

    Parameters
    ----------
    task : TaskSpec
    records : ForecastContributionRecords
        Validated records for a single run.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.spark.get_spark_session`.

    Returns
    -------
    int
        Number of rows written.
    """
    spark = spark if spark is not None else get_spark_session()
    table = task.contribution_table
    _create_run_partitioned_table(
        spark,
        table,
        f"""strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          component string,
          component_order int,
          feature_value double,
          {task.contribution_col} double,
          published_at timestamp""",
    )
    feature_value = F.col("feature_value").cast("double")
    sdf = spark.createDataFrame(records.df).select(
        F.col("strategy").cast("string"),
        F.col("area_code").cast("string"),
        F.col("forecast_issued_ts").cast("timestamp"),
        F.col("trade_date").cast("date"),
        F.col("time_code").cast("int"),
        F.col("component").cast("string"),
        F.col("component_order").cast("int"),
        # pandas NaN arrives as a double NaN, which is not SQL null.
        F.when(F.isnan(feature_value), F.lit(None)).otherwise(feature_value).alias("feature_value"),
        F.col("contribution").cast("double").alias(task.contribution_col),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    _overwrite_run_partitions(spark, table, sdf)
    logger.info(
        "Published {} contribution rows to {} (run_id={})",
        len(records),
        table,
        records.df["run_id"].iloc[0],
    )
    return len(records)
```

Update the module docstring: "Each task has two destination tables — the forecasts (`TaskSpec.forecast_table`) and, for strategies that explain themselves, their per-component contributions (`TaskSpec.contribution_table`) — both partitioned by `run_id` …".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_publish.py -q -p no:cacheprovider --no-cov`
Expected: all pass (existing forecast tests included — the refactor must not change them).

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/publish.py tests/test_forecasting_publish.py
git commit -m "forecasting: publish contribution records to pma_ml.<task>_forecast_contribution

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: The backtest scripts publish contributions

**Files:**
- Modify: `scripts/demand_backtest.py`, `scripts/spot_price_backtest.py`
- Test: `tests/test_demand_scripts.py`, `tests/test_spot_price_scripts.py`

**Interfaces:**
- Consumes: `strategy.contributions()` (Tasks 3–4), `build_contribution_records` / `publish_contribution_records` (Task 5), `TASK.contribution_table` (Task 2).
- Produces: rows in `pma_ml.<task>_forecast_contribution` and the MLflow tag `contribution_table` for every run whose strategy explains itself; nothing (and no tag) otherwise.

- [ ] **Step 1: Write the failing tests**

`tests/test_demand_scripts.py` — add `import numpy as np` and, next to `FORECAST_TABLE`, `CONTRIBUTION_TABLE = "pma_ml.demand_forecast_contribution"` plus a helper after `published_rows`:

```python
def published_contribution_rows(spark, run_id: str) -> pd.DataFrame:
    return (
        spark.table(CONTRIBUTION_TABLE)
        .filter(F.col("run_id") == run_id)
        .toPandas()
        .sort_values(["trade_date", "time_code", "component_order"], ignore_index=True)
    )
```

Append to `test_lightgbm_over_a_pinned_window` (after the `forecast_issued_ts` assertion):

```python
        # The run's TreeSHAP decomposition lands next to the forecasts.
        assert run.data.tags["contribution_table"] == CONTRIBUTION_TABLE
        contributions = published_contribution_rows(spark, run.info.run_id)
        assert list(contributions.columns) == [
            "strategy",
            "area_code",
            "forecast_issued_ts",
            "trade_date",
            "time_code",
            "component",
            "component_order",
            "feature_value",
            "contribution_demand_kwh",
            "published_at",
            "run_id",
        ]
        assert len(contributions) == 144 * 6  # base + the 5 lightgbm features per scored period
        assert set(contributions["component"]) == {
            "base",
            "time_code",
            "month",
            "day_of_week",
            "wavg_temperature_c",
            "lag_7d_demand_kwh",
        }
        assert contributions["published_at"].nunique() == 1
        assert contributions["published_at"].iloc[0] == published["published_at"].iloc[0]
        assert contributions.loc[contributions["component"] == "base", "feature_value"].isna().all()
        # Per period the components sum to the published forecast.
        sums = contributions.groupby(["trade_date", "time_code"])["contribution_demand_kwh"].sum()
        forecasts = published.set_index(["trade_date", "time_code"])["forecast_demand_kwh"]
        np.testing.assert_allclose(
            sums.reindex(forecasts.index).to_numpy(), forecasts.to_numpy(), rtol=1e-9, atol=1e-3
        )
```

Append to `test_hole_day_is_partly_scored_and_its_d7_successor_skipped`:

```python
        # Contributions exist for exactly the scored periods (the hole day's 10, no skipped day).
        contributions = published_contribution_rows(spark, run.info.run_id)
        assert contributions.groupby(["trade_date", "time_code"]).ngroups == len(published)
```

`tests/test_spot_price_scripts.py` — add `CONTRIBUTION_TABLE = "pma_ml.spot_price_forecast_contribution"` next to `FORECAST_TABLE`, the same `published_contribution_rows` helper (reading `CONTRIBUTION_TABLE`), and:

In `test_previous_day_over_a_pinned_window`, after the `warehouse_table` tag assertion:

```python
        # A naive rule has nothing to attribute: no contributions, no tag.
        assert "contribution_table" not in run.data.tags
        if spark.catalog.tableExists(CONTRIBUTION_TABLE):
            assert published_contribution_rows(spark, run.info.run_id).empty
```

In `test_lightgbm_receives_train_start`, at the end:

```python
        assert run.data.tags["contribution_table"] == CONTRIBUTION_TABLE
        contributions = published_contribution_rows(spark, run.info.run_id)
        assert contributions.groupby(["trade_date", "time_code"]).ngroups == 96
        assert {"base", "time_code", "month", "day_of_week"} <= set(contributions["component"])
        assert set(contributions["strategy"]) == {"lightgbm"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_scripts.py tests/test_spot_price_scripts.py -q -p no:cacheprovider --no-cov -k "pinned_window or hole_day or train_start"`
Expected: failures on `run.data.tags["contribution_table"]` (KeyError) / table not found.

- [ ] **Step 3: Implement**

In both scripts extend the publish import:

```python
from power_market_analytics.forecasting.publish import (
    build_contribution_records,
    build_forecast_records,
    publish_contribution_records,
    publish_forecast_records,
)
```

In `scripts/demand_backtest.py`, right after `mlflow.set_tag("warehouse_table", TASK.forecast_table)`:

```python
        contributions = strategy.contributions()
        if contributions is None:
            logger.info("{}: strategy produces no contributions; nothing to publish", args.strategy)
        else:
            contribution_records = build_contribution_records(
                TASK,
                contributions,
                result,
                run_id=mlflow_run.info.run_id,
                strategy=args.strategy,
                area_code=args.area,
                published_at=records.df["published_at"].iloc[0],
            )
            publish_contribution_records(TASK, contribution_records)
            mlflow.set_tag("contribution_table", TASK.contribution_table)
```

and after the final `logger.info("Forecasts written to …")`:

```python
    if contributions is not None:
        logger.info("Contributions written to {} (partition run_id={})", TASK.contribution_table, run_id)
```

Make the identical change in `scripts/spot_price_backtest.py` (same insertion points; its final log block ends with the same "Forecasts written to" line). Extend each script's module docstring with one sentence: "Strategies that explain their forecasts (the LightGBM ones) also publish their TreeSHAP contributions to `pma_ml.<task>_forecast_contribution`."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demand_scripts.py tests/test_spot_price_scripts.py -q -p no:cacheprovider --no-cov`
Expected: all pass (~2–3 min: real backtests on the synthetic warehouse).

- [ ] **Step 5: Lint, type-check, full suite with coverage**

Run: `just lint && just mypy && just test -q`
Expected: ruff/mypy clean; pytest passes with total coverage 100 % (the gate). If a new line is uncovered, add the missing test rather than an exclusion.

- [ ] **Step 6: Commit**

```bash
git add scripts/demand_backtest.py scripts/spot_price_backtest.py tests/test_demand_scripts.py tests/test_spot_price_scripts.py
git commit -m "backtest scripts: publish TreeSHAP contributions next to the forecasts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Materialise the warehouse tables with real runs

The dbt staging models `REFRESH TABLE` their source, so both contribution tables must exist before Tasks 8–9 can `dbt build`. Two short real backtests do that. No code in this task.

**Files:** none (warehouse state only).

- [ ] **Step 1: Check the stack is up**

Run: `docker compose ps --format '{{.Name}} {{.Status}}' | grep -E 'devcontainer|thriftserver|mlflow|superset-1'`
Expected: every service `Up`. If not: `docker compose up -d` and wait for the healthchecks.

- [ ] **Step 2: Demand run (kept baseline, 30 days)**

Run (foreground, timeout 600000 ms): `just python scripts/demand_backtest.py --days 30 --shap-nsamples 200`
Expected: ends with `Forecasts written to pma_ml.demand_forecast …` and `Contributions written to pma_ml.demand_forecast_contribution …`; note the run id.

- [ ] **Step 3: Spot run (lightgbm, 30 days)**

Run (foreground, timeout 600000 ms): `just python scripts/spot_price_backtest.py --strategy lightgbm --days 30 --shap-nsamples 200`
Expected: `Contributions written to pma_ml.spot_price_forecast_contribution …`.

- [ ] **Step 4: Verify the tables**

Run: `just dbt show --inline "select component, component_order, count(*) as n, count(feature_value) as n_values from pma_ml.demand_forecast_contribution group by component, component_order order by component_order" --limit 20`
Expected: `base` with `n_values = 0`, then the seven features of `lightgbm_msm_popw_daytype` in order (`time_code, month, day_of_week, wavg_temperature_c, lag_7d_demand_kwh, popw_forecast_temperature_c, day_type`), equal `n` per component. Repeat for `pma_ml.spot_price_forecast_contribution`.

Nothing to commit.

---

### Task 8: dbt — demand contribution models and tests

**Files:**
- Modify: `dbt/models/raw/ml.yml`
- Create: `dbt/models/staging/stg_ml__demand_forecast_contribution.sql` + `.yml`, `dbt/models/standardized/std_ml__demand_forecast_contribution.sql` + `.yml`, `dbt/models/curated/fct_demand_forecast_contribution.sql` + `.yml`, `dbt/dbt_tests/assert_fct_demand_forecast_contribution_has_one_base_per_period.sql`, `dbt/dbt_tests/assert_fct_demand_forecast_contribution_sums_to_forecast.sql`

**Interfaces:**
- Consumes: `pma_ml.demand_forecast_contribution` (Task 5/7), `dim_area`, `fct_demand_forecast`.
- Produces: `pma_curated.fct_demand_forecast_contribution` with columns `date_key date, time_code int, area_key int, run_id string, strategy string, component string, component_order int, is_base boolean, trade_datetime timestamp, forecast_issued_ts timestamp, feature_value double, contribution_demand_kwh double, published_at timestamp` (read by the Superset explanation dataset, Task 10).

- [ ] **Step 1: Add the source**

Append to the `tables:` list of `dbt/models/raw/ml.yml`:

```yaml
      - name: demand_forecast_contribution
        description: >
          Per-component decomposition of the forecasts in demand_forecast,
          written by the same backtest run (scripts/demand_backtest.py;
          SlidingWindowLightGbmStrategy.contributions — exact TreeSHAP from
          LightGBM's pred_contrib). One row per MLflow run, area, 30-minute
          delivery period and component: the model's base value (component
          'base', component_order 0) or one feature. Per period, base + the sum
          of the feature contributions = forecast_demand_kwh. Partitioned by
          run_id and overwritten per run like demand_forecast; a strategy that
          cannot explain its forecasts writes nothing here.
        columns:
          - name: run_id
            description: MLflow run id (experiment demand); the run's tag contribution_table points back here.
            data_tests:
              - not_null
          - name: strategy
            description: Strategy registry key, e.g. lightgbm_msm_popw_daytype.
            data_tests:
              - not_null
          - name: area_code
            description: Bidding zone, joins to dim_area.area_code.
            data_tests:
              - not_null
          - name: forecast_issued_ts
            description: When the forecast was made (09:30 JST on D-1), as on demand_forecast.
            data_tests:
              - not_null
          - name: trade_date
            description: Target delivery date.
            data_tests:
              - not_null
          - name: time_code
            description: 30-minute delivery period of the day, 1-48.
            data_tests:
              - not_null
          - name: component
            description: >
              'base' for the model's expected value, else the feature name as
              listed in the run's lgbm_feature_cols param (time_code, month,
              day_of_week, wavg_temperature_c, lag_7d_demand_kwh, ...).
            data_tests:
              - not_null
          - name: component_order
            description: 0 for the base, then the feature's 1-based position in the model's feature list.
            data_tests:
              - not_null
          - name: feature_value
            description: >
              The feature's value as the model saw it (units vary per feature:
              a time code, a month, degrees C, kWh, a day-type code); null on
              the base row.
          - name: contribution_demand_kwh
            description: The component's additive contribution to the forecast, kWh per 30-minute period.
            data_tests:
              - not_null
          - name: published_at
            description: Same instant as the run's demand_forecast rows (naive JST).
            data_tests:
              - not_null
```

- [ ] **Step 2: Staging model**

`dbt/models/staging/stg_ml__demand_forecast_contribution.sql`:

```sql
-- Written by a separate Spark application (the backtest script); refresh the
-- thriftserver's cached file listing before reading, as for the forecasts.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'demand_forecast_contribution') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    forecast_issued_ts,
    trade_date,
    time_code,
    component,
    component_order,
    feature_value,
    contribution_demand_kwh,
    published_at
  from
    {{ source('ml', 'demand_forecast_contribution') }}
  )

select * from source
```

`dbt/models/staging/stg_ml__demand_forecast_contribution.yml`:

```yaml
models:
  - name: stg_ml__demand_forecast_contribution
    config:
      contract:
        enforced: true
    description: >
      As-is representation of pma_ml.demand_forecast_contribution (the
      per-component TreeSHAP decomposition of the demand forecasts). One row
      per MLflow run, area, 30-minute time_code and component. Column
      documentation lives on the source (models/raw/ml.yml).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_code
              - trade_date
              - time_code
              - component
    columns:
      - name: run_id
        data_type: string
        data_tests:
          - not_null
      - name: strategy
        data_type: string
        data_tests:
          - not_null
      - name: area_code
        data_type: string
        data_tests:
          - not_null
      - name: forecast_issued_ts
        data_type: timestamp
        data_tests:
          - not_null
      - name: trade_date
        data_type: date
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        data_tests:
          - not_null
      - name: component
        data_type: string
        data_tests:
          - not_null
      - name: component_order
        data_type: int
        data_tests:
          - not_null
      - name: feature_value
        data_type: double
      - name: contribution_demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 3: Standardized model**

`dbt/models/standardized/std_ml__demand_forecast_contribution.sql`:

```sql
with
  staging as (
  select
    *
  from
    {{ ref('stg_ml__demand_forecast_contribution') }}
  ),

  final as (
  select
    run_id,
    strategy,
    area_code,
    trade_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(trade_date as timestamp)) as trade_datetime,
    forecast_issued_ts,
    component,
    component_order,
    component = 'base' as is_base,
    feature_value,
    contribution_demand_kwh,
    published_at
  from
    staging
  )

select * from final
```

`dbt/models/standardized/std_ml__demand_forecast_contribution.yml`:

```yaml
models:
  - name: std_ml__demand_forecast_contribution
    config:
      contract:
        enforced: true
    description: >
      stg_ml__demand_forecast_contribution with the typed time axis
      (trade_datetime, the start of the 30-minute delivery period, as in
      std_ml__demand_forecast) and is_base (component = 'base').
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_code
              - trade_date
              - time_code
              - component
    columns:
      - name: run_id
        data_type: string
        data_tests:
          - not_null
      - name: strategy
        data_type: string
        data_tests:
          - not_null
      - name: area_code
        data_type: string
        data_tests:
          - not_null
      - name: trade_date
        data_type: date
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        data_tests:
          - not_null
      - name: trade_datetime
        data_type: timestamp
        data_tests:
          - not_null
      - name: forecast_issued_ts
        data_type: timestamp
        data_tests:
          - not_null
      - name: component
        data_type: string
        data_tests:
          - not_null
      - name: component_order
        data_type: int
        data_tests:
          - not_null
      - name: is_base
        data_type: boolean
        data_tests:
          - not_null
      - name: feature_value
        data_type: double
      - name: contribution_demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 4: Curated fact**

`dbt/models/curated/fct_demand_forecast_contribution.sql`:

```sql
with
  contribution as (
  select
    *
  from
    {{ ref('std_ml__demand_forecast_contribution') }}
  ),

  final as (
  select
    contribution.trade_date as date_key,
    contribution.time_code,
    dim_area.area_key,
    contribution.run_id,
    contribution.strategy,
    contribution.component,
    contribution.component_order,
    contribution.is_base,
    contribution.trade_datetime,
    contribution.forecast_issued_ts,
    contribution.feature_value,
    contribution.contribution_demand_kwh,
    contribution.published_at
  from
    contribution
    left join {{ ref('dim_area') }} as dim_area
      on contribution.area_code = dim_area.area_code
  )

select * from final
```

`dbt/models/curated/fct_demand_forecast_contribution.yml`:

```yaml
models:
  - name: fct_demand_forecast_contribution
    config:
      contract:
        enforced: true
    description: >
      Per-component decomposition (exact TreeSHAP) of the demand forecasts in
      fct_demand_forecast. Grain: one row per MLflow run x delivery period
      (trade date x 30-minute time code) x area x component, where component
      is the model's base value ('base', component_order 0, is_base) or one
      model feature in the model's feature order (component_order 1..n).
      component is a degenerate dimension like strategy; feature_value is the
      feature as the model saw it (units vary per feature, null on the base
      row) and is NON-ADDITIVE; contribution_demand_kwh is additive across the
      components of one period only — base + the feature rows reproduce that
      period's forecast_demand_kwh (see the singular tests) — never across
      runs. Drill across to fct_demand_forecast / fct_demand_forecast_accuracy
      on (run_id, date_key, time_code, area_key). Only strategies that explain
      themselves (the LightGBM ones) have rows here.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - date_key
              - time_code
              - area_key
              - component
    columns:
      - name: date_key
        data_type: date
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_key
      - name: time_code
        data_type: int
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_delivery_period')
                field: time_code
      - name: area_key
        data_type: int
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_area')
                field: area_key
      - name: run_id
        data_type: string
        data_tests:
          - not_null
      - name: strategy
        data_type: string
        data_tests:
          - not_null
      - name: component
        data_type: string
        data_tests:
          - not_null
      - name: component_order
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: is_base
        data_type: boolean
        data_tests:
          - not_null
      - name: trade_datetime
        data_type: timestamp
        data_tests:
          - not_null
      - name: forecast_issued_ts
        data_type: timestamp
        data_tests:
          - not_null
      - name: feature_value
        data_type: double
      - name: contribution_demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 5: Singular tests**

`dbt/dbt_tests/assert_fct_demand_forecast_contribution_has_one_base_per_period.sql`:

```sql
-- Every explained period carries exactly one base row (the model's expected
-- value): the dashboard's base-value tile and the additivity test rely on it.
select
  run_id,
  date_key,
  time_code,
  area_key,
  sum(case when is_base then 1 else 0 end) as n_base_rows
from
  {{ ref('fct_demand_forecast_contribution') }}
group by
  run_id,
  date_key,
  time_code,
  area_key
having
  sum(case when is_base then 1 else 0 end) <> 1
```

`dbt/dbt_tests/assert_fct_demand_forecast_contribution_sums_to_forecast.sql`:

```sql
-- Per period the base plus the feature contributions must reproduce the
-- published forecast (TreeSHAP is exactly additive; the tolerance absorbs
-- floating-point summation noise), and every explained period must have a
-- forecast row. A failure means the two write-backs came from different runs
-- or the melt lost a component.
with
  decomposed as (
  select
    run_id,
    date_key,
    time_code,
    area_key,
    sum(contribution_demand_kwh) as contribution_total_kwh
  from
    {{ ref('fct_demand_forecast_contribution') }}
  group by
    run_id,
    date_key,
    time_code,
    area_key
  )

select
  decomposed.run_id,
  decomposed.date_key,
  decomposed.time_code,
  decomposed.area_key,
  decomposed.contribution_total_kwh,
  forecast.forecast_demand_kwh
from
  decomposed
  left join {{ ref('fct_demand_forecast') }} as forecast
    on decomposed.run_id = forecast.run_id
    and decomposed.date_key = forecast.date_key
    and decomposed.time_code = forecast.time_code
    and decomposed.area_key = forecast.area_key
where
  forecast.forecast_demand_kwh is null
  or abs(decomposed.contribution_total_kwh - forecast.forecast_demand_kwh)
    > cast(1 as double) / 1000000 * greatest(abs(forecast.forecast_demand_kwh), cast(1 as double))
```

- [ ] **Step 6: Build and test**

Run: `just dbt build --select +fct_demand_forecast_contribution`
Expected: the three models build with their contracts, every generic test and both singular tests pass (`PASS`), no deprecation warnings about test arguments.

- [ ] **Step 7: Commit**

```bash
git add dbt/models/raw/ml.yml dbt/models/staging/stg_ml__demand_forecast_contribution.* dbt/models/standardized/std_ml__demand_forecast_contribution.* dbt/models/curated/fct_demand_forecast_contribution.* dbt/dbt_tests/assert_fct_demand_forecast_contribution_*.sql
git commit -m "dbt: fct_demand_forecast_contribution (TreeSHAP decomposition of the demand forecasts)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: dbt — spot-price contribution models and tests

The spot twin of Task 8; every file is given in full so the task stands alone.

**Files:**
- Modify: `dbt/models/raw/ml.yml`
- Create: `dbt/models/staging/stg_ml__spot_price_forecast_contribution.sql` + `.yml`, `dbt/models/standardized/std_ml__spot_price_forecast_contribution.sql` + `.yml`, `dbt/models/curated/fct_spot_price_forecast_contribution.sql` + `.yml`, `dbt/dbt_tests/assert_fct_spot_price_forecast_contribution_has_one_base_per_period.sql`, `dbt/dbt_tests/assert_fct_spot_price_forecast_contribution_sums_to_forecast.sql`

**Interfaces:**
- Consumes: `pma_ml.spot_price_forecast_contribution` (Tasks 5/7), `dim_area`, `fct_spot_price_forecast`.
- Produces: `pma_curated.fct_spot_price_forecast_contribution` with the Task 8 columns, the measure named `contribution_price_jpy_kwh` (read by the Superset explanation dataset, Task 10).

- [ ] **Step 1: Add the source**

Append to the `tables:` list of `dbt/models/raw/ml.yml`:

```yaml
      - name: spot_price_forecast_contribution
        description: >
          Per-component decomposition of the forecasts in spot_price_forecast,
          written by the same backtest run (scripts/spot_price_backtest.py;
          SlidingWindowLightGbmStrategy.contributions — exact TreeSHAP from
          LightGBM's pred_contrib). One row per MLflow run, area, 30-minute
          delivery period and component: the model's base value (component
          'base', component_order 0) or one feature. Per period, base + the sum
          of the feature contributions = forecast_price_jpy_kwh. Partitioned by
          run_id and overwritten per run like spot_price_forecast; a strategy
          that cannot explain its forecasts (previous_day) writes nothing here.
        columns:
          - name: run_id
            description: MLflow run id (experiment spot_price); the run's tag contribution_table points back here.
            data_tests:
              - not_null
          - name: strategy
            description: Strategy registry key, e.g. lightgbm.
            data_tests:
              - not_null
          - name: area_code
            description: Bidding zone, joins to dim_area.area_code.
            data_tests:
              - not_null
          - name: forecast_issued_ts
            description: When the forecast was made (9:55 JST on D-1), as on spot_price_forecast.
            data_tests:
              - not_null
          - name: trade_date
            description: Target delivery date.
            data_tests:
              - not_null
          - name: time_code
            description: 30-minute delivery period of the day, 1-48.
            data_tests:
              - not_null
          - name: component
            description: >
              'base' for the model's expected value, else the feature name as
              listed in the run's lgbm_feature_cols param (time_code, month,
              day_of_week, the price lags, ...).
            data_tests:
              - not_null
          - name: component_order
            description: 0 for the base, then the feature's 1-based position in the model's feature list.
            data_tests:
              - not_null
          - name: feature_value
            description: >
              The feature's value as the model saw it (units vary per feature:
              a time code, a month, JPY/kWh, MW); null on the base row.
          - name: contribution_price_jpy_kwh
            description: The component's additive contribution to the forecast, JPY/kWh.
            data_tests:
              - not_null
          - name: published_at
            description: Same instant as the run's spot_price_forecast rows (naive JST).
            data_tests:
              - not_null
```

- [ ] **Step 2: Staging model**

`dbt/models/staging/stg_ml__spot_price_forecast_contribution.sql`:

```sql
-- Written by a separate Spark application (the backtest script); refresh the
-- thriftserver's cached file listing before reading, as for the forecasts.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'spot_price_forecast_contribution') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    forecast_issued_ts,
    trade_date,
    time_code,
    component,
    component_order,
    feature_value,
    contribution_price_jpy_kwh,
    published_at
  from
    {{ source('ml', 'spot_price_forecast_contribution') }}
  )

select * from source
```

`dbt/models/staging/stg_ml__spot_price_forecast_contribution.yml`:

```yaml
models:
  - name: stg_ml__spot_price_forecast_contribution
    config:
      contract:
        enforced: true
    description: >
      As-is representation of pma_ml.spot_price_forecast_contribution (the
      per-component TreeSHAP decomposition of the spot price forecasts). One
      row per MLflow run, area, 30-minute time_code and component. Column
      documentation lives on the source (models/raw/ml.yml).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_code
              - trade_date
              - time_code
              - component
    columns:
      - name: run_id
        data_type: string
        data_tests:
          - not_null
      - name: strategy
        data_type: string
        data_tests:
          - not_null
      - name: area_code
        data_type: string
        data_tests:
          - not_null
      - name: forecast_issued_ts
        data_type: timestamp
        data_tests:
          - not_null
      - name: trade_date
        data_type: date
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        data_tests:
          - not_null
      - name: component
        data_type: string
        data_tests:
          - not_null
      - name: component_order
        data_type: int
        data_tests:
          - not_null
      - name: feature_value
        data_type: double
      - name: contribution_price_jpy_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 3: Standardized model**

`dbt/models/standardized/std_ml__spot_price_forecast_contribution.sql`:

```sql
with
  staging as (
  select
    *
  from
    {{ ref('stg_ml__spot_price_forecast_contribution') }}
  ),

  final as (
  select
    run_id,
    strategy,
    area_code,
    trade_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(trade_date as timestamp)) as trade_datetime,
    forecast_issued_ts,
    component,
    component_order,
    component = 'base' as is_base,
    feature_value,
    contribution_price_jpy_kwh,
    published_at
  from
    staging
  )

select * from final
```

`dbt/models/standardized/std_ml__spot_price_forecast_contribution.yml`:

```yaml
models:
  - name: std_ml__spot_price_forecast_contribution
    config:
      contract:
        enforced: true
    description: >
      stg_ml__spot_price_forecast_contribution with the typed time axis
      (trade_datetime, the start of the 30-minute delivery period, as in
      std_ml__spot_price_forecast) and is_base (component = 'base').
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_code
              - trade_date
              - time_code
              - component
    columns:
      - name: run_id
        data_type: string
        data_tests:
          - not_null
      - name: strategy
        data_type: string
        data_tests:
          - not_null
      - name: area_code
        data_type: string
        data_tests:
          - not_null
      - name: trade_date
        data_type: date
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        data_tests:
          - not_null
      - name: trade_datetime
        data_type: timestamp
        data_tests:
          - not_null
      - name: forecast_issued_ts
        data_type: timestamp
        data_tests:
          - not_null
      - name: component
        data_type: string
        data_tests:
          - not_null
      - name: component_order
        data_type: int
        data_tests:
          - not_null
      - name: is_base
        data_type: boolean
        data_tests:
          - not_null
      - name: feature_value
        data_type: double
      - name: contribution_price_jpy_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 4: Curated fact**

`dbt/models/curated/fct_spot_price_forecast_contribution.sql`:

```sql
with
  contribution as (
  select
    *
  from
    {{ ref('std_ml__spot_price_forecast_contribution') }}
  ),

  final as (
  select
    contribution.trade_date as date_key,
    contribution.time_code,
    dim_area.area_key,
    contribution.run_id,
    contribution.strategy,
    contribution.component,
    contribution.component_order,
    contribution.is_base,
    contribution.trade_datetime,
    contribution.forecast_issued_ts,
    contribution.feature_value,
    contribution.contribution_price_jpy_kwh,
    contribution.published_at
  from
    contribution
    left join {{ ref('dim_area') }} as dim_area
      on contribution.area_code = dim_area.area_code
  )

select * from final
```

`dbt/models/curated/fct_spot_price_forecast_contribution.yml`:

```yaml
models:
  - name: fct_spot_price_forecast_contribution
    config:
      contract:
        enforced: true
    description: >
      Per-component decomposition (exact TreeSHAP) of the spot price forecasts
      in fct_spot_price_forecast. Grain: one row per MLflow run x delivery
      period (trade date x 30-minute time code) x area x component, where
      component is the model's base value ('base', component_order 0,
      is_base) or one model feature in the model's feature order
      (component_order 1..n). component is a degenerate dimension like
      strategy; feature_value is the feature as the model saw it (units vary
      per feature, null on the base row) and is NON-ADDITIVE;
      contribution_price_jpy_kwh is additive across the components of one
      period only — base + the feature rows reproduce that period's
      forecast_price_jpy_kwh (see the singular tests) — never across runs.
      Drill across to fct_spot_price_forecast / fct_spot_price_forecast_accuracy
      on (run_id, date_key, time_code, area_key). Only strategies that explain
      themselves (the LightGBM ones) have rows here; previous_day has none.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - date_key
              - time_code
              - area_key
              - component
    columns:
      - name: date_key
        data_type: date
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_key
      - name: time_code
        data_type: int
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_delivery_period')
                field: time_code
      - name: area_key
        data_type: int
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_area')
                field: area_key
      - name: run_id
        data_type: string
        data_tests:
          - not_null
      - name: strategy
        data_type: string
        data_tests:
          - not_null
      - name: component
        data_type: string
        data_tests:
          - not_null
      - name: component_order
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: is_base
        data_type: boolean
        data_tests:
          - not_null
      - name: trade_datetime
        data_type: timestamp
        data_tests:
          - not_null
      - name: forecast_issued_ts
        data_type: timestamp
        data_tests:
          - not_null
      - name: feature_value
        data_type: double
      - name: contribution_price_jpy_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

- [ ] **Step 5: Singular tests**

`dbt/dbt_tests/assert_fct_spot_price_forecast_contribution_has_one_base_per_period.sql`:

```sql
-- Every explained period carries exactly one base row (the model's expected
-- value): the dashboard's base-value tile and the additivity test rely on it.
select
  run_id,
  date_key,
  time_code,
  area_key,
  sum(case when is_base then 1 else 0 end) as n_base_rows
from
  {{ ref('fct_spot_price_forecast_contribution') }}
group by
  run_id,
  date_key,
  time_code,
  area_key
having
  sum(case when is_base then 1 else 0 end) <> 1
```

`dbt/dbt_tests/assert_fct_spot_price_forecast_contribution_sums_to_forecast.sql`:

```sql
-- Per period the base plus the feature contributions must reproduce the
-- published forecast (TreeSHAP is exactly additive; the tolerance absorbs
-- floating-point summation noise), and every explained period must have a
-- forecast row. A failure means the two write-backs came from different runs
-- or the melt lost a component.
with
  decomposed as (
  select
    run_id,
    date_key,
    time_code,
    area_key,
    sum(contribution_price_jpy_kwh) as contribution_total_jpy_kwh
  from
    {{ ref('fct_spot_price_forecast_contribution') }}
  group by
    run_id,
    date_key,
    time_code,
    area_key
  )

select
  decomposed.run_id,
  decomposed.date_key,
  decomposed.time_code,
  decomposed.area_key,
  decomposed.contribution_total_jpy_kwh,
  forecast.forecast_price_jpy_kwh
from
  decomposed
  left join {{ ref('fct_spot_price_forecast') }} as forecast
    on decomposed.run_id = forecast.run_id
    and decomposed.date_key = forecast.date_key
    and decomposed.time_code = forecast.time_code
    and decomposed.area_key = forecast.area_key
where
  forecast.forecast_price_jpy_kwh is null
  or abs(decomposed.contribution_total_jpy_kwh - forecast.forecast_price_jpy_kwh)
    > cast(1 as double) / 1000000 * greatest(abs(forecast.forecast_price_jpy_kwh), cast(1 as double))
```

- [ ] **Step 6: Build and test**

Run: `just dbt build --select +fct_spot_price_forecast_contribution +fct_demand_forecast_contribution`
Expected: all models and tests pass for both tasks.

- [ ] **Step 7: Commit**

```bash
git add dbt/models/raw/ml.yml dbt/models/staging/stg_ml__spot_price_forecast_contribution.* dbt/models/standardized/std_ml__spot_price_forecast_contribution.* dbt/models/curated/fct_spot_price_forecast_contribution.* dbt/dbt_tests/assert_fct_spot_price_forecast_contribution_*.sql
git commit -m "dbt: fct_spot_price_forecast_contribution

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Dashboard spec fields and the explanation dataset

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (template + column constants after `COMMON_DATASET_COLUMNS`; `DashboardSpec` fields/properties; `SPOT_PRICE` / `DEMAND`; `upsert_dataset`)
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces: `EXPLANATION_DATASET_SQL_TEMPLATE`, `COMMON_EXPLANATION_COLUMNS`; `DashboardSpec` fields `explanation_dataset_name: str`, `contribution_table: str`, `contribution_col: str`, `contribution_format: str`, `explanation_value_columns_sql: str`, `explanation_value_columns: tuple[tuple[str, str, bool], ...]`; properties `explanation_dataset_sql -> str`, `explanation_dataset_columns -> list[tuple[str, str, bool]]`, `contribution_metric -> dict`, `base_value_metric -> dict`, `net_effect_metric -> dict`; `upsert_dataset(client, database_id, name, sql, columns) -> int` (generalised — was `(client, database_id, spec)`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_create_forecast_dashboard.py`, after `DEMAND_COLUMNS`, add the expected explanation SQL and columns:

```python
EXPLANATION_SQL_HEAD = """\
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
"""


def explanation_sql_tail(contribution_table: str, accuracy_table: str) -> str:
    return f"""\
from {contribution_table} c
join pma_curated.dim_area a on c.area_key = a.area_key
join pma_curated.dim_delivery_period p on c.time_code = p.time_code
join pma_curated.dim_date d on c.date_key = d.date_key
left join {accuracy_table} f
  on c.run_id = f.run_id
  and c.date_key = f.date_key
  and c.time_code = f.time_code
  and c.area_key = f.area_key
"""


SPOT_EXPLANATION_SQL = (
    EXPLANATION_SQL_HEAD
    + """\
  c.contribution_price_jpy_kwh,
  f.forecast_price_jpy_kwh,
  f.actual_price_jpy_kwh
"""
    + explanation_sql_tail(
        "pma_curated.fct_spot_price_forecast_contribution",
        "pma_curated.fct_spot_price_forecast_accuracy",
    )
)
DEMAND_EXPLANATION_SQL = (
    EXPLANATION_SQL_HEAD
    + """\
  c.contribution_demand_kwh / 1000 as contribution_mwh,
  f.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  f.actual_demand_kwh / 1000 as actual_demand_mwh
"""
    + explanation_sql_tail(
        "pma_curated.fct_demand_forecast_contribution",
        "pma_curated.fct_demand_forecast_accuracy",
    )
)
EXPLANATION_COLUMNS_HEAD = [
    ("date_key", "DATE", True),
    ("trade_date_label", "STRING", False),
    ("trade_datetime", "TIMESTAMP", True),
    ("time_code", "INT", False),
    ("period_label", "STRING", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_type", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("component", "STRING", False),
    ("component_order", "INT", False),
    ("component_label", "STRING", False),
    ("is_base", "BOOLEAN", False),
    ("feature_value", "DOUBLE", False),
]
SPOT_EXPLANATION_COLUMNS = EXPLANATION_COLUMNS_HEAD + [
    ("contribution_price_jpy_kwh", "DOUBLE", False),
    ("forecast_price_jpy_kwh", "DOUBLE", False),
    ("actual_price_jpy_kwh", "DOUBLE", False),
]
DEMAND_EXPLANATION_COLUMNS = EXPLANATION_COLUMNS_HEAD + [
    ("contribution_mwh", "DOUBLE", False),
    ("forecast_demand_mwh", "DOUBLE", False),
    ("actual_demand_mwh", "DOUBLE", False),
]
```

Append to `class TestDashboardSpecs`:

```python
    def test_explanation_identity(self, spot, demand):
        assert spot.explanation_dataset_name == "spot_price_forecast_explanation"
        assert spot.contribution_table == "pma_curated.fct_spot_price_forecast_contribution"
        assert spot.contribution_col == "contribution_price_jpy_kwh"
        assert spot.contribution_format == "+,.3f"
        assert demand.explanation_dataset_name == "demand_forecast_explanation"
        assert demand.contribution_table == "pma_curated.fct_demand_forecast_contribution"
        assert demand.contribution_col == "contribution_mwh"
        assert demand.contribution_format == "+,.0f"

    def test_explanation_dataset_sql(self, spot, demand):
        assert spot.explanation_dataset_sql == SPOT_EXPLANATION_SQL
        assert demand.explanation_dataset_sql == DEMAND_EXPLANATION_SQL

    def test_explanation_columns_follow_the_sql(self, spot, demand):
        assert spot.explanation_dataset_columns == SPOT_EXPLANATION_COLUMNS
        assert demand.explanation_dataset_columns == DEMAND_EXPLANATION_COLUMNS

    def test_explanation_columns_match_the_sql_select_list_in_order(self, spec):
        select_list = spec.explanation_dataset_sql.split("\nfrom ", 1)[0].splitlines()[1:]
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[cfpda]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.explanation_dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.explanation_dataset_columns if is_dttm] == [
            "date_key",
            "trade_datetime",
            "published_at",
        ]

    def test_explanation_metrics(self, script, spot, demand):
        assert spot.contribution_metric == script.avg_metric(
            "contribution_price_jpy_kwh", "Contribution (JPY/kWh)"
        )
        assert demand.contribution_metric == script.avg_metric("contribution_mwh", "Contribution (MWh)")
        assert spot.base_value_metric == script.sql_metric(
            "avg(case when is_base then contribution_price_jpy_kwh end)", "Base value"
        )
        assert demand.base_value_metric["sqlExpression"] == (
            "avg(case when is_base then contribution_mwh end)"
        )
        assert spot.net_effect_metric["sqlExpression"] == (
            "avg(forecast_price_jpy_kwh) - avg(case when is_base then contribution_price_jpy_kwh end)"
        )
        assert demand.net_effect_metric["sqlExpression"] == (
            "avg(forecast_demand_mwh) - avg(case when is_base then contribution_mwh end)"
        )
        assert demand.net_effect_metric["label"] == "Net feature effect"
        assert demand.net_effect_metric["optionName"] == "metric_net_feature_effect"
```

In `class TestUpsertDataset` change every `script.upsert_dataset(client, 3, spec)` / `(client, 3, spot)` / `(client, 3, demand)` call to the new signature, e.g. `script.upsert_dataset(client, 3, spec.dataset_name, spec.dataset_sql, spec.dataset_columns)`, and append:

```python
    def test_explanation_dataset_is_a_second_dataset_on_the_same_database(self, script, fake, demand):
        client = make_client(script, fake)
        analysis_id = script.upsert_dataset(
            client, 3, demand.dataset_name, demand.dataset_sql, demand.dataset_columns
        )
        explanation_id = script.upsert_dataset(
            client,
            3,
            demand.explanation_dataset_name,
            demand.explanation_dataset_sql,
            demand.explanation_dataset_columns,
        )
        assert (analysis_id, explanation_id) == (10, 11)
        row = fake.rows["dataset"][11]
        assert row["table_name"] == "demand_forecast_explanation"
        assert row["sql"] == DEMAND_EXPLANATION_SQL
        assert row["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in row["columns"]] == (
            DEMAND_EXPLANATION_COLUMNS
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov -k "explanation or UpsertDataset"`
Expected: failures (`AttributeError: explanation_dataset_name`, TypeError on the new `upsert_dataset` signature).

- [ ] **Step 3: Implement**

In `scripts/create_forecast_dashboard.py`, after `COMMON_DATASET_COLUMNS`:

```python
# Shared skeleton of every task's explanation dataset: the contribution fact
# (one row per period x component) with calendar / period / area context,
# the same run_label construction as the analysis dataset (so the Run filter
# selects both), sortable Day / Period / component labels, then the task's
# value block — the contribution and, from the accuracy mart, the period's
# forecast and actual (repeated on each component row: AVG-only metrics).
EXPLANATION_DATASET_SQL_TEMPLATE = """\
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
"""

COMMON_EXPLANATION_COLUMNS = (
    ("date_key", "DATE", True),
    ("trade_date_label", "STRING", False),
    ("trade_datetime", "TIMESTAMP", True),
    ("time_code", "INT", False),
    ("period_label", "STRING", False),
    ("hour_of_day", "INT", False),
    ("day_part", "STRING", False),
    ("day_name", "STRING", False),
    ("day_type", "STRING", False),
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("component", "STRING", False),
    ("component_order", "INT", False),
    ("component_label", "STRING", False),
    ("is_base", "BOOLEAN", False),
    ("feature_value", "DOUBLE", False),
)
```

Add to `DashboardSpec` (fields after `worst_days_max_format`; document them in the class docstring: "explanation_dataset_name / contribution_table: the explanation dataset and the contribution fact it reads; contribution_col: the *dataset* column of a component's contribution (rescaled like the value columns); contribution_format: signed d3 format for contributions; explanation_value_columns_sql / explanation_value_columns: the value block — contribution, forecast, actual — two-space indented, the last line without a trailing comma"):

```python
    explanation_dataset_name: str
    contribution_table: str
    contribution_col: str
    contribution_format: str
    explanation_value_columns_sql: str
    explanation_value_columns: tuple[tuple[str, str, bool], ...]
```

and the properties after `p90_metric`:

```python
    @property
    def explanation_dataset_sql(self) -> str:
        """The explanation dataset's SQL: the shared template around this task's value block."""
        return EXPLANATION_DATASET_SQL_TEMPLATE.format(
            explanation_value_columns_sql=self.explanation_value_columns_sql,
            contribution_table=self.contribution_table,
            accuracy_table=self.accuracy_table,
        )

    @property
    def explanation_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every explanation column, in select order."""
        return [*COMMON_EXPLANATION_COLUMNS, *self.explanation_value_columns]

    @property
    def contribution_metric(self) -> dict:
        """Mean contribution per period of the selection (SHAP is additive, so the
        per-period mean is a valid decomposition of the mean forecast)."""
        return avg_metric(self.contribution_col, f"Contribution ({self.unit})")

    @property
    def base_value_metric(self) -> dict:
        return sql_metric(f"avg(case when is_base then {self.contribution_col} end)", "Base value")

    @property
    def net_effect_metric(self) -> dict:
        """forecast − base = the sum of the feature contributions (the waterfall's Total)."""
        return sql_metric(
            f"avg({self.forecast_col}) - avg(case when is_base then {self.contribution_col} end)",
            "Net feature effect",
        )
```

Add to `SPOT_PRICE`:

```python
    explanation_dataset_name="spot_price_forecast_explanation",
    contribution_table="pma_curated.fct_spot_price_forecast_contribution",
    contribution_col="contribution_price_jpy_kwh",
    contribution_format="+,.3f",
    explanation_value_columns_sql="""\
  c.contribution_price_jpy_kwh,
  f.forecast_price_jpy_kwh,
  f.actual_price_jpy_kwh""",
    explanation_value_columns=(
        ("contribution_price_jpy_kwh", "DOUBLE", False),
        ("forecast_price_jpy_kwh", "DOUBLE", False),
        ("actual_price_jpy_kwh", "DOUBLE", False),
    ),
```

and to `DEMAND`:

```python
    explanation_dataset_name="demand_forecast_explanation",
    contribution_table="pma_curated.fct_demand_forecast_contribution",
    contribution_col="contribution_mwh",
    contribution_format="+,.0f",
    explanation_value_columns_sql="""\
  c.contribution_demand_kwh / 1000 as contribution_mwh,
  f.forecast_demand_kwh / 1000 as forecast_demand_mwh,
  f.actual_demand_kwh / 1000 as actual_demand_mwh""",
    explanation_value_columns=(
        ("contribution_mwh", "DOUBLE", False),
        ("forecast_demand_mwh", "DOUBLE", False),
        ("actual_demand_mwh", "DOUBLE", False),
    ),
```

Generalise `upsert_dataset`:

```python
def upsert_dataset(
    client: SupersetClient,
    database_id: int,
    name: str,
    sql: str,
    columns: list[tuple[str, str, bool]],
) -> int:
    """Create or update a virtual dataset and return its id.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
        Superset id of the Spark Thriftserver connection.
    name : str
        ``table_name`` of the virtual dataset (matched on reruns).
    sql : str
        The dataset SQL.
    columns : list of (str, str, bool)
        (column_name, generic type, is temporal) for every output column, in
        select order; overrides any stale column metadata on reruns.

    Returns
    -------
    int
    """
    column_payload = [
        {"column_name": col, "type": dtype, "is_dttm": is_dttm, "groupby": True, "filterable": True}
        for col, dtype, is_dttm in columns
    ]
    dataset_id = client.find_one("dataset", table_name=name)
    if dataset_id is None:
        dataset_id = client._post_json(
            "/api/v1/dataset/", {"database": database_id, "table_name": name, "sql": sql}
        )["id"]
    client._put_json(
        f"/api/v1/dataset/{dataset_id}",
        {"sql": sql, "main_dttm_col": "trade_datetime", "columns": column_payload},
        params={"override_columns": "true"},
    )
    return dataset_id
```

and in `build_dashboard` call it as `upsert_dataset(client, database_id, spec.dataset_name, spec.dataset_sql, spec.dataset_columns)` (the second dataset is wired in Task 12).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov`
Expected: all pass (the build/main tests are untouched by this task since `build_dashboard` still creates one dataset).

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "dashboard: explanation dataset template and spec fields

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Explanation chart builders

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (after `detail_params`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestChartParams`)

**Interfaces:**
- Consumes: `DashboardSpec.contribution_metric`, `contribution_format`, `axis_format`, `unit` (Task 10).
- Produces: `NOT_BASE_FILTER: dict`; `waterfall_params(spec, dataset_id) -> dict`; `feature_table_params(spec, dataset_id) -> dict`; `contribution_by_period_params(spec, dataset_id) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `class TestChartParams`:

```python
    def test_waterfall(self, script, spec):
        p = script.waterfall_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "waterfall"
        assert p["x_axis"] == "component_label"
        assert p["groupby"] == []
        assert p["metric"] == spec.contribution_metric
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["show_total"] is True
        assert p["total_label"] == "Net effect"
        assert p["increase_label"] == "Pushes forecast up"
        assert p["decrease_label"] == "Pushes forecast down"
        assert p["show_value"] is True
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_label"] == spec.unit
        assert p["row_limit"] == 100
        assert p["extra_form_data"] == {}

    def test_not_base_filter_is_a_sql_where_clause(self, script):
        assert script.NOT_BASE_FILTER == {
            "expressionType": "SQL",
            "sqlExpression": "not is_base",
            "clause": "WHERE",
            "filterOptionName": "filter_not_is_base",
        }

    def test_feature_table(self, script, spec):
        p = script.feature_table_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["component_label"]
        order, value, contribution = p["metrics"]
        assert order == script.sql_metric("min(component_order)", "Order")
        assert value == script.sql_metric("avg(feature_value)", "Feature value")
        assert contribution == spec.contribution_metric
        assert p["timeseries_limit_metric"] == order
        assert p["order_desc"] is False
        assert p["adhoc_filters"] == []  # the base row stays: the column sums to the forecast
        assert p["column_config"] == {
            "Order": {"d3NumberFormat": ",d"},
            "Feature value": {"d3NumberFormat": ",.2~f"},
            f"Contribution ({spec.unit})": {"d3NumberFormat": spec.contribution_format},
        }

    def test_contribution_by_period(self, script, spec):
        p = script.contribution_by_period_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["x_axis"] == "time_code"
        assert p["groupby"] == ["component_label"]
        assert p["metrics"] == [spec.contribution_metric]
        assert p["stack"] == "Stack"
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["x_axis_sort_asc"] is True
        assert p["order_desc"] is False
        assert p["show_legend"] is True
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == spec.unit
        assert p["row_limit"] == 10000
```

Also extend `test_every_builder_targets_the_dataset_and_starts_unfiltered` with `lambda: script.feature_table_params(spec, 12)` (the two filtered builders are covered above).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov -k "waterfall or not_base or feature_table or by_period"`
Expected: `AttributeError` for each missing builder.

- [ ] **Step 3: Implement**

After `detail_params` in `scripts/create_forecast_dashboard.py`:

```python
# Ad-hoc WHERE clause that drops the base row: the waterfall and the stacked
# bars show feature contributions only (Superset's value axis always includes
# zero, so a base bar ~30x the contributions would flatten them; the base is
# a KPI tile instead).
NOT_BASE_FILTER = {
    "expressionType": "SQL",
    "sqlExpression": "not is_base",
    "clause": "WHERE",
    "filterOptionName": "filter_not_is_base",
}


def waterfall_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the SHAP waterfall of the selected day / period.

    One bar per model feature in the model's feature order (the chart sorts
    by x-axis label, hence ``component_label``'s zero-padded order prefix),
    each the mean contribution per period of the selection; the Total bar is
    forecast − base. The base row is filtered out (see ``NOT_BASE_FILTER``).

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "waterfall",
        "x_axis": "component_label",
        "time_grain_sqla": None,
        "groupby": [],
        "metric": spec.contribution_metric,
        "adhoc_filters": [NOT_BASE_FILTER],
        "row_limit": 100,
        "show_value": True,
        "show_legend": True,
        "increase_label": "Pushes forecast up",
        "decrease_label": "Pushes forecast down",
        "show_total": True,
        "total_label": "Net effect",
        "x_axis_label": "Component (model feature order)",
        "x_axis_time_format": "smart_date",
        "x_ticks_layout": "auto",
        "y_axis_label": spec.unit,
        "y_axis_format": spec.axis_format,
        "extra_form_data": {},
    }


def feature_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the component table: order, mean feature value, mean contribution.

    Keeps the base row (order 00, no feature value), so the contribution
    column sums to the forecast; sorted by ``Order`` to read in the model's
    feature order.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    order = sql_metric("min(component_order)", "Order")
    contribution = spec.contribution_metric
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["component_label"],
        "metrics": [order, sql_metric("avg(feature_value)", "Feature value"), contribution],
        "adhoc_filters": [],
        "timeseries_limit_metric": order,
        "order_desc": False,
        "row_limit": 100,
        "server_page_length": 20,
        "table_timestamp_format": "smart_date",
        "column_config": {
            "Order": {"d3NumberFormat": ",d"},
            "Feature value": {"d3NumberFormat": ",.2~f"},
            contribution["label"]: {"d3NumberFormat": spec.contribution_format},
        },
        "extra_form_data": {},
    }


def contribution_by_period_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the stacked bars of each feature's contribution over the day's 48 periods.

    Excluded from the Period filter by the caller, so it keeps the whole
    selected day as context when one period is selected.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_bar",
        "x_axis": "time_code",
        "time_grain_sqla": None,
        "x_axis_sort_asc": True,
        "metrics": [spec.contribution_metric],
        "groupby": ["component_label"],
        "adhoc_filters": [NOT_BASE_FILTER],
        "order_desc": False,
        "row_limit": 10000,
        "stack": "Stack",
        "show_legend": True,
        "legendType": "scroll",
        "legendOrientation": "top",
        "rich_tooltip": True,
        "y_axis_format": spec.axis_format,
        "y_axis_title": spec.unit,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "dashboard: waterfall, component table and by-period chart builders

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Day / Period filters, `latest_run`, and the section wiring

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`latest_run_label` → `latest_run`; `build_native_filters` + a `_select_filter` helper; `build_dashboard`; module docstring)
- Test: `tests/test_create_forecast_dashboard.py` (`FakeSupersetSession`, `TestLatestRun`, `TestBuildNativeFilters`, `TestBuildDashboard`, `TestMain`)

**Interfaces:**
- Consumes: Task 10's dataset/spec members, Task 11's builders, `big_number_params`, `avg_metric`.
- Produces: `latest_run(client, database_id, spec) -> tuple[str, str] | None` (run label, last delivery day `yyyy-MM-dd`); `build_native_filters(dataset_id, run_excluded, default_run_label, explanation_dataset_id, day_excluded, period_excluded, default_day_label) -> list[dict]` returning Run, Day, Period; `build_dashboard` creating two datasets, 26 charts and the section `Explanation (SHAP)`.

Chart/dataset ids in the fake (allocated from 10, in creation order): analysis dataset 10, explanation dataset 11, analysis charts 12–30 (Run leaderboard 28, Worst days 29, 30-min detail 30), explanation charts 31–37 (Base value 31 … Contributions by period 37), dashboard 38. A second dashboard built in the same fake: datasets 39/40, charts 41–59 and 60–66, dashboard 67.

- [ ] **Step 1: Update the fake and write the failing tests**

Constants next to `DEFAULT_LABEL`:

```python
DEFAULT_LAST_DAY = "2026-08-17"
```

In `FakeSupersetSession._post`, the SQL Lab response becomes:

```python
        if path == "/api/v1/sqllab/execute/":
            return FakeResponse({"data": [{"run_label": DEFAULT_LABEL, "last_day": DEFAULT_LAST_DAY}]})
```

Replace `class TestLatestRunLabel` by:

```python
class TestLatestRun:
    def test_returns_newest_label_and_last_day_via_sqllab(self, script, fake, spec):
        client = make_client(script, fake)

        assert script.latest_run(client, 3, spec) == (DEFAULT_LABEL, DEFAULT_LAST_DAY)

        (call,) = fake.calls_after_login()
        method, url, payload, params = call
        assert (method, url, params) == ("POST", f"{BASE}/api/v1/sqllab/execute/", None)
        assert payload["database_id"] == 3
        assert payload["runAsync"] is False
        assert f"from {spec.accuracy_table} f" in payload["sql"]
        assert "date_format(max(f.date_key), 'yyyy-MM-dd') as last_day" in payload["sql"]
        assert "group by f.run_id, f.published_at, a.area_code" in payload["sql"]
        assert "order by f.published_at desc" in payload["sql"]
        assert "limit 1" in payload["sql"]
        assert "as run_label" in payload["sql"]

    def test_none_on_http_error(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"message": "boom"}, 500))
        assert script.latest_run(make_client(script, fake), 3, spot) is None

    def test_none_when_mart_is_empty(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": []}))
        assert script.latest_run(make_client(script, fake), 3, spot) is None

    def test_none_when_response_has_no_data_key(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"result": "no data here"}))
        assert script.latest_run(make_client(script, fake), 3, spot) is None

    def test_none_when_the_row_lacks_the_last_day(self, script, spot):
        fake = FakeSupersetSession(sqllab=FakeResponse({"data": [{"run_label": DEFAULT_LABEL}]}))
        assert script.latest_run(make_client(script, fake), 3, spot) is None
```

Replace `class TestBuildNativeFilters` by:

```python
class TestBuildNativeFilters:
    def test_explicit_defaults_apply_on_load(self, script):
        run, day, period = script.build_native_filters(
            10, [27], DEFAULT_LABEL, 11, [12, 13], [12, 13, 37], DEFAULT_LAST_DAY
        )
        assert [f["type"] for f in (run, day, period)] == ["NATIVE_FILTER"] * 3
        assert [f["filterType"] for f in (run, day, period)] == ["filter_select"] * 3

        assert run["id"] == "NATIVE_FILTER-run"
        assert run["name"] == "Run"
        assert run["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        assert run["defaultDataMask"] == {
            "extraFormData": {"filters": [{"col": "run_label", "op": "IN", "val": [DEFAULT_LABEL]}]},
            "filterState": {"value": [DEFAULT_LABEL], "label": DEFAULT_LABEL},
        }
        assert run["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": True,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": False,
        }
        assert run["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [27]}
        assert run["cascadeParentIds"] == []

        assert day["id"] == "NATIVE_FILTER-day"
        assert day["name"] == "Day"
        assert day["targets"] == [{"column": {"name": "trade_date_label"}, "datasetId": 11}]
        assert day["defaultDataMask"] == {
            "extraFormData": {
                "filters": [{"col": "trade_date_label", "op": "IN", "val": [DEFAULT_LAST_DAY]}]
            },
            "filterState": {"value": [DEFAULT_LAST_DAY], "label": DEFAULT_LAST_DAY},
        }
        assert day["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": False,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": False,
        }
        assert day["cascadeParentIds"] == ["NATIVE_FILTER-run"]
        assert day["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [12, 13]}

        assert period["id"] == "NATIVE_FILTER-period"
        assert period["name"] == "Period"
        assert period["targets"] == [{"column": {"name": "period_label"}, "datasetId": 11}]
        assert period["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert period["controlValues"] == {
            "multiSelect": False,
            "enableEmptyFilter": False,
            "defaultToFirstItem": False,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": True,
        }
        assert period["cascadeParentIds"] == []
        assert period["scope"] == {"rootPath": ["ROOT_ID"], "excluded": [12, 13, 37]}

    def test_no_defaults_fall_back_to_first_item(self, script):
        run, day, period = script.build_native_filters(10, [], None, 11, [], [], None)
        assert run["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert run["controlValues"]["defaultToFirstItem"] is True
        assert run["scope"] == {"rootPath": ["ROOT_ID"], "excluded": []}
        assert day["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}
        assert day["controlValues"]["defaultToFirstItem"] is True
        # The period filter never pre-selects: empty means the whole day.
        assert period["controlValues"]["defaultToFirstItem"] is False
```

Constants for the build tests — add after `EXPECTED_DEMAND_CHART_NAMES`:

```python
EXPLANATION_CHART_NAMES = [
    "Base value",
    "Forecast (selection)",
    "Actual (selection)",
    "Net feature effect",
    "SHAP waterfall",
    "Feature values & contributions",
    "Contributions by period",
]
```

and extend `EXPECTED_GRID_CHILDREN` with `"HEADER-4", "ROW-4-0", "ROW-4-1", "ROW-4-2"`.

`test_builds_dataset_charts_and_dashboard` — apply these edits:

```python
        # datasets: the analysis one, then the explanation one
        analysis, explanation = superset.rows["dataset"].values()
        assert (analysis["id"], explanation["id"]) == (10, 11)
        assert analysis["table_name"] == "demand_forecast_analysis"
        assert analysis["sql"] == DEMAND_DATASET_SQL
        assert analysis["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in analysis["columns"]] == (
            DEMAND_COLUMNS
        )
        assert explanation["table_name"] == "demand_forecast_explanation"
        assert explanation["sql"] == DEMAND_EXPLANATION_SQL
        assert explanation["main_dttm_col"] == "trade_datetime"
        assert [(c["column_name"], c["type"], c["is_dttm"]) for c in explanation["columns"]] == (
            DEMAND_EXPLANATION_COLUMNS
        )
        dataset_puts = [
            c
            for c in superset.calls
            if c[0] == "PUT" and c[1] in (f"{BASE}/api/v1/dataset/10", f"{BASE}/api/v1/dataset/11")
        ]
        assert [c[3] for c in dataset_puts] == [{"override_columns": "true"}] * 2

        # charts, in creation order: the 19 analysis charts, then the 7 explanation charts
        charts = list(superset.rows["chart"].values())
        assert [c["slice_name"] for c in charts] == (
            EXPECTED_DEMAND_CHART_NAMES + EXPLANATION_CHART_NAMES
        )
        assert [c["id"] for c in charts] == list(range(12, 38))
        for c in charts[:19]:
            assert c["datasource_id"] == 10
            assert json.loads(c["params"])["datasource"] == "10__table"
        for c in charts[19:]:
            assert c["datasource_id"] == 11
            assert json.loads(c["params"])["datasource"] == "11__table"
        for c in charts:
            assert c["datasource_type"] == "table"
            assert json.loads(c["params"])["viz_type"] == c["viz_type"]
```

then keep the `by_name` assertions and add:

```python
        assert by_name["Base value"]["viz_type"] == "big_number_total"
        assert by_name["Base value"]["metric"] == demand.base_value_metric
        assert by_name["Base value"]["subheader"] == "MWh; model expected value, mean per period"
        assert by_name["Forecast (selection)"]["metric"] == script.avg_metric(
            "forecast_demand_mwh", "Forecast"
        )
        assert by_name["Forecast (selection)"]["subheader"] == "MWh; mean per period"
        assert by_name["Actual (selection)"]["metric"] == script.avg_metric(
            "actual_demand_mwh", "Actual"
        )
        assert by_name["Net feature effect"]["metric"] == demand.net_effect_metric
        assert by_name["Net feature effect"]["subheader"] == "MWh; forecast − base"
        assert by_name["Net feature effect"]["y_axis_format"] == "+,.1f"
        assert by_name["SHAP waterfall"]["viz_type"] == "waterfall"
        assert by_name["Feature values & contributions"]["viz_type"] == "table"
        assert by_name["Contributions by period"]["stack"] == "Stack"
```

dashboard block edits:

```python
        (dashboard,) = superset.rows["dashboard"].values()
        assert dashboard_id == dashboard["id"] == 38
        ...
        run_filter, day_filter, period_filter = metadata["native_filter_configuration"]
        assert run_filter["targets"] == [{"column": {"name": "run_label"}, "datasetId": 10}]
        leaderboard_id = superset.id_of("chart", "slice_name", "Run leaderboard")
        assert leaderboard_id == 28
        assert run_filter["scope"]["excluded"] == [leaderboard_id]
        assert run_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LABEL]
        assert run_filter["controlValues"]["defaultToFirstItem"] is False
        # Day / Period apply to the explanation section only
        analysis_ids = list(range(12, 31))
        assert day_filter["targets"] == [{"column": {"name": "trade_date_label"}, "datasetId": 11}]
        assert day_filter["scope"]["excluded"] == analysis_ids
        assert day_filter["cascadeParentIds"] == ["NATIVE_FILTER-run"]
        assert day_filter["defaultDataMask"]["filterState"]["value"] == [DEFAULT_LAST_DAY]
        assert period_filter["targets"] == [{"column": {"name": "period_label"}, "datasetId": 11}]
        assert period_filter["scope"]["excluded"] == [*analysis_ids, 37]  # + Contributions by period
        assert period_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}

        position = json.loads(dashboard["position_json"])
        assert position["HEADER_ID"]["meta"]["text"] == "Demand Forecast Analysis"
        chart_keys = sorted(k for k in position if k.startswith("CHART-"))
        assert chart_keys == sorted(f"CHART-{i}" for i in range(12, 38))
        assert position["GRID_ID"]["children"] == EXPECTED_GRID_CHILDREN
        assert [
            position[h]["meta"]["text"] for h in ("HEADER-1", "HEADER-2", "HEADER-3", "HEADER-4")
        ] == ["Error structure", "Calibration & distribution", "Runs & drilldown", "Explanation (SHAP)"]
        assert position["ROW-0-0"]["children"] == [f"CHART-{i}" for i in range(12, 18)]
        assert position["CHART-12"]["meta"] == {
            "chartId": 12, "width": 2, "height": 24, "sliceName": "Overall MAE",
        }
        assert position["ROW-2-0"]["children"] == ["CHART-25", "CHART-26"]
        assert position["CHART-25"]["meta"]["sliceName"] == "MAE by actual demand band"
        assert position["CHART-25"]["meta"]["width"] == 5
        assert position["CHART-26"]["meta"]["width"] == 7
        assert position["ROW-3-0"]["children"] == [f"CHART-{leaderboard_id}"]
        assert position["CHART-30"]["meta"]["height"] == 60  # 30-min detail
        assert position["ROW-4-0"]["children"] == [f"CHART-{i}" for i in range(31, 35)]
        assert position["CHART-31"]["meta"] == {
            "chartId": 31, "width": 3, "height": 24, "sliceName": "Base value",
        }
        assert position["ROW-4-1"]["children"] == ["CHART-35", "CHART-36"]
        assert (position["CHART-35"]["meta"]["width"], position["CHART-35"]["meta"]["height"]) == (8, 46)
        assert (position["CHART-36"]["meta"]["width"], position["CHART-36"]["meta"]["height"]) == (4, 46)
        assert position["ROW-4-2"]["children"] == ["CHART-37"]
        assert position["CHART-37"]["meta"]["height"] == 44

        assert all(c["dashboards"] == [38] for c in charts)
        assert method_counts(superset.calls, "dataset") == {"GET": 2, "POST": 2, "PUT": 2}
        assert method_counts(superset.calls, "chart") == {"GET": 26, "POST": 26, "PUT": 26}
        assert method_counts(superset.calls, "dashboard") == {"GET": 1, "POST": 1, "PUT": 1}
```

`test_spot_price_dashboard_keeps_its_names_layout_and_formats`: `analysis, explanation = superset.rows["dataset"].values()`; `analysis["sql"] == SPOT_DATASET_SQL`; `explanation["table_name"] == "spot_price_forecast_explanation"`; `explanation["sql"] == SPOT_EXPLANATION_SQL`; chart names `EXPECTED_SPOT_CHART_NAMES + EXPLANATION_CHART_NAMES`; add `by_name["Net feature effect"]["y_axis_format"] == "+,.3f"` and `by_name["SHAP waterfall"]["y_axis_format"] == ",.2f"`; `position["ROW-3-0"]["children"] == ["CHART-28"]`; `run_filter, _, _ = …native_filter_configuration`; `run_filter["scope"]["excluded"] == [28]`.

`test_two_dashboards_coexist_with_their_own_datasets_and_charts`:

```python
        assert (spot_id, demand_id) == (38, 67)  # 2 datasets + 26 charts + dashboard, twice
        spot_ds = superset.id_of("dataset", "table_name", "spot_price_forecast_analysis")
        spot_ex = superset.id_of("dataset", "table_name", "spot_price_forecast_explanation")
        demand_ds = superset.id_of("dataset", "table_name", "demand_forecast_analysis")
        demand_ex = superset.id_of("dataset", "table_name", "demand_forecast_explanation")
        assert (spot_ds, spot_ex, demand_ds, demand_ex) == (10, 11, 39, 40)
        assert len(superset.rows["chart"]) == 52
        assert [c["slice_name"] for c in charts_of(superset, spot_ds)] == EXPECTED_SPOT_CHART_NAMES
        assert [c["slice_name"] for c in charts_of(superset, spot_ex)] == EXPLANATION_CHART_NAMES
        assert [c["slice_name"] for c in charts_of(superset, demand_ds)] == (
            EXPECTED_DEMAND_CHART_NAMES
        )
        assert [c["slice_name"] for c in charts_of(superset, demand_ex)] == EXPLANATION_CHART_NAMES
        for dashboard_id, dataset_ids in ((spot_id, (spot_ds, spot_ex)), (demand_id, (demand_ds, demand_ex))):
            for dataset_id in dataset_ids:
                assert all(c["dashboards"] == [dashboard_id] for c in charts_of(superset, dataset_id))
            metadata = json.loads(superset.rows["dashboard"][dashboard_id]["json_metadata"])
            run_filter, day_filter, period_filter = metadata["native_filter_configuration"]
            assert run_filter["targets"][0]["datasetId"] == dataset_ids[0]
            assert day_filter["targets"][0]["datasetId"] == dataset_ids[1]
            assert period_filter["targets"][0]["datasetId"] == dataset_ids[1]
            position = json.loads(superset.rows["dashboard"][dashboard_id]["position_json"])
            chart_ids = sorted(int(k[6:]) for k in position if k.startswith("CHART-"))
            assert chart_ids == sorted(
                c["id"] for d in dataset_ids for c in charts_of(superset, d)
            )
```

`test_second_build_is_idempotent_and_rewrites_metadata`: `dataset {"GET": 2, "PUT": 2}`, `chart {"GET": 26, "PUT": 52}`; `run_filter, day_filter, _ = …`; keep the Run assertions with `[28]`; add `assert day_filter["defaultDataMask"] == {"extraFormData": {}, "filterState": {}}` and `assert day_filter["controlValues"]["defaultToFirstItem"] is True`; `all(c["dashboards"] == [38] …)`.

`TestMain`: `test_builds_every_dashboard_by_default` dataset names → `["spot_price_forecast_analysis", "spot_price_forecast_explanation", "demand_forecast_analysis", "demand_forecast_explanation"]`, `len(charts) == 52`, chart counts `{"GET": 52, "POST": 52, "PUT": 52}`; `test_task_flag_selects_one_dashboard` datasets → `["demand_forecast_analysis", "demand_forecast_explanation"]`, names → `EXPECTED_DEMAND_CHART_NAMES + EXPLANATION_CHART_NAMES`; `test_env_derived_defaults` → `len(charts) == 52`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov`
Expected: `TestLatestRun`, `TestBuildNativeFilters`, `TestBuildDashboard`, `TestMain` fail (missing `latest_run`, old filter signature, one dataset / 19 charts).

- [ ] **Step 3: Implement**

Replace `latest_run_label` with:

```python
def latest_run(
    client: SupersetClient, database_id: int, spec: DashboardSpec
) -> tuple[str, str] | None:
    """Newest run's label and its last delivery day, for the filters' on-load defaults.

    ``defaultToFirstItem`` only stages a value (charts render unfiltered until
    Apply is clicked); an explicit default applies on page load. The label
    must be built exactly like the datasets' ``run_label``; the day like the
    explanation dataset's ``trade_date_label``.

    Parameters
    ----------
    client : SupersetClient
    database_id : int
    spec : DashboardSpec

    Returns
    -------
    tuple of (str, str) or None
        ``(run_label, last_day)``; None when the query fails or the mart is
        empty (both filters then fall back to ``defaultToFirstItem``).
    """
    sql = f"""\
select
  concat(
    date_format(f.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', substring(f.run_id, 1, 8)
  ) as run_label,
  date_format(max(f.date_key), 'yyyy-MM-dd') as last_day
from {spec.accuracy_table} f
join pma_curated.dim_area a on f.area_key = a.area_key
group by f.run_id, f.published_at, a.area_code
order by f.published_at desc
limit 1
"""
    try:
        result = client._post_json(
            "/api/v1/sqllab/execute/",
            {"database_id": database_id, "sql": sql, "runAsync": False},
        )
        row = result["data"][0]
        return row["run_label"], row["last_day"]
    except (requests.HTTPError, KeyError, IndexError):
        return None
```

Replace `build_native_filters` with a helper plus the three filters:

```python
def _select_filter(
    filter_id: str,
    name: str,
    column: str,
    dataset_id: int,
    *,
    excluded: list[int],
    default: str | None,
    default_to_first: bool,
    required: bool,
    sort_ascending: bool,
    cascade_parent_ids: list[str],
    description: str,
) -> dict:
    """One single-select native filter on ``column`` of ``dataset_id``.

    Parameters
    ----------
    filter_id, name : str
        Stable id (``NATIVE_FILTER-…``) and the label shown in the filter bar.
    column : str
        Dataset column the filter reads its values from and filters on; a
        chart on another dataset is filtered too when that dataset has a
        column of the same name.
    dataset_id : int
    excluded : list of int
        Charts the filter must NOT apply to.
    default : str or None
        Explicit on-load value; None falls back to ``default_to_first``.
    default_to_first : bool
        Stage the first option when there is no explicit default.
    required : bool
        Whether a value must be selected (``enableEmptyFilter``).
    sort_ascending : bool
    cascade_parent_ids : list of str
        Filters whose selection restricts this filter's options.
    description : str

    Returns
    -------
    dict
    """
    default_mask: dict[str, Any]
    if default is None:
        default_mask = {"extraFormData": {}, "filterState": {}}
    else:
        default_mask = {
            "extraFormData": {"filters": [{"col": column, "op": "IN", "val": [default]}]},
            "filterState": {"value": [default], "label": default},
        }
    return {
        "id": filter_id,
        "name": name,
        "filterType": "filter_select",
        "targets": [{"column": {"name": column}, "datasetId": dataset_id}],
        "defaultDataMask": default_mask,
        "controlValues": {
            "multiSelect": False,
            "enableEmptyFilter": required,
            "defaultToFirstItem": default is None and default_to_first,
            "inverseSelection": False,
            "searchAllOptions": False,
            "sortAscending": sort_ascending,
        },
        "cascadeParentIds": cascade_parent_ids,
        "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
        "type": "NATIVE_FILTER",
        "description": description,
    }


def build_native_filters(
    dataset_id: int,
    run_excluded: list[int],
    default_run_label: str | None,
    explanation_dataset_id: int,
    day_excluded: list[int],
    period_excluded: list[int],
    default_day_label: str | None,
) -> list[dict]:
    """Native filter configuration: Run (whole dashboard), then Day and Period
    (the Explanation section only).

    Parameters
    ----------
    dataset_id : int
        Analysis dataset the Run filter reads ``run_label`` from.
    run_excluded : list of int
        Charts the Run filter must NOT apply to (the cross-run leaderboard).
    default_run_label : str or None
        Explicit on-load run; None falls back to ``defaultToFirstItem``.
    explanation_dataset_id : int
        Explanation dataset the Day / Period filters read their values from.
    day_excluded, period_excluded : list of int
        Charts outside each filter's scope (everything but the explanation
        section; the Period filter also skips "Contributions by period").
    default_day_label : str or None
        Explicit on-load day (the default run's last delivery day).

    Returns
    -------
    list of dict
    """
    return [
        _select_filter(
            "NATIVE_FILTER-run", "Run", "run_label", dataset_id,
            excluded=run_excluded, default=default_run_label, default_to_first=True,
            required=True, sort_ascending=False, cascade_parent_ids=[],
            description="published_at | area | run_id prefix (newest first)",
        ),
        _select_filter(
            "NATIVE_FILTER-day", "Day", "trade_date_label", explanation_dataset_id,
            excluded=day_excluded, default=default_day_label, default_to_first=True,
            required=False, sort_ascending=False, cascade_parent_ids=["NATIVE_FILTER-run"],
            description="Delivery day explained (empty = the run's mean decomposition)",
        ),
        _select_filter(
            "NATIVE_FILTER-period", "Period", "period_label", explanation_dataset_id,
            excluded=period_excluded, default=None, default_to_first=False,
            required=False, sort_ascending=True, cascade_parent_ids=[],
            description="30-minute period of the day (empty = the whole day, mean per period)",
        ),
    ]
```

In `build_dashboard`: create both datasets first, let `chart()` take the dataset, add the explanation charts, the section, and the filters:

```python
    dataset_id = upsert_dataset(
        client, database_id, spec.dataset_name, spec.dataset_sql, spec.dataset_columns
    )
    logger.info("dataset {}: id={}", spec.dataset_name, dataset_id)
    explanation_id = upsert_dataset(
        client,
        database_id,
        spec.explanation_dataset_name,
        spec.explanation_dataset_sql,
        spec.explanation_dataset_columns,
    )
    logger.info("dataset {}: id={}", spec.explanation_dataset_name, explanation_id)

    def chart(name: str, params: dict, on: int = dataset_id) -> int:
        chart_id = upsert_chart(client, name, on, params)
        logger.info("chart {}: {}", chart_id, name)
        return chart_id
```

after the `detail` chart:

```python
    # Explanation (SHAP): the contribution fact, filtered by Run + Day (+ Period)
    per_period = f"{unit}; mean per period"
    kpi_base = chart(
        "Base value",
        big_number_params(
            explanation_id, spec.base_value_metric, f"{unit}; model expected value, mean per period", fmt
        ),
        explanation_id,
    )
    kpi_forecast = chart(
        "Forecast (selection)",
        big_number_params(explanation_id, avg_metric(spec.forecast_col, "Forecast"), per_period, fmt),
        explanation_id,
    )
    kpi_actual = chart(
        "Actual (selection)",
        big_number_params(explanation_id, avg_metric(spec.actual_col, "Actual"), per_period, fmt),
        explanation_id,
    )
    kpi_net = chart(
        "Net feature effect",
        big_number_params(
            explanation_id, spec.net_effect_metric, f"{unit}; forecast − base", spec.signed_number_format
        ),
        explanation_id,
    )
    waterfall = chart("SHAP waterfall", waterfall_params(spec, explanation_id), explanation_id)
    feature_table = chart(
        "Feature values & contributions", feature_table_params(spec, explanation_id), explanation_id
    )
    by_period = chart(
        "Contributions by period", contribution_by_period_params(spec, explanation_id), explanation_id
    )
```

append to `sections`:

```python
        {
            "header": "Explanation (SHAP)",
            "rows": [
                [
                    (kpi_base, "Base value", 3, 24),
                    (kpi_forecast, "Forecast (selection)", 3, 24),
                    (kpi_actual, "Actual (selection)", 3, 24),
                    (kpi_net, "Net feature effect", 3, 24),
                ],
                [
                    (waterfall, "SHAP waterfall", 8, 46),
                    (feature_table, "Feature values & contributions", 4, 46),
                ],
                [(by_period, "Contributions by period", 12, 44)],
            ],
        },
```

and replace the tail of the function (from `all_charts = [` to `attach_charts`) with:

```python
    analysis_charts = [
        kpi_mae, kpi_bias, kpi_rmse, kpi_ratio, kpi_wape, kpi_p90,
        mae_year, mae_tc, heat_tc, heat_month, mae_dow, mae_daypart, mae_daytype,
        mae_band, calibration, histogram, leaderboard, worst_days, detail,
    ]
    explanation_charts = [
        kpi_base, kpi_forecast, kpi_actual, kpi_net, waterfall, feature_table, by_period,
    ]
    all_charts = [*analysis_charts, *explanation_charts]

    latest = latest_run(client, database_id, spec)
    default_run, default_day = (None, None) if latest is None else latest
    logger.info("default run: {} (last day: {})", default_run, default_day)
    dashboard_id = upsert_dashboard(
        client,
        spec,
        build_position_json(spec, sections),
        build_native_filters(
            dataset_id,
            run_excluded=[leaderboard],
            default_run_label=default_run,
            explanation_dataset_id=explanation_id,
            day_excluded=analysis_charts,
            period_excluded=[*analysis_charts, by_period],
            default_day_label=default_day,
        ),
    )
    attach_charts(client, dashboard_id, all_charts)
```

Update the module docstring bullets: two virtual datasets per dashboard (`<task>_forecast_analysis`, `<task>_forecast_explanation` — the contribution fact, one row per period × component, joined to the accuracy mart); a fifth section "Explanation (SHAP)" (base / forecast / actual / net-effect tiles, the waterfall of mean per-period feature contributions, the component table, stacked contributions by period); the Run filter plus Day and Period filters scoped to that section (Day cascades from Run; both optional).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov`
Expected: all pass. Then `just lint && just mypy && just test -q` — coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "dashboard: Explanation (SHAP) section with Day / Period filters

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13 (optional): Worst-days cross-filter scoped to the explanation section

Keep this task only if it behaves in the live dashboard (Task 14, Step 4); otherwise revert its commit. Known caveats: a cross-filter ANDs with the Day native filter (disagreeing selections show no data until one is cleared), and the value a table emits for a `DATE` cell must filter `date_key` correctly.

**Files:**
- Modify: `scripts/create_forecast_dashboard.py` (`upsert_dashboard`, `build_dashboard`; new `build_chart_configuration`)
- Test: `tests/test_create_forecast_dashboard.py` (`TestUpsertDashboard`, `TestBuildDashboard`)

**Interfaces:**
- Produces: `build_chart_configuration(worst_days: int, in_scope: list[int], excluded: list[int]) -> dict`; `upsert_dashboard(client, spec, position, native_filters, chart_configuration)` (new last parameter, written to `json_metadata["chart_configuration"]`).

- [ ] **Step 1: Write the failing tests**

Append to `class TestBuildDashboard`:

```python
    def test_worst_days_cross_filter_is_scoped_to_the_explanation_section(self, script, superset, demand):
        client = make_client(script, superset)
        script.build_dashboard(client, 3, demand)
        (dashboard,) = superset.rows["dashboard"].values()
        configuration = json.loads(dashboard["json_metadata"])["chart_configuration"]
        worst_days = superset.id_of("chart", "slice_name", "Worst days")
        detail = superset.id_of("chart", "slice_name", "Forecast vs actual (30-min detail)")
        assert list(configuration) == [str(worst_days)]
        entry = configuration[str(worst_days)]
        assert entry["id"] == worst_days
        assert entry["crossFilters"]["chartsInScope"] == [detail, *range(31, 38)]
        assert entry["crossFilters"]["scope"] == {
            "rootPath": ["ROOT_ID"],
            "excluded": [i for i in range(12, 31) if i != detail],
        }
```

In `TestUpsertDashboard`, pass `{}` as the new fifth argument in both tests and assert `metadata["chart_configuration"] == {}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov -k "cross_filter or UpsertDashboard"`
Expected: FAIL (`chart_configuration == {}` / TypeError on the new parameter).

- [ ] **Step 3: Implement**

```python
def build_chart_configuration(worst_days: int, in_scope: list[int], excluded: list[int]) -> dict:
    """Per-chart cross-filter scopes: clicking a Worst-days row selects that day
    in the explanation section (and the 30-minute detail chart) only.

    Parameters
    ----------
    worst_days : int
        The Worst-days table's chart id (the emitter).
    in_scope : list of int
        Charts that receive its cross-filter.
    excluded : list of int
        Every other chart on the dashboard.

    Returns
    -------
    dict
        ``json_metadata["chart_configuration"]``.
    """
    return {
        str(worst_days): {
            "id": worst_days,
            "crossFilters": {
                "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
                "chartsInScope": in_scope,
            },
        }
    }
```

`upsert_dashboard` gains `chart_configuration: dict` (documented: "per-chart cross-filter scopes from `build_chart_configuration`") and writes it as `"chart_configuration": chart_configuration`. In `build_dashboard`:

```python
    cross_filter_targets = [detail, *explanation_charts]
    chart_configuration = build_chart_configuration(
        worst_days,
        in_scope=cross_filter_targets,
        excluded=[c for c in analysis_charts if c != detail],
    )
```

and pass it as the last argument of `upsert_dashboard`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -p no:cacheprovider --no-cov`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "dashboard: scope the Worst-days cross-filter to the explanation section

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: Live verification in Superset

No code. Rebuild the dashboards against the runs of Task 7 and check the section renders.

**Files:** none (a screenshot saved under the session scratchpad, not the repo).

- [ ] **Step 1: Rebuild both dashboards**

Run: `just python scripts/create_forecast_dashboard.py`
Expected: for each task, log lines for two datasets (`…_forecast_analysis`, `…_forecast_explanation`), 26 charts, `default run: <label> (last day: <yyyy-MM-dd>)` and the dashboard URL.

- [ ] **Step 2: Open the Demand dashboard**

With the Playwright MCP tools: navigate to `http://localhost:8088/login/`, log in (`admin` / the `SUPERSET_ADMIN_PASSWORD` in `.env`, default `admin`), then open `http://localhost:8088/superset/dashboard/demand-forecast-analysis/`. Expected on load: the filter bar shows **Run** (newest run), **Day** (that run's last day) and **Period** (empty); the top sections unchanged; the last section `Explanation (SHAP)` shows four tiles with numbers, the waterfall with one bar per feature (7 for `lightgbm_msm_popw_daytype`) plus a `Net effect` total, the component table (8 rows incl. `00 base`), and the stacked by-period bars over time codes 1–48.

- [ ] **Step 3: Check the arithmetic and the filters**

- Tiles: `Base value + Net feature effect == Forecast (selection)` (±1 MWh); the waterfall's Total equals `Net feature effect`.
- Pick a **Period** (e.g. `18:00-18:30`): the tiles and the waterfall change to that period's values; `Contributions by period` still shows all 48 periods; the sections above are unchanged.
- Clear **Day**: the section shows the run-wide means; the table's contribution column still sums to the forecast tile.
- Take a screenshot of the section with a day and a period selected → `<scratchpad>/explanation-section.png` and look at it: bars readable, labels in feature order (`01 time_code` … `07 day_type`), no `NaN`/`null` in the tiles.

- [ ] **Step 4: Check the optional cross-filter (Task 13)**

Click a date cell in **Worst days**. Expected: the explanation section and the 30-minute detail chart switch to that day (the filter bar shows the cross-filter); the KPI/error sections do not change. If instead the section empties, errors, or the top sections change, revert Task 13 (`git revert <its commit>`) and note it in the PR.

- [ ] **Step 5: Spot dashboard smoke check**

Open `http://localhost:8088/superset/dashboard/spot-price-forecast-analysis/`, select the `lightgbm` run of Task 7 in **Run**: the section renders in JPY/kWh with the spot features. (`previous_day` runs show an empty section — expected.)

Nothing to commit.

---

### Task 15: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/research/demand/README.md`, `docs/research/spot_price/README.md`

- [ ] **Step 1: CLAUDE.md**

Command bullet `just python scripts/create_forecast_dashboard.py …` — append:

> Each dashboard has two virtual datasets — `<task>_forecast_analysis` (the accuracy mart) and `<task>_forecast_explanation` (`fct_<task>_forecast_contribution` joined to the accuracy mart: one row per period × component, so AVG-only metrics) — and ends with an **Explanation (SHAP)** section: **Day** / **Period** native filters (scoped to that section; Day cascades from Run, defaults to the default run's last day; Period empty = the whole day, mean per period) drive base / forecast / actual / net-effect tiles, a `waterfall` of the mean per-period feature contributions (the base is a tile, not a bar: Superset's value axis always includes zero; bars sort by label, hence the `00 base`, `01 time_code`… `component_label` prefix), the component table and stacked contributions by period. Runs published before 2026-08-26 have no contributions and show an empty section until re-run.

Architecture, after the "Forecast write-back" bullet — new bullet:

> - Explanations: every `SlidingWindowLightGbmStrategy` records exact TreeSHAP values per predicted row; `strategy.contributions()` (`None` for strategies with nothing to attribute — spot `previous_day`) melts them into `ForecastContributions` (one row per period × component: `base` = the expected value, `component_order` 0, then the features in `feature_cols` order; per period `base + Σ features = forecast`; `feature_value` = the feature as the model saw it, null on the base row) and the backtest scripts publish them right after the forecasts (`publish.build_contribution_records` aligned to the scored periods, the forecast rows' `published_at` reused so `run_label` matches; `publish_contribution_records` → `pma_ml.<task>_forecast_contribution` = `TaskSpec.contribution_table`, column `TaskSpec.contribution_col` = `contribution_demand_kwh` / `contribution_price_jpy_kwh`; MLflow tag `contribution_table`) → `stg/std_ml__<task>_forecast_contribution` (+ `trade_datetime`, `is_base`) → `fct_<task>_forecast_contribution` (grain run × period × area × component; singular tests: one base row per period, Σ contributions = the forecast within 1e-6) → Superset dataset `<task>_forecast_explanation`.

Demand task paragraph — after "Write-back: `pma_ml.demand_forecast` → … → Superset **Demand Forecast Analysis** dashboard (…)", add: "; contributions to `pma_ml.demand_forecast_contribution` → `fct_demand_forecast_contribution` → the dashboard's Explanation (SHAP) section".

- [ ] **Step 2: Research READMEs**

`docs/research/demand/README.md`, "Segments reported by the tooling" bullet — extend the Superset parenthesis: "…, error histogram, and the per-day / per-period SHAP waterfall of the **Explanation (SHAP)** section (Day / Period filters)". Same sentence in `docs/research/spot_price/README.md` after "the calibration curve".

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/research/demand/README.md docs/research/spot_price/README.md
git commit -m "docs: SHAP explanation section, contribution write-back

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 16: Finish the branch

- [ ] **Step 1: Full verification**

Run: `just test -q && just lint && just mypy && just checkov`
Expected: pytest green with coverage 100 %, ruff/mypy/checkov clean.

Run: `just dbt build`
Expected: every model and test passes, including the four new singular tests.

- [ ] **Step 2: Rollout runs (spec §8)**

Re-run the kept demand baseline over the R-003 window so the production dashboard has a fully explained run: read the `--start-date` / `--end-date` / `--train-start` of the R-003 E-001 candidate run in `docs/research/demand/R-003-day-type-feature.md` and run `just python scripts/demand_backtest.py --area tokyo --start-date <s> --end-date <e> [--train-start <t>]` as a **main-session background task** (a 729-day run takes tens of minutes); then `just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution` and `just python scripts/create_forecast_dashboard.py --task demand`. Same for the spot `lightgbm` baseline if a comparable window is documented in `docs/research/spot_price/README.md`; otherwise `--days 365`.

- [ ] **Step 3: Integrate**

Use the `superpowers:finishing-a-development-branch` skill: open a PR from `shap-explanation-dashboard` to `main` titled "SHAP explanation section for the forecast dashboards", body summarising the spec (capture → write-back → dbt → dashboard), the verification commands run, the screenshot's findings, and whether Task 13 was kept.

---

## Self-review notes

- Spec coverage: §1 → Tasks 1–4; §2 → Tasks 5–6; §3 → Tasks 8–9 (after Task 7 materialises the sources); §4 → Task 10; §5.1 → Tasks 11–12; §5.2 → Task 12; §5.3 → Task 13; §6 → every task's test steps + Task 14 + Task 16; §7 → Task 15; §8 → Task 16 Step 2.
- Names used across tasks: `contributions()` (Tasks 3, 4, 6), `build_contribution_records` / `publish_contribution_records` (Tasks 5, 6), `contribution_table` / `contribution_col` (Tasks 2, 5, 6, 8, 9), `explanation_dataset_*` / `contribution_metric` / `base_value_metric` / `net_effect_metric` (Tasks 10–12), `NOT_BASE_FILTER` / `waterfall_params` / `feature_table_params` / `contribution_by_period_params` (Tasks 11–12), `latest_run` / `build_native_filters` (Task 12), `build_chart_configuration` (Task 13).
