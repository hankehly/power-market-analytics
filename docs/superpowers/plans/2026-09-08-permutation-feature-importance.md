# Permutation Feature Importance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute the walk-forward permutation feature importance of every LightGBM backtest run with scikit-learn, publish it to the warehouse, log it to MLflow, and show it in a "Feature importance" section on both Superset dashboards.

**Architecture:** A new pure module `forecasting/importance.py` wraps the run's refit boosters in a scikit-learn estimator that routes each row to the model that forecast its day, and hands it to `sklearn.inspection.permutation_importance`. The LightGBM base keeps every refit and exposes an optional `permutation_importance(run, …)` hook (like `contributions()`); the backtest scripts publish the result to `pma_ml.<task>_forecast_importance` and log a CSV + plot; dbt models it into `fct_<task>_forecast_importance` (stg → fct, with a reconciliation test against the accuracy mart); the dashboard builder adds a fifth virtual dataset and a section at the bottom of the Explanation tab.

**Tech Stack:** Python 3.13, pandas, numpy, scikit-learn 1.9 (`sklearn.inspection.permutation_importance`, `BaseEstimator`, `RegressorMixin`), LightGBM, matplotlib, MLflow, PySpark (publish), dbt-spark, Superset 6.1 REST API, pytest with a local Spark fixture.

**Spec:** `docs/superpowers/specs/2026-09-08-permutation-feature-importance-design.md`

## Global Constraints

- scikit-learn: already installed (1.9.0, transitively); declare it in `pyproject.toml` `dependencies` as `"scikit-learn>=1.9"` and add `"sklearn.*"` to the mypy `ignore_missing_imports` override list next to `"shap.*"`.
- Five repeats, seed 0 by default; the script flag is `--importance-repeats` (default 5); the seed is fixed at 0 and logged as `permutation_seed`.
- Baseline MAE = the run's own MAE on the rows the run scored (`run.result`), so the importance rows reconcile with the accuracy mart.
- Warehouse grain: run × area × feature × repeat; measures `n_periods`, `mae`, `permuted_mae` (raw frame names) written as the task's unit-suffixed columns (`mae_demand_kwh` / `permuted_mae_demand_kwh`, `mae_price_jpy_kwh` / `permuted_mae_price_jpy_kwh`).
- Table names: `pma_ml.<task>_forecast_importance`, `stg_ml__<task>_forecast_importance`, `fct_<task>_forecast_importance`; no `std` model.
- MLflow artifacts: `permutation_importance.csv`, `permutation_importance_repeats.csv`, `permutation_importance_plot.png`; params `permutation_repeats`, `permutation_seed`; tag `importance_table`. No per-feature metrics.
- Dashboard: dataset `<task>_forecast_importance` (`main_dttm_col` = `published_at`); the Explanation tab is renamed "Explanation"; its new section holds three charts named "Permutation importance", "Mean |SHAP| by feature", "Feature importance table", outside the Day filter and the day tables' cross-filters.
- Every dbt model has an enforced contract and a uniqueness test; test args go under `arguments:`.
- Coverage gate 100 % (`just test`); `just lint`, `just mypy` clean; NumPy-style docstrings; plain-language prose in docs.
- Branch `feature/permutation-feature-importance`; Conventional Commits with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last trailer. Never write the Codex mention anywhere.
- Long-running commands (a full backtest, `just dbt build`) run as main-session background Bash tasks, never from a subagent.
- A PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` file you edit; re-read a file before editing it again.

---

## File structure

Create:
- `power_market_analytics/forecasting/importance.py` — `MODEL_INDEX_COL`, `DEFAULT_N_REPEATS`, `DEFAULT_SEED`, `WalkForwardPredictor`, `permutation_importance()`.
- `tests/test_forecasting_importance.py`.
- `dbt/models/staging/stg_ml__demand_forecast_importance.{sql,yml}`, `dbt/models/curated/fct_demand_forecast_importance.{sql,yml}`, `dbt/dbt_tests/assert_fct_demand_forecast_importance_reconciles_with_accuracy.sql`, and the same three for `spot_price`.

Modify:
- `power_market_analytics/forecasting/frames.py` — `PermutationImportance` (+ `summary()`), `PermutationImportanceSummary`, `ForecastImportanceRecords`.
- `power_market_analytics/forecasting/task.py` — `importance_table`, `mae_col`, `permuted_mae_col`.
- `power_market_analytics/forecasting/strategy.py` — `permutation_importance()` hook, default `None`.
- `power_market_analytics/forecasting/lgbm.py` — keep every refit (`_models`, `_model_of_day`), implement the hook.
- `power_market_analytics/forecasting/publish.py` — `build_importance_records`, `publish_importance_records`.
- `power_market_analytics/forecasting/plots.py` — `permutation_importance_plot()`.
- `scripts/demand_backtest.py`, `scripts/spot_price_backtest.py` — the flag, publish, artifacts.
- `scripts/create_forecast_dashboard.py` — template, spec fields, metrics, builders, section, tab rename, `main_dttm_col`.
- `dbt/models/raw/ml.yml` — two sources.
- `pyproject.toml` — dependency + mypy override.
- `CLAUDE.md`, `docs/README.md`, `docs/research/demand/README.md`, `docs/research/spot_price/README.md`.
- Tests: `tests/test_forecasting_frames.py`, `tests/test_forecasting_task.py`, `tests/test_forecasting_strategy.py`, `tests/test_forecasting_lgbm.py`, `tests/test_spot_price_lgbm.py`, `tests/test_forecasting_publish.py`, `tests/test_forecasting_plots.py`, `tests/test_demand_scripts.py`, `tests/test_spot_price_scripts.py`, `tests/test_create_forecast_dashboard.py`.

Chart / dataset creation order after this change (the dashboard tests pin ids): datasets 10 analysis, 11 explanation, 12 comparison, 13 explanation-comparison, 14 importance; charts 15–33 Accuracy (19), 34–40 Explanation (7), 41 "Permutation importance" (dataset 14), 42 "Mean |SHAP| by feature" (dataset 11), 43 "Feature importance table" (dataset 14), 44–66 Compare (23), 67–71 explanation vs baseline (5); dashboard 72.

---

### Task 1: Branch, spec commit, frames

**Files:**
- Modify: `power_market_analytics/forecasting/frames.py` (append after `ForecastContributionRecords`, before `MetricByYearTimeCode`)
- Test: `tests/test_forecasting_frames.py`

**Interfaces:**
- Produces: `PermutationImportance` (grain `feature × repeat_index`; columns `feature`, `feature_order`, `repeat_index`, `n_periods`, `mae`, `permuted_mae`; method `summary() -> PermutationImportanceSummary`), `PermutationImportanceSummary` (one row per feature: `feature`, `feature_order`, `mae`, `permuted_mae`, `importance_mae`, `importance_std`, `importance_pct`, `n_repeats`), `ForecastImportanceRecords` (the importance frame plus `run_id`, `strategy`, `area_code`, `published_at`).

- [ ] **Step 1: Create the branch and commit the spec**

```bash
cd /Users/hankehly/Projects/power-market-analytics
git checkout -b feature/permutation-feature-importance
git add docs/superpowers/specs/2026-09-08-permutation-feature-importance-design.md docs/superpowers/plans/2026-09-08-permutation-feature-importance.md
git commit -m "docs(specs): permutation feature importance design and plan

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 2: Write the failing frame tests**

Append to `tests/test_forecasting_frames.py` (add `PermutationImportance`, `PermutationImportanceSummary`, `ForecastImportanceRecords` to the import from `power_market_analytics.forecasting.frames`):

```python
def importance_df() -> pd.DataFrame:
    """Two features x two repeats over 96 scored periods; MAE 10; ``x`` hurts more."""
    rows = []
    for order, (feature, deltas) in enumerate(
        [("x", [4.0, 6.0]), ("time_code", [0.0, 0.5])], start=1
    ):
        for repeat, delta in enumerate(deltas):
            rows.append(
                {
                    "feature": feature,
                    "feature_order": order,
                    "repeat_index": repeat,
                    "n_periods": 96,
                    "mae": 10.0,
                    "permuted_mae": 10.0 + delta,
                }
            )
    return pd.DataFrame(rows).astype(
        {"feature_order": "int64", "repeat_index": "int64", "n_periods": "int64"}
    )


class TestPermutationImportance:
    def test_grain_and_schema(self):
        assert PermutationImportance.keys == ["feature", "repeat_index"]
        assert list(PermutationImportance.schema) == [
            "feature",
            "feature_order",
            "repeat_index",
            "n_periods",
            "mae",
            "permuted_mae",
        ]
        assert PermutationImportance.non_null_cols == [
            "feature_order",
            "n_periods",
            "mae",
            "permuted_mae",
        ]
        assert len(PermutationImportance.from_df(importance_df())) == 4

    def test_n_periods_is_one_positive_value(self):
        df = importance_df()
        df.loc[0, "n_periods"] = 48
        with pytest.raises(ValueError, match=r"n_periods must be one value >= 1, got \[48, 96\]"):
            PermutationImportance.from_df(df)
        df["n_periods"] = 0
        with pytest.raises(ValueError, match=r"n_periods must be one value >= 1, got \[0\]"):
            PermutationImportance.from_df(df)

    def test_mae_is_one_non_negative_value(self):
        df = importance_df()
        df.loc[0, "mae"] = 9.0
        with pytest.raises(ValueError, match=r"mae must be one value >= 0, got \[9\.0, 10\.0\]"):
            PermutationImportance.from_df(df)
        df["mae"] = -1.0
        with pytest.raises(ValueError, match=r"mae must be one value >= 0, got \[-1\.0\]"):
            PermutationImportance.from_df(df)

    def test_permuted_mae_is_non_negative(self):
        df = importance_df()
        df.loc[0, "permuted_mae"] = -0.5
        with pytest.raises(ValueError, match="permuted_mae must be >= 0"):
            PermutationImportance.from_df(df)

    def test_feature_order_is_constant_per_feature_and_runs_1_to_n(self):
        df = importance_df()
        df.loc[0, "feature_order"] = 2
        with pytest.raises(ValueError, match="feature_order must be constant per feature"):
            PermutationImportance.from_df(df)
        df = importance_df()
        df.loc[df["feature"] == "x", "feature_order"] = 3
        with pytest.raises(ValueError, match=r"feature_order must run 1\.\.2, got \[2, 3\]"):
            PermutationImportance.from_df(df)

    def test_every_feature_carries_the_same_repeats(self):
        df = importance_df()
        df = df[~((df["feature"] == "x") & (df["repeat_index"] == 1))]
        with pytest.raises(ValueError, match=r"every feature must carry repeats 0\.\.1"):
            PermutationImportance.from_df(df)

    def test_summary_is_one_row_per_feature_in_model_order(self):
        summary = PermutationImportance.from_df(importance_df()).summary()
        assert isinstance(summary, PermutationImportanceSummary)
        assert list(summary.df.columns) == [
            "feature",
            "feature_order",
            "mae",
            "permuted_mae",
            "importance_mae",
            "importance_std",
            "importance_pct",
            "n_repeats",
        ]
        assert summary.df["feature"].tolist() == ["x", "time_code"]
        assert summary.df["feature_order"].tolist() == [1, 2]
        assert summary.df["mae"].tolist() == [10.0, 10.0]
        assert summary.df["permuted_mae"].tolist() == [15.0, 10.25]
        assert summary.df["importance_mae"].tolist() == [5.0, 0.25]
        # population std over the repeats (ddof 0), as scikit-learn's importances_std
        assert summary.df["importance_std"].tolist() == [1.0, 0.25]
        assert summary.df["importance_pct"].tolist() == [50.0, 2.5]
        assert summary.df["n_repeats"].tolist() == [2, 2]

    def test_summary_pct_is_nan_when_the_mae_is_zero(self):
        df = importance_df()
        df["mae"] = 0.0
        summary = PermutationImportance.from_df(df).summary()
        assert summary.df["importance_pct"].isna().all()
        assert summary.df["importance_mae"].tolist() == [15.0, 10.25]


class TestPermutationImportanceSummary:
    def test_grain_and_schema(self):
        assert PermutationImportanceSummary.keys == ["feature"]
        assert PermutationImportanceSummary.non_null_cols == [
            "feature_order",
            "mae",
            "permuted_mae",
            "importance_mae",
            "importance_std",
            "n_repeats",
        ]


def importance_records_df() -> pd.DataFrame:
    return importance_df().assign(
        run_id="r",
        strategy="s",
        area_code="tokyo",
        published_at=pd.Timestamp("2026-09-08 10:00").as_unit("ns"),
    )


class TestForecastImportanceRecords:
    def test_grain_and_schema(self):
        assert ForecastImportanceRecords.keys == ["run_id", "area_code", "feature", "repeat_index"]
        assert list(ForecastImportanceRecords.schema) == [
            "run_id",
            "strategy",
            "area_code",
            "feature",
            "feature_order",
            "repeat_index",
            "n_periods",
            "mae",
            "permuted_mae",
            "published_at",
        ]
        assert ForecastImportanceRecords.non_null_cols == [
            "strategy",
            "feature_order",
            "n_periods",
            "mae",
            "permuted_mae",
            "published_at",
        ]
        assert len(ForecastImportanceRecords.from_df(importance_records_df())) == 4

    def test_duplicate_feature_repeat_within_a_run_rejected(self):
        df = pd.concat([importance_records_df()] * 2, ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique"):
            ForecastImportanceRecords.from_df(df)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_frames.py -q -k "Importance"`
Expected: FAIL with `ImportError: cannot import name 'PermutationImportance'`

- [ ] **Step 4: Implement the frames**

Add `import numpy as np` to the imports of `power_market_analytics/forecasting/frames.py` and insert after `ForecastContributionRecords`:

```python
class PermutationImportance(DomainFrame):
    """Permutation feature importance of one backtest run, per feature and repeat.

    ``feature_order`` is the feature's 1-based position in the strategy's
    ``feature_cols``. ``n_periods`` (the scored periods) and ``mae`` (the run's
    own MAE on them, the baseline) are the same on every row; ``permuted_mae``
    is the MAE after shuffling the feature's column across those periods, once
    per ``repeat_index``. Both MAEs are in the task's forecast unit.

    Grain: (feature, repeat_index).
    """

    schema = {
        "feature": "object",
        "feature_order": "int64",
        "repeat_index": "int64",
        "n_periods": "int64",
        "mae": "float64",
        "permuted_mae": "float64",
    }
    keys = ["feature", "repeat_index"]
    non_null_cols = ["feature_order", "n_periods", "mae", "permuted_mae"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        name = cls.__name__
        n_periods = sorted(df["n_periods"].unique().tolist())
        if len(n_periods) != 1 or n_periods[0] < 1:
            raise ValueError(f"{name}: n_periods must be one value >= 1, got {n_periods}")
        mae = sorted(df["mae"].unique().tolist())
        if len(mae) != 1 or mae[0] < 0:
            raise ValueError(f"{name}: mae must be one value >= 0, got {mae}")
        if (df["permuted_mae"] < 0).any():
            raise ValueError(f"{name}: permuted_mae must be >= 0")
        orders = df.groupby("feature")["feature_order"]
        if (orders.nunique() != 1).any():
            raise ValueError(f"{name}: feature_order must be constant per feature")
        found = sorted(orders.first().tolist())
        if found != list(range(1, len(found) + 1)):
            raise ValueError(f"{name}: feature_order must run 1..{len(found)}, got {found}")
        expected = tuple(range(int(df["repeat_index"].max()) + 1))
        repeats = df.groupby("feature")["repeat_index"].apply(lambda s: tuple(sorted(s)))
        if any(r != expected for r in repeats):
            raise ValueError(f"{name}: every feature must carry repeats 0..{expected[-1]}")

    def summary(self) -> PermutationImportanceSummary:
        """Collapse the repeats into one row per feature.

        Returns
        -------
        PermutationImportanceSummary
            Sorted by ``feature_order``. ``importance_mae`` is the mean over
            repeats of ``permuted_mae − mae`` (scikit-learn's
            ``importances_mean``), ``importance_std`` its population standard
            deviation (``importances_std``), ``importance_pct`` the mean as a
            percentage of ``mae`` (NaN when ``mae`` is 0).
        """
        mae = float(self.df["mae"].iloc[0])
        grouped = self.df.groupby(["feature", "feature_order"], sort=False)["permuted_mae"]
        out = (
            pd.DataFrame(
                {
                    "permuted_mae": grouped.mean(),
                    "importance_std": grouped.std(ddof=0),
                    "n_repeats": grouped.size(),
                }
            )
            .reset_index()
            .sort_values("feature_order", ignore_index=True)
            .assign(mae=mae)
        )
        out["importance_mae"] = out["permuted_mae"] - mae
        out["importance_pct"] = 100 * out["importance_mae"] / mae if mae > 0 else np.nan
        return PermutationImportanceSummary.from_df(out.astype({"n_repeats": "int64"}))


class PermutationImportanceSummary(DomainFrame):
    """One row per feature: the mean and spread of its permutation importance over the repeats.

    Grain: (feature).
    """

    schema = {
        "feature": "object",
        "feature_order": "int64",
        "mae": "float64",
        "permuted_mae": "float64",
        "importance_mae": "float64",
        "importance_std": "float64",
        "importance_pct": "float64",
        "n_repeats": "int64",
    }
    keys = ["feature"]
    non_null_cols = [
        "feature_order",
        "mae",
        "permuted_mae",
        "importance_mae",
        "importance_std",
        "n_repeats",
    ]


class ForecastImportanceRecords(DomainFrame):
    """One backtest run's permutation importance shaped for the task's importance
    write-back table.

    Grain: (run_id, area_code, feature, repeat_index). The MAE columns keep
    their generic names here; the publisher writes them under the task's
    unit-suffixed ``TaskSpec.mae_col`` / ``TaskSpec.permuted_mae_col``.
    """

    schema = {
        "run_id": "object",
        "strategy": "object",
        "area_code": "object",
        "feature": "object",
        "feature_order": "int64",
        "repeat_index": "int64",
        "n_periods": "int64",
        "mae": "float64",
        "permuted_mae": "float64",
        "published_at": "datetime64[ns]",
    }
    keys = ["run_id", "area_code", "feature", "repeat_index"]
    non_null_cols = ["strategy", "feature_order", "n_periods", "mae", "permuted_mae", "published_at"]
```

`PermutationImportanceSummary` is referenced before its definition inside `summary()`; that is fine at call time (the module has `from __future__ import annotations`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_frames.py -q`
Expected: PASS (all, including the existing ones)

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/forecasting/frames.py tests/test_forecasting_frames.py
git commit -m "feat(forecasting): permutation importance frames

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: TaskSpec derives the importance table and MAE columns

**Files:**
- Modify: `power_market_analytics/forecasting/task.py` (after `contribution_col`)
- Test: `tests/test_forecasting_task.py`

**Interfaces:**
- Produces: `TaskSpec.importance_table` (`"pma_ml.demand_forecast_importance"`), `TaskSpec.mae_col` (`"mae_demand_kwh"` / `"mae_price_jpy_kwh"`), `TaskSpec.permuted_mae_col` (`"permuted_mae_demand_kwh"` / `"permuted_mae_price_jpy_kwh"`).

- [ ] **Step 1: Write the failing test**

Add to `TestTaskSpec` in `tests/test_forecasting_task.py`:

```python
    def test_importance_table_and_mae_columns_derive_from_the_forecast_ones(self):
        spec = make_spec()
        assert spec.importance_table == "pma_ml.load_forecast_importance"
        assert spec.mae_col == "mae_load_mw"
        assert spec.permuted_mae_col == "permuted_mae_load_mw"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_forecasting_task.py -q -k importance`
Expected: FAIL with `AttributeError: 'TaskSpec' object has no attribute 'importance_table'`

- [ ] **Step 3: Implement the properties**

In `power_market_analytics/forecasting/task.py`, after `contribution_col`:

```python
    @property
    def importance_table(self) -> str:
        """Warehouse table the run's permutation feature importance is published to.

        ``forecast_table`` with an ``_importance`` suffix, e.g.
        ``pma_ml.demand_forecast_importance``.
        """
        return f"{self.forecast_table}_importance"

    @property
    def mae_col(self) -> str:
        """Warehouse column of the importance rows' baseline MAE.

        ``forecast_col`` with its ``forecast_`` prefix swapped for ``mae_``,
        e.g. ``mae_demand_kwh`` — the forecast unit.
        """
        return "mae_" + self.forecast_col.removeprefix("forecast_")

    @property
    def permuted_mae_col(self) -> str:
        """Warehouse column of the MAE after shuffling the feature, e.g.
        ``permuted_mae_demand_kwh``."""
        return "permuted_" + self.mae_col
```

Also extend the class docstring's second paragraph: "The contribution table and column (``contribution_table``, ``contribution_col``) and the importance table and MAE columns (``importance_table``, ``mae_col``, ``permuted_mae_col``) are derived rather than stored."

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_forecasting_task.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/task.py tests/test_forecasting_task.py
git commit -m "feat(forecasting): derive the importance table and MAE columns on TaskSpec

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The importance module (scikit-learn over the walk-forward models)

**Files:**
- Modify: `pyproject.toml`
- Create: `power_market_analytics/forecasting/importance.py`
- Test: `tests/test_forecasting_importance.py`

**Interfaces:**
- Consumes: `PermutationImportance` (Task 1).
- Produces: `MODEL_INDEX_COL = "model_index"`, `DEFAULT_N_REPEATS = 5`, `DEFAULT_SEED = 0`, `WalkForwardPredictor(models, model_of_row)` with `fit`/`predict`, and `permutation_importance(rows, models, feature_cols, *, actual_col, n_repeats=5, seed=0) -> PermutationImportance` where `rows` holds the feature columns, `actual_col` and `MODEL_INDEX_COL`.

- [ ] **Step 1: Declare the dependency**

```bash
cd /Users/hankehly/Projects/power-market-analytics
uv add "scikit-learn>=1.9"
```

Then in `pyproject.toml` change the mypy override to
`module = ["eccodes.*", "gribapi.*", "plotly.*", "scipy.*", "shap.*", "sklearn.*"]`.
Check `uv.lock` still resolves scikit-learn 1.9.0 (`grep -A1 'name = "scikit-learn"' uv.lock`).

Commit:

```bash
git add pyproject.toml uv.lock
git commit -m "build: declare scikit-learn as a direct dependency

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_forecasting_importance.py`:

```python
"""Tests for the walk-forward permutation importance (scikit-learn over routed models)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.base import is_regressor

from power_market_analytics.forecasting.frames import PermutationImportance
from power_market_analytics.forecasting.importance import (
    DEFAULT_N_REPEATS,
    DEFAULT_SEED,
    MODEL_INDEX_COL,
    WalkForwardPredictor,
    permutation_importance,
)


class Linear:
    """A stand-in model: ``slope * signal + intercept`` (ignores every other column)."""

    def __init__(self, slope: float, intercept: float) -> None:
        self.slope, self.intercept = slope, intercept

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.slope * X["signal"].to_numpy() + self.intercept


MODELS = [Linear(3.0, 0.0), Linear(3.0, 0.05)]


def make_rows(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    return pd.DataFrame(
        {
            "signal": signal,
            "noise": rng.normal(size=n),
            "actual": 3.0 * signal + 0.1 * rng.normal(size=n),
            MODEL_INDEX_COL: np.repeat([0, 1], n // 2),
        }
    )


class TestWalkForwardPredictor:
    def test_routes_rows_by_position_not_by_index(self):
        X = pd.DataFrame({"signal": [1.0, 2.0, 3.0, 4.0]}, index=[9, 8, 7, 6])
        predictor = WalkForwardPredictor([Linear(1.0, 0.0), Linear(1.0, 100.0)], [0, 1, 0, 1])
        np.testing.assert_array_equal(predictor.predict(X), [1.0, 102.0, 3.0, 104.0])

    def test_a_model_without_rows_is_skipped(self):
        X = pd.DataFrame({"signal": [1.0, 2.0]})
        predictor = WalkForwardPredictor([Linear(2.0, 0.0), Linear(1.0, 100.0)], [0, 0])
        np.testing.assert_array_equal(predictor.predict(X), [2.0, 4.0])

    def test_row_count_must_match_the_routing(self):
        predictor = WalkForwardPredictor([Linear(1.0, 0.0)], [0, 0, 0])
        with pytest.raises(ValueError, match="model_of_row has 3 entries for 2 rows"):
            predictor.predict(pd.DataFrame({"signal": [1.0, 2.0]}))

    def test_model_index_must_exist(self):
        predictor = WalkForwardPredictor([Linear(1.0, 0.0)], [0, 1])
        with pytest.raises(ValueError, match=r"model_of_row refers to models outside 0\.\.0"):
            predictor.predict(pd.DataFrame({"signal": [1.0, 2.0]}))

    def test_is_a_fitted_regressor_for_scikit_learn(self):
        predictor = WalkForwardPredictor([Linear(1.0, 0.0)], [0])
        assert predictor.fit(pd.DataFrame({"signal": [1.0]}), [1.0]) is predictor
        assert is_regressor(predictor)


class TestPermutationImportance:
    def test_the_signal_feature_matters_and_the_noise_feature_does_not(self):
        rows = make_rows()
        importance = permutation_importance(
            rows, MODELS, ("signal", "noise"), actual_col="actual", n_repeats=5, seed=0
        )
        assert isinstance(importance, PermutationImportance)
        assert importance.df["feature"].tolist() == ["signal"] * 5 + ["noise"] * 5
        assert importance.df["feature_order"].tolist() == [1] * 5 + [2] * 5
        assert importance.df["repeat_index"].tolist() == list(range(5)) * 2
        assert importance.df["n_periods"].unique().tolist() == [400]
        expected_mae = np.abs(
            rows["actual"] - WalkForwardPredictor(MODELS, rows[MODEL_INDEX_COL]).predict(rows)
        ).mean()
        assert importance.df["mae"].unique().tolist() == pytest.approx([expected_mae])
        summary = importance.summary().df.set_index("feature")
        assert summary.loc["signal", "importance_mae"] > 1.0
        assert summary.loc["noise", "importance_mae"] == 0.0
        assert summary.loc["noise", "importance_std"] == 0.0

    def test_the_same_seed_reproduces_and_another_seed_changes_the_shuffles(self):
        rows = make_rows()
        first = permutation_importance(rows, MODELS, ("signal",), actual_col="actual", seed=0)
        again = permutation_importance(rows, MODELS, ("signal",), actual_col="actual", seed=0)
        other = permutation_importance(rows, MODELS, ("signal",), actual_col="actual", seed=1)
        pd.testing.assert_frame_equal(first.df, again.df)
        assert not first.df["permuted_mae"].equals(other.df["permuted_mae"])

    def test_defaults_are_five_repeats_and_seed_zero(self):
        assert (DEFAULT_N_REPEATS, DEFAULT_SEED) == (5, 0)
        rows = make_rows()
        by_default = permutation_importance(rows, MODELS, ("signal",), actual_col="actual")
        explicit = permutation_importance(
            rows, MODELS, ("signal",), actual_col="actual", n_repeats=5, seed=0
        )
        pd.testing.assert_frame_equal(by_default.df, explicit.df)

    def test_rows_must_carry_the_features_the_actual_and_the_model_index(self):
        rows = make_rows().drop(columns=[MODEL_INDEX_COL])
        with pytest.raises(ValueError, match=r"rows lack columns \['model_index'\]"):
            permutation_importance(rows, MODELS, ("signal",), actual_col="actual")

    def test_needs_rows_and_at_least_one_repeat(self):
        rows = make_rows()
        with pytest.raises(ValueError, match="no rows to score"):
            permutation_importance(rows.iloc[:0], MODELS, ("signal",), actual_col="actual")
        with pytest.raises(ValueError, match="n_repeats must be >= 1, got 0"):
            permutation_importance(rows, MODELS, ("signal",), actual_col="actual", n_repeats=0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_importance.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.importance'`

- [ ] **Step 4: Implement the module**

Create `power_market_analytics/forecasting/importance.py`:

```python
"""Permutation feature importance of a walk-forward backtest.

scikit-learn's ``permutation_importance`` scores one estimator: it shuffles a
feature's column across every row, re-scores, and records the loss increase.
A sliding-window backtest has no single model — each refit forecast a block
of days — so :class:`WalkForwardPredictor` presents the refits as one
estimator that routes every row to the model that forecast its day. The
importance is then out of sample and its baseline is the run's own MAE.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.inspection import permutation_importance as sklearn_permutation_importance

from power_market_analytics.forecasting.frames import PermutationImportance

#: Column of ``rows`` naming, per row, the index into ``models`` of the model that forecast it.
MODEL_INDEX_COL = "model_index"
DEFAULT_N_REPEATS = 5
DEFAULT_SEED = 0


class Predictor(Protocol):
    """Anything with a scikit-learn style ``predict`` (a LightGBM regressor, a rule)."""

    def predict(self, X: pd.DataFrame) -> Any: ...


class WalkForwardPredictor(RegressorMixin, BaseEstimator):
    """One estimator over many models: each row is scored by the model that forecast it.

    Rows are routed by position (``model_of_row[i]`` is the model of row
    ``i``), which is what scikit-learn's permutation loop preserves — it
    shuffles a column's values, never the rows.

    Parameters
    ----------
    models : sequence of Predictor
        The refits, in the order ``model_of_row`` indexes them.
    model_of_row : array-like of int
        One model index per row of the design matrix.
    """

    def __init__(
        self, models: Sequence[Predictor] | None = None, model_of_row: Any = None
    ) -> None:
        self.models = models
        self.model_of_row = model_of_row

    def fit(self, X: Any, y: Any = None) -> WalkForwardPredictor:
        """Nothing to fit: the models arrive fitted. Required by scikit-learn.

        Parameters
        ----------
        X, y : Any
            Ignored.

        Returns
        -------
        WalkForwardPredictor
            ``self``.
        """
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Score every row of ``X`` with its own model.

        Parameters
        ----------
        X : pandas.DataFrame
            Design matrix; row ``i`` belongs to ``models[model_of_row[i]]``.

        Returns
        -------
        numpy.ndarray
            One prediction per row, in row order.

        Raises
        ------
        ValueError
            If ``model_of_row`` does not have one entry per row, or names a
            model outside ``models``.
        """
        models = list(self.models or [])
        model_of_row = np.asarray(self.model_of_row, dtype="int64")
        if len(model_of_row) != len(X):
            raise ValueError(f"model_of_row has {len(model_of_row)} entries for {len(X)} rows")
        if model_of_row.min() < 0 or model_of_row.max() >= len(models):
            raise ValueError(f"model_of_row refers to models outside 0..{len(models) - 1}")
        out = np.empty(len(X), dtype="float64")
        for index, model in enumerate(models):
            rows = np.flatnonzero(model_of_row == index)
            if rows.size:
                out[rows] = np.asarray(model.predict(X.iloc[rows]), dtype="float64")
        return out


def permutation_importance(
    rows: pd.DataFrame,
    models: Sequence[Predictor],
    feature_cols: Sequence[str],
    *,
    actual_col: str,
    n_repeats: int = DEFAULT_N_REPEATS,
    seed: int = DEFAULT_SEED,
) -> PermutationImportance:
    """Permutation importance of every feature over the rows a backtest scored.

    Parameters
    ----------
    rows : pandas.DataFrame
        The scored rows: ``feature_cols`` as the models saw them,
        ``actual_col`` and :data:`MODEL_INDEX_COL`.
    models : sequence of Predictor
        The refits, indexed by ``rows[MODEL_INDEX_COL]``.
    feature_cols : sequence of str
        The model's features, in its order (``feature_order`` follows it).
    actual_col : str
        The realized-value column of ``rows``.
    n_repeats : int, optional
        Shuffles per feature.
    seed : int, optional
        ``random_state`` of the shuffles; the same seed reproduces the result.

    Returns
    -------
    PermutationImportance
        ``mae`` is the MAE of the routed predictions on ``rows`` (the run's
        own MAE); ``permuted_mae`` = ``mae`` + scikit-learn's importance,
        scored with ``neg_mean_absolute_error``.

    Raises
    ------
    ValueError
        If ``rows`` is empty, lacks a needed column, or ``n_repeats`` < 1.
    """
    needed = [*feature_cols, actual_col, MODEL_INDEX_COL]
    missing = [col for col in needed if col not in rows.columns]
    if missing:
        raise ValueError(f"rows lack columns {missing}")
    if rows.empty:
        raise ValueError("no rows to score")
    if n_repeats < 1:
        raise ValueError(f"n_repeats must be >= 1, got {n_repeats}")
    X = rows[list(feature_cols)].astype("float64").reset_index(drop=True)
    y = rows[actual_col].to_numpy(dtype="float64")
    estimator = WalkForwardPredictor(models, rows[MODEL_INDEX_COL].to_numpy())
    result = sklearn_permutation_importance(
        estimator,
        X,
        y,
        scoring="neg_mean_absolute_error",
        n_repeats=n_repeats,
        random_state=seed,
    )
    mae = float(np.abs(y - estimator.predict(X)).mean())
    n_features = len(feature_cols)
    frame = pd.DataFrame(
        {
            "feature": np.repeat(list(feature_cols), n_repeats),
            "feature_order": np.repeat(np.arange(1, n_features + 1), n_repeats),
            "repeat_index": np.tile(np.arange(n_repeats), n_features),
            "n_periods": len(rows),
            "mae": mae,
            # importances is (n_features, n_repeats); its row-major ravel lines
            # up with the repeat/tile above.
            "permuted_mae": mae + np.asarray(result.importances).ravel(),
        }
    ).astype({"feature_order": "int64", "repeat_index": "int64", "n_periods": "int64"})
    return PermutationImportance.from_df(frame)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_importance.py -q`
Expected: PASS

- [ ] **Step 6: Lint and type-check**

Run: `just lint && just mypy`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add power_market_analytics/forecasting/importance.py tests/test_forecasting_importance.py
git commit -m "feat(forecasting): permutation importance with scikit-learn over the walk-forward models

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The strategy hook and its LightGBM implementation

**Files:**
- Modify: `power_market_analytics/forecasting/strategy.py` (after `diagnostics`)
- Modify: `power_market_analytics/forecasting/lgbm.py` (`__init__`, `predict`, `_ensure_fitted`, new method after `contributions`)
- Test: `tests/test_forecasting_strategy.py`, `tests/test_forecasting_lgbm.py`, `tests/test_spot_price_lgbm.py`

**Interfaces:**
- Consumes: `permutation_importance`, `MODEL_INDEX_COL`, `DEFAULT_N_REPEATS`, `DEFAULT_SEED` (Task 3).
- Produces: `ForecastStrategy.permutation_importance(run, *, n_repeats=5, seed=0) -> PermutationImportance | None` (default `None`); `SlidingWindowLightGbmStrategy._models: list[LGBMRegressor]`, `_model_of_day: dict[Timestamp, int]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_forecasting_strategy.py`, add to `TestForecastStrategy`:

```python
    def test_permutation_importance_defaults_to_none(self):
        class Done(ForecastStrategy):
            name = "done"

            def predict(self, target_date, history):
                raise NotImplementedError

            def build_eval_set(self, history, start_date, end_date, run=None):
                raise NotImplementedError

            def evaluate(self, eval_set, **kwargs):
                raise NotImplementedError

        assert Done().permutation_importance(run=None) is None
```

`tests/test_forecasting_lgbm.py`, add to `TestSlidingWindowLightGbmStrategy`:

```python
    def test_permutation_importance_needs_a_backtest_first(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _add_features(self, featured, history_df):
                return featured

        with pytest.raises(RuntimeError, match="m: no recorded forecasts; run the backtest first"):
            Minimal().permutation_importance(run=None)
```

`tests/test_spot_price_lgbm.py`, add after `TestEvaluate` (imports: `from power_market_analytics.forecasting.frames import DayAheadForecast, PermutationImportance`):

```python
# --------------------------------------------------------------------------- permutation importance


class TestPermutationImportance:
    def test_baseline_is_the_backtests_mae_over_every_refit(self, backtested):
        strategy, run = backtested
        # 14 days at a 7-day cadence: two refits, both kept, the newest still current.
        assert len(strategy._models) == 2
        assert strategy._models[-1] is strategy._model
        assert sorted(set(strategy._model_of_day.values())) == [0, 1]
        assert strategy._model_of_day[pd.Timestamp("2024-04-01")] == 0
        assert strategy._model_of_day[pd.Timestamp("2024-04-14")] == 1

        importance = strategy.permutation_importance(run, n_repeats=3, seed=1)

        assert isinstance(importance, PermutationImportance)
        df = run.result.df
        expected_mae = (df["forecast_price_jpy_kwh"] - df["actual_price_jpy_kwh"]).abs().mean()
        assert importance.df["mae"].iloc[0] == pytest.approx(expected_mae, abs=1e-9)
        assert importance.df["n_periods"].iloc[0] == 14 * 48
        assert importance.df["feature"].unique().tolist() == list(BASE_FEATURE_COLS)
        assert sorted(importance.df["repeat_index"].unique()) == [0, 1, 2]
        summary = importance.summary().df.set_index("feature")
        # April only: month is constant over the scored rows, so shuffling it changes nothing.
        assert summary.loc["month", "importance_mae"] == 0.0
        assert summary["importance_mae"].max() > 0.0

    def test_defaults_are_five_repeats(self, backtested):
        strategy, run = backtested
        importance = strategy.permutation_importance(run)
        assert sorted(importance.df["repeat_index"].unique()) == [0, 1, 2, 3, 4]

    def test_every_scored_period_needs_a_recorded_forecast(self, prices):
        full = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        run = run_backtest(full, prices, WINDOW_START, WINDOW_END)
        other = LightGbmStrategy(train_window_days=30, refit_every_days=7)
        run_backtest(other, prices, WINDOW_START, pd.Timestamp("2024-04-10"))
        with pytest.raises(
            RuntimeError,
            match=(
                r"lightgbm: 192 scored period\(s\) have no recorded forecast, "
                r"e\.g\. 2024-04-11 time_code 1"
            ),
        ):
            other.permutation_importance(run)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_strategy.py tests/test_forecasting_lgbm.py tests/test_spot_price_lgbm.py -q -k "permutation or Permutation"`
Expected: FAIL with `AttributeError: ... has no attribute 'permutation_importance'`

- [ ] **Step 3: Implement the hook on the base strategy**

`power_market_analytics/forecasting/strategy.py`: import `PermutationImportance` from `forecasting.frames`, and add after `diagnostics`:

```python
    def permutation_importance(
        self,
        run: BacktestRun,
        *,
        n_repeats: int = 5,
        seed: int = 0,
    ) -> PermutationImportance | None:
        """Permutation feature importance over the periods the backtest scored.

        Optional, like :meth:`contributions`. A model strategy shuffles each
        feature's column across the run's scored rows, re-scores every day
        with the model that forecast it, and reports the MAE increase per
        feature and repeat; the baseline is the run's own MAE. The default,
        for strategies without features to shuffle (a naive rule), is
        ``None``: the backtest scripts then publish nothing.

        Parameters
        ----------
        run : BacktestRun
            The backtest's forecasts; fixes the rows that count.
        n_repeats : int, optional
            Shuffles per feature.
        seed : int, optional
            Seed of the shuffles.

        Returns
        -------
        PermutationImportance or None
        """
        return None
```

- [ ] **Step 4: Implement it on the LightGBM base**

In `power_market_analytics/forecasting/lgbm.py`:

1. Imports: add `from power_market_analytics.forecasting.importance import (DEFAULT_N_REPEATS, DEFAULT_SEED, MODEL_INDEX_COL, permutation_importance)` and `PermutationImportance` to the frames import.
2. `__init__`: after `self._model = None` add
   ```python
   #: Every refit, in fit order; ``_model`` is always its last element.
   self._models: list[lightgbm.LGBMRegressor] = []
   #: Index into ``_models`` of the model that scored each predicted day.
   self._model_of_day: dict[pd.Timestamp, int] = {}
   ```
3. `predict`: right after `model = self._ensure_fitted(history.df, target_date)` add `self._model_of_day[target_date] = len(self._models) - 1`.
4. `_ensure_fitted`: after `self._model = model` add `self._models.append(model)`.
5. Class docstring: add a sentence to the "Each :meth:`predict` call..." paragraph: "Every refit is kept, so :meth:`permutation_importance` can re-score each day with the model that forecast it."
6. New method after `contributions`:

```python
    def permutation_importance(
        self,
        run: BacktestRun,
        *,
        n_repeats: int = DEFAULT_N_REPEATS,
        seed: int = DEFAULT_SEED,
    ) -> PermutationImportance:
        """Walk-forward permutation importance over the run's scored periods.

        The recorded per-day features (the same rows :meth:`contributions`
        melts) are aligned to ``run.result`` and each row is tagged with the
        refit that forecast its day; scikit-learn then shuffles one feature at
        a time across all rows and re-scores them through
        :class:`~power_market_analytics.forecasting.importance.WalkForwardPredictor`.

        Parameters
        ----------
        run : BacktestRun
            The backtest whose scored periods define the rows.
        n_repeats, seed : int, optional
            Shuffles per feature and their seed.

        Returns
        -------
        PermutationImportance

        Raises
        ------
        RuntimeError
            If no day has been predicted yet, or a scored period of ``run``
            has no recorded forecast (the backtest and the run disagree).
        """
        if not self._shap_records:
            raise RuntimeError(f"{self.name}: no recorded forecasts; run the backtest first")
        columns = list(dict.fromkeys([*GRAIN_COLS, *self.feature_cols]))
        pooled = pd.concat(
            [
                records[columns].assign(**{MODEL_INDEX_COL: self._model_of_day[day]})
                for day, records in self._shap_records.items()
            ],
            ignore_index=True,
        )
        rows = run.result.df[[*GRAIN_COLS, self.task.actual_col]].merge(
            pooled, how="left", on=GRAIN_COLS, validate="one_to_one", indicator=True
        )
        missing = rows.loc[rows["_merge"] == "left_only", GRAIN_COLS]
        if not missing.empty:
            first = missing.iloc[0]
            raise RuntimeError(
                f"{self.name}: {len(missing)} scored period(s) have no recorded forecast, "
                f"e.g. {first['trade_date'].date()} time_code {first['time_code']}"
            )
        return permutation_importance(
            rows.drop(columns="_merge"),
            self._models,
            self.feature_cols,
            actual_col=self.task.actual_col,
            n_repeats=n_repeats,
            seed=seed,
        )
```

Also update the module docstring's list ("TreeSHAP recording per forecast day and their melt into ``contributions()``, the permutation importance over the kept refits, …").

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_forecasting_strategy.py tests/test_forecasting_lgbm.py tests/test_spot_price_lgbm.py tests/test_demand_lgbm.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/forecasting/strategy.py power_market_analytics/forecasting/lgbm.py tests/test_forecasting_strategy.py tests/test_forecasting_lgbm.py tests/test_spot_price_lgbm.py
git commit -m "feat(forecasting): permutation_importance hook, kept refits on the LightGBM base

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Warehouse write-back

**Files:**
- Modify: `power_market_analytics/forecasting/publish.py` (append)
- Test: `tests/test_forecasting_publish.py`

**Interfaces:**
- Consumes: `PermutationImportance`, `ForecastImportanceRecords` (Task 1); `TaskSpec.importance_table`, `mae_col`, `permuted_mae_col` (Task 2).
- Produces: `build_importance_records(task, importance, *, run_id, strategy, area_code, published_at) -> ForecastImportanceRecords`, `publish_importance_records(task, records, spark=None) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_forecasting_publish.py` (add `ForecastImportanceRecords`, `PermutationImportance` to the frames import and `build_importance_records`, `publish_importance_records` to the publish import):

```python
IMPORTANCE_TABLE = TASK.importance_table  # pma_ml.spot_price_forecast_importance


def make_importance(n_repeats: int = 2, mae: float = 10.0) -> PermutationImportance:
    """Two features; the lag hurts ten times more than the time code per repeat."""
    rows = [
        {
            "feature": feature,
            "feature_order": order,
            "repeat_index": repeat,
            "n_periods": 96,
            "mae": mae,
            "permuted_mae": mae + step * (repeat + 1),
        }
        for order, (feature, step) in enumerate(
            [("time_code", 0.1), ("lag_1d_price", 1.0)], start=1
        )
        for repeat in range(n_repeats)
    ]
    return PermutationImportance.from_df(
        pd.DataFrame(rows).astype(
            {"feature_order": "int64", "repeat_index": "int64", "n_periods": "int64"}
        )
    )


class TestBuildImportanceRecords:
    def test_stamps_the_run(self):
        records = build_importance_records(
            TASK,
            make_importance(),
            run_id="run-123",
            strategy="lightgbm",
            area_code="tokyo",
            published_at=PUBLISHED_AT,
        )
        assert isinstance(records, ForecastImportanceRecords)
        assert list(records.df.columns) == list(ForecastImportanceRecords.schema)
        assert len(records) == 4
        assert records.df["run_id"].eq("run-123").all()
        assert records.df["strategy"].eq("lightgbm").all()
        assert records.df["area_code"].eq("tokyo").all()
        assert records.df["published_at"].eq(PUBLISHED_AT).all()
        assert records.df["published_at"].dtype == "datetime64[ns]"
        assert records.df["permuted_mae"].tolist() == [10.1, 10.2, 11.0, 12.0]


def published_importance_rows(spark, run_id: str) -> pd.DataFrame:
    return (
        spark.sql(
            f"""
            select
              strategy, area_code, feature, feature_order, repeat_index, n_periods,
              mae_price_jpy_kwh, permuted_mae_price_jpy_kwh,
              date_format(published_at, 'yyyy-MM-dd HH:mm:ss') as published_at,
              run_id
            from {IMPORTANCE_TABLE}
            where run_id = '{run_id}'
            order by feature_order, repeat_index
            """
        )
        .toPandas()
        .reset_index(drop=True)
    )


def importance_records(*, run_id, n_repeats=2, mae=10.0, strategy="lightgbm"):
    return build_importance_records(
        TASK,
        make_importance(n_repeats=n_repeats, mae=mae),
        run_id=run_id,
        strategy=strategy,
        area_code="tokyo",
        published_at=PUBLISHED_AT,
    )


class TestPublishImportanceRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        records = importance_records(run_id="imp-create")

        assert publish_importance_records(TASK, records, spark=spark) == 4

        assert spark.catalog.tableExists(IMPORTANCE_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(IMPORTANCE_TABLE)}
        assert {name: c.dataType for name, c in columns.items()} == {
            "strategy": "string",
            "area_code": "string",
            "feature": "string",
            "feature_order": "int",
            "repeat_index": "int",
            "n_periods": "int",
            "mae_price_jpy_kwh": "double",
            "permuted_mae_price_jpy_kwh": "double",
            "published_at": "timestamp",
            "run_id": "string",
        }
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        rows = published_importance_rows(spark, "imp-create")
        assert rows[["feature", "feature_order", "repeat_index", "n_periods"]].values.tolist() == [
            ["time_code", 1, 0, 96],
            ["time_code", 1, 1, 96],
            ["lag_1d_price", 2, 0, 96],
            ["lag_1d_price", 2, 1, 96],
        ]
        assert rows["mae_price_jpy_kwh"].tolist() == [10.0] * 4
        assert rows["permuted_mae_price_jpy_kwh"].tolist() == [10.1, 10.2, 11.0, 12.0]
        assert rows["published_at"].eq("2026-08-26 10:00:00").all()
        assert rows["strategy"].eq("lightgbm").all()
        assert rows["area_code"].eq("tokyo").all()

    def test_republishing_a_run_replaces_only_that_runs_partition(self, spark):
        keep = importance_records(run_id="imp-keep", mae=20.0)
        first = importance_records(run_id="imp-replace", n_repeats=3)
        publish_importance_records(TASK, keep, spark=spark)
        assert publish_importance_records(TASK, first, spark=spark) == 6

        second = importance_records(run_id="imp-replace", n_repeats=1, mae=30.0)
        assert publish_importance_records(TASK, second, spark=spark) == 2

        replaced = published_importance_rows(spark, "imp-replace")
        assert replaced[["feature", "repeat_index", "permuted_mae_price_jpy_kwh"]].values.tolist() == [
            ["time_code", 0, 30.1],
            ["lag_1d_price", 0, 31.0],
        ]
        kept = published_importance_rows(spark, "imp-keep")
        assert kept["mae_price_jpy_kwh"].tolist() == [20.0] * 4

    def test_defaults_to_the_active_spark_session(self, spark):
        records = importance_records(run_id="imp-default-session", n_repeats=1)
        assert publish_importance_records(TASK, records) == 2
        assert published_importance_rows(spark, "imp-default-session")["feature"].tolist() == [
            "time_code",
            "lag_1d_price",
        ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_publish.py -q -k Importance`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

Append to `power_market_analytics/forecasting/publish.py` (add `ForecastImportanceRecords`, `PermutationImportance` to the frames import; extend the module docstring: "Each task has three destination tables — the forecasts, the contributions and the permutation importance (``TaskSpec.importance_table``) …"):

```python
def build_importance_records(
    task: TaskSpec,
    importance: PermutationImportance,
    *,
    run_id: str,
    strategy: str,
    area_code: str,
    published_at: pd.Timestamp,
) -> ForecastImportanceRecords:
    """Shape a strategy's permutation importance into warehouse write-back records.

    Parameters
    ----------
    task : TaskSpec
        Task the importance belongs to (unused beyond typing; the publisher
        reads the column names off it).
    importance : PermutationImportance
        ``strategy.permutation_importance(run, …)``.
    run_id, strategy, area_code : str
        As for :func:`build_forecast_records`.
    published_at : pandas.Timestamp
        The instant stamped on the run's forecast records (naive JST).

    Returns
    -------
    ForecastImportanceRecords
    """
    df = importance.df.assign(
        run_id=run_id,
        strategy=strategy,
        area_code=area_code,
        published_at=pd.Timestamp(published_at),
    ).astype({"published_at": "datetime64[ns]"})
    return ForecastImportanceRecords.from_df(df)


def publish_importance_records(
    task: TaskSpec, records: ForecastImportanceRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's permutation importance to ``task.importance_table``.

    Same mechanics as :func:`publish_forecast_records`; the generic ``mae`` /
    ``permuted_mae`` columns are written as the task's unit-suffixed
    ``mae_col`` / ``permuted_mae_col``.

    Parameters
    ----------
    task : TaskSpec
    records : ForecastImportanceRecords
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
    table = task.importance_table
    _create_run_partitioned_table(
        spark,
        table,
        f"""strategy string,
          area_code string,
          feature string,
          feature_order int,
          repeat_index int,
          n_periods int,
          {task.mae_col} double,
          {task.permuted_mae_col} double,
          published_at timestamp""",
    )
    sdf = spark.createDataFrame(records.df).select(
        F.col("strategy").cast("string"),
        F.col("area_code").cast("string"),
        F.col("feature").cast("string"),
        F.col("feature_order").cast("int"),
        F.col("repeat_index").cast("int"),
        F.col("n_periods").cast("int"),
        F.col("mae").cast("double").alias(task.mae_col),
        F.col("permuted_mae").cast("double").alias(task.permuted_mae_col),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    _overwrite_run_partitions(spark, table, sdf)
    logger.info(
        "Published {} importance rows to {} (run_id={})",
        len(records),
        table,
        records.df["run_id"].iloc[0],
    )
    return len(records)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_forecasting_publish.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/publish.py tests/test_forecasting_publish.py
git commit -m "feat(forecasting): publish permutation importance to pma_ml.<task>_forecast_importance

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The MLflow bar plot

**Files:**
- Modify: `power_market_analytics/forecasting/plots.py` (append)
- Test: `tests/test_forecasting_plots.py`

**Interfaces:**
- Consumes: `PermutationImportanceSummary` (Task 1).
- Produces: `permutation_importance_plot(task, summary, title) -> matplotlib.figure.Figure`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_forecasting_plots.py` (import `PermutationImportanceSummary` and `permutation_importance_plot`; `import matplotlib.pyplot as plt`):

```python
def make_summary() -> PermutationImportanceSummary:
    return PermutationImportanceSummary.from_df(
        pd.DataFrame(
            {
                "feature": ["time_code", "month", "lag_1d_price"],
                "feature_order": [1, 2, 3],
                "mae": [2.0, 2.0, 2.0],
                "permuted_mae": [3.0, 2.0, 5.0],
                "importance_mae": [1.0, 0.0, 3.0],
                "importance_std": [0.1, 0.0, 0.5],
                "importance_pct": [50.0, 0.0, 150.0],
                "n_repeats": [5, 5, 5],
            }
        ).astype({"feature_order": "int64", "n_repeats": "int64"})
    )


class TestPermutationImportancePlot:
    def test_horizontal_bars_largest_on_top_with_std_error_bars(self):
        fig = permutation_importance_plot(TASK, make_summary(), title="lightgbm, tokyo")
        try:
            (ax,) = fig.axes
            # barh draws bottom-up: ascending order puts the most important feature on top.
            assert [t.get_text() for t in ax.get_yticklabels()] == [
                "month",
                "time_code",
                "lag_1d_price",
            ]
            assert [bar.get_width() for bar in ax.patches] == [0.0, 1.0, 3.0]
            (container,) = ax.containers
            assert container.errorbar is not None
            assert ax.get_title() == "lightgbm, tokyo"
            assert ax.get_xlabel() == (
                "ΔMAE (JPY/kWh) when the feature is shuffled; error bars = std over 5 repeats"
            )
        finally:
            plt.close(fig)

    def test_unit_comes_from_the_task(self):
        import dataclasses

        fig = permutation_importance_plot(dataclasses.replace(TASK, unit="kWh"), make_summary(), "t")
        try:
            assert fig.axes[0].get_xlabel().startswith("ΔMAE (kWh)")
        finally:
            plt.close(fig)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_plots.py -q -k Permutation`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

In `power_market_analytics/forecasting/plots.py`: add `import matplotlib.pyplot as plt` and `from matplotlib.figure import Figure` to the imports, `PermutationImportanceSummary` to the frames import, and append:

```python
def permutation_importance_plot(
    task: TaskSpec, summary: PermutationImportanceSummary, title: str
) -> Figure:
    """Horizontal bars of each feature's permutation importance, largest on top.

    A matplotlib figure (logged as a PNG next to the SHAP plots): the mean
    ΔMAE over the repeats per feature, error bars = its standard deviation.

    Parameters
    ----------
    task : TaskSpec
        Labels the axis with ``task.unit``.
    summary : PermutationImportanceSummary
    title : str

    Returns
    -------
    matplotlib.figure.Figure
        The caller closes it after logging.
    """
    df = summary.df.sort_values("importance_mae", ascending=True, ignore_index=True)
    fig, ax = plt.subplots(figsize=(8, 0.45 * len(df) + 1.8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.barh(
        df["feature"],
        df["importance_mae"],
        xerr=df["importance_std"],
        color=SEQUENTIAL_BLUES[7],
        ecolor=INK_SECONDARY,
        capsize=3,
    )
    ax.axvline(0, color=INK_MUTED, linewidth=0.8)
    ax.set_xlabel(
        f"ΔMAE ({task.unit}) when the feature is shuffled; error bars = std over "
        f"{int(df['n_repeats'].iloc[0])} repeats",
        fontsize=9,
        color=INK_SECONDARY,
    )
    ax.set_title(title, fontsize=10, color=INK_PRIMARY, loc="left")
    ax.tick_params(axis="both", labelsize=8, colors=INK_SECONDARY)
    ax.xaxis.grid(True, color="#e6e5e1", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    return fig
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_forecasting_plots.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/plots.py tests/test_forecasting_plots.py
git commit -m "feat(forecasting): permutation importance bar plot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The backtest scripts

**Files:**
- Modify: `scripts/demand_backtest.py`, `scripts/spot_price_backtest.py`
- Test: `tests/test_demand_scripts.py`, `tests/test_spot_price_scripts.py`

**Interfaces:**
- Consumes: the hook (Task 4), `build_importance_records` / `publish_importance_records` (Task 5), `permutation_importance_plot` (Task 6), `DEFAULT_N_REPEATS` / `DEFAULT_SEED` (Task 3).
- Produces: flag `--importance-repeats` (default 5); MLflow params `permutation_repeats`, `permutation_seed`; tag `importance_table`; artifacts `permutation_importance.csv`, `permutation_importance_repeats.csv`, `permutation_importance_plot.png`; rows in `pma_ml.<task>_forecast_importance`.

- [ ] **Step 1: Write the failing tests**

`tests/test_demand_scripts.py`: add `IMPORTANCE_TABLE = "pma_ml.demand_forecast_importance"` and

```python
def published_importance_rows(spark, run_id: str) -> pd.DataFrame:
    return (
        spark.table(IMPORTANCE_TABLE)
        .filter(F.col("run_id") == run_id)
        .toPandas()
        .sort_values(["feature_order", "repeat_index"], ignore_index=True)
    )
```

In `test_lightgbm_over_a_pinned_window`, after the contributions block append:

```python
        # The run's permutation importance lands next to them: 5 features x 5 repeats.
        assert run.data.tags["importance_table"] == IMPORTANCE_TABLE
        assert params["permutation_repeats"] == "5"
        assert params["permutation_seed"] == "0"
        assert {
            "permutation_importance.csv",
            "permutation_importance_repeats.csv",
            "permutation_importance_plot.png",
        } <= artifacts
        importance = published_importance_rows(spark, run.info.run_id)
        assert list(importance.columns) == [
            "strategy",
            "area_code",
            "feature",
            "feature_order",
            "repeat_index",
            "n_periods",
            "mae_demand_kwh",
            "permuted_mae_demand_kwh",
            "published_at",
            "run_id",
        ]
        assert len(importance) == 5 * 5
        assert importance["feature"].unique().tolist() == [
            "time_code",
            "month",
            "day_of_week",
            "wavg_temperature_c",
            "lag_7d_demand_kwh",
        ]
        assert importance["n_periods"].unique().tolist() == [144]
        # the baseline is the run's own MAE (the MLflow evaluation replays the same rows)
        assert importance["mae_demand_kwh"].iloc[0] == pytest.approx(
            run.data.metrics["mean_absolute_error"], rel=1e-9
        )
        assert importance["published_at"].iloc[0] == published["published_at"].iloc[0]
        summary = pd.read_csv(
            mlflow.artifacts.download_artifacts(
                run_id=run.info.run_id, artifact_path="permutation_importance.csv"
            )
        )
        assert list(summary.columns) == [
            "feature",
            "feature_order",
            "mae",
            "permuted_mae",
            "importance_mae",
            "importance_std",
            "importance_pct",
            "n_repeats",
        ]
        assert summary["feature"].tolist() == importance["feature"].unique().tolist()
        assert summary["n_repeats"].tolist() == [5] * 5
```

Add a test for the flag and rename/extend the None-branch test:

```python
    def test_importance_repeats_reaches_the_strategy(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(["--days", "1", "--shap-nsamples", "20", "--importance-repeats", "2"])
        run = last_run()
        assert run.data.params["permutation_repeats"] == "2"
        importance = published_importance_rows(spark, run.info.run_id)
        assert sorted(importance["repeat_index"].unique()) == [0, 1]
        assert len(importance) == 2 * 7  # the default strategy's seven features

    def test_strategy_without_contributions_or_importance_skips_publishing(
        self, spark, curated_warehouse, monkeypatch
    ):
        # Every registered demand strategy is LightGBM-based and always explains itself
        # (unlike spot_price's previous_day); simulate a non-explaining strategy here so
        # the "nothing to publish" branches are exercised too.
        script = import_script("demand_backtest")
        real_build_strategy = script.build_strategy

        def build_strategy_without_explanations(*args, **kwargs):
            strategy = real_build_strategy(*args, **kwargs)
            monkeypatch.setattr(strategy, "contributions", lambda: None)
            monkeypatch.setattr(strategy, "permutation_importance", lambda run, **kw: None)
            return strategy

        monkeypatch.setattr(script, "build_strategy", build_strategy_without_explanations)
        script.main(["--days", "1", "--shap-nsamples", "20"])
        run = last_run()
        assert run.info.status == "FINISHED"
        assert "contribution_table" not in run.data.tags
        assert "importance_table" not in run.data.tags
        assert "permutation_repeats" not in run.data.params
        assert "permutation_importance.csv" not in artifact_names(run.info.run_id)
        if spark.catalog.tableExists(CONTRIBUTION_TABLE):
            assert published_contribution_rows(spark, run.info.run_id).empty
        if spark.catalog.tableExists(IMPORTANCE_TABLE):
            assert published_importance_rows(spark, run.info.run_id).empty
```

(The old `test_strategy_without_contributions_skips_publishing` is replaced by this one.)

`tests/test_spot_price_scripts.py`: add `IMPORTANCE_TABLE = "pma_ml.spot_price_forecast_importance"`, the same `published_importance_rows` helper (sorted by `feature_order`, `repeat_index`), in `test_previous_day_over_a_pinned_window` after the contribution assertions:

```python
        # ... and nothing to shuffle: no importance, no tag.
        assert "importance_table" not in run.data.tags
        assert "permutation_repeats" not in run.data.params
        if spark.catalog.tableExists(IMPORTANCE_TABLE):
            assert published_importance_rows(spark, run.info.run_id).empty
```

and a new test:

```python
    def test_lightgbm_publishes_its_permutation_importance(self, spark, curated_warehouse):
        script = import_script("spot_price_backtest")
        script.main(
            [
                "--strategy",
                "lightgbm",
                "--start-date",
                "2024-05-01",
                "--end-date",
                "2024-05-02",
                "--shap-nsamples",
                "20",
                "--importance-repeats",
                "3",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.data.tags["importance_table"] == IMPORTANCE_TABLE
        assert run.data.params["permutation_repeats"] == "3"
        assert run.data.params["permutation_seed"] == "0"
        assert {
            "permutation_importance.csv",
            "permutation_importance_repeats.csv",
            "permutation_importance_plot.png",
        } <= artifact_names(run.info.run_id)
        importance = published_importance_rows(spark, run.info.run_id)
        assert list(importance.columns) == [
            "strategy",
            "area_code",
            "feature",
            "feature_order",
            "repeat_index",
            "n_periods",
            "mae_price_jpy_kwh",
            "permuted_mae_price_jpy_kwh",
            "published_at",
            "run_id",
        ]
        assert len(importance) == 4 * 3  # the four lightgbm features x 3 repeats
        assert importance["feature"].unique().tolist() == [
            "time_code",
            "month",
            "day_of_week",
            "lag_1d_price",
        ]
        assert importance["n_periods"].unique().tolist() == [96]
        assert importance["mae_price_jpy_kwh"].iloc[0] == pytest.approx(
            run.data.metrics["mean_absolute_error"], rel=1e-9
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_demand_scripts.py tests/test_spot_price_scripts.py -q -k "importance or pinned_window or skips_publishing"`
Expected: FAIL (`unrecognized arguments: --importance-repeats`, missing tag)

- [ ] **Step 3: Wire both scripts**

In each script: import `matplotlib.pyplot as plt`, `from power_market_analytics.forecasting.importance import DEFAULT_N_REPEATS, DEFAULT_SEED`, `permutation_importance_plot` from plots, `build_importance_records` / `publish_importance_records` from publish. Add the flag after `--shap-nsamples`:

```python
    parser.add_argument(
        "--importance-repeats",
        type=int,
        default=DEFAULT_N_REPEATS,
        help="Shuffles per feature for the permutation feature importance.",
    )
```

After the contributions block and before the `diagnostics` loop:

```python
        importance = strategy.permutation_importance(
            run, n_repeats=args.importance_repeats, seed=DEFAULT_SEED
        )
        if importance is None:
            logger.info("{}: strategy has no permutation importance; nothing to publish", args.strategy)
        else:
            publish_importance_records(
                TASK,
                build_importance_records(
                    TASK,
                    importance,
                    run_id=mlflow_run.info.run_id,
                    strategy=args.strategy,
                    area_code=args.area,
                    published_at=records.df["published_at"].iloc[0],
                ),
            )
            mlflow.set_tag("importance_table", TASK.importance_table)
            mlflow.log_params(
                {"permutation_repeats": args.importance_repeats, "permutation_seed": DEFAULT_SEED}
            )
            summary = importance.summary()
            log_dataframe(summary.df, "permutation_importance.csv")
            log_dataframe(importance.df, "permutation_importance_repeats.csv")
            figure = permutation_importance_plot(
                TASK, summary, title=f"Permutation importance — {args.strategy}, {args.area}"
            )
            mlflow.log_figure(figure, "permutation_importance_plot.png")
            plt.close(figure)
```

At the end, next to the contributions log line:

```python
    if importance is not None:
        logger.info("Importance written to {} (partition run_id={})", TASK.importance_table, run_id)
```

Module docstrings: add "… and their permutation feature importance to ``pma_ml.<task>_forecast_importance``."

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_demand_scripts.py tests/test_spot_price_scripts.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/demand_backtest.py scripts/spot_price_backtest.py tests/test_demand_scripts.py tests/test_spot_price_scripts.py
git commit -m "feat(forecasting): log and publish permutation importance from the backtest scripts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: dbt models

**Files:**
- Modify: `dbt/models/raw/ml.yml`
- Create: `dbt/models/staging/stg_ml__{demand,spot_price}_forecast_importance.{sql,yml}`, `dbt/models/curated/fct_{demand,spot_price}_forecast_importance.{sql,yml}`, `dbt/dbt_tests/assert_fct_{demand,spot_price}_forecast_importance_reconciles_with_accuracy.sql`

- [ ] **Step 1: Sources**

Append to the `tables:` list of `dbt/models/raw/ml.yml` (demand shown; spot price identical with `spot_price`, `scripts/spot_price_backtest.py`, `mae_price_jpy_kwh`, `permuted_mae_price_jpy_kwh`, `spot_price_forecast`, `spot_price_forecast_accuracy`, and "(previous_day)" in the last sentence):

```yaml
      - name: demand_forecast_importance
        description: >
          Permutation feature importance of the forecasts in demand_forecast,
          written by the same backtest run (scripts/demand_backtest.py;
          SlidingWindowLightGbmStrategy.permutation_importance —
          scikit-learn's permutation_importance over the run's refits, each
          row re-scored by the model that forecast its day). One row per
          MLflow run, area, model feature and repeat: the run's MAE on its
          scored periods and the MAE after shuffling that feature's column
          across them. Partitioned by run_id and overwritten per run like
          demand_forecast; a strategy with nothing to shuffle writes nothing
          here.
        columns:
          - name: run_id
            description: MLflow run id (experiment demand); the run's tag importance_table points back here.
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
          - name: feature
            description: The model feature, as listed in the run's lgbm_feature_cols param.
            data_tests:
              - not_null
          - name: feature_order
            description: The feature's 1-based position in the model's feature list.
            data_tests:
              - not_null
          - name: repeat_index
            description: Shuffle number, 0 .. permutation_repeats − 1 (run param).
            data_tests:
              - not_null
          - name: n_periods
            description: The run's scored periods (the rows of demand_forecast_accuracy with an actual); the same on every row of the run.
            data_tests:
              - not_null
          - name: mae_demand_kwh
            description: The run's MAE over its scored periods (kWh per 30-minute period), the baseline; the same on every row of the run.
            data_tests:
              - not_null
          - name: permuted_mae_demand_kwh
            description: The MAE over the same periods after shuffling the feature's column, kWh; minus mae_demand_kwh = the importance.
            data_tests:
              - not_null
          - name: published_at
            description: Same instant as the run's demand_forecast rows (naive JST).
            data_tests:
              - not_null
```

- [ ] **Step 2: Staging models**

`dbt/models/staging/stg_ml__demand_forecast_importance.sql`:

```sql
-- Written by a separate Spark application (the backtest script); refresh the
-- thriftserver's cached file listing before reading, as for the forecasts.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'demand_forecast_importance') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    feature,
    feature_order,
    repeat_index,
    n_periods,
    mae_demand_kwh,
    permuted_mae_demand_kwh,
    published_at
  from
    {{ source('ml', 'demand_forecast_importance') }}
  )

select * from source
```

`dbt/models/staging/stg_ml__demand_forecast_importance.yml`:

```yaml
models:
  - name: stg_ml__demand_forecast_importance
    config:
      contract:
        enforced: true
    description: >
      As-is representation of pma_ml.demand_forecast_importance (the
      permutation feature importance of the demand forecasts). One row per
      MLflow run, area, model feature and repeat. Column documentation lives
      on the source (models/raw/ml.yml).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_code
              - feature
              - repeat_index
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
      - name: feature
        data_type: string
        data_tests:
          - not_null
      - name: feature_order
        data_type: int
        data_tests:
          - not_null
      - name: repeat_index
        data_type: int
        data_tests:
          - not_null
      - name: n_periods
        data_type: int
        data_tests:
          - not_null
      - name: mae_demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: permuted_mae_demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

Spot price: the same two files with `spot_price` / `mae_price_jpy_kwh` / `permuted_mae_price_jpy_kwh`.

- [ ] **Step 3: Curated models**

`dbt/models/curated/fct_demand_forecast_importance.sql`:

```sql
with
  importance as (
  select
    *
  from
    {{ ref('stg_ml__demand_forecast_importance') }}
  ),

  final as (
  select
    dim_area.area_key,
    importance.run_id,
    importance.strategy,
    importance.feature,
    importance.feature_order,
    importance.repeat_index,
    importance.n_periods,
    importance.mae_demand_kwh,
    importance.permuted_mae_demand_kwh,
    importance.published_at
  from
    importance
    left join {{ ref('dim_area') }} as dim_area
      on importance.area_code = dim_area.area_code
  )

select * from final
```

`dbt/models/curated/fct_demand_forecast_importance.yml`:

```yaml
models:
  - name: fct_demand_forecast_importance
    config:
      contract:
        enforced: true
    description: >
      Permutation feature importance of the demand forecast runs in
      fct_demand_forecast. Grain: one row per MLflow run x area x model
      feature x repeat (shuffle). feature and feature_order are degenerate
      dimensions like strategy. n_periods and mae_demand_kwh describe the run
      (its scored periods and its MAE over them, the baseline) and repeat on
      every row; permuted_mae_demand_kwh is the MAE over the same periods
      after shuffling the feature's column, so permuted − mae is the
      feature's importance (ΔMAE), and 100 x that / mae its importance in
      percent. The measures are averages: aggregate over the repeats of one
      feature (avg, stddev_pop), never across features or runs. Baseline =
      the run's own error: n_periods and mae_demand_kwh equal the accuracy
      mart's period count and MAE for the run (singular test). Drill across
      to fct_demand_forecast_accuracy on (run_id, area_key). Only strategies
      with features to shuffle (the LightGBM ones) have rows here.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_key
              - feature
              - repeat_index
    columns:
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
      - name: feature
        data_type: string
        data_tests:
          - not_null
      - name: feature_order
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
      - name: repeat_index
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: n_periods
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
      - name: mae_demand_kwh
        data_type: double
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: permuted_mae_demand_kwh
        data_type: double
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

`dbt/dbt_tests/assert_fct_demand_forecast_importance_reconciles_with_accuracy.sql`:

```sql
-- The baseline of the permutation importance is the run's own error: every
-- importance row must carry the accuracy mart's period count and MAE for its
-- run and area (relative tolerance 1e-9 on the MAE).
with
  importance as (
  select
    run_id,
    area_key,
    feature,
    repeat_index,
    n_periods,
    mae_demand_kwh
  from
    {{ ref('fct_demand_forecast_importance') }}
  ),

  accuracy as (
  select
    run_id,
    area_key,
    count(*) as n_periods,
    avg(abs_error_kwh) as mae_kwh
  from
    {{ ref('fct_demand_forecast_accuracy') }}
  where
    abs_error_kwh is not null
  group by
    run_id,
    area_key
  )

select
  importance.run_id,
  importance.area_key,
  importance.feature,
  importance.repeat_index,
  importance.n_periods,
  accuracy.n_periods as accuracy_n_periods,
  importance.mae_demand_kwh,
  accuracy.mae_kwh as accuracy_mae_kwh
from
  importance
  left join accuracy
    on importance.run_id = accuracy.run_id
    and importance.area_key = accuracy.area_key
where
  accuracy.run_id is null
  or importance.n_periods <> accuracy.n_periods
  or abs(importance.mae_demand_kwh - accuracy.mae_kwh) > 1e-9 * greatest(accuracy.mae_kwh, 1)
```

Spot price: the same three files with `spot_price`, `mae_price_jpy_kwh`, `permuted_mae_price_jpy_kwh`, `abs_error_jpy_kwh`, `fct_spot_price_forecast_accuracy`, "(the LightGBM ones; previous_day has none)".

- [ ] **Step 4: Parse**

Run: `cd dbt && uv run dbt deps && uv run dbt parse` (no warehouse needed; `just dbt parse` in the devcontainer is equivalent)
Expected: parse succeeds with no contract / ref errors.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/raw/ml.yml dbt/models/staging/stg_ml__*_forecast_importance.* dbt/models/curated/fct_*_forecast_importance.* dbt/dbt_tests/assert_fct_*_forecast_importance_reconciles_with_accuracy.sql
git commit -m "feat(dbt): fct_<task>_forecast_importance with the accuracy reconciliation test

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: The dashboard section

**Files:**
- Modify: `scripts/create_forecast_dashboard.py`
- Test: `tests/test_create_forecast_dashboard.py`

**Interfaces:**
- Produces: `IMPORTANCE_DATASET_SQL_TEMPLATE`, `COMMON_IMPORTANCE_COLUMNS`, `IMPORTANCE_SECTION_HEADER`, `RUN_LEVEL_CHART_NAMES`; `DashboardSpec` fields `importance_dataset_name`, `importance_table`, `importance_mae_col`, `importance_permuted_mae_col`, `importance_value_columns_sql`, `importance_value_columns` and properties `importance_dataset_sql`, `importance_dataset_columns`, `importance_mae_metric`, `permuted_mae_metric`, `importance_delta_sql`, `importance_metric`, `importance_pct_metric`, `importance_std_metric`, `mean_abs_shap_metric`; builders `importance_bar_params(spec, dataset_id)`, `mean_abs_shap_params(spec, dataset_id)`, `importance_table_params(spec, dataset_id)`; `upsert_dataset(..., main_dttm_col="trade_datetime")`; `build_explanation_tab(chart, spec, explanation_id, importance_id)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_create_forecast_dashboard.py`:

1. Fixtures after `DEMAND_EXPLANATION_COLUMNS`:

```python
IMPORTANCE_SQL_HEAD = """\
select
  a.area_code,
  a.area_name_en,
  i.run_id,
  concat(
    date_format(i.published_at, 'yyyy-MM-dd HH:mm'),
    ' | ', a.area_code,
    ' | ', i.strategy,
    ' | ', substring(i.run_id, 1, 8)
  ) as run_label,
  i.strategy,
  i.published_at,
  i.feature,
  i.feature_order,
  concat(lpad(cast(i.feature_order as string), 2, '0'), ' ', i.feature) as feature_label,
  i.repeat_index,
  i.n_periods,
"""


def importance_sql_tail(importance_table: str) -> str:
    return f"""\
from {importance_table} i
join pma_curated.dim_area a on i.area_key = a.area_key
"""


SPOT_IMPORTANCE_SQL = (
    IMPORTANCE_SQL_HEAD
    + """\
  i.mae_price_jpy_kwh,
  i.permuted_mae_price_jpy_kwh
"""
    + importance_sql_tail("pma_curated.fct_spot_price_forecast_importance")
)
DEMAND_IMPORTANCE_SQL = (
    IMPORTANCE_SQL_HEAD
    + """\
  i.mae_demand_kwh / 1000 as mae_mwh,
  i.permuted_mae_demand_kwh / 1000 as permuted_mae_mwh
"""
    + importance_sql_tail("pma_curated.fct_demand_forecast_importance")
)
IMPORTANCE_COLUMNS_HEAD = [
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("feature", "STRING", False),
    ("feature_order", "INT", False),
    ("feature_label", "STRING", False),
    ("repeat_index", "INT", False),
    ("n_periods", "INT", False),
]
SPOT_IMPORTANCE_COLUMNS = IMPORTANCE_COLUMNS_HEAD + [
    ("mae_price_jpy_kwh", "DOUBLE", False),
    ("permuted_mae_price_jpy_kwh", "DOUBLE", False),
]
DEMAND_IMPORTANCE_COLUMNS = IMPORTANCE_COLUMNS_HEAD + [
    ("mae_mwh", "DOUBLE", False),
    ("permuted_mae_mwh", "DOUBLE", False),
]
IMPORTANCE_CHART_NAMES = [
    "Permutation importance",
    "Mean |SHAP| by feature",
    "Feature importance table",
]
```

2. `TestDashboardSpecs`, after `test_minus_base_metrics_read_the_base_row_of_the_same_period`:

```python
    def test_importance_identity(self, spot, demand):
        assert spot.importance_dataset_name == "spot_price_forecast_importance"
        assert spot.importance_table == "pma_curated.fct_spot_price_forecast_importance"
        assert (spot.importance_mae_col, spot.importance_permuted_mae_col) == (
            "mae_price_jpy_kwh",
            "permuted_mae_price_jpy_kwh",
        )
        assert demand.importance_dataset_name == "demand_forecast_importance"
        assert demand.importance_table == "pma_curated.fct_demand_forecast_importance"
        assert (demand.importance_mae_col, demand.importance_permuted_mae_col) == (
            "mae_mwh",
            "permuted_mae_mwh",
        )

    def test_importance_dataset_sql(self, spot, demand):
        assert spot.importance_dataset_sql == SPOT_IMPORTANCE_SQL
        assert demand.importance_dataset_sql == DEMAND_IMPORTANCE_SQL

    def test_importance_columns_follow_the_sql(self, spot, demand):
        assert spot.importance_dataset_columns == SPOT_IMPORTANCE_COLUMNS
        assert demand.importance_dataset_columns == DEMAND_IMPORTANCE_COLUMNS

    def test_importance_columns_match_the_sql_select_list_in_order(self, spec):
        select_list = spec.importance_dataset_sql.split("\nfrom ", 1)[0].splitlines()[1:]
        output_names = []
        for line in select_list:
            if m := re.fullmatch(r"\s+[ia]\.(\w+),?", line):
                output_names.append(m.group(1))
            elif m := re.search(r"\bas (\w+),?$", line):
                output_names.append(m.group(1))
        assert [name for name, _, _ in spec.importance_dataset_columns] == output_names
        assert [n for n, _, is_dttm in spec.importance_dataset_columns if is_dttm] == [
            "published_at"
        ]

    def test_importance_metrics(self, script, spot, demand):
        assert demand.importance_mae_metric == script.avg_metric("mae_mwh", "MAE (MWh)")
        assert demand.permuted_mae_metric == script.avg_metric(
            "permuted_mae_mwh", "Permuted MAE (MWh)"
        )
        assert demand.importance_delta_sql == "avg(permuted_mae_mwh) - avg(mae_mwh)"
        assert demand.importance_metric == script.sql_metric(
            "avg(permuted_mae_mwh) - avg(mae_mwh)", "ΔMAE (MWh)", option_name="importance_delta_mae"
        )
        assert demand.importance_pct_metric == script.sql_metric(
            "100 * (avg(permuted_mae_mwh) - avg(mae_mwh)) / avg(mae_mwh)",
            "Importance %",
            option_name="importance_pct",
        )
        assert demand.importance_std_metric == script.sql_metric(
            "stddev_pop(permuted_mae_mwh)", "Std over repeats (MWh)", option_name="importance_std"
        )
        assert spot.importance_metric["sqlExpression"] == (
            "avg(permuted_mae_price_jpy_kwh) - avg(mae_price_jpy_kwh)"
        )
        assert spot.importance_metric["label"] == "ΔMAE (JPY/kWh)"
        # on the explanation dataset: the attribution counterpart of the importance bars
        assert spot.mean_abs_shap_metric == script.sql_metric(
            "avg(abs(contribution_price_jpy_kwh))", "Mean |SHAP| (JPY/kWh)", option_name="mean_abs_shap"
        )
        assert demand.mean_abs_shap_metric["sqlExpression"] == "avg(abs(contribution_mwh))"
```

3. `TestUpsertDataset`: add

```python
    def test_main_dttm_col_can_be_overridden(self, script, fake, demand):
        client = make_client(script, fake)
        dataset_id = script.upsert_dataset(
            client,
            3,
            demand.importance_dataset_name,
            demand.importance_dataset_sql,
            demand.importance_dataset_columns,
            main_dttm_col="published_at",
        )
        assert fake.rows["dataset"][dataset_id]["main_dttm_col"] == "published_at"
```

4. `TestChartParams`: after `test_feature_table`:

```python
    def test_importance_bar_is_horizontal_sorted_by_the_delta(self, script, spec):
        p = script.importance_bar_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["orientation"] == "horizontal"
        assert p["x_axis"] == "feature"
        assert p["metrics"] == [spec.importance_metric]
        # ascending on a horizontal bar puts the largest importance on top
        assert p["x_axis_sort"] == spec.importance_metric["label"]
        assert p["x_axis_sort_asc"] is True
        assert p["adhoc_filters"] == []
        assert p["y_axis_format"] == spec.axis_format
        assert p["y_axis_title"] == f"ΔMAE ({spec.unit}) when the feature is shuffled"
        assert p["show_legend"] is False
        assert p["extra_form_data"] == {}

    def test_mean_abs_shap_bar_reads_the_explanation_dataset_without_the_base(
        self, script, spec
    ):
        p = script.mean_abs_shap_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "echarts_timeseries_bar"
        assert p["orientation"] == "horizontal"
        assert p["x_axis"] == "component"
        assert p["metrics"] == [spec.mean_abs_shap_metric]
        assert p["x_axis_sort"] == spec.mean_abs_shap_metric["label"]
        assert p["adhoc_filters"] == [script.NOT_BASE_FILTER]
        assert p["y_axis_title"] == f"Mean |SHAP| ({spec.unit})"

    def test_importance_table(self, script, spec):
        p = script.importance_table_params(spec, 7)
        assert p["datasource"] == "7__table"
        assert p["viz_type"] == "table"
        assert p["query_mode"] == "aggregate"
        assert p["groupby"] == ["feature_label"]
        order, mae, permuted, delta, std, pct = p["metrics"]
        assert order == script.sql_metric("min(feature_order)", "Order")
        assert (mae, permuted, delta, std, pct) == (
            spec.importance_mae_metric,
            spec.permuted_mae_metric,
            spec.importance_metric,
            spec.importance_std_metric,
            spec.importance_pct_metric,
        )
        assert p["timeseries_limit_metric"] == order
        assert p["order_desc"] is False
        assert p["adhoc_filters"] == []
        assert p["column_config"] == {
            "Order": {"d3NumberFormat": ",d"},
            f"MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            f"Permuted MAE ({spec.unit})": {"d3NumberFormat": spec.number_format},
            f"ΔMAE ({spec.unit})": {"d3NumberFormat": spec.signed_number_format},
            f"Std over repeats ({spec.unit})": {"d3NumberFormat": spec.number_format},
            "Importance %": {"d3NumberFormat": "+.1f"},
        }
        assert p["extra_form_data"] == {}
```

and add `lambda: script.importance_bar_params(spec, 12)` and `lambda: script.importance_table_params(spec, 12)` to the `builders` list of `test_every_builder_targets_the_dataset_and_starts_unfiltered`.

5. `TestTabBuilders.test_explanation_tab` becomes:

```python
    def test_explanation_tab(self, script, spot):
        chart, created = recording_factory()
        tab = script.build_explanation_tab(chart, spot, 11, 14)
        assert tab.title == "Explanation"
        assert list(tab.charts) == EXPLANATION_CHART_NAMES + IMPORTANCE_CHART_NAMES
        assert tab.chart_ids == list(range(101, 111))
        # the importance bars and table read the importance dataset; mean |SHAP| the explanation one
        assert [on for _, on in created] == [11] * 7 + [14, 11, 14]
        assert list(script.RUN_LEVEL_CHART_NAMES) == IMPORTANCE_CHART_NAMES
        assert [s["header"] for s in tab.sections] == [None, script.IMPORTANCE_SECTION_HEADER]
        assert_sections_are_consistent(tab)
        assert [[name for _, name, _, _ in row] for row in tab.sections[1]["rows"]] == [
            ["Permutation importance", "Mean |SHAP| by feature"],
            ["Feature importance table"],
        ]
```

6. `EXPECTED_EXPLANATION_TAB_CHILDREN = ["ROW-1-0-0", "ROW-1-0-1", "ROW-1-0-2", "HEADER-1-1", "ROW-1-1-0", "ROW-1-1-1"]`.

7. `TestBuildDashboard` — apply the id shift (dataset +1 for every chart; the three new charts 41–43 sit between the Explanation charts and the Compare charts, so Compare and explanation-vs-baseline ids shift by +4; the dashboard id is 72). Exact replacements in `test_builds_dataset_charts_and_dashboard`:
   - unpack five datasets (`…, explanation_comparison, importance = …`), ids `(10, 11, 12, 13, 14)`; add `assert importance["table_name"] == "demand_forecast_importance"`, `importance["sql"] == DEMAND_IMPORTANCE_SQL`, `importance["main_dttm_col"] == "published_at"`, its columns `== DEMAND_IMPORTANCE_COLUMNS`; the PUT check runs over `(10, 11, 12, 13, 14)` and expects `* 5`.
   - chart names `EXPECTED_DEMAND_CHART_NAMES + EXPLANATION_CHART_NAMES + IMPORTANCE_CHART_NAMES + DEMAND_COMPARISON_CHART_NAMES + EXPLANATION_VS_BASELINE_CHART_NAMES`; ids `list(range(15, 72))`; datasets `[10] * 19 + [11] * 7 + [14, 11, 14] + [12] * 23 + [13] * 5`.
   - add `assert by_name["Permutation importance"]["metrics"] == [demand.importance_metric]`, `assert by_name["Mean |SHAP| by feature"]["adhoc_filters"] == [script.NOT_BASE_FILTER]`, `assert by_name["Feature importance table"]["groupby"] == ["feature_label"]`.
   - dashboard id `72`; `analysis_ids = list(range(15, 34))`, `explanation_ids = list(range(34, 41))`, `importance_ids = [41, 42, 43]`, `comparison_ids = list(range(44, 67))`, `explained_vs_baseline_ids = list(range(67, 72))`; leaderboard `31`; `day_filter["scope"]["excluded"] == analysis_ids + importance_ids + comparison_ids`; `baseline_filter["scope"]["excluded"] == analysis_ids + explanation_ids + importance_ids`.
   - `chart_keys == sorted(f"CHART-{i}" for i in range(15, 72))`; tab titles `["Accuracy", "Explanation", "Compare"]`; `assert position["HEADER-1-1"]["meta"]["text"] == script.IMPORTANCE_SECTION_HEADER`.
   - Accuracy rows: `ROW-0-0-0` → `range(15, 21)`, `CHART-15` (Overall MAE), `ROW-0-2-0` → `["CHART-28", "CHART-29"]` (widths 5, 7), `CHART-33` height 60.
   - Explanation rows: `ROW-1-0-0` → `range(34, 38)`, `ROW-1-0-1` → `["CHART-38", "CHART-39"]`, `ROW-1-0-2` → `["CHART-40"]` (parents `ROW-1-0-2`); add `assert position["ROW-1-1-0"]["children"] == ["CHART-41", "CHART-42"]`, `assert position["CHART-41"]["meta"] == {"chartId": 41, "width": 6, "height": 40, "sliceName": "Permutation importance"}`, `assert position["ROW-1-1-1"]["children"] == ["CHART-43"]`, `assert position["CHART-43"]["meta"]["width"] == 12`.
   - Compare rows: every id +4 (`ROW-2-0-0` → `range(44, 50)`, `CHART-44` Baseline MAE, `ROW-2-0-1` → `range(50, 54)`, `CHART-50` width 3, `ROW-2-1-0` → `["CHART-54"]`, `ROW-2-1-1` → 55/56/57, `CHART-55` width 4, `ROW-2-1-2` → 58/59, `CHART-58` band chart, `ROW-2-1-3` → 60, `ROW-2-1-4` → 61, `ROW-2-2-0` → 62, `ROW-2-2-1` → 63, `ROW-2-2-2` → 64, `ROW-2-2-3` → 65, `ROW-2-3-0` → 67/68/69, `CHART-67` Δ base value, `ROW-2-3-1` → 70, `ROW-2-3-2` → 71, `ROW-2-4-0` → 66, `CHART-66` height 60).
   - `dashboards == [72]`; `method_counts` dataset `{"GET": 5, "POST": 5, "PUT": 5}`, chart `{"GET": 57, "POST": 57, "PUT": 57}`.
   
   `test_spot_price_dashboard_keeps_its_names_layout_and_formats`: unpack five datasets, `importance["sql"] == SPOT_IMPORTANCE_SQL`; chart names include `IMPORTANCE_CHART_NAMES` after the explanation ones; add `assert by_name["Permutation importance"]["y_axis_format"] == ",.2f"`; `ROW-0-3-0` → `["CHART-31"]`; `run_filter excluded == [31]`; `baseline_filter excluded == list(range(15, 44))`.
   
   `test_two_dashboards_coexist…`: `(spot_id, demand_id) == (72, 135)` ("5 datasets + 57 charts + dashboard, twice"); add `spot_imp` / `demand_imp` (`…_forecast_importance`) and expect ids `(10, 11, 12, 13, 14, 73, 74, 75, 76, 77)` for `(spot_ds, spot_ex, spot_cmp, spot_xc, spot_imp, demand_ds, …, demand_imp)`; `len(charts) == 114`; explanation datasets now list `EXPLANATION_CHART_NAMES + ["Mean |SHAP| by feature"]`; importance datasets list `["Permutation importance", "Feature importance table"]`; the dataset tuples in the final loop gain the importance id.
   
   `test_second_build_is_idempotent…`: dataset `{"GET": 5, "PUT": 5}`, chart `{"GET": 57, "PUT": 114}`, `run excluded == [31]`, `dashboards == [72]`.
   
   `test_day_tables_cross_filter…`: `explained = [*range(34, 41), *range(67, 72)]`; ids `(worst_days, improved, worsened, detail, compare_detail) == (32, 64, 65, 33, 66)`; configuration keys `["32", "64", "65"]`; the excluded ranges use `range(15, 72)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q -x`
Expected: FAIL (`AttributeError: 'DashboardSpec' object has no attribute 'importance_dataset_name'` or a TypeError on the spec constructor).

- [ ] **Step 3: Implement the builder changes**

In `scripts/create_forecast_dashboard.py`:

1. Module docstring: "five virtual datasets per dashboard … and ``<task>_forecast_importance`` — the permutation feature importance fact joined to dim_area, one row per feature × repeat"; rename the tab to **Explanation** and add "… and, at the bottom, the feature-importance section: permutation importance bars (ΔMAE per feature), mean |SHAP| bars and the importance table (the run, not the Day)".

2. After `COMMON_EXPLANATION_COLUMNS`:

```python
# Shared skeleton of every task's importance dataset: the permutation feature
# importance fact (one row per run x feature x repeat) with the run label and
# area context. feature_label carries the model's feature order as a sortable
# prefix like component_label. No delivery-day axis: importance describes a run.
IMPORTANCE_DATASET_SQL_TEMPLATE = """\
select
  a.area_code,
  a.area_name_en,
  i.run_id,
  {run_label_sql} as run_label,
  i.strategy,
  i.published_at,
  i.feature,
  i.feature_order,
  concat(lpad(cast(i.feature_order as string), 2, '0'), ' ', i.feature) as feature_label,
  i.repeat_index,
  i.n_periods,
{importance_value_columns_sql}
from {importance_table} i
join pma_curated.dim_area a on i.area_key = a.area_key
"""

COMMON_IMPORTANCE_COLUMNS = (
    ("area_code", "STRING", False),
    ("area_name_en", "STRING", False),
    ("run_id", "STRING", False),
    ("run_label", "STRING", False),
    ("strategy", "STRING", False),
    ("published_at", "TIMESTAMP", True),
    ("feature", "STRING", False),
    ("feature_order", "INT", False),
    ("feature_label", "STRING", False),
    ("repeat_index", "INT", False),
    ("n_periods", "INT", False),
)
```

3. `DashboardSpec`: document and add the six fields at the end of the field list:

```python
    importance_dataset_name: str
    importance_table: str
    importance_mae_col: str
    importance_permuted_mae_col: str
    importance_value_columns_sql: str
    importance_value_columns: tuple[tuple[str, str, bool], ...]
```

Docstring entries: "importance_dataset_name, importance_table : str — the importance dataset and the importance fact it reads. importance_mae_col, importance_permuted_mae_col : str — the *dataset* columns of the run's MAE and of the MAE after shuffling a feature (rescaled like the value columns). importance_value_columns_sql, importance_value_columns — the two-line value block (MAE, permuted MAE), two-space indented, the last line without a trailing comma, and its column metadata."

Properties after `actual_minus_base_metric`:

```python
    # -- importance dataset (permutation feature importance, one row per feature x repeat)

    @property
    def importance_dataset_sql(self) -> str:
        """The importance dataset's SQL: the shared template around this task's value block."""
        return IMPORTANCE_DATASET_SQL_TEMPLATE.format(
            importance_value_columns_sql=self.importance_value_columns_sql,
            importance_table=self.importance_table,
            run_label_sql=RUN_LABEL_SQL.format(f="i", a="a"),
        )

    @property
    def importance_dataset_columns(self) -> list[tuple[str, str, bool]]:
        """(column_name, generic type, is temporal) for every importance column, in select order."""
        return [*COMMON_IMPORTANCE_COLUMNS, *self.importance_value_columns]

    @property
    def importance_mae_metric(self) -> dict:
        """The run's MAE (the same on every row, so the average is the value)."""
        return avg_metric(self.importance_mae_col, f"MAE ({self.unit})")

    @property
    def permuted_mae_metric(self) -> dict:
        """Mean over the repeats of the MAE after shuffling the feature."""
        return avg_metric(self.importance_permuted_mae_col, f"Permuted MAE ({self.unit})")

    @property
    def importance_delta_sql(self) -> str:
        """Permuted MAE − MAE, mean over the selection's repeats (aggregate expression)."""
        return f"avg({self.importance_permuted_mae_col}) - avg({self.importance_mae_col})"

    @property
    def importance_metric(self) -> dict:
        return sql_metric(
            self.importance_delta_sql, f"ΔMAE ({self.unit})", option_name="importance_delta_mae"
        )

    @property
    def importance_pct_metric(self) -> dict:
        return sql_metric(
            f"100 * ({self.importance_delta_sql}) / avg({self.importance_mae_col})",
            "Importance %",
            option_name="importance_pct",
        )

    @property
    def importance_std_metric(self) -> dict:
        """Population std of the permuted MAE over the repeats (scikit-learn's importances_std)."""
        return sql_metric(
            f"stddev_pop({self.importance_permuted_mae_col})",
            f"Std over repeats ({self.unit})",
            option_name="importance_std",
        )

    @property
    def mean_abs_shap_metric(self) -> dict:
        """Mean |contribution| per component on the explanation dataset: attribution next
        to the dependence the permutation bars show."""
        return sql_metric(
            f"avg(abs({self.contribution_col}))",
            f"Mean |SHAP| ({self.unit})",
            option_name="mean_abs_shap",
        )
```

4. `SPOT_PRICE` gains:

```python
    importance_dataset_name="spot_price_forecast_importance",
    importance_table="pma_curated.fct_spot_price_forecast_importance",
    importance_mae_col="mae_price_jpy_kwh",
    importance_permuted_mae_col="permuted_mae_price_jpy_kwh",
    importance_value_columns_sql="""\
  i.mae_price_jpy_kwh,
  i.permuted_mae_price_jpy_kwh""",
    importance_value_columns=(
        ("mae_price_jpy_kwh", "DOUBLE", False),
        ("permuted_mae_price_jpy_kwh", "DOUBLE", False),
    ),
```

`DEMAND` gains:

```python
    importance_dataset_name="demand_forecast_importance",
    importance_table="pma_curated.fct_demand_forecast_importance",
    importance_mae_col="mae_mwh",
    importance_permuted_mae_col="permuted_mae_mwh",
    importance_value_columns_sql="""\
  i.mae_demand_kwh / 1000 as mae_mwh,
  i.permuted_mae_demand_kwh / 1000 as permuted_mae_mwh""",
    importance_value_columns=(
        ("mae_mwh", "DOUBLE", False),
        ("permuted_mae_mwh", "DOUBLE", False),
    ),
```

5. `upsert_dataset(client, database_id, name, sql, columns, *, main_dttm_col: str = "trade_datetime")`, documented ("main_dttm_col : str, optional — the dataset's main temporal column; ``trade_datetime`` for the period-grain datasets, ``published_at`` for the run-grain importance dataset") and used in the PUT payload.

6. Builders after `feature_table_params`:

```python
def _horizontal_bar_params(
    dataset_id: int,
    *,
    x_axis: str,
    metric: dict,
    adhoc_filters: list[dict],
    y_axis_format: str,
    y_axis_title: str,
) -> dict:
    """Params for a single-metric horizontal bar chart sorted by that metric.

    Ascending order on a horizontal bar draws the largest value on top.

    Parameters
    ----------
    dataset_id : int
    x_axis : str
        Category column (one bar each).
    metric : dict
        The bar length.
    adhoc_filters : list of dict
    y_axis_format, y_axis_title : str
        Format and title of the value axis.

    Returns
    -------
    dict
    """
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "echarts_timeseries_bar",
        "orientation": "horizontal",
        "x_axis": x_axis,
        "time_grain_sqla": None,
        "x_axis_sort": metric["label"],
        "x_axis_sort_asc": True,
        "metrics": [metric],
        "groupby": [],
        "adhoc_filters": adhoc_filters,
        "order_desc": True,
        "row_limit": 100,
        "show_legend": False,
        "rich_tooltip": True,
        "y_axis_format": y_axis_format,
        "y_axis_title": y_axis_title,
        "y_axis_title_margin": 30,
        "truncateYAxis": False,
        "color_scheme": "supersetColors",
        "x_axis_time_format": "smart_date",
        "extra_form_data": {},
    }


def importance_bar_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the permutation-importance bars: ΔMAE per feature, largest on top.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The importance dataset.

    Returns
    -------
    dict
    """
    return _horizontal_bar_params(
        dataset_id,
        x_axis="feature",
        metric=spec.importance_metric,
        adhoc_filters=[],
        y_axis_format=spec.axis_format,
        y_axis_title=f"ΔMAE ({spec.unit}) when the feature is shuffled",
    )


def mean_abs_shap_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the mean |SHAP| bars per feature on the explanation dataset (base excluded).

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The explanation dataset.

    Returns
    -------
    dict
    """
    return _horizontal_bar_params(
        dataset_id,
        x_axis="component",
        metric=spec.mean_abs_shap_metric,
        adhoc_filters=[NOT_BASE_FILTER],
        y_axis_format=spec.axis_format,
        y_axis_title=f"Mean |SHAP| ({spec.unit})",
    )


def importance_table_params(spec: DashboardSpec, dataset_id: int) -> dict:
    """Params for the importance table: order, MAE, permuted MAE, ΔMAE, its std, importance %.

    Sorted by ``Order`` to read in the model's feature order.

    Parameters
    ----------
    spec : DashboardSpec
    dataset_id : int
        The importance dataset.

    Returns
    -------
    dict
    """
    order = sql_metric("min(feature_order)", "Order")
    mae, permuted, delta, std, pct = (
        spec.importance_mae_metric,
        spec.permuted_mae_metric,
        spec.importance_metric,
        spec.importance_std_metric,
        spec.importance_pct_metric,
    )
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "query_mode": "aggregate",
        "groupby": ["feature_label"],
        "metrics": [order, mae, permuted, delta, std, pct],
        "adhoc_filters": [],
        "timeseries_limit_metric": order,
        "order_desc": False,
        "row_limit": 100,
        "server_page_length": 20,
        "table_timestamp_format": "smart_date",
        "column_config": {
            "Order": {"d3NumberFormat": ",d"},
            mae["label"]: {"d3NumberFormat": spec.number_format},
            permuted["label"]: {"d3NumberFormat": spec.number_format},
            delta["label"]: {"d3NumberFormat": spec.signed_number_format},
            std["label"]: {"d3NumberFormat": spec.number_format},
            pct["label"]: {"d3NumberFormat": "+.1f"},
        },
        "extra_form_data": {},
    }
```

7. Next to `EXPLANATION_VS_BASELINE_CHART_NAMES`:

```python
# The Explanation tab's run-level charts: outside the Day filter and the day
# tables' cross-filters, because importance describes the whole run.
RUN_LEVEL_CHART_NAMES = (
    "Permutation importance",
    "Mean |SHAP| by feature",
    "Feature importance table",
)
IMPORTANCE_SECTION_HEADER = (
    "Feature importance — ΔMAE when a feature is shuffled across the run's periods "
    "(the run, not the Day); correlated features share importance"
)
```

8. `build_explanation_tab(chart, spec, explanation_id, importance_id)`: docstring gains `importance_id : int — The importance dataset.`; after the existing charts:

```python
    add_importance = _chart_adder(chart, charts, importance_id)
    bars = add_importance("Permutation importance", importance_bar_params(spec, importance_id))
    mean_shap = add("Mean |SHAP| by feature", mean_abs_shap_params(spec, explanation_id))
    table = add_importance("Feature importance table", importance_table_params(spec, importance_id))
```

and a second section:

```python
        {
            "header": IMPORTANCE_SECTION_HEADER,
            "rows": [
                [
                    (bars, "Permutation importance", 6, 40),
                    (mean_shap, "Mean |SHAP| by feature", 6, 40),
                ],
                [(table, "Feature importance table", 12, 36)],
            ],
        },
```

Return `DashboardTab("Explanation", charts, sections)`.

9. `build_dashboard`: the inner `dataset()` takes `main_dttm_col: str = "trade_datetime"` and forwards it; after the explanation-comparison dataset add `importance_id = dataset(spec.importance_dataset_name, spec.importance_dataset_sql, spec.importance_dataset_columns, main_dttm_col="published_at")`; `explanation = build_explanation_tab(chart, spec, explanation_id, importance_id)`; and

```python
    explained = [
        *(
            chart_id
            for name, chart_id in explanation.charts.items()
            if name not in RUN_LEVEL_CHART_NAMES
        ),
        *(compare.charts[name] for name in EXPLANATION_VS_BASELINE_CHART_NAMES),
    ]
```

The `day_excluded` / `baseline_excluded` arguments need no change (the run-level charts fall out of `explained`, and `explanation.chart_ids` already includes them for the Baseline filter).

10. `build_native_filters` docstring: "Day (the Explanation tab's per-day charts and …)".

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_create_forecast_dashboard.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/create_forecast_dashboard.py tests/test_create_forecast_dashboard.py
git commit -m "feat(dashboard): feature importance section on the Explanation tab

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/README.md`, `docs/research/demand/README.md`, `docs/research/spot_price/README.md`

- [ ] **Step 1: CLAUDE.md**

1. Spot backtest bullet: after "… build `+fct_spot_price_forecast_accuracy +fct_spot_price_forecast_contribution` afterwards)" add "and, for the same strategies, their permutation feature importance to `pma_ml.spot_price_forecast_importance` (`--importance-repeats`, default 5; add `+fct_spot_price_forecast_importance` to that build)".
2. Demand backtest bullet: replace "then `just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution` (the second selector materialises the run's TreeSHAP contributions for the dashboard's Explanation (SHAP) tab; the first is what its Run filter reads)" with "then `just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution +fct_demand_forecast_importance` (the second selector materialises the run's TreeSHAP contributions for the dashboard's Explanation tab, the third its permutation feature importance for that tab's Feature importance section — `--importance-repeats`, default 5; the first is what its Run filter reads)".
3. Dashboard bullet: "four virtual datasets" → "five virtual datasets" adding "`<task>_forecast_importance` (`fct_<task>_forecast_importance` joined to `dim_area`: one row per feature × repeat, run grain, `main_dttm_col` = `published_at`)"; every "**Explanation (SHAP)**" → "**Explanation**"; after the Contributions-by-period sentence add: "At the bottom of the tab, the **Feature importance** section (the run, not the Day: outside the Day filter and the day tables' cross-filters) shows **Permutation importance** — horizontal bars of ΔMAE per feature, the MAE increase when that feature's column is shuffled across the run's scored periods (`avg(permuted_mae) − avg(mae)`, mean over the repeats) — next to **Mean |SHAP| by feature** on the explanation dataset, and the **Feature importance table** (MAE, permuted MAE, ΔMAE, std over repeats, importance %). Correlated features share importance (the section header says so)." Update the after-run build command to include `+fct_<task>_forecast_importance`.
4. Architecture: after the "Diagnostics:" paragraph add:
   "- Importance: `ForecastStrategy.permutation_importance(run, n_repeats=5, seed=0)` (default `None`) returns a `PermutationImportance` frame (grain feature × repeat: `n_periods`, `mae` = the run's MAE on its scored periods, `permuted_mae`); the LightGBM base keeps every refit (`_models`, `_model_of_day`) and `forecasting/importance.py` wraps them in `WalkForwardPredictor` (a scikit-learn estimator routing each row to the model that forecast its day) for `sklearn.inspection.permutation_importance` (`neg_mean_absolute_error`; the same shuffles for every feature), so the importance is walk-forward and out of sample. The scripts publish it (`publish.build_importance_records` / `publish_importance_records` → `pma_ml.<task>_forecast_importance` = `TaskSpec.importance_table`, columns `TaskSpec.mae_col` / `permuted_mae_col` = `mae_demand_kwh` / `permuted_mae_demand_kwh`; tag `importance_table`; params `permutation_repeats`, `permutation_seed`) and log `permutation_importance.csv` (`PermutationImportance.summary()`: mean / population std over repeats, importance %), `permutation_importance_repeats.csv` and `permutation_importance_plot.png` (`plots.permutation_importance_plot`) → `stg_ml__<task>_forecast_importance` → `fct_<task>_forecast_importance` (no `std`: no time axis; singular test: `n_periods` and the MAE reconcile with the accuracy mart per run) → Superset dataset `<task>_forecast_importance`."
5. Gotchas: after the `scipy` line add "- `scikit-learn` is a declared dependency since 2026-09-08 (`sklearn.inspection.permutation_importance`; it was already installed through lightgbm / mlflow / shap); `sklearn.*` is mypy-ignored. Its `permutation_importance` needs a real estimator (`BaseEstimator` with `fit`), keeps row order on every scoring call (so positional routing to the refits is safe) and returns run-level scores only."
6. Architecture "Explanations:" paragraph, dashboard tab mention: "Explanation (SHAP)" → "Explanation".

- [ ] **Step 2: docs/README.md**

In "Forecast analysis": MLflow bullet → "params, metrics, SHAP plots, the permutation feature importance (CSV + plot) and CSV artifacts per run"; the dashboard paragraph: after "(click a row to cross-filter the dashboard to that day), and a zoomable 30-minute forecast-vs-actual detail." add "An **Explanation** tab decomposes a day's forecast into per-feature SHAP contributions and, at the bottom, shows the run's **Feature importance**: permutation importance (the MAE increase when a feature's column is shuffled across the run, computed with scikit-learn over the walk-forward models) next to the mean |SHAP| per feature."

- [ ] **Step 3: Research READMEs**

`docs/research/demand/README.md` "Segments reported by the tooling": "of the **Explanation (SHAP)** tab (Day filter))" → "of the **Explanation** tab (Day filter) and its run-level Feature importance section (permutation ΔMAE per feature, mean |SHAP|))". Same edit in `docs/research/spot_price/README.md`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/README.md docs/research/demand/README.md docs/research/spot_price/README.md
git commit -m "docs: permutation feature importance

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Verification, rollout and the PR

- [ ] **Step 1: The full gates**

Run: `just lint && just mypy && just test && (cd dbt && uv run dbt parse)`
Expected: all clean, coverage 100 %.

- [ ] **Step 2: Live check (needs the local stack: `docker compose ps` shows the devcontainer, thriftserver, mlflow and superset up)**

As a main-session background task:

```bash
just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday --area tokyo --start-date 2024-08-18 --end-date 2026-08-17
```

then

```bash
just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution +fct_demand_forecast_importance
just python scripts/create_forecast_dashboard.py
```

Check: the reconciliation test passes in the build; in the browser the demand dashboard's Explanation tab shows the Feature importance section, its table's MAE equals the run's Overall MAE on the Accuracy tab, and the bars match `permutation_importance.csv` in MLflow. Screenshot for the PR's Proof section. If the stack is down, say so in the PR and leave the live check to the researcher.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feature/permutation-feature-importance
gh pr create --title "feat(forecasting): permutation feature importance in Superset and MLflow" --body-file <body> 
gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label documentation
```

Body sections *Why* / *What* / *Proof* with the measured numbers; never the review-bot mention. Then drive the automatic review loop as `CLAUDE.md` describes (poll reviews / reactions every 60 s, address every finding, resolve threads) and report the PR as ready — the researcher merges.
