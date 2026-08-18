# Demand Forecasting Task + Shared Forecasting Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `demand` modeling task (LightGBM baseline: calendar + recency-weighted temperature + D-7 demand, issued 09:30 D-1) that is backtested, MLflow-logged and published like `spot_price`, by first extracting the task-agnostic machinery into `power_market_analytics/forecasting/`.

**Architecture:** A frozen `TaskSpec` (name, unit, history lead days, issue offset, forecast table, four frame classes) parameterises a generic package: frame bases whose schema is assembled from one ClassVar per subclass, a rolling backtest engine with a gaps policy, a sliding-window LightGBM strategy base with an `_add_features` hook, warehouse publish and error heatmaps. `spot_price` is migrated onto it behaviour-preservingly (same column names, artifacts, table, dbt); `demand` is a second thin configuration plus its own datasets/features/strategy/script/dbt models.

**Tech Stack:** Python 3.13, pandas, PySpark (local session in tests; devcontainer warehouse for real runs), LightGBM, SHAP, MLflow 3, Plotly, pytest + pytest-cov (100 % gate), ruff, dbt 1.11 (Spark thrift), `just`.

**Spec:** `docs/superpowers/specs/2026-08-18-demand-forecasting-task-design.md`

## Global Constraints

- Coverage gate: `just test` must report **100 %** over `power_market_analytics/` + `scripts/` (`fail_under = 100`); the only excluded line is `if __name__ == "__main__":`. Any new branch needs a test.
- Lint: `uv run ruff check .` clean; a PostToolUse hook auto-formats `.py` files on Edit/Write — re-read a file before a follow-up Edit if in doubt.
- Docstrings: NumPy style (`Parameters` / `Returns` / `Raises`, underlined headers).
- pandas rules (CLAUDE.md): domain wrappers via `DomainFrame.from_df`; every `merge` states `how=`, `on=`, `validate=`; use column-set constants, not ad-hoc strings.
- dbt: every model `contract: enforced: true` with `data_type` on every column; uniqueness test on the primary key (`dbt_utils.unique_combination_of_columns` for composite keys); generic-test args under `arguments:`.
- Anything that creates a SparkSession runs either in pytest (local session, `spark` fixture) or in the devcontainer (`just python …`, `just dbt …`); never a bare host `python` against the warehouse.
- Branch: `demand-forecasting-task` (already created; the spec is committed on it). One commit per task; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Working tree already contains (uncommitted) `power_market_analytics/tasks/demand/__init__.py`, `tests/test_demand.py` and a CLAUDE.md hunk from the earlier bare-package scaffold; Task 10 supersedes the first two, Task 16 the CLAUDE.md hunk. CLAUDE.md also has an unrelated user hunk ("Timestamps in tests" gotcha) — keep it.
- Behaviour to preserve for `spot_price`: frame column names (`price_jpy_kwh`, `actual_price_jpy_kwh`, `forecast_price_jpy_kwh`), MLflow artifact names/columns, `pma_ml.spot_price_forecast` schema, dbt models, CLI flags.

---

## File structure

**New — `power_market_analytics/forecasting/`** (task-agnostic):
- `__init__.py` — package docstring only.
- `frames.py` — `N_PERIODS`, `GRAIN_SCHEMA`, `GRAIN_COLS`, generic bases `HalfHourlySeries`, `DayAheadForecast`, `BacktestResult`, `ForecastRecords`, plus `MetricByYearTimeCode`.
- `task.py` — `TaskSpec`.
- `strategy.py` — `ForecastStrategy`, `ForecastUnavailableError`.
- `backtest.py` — `BacktestRun`, `run_backtest`, `daily_metrics`.
- `features.py` — `join_lag`.
- `lgbm.py` — `LGBM_PARAMS`, `CALENDAR_FEATURE_COLS`, `LightGbmEvalSetBase`, `SlidingWindowLightGbmStrategy`.
- `publish.py` — `build_forecast_records`, `publish_forecast_records`.
- `plots.py` — palette constants, `metric_by_year_time_code`, `error_heatmaps`.

**Modified — `power_market_analytics/tasks/spot_price/`:**
- `__init__.py` — adds `TASK`; `MLFLOW_EXPERIMENT = TASK.name`.
- `frames.py` — `SpotPrices`, `SpotPriceForecast`, `SpotPriceBacktestResult`, `SpotPriceForecastRecords` (2-line subclasses), `OcctoDemandForecast` unchanged.
- `strategies/naive.py`, `strategies/lgbm.py` — rebased on the generic bases.
- **Deleted:** `backtest.py`, `features.py`, `publish.py`, `plots.py`, `strategies/base.py`.
- `scripts/spot_price_backtest.py` — uses the generic modules + `TASK`.

**New — `power_market_analytics/tasks/demand/`:** `__init__.py` (task def + `TASK`), `frames.py`, `datasets.py`, `features.py`, `strategies/__init__.py`, `strategies/lgbm.py`; `scripts/demand_backtest.py`.

**dbt:** `seeds/jepx_areas.csv` (+ column), `models/curated/dim_area.{sql,yml}`, `models/raw/ml.yml` (+ source table), `models/staging/stg_ml__demand_forecast.{sql,yml}`, `models/standardized/std_ml__demand_forecast.{sql,yml}`, `models/curated/fct_demand_forecast.{sql,yml}`, `models/curated/fct_demand_forecast_accuracy.{sql,yml}`.

**Tests:** new `tests/test_forecasting_{frames,task,features,backtest,lgbm,publish,plots}.py`, `tests/test_demand_{task,frames,datasets,features,lgbm,strategies,scripts}.py`; `tests/conftest.py` fixture extension; existing `tests/test_spot_price_*.py` updated for the new import paths and frame names; `tests/test_spot_price_{backtest,features,publish,plots}.py` are moved (git mv) to the `test_forecasting_*` names.

**Spec deviations (deliberate, small — reflect them in the spec in Task 6):**
- The skip exception is `ForecastUnavailableError`, not `FeaturesUnavailableError`: it also covers "no complete training rows in the visible history", so a walk-forward run started at the beginning of the data warms up instead of crashing.
- `build_eval_set(history, start_date, end_date, run: BacktestRun | None = None)` takes the whole `BacktestRun` (spec: `result`), because the eval set must exclude skipped days and only the run knows them.
- `TaskSpec.issued_at` is not implemented; publish adds `task.issue_offset` to the `trade_date` column directly.
- The engine's "every actual row must have exactly one forecast, else raise" check is dropped: with a 48-row `DayAheadForecast` contract it is unreachable (an actual's time code is always 1..48), and an unreachable branch cannot pass the 100 % coverage gate. Only "forecast points without an actual are dropped and counted" remains.

---

### Task 1: Generic frame bases (`forecasting/frames.py`)

**Files:**
- Create: `power_market_analytics/forecasting/__init__.py`
- Create: `power_market_analytics/forecasting/frames.py`
- Test: `tests/test_forecasting_frames.py`

**Interfaces:**
- Consumes: `power_market_analytics.common.frames.DomainFrame`.
- Produces: `N_PERIODS: int = 48`, `GRAIN_SCHEMA: dict[str, str]`, `GRAIN_COLS: list[str]`; classes `HalfHourlySeries` (ClassVar `value_col: str`), `DayAheadForecast` (`forecast_col`), `BacktestResult` (`actual_col`, `forecast_col`), `ForecastRecords` (`forecast_col`), `MetricByYearTimeCode` (unchanged API: `to_matrix()`). Subclassing any base without its ClassVar raises `TypeError` at class-definition time.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_forecasting_frames.py
"""Tests for the generic day-ahead frame bases and MetricByYearTimeCode."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.frames import (
    GRAIN_COLS,
    N_PERIODS,
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
    MetricByYearTimeCode,
)

D1 = pd.Timestamp("2024-01-01").as_unit("ns")
D2 = pd.Timestamp("2024-01-02").as_unit("ns")


class Series(HalfHourlySeries):
    value_col = "load_mw"


class Forecast(DayAheadForecast):
    forecast_col = "forecast_load_mw"


class Result(BacktestResult):
    actual_col = "actual_load_mw"
    forecast_col = "forecast_load_mw"


class Records(ForecastRecords):
    forecast_col = "forecast_load_mw"


def full_day(day: pd.Timestamp, col: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [day] * N_PERIODS,
            "time_code": np.arange(1, N_PERIODS + 1, dtype="int64"),
            col: np.linspace(1.0, 2.0, N_PERIODS),
        }
    )


class TestGrainConstants:
    def test_grain_is_trade_date_and_time_code(self):
        assert GRAIN_COLS == ["trade_date", "time_code"]
        assert N_PERIODS == 48


class TestHalfHourlySeries:
    def test_schema_keys_and_non_null_come_from_value_col(self):
        assert Series.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "load_mw": "float64",
        }
        assert Series.keys == ["trade_date", "time_code"]
        assert Series.non_null_cols == ["load_mw"]

    def test_accepts_a_valid_history_and_keeps_schema_order(self):
        df = full_day(D1, "load_mw")[["load_mw", "time_code", "trade_date"]]
        out = Series.from_df(df)
        assert isinstance(out, Series)
        assert list(out.df.columns) == ["trade_date", "time_code", "load_mw"]

    def test_null_value_rejected(self):
        df = full_day(D1, "load_mw")
        df.loc[0, "load_mw"] = np.nan
        with pytest.raises(ValueError, match="Series: column 'load_mw' has 1 null values"):
            Series.from_df(df)

    def test_duplicate_grain_rejected(self):
        df = pd.concat([full_day(D1, "load_mw")] * 2, ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique"):
            Series.from_df(df)

    def test_subclass_without_value_col_is_rejected_at_definition(self):
        with pytest.raises(TypeError, match="Broken must set the class attribute 'value_col'"):

            class Broken(HalfHourlySeries):
                pass

    def test_sub_subclass_inherits_the_value_col(self):
        class Narrower(Series):
            pass

        assert Narrower.schema == Series.schema
        assert Narrower.non_null_cols == ["load_mw"]


class TestDayAheadForecast:
    def test_exactly_time_codes_1_to_48_for_one_day_accepted(self):
        out = Forecast.from_df(full_day(D1, "forecast_load_mw"))
        assert Forecast.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "forecast_load_mw": "float64",
        }
        assert len(out) == N_PERIODS

    def test_two_target_days_rejected(self):
        df = pd.concat(
            [full_day(D1, "forecast_load_mw"), full_day(D2, "forecast_load_mw")],
            ignore_index=True,
        )
        with pytest.raises(ValueError, match="expected a single target day"):
            Forecast.from_df(df)

    def test_47_rows_rejected(self):
        with pytest.raises(ValueError, match="expected exactly time codes 1..48, got 47 rows"):
            Forecast.from_df(full_day(D1, "forecast_load_mw").iloc[:-1])

    def test_wrong_time_codes_with_48_rows_rejected(self):
        df = full_day(D1, "forecast_load_mw")
        df["time_code"] = np.arange(2, N_PERIODS + 2, dtype="int64")
        with pytest.raises(ValueError, match="expected exactly time codes 1..48"):
            Forecast.from_df(df)

    def test_subclass_without_forecast_col_is_rejected_at_definition(self):
        with pytest.raises(TypeError, match="must set the class attribute 'forecast_col'"):

            class Broken(DayAheadForecast):
                pass


class TestBacktestResult:
    def test_schema_from_actual_and_forecast_cols(self):
        assert Result.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "actual_load_mw": "float64",
            "forecast_load_mw": "float64",
        }
        assert Result.keys == ["trade_date", "time_code"]
        assert Result.non_null_cols == ["actual_load_mw", "forecast_load_mw"]

    def test_accepts_joined_rows(self):
        df = full_day(D1, "actual_load_mw").assign(forecast_load_mw=1.5)
        assert len(Result.from_df(df)) == N_PERIODS

    @pytest.mark.parametrize("col", ["actual_load_mw", "forecast_load_mw"])
    def test_null_measure_rejected(self, col):
        df = full_day(D1, "actual_load_mw").assign(forecast_load_mw=1.5)
        df.loc[3, col] = np.nan
        with pytest.raises(ValueError, match=f"column '{col}' has 1 null values"):
            Result.from_df(df)

    def test_subclass_missing_either_col_is_rejected(self):
        with pytest.raises(TypeError, match="must set the class attribute 'actual_col'"):

            class NoActual(BacktestResult):
                forecast_col = "f"

        with pytest.raises(TypeError, match="must set the class attribute 'forecast_col'"):

            class NoForecast(BacktestResult):
                actual_col = "a"


def records_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": ["r", "r"],
            "strategy": ["s", "s"],
            "area_code": ["tokyo", "tokyo"],
            "forecast_issued_ts": [pd.Timestamp("2023-12-31 09:30")] * 2,
            "trade_date": [D1, D1],
            "time_code": np.array([1, 2], dtype="int64"),
            "forecast_load_mw": [1.0, 2.0],
            "published_at": [pd.Timestamp("2024-01-05 12:00")] * 2,
        }
    )


class TestForecastRecords:
    def test_grain_and_schema(self):
        assert Records.keys == ["run_id", "area_code", "trade_date", "time_code"]
        assert list(Records.schema) == [
            "run_id",
            "strategy",
            "area_code",
            "forecast_issued_ts",
            "trade_date",
            "time_code",
            "forecast_load_mw",
            "published_at",
        ]
        assert Records.non_null_cols == [
            "strategy",
            "forecast_issued_ts",
            "forecast_load_mw",
            "published_at",
        ]
        assert len(Records.from_df(records_df())) == 2

    def test_same_run_area_date_time_code_twice_rejected(self):
        df = pd.concat([records_df(), records_df().iloc[[0]]], ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique"):
            Records.from_df(df)

    def test_null_forecast_rejected(self):
        df = records_df()
        df.loc[0, "forecast_load_mw"] = np.nan
        with pytest.raises(ValueError, match="'forecast_load_mw' has 1 null values"):
            Records.from_df(df)


class TestMetricByYearTimeCode:
    def make(self, year, time_code, value):
        return pd.DataFrame(
            {
                "year": np.array(year, dtype="int64"),
                "time_code": np.array(time_code, dtype="int64"),
                "value": np.array(value, dtype="float64"),
            }
        )

    def test_time_code_bounds_1_and_48_accepted(self):
        out = MetricByYearTimeCode.from_df(self.make([2024, 2024], [1, 48], [1.0, 2.0]))
        assert len(out) == 2

    @pytest.mark.parametrize("bad", [0, 49])
    def test_time_code_outside_1_48_rejected(self, bad):
        with pytest.raises(ValueError, match=f"time_code outside 1..48: \\[{bad}\\]"):
            MetricByYearTimeCode.from_df(self.make([2024], [bad], [1.0]))

    def test_nan_value_allowed(self):
        out = MetricByYearTimeCode.from_df(self.make([2024], [1], [np.nan]))
        assert np.isnan(out.df["value"].iloc[0])

    def test_to_matrix_sorts_years_and_time_codes(self):
        out = MetricByYearTimeCode.from_df(
            self.make([2024, 2023, 2024], [2, 1, 1], [3.0, 1.0, 2.0])
        )
        matrix = out.to_matrix()
        assert matrix.index.tolist() == [2023, 2024]
        assert matrix.columns.tolist() == [1, 2]
        assert matrix.loc[2024, 2] == 3.0
        assert np.isnan(matrix.loc[2023, 2])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_frames.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'power_market_analytics.forecasting'`.

- [ ] **Step 3: Write the package and the frames module**

```python
# power_market_analytics/forecasting/__init__.py
"""Task-agnostic day-ahead forecasting framework.

Every modeling task under ``power_market_analytics.tasks`` forecasts one value
per 30-minute delivery period of a delivery day for one area, from an issue
time on the day before. This package holds what does not depend on *which*
value: the frame bases (``frames``), the task spec that names a task's columns,
cutoff and destination (``task``), the strategy interface (``strategy``), the
rolling backtest engine (``backtest``), lag features (``features``), the
sliding-window LightGBM strategy base (``lgbm``), the warehouse write-back
(``publish``) and the error heatmaps (``plots``). A task package supplies a
``TaskSpec``, its frames, its datasets and its concrete strategies.
"""
```

```python
# power_market_analytics/forecasting/frames.py
"""Generic domain frames for day-ahead half-hourly forecasting tasks.

A task forecasts one value per 30-minute delivery period; the bases below fix
the shared shape — grain ``(trade_date, time_code)``, 48 periods per day —
while each task names the value column itself. A task declares its frames as
two-line subclasses::

    class SpotPrices(HalfHourlySeries):
        value_col = "price_jpy_kwh"

and the base assembles ``schema`` / ``keys`` / ``non_null_cols`` from that one
attribute when the subclass is defined, so the task-specific column name is the
only thing a task writes and the generic engine reads it back off the class.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from power_market_analytics.common.frames import DomainFrame

N_PERIODS = 48
GRAIN_SCHEMA: dict[str, str] = {"trade_date": "datetime64[ns]", "time_code": "int64"}
GRAIN_COLS: list[str] = list(GRAIN_SCHEMA)


def _column_attr(cls: type, attr: str) -> str:
    """Read a required column-name class attribute off a frame subclass.

    Parameters
    ----------
    cls : type
        The subclass being defined.
    attr : str
        Attribute name, e.g. ``"value_col"``.

    Returns
    -------
    str

    Raises
    ------
    TypeError
        If the attribute is missing or not a non-empty string.
    """
    value = getattr(cls, attr, None)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{cls.__name__} must set the class attribute {attr!r} to a column name")
    return value


class HalfHourlySeries(DomainFrame):
    """One area's half-hourly history of a task's value.

    Grain: (trade_date, time_code). Subclasses set ``value_col``.
    """

    value_col: ClassVar[str]

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        value_col = _column_attr(cls, "value_col")
        cls.schema = {**GRAIN_SCHEMA, value_col: "float64"}
        cls.keys = list(GRAIN_COLS)
        cls.non_null_cols = [value_col]


class DayAheadForecast(DomainFrame):
    """Forecast for one delivery day: exactly 48 half-hour values.

    Grain: (trade_date, time_code); trade_date is the target delivery day.
    Subclasses set ``forecast_col``.
    """

    forecast_col: ClassVar[str]

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        forecast_col = _column_attr(cls, "forecast_col")
        cls.schema = {**GRAIN_SCHEMA, forecast_col: "float64"}
        cls.keys = list(GRAIN_COLS)
        cls.non_null_cols = [forecast_col]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        if df["trade_date"].nunique() != 1:
            raise ValueError(
                f"{cls.__name__}: expected a single target day, got "
                f"{sorted(df['trade_date'].unique())}"
            )
        if len(df) != N_PERIODS or set(df["time_code"]) != set(range(1, N_PERIODS + 1)):
            raise ValueError(
                f"{cls.__name__}: expected exactly time codes 1..{N_PERIODS}, got {len(df)} rows"
            )


class BacktestResult(DomainFrame):
    """Forecasts joined to actuals over a backtest window.

    Grain: (trade_date, time_code). Subclasses set ``actual_col`` and
    ``forecast_col``.
    """

    actual_col: ClassVar[str]
    forecast_col: ClassVar[str]

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        actual_col = _column_attr(cls, "actual_col")
        forecast_col = _column_attr(cls, "forecast_col")
        cls.schema = {**GRAIN_SCHEMA, actual_col: "float64", forecast_col: "float64"}
        cls.keys = list(GRAIN_COLS)
        cls.non_null_cols = [actual_col, forecast_col]


class ForecastRecords(DomainFrame):
    """One backtest run's forecasts shaped for a task's warehouse write-back table.

    Grain: (run_id, area_code, trade_date, time_code) — one forecast per run,
    area and delivery period. Forecasts only; actuals stay in the source fact
    and are joined downstream by dbt. Subclasses set ``forecast_col``.
    """

    forecast_col: ClassVar[str]

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        forecast_col = _column_attr(cls, "forecast_col")
        cls.schema = {
            "run_id": "object",
            "strategy": "object",
            "area_code": "object",
            "forecast_issued_ts": "datetime64[ns]",
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            forecast_col: "float64",
            "published_at": "datetime64[ns]",
        }
        cls.keys = ["run_id", "area_code", "trade_date", "time_code"]
        cls.non_null_cols = ["strategy", "forecast_issued_ts", forecast_col, "published_at"]


class MetricByYearTimeCode(DomainFrame):
    """One error-metric value per calendar year and time code.

    Grain: (year, time_code). ``value`` may be NaN where the metric is
    undefined for a cell (e.g. MAPE over all-zero actuals), so it is not a
    non-null column.
    """

    schema = {
        "year": "int64",
        "time_code": "int64",
        "value": "float64",
    }
    keys = ["year", "time_code"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        bad = df.loc[~df["time_code"].between(1, N_PERIODS), "time_code"]
        if not bad.empty:
            raise ValueError(
                f"{cls.__name__}: time_code outside 1..{N_PERIODS}: {sorted(bad.unique())}"
            )

    def to_matrix(self) -> pd.DataFrame:
        """Pivot to a wide year x time_code matrix for rendering.

        Returns
        -------
        pandas.DataFrame
            Index: year (ascending). Columns: time_code (ascending).
            Values: the metric.
        """
        return (
            self.df.pivot(index="year", columns="time_code", values="value")
            .sort_index()
            .sort_index(axis="columns")
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_frames.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/__init__.py power_market_analytics/forecasting/frames.py tests/test_forecasting_frames.py
git commit -m "$(cat <<'EOF'
Add generic day-ahead frame bases (forecasting.frames)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `TaskSpec` (`forecasting/task.py`)

**Files:**
- Create: `power_market_analytics/forecasting/task.py`
- Test: `tests/test_forecasting_task.py`

**Interfaces:**
- Consumes: the frame bases from Task 1.
- Produces: `TaskSpec(name, unit, history_lead_days, issue_offset, forecast_table, history_cls, forecast_cls, result_cls, records_cls)` frozen dataclass with properties `value_col`, `forecast_col`, `actual_col` and method `history_cutoff(target_date) -> pd.Timestamp`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_forecasting_task.py
"""Tests for the TaskSpec that parameterises the forecasting framework."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)
from power_market_analytics.forecasting.task import TaskSpec


class Series(HalfHourlySeries):
    value_col = "load_mw"


class Forecast(DayAheadForecast):
    forecast_col = "forecast_load_mw"


class Result(BacktestResult):
    actual_col = "actual_load_mw"
    forecast_col = "forecast_load_mw"


class Records(ForecastRecords):
    forecast_col = "forecast_load_mw"


class OtherRecords(ForecastRecords):
    forecast_col = "forecast_something_else"


def make_spec(**overrides) -> TaskSpec:
    kwargs = dict(
        name="load",
        unit="MW",
        history_lead_days=2,
        issue_offset=pd.Timedelta(days=-1, hours=9, minutes=30),
        forecast_table="pma_ml.load_forecast",
        history_cls=Series,
        forecast_cls=Forecast,
        result_cls=Result,
        records_cls=Records,
    )
    kwargs.update(overrides)
    return TaskSpec(**kwargs)


class TestTaskSpec:
    def test_column_names_are_read_off_the_frame_classes(self):
        spec = make_spec()
        assert spec.value_col == "load_mw"
        assert spec.forecast_col == "forecast_load_mw"
        assert spec.actual_col == "actual_load_mw"

    def test_history_cutoff_is_lead_days_before_the_target(self):
        spec = make_spec(history_lead_days=2)
        assert spec.history_cutoff(pd.Timestamp("2024-04-10")) == pd.Timestamp("2024-04-08")
        # numpy datetime64 (as produced by Series.unique()) is accepted too
        assert spec.history_cutoff(pd.Timestamp("2024-04-10").to_datetime64()) == pd.Timestamp(
            "2024-04-08"
        )

    def test_is_frozen(self):
        spec = make_spec()
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "other"

    def test_lead_days_below_one_rejected(self):
        with pytest.raises(ValueError, match="history_lead_days must be >= 1, got 0"):
            make_spec(history_lead_days=0)

    def test_frames_must_agree_on_the_forecast_column(self):
        with pytest.raises(
            ValueError,
            match=r"load: forecast column differs across frames: "
            r"\['forecast_load_mw', 'forecast_something_else'\]",
        ):
            make_spec(records_cls=OtherRecords)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_forecasting_task.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.task'`.

- [ ] **Step 3: Write `task.py`**

```python
# power_market_analytics/forecasting/task.py
"""The spec that turns the generic framework into one concrete task."""

from __future__ import annotations

import dataclasses

import pandas as pd

from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)


@dataclasses.dataclass(frozen=True)
class TaskSpec:
    """Everything the generic engine, strategies, publish and plots need to know
    about one modeling task.

    Column names are not stored twice: they are read off the frame classes,
    which own the contracts.

    Attributes
    ----------
    name : str
        Task name; doubles as the MLflow experiment name.
    unit : str
        Unit of the forecast value for labels, e.g. ``"JPY/kWh"``.
    history_lead_days : int
        How many days before delivery day D the newest usable history day
        lies: 1 when D-1 is fully known at issue time, 2 when only D-2 is.
    issue_offset : pandas.Timedelta
        Issue time relative to D 00:00, e.g. ``Timedelta(days=-1, hours=9,
        minutes=55)`` for 09:55 on D-1.
    forecast_table : str
        Warehouse table the run's forecasts are published to.
    history_cls, forecast_cls, result_cls, records_cls : type
        The task's ``HalfHourlySeries``, ``DayAheadForecast``,
        ``BacktestResult`` and ``ForecastRecords`` subclasses.
    """

    name: str
    unit: str
    history_lead_days: int
    issue_offset: pd.Timedelta
    forecast_table: str
    history_cls: type[HalfHourlySeries]
    forecast_cls: type[DayAheadForecast]
    result_cls: type[BacktestResult]
    records_cls: type[ForecastRecords]

    def __post_init__(self) -> None:
        if self.history_lead_days < 1:
            raise ValueError(f"history_lead_days must be >= 1, got {self.history_lead_days}")
        forecast_cols = {
            self.forecast_cls.forecast_col,
            self.result_cls.forecast_col,
            self.records_cls.forecast_col,
        }
        if len(forecast_cols) != 1:
            raise ValueError(
                f"{self.name}: forecast column differs across frames: {sorted(forecast_cols)}"
            )

    @property
    def value_col(self) -> str:
        """History value column, e.g. ``price_jpy_kwh``."""
        return self.history_cls.value_col

    @property
    def forecast_col(self) -> str:
        """Forecast column shared by the forecast, result and records frames."""
        return self.forecast_cls.forecast_col

    @property
    def actual_col(self) -> str:
        """Actual-value column of the backtest result."""
        return self.result_cls.actual_col

    def history_cutoff(self, target_date: pd.Timestamp) -> pd.Timestamp:
        """Newest delivery day a strategy may see when forecasting ``target_date``.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D.

        Returns
        -------
        pandas.Timestamp
            ``D - history_lead_days`` days; history rows must satisfy
            ``trade_date <= cutoff``.
        """
        return pd.Timestamp(target_date) - pd.Timedelta(days=self.history_lead_days)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_forecasting_task.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/forecasting/task.py tests/test_forecasting_task.py
git commit -m "$(cat <<'EOF'
Add TaskSpec for the forecasting framework

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: spot_price frames on the generic bases + `TASK`

**Files:**
- Modify: `power_market_analytics/tasks/spot_price/frames.py`
- Modify: `power_market_analytics/tasks/spot_price/__init__.py`
- Modify: `tests/test_spot_price_frames.py`
- Test: `tests/test_spot_price_task.py` (new)

**Interfaces:**
- Consumes: Task 1 bases, Task 2 `TaskSpec`.
- Produces: `SpotPrices(HalfHourlySeries)` (`value_col="price_jpy_kwh"`), `SpotPriceForecast(DayAheadForecast)` (`forecast_col="forecast_price_jpy_kwh"`), `SpotPriceBacktestResult(BacktestResult)` (`actual_col="actual_price_jpy_kwh"`), `SpotPriceForecastRecords(ForecastRecords)`; `OcctoDemandForecast` unchanged; **temporary aliases** `DayAheadForecast = SpotPriceForecast`, `BacktestResult = SpotPriceBacktestResult`, `ForecastRecords = SpotPriceForecastRecords`, `N_PERIODS`, `MetricByYearTimeCode` re-exported (all removed in Task 9); `tasks.spot_price.TASK: TaskSpec` (name `spot_price`, unit `JPY/kWh`, lead 1, offset `Timedelta(days=-1, hours=9, minutes=55)`, table `pma_ml.spot_price_forecast`) and `MLFLOW_EXPERIMENT = TASK.name`.

- [ ] **Step 1: Write the failing test for `TASK`**

```python
# tests/test_spot_price_task.py
"""Tests for the spot-price TaskSpec."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks.spot_price import MLFLOW_EXPERIMENT, TASK
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPriceForecastRecords,
    SpotPrices,
)


class TestSpotPriceTask:
    def test_spec(self):
        assert isinstance(TASK, TaskSpec)
        assert TASK.name == "spot_price"
        assert MLFLOW_EXPERIMENT == "spot_price"
        assert TASK.unit == "JPY/kWh"
        assert TASK.history_lead_days == 1
        assert TASK.issue_offset == pd.Timedelta(days=-1, hours=9, minutes=55)
        assert TASK.forecast_table == "pma_ml.spot_price_forecast"
        assert TASK.history_cls is SpotPrices
        assert TASK.forecast_cls is SpotPriceForecast
        assert TASK.result_cls is SpotPriceBacktestResult
        assert TASK.records_cls is SpotPriceForecastRecords

    def test_column_names_are_the_historical_spot_price_names(self):
        assert TASK.value_col == "price_jpy_kwh"
        assert TASK.actual_col == "actual_price_jpy_kwh"
        assert TASK.forecast_col == "forecast_price_jpy_kwh"

    def test_history_visible_at_9_55_on_d_minus_1_ends_at_d_minus_1(self):
        assert TASK.history_cutoff(pd.Timestamp("2024-04-10")) == pd.Timestamp("2024-04-09")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_spot_price_task.py -q`
Expected: `ImportError: cannot import name 'TASK'`.

- [ ] **Step 3: Rewrite `spot_price/frames.py`**

Replace the whole file with:

```python
"""Domain frames for the spot price forecasting task."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting import frames as generic
from power_market_analytics.forecasting.frames import HalfHourlySeries, MetricByYearTimeCode

# N_PERIODS and MetricByYearTimeCode are re-exported for the not-yet-migrated
# spot_price.plots and its tests; Task 9 removes them together with the aliases.
N_PERIODS = generic.N_PERIODS

__all__ = [
    "N_PERIODS",
    "MetricByYearTimeCode",
    "SpotPrices",
    "OcctoDemandForecast",
    "SpotPriceForecast",
    "SpotPriceBacktestResult",
    "SpotPriceForecastRecords",
    "DayAheadForecast",
    "BacktestResult",
    "ForecastRecords",
]


class SpotPrices(HalfHourlySeries):
    """Half-hourly spot price history for one area.

    Grain: (trade_date, time_code).
    """

    value_col = "price_jpy_kwh"


class OcctoDemandForecast(DomainFrame):
    """OCCTO 翌々日 (day-after-next) demand forecast for one area, as features.

    One row per delivery day, carrying only the fields experiment E-001 in
    docs/research/R-001-supply-demand-tightness.md uses: the forecast peak
    demand, its hour, and the peak supply capacity. The min-demand fields
    (meaning changed 2025-04-01) and the derived rates are deliberately not
    part of this contract. The forecast for delivery day D is published on
    D-2 at ~17:45 JST, so it is available at the task's D-1 09:55 cutoff and
    may be joined to D's feature rows without leakage.

    Grain: (trade_date), the forecast target date.
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "max_demand_hour_ending": "int64",
        "max_demand_mw": "int64",
        "max_supply_capacity_mw": "int64",
    }
    keys = ["trade_date"]
    non_null_cols = ["max_demand_hour_ending", "max_demand_mw", "max_supply_capacity_mw"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        bad = df.loc[~df["max_demand_hour_ending"].between(1, 24), "max_demand_hour_ending"]
        if not bad.empty:
            raise ValueError(
                f"{cls.__name__}: max_demand_hour_ending outside 1..24: {sorted(bad.unique())}"
            )


class SpotPriceForecast(generic.DayAheadForecast):
    """Forecast for one delivery day: exactly 48 half-hour prices.

    Grain: (trade_date, time_code); trade_date is the target delivery day.
    """

    forecast_col = "forecast_price_jpy_kwh"


class SpotPriceBacktestResult(generic.BacktestResult):
    """Forecasts joined to actual prices over a backtest window.

    Grain: (trade_date, time_code).
    """

    actual_col = "actual_price_jpy_kwh"
    forecast_col = "forecast_price_jpy_kwh"


class SpotPriceForecastRecords(generic.ForecastRecords):
    """One backtest run's price forecasts shaped for ``pma_ml.spot_price_forecast``.

    Grain: (run_id, area_code, trade_date, time_code).
    """

    forecast_col = "forecast_price_jpy_kwh"


# Transitional aliases: the remaining spot_price modules and tests still import
# these names; Tasks 4-9 migrate them and Task 9 deletes the aliases.
DayAheadForecast = SpotPriceForecast
BacktestResult = SpotPriceBacktestResult
ForecastRecords = SpotPriceForecastRecords
```

- [ ] **Step 4: Rewrite `spot_price/__init__.py`**

```python
"""Day-ahead JEPX spot price forecasting.

Task definition: at 9:55 JST on day D-1 (just before the 10:00 gate closure
of the day-ahead auction), forecast all 48 half-hour prices for delivery day
D in a given area. At that moment the newest published spot results are for
delivery day D-1 (published ~noon on D-2), so a strategy's usable history is
delivery days <= D-1.
"""

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPriceForecastRecords,
    SpotPrices,
)

TASK = TaskSpec(
    name="spot_price",
    unit="JPY/kWh",
    history_lead_days=1,
    # Forecasts for delivery day D are issued at 9:55 JST on D-1.
    issue_offset=pd.Timedelta(days=-1, hours=9, minutes=55),
    forecast_table="pma_ml.spot_price_forecast",
    history_cls=SpotPrices,
    forecast_cls=SpotPriceForecast,
    result_cls=SpotPriceBacktestResult,
    records_cls=SpotPriceForecastRecords,
)

MLFLOW_EXPERIMENT = TASK.name
```

- [ ] **Step 5: Update `tests/test_spot_price_frames.py`**

Change the import block to the new names and use them throughout the file (the classes' behaviour is identical, so only names change):

```python
from power_market_analytics.tasks.spot_price.frames import (
    OcctoDemandForecast,
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPriceForecastRecords,
    SpotPrices,
)
```

Then in the file: `DayAheadForecast` → `SpotPriceForecast`, `BacktestResult` → `SpotPriceBacktestResult`, `ForecastRecords` → `SpotPriceForecastRecords` (class names in `from_df(...)` calls, `isinstance` checks and the error-message `match=` strings such as `"DayAheadForecast: expected"` → `"SpotPriceForecast: expected"`). Delete the whole `TestMetricByYearTimeCode` class and its `MetricByYearTimeCode` import (now covered by `tests/test_forecasting_frames.py`). Run `grep -n "DayAheadForecast\|BacktestResult\|ForecastRecords\|MetricByYear" tests/test_spot_price_frames.py` afterwards — every hit must be one of the three `SpotPrice*` names.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_spot_price_task.py tests/test_spot_price_frames.py tests/test_forecasting_frames.py -q && uv run pytest -q -x`
Expected: all pass (the aliases keep every other spot module working).

- [ ] **Step 7: Commit**

```bash
git add power_market_analytics/tasks/spot_price/frames.py power_market_analytics/tasks/spot_price/__init__.py tests/test_spot_price_frames.py tests/test_spot_price_task.py
git commit -m "$(cat <<'EOF'
Rebase spot_price frames on the generic bases and add its TaskSpec

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `join_lag` with a value column (`forecasting/features.py`)

**Files:**
- Create: `power_market_analytics/forecasting/features.py`
- Delete: `power_market_analytics/tasks/spot_price/features.py`
- Modify: `power_market_analytics/tasks/spot_price/strategies/naive.py` (import + call), `power_market_analytics/tasks/spot_price/strategies/lgbm.py` (import + call)
- Move: `tests/test_spot_price_features.py` → `tests/test_forecasting_features.py`

**Interfaces:**
- Produces: `join_lag(left: pd.DataFrame, series: pd.DataFrame, *, value_col: str, days: int, name: str) -> pd.DataFrame` — attaches `series[value_col]` from `days` calendar days earlier, same time code, as column `name` (NaN where absent); left join on `GRAIN_COLS`, `validate="one_to_one"`.

- [ ] **Step 1: Move the test module and make it fail against the new signature**

```bash
git mv tests/test_spot_price_features.py tests/test_forecasting_features.py
```

Edit `tests/test_forecasting_features.py`: docstring → `"""Tests for the calendar-lag feature join."""` (unchanged), import → `from power_market_analytics.forecasting.features import join_lag`, and every call `join_lag(df, df, days=..., name=...)` / `join_lag(left, history, days=1, name="lag_1d")` / `join_lag(df, dup, ...)` / `join_lag(dup, df, ...)` gains `value_col="price_jpy_kwh"` as the first keyword, e.g. `join_lag(df, df, value_col="price_jpy_kwh", days=1, name="lag_1d")`. Add one new test at the end of `TestJoinLag`:

```python
    def test_value_col_names_the_series_column(self):
        df = prices([D1, D2]).rename(columns={"price_jpy_kwh": "demand_kwh"})
        out = join_lag(df, df, value_col="demand_kwh", days=1, name="lag_1d_demand_kwh")
        assert list(out.columns) == ["trade_date", "time_code", "demand_kwh", "lag_1d_demand_kwh"]
        assert out.loc[out["trade_date"] == D2, "lag_1d_demand_kwh"].tolist() == [1.1, 1.2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_forecasting_features.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.features'`.

- [ ] **Step 3: Write `forecasting/features.py` and delete the spot one**

```python
# power_market_analytics/forecasting/features.py
"""Feature helpers shared by forecast strategies."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.frames import GRAIN_COLS


def join_lag(
    left: pd.DataFrame, series: pd.DataFrame, *, value_col: str, days: int, name: str
) -> pd.DataFrame:
    """Attach the value from ``days`` calendar days earlier, same time code.

    Joins on calendar date rather than row position so that gaps in the
    history (e.g. Hokkaido's 2018 suspension) shift no rows; points whose
    lagged day is missing get NaN.

    Parameters
    ----------
    left : pandas.DataFrame
        Frame to attach the lag to; keyed on (trade_date, time_code).
    series : pandas.DataFrame
        Source history in a ``HalfHourlySeries`` layout with ``value_col``.
    value_col : str
        Column of ``series`` to lag, e.g. ``price_jpy_kwh``.
    days : int
        Lag length in calendar days.
    name : str
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
    """
    lagged = series[[*GRAIN_COLS, value_col]].assign(
        trade_date=series["trade_date"] + pd.Timedelta(days=days)
    )
    return left.merge(
        lagged.rename(columns={value_col: name}),
        how="left",
        on=GRAIN_COLS,
        validate="one_to_one",
    )
```

```bash
git rm power_market_analytics/tasks/spot_price/features.py
```

- [ ] **Step 4: Point the spot strategies at it**

In `power_market_analytics/tasks/spot_price/strategies/naive.py`: replace `from power_market_analytics.tasks.spot_price.features import join_lag` with `from power_market_analytics.forecasting.features import join_lag`, and in `build_eval_set` change `.pipe(join_lag, df, days=1, name="lag_1d_price")` to `.pipe(join_lag, df, value_col="price_jpy_kwh", days=1, name="lag_1d_price")`.

In `power_market_analytics/tasks/spot_price/strategies/lgbm.py`: same import replacement, and in `_features` change `join_lag(points, prices, days=1, name="lag_1d_price")` to `join_lag(points, prices, value_col="price_jpy_kwh", days=1, name="lag_1d_price")`.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_forecasting_features.py tests/test_spot_price_naive.py tests/test_spot_price_lgbm.py -q && uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add -A power_market_analytics/forecasting/features.py power_market_analytics/tasks/spot_price tests/test_forecasting_features.py tests/test_spot_price_features.py
git commit -m "$(cat <<'EOF'
Move join_lag to forecasting.features with an explicit value column

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Strategy interface (`forecasting/strategy.py`)

**Files:**
- Create: `power_market_analytics/forecasting/strategy.py`
- Delete: `power_market_analytics/tasks/spot_price/strategies/base.py`
- Modify: `power_market_analytics/tasks/spot_price/strategies/{__init__,naive,lgbm}.py`, `power_market_analytics/tasks/spot_price/backtest.py` (imports; `task = TASK` on the two concrete strategies)
- Modify: `tests/test_spot_price_naive.py`, `tests/test_spot_price_backtest.py` (import path)
- Test: `tests/test_forecasting_strategy.py` (new)

**Interfaces:**
- Produces: `ForecastUnavailableError(ValueError)`; `ForecastStrategy(ABC)` with ClassVars `name: str`, `task: TaskSpec` and abstract `predict(target_date, history) -> DayAheadForecast`, `build_eval_set(history, start_date, end_date, run=None) -> DomainFrame`, `evaluate(eval_set, **kwargs) -> EvaluationResult`. (`run` is the `BacktestRun` from Task 6; until Task 7 the spot strategies still accept `result=` — see Step 4.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_forecasting_strategy.py
"""Tests for the strategy interface and its skip exception."""

from __future__ import annotations

import pytest

from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError


class TestForecastUnavailableError:
    def test_is_a_value_error(self):
        assert issubclass(ForecastUnavailableError, ValueError)
        with pytest.raises(ValueError, match="no lag"):
            raise ForecastUnavailableError("no lag")


class TestForecastStrategy:
    def test_cannot_be_instantiated_without_the_three_methods(self):
        class Incomplete(ForecastStrategy):
            name = "incomplete"

        with pytest.raises(TypeError, match="abstract"):
            Incomplete()

    def test_concrete_subclass_instantiates(self):
        class Done(ForecastStrategy):
            name = "done"

            def predict(self, target_date, history):
                raise NotImplementedError

            def build_eval_set(self, history, start_date, end_date, run=None):
                raise NotImplementedError

            def evaluate(self, eval_set, **kwargs):
                raise NotImplementedError

        assert Done().name == "done"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_forecasting_strategy.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.strategy'`.

- [ ] **Step 3: Write `forecasting/strategy.py`**

```python
# power_market_analytics/forecasting/strategy.py
"""Forecast strategy interface shared by every task."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

import pandas as pd
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import DayAheadForecast, HalfHourlySeries
from power_market_analytics.forecasting.task import TaskSpec

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from power_market_analytics.forecasting.backtest import BacktestRun


class ForecastUnavailableError(ValueError):
    """The strategy cannot forecast the target day from the history it was given.

    Raised by :meth:`ForecastStrategy.predict` when a feature of the target
    day is missing (a lag lands on a gap, an exogenous row is absent) or when
    the visible history holds no complete training rows. The backtest engine
    treats it as "skip this day" rather than as a failure of the run.
    """


class ForecastStrategy(ABC):
    """Produces a 48-period day-ahead forecast for one delivery day.

    Attributes
    ----------
    name : str
        Registry key and MLflow tag for the strategy.
    task : TaskSpec
        The task this strategy forecasts; fixes the frame classes, the
        history cutoff and the column names the engine reads.
    """

    name: ClassVar[str]
    task: ClassVar[TaskSpec]

    @abstractmethod
    def predict(self, target_date: pd.Timestamp, history: HalfHourlySeries) -> DayAheadForecast:
        """Forecast all 48 values for one delivery day.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : HalfHourlySeries
            History available at the task's issue time, i.e. delivery days
            ``<= task.history_cutoff(D)`` only. The backtest engine enforces
            this cutoff; strategies must not assume anything newer exists.

        Returns
        -------
        DayAheadForecast

        Raises
        ------
        ForecastUnavailableError
            If the day cannot be forecast from ``history``.
        """

    @abstractmethod
    def build_eval_set(
        self,
        history: HalfHourlySeries,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        run: BacktestRun | None = None,
    ) -> DomainFrame:
        """Assemble the design matrix MLflow evaluates this strategy on.

        Every strategy must be evaluable: the backtest scripts always run
        MLflow's regressor evaluation, so this is part of the contract rather
        than an optional extra.

        Parameters
        ----------
        history : HalfHourlySeries
            Full history, including whatever lookback the features need
            before ``start_date``.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.
        run : BacktestRun, optional
            Walk-forward forecasts (and skipped days) from ``run_backtest``
            over the same window. Strategies whose evaluation replays the
            backtest's own predictions (because no single model produced
            them) require it and raise ``ValueError`` without it; strategies
            whose logged model reproduces its predictions exactly ignore it.

        Returns
        -------
        DomainFrame
            A frame exposing ``to_eval_frame()``: numeric feature columns plus
            the target column, with no non-numeric columns (the SHAP evaluator
            skips otherwise).
        """

    @abstractmethod
    def evaluate(self, eval_set: DomainFrame, **kwargs: object) -> EvaluationResult:
        """Log this strategy as a model and evaluate it with MLflow.

        Called inside an active MLflow run; the model, metrics and SHAP plots
        land in that run.

        Parameters
        ----------
        eval_set : DomainFrame
            Design matrix from :meth:`build_eval_set`.
        **kwargs
            Strategy-specific evaluation options.

        Returns
        -------
        mlflow.models.EvaluationResult
        """
```

- [ ] **Step 4: Migrate the spot modules to the new base**

```bash
git rm power_market_analytics/tasks/spot_price/strategies/base.py
```

Then replace `from power_market_analytics.tasks.spot_price.strategies.base import ForecastStrategy` with `from power_market_analytics.forecasting.strategy import ForecastStrategy` in:
- `power_market_analytics/tasks/spot_price/strategies/__init__.py`
- `power_market_analytics/tasks/spot_price/strategies/naive.py` — also add `from power_market_analytics.tasks.spot_price import TASK` and, inside `class PreviousDayStrategy(ForecastStrategy):`, add `task = TASK` right after `name = "previous_day"`. Keep the `build_eval_set(self, prices, start_date, end_date, result=None)` signature for now (Task 7 renames it).
- `power_market_analytics/tasks/spot_price/strategies/lgbm.py` — same import; add `from power_market_analytics.tasks.spot_price import TASK` and `task = TASK` right after `name = "lightgbm"` in `LightGbmStrategy`.
- `power_market_analytics/tasks/spot_price/backtest.py` — same import.
- `tests/test_spot_price_naive.py`, `tests/test_spot_price_backtest.py` — same import.

The abstract signature now names the fourth parameter `run`, while the two spot strategies still call it `result` (positional compatibility keeps them working); Task 7 aligns them.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q -x && uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add -A power_market_analytics/forecasting/strategy.py power_market_analytics/tasks/spot_price tests/test_forecasting_strategy.py tests/test_spot_price_naive.py tests/test_spot_price_backtest.py
git commit -m "$(cat <<'EOF'
Move the ForecastStrategy interface to forecasting.strategy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Backtest engine with the gaps policy (`forecasting/backtest.py`)

**Files:**
- Create: `power_market_analytics/forecasting/backtest.py`
- Delete: `power_market_analytics/tasks/spot_price/backtest.py`
- Modify: `scripts/spot_price_backtest.py`, `tests/test_spot_price_lgbm.py`, `tests/test_spot_price_scripts.py` (one new `n_days_skipped` assertion), `tests/test_spot_price_naive.py`, the spec (§4.3/4.4/4.6 wording: `FeaturesUnavailableError` → `ForecastUnavailableError`)
- Move: `tests/test_spot_price_backtest.py` → `tests/test_forecasting_backtest.py`

**Interfaces:**
- Consumes: `ForecastStrategy.task`, `ForecastUnavailableError`, frame bases, `common.metrics.mae/mape`.
- Produces: `BacktestRun(result: BacktestResult, skipped_days: tuple[pd.Timestamp, ...])` (frozen dataclass); `run_backtest(strategy, history, start_date, end_date) -> BacktestRun`; `daily_metrics(result) -> pd.DataFrame` (columns `trade_date, mae, mape`).

- [ ] **Step 1: Move the engine tests and rewrite them for the new behaviour**

```bash
git mv tests/test_spot_price_backtest.py tests/test_forecasting_backtest.py
```

Replace the whole file with:

```python
# tests/test_forecasting_backtest.py
"""Tests for the rolling daily backtest engine, exercised through the spot task."""

from __future__ import annotations

import contextlib
from typing import Iterator

import numpy as np
import pandas as pd
import pytest
from loguru import logger
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.backtest import BacktestRun, daily_metrics, run_backtest
from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy

D1, D2, D3, D4, D5, D6 = pd.date_range("2024-01-01", periods=6, freq="D", unit="ns")


@contextlib.contextmanager
def captured_logs(level: str = "INFO") -> Iterator[list[str]]:
    """Collect loguru messages at ``level`` and above (the repo's test idiom)."""
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(m.record["message"]), level=level)
    try:
        yield messages
    finally:
        logger.remove(sink)


def history(days: list[pd.Timestamp], time_codes: range = range(1, 49)) -> SpotPrices:
    """Price = day-of-month + time_code / 100, so every (day, time_code) is distinct."""
    return SpotPrices.from_df(
        pd.DataFrame(
            {
                "trade_date": [d for d in days for _ in time_codes],
                "time_code": np.array(list(time_codes) * len(days), dtype="int64"),
                "price_jpy_kwh": [d.day + tc / 100 for d in days for tc in time_codes],
            }
        )
    )


class ConstantStrategy(ForecastStrategy):
    """Forecasts 0.0 for every period and records what history it was shown.

    Days listed in ``unavailable`` raise ForecastUnavailableError instead.
    """

    name = "constant"
    task = TASK

    def __init__(self, unavailable: tuple[pd.Timestamp, ...] = ()) -> None:
        self.calls: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
        self.unavailable = unavailable

    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> SpotPriceForecast:
        self.calls.append((target_date, history.df["trade_date"].max(), len(history)))
        if target_date in self.unavailable:
            raise ForecastUnavailableError(f"constant: no features for {target_date.date()}")
        return SpotPriceForecast.from_df(
            pd.DataFrame(
                {
                    "trade_date": [target_date] * 48,
                    "time_code": np.arange(1, 49, dtype="int64"),
                    "forecast_price_jpy_kwh": [0.0] * 48,
                }
            )
        )

    def build_eval_set(self, history, start_date, end_date, run=None) -> DomainFrame:
        raise NotImplementedError

    def evaluate(self, eval_set, **kwargs) -> EvaluationResult:
        raise NotImplementedError


class TestRunBacktest:
    def test_previous_day_forecast_equals_prior_day_actual(self):
        run = run_backtest(PreviousDayStrategy(), history([D1, D2, D3, D4]), D2, D4)
        assert isinstance(run, BacktestRun)
        assert run.skipped_days == ()
        result = run.result
        assert isinstance(result, SpotPriceBacktestResult)
        df = result.df
        assert len(df) == 3 * 48
        assert sorted(df["trade_date"].unique()) == [D2, D3, D4]
        # Day k's price is k + tc/100, so the D-1 forecast is exactly 1 lower.
        np.testing.assert_allclose(
            df["forecast_price_jpy_kwh"], df["actual_price_jpy_kwh"] - 1.0, atol=1e-12
        )
        row = df[(df["trade_date"] == D3) & (df["time_code"] == 5)].iloc[0]
        assert row["actual_price_jpy_kwh"] == 3.05
        assert row["forecast_price_jpy_kwh"] == 2.05

    def test_only_days_inside_the_window_are_forecast(self):
        # History extends past the window (D5, D6) and before it (D1..D2).
        run = run_backtest(PreviousDayStrategy(), history([D1, D2, D3, D4, D5, D6]), D3, D4)
        assert sorted(run.result.df["trade_date"].unique()) == [D3, D4]
        assert len(run.result) == 96

    def test_window_bounds_are_clipped_to_available_days(self):
        run = run_backtest(PreviousDayStrategy(), history([D1, D2, D3]), D2, D6)
        assert sorted(run.result.df["trade_date"].unique()) == [D2, D3]

    def test_strategy_sees_history_through_the_task_cutoff_only(self):
        # spot_price: history_lead_days = 1, so the newest visible day is D-1.
        strategy = ConstantStrategy()
        run_backtest(strategy, history([D1, D2, D3, D4]), D2, D4)
        assert [t for t, _, _ in strategy.calls] == [D2, D3, D4]
        assert [(latest, n) for _, latest, n in strategy.calls] == [
            (D1, 48),
            (D2, 96),
            (D3, 144),
        ]

    def test_target_days_are_nanosecond_timestamps(self):
        strategy = ConstantStrategy()
        run_backtest(strategy, history([D1, D2]), D2, D2)
        (target, _, _) = strategy.calls[0]
        assert isinstance(target, pd.Timestamp)
        assert target.unit == "ns"

    def test_empty_window_raises(self):
        with pytest.raises(ValueError, match="No delivery days between 2024-01-05 .* 2024-01-06"):
            run_backtest(PreviousDayStrategy(), history([D1, D2, D3]), D5, D6)

    def test_unforecastable_days_are_skipped_and_reported(self):
        strategy = ConstantStrategy(unavailable=(D3,))
        with captured_logs("WARNING") as messages:
            run = run_backtest(strategy, history([D1, D2, D3, D4]), D2, D4)
        assert run.skipped_days == (D3,)
        assert sorted(run.result.df["trade_date"].unique()) == [D2, D4]
        assert len(run.result) == 96
        assert "Skipping 2024-01-03: constant: no features for 2024-01-03" in messages

    def test_all_days_skipped_raises(self):
        strategy = ConstantStrategy(unavailable=(D2, D3))
        with pytest.raises(
            ValueError,
            match=(
                "No delivery day between 2024-01-02 and 2024-01-03 could be forecast "
                r"\(2 skipped; last reason: constant: no features for 2024-01-03\)"
            ),
        ):
            run_backtest(strategy, history([D1, D2, D3]), D2, D3)

    def test_previous_day_missing_is_a_skip_not_a_crash(self):
        # D2 is absent from history: D3 has no D-1 and is skipped; D2 itself is
        # not a target day (no actuals) and D4 forecasts from D3.
        run = run_backtest(PreviousDayStrategy(), history([D1, D3, D4]), D2, D4)
        assert run.skipped_days == (D3,)
        assert sorted(run.result.df["trade_date"].unique()) == [D4]

    def test_forecast_points_without_an_actual_are_dropped(self):
        # D3 has only time codes 1..47: the 48-row forecast for D3 joins to 47
        # actuals; the 48th forecast point is dropped and logged.
        prices = SpotPrices.from_df(
            pd.concat([history([D1, D2]).df, history([D3], range(1, 48)).df], ignore_index=True)
        )
        with captured_logs("INFO") as messages:
            run = run_backtest(ConstantStrategy(), prices, D2, D3)
        assert len(run.result) == 48 + 47
        assert run.skipped_days == ()
        assert "1 forecast points have no actual and were dropped" in messages

    def test_result_columns_follow_the_backtest_result_schema(self):
        run = run_backtest(ConstantStrategy(), history([D1, D2]), D2, D2)
        assert list(run.result.df.columns) == [
            "trade_date",
            "time_code",
            "actual_price_jpy_kwh",
            "forecast_price_jpy_kwh",
        ]
        assert (run.result.df["forecast_price_jpy_kwh"] == 0.0).all()
        assert run.result.df["actual_price_jpy_kwh"].tolist()[:2] == [2.01, 2.02]


class TestDailyMetrics:
    def test_one_row_per_day_with_mae_and_mape(self):
        result = SpotPriceBacktestResult.from_df(
            pd.DataFrame(
                {
                    "trade_date": [D2, D2, D1, D1],
                    "time_code": np.array([1, 2, 1, 2], dtype="int64"),
                    # D1: errors 1 and 1 on actuals 2 and 4 -> MAE 1.0,
                    #     MAPE (0.5 + 0.25) / 2 = 37.5 %.
                    # D2: errors 2 and 1 on actuals 10 and 0 -> MAE 1.5,
                    #     MAPE 20 % (the zero actual is excluded).
                    "actual_price_jpy_kwh": [10.0, 0.0, 2.0, 4.0],
                    "forecast_price_jpy_kwh": [8.0, 1.0, 3.0, 3.0],
                }
            )
        )
        metrics = daily_metrics(result)
        assert list(metrics.columns) == ["trade_date", "mae", "mape"]
        assert metrics["trade_date"].tolist() == [D1, D2]
        assert metrics["mae"].tolist() == [1.0, 1.5]
        assert metrics["mape"].tolist() == [37.5, 20.0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_forecasting_backtest.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.backtest'`.

- [ ] **Step 3: Write `forecasting/backtest.py`, delete the spot one**

```python
# power_market_analytics/forecasting/backtest.py
"""Rolling daily backtest engine shared by every task."""

from __future__ import annotations

import dataclasses

import pandas as pd
from loguru import logger

from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.forecasting.frames import GRAIN_COLS, BacktestResult, HalfHourlySeries
from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError


@dataclasses.dataclass(frozen=True)
class BacktestRun:
    """What a walk-forward backtest produced.

    Attributes
    ----------
    result : BacktestResult
        Forecasts joined to actuals for every day that was forecast.
    skipped_days : tuple of pandas.Timestamp
        Target days the strategy could not forecast (it raised
        ``ForecastUnavailableError``); they have no rows in ``result``.
    """

    result: BacktestResult
    skipped_days: tuple[pd.Timestamp, ...]


def run_backtest(
    strategy: ForecastStrategy,
    history: HalfHourlySeries,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> BacktestRun:
    """Backtest a strategy over each delivery day in a window.

    For each target day D in [start_date, end_date] that has actuals, the
    strategy receives only history through ``task.history_cutoff(D)`` —
    everything published by the task's issue time — and its 48 predictions
    are joined to the realized values.

    Gaps policy: a day the strategy cannot forecast (it raises
    ``ForecastUnavailableError``) is skipped with a warning and reported in
    ``skipped_days``; forecast points whose actual is missing are dropped
    from the result (count logged). Only a window in which *no* day could be
    forecast is an error.

    Parameters
    ----------
    strategy : ForecastStrategy
        Strategy under test; ``strategy.task`` fixes the cutoff and frames.
    history : HalfHourlySeries
        Full history; must cover the window plus whatever lookback the
        strategy needs before ``start_date``.
    start_date, end_date : pandas.Timestamp
        First and last delivery days to forecast, inclusive.

    Returns
    -------
    BacktestRun

    Raises
    ------
    ValueError
        If the window contains no delivery days, or none could be forecast.
    """
    task = strategy.task
    df = history.df
    target_days = sorted(
        df.loc[
            (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date), "trade_date"
        ].unique()
    )
    if not target_days:
        raise ValueError(f"No delivery days between {start_date} and {end_date}")
    logger.info(
        "Backtesting {} over {} days ({}..{})",
        strategy.name,
        len(target_days),
        pd.Timestamp(target_days[0]).date(),
        pd.Timestamp(target_days[-1]).date(),
    )

    forecasts: list[pd.DataFrame] = []
    skipped: list[pd.Timestamp] = []
    last_reason = ""
    for day in target_days:
        target_day = pd.Timestamp(day).as_unit("ns")
        visible = task.history_cls.from_df(
            df[df["trade_date"] <= task.history_cutoff(target_day)]
        )
        try:
            forecasts.append(strategy.predict(target_day, visible).df)
        except ForecastUnavailableError as exc:
            last_reason = str(exc)
            logger.warning("Skipping {}: {}", target_day.date(), exc)
            skipped.append(target_day)
    if not forecasts:
        raise ValueError(
            f"No delivery day between {pd.Timestamp(target_days[0]).date()} and "
            f"{pd.Timestamp(target_days[-1]).date()} could be forecast "
            f"({len(skipped)} skipped; last reason: {last_reason})"
        )

    forecast_df = pd.concat(forecasts, ignore_index=True)
    actuals = df[df["trade_date"].isin(forecast_df["trade_date"].unique())].rename(
        columns={task.value_col: task.actual_col}
    )
    result = actuals.merge(forecast_df, how="inner", on=GRAIN_COLS, validate="one_to_one")
    n_unscored = len(forecast_df) - len(result)
    if n_unscored:
        logger.info("{} forecast points have no actual and were dropped", n_unscored)
    return BacktestRun(result=task.result_cls.from_df(result), skipped_days=tuple(skipped))


def daily_metrics(result: BacktestResult) -> pd.DataFrame:
    """Per-delivery-day error metrics.

    Parameters
    ----------
    result : BacktestResult
        Any task's backtest result; the actual/forecast columns are read off
        its class.

    Returns
    -------
    pandas.DataFrame
        One row per trade_date with ``mae`` and ``mape`` columns.
    """
    actual_col, forecast_col = type(result).actual_col, type(result).forecast_col
    return (
        result.df.groupby("trade_date")
        .apply(
            lambda g: pd.Series(
                {
                    "mae": mae(g[actual_col], g[forecast_col]),
                    "mape": mape(g[actual_col], g[forecast_col]),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
```

```bash
git rm power_market_analytics/tasks/spot_price/backtest.py
```

- [ ] **Step 4: Make `PreviousDayStrategy` skip instead of crash**

In `power_market_analytics/tasks/spot_price/strategies/naive.py`, import `ForecastUnavailableError` from `power_market_analytics.forecasting.strategy` and change the raise in `predict` to `raise ForecastUnavailableError(f"{self.name}: no history for previous day {previous_day.date()}")`; update the `Raises` docstring section accordingly (`ForecastUnavailableError`).

- [ ] **Step 5: Update the script and the other callers**

`scripts/spot_price_backtest.py`:
- import: `from power_market_analytics.forecasting.backtest import daily_metrics, run_backtest` (replacing the spot import);
- `result = run_backtest(...)` → `run = run_backtest(strategy, prices, start_date=start_date, end_date=end_date)` followed by `result = run.result`;
- add `"n_days_skipped": len(run.skipped_days),` to the `mlflow.log_params({...})` dict after `"n_predictions"`;
- `strategy.build_eval_set(prices, start_date=start_date, end_date=end_date, result=result)` stays for now (Task 7 changes it to `run=run`).

`tests/test_spot_price_lgbm.py`: import `run_backtest` from `power_market_analytics.forecasting.backtest`; in the `backtested` fixture `result = run_backtest(...)` → `run = run_backtest(...)`, `return strategy, run.result`; in `test_rows_with_forecasts_missing_are_rejected` `result = run_backtest(...)` → `result = run_backtest(...).result`; and in `test_missing_previous_day_raises` change the expectation to
```python
        with pytest.raises(ValueError, match="lightgbm: no history for previous day 2024-04-09"):
```
(unchanged for now — Task 7 rewrites this test when `predict` stops relying on D-1's rows). Also in `tests/test_spot_price_naive.py`, the "no history for previous day" test keeps passing because `ForecastUnavailableError` is a `ValueError`; add `from power_market_analytics.forecasting.strategy import ForecastUnavailableError` and tighten that test's `pytest.raises(ValueError, ...)` to `pytest.raises(ForecastUnavailableError, ...)`.

`tests/test_spot_price_scripts.py`: in `test_previous_day_over_a_pinned_window` add `assert params["n_days_skipped"] == "0"` after the `n_predictions` assertion.

Spec wording: in `docs/superpowers/specs/2026-08-18-demand-forecasting-task-design.md` (a) replace every `FeaturesUnavailableError` with `ForecastUnavailableError` and in §4.3 extend the sentence to "…raised by `predict` when the target day's features cannot be built or the visible history holds no complete training rows."; (b) in §4.3 change `build_eval_set(history, start_date, end_date, result=None)` to `build_eval_set(history, start_date, end_date, run: BacktestRun | None = None)`; (c) in §4.1 drop `issued_at(trade_date)` from the methods; (d) in §4.4 replace the bullet "Every actual row of a forecast day must have exactly one forecast, else `ValueError` (unchanged strictness for the forecast side)." with "(An actual without a forecast cannot occur: every forecast carries all 48 time codes.)". Commit the spec edit with this task.

- [ ] **Step 6: Run the suite**

Run: `uv run pytest -q -x && uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add -A power_market_analytics/forecasting/backtest.py power_market_analytics/tasks/spot_price scripts/spot_price_backtest.py tests docs/superpowers/specs
git commit -m "$(cat <<'EOF'
Move the backtest engine to forecasting.backtest with a gaps policy

Days a strategy cannot forecast are skipped (ForecastUnavailableError) and
reported on BacktestRun; forecast points without an actual are dropped.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Sliding-window LightGBM base (`forecasting/lgbm.py`) + spot strategies rebased

**Files:**
- Create: `power_market_analytics/forecasting/lgbm.py`
- Modify: `power_market_analytics/tasks/spot_price/strategies/lgbm.py` (rewrite), `power_market_analytics/tasks/spot_price/strategies/naive.py` (signature + frame names), `scripts/spot_price_backtest.py` (`run=run`)
- Modify: `tests/test_spot_price_lgbm.py`, `tests/test_spot_price_naive.py`
- Test: `tests/test_forecasting_lgbm.py` (new; the base's own guards)

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces (generic): `LGBM_PARAMS: dict`, `CALENDAR_FEATURE_COLS = ("time_code", "month", "day_of_week")`; `LightGbmEvalSetBase(DomainFrame)` with ClassVars `feature_cols`, `target_col`, `forecast_col` and `to_eval_frame() -> pd.DataFrame`; `SlidingWindowLightGbmStrategy(ForecastStrategy)` with ClassVars `feature_cols`, `eval_set_cls`, `lookback_days`, `__init__(train_window_days=730, refit_every_days=7, train_start_date=None)`, properties `shap_cols`, `target_col`, `forecast_col`, methods `predict`, `build_eval_set(history, start_date, end_date, run=None)`, `evaluate(eval_set, *, explainability_nsamples=500)`, hooks `_add_features(featured, history_df) -> pd.DataFrame` (abstract) and `_extra_params() -> dict[str, object]` (default `{}`); internals `_ensure_fitted`, `_design_matrix`, `_features`, `_log_shap_plots`, `_model`, `_trained_through`, `_fit_anchor`, `_n_fits`, `_shap_records`.
- Produces (spot, unchanged public names): `BASE_FEATURE_COLS`, `OCCTO_FEATURE_COLS`, `TARGET_COL`, `FORECAST_COL`, `LightGbmEvalSet`, `LightGbmOcctoEvalSet`, `LightGbmStrategy` (`name="lightgbm"`), `LightGbmOcctoStrategy` (`name="lightgbm_occto"`, `occto` attribute, `_join_daily_features` hook).

- [ ] **Step 1: Write the failing tests for the base's guards**

```python
# tests/test_forecasting_lgbm.py
"""Tests for the generic sliding-window LightGBM base (its own guards).

The full behaviour — refits, TreeSHAP records, eval sets, MLflow logging — is
exercised through the concrete spot_price and demand strategies in
tests/test_spot_price_lgbm.py and tests/test_demand_lgbm.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LGBM_PARAMS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.spot_price import TASK


class TestConstants:
    def test_calendar_features(self):
        assert CALENDAR_FEATURE_COLS == ("time_code", "month", "day_of_week")

    def test_lgbm_params_are_fixed_and_deterministic(self):
        assert LGBM_PARAMS == {
            "n_estimators": 500,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "random_state": 0,
            "verbose": -1,
        }


class TestEvalSetBase:
    def test_to_eval_frame_uses_the_class_columns_as_float64(self):
        class EvalSet(LightGbmEvalSetBase):
            feature_cols = ("time_code", "x")
            target_col = "y"
            forecast_col = "yhat"
            schema = {
                "trade_date": "datetime64[ns]",
                "time_code": "int64",
                "x": "int64",
                "y": "float64",
                "yhat": "float64",
            }
            keys = ["trade_date", "time_code"]
            non_null_cols = ["x", "y", "yhat"]

        es = EvalSet.from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-01-01"]),
                    "time_code": [1],
                    "x": [3],
                    "y": [1.0],
                    "yhat": [1.5],
                }
            )
        )
        frame = es.to_eval_frame()
        assert list(frame.columns) == ["time_code", "x", "y", "yhat"]
        assert frame.dtypes.astype(str).unique().tolist() == ["float64"]


class TestSlidingWindowLightGbmStrategy:
    def test_add_features_is_abstract(self):
        class NoFeatures(SlidingWindowLightGbmStrategy):
            name = "n"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

        with pytest.raises(TypeError, match="abstract"):
            NoFeatures()

    def test_extra_params_default_to_empty(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _add_features(self, featured, history_df):
                return featured

        assert Minimal()._extra_params() == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_forecasting_lgbm.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.lgbm'`.

- [ ] **Step 3: Write `forecasting/lgbm.py`**

```python
# power_market_analytics/forecasting/lgbm.py
"""Sliding-window LightGBM strategy base shared by every task.

A concrete strategy sets ``task``, ``name``, ``feature_cols``,
``eval_set_cls`` and ``lookback_days`` and implements ``_add_features`` (lags,
exogenous columns); everything else — the calendar features, periodic refits
on a trailing window, TreeSHAP recording per forecast day, replaying the
walk-forward forecasts through MLflow's static-dataset evaluation and the SHAP
summary plots — lives here.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

import lightgbm
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
import shap
from loguru import logger
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.tracking import evaluate_predictions
from power_market_analytics.forecasting.backtest import BacktestRun
from power_market_analytics.forecasting.frames import (
    GRAIN_COLS,
    N_PERIODS,
    DayAheadForecast,
    HalfHourlySeries,
)
from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError

CALENDAR_FEATURE_COLS = ("time_code", "month", "day_of_week")

# Modest, fixed hyperparameters: a handful of low-cardinality features does
# not warrant tuning machinery yet. Logged to the MLflow run in `evaluate`.
LGBM_PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "random_state": 0,
    "verbose": -1,
}


class LightGbmEvalSetBase(DomainFrame):
    """Design matrix base for evaluating a sliding-window LightGBM strategy.

    One row per forecast point: the features knowable at the task's issue
    time, the realized value (``target_col``, the task's actual column) and
    the walk-forward forecast (``forecast_col``). Concrete subclasses declare
    the explicit ``schema`` (grain, features, target, forecast) plus
    ``keys`` / ``non_null_cols``.

    Grain: (trade_date, time_code).
    """

    feature_cols: ClassVar[tuple[str, ...]]
    target_col: ClassVar[str]
    forecast_col: ClassVar[str]

    def to_eval_frame(self) -> pd.DataFrame:
        """Features, target and forecast in the layout ``mlflow`` expects.

        Drops ``trade_date``, since MLflow treats every non-target,
        non-prediction column as a feature; ``time_code`` stays because it
        genuinely is one. Everything is cast to float64 so the SHAP plots
        and the dataset profile see a uniform numeric input.

        Returns
        -------
        pandas.DataFrame
        """
        return self.df[[*self.feature_cols, self.target_col, self.forecast_col]].astype("float64")


class SlidingWindowLightGbmStrategy(ForecastStrategy):
    """LightGBM regressor over calendar features plus task-specific features.

    The model is refit every ``refit_every_days`` calendar days on a sliding
    window of the trailing ``train_window_days`` days of history (or as much
    of it as exists), so every delivery day is scored by a model fitted only
    on data published before it. Each :meth:`predict` call also records the
    exact TreeSHAP contributions of the model that scored it.

    Training and prediction rows go through the same feature builder
    (:meth:`_features`), so the two can never disagree: the base adds
    ``month`` and ``day_of_week`` and the subclass's :meth:`_add_features`
    adds its lags and exogenous columns.

    Evaluation replays the backtest's own forecasts through MLflow's
    static-dataset mode instead of re-scoring with any single model: with
    periodic refits no one model spans the window, and any single refit
    would be partly in-sample there. The logged metrics are therefore
    exactly the backtest's numbers. The SHAP plots pool the recorded
    per-day contributions, so each row is explained by the model that
    actually forecast it, out-of-sample — at the price of mixing per-model
    baselines, which is fine for the distributional beeswarm and importance
    plots but is not a single-model decomposition.

    Class Attributes
    ----------------
    feature_cols : tuple of str
        Model features, in order; must start with ``CALENDAR_FEATURE_COLS``
        or otherwise include every column :meth:`_features` produces.
    eval_set_cls : type
        ``LightGbmEvalSetBase`` subclass for this strategy's design matrix.
    lookback_days : int
        Extra days of history before the training window's first day that
        its features need (the longest lag).

    Parameters
    ----------
    train_window_days : int, optional
        Sliding training window length in calendar days.
    refit_every_days : int, optional
        Refit cadence in calendar days, anchored to the day that triggered
        the previous refit (so data gaps cannot drift it).
    train_start_date : pandas.Timestamp, optional
        First delivery day eligible as a training row. Earlier history is
        still used for lag features, it just never becomes a target. Lets a
        baseline be fitted on exactly the rows a feature-limited candidate
        can use.
    """

    feature_cols: ClassVar[tuple[str, ...]]
    eval_set_cls: ClassVar[type[LightGbmEvalSetBase]]
    lookback_days: ClassVar[int]

    def __init__(
        self,
        train_window_days: int = 730,
        refit_every_days: int = 7,
        train_start_date: pd.Timestamp | None = None,
    ) -> None:
        self.train_window_days = train_window_days
        self.refit_every_days = refit_every_days
        self.train_start_date = (
            None if train_start_date is None else pd.Timestamp(train_start_date).as_unit("ns")
        )
        self._model: lightgbm.LGBMRegressor | None = None
        self._trained_through: pd.Timestamp | None = None
        self._fit_anchor: pd.Timestamp | None = None
        self._n_fits = 0
        self._shap_records: dict[pd.Timestamp, pd.DataFrame] = {}

    @property
    def shap_cols(self) -> tuple[str, ...]:
        """Per-feature SHAP contribution column names, aligned with ``feature_cols``."""
        return tuple(f"shap_{col}" for col in self.feature_cols)

    @property
    def target_col(self) -> str:
        """The realized-value column of the design matrix (the task's actual column)."""
        return self.eval_set_cls.target_col

    @property
    def forecast_col(self) -> str:
        """The forecast column of the design matrix (the task's forecast column)."""
        return self.eval_set_cls.forecast_col

    def predict(self, target_date: pd.Timestamp, history: HalfHourlySeries) -> DayAheadForecast:
        """Score the 48 periods of one delivery day, refitting first if due.

        Also records the model's TreeSHAP contributions for the 48 rows,
        keyed by delivery day, for pooling in :meth:`evaluate`. Predicting
        the same day again overwrites its recorded contributions.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : HalfHourlySeries
            History through the task's cutoff for D.

        Returns
        -------
        DayAheadForecast
            An instance of ``task.forecast_cls``.

        Raises
        ------
        ForecastUnavailableError
            If any feature is unavailable for the target day, or the
            training window contains no complete rows.
        """
        # A string-parsed Timestamp carries second resolution; normalize so
        # the assigned trade_date column is datetime64[ns] per the contract.
        target_date = pd.Timestamp(target_date).as_unit("ns")
        self._ensure_fitted(history.df, target_date)
        points = pd.DataFrame(
            {"trade_date": target_date, "time_code": np.arange(1, N_PERIODS + 1, dtype="int64")}
        )
        featured = self._features(points, history.df)
        missing = featured[list(self.feature_cols)].isna().any()
        if missing.any():
            raise ForecastUnavailableError(
                f"{self.name}: features {list(missing[missing].index)} unavailable for "
                f"{target_date.date()}"
            )
        features = featured[list(self.feature_cols)].astype("float64")
        forecast = featured[GRAIN_COLS].assign(**{self.forecast_col: self._model.predict(features)})
        # Exact TreeSHAP from LightGBM itself: per-feature contributions
        # plus a trailing expected-value column that sum to the prediction.
        contributions = self._model.predict(features, pred_contrib=True)
        self._shap_records[target_date] = pd.concat(
            [
                forecast[GRAIN_COLS],
                # time_code is already present as an int64 key column.
                features.drop(columns=["time_code"]),
                pd.DataFrame(
                    contributions[:, : len(self.feature_cols)],
                    columns=list(self.shap_cols),
                    index=features.index,
                ),
            ],
            axis=1,
        ).assign(shap_expected_value=contributions[:, -1])
        return self.task.forecast_cls.from_df(forecast)

    def build_eval_set(
        self,
        history: HalfHourlySeries,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        run: BacktestRun | None = None,
    ) -> LightGbmEvalSetBase:
        """Assemble the MLflow design matrix for a backtest window.

        Joins the backtest's walk-forward forecasts onto the feature rows;
        the frame contract then enforces that every eval point has exactly
        one forecast. Points missing any feature — the first days of history,
        the days after a gap, a day without exogenous data — and the days
        the backtest skipped are dropped, since MLflow needs a complete
        numeric matrix and skipped days have nothing to replay.

        Parameters
        ----------
        history : HalfHourlySeries
            Full history; must cover ``start_date`` minus ``lookback_days``.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.
        run : BacktestRun
            Walk-forward forecasts from ``run_backtest`` over the same
            window. Required: no single model produced them, so evaluation
            replays them rather than re-scoring.

        Returns
        -------
        LightGbmEvalSetBase
            An instance of ``eval_set_cls``.

        Raises
        ------
        ValueError
            If ``run`` is missing, no complete rows remain in the window,
            or the forecasts do not cover every eval row.
        """
        if run is None:
            raise ValueError(
                f"{self.name}: build_eval_set requires the backtest run; "
                "run run_backtest over the same window first"
            )
        featured = self._design_matrix(history.df)
        window = featured[featured["trade_date"].between(start_date, end_date)]
        complete = window.dropna(subset=[*self.feature_cols, self.target_col])
        n_dropped = len(window) - len(complete)
        if n_dropped:
            logger.info(
                "{} eval set: dropped {} of {} rows with incomplete features",
                self.name,
                n_dropped,
                len(window),
            )
        if run.skipped_days:
            n_before = len(complete)
            complete = complete[~complete["trade_date"].isin(run.skipped_days)]
            logger.info(
                "{} eval set: dropped {} rows on {} skipped days",
                self.name,
                n_before - len(complete),
                len(run.skipped_days),
            )
        if complete.empty:
            raise ValueError(
                f"{self.name}: no complete feature rows between "
                f"{start_date.date()} and {end_date.date()}"
            )
        # A left-joined feature is float64 whenever any row of the full
        # history lacked it; restore the contract dtype now that only
        # complete rows remain.
        schema = self.eval_set_cls.schema
        complete = complete.astype({col: schema[col] for col in self.feature_cols})
        merged = complete.merge(
            run.result.df[[*GRAIN_COLS, self.forecast_col]],
            how="left",
            on=GRAIN_COLS,
            validate="one_to_one",
        )
        logger.info(
            "{} eval set: {} rows, {} features", self.name, len(merged), len(self.feature_cols)
        )
        return self.eval_set_cls.from_df(merged)

    def evaluate(
        self,
        eval_set: LightGbmEvalSetBase,
        *,
        explainability_nsamples: int = 500,
    ) -> EvaluationResult:
        """Evaluate the walk-forward forecasts and explain them with MLflow.

        Must be called inside an active MLflow run, after a backtest. The
        metrics come from MLflow's static-dataset mode over the forecast
        column of ``eval_set``, so they are exactly the backtest's numbers;
        the SHAP plots pool the per-day contributions recorded by
        :meth:`predict`. The final refit's booster is logged for reference
        and serving, but computes nothing here.

        Parameters
        ----------
        eval_set : LightGbmEvalSetBase
            Design matrix from :meth:`build_eval_set`.
        explainability_nsamples : int, optional
            Rows sampled for the beeswarm plot (rendering cost only); the
            feature-importance plot always uses every row.

        Returns
        -------
        mlflow.models.EvaluationResult

        Raises
        ------
        RuntimeError
            If no backtest has recorded a model and contributions, or the
            recorded contributions do not cover the eval rows.
        """
        if self._model is None or not self._shap_records:
            raise RuntimeError(
                f"{self.name}: no fitted model or recorded contributions; run the backtest first"
            )
        eval_frame = eval_set.to_eval_frame()
        mlflow.log_params(
            {
                **{f"lgbm_{key}": value for key, value in LGBM_PARAMS.items()},
                "lgbm_train_window_days": self.train_window_days,
                "lgbm_refit_every_days": self.refit_every_days,
                "lgbm_train_start_date": (
                    "none" if self.train_start_date is None else str(self.train_start_date.date())
                ),
                "lgbm_feature_cols": ",".join(self.feature_cols),
                **self._extra_params(),
            }
        )
        mlflow.log_metric("n_refits", self._n_fits)
        mlflow.lightgbm.log_model(
            self._model,
            name=f"{self.name}_model",
            input_example=eval_frame[list(self.feature_cols)].head(),
        )
        self._log_shap_plots(eval_set, nsamples=explainability_nsamples)
        return evaluate_predictions(
            eval_frame, targets=self.target_col, predictions=self.forecast_col
        )

    def _extra_params(self) -> dict[str, object]:
        """Strategy-specific run params logged next to the ``lgbm_*`` ones.

        Returns
        -------
        dict of str to object
            Empty by default.
        """
        return {}

    def _log_shap_plots(self, eval_set: LightGbmEvalSetBase, *, nsamples: int) -> None:
        """Pool the per-day TreeSHAP contributions and log summary plots.

        Every eval row is explained by the model that actually forecast it.
        Artifact names follow MLflow's shap evaluator so runs stay
        comparable across strategies.

        Parameters
        ----------
        eval_set : LightGbmEvalSetBase
            Eval rows to align the recorded contributions against.
        nsamples : int
            Beeswarm sample size; the importance bars use every row.

        Raises
        ------
        RuntimeError
            If the recorded contributions do not cover every eval row.
        """
        pooled = pd.concat(self._shap_records.values(), ignore_index=True)
        aligned = eval_set.df[GRAIN_COLS].merge(
            pooled,
            how="inner",
            on=GRAIN_COLS,
            validate="one_to_one",
        )
        if len(aligned) != len(eval_set):
            raise RuntimeError(
                f"{self.name}: recorded contributions cover {len(aligned)} of "
                f"{len(eval_set)} eval rows; backtest and eval windows disagree"
            )
        feature_cols = list(self.feature_cols)
        shap_cols = list(self.shap_cols)
        sample = aligned.sample(n=min(nsamples, len(aligned)), random_state=0)
        shap.summary_plot(sample[shap_cols].to_numpy(), sample[feature_cols], show=False)
        mlflow.log_figure(plt.gcf(), "shap_beeswarm_plot.png")
        plt.close("all")
        shap.summary_plot(
            aligned[shap_cols].to_numpy(),
            aligned[feature_cols],
            plot_type="bar",
            show=False,
        )
        mlflow.log_figure(plt.gcf(), "shap_feature_importance_plot.png")
        plt.close("all")

    def _ensure_fitted(self, history: pd.DataFrame, target_date: pd.Timestamp) -> None:
        """Refit the sliding-window model for a target day if due.

        A refit is due when there is no model yet, the refit cadence has
        elapsed since the day that triggered the last one, or the cached
        model saw data at or after ``target_date`` (i.e. reusing it would
        leak the future). The window never reaches back before
        ``train_start_date``.

        Parameters
        ----------
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout; days after
            the task's cutoff for ``target_date`` must already be absent.
        target_date : pandas.Timestamp
            Delivery day about to be forecast.

        Raises
        ------
        ForecastUnavailableError
            If the training window contains no complete rows.
        """
        due = (
            self._model is None
            or target_date <= self._trained_through
            or target_date >= self._fit_anchor + pd.Timedelta(days=self.refit_every_days)
        )
        if not due:
            return
        window_start = target_date - pd.Timedelta(days=self.train_window_days)
        if self.train_start_date is not None:
            window_start = max(window_start, self.train_start_date)
        # Extra days of history so the window's first day keeps its lags.
        recent = history[history["trade_date"] >= window_start - pd.Timedelta(days=self.lookback_days)]
        train = self._design_matrix(recent)
        train = train[train["trade_date"] >= window_start].dropna(
            subset=[*self.feature_cols, self.target_col]
        )
        if train.empty:
            raise ForecastUnavailableError(
                f"{self.name}: no complete training rows in the "
                f"{self.train_window_days} days before {target_date.date()}"
            )
        model = lightgbm.LGBMRegressor(**LGBM_PARAMS)
        model.fit(train[list(self.feature_cols)].astype("float64"), train[self.target_col])
        self._model = model
        self._trained_through = train["trade_date"].max()
        self._fit_anchor = target_date
        self._n_fits += 1
        logger.info(
            "{}: refit #{} on {} rows ({}..{})",
            self.name,
            self._n_fits,
            len(train),
            train["trade_date"].min().date(),
            self._trained_through.date(),
        )

    def _design_matrix(self, history: pd.DataFrame) -> pd.DataFrame:
        """Features and target for every (trade_date, time_code) point of ``history``.

        Rows missing a feature (no lag day, no exogenous row) keep NaN
        there; callers decide whether to drop them.

        Parameters
        ----------
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout.

        Returns
        -------
        pandas.DataFrame
            Grain columns, ``feature_cols`` and ``target_col``.
        """
        return self._features(
            history.rename(columns={self.task.value_col: self.target_col}), history
        )

    def _features(self, points: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach every feature column to a set of (trade_date, time_code) points.

        The single feature path for both training rows and the target day's
        48 prediction rows, so the two can never disagree.

        Parameters
        ----------
        points : pandas.DataFrame
            Rows keyed on (trade_date, time_code); other columns pass through.
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout, for lags.

        Returns
        -------
        pandas.DataFrame
            ``points`` plus ``feature_cols`` (NaN where unavailable).
        """
        featured = points.assign(
            month=points["trade_date"].dt.month.astype("int64"),
            day_of_week=points["trade_date"].dt.dayofweek.astype("int64"),
        )
        return self._add_features(featured, history)

    @abstractmethod
    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the strategy's own features (lags, exogenous columns).

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) carrying the calendar
            features.
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's remaining ``feature_cols``
            (NaN where unavailable).
        """
```

- [ ] **Step 4: Rewrite `spot_price/strategies/lgbm.py`**

```python
"""Gradient-boosted tree strategies over calendar, lag and OCCTO features."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.features import join_lag
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import OcctoDemandForecast

BASE_FEATURE_COLS = (*CALENDAR_FEATURE_COLS, "lag_1d_price")
OCCTO_FEATURE_COLS = ("max_demand_hour_ending", "max_demand_mw", "max_supply_capacity_mw")
TARGET_COL = TASK.actual_col
FORECAST_COL = TASK.forecast_col


class LightGbmEvalSet(LightGbmEvalSetBase):
    """Design matrix for evaluating :class:`LightGbmStrategy` with MLflow.

    One row per forecast point, holding the features knowable at 9:55 JST on
    D-1, the realized price, and the walk-forward forecast the backtest
    produced for that point. Unlike the naive eval set, ``time_code`` is a
    model feature here as well as a grain column.

    Grain: (trade_date, time_code).
    """

    feature_cols = BASE_FEATURE_COLS
    target_col = TARGET_COL
    forecast_col = FORECAST_COL
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        "lag_1d_price": "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    keys = list(GRAIN_COLS)
    non_null_cols = [*BASE_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmOcctoEvalSet(LightGbmEvalSet):
    """Design matrix for :class:`LightGbmOcctoStrategy`: the base features
    plus the OCCTO 翌々日 peak-demand/supply forecast for the delivery day.

    Grain: (trade_date, time_code).
    """

    feature_cols = (*BASE_FEATURE_COLS, *OCCTO_FEATURE_COLS)
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        "lag_1d_price": "float64",
        "max_demand_hour_ending": "int64",
        "max_demand_mw": "int64",
        "max_supply_capacity_mw": "int64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*feature_cols, TARGET_COL, FORECAST_COL]


class LightGbmStrategy(SlidingWindowLightGbmStrategy):
    """LightGBM regressor on time code, month, day of week and the 1-day lag.

    See :class:`SlidingWindowLightGbmStrategy` for the refit schedule, the
    TreeSHAP records and the evaluation. A subclass that adds per-day
    exogenous features — see :class:`LightGbmOcctoStrategy` — overrides
    :meth:`_join_daily_features` plus the ``feature_cols`` /
    ``eval_set_cls`` class attributes.
    """

    name = "lightgbm"
    task = TASK
    feature_cols = BASE_FEATURE_COLS
    eval_set_cls = LightGbmEvalSet
    # The 1-day lag needs one extra day before the training window.
    lookback_days = 1

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the D-1 price lag, then any per-day exogenous features.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Price history in the ``SpotPrices`` layout.

        Returns
        -------
        pandas.DataFrame
        """
        featured = join_lag(
            featured, history, value_col=self.task.value_col, days=1, name="lag_1d_price"
        )
        return self._join_daily_features(featured)

    def _join_daily_features(self, featured: pd.DataFrame) -> pd.DataFrame:
        """Hook for per-delivery-day exogenous features; identity here.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the base features.

        Returns
        -------
        pandas.DataFrame
        """
        return featured


class LightGbmOcctoStrategy(LightGbmStrategy):
    """:class:`LightGbmStrategy` plus OCCTO 翌々日 peak-demand/supply features.

    Experiment E-001 of docs/research/R-001-supply-demand-tightness.md: the
    OCCTO forecast for delivery day D (published D-2 ~17:45 JST, before the
    D-1 09:55 cutoff) is joined to D's 48 rows, adding
    ``max_demand_hour_ending``, ``max_demand_mw`` and
    ``max_supply_capacity_mw`` to the feature set. Model parameters, refit
    cadence and the base features are unchanged.

    Because rows without an OCCTO forecast are dropped from training, this
    strategy's training set starts on the first OCCTO day (2024-04-01)
    however long the price history is; a matched baseline must be run with
    the same ``train_start_date`` explicitly.

    Parameters
    ----------
    occto : OcctoDemandForecast
        OCCTO forecasts for the same area as the prices being forecast.
    **kwargs
        Forwarded to :class:`LightGbmStrategy`.
    """

    name = "lightgbm_occto"
    feature_cols = (*BASE_FEATURE_COLS, *OCCTO_FEATURE_COLS)
    eval_set_cls = LightGbmOcctoEvalSet

    def __init__(self, occto: OcctoDemandForecast, **kwargs) -> None:
        super().__init__(**kwargs)
        self.occto = occto

    def _join_daily_features(self, featured: pd.DataFrame) -> pd.DataFrame:
        """Left-join the delivery day's OCCTO forecast onto every row.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the base features.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus ``OCCTO_FEATURE_COLS`` (NaN on days without a
            forecast).
        """
        return featured.merge(self.occto.df, how="left", on="trade_date", validate="many_to_one")
```

- [ ] **Step 5: Rebase `spot_price/strategies/naive.py`**

Edit the file so that:
- imports become
  ```python
  from power_market_analytics.common.frames import DomainFrame
  from power_market_analytics.common.tracking import evaluate_regressor
  from power_market_analytics.forecasting.backtest import BacktestRun
  from power_market_analytics.forecasting.features import join_lag
  from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError
  from power_market_analytics.tasks.spot_price import TASK
  from power_market_analytics.tasks.spot_price.frames import SpotPriceForecast, SpotPrices
  ```
  (drop `BacktestResult`/`DayAheadForecast` imports);
- `TARGET_COL = TASK.actual_col` (was the literal `"actual_price_jpy_kwh"`; same value);
- `predict` returns `SpotPriceForecast.from_df(forecast)` and renames with `columns={TASK.value_col: TASK.forecast_col}`; the raise is `ForecastUnavailableError` (Task 6);
- `build_eval_set(self, history: SpotPrices, start_date, end_date, run: BacktestRun | None = None) -> PreviousDayEvalSet` — body uses `history.df`; docstring: `history` "Full price history; must cover ``start_date`` minus 1 day." and `run` "Unused: the logged pyfunc model restates this strategy's rule exactly, so evaluation re-scores the model instead of replaying the backtest's predictions."; the `join_lag` call is `.pipe(join_lag, df, value_col=TASK.value_col, days=1, name="lag_1d_price")`.

- [ ] **Step 6: Update the script and the tests**

`scripts/spot_price_backtest.py`: `strategy.build_eval_set(prices, start_date=start_date, end_date=end_date, result=result)` → `strategy.build_eval_set(prices, start_date=start_date, end_date=end_date, run=run)`.

`tests/test_spot_price_lgbm.py`:
- add `from power_market_analytics.forecasting.backtest import BacktestRun` and `from power_market_analytics.forecasting.strategy import ForecastUnavailableError`;
- `backtested` fixture: `run = run_backtest(strategy, prices, WINDOW_START, WINDOW_END)`; `return strategy, run` (type hint `tuple[LightGbmStrategy, BacktestRun]`); in `test_replays_the_backtest_forecasts_onto_the_feature_rows`: `strategy, run = backtested`, `eval_set = strategy.build_eval_set(prices, WINDOW_START, WINDOW_END, run=run)`, then `result = run.result` before the existing `merged = eval_set.df.merge(result.df, ...)`;
- replace `hand_backtest_result()` by
  ```python
  def hand_backtest_run() -> BacktestRun:
      """A minimal, valid BacktestRun for tests that only need *some* run."""
      result = BacktestResult.from_df(
          pd.DataFrame(
              {
                  "trade_date": pd.to_datetime(["2024-04-01"]),
                  "time_code": [1],
                  "actual_price_jpy_kwh": [10.0],
                  "forecast_price_jpy_kwh": [10.5],
              }
          )
      )
      return BacktestRun(result=result, skipped_days=())
  ```
  and every `result=hand_backtest_result()` → `run=hand_backtest_run()`;
- `test_requires_the_backtest_result` → `match="lightgbm: build_eval_set requires the backtest run"`;
- `test_rows_with_forecasts_missing_are_rejected`: `run = run_backtest(strategy, prices, pd.Timestamp("2024-03-03"), pd.Timestamp("2024-03-04"))` and `run=run` in the `build_eval_set` call;
- every other `result = run_backtest(...)` + `build_eval_set(..., result=result)` pair (TestEvaluate ×3, TestLightGbmOcctoStrategy ×2) → `run = run_backtest(...)` + `run=run`; where the test then reads `result.df` use `run.result.df`;
- the two-run concatenation in `TestLightGbmOcctoStrategy` (`first = run_backtest(...)`, `second = run_backtest(...)`, `result = BacktestResult.from_df(pd.concat([first.df, second.df], ...))`) becomes
  ```python
        first = run_backtest(strategy, prices, WINDOW_START, pd.Timestamp("2024-04-05"))
        second = run_backtest(strategy, prices, pd.Timestamp("2024-04-06"), pd.Timestamp("2024-04-10"))
        run = BacktestRun(
            result=BacktestResult.from_df(
                pd.concat([first.result.df, second.result.df], ignore_index=True)
            ),
            skipped_days=(),
        )
        eval_set = strategy.build_eval_set(prices, WINDOW_START, pd.Timestamp("2024-04-10"), run=run)
  ```
  (keep whatever the second call's actual dates are in the file — only the plumbing changes);
- `test_missing_previous_day_raises` becomes
  ```python
    def test_missing_previous_day_is_unforecastable(self, prices):
        strategy = LightGbmStrategy(train_window_days=30)
        # History stops two days before the target: enough to fit, but D-1 is absent.
        history = history_before(prices, D - pd.Timedelta(days=1))
        with pytest.raises(
            ForecastUnavailableError,
            match=r"lightgbm: features \['lag_1d_price'\] unavailable for 2024-04-10",
        ):
            strategy.predict(D, history)
  ```
- `test_feature_unavailable_for_target_day_raises`, `test_train_start_date_after_the_history_raises`, `test_history_of_a_single_day_has_no_complete_rows`: change `pytest.raises(ValueError, …)` to `pytest.raises(ForecastUnavailableError, …)` (messages unchanged);
- add to `TestBuildEvalSet`:
  ```python
    def test_skipped_days_are_dropped_from_the_eval_set(self, prices):
        strategy = LightGbmStrategy(train_window_days=30)
        run = run_backtest(strategy, prices, WINDOW_START, pd.Timestamp("2024-04-03"))
        skipped = BacktestRun(
            result=BacktestResult.from_df(
                run.result.df[run.result.df["trade_date"] != pd.Timestamp("2024-04-02")]
            ),
            skipped_days=(pd.Timestamp("2024-04-02"),),
        )
        eval_set = strategy.build_eval_set(prices, WINDOW_START, pd.Timestamp("2024-04-03"), run=skipped)
        assert len(eval_set) == 2 * 48
        assert pd.Timestamp("2024-04-02") not in set(eval_set.df["trade_date"])
  ```

`tests/test_spot_price_naive.py`: every `build_eval_set(..., result=...)` → `run=...` (grep; the naive strategy ignores it), and every `DayAheadForecast` → `SpotPriceForecast` in imports/`isinstance`/`match=` strings.

- [ ] **Step 7: Run the suite**

Run: `uv run pytest -q -x && uv run ruff check .`
Expected: all pass, ruff clean. If a `test_spot_price_lgbm.py` assertion on `training_rows` or `_trained_through` differs, the cause is `lookback_days` (must be 1) or the training slice — do not loosen the tests.

- [ ] **Step 8: Commit**

```bash
git add -A power_market_analytics/forecasting/lgbm.py power_market_analytics/tasks/spot_price scripts/spot_price_backtest.py tests/test_forecasting_lgbm.py tests/test_spot_price_lgbm.py tests/test_spot_price_naive.py
git commit -m "$(cat <<'EOF'
Extract SlidingWindowLightGbmStrategy; rebase the spot strategies on it

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Generic warehouse publish (`forecasting/publish.py`)

**Files:**
- Create: `power_market_analytics/forecasting/publish.py`
- Delete: `power_market_analytics/tasks/spot_price/publish.py`
- Modify: `scripts/spot_price_backtest.py`
- Move: `tests/test_spot_price_publish.py` → `tests/test_forecasting_publish.py`

**Interfaces:**
- Produces: `build_forecast_records(task: TaskSpec, result: BacktestResult, *, run_id: str, strategy: str, area_code: str) -> ForecastRecords` (instance of `task.records_cls`; `forecast_issued_ts = trade_date + task.issue_offset`; `published_at` = one naive-JST now per call) and `publish_forecast_records(task: TaskSpec, records: ForecastRecords, spark: SparkSession | None = None) -> int` (creates `task.forecast_table` — parquet, partitioned by `run_id`, forecast column named `task.forecast_col` — and overwrites only the run's partition).

- [ ] **Step 1: Move and adapt the tests**

```bash
git mv tests/test_spot_price_publish.py tests/test_forecasting_publish.py
```

Edit `tests/test_forecasting_publish.py`:
- module docstring: `"""Tests for the forecast write-back, exercised through the spot task (``pma_ml.spot_price_forecast``)."""` (keep the paragraph about run ids being unique to the module);
- imports:
  ```python
  from power_market_analytics.forecasting.publish import build_forecast_records, publish_forecast_records
  from power_market_analytics.tasks.spot_price import TASK
  from power_market_analytics.tasks.spot_price.frames import (
      SpotPriceBacktestResult,
      SpotPriceForecastRecords,
  )

  FORECAST_TABLE = TASK.forecast_table
  ```
- `make_result` builds `SpotPriceBacktestResult`; the `isinstance(records, ForecastRecords)` check becomes `SpotPriceForecastRecords`;
- every `build_forecast_records(make_result(...), run_id=..., ...)` gains `TASK` as the first positional argument: `build_forecast_records(TASK, make_result(...), run_id=..., ...)`; every `publish_forecast_records(records, spark=spark)` / `publish_forecast_records(records)` becomes `publish_forecast_records(TASK, records, spark=spark)` / `publish_forecast_records(TASK, records)`;
- add one test to `TestBuildForecastRecords`:
  ```python
    def test_issue_time_comes_from_the_task_spec(self):
        # A task issuing at 09:30 two days ahead stamps that instead of spot's 09:55.
        other = dataclasses.replace(
            TASK, issue_offset=pd.Timedelta(days=-2, hours=9, minutes=30)
        )
        records = build_forecast_records(
            other, make_result(["2024-04-10"], [1]), run_id="r", strategy="s", area_code="tokyo"
        )
        assert records.df["forecast_issued_ts"].iloc[0] == pd.Timestamp("2024-04-08 09:30")
  ```
  with `import dataclasses` at the top.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_forecasting_publish.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.publish'`.

- [ ] **Step 3: Write `forecasting/publish.py`, delete the spot one**

```python
# power_market_analytics/forecasting/publish.py
"""Write backtest forecasts back to the Spark warehouse.

MLflow stays the system of record for the experiment (params, metrics,
artifacts); the warehouse holds the row-level forecasts so dbt can join them
to actuals and dimensions and Superset can chart them. ``run_id`` is the link
between the two systems.

Each task has its own destination table (``TaskSpec.forecast_table``) with
its own forecast column name; the table is partitioned by ``run_id`` and
written with dynamic partition overwrite, so republishing a run replaces
exactly that run's rows.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.forecasting.frames import BacktestResult, ForecastRecords
from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.spark import get_spark_session


def build_forecast_records(
    task: TaskSpec, result: BacktestResult, *, run_id: str, strategy: str, area_code: str
) -> ForecastRecords:
    """Shape a backtest result into warehouse write-back records.

    Parameters
    ----------
    task : TaskSpec
        Task the result belongs to; supplies the issue offset and the
        records frame class.
    result : BacktestResult
        Forecasts joined to actuals; the actuals column is dropped here —
        the warehouse table stores forecasts only.
    run_id : str
        MLflow run id the forecasts belong to.
    strategy : str
        Strategy registry key, e.g. ``previous_day``.
    area_code : str
        dim_area.area_code value the run forecast, e.g. ``tokyo``.

    Returns
    -------
    ForecastRecords
        An instance of ``task.records_cls``.
    """
    df = result.df.assign(
        run_id=run_id,
        strategy=strategy,
        area_code=area_code,
        forecast_issued_ts=lambda d: d["trade_date"] + task.issue_offset,
        # Naive JST like every other warehouse timestamp; one value per run so
        # BI tools can label runs (a republish refreshes it).
        published_at=pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None),
    ).astype({"published_at": "datetime64[ns]"})
    return task.records_cls.from_df(df)


def publish_forecast_records(
    task: TaskSpec, records: ForecastRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's forecasts to ``task.forecast_table``.

    Creates the table's database and the table (parquet, partitioned by
    ``run_id``) if they do not exist, then overwrites only the partitions
    present in ``records`` — republishing a run replaces its rows without
    touching other runs.

    Parameters
    ----------
    task : TaskSpec
        Task being published; supplies the table and forecast column names.
    records : ForecastRecords
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
    table = task.forecast_table
    database = table.split(".")[0]
    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    # Explicit DDL (rather than saveAsTable schema inference) so the table
    # schema is stable across writers; the partition column must come last to
    # line up with insertInto's positional semantics.
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
          strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          {task.forecast_col} double,
          published_at timestamp,
          run_id string
        )
        USING parquet
        PARTITIONED BY (run_id)
        """
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
    # "dynamic" scopes the overwrite to the partitions being written; the
    # default ("static") would truncate every other run's partition too.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    sdf.write.mode("overwrite").insertInto(table)
    logger.info(
        "Published {} rows to {} (run_id={})", len(records), table, records.df["run_id"].iloc[0]
    )
    return len(records)
```

```bash
git rm power_market_analytics/tasks/spot_price/publish.py
```

- [ ] **Step 4: Update the script**

`scripts/spot_price_backtest.py`: replace the `from power_market_analytics.tasks.spot_price.publish import (FORECAST_TABLE, build_forecast_records, publish_forecast_records)` import with `from power_market_analytics.forecasting.publish import build_forecast_records, publish_forecast_records`; the calls become `build_forecast_records(TASK, result, run_id=run.info.run_id, strategy=args.strategy, area_code=args.area)` (rename the MLflow `run` context variable to `mlflow_run` first — `with task_run(...) as mlflow_run:` and `mlflow_run.info.run_id` — so it does not shadow the `BacktestRun`), `publish_forecast_records(TASK, records)`, `mlflow.set_tag("warehouse_table", TASK.forecast_table)`, and the final log line uses `TASK.forecast_table`. Import `TASK` next to `MLFLOW_EXPERIMENT`.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_forecasting_publish.py tests/test_spot_price_scripts.py -q && uv run pytest -q -x && uv run ruff check .`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add -A power_market_analytics/forecasting/publish.py power_market_analytics/tasks/spot_price scripts/spot_price_backtest.py tests/test_forecasting_publish.py tests/test_spot_price_publish.py
git commit -m "$(cat <<'EOF'
Move the warehouse publish path to forecasting.publish (TaskSpec-driven)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Generic plots (`forecasting/plots.py`) + alias cleanup

**Files:**
- Create: `power_market_analytics/forecasting/plots.py`
- Delete: `power_market_analytics/tasks/spot_price/plots.py`
- Modify: `power_market_analytics/tasks/spot_price/frames.py` (drop aliases/re-exports), `scripts/spot_price_backtest.py`, any test still using an alias
- Move: `tests/test_spot_price_plots.py` → `tests/test_forecasting_plots.py`

**Interfaces:**
- Produces: `SEQUENTIAL_BLUES`, `SEQUENTIAL_AQUAS`, `SURFACE`, `INK_PRIMARY`, `INK_SECONDARY`, `INK_MUTED`, `FONT_FAMILY`; `metric_by_year_time_code(result: BacktestResult, metric) -> MetricByYearTimeCode`; `error_heatmaps(task: TaskSpec, result: BacktestResult, title: str) -> plotly.graph_objects.Figure` (MAE panel unit = `task.unit`); private `_period_label`, `_colorscale`.

- [ ] **Step 1: Move and adapt the tests**

```bash
git mv tests/test_spot_price_plots.py tests/test_forecasting_plots.py
```

Edit `tests/test_forecasting_plots.py`: imports become
```python
from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.forecasting.frames import MetricByYearTimeCode
from power_market_analytics.forecasting.plots import (
    SEQUENTIAL_AQUAS,
    SEQUENTIAL_BLUES,
    _colorscale,
    _period_label,
    error_heatmaps,
    metric_by_year_time_code,
)
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import SpotPriceBacktestResult
```
`BacktestResult` → `SpotPriceBacktestResult` throughout; every `error_heatmaps(x, "title")` → `error_heatmaps(TASK, x, "title")`. Add to `TestErrorHeatmaps`:
```python
    def test_mae_unit_comes_from_the_task(self):
        import dataclasses

        kwh = dataclasses.replace(TASK, unit="kWh")
        fig = error_heatmaps(kwh, full_day_result([Y2024A]), "t")
        assert fig.data[0].colorbar.title.text == "kWh"
        assert "MAE: %{z:.2f} kWh" in fig.data[0].hovertemplate
        assert [a.text for a in fig.layout.annotations] == ["MAE (kWh)", "MAPE (%)"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_forecasting_plots.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.forecasting.plots'`.

- [ ] **Step 3: Create `forecasting/plots.py` from the spot module**

```bash
git mv power_market_analytics/tasks/spot_price/plots.py power_market_analytics/forecasting/plots.py
```

Then edit `power_market_analytics/forecasting/plots.py`:
- docstring first line → `"""Interactive visualizations for backtests (any task)."""` (keep the rest);
- imports: replace the `power_market_analytics.tasks.spot_price.frames` import with
  ```python
  from power_market_analytics.forecasting.frames import (
      N_PERIODS,
      BacktestResult,
      MetricByYearTimeCode,
  )
  from power_market_analytics.forecasting.task import TaskSpec
  ```
- `metric_by_year_time_code(result, metric)`: replace the hard-coded columns with `actual_col, forecast_col = type(result).actual_col, type(result).forecast_col` and `metric(g[actual_col], g[forecast_col])`; docstring `result : BacktestResult` → "Any task's backtest result; the actual/forecast columns are read off its class.";
- `error_heatmaps(task: TaskSpec, result: BacktestResult, title: str)`: add the `task` parameter (docstring: "Task the result belongs to; labels the MAE panel with ``task.unit``.") and change the panels to
  ```python
    panels = [
        ("MAE", task.unit, mae, SEQUENTIAL_BLUES),
        ("MAPE", "%", mape, SEQUENTIAL_AQUAS),
    ]
  ```

- [ ] **Step 4: Update the script and drop the transitional aliases**

`scripts/spot_price_backtest.py`: `from power_market_analytics.forecasting.plots import error_heatmaps`; call `error_heatmaps(TASK, result, title=...)`.

`power_market_analytics/tasks/spot_price/frames.py`: delete the `generic` module import, `N_PERIODS`, `MetricByYearTimeCode` import, the `__all__` list and the three alias lines; the subclasses import their bases directly:
```python
from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)
```
and inherit `class SpotPriceForecast(DayAheadForecast)`, `class SpotPriceBacktestResult(BacktestResult)`, `class SpotPriceForecastRecords(ForecastRecords)`.

Then sweep: `grep -rn "spot_price.frames import" power_market_analytics scripts tests | grep -E "DayAheadForecast|BacktestResult|ForecastRecords|N_PERIODS|MetricByYearTimeCode"` — for each hit, import `SpotPriceForecast` / `SpotPriceBacktestResult` / `SpotPriceForecastRecords` instead (and `N_PERIODS` / `MetricByYearTimeCode` from `power_market_analytics.forecasting.frames`), and rename the uses in that file (`isinstance`, `from_df`, type hints, `match=` strings that spell the class name). Expected hits: `tests/test_spot_price_lgbm.py`, `tests/test_spot_price_naive.py` (if not done in Task 7), `tests/test_spot_price_strategies.py`, `tests/test_spot_price_scripts.py` (check), `power_market_analytics/tasks/spot_price/strategies/__init__.py` (check).

Finally `grep -rn "tasks.spot_price.\(backtest\|features\|publish\|plots\)\|strategies.base" power_market_analytics scripts tests` must return nothing.

- [ ] **Step 5: Run the full suite with coverage**

Run: `just test -q && uv run ruff check .`
Expected: everything passes, coverage 100 %, ruff clean. If a `forecasting/*` line is uncovered, add the missing test to the corresponding `tests/test_forecasting_*.py` (do not add `pragma: no cover`).

- [ ] **Step 6: Commit**

```bash
git add -A power_market_analytics/forecasting power_market_analytics/tasks/spot_price scripts/spot_price_backtest.py tests
git commit -m "$(cat <<'EOF'
Move the error heatmaps to forecasting.plots; finish the spot_price migration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Demand task definition and frames

**Files:**
- Modify: `power_market_analytics/tasks/demand/__init__.py` (replace the bare-package stub)
- Create: `power_market_analytics/tasks/demand/frames.py`
- Move: `tests/test_demand.py` → `tests/test_demand_task.py` (rewrite)
- Test: `tests/test_demand_frames.py`

**Interfaces:**
- Produces: `tasks.demand.TASK: TaskSpec` (name `demand`, unit `kWh`, lead 2, offset `Timedelta(days=-1, hours=9, minutes=30)`, table `pma_ml.demand_forecast`), `MLFLOW_EXPERIMENT = TASK.name`; frames `AreaDemand(HalfHourlySeries)` (`value_col="demand_kwh"`), `DemandForecast(DayAheadForecast)` (`forecast_col="forecast_demand_kwh"`), `DemandBacktestResult(BacktestResult)` (`actual_col="actual_demand_kwh"`), `DemandForecastRecords(ForecastRecords)`, `AreaTemperature(DomainFrame)` (schema `obs_date: datetime64[ns]`, `hour_ending: int64`, `temperature_c: float64`; keys `obs_date, hour_ending`; `temperature_c` nullable; `hour_ending` in 1..24).

- [ ] **Step 1: Write the failing tests**

```bash
git mv tests/test_demand.py tests/test_demand_task.py
```

```python
# tests/test_demand_task.py
"""Tests for the demand task definition (TaskSpec + experiment name)."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks import demand, spot_price
from power_market_analytics.tasks.demand import MLFLOW_EXPERIMENT, TASK
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
)


class TestDemandTask:
    def test_spec(self):
        assert isinstance(TASK, TaskSpec)
        assert TASK.name == "demand"
        assert MLFLOW_EXPERIMENT == "demand"
        assert TASK.unit == "kWh"
        assert TASK.history_lead_days == 2
        assert TASK.issue_offset == pd.Timedelta(days=-1, hours=9, minutes=30)
        assert TASK.forecast_table == "pma_ml.demand_forecast"
        assert TASK.history_cls is AreaDemand
        assert TASK.forecast_cls is DemandForecast
        assert TASK.result_cls is DemandBacktestResult
        assert TASK.records_cls is DemandForecastRecords

    def test_column_names(self):
        assert TASK.value_col == "demand_kwh"
        assert TASK.actual_col == "actual_demand_kwh"
        assert TASK.forecast_col == "forecast_demand_kwh"

    def test_history_visible_at_9_30_on_d_minus_1_ends_at_d_minus_2(self):
        # A TSO's file for D-1 only finalises after midnight of D.
        assert TASK.history_cutoff(pd.Timestamp("2024-04-10")) == pd.Timestamp("2024-04-08")

    def test_experiment_is_distinct_from_spot_price(self):
        assert demand.MLFLOW_EXPERIMENT != spot_price.MLFLOW_EXPERIMENT
```

```python
# tests/test_demand_frames.py
"""Tests for the demand task's frames (the generic bases are tested elsewhere)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaTemperature,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
)

D1 = pd.Timestamp("2024-04-01").as_unit("ns")


class TestSeriesFrames:
    def test_area_demand_contract(self):
        assert AreaDemand.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "demand_kwh": "float64",
        }
        assert AreaDemand.non_null_cols == ["demand_kwh"]

    def test_forecast_result_and_records_share_the_forecast_column(self):
        assert DemandForecast.forecast_col == "forecast_demand_kwh"
        assert DemandBacktestResult.forecast_col == "forecast_demand_kwh"
        assert DemandBacktestResult.actual_col == "actual_demand_kwh"
        assert DemandForecastRecords.forecast_col == "forecast_demand_kwh"
        assert "forecast_demand_kwh" in DemandForecastRecords.schema

    def test_area_demand_accepts_a_day(self):
        df = pd.DataFrame(
            {
                "trade_date": [D1] * 48,
                "time_code": np.arange(1, 49, dtype="int64"),
                "demand_kwh": np.full(48, 15_000_000.0),
            }
        )
        assert len(AreaDemand.from_df(df)) == 48


def temperature_df(hours: list[int], temps: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "obs_date": [D1] * len(hours),
            "hour_ending": np.array(hours, dtype="int64"),
            "temperature_c": np.array(temps, dtype="float64"),
        }
    )


class TestAreaTemperature:
    def test_grain_is_observation_day_and_hour_ending(self):
        assert AreaTemperature.keys == ["obs_date", "hour_ending"]
        out = AreaTemperature.from_df(temperature_df([1, 24], [5.0, 7.5]))
        assert list(out.df.columns) == ["obs_date", "hour_ending", "temperature_c"]

    def test_missing_temperature_is_allowed(self):
        out = AreaTemperature.from_df(temperature_df([1, 2], [5.0, np.nan]))
        assert out.df["temperature_c"].isna().tolist() == [False, True]

    @pytest.mark.parametrize("hour", [0, 25])
    def test_hour_ending_outside_1_24_rejected(self, hour):
        with pytest.raises(ValueError, match=f"hour_ending outside 1..24: \\[{hour}\\]"):
            AreaTemperature.from_df(temperature_df([hour], [5.0]))

    def test_duplicate_day_hour_rejected(self):
        with pytest.raises(ValueError, match="grain .* not unique"):
            AreaTemperature.from_df(temperature_df([1, 1], [5.0, 6.0]))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_demand_task.py tests/test_demand_frames.py -q`
Expected: `ImportError: cannot import name 'TASK'` / `ModuleNotFoundError: ... tasks.demand.frames`.

- [ ] **Step 3: Write `demand/frames.py`**

```python
# power_market_analytics/tasks/demand/frames.py
"""Domain frames for the area demand (load) forecasting task."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)


class AreaDemand(HalfHourlySeries):
    """Half-hourly area demand history for one area, in kWh per 30-minute period.

    Rows whose actual is unpublished (the TSO holes, e.g. Tokyo 2025-06-14
    time codes 11-48) are absent — the loader drops them — so the value is
    non-null and the grain may be sparse on those days.

    Grain: (trade_date, time_code).
    """

    value_col = "demand_kwh"


class DemandForecast(DayAheadForecast):
    """Forecast for one delivery day: exactly 48 half-hour demand values (kWh).

    Grain: (trade_date, time_code); trade_date is the target delivery day.
    """

    forecast_col = "forecast_demand_kwh"


class DemandBacktestResult(BacktestResult):
    """Demand forecasts joined to actuals over a backtest window.

    Grain: (trade_date, time_code).
    """

    actual_col = "actual_demand_kwh"
    forecast_col = "forecast_demand_kwh"


class DemandForecastRecords(ForecastRecords):
    """One backtest run's demand forecasts shaped for ``pma_ml.demand_forecast``.

    Grain: (run_id, area_code, trade_date, time_code).
    """

    forecast_col = "forecast_demand_kwh"


class AreaTemperature(DomainFrame):
    """Hourly temperature at an area's representative JMA station.

    ``hour_ending`` is JMA's observation hour 1..24 (24 = the reading at
    24:00, which the weather fact stores as next-day 00:00 but keys to the
    observation day). ``temperature_c`` is null where JMA published no usable
    value (quality flag 2/1/0), so it is not a non-null column.

    Grain: (obs_date, hour_ending).
    """

    schema = {
        "obs_date": "datetime64[ns]",
        "hour_ending": "int64",
        "temperature_c": "float64",
    }
    keys = ["obs_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        bad = df.loc[~df["hour_ending"].between(1, 24), "hour_ending"]
        if not bad.empty:
            raise ValueError(f"{cls.__name__}: hour_ending outside 1..24: {sorted(bad.unique())}")
```

- [ ] **Step 4: Rewrite `demand/__init__.py`**

```python
"""Area demand (load) forecasting.

Task definition: at 09:30 JST on day D-1 — before the 10:00 gate closure of
the JEPX day-ahead auction — forecast all 48 half-hourly area demand values
(``demand_kwh``, 30分kWh as the TSOs publish them) for delivery day D in one
area. At that moment the newest finalized TSO 実績 file is D-2's: a day's file
is finalized shortly after midnight of the following day, so D-1 is still in
progress. A strategy's usable demand history is therefore delivery days
<= D-2 (``history_lead_days = 2``). JMA hourly observations exist through
09:00 on D-1, but features use complete observation days only (<= D-2), so
every one of the 48 periods is built from the same window. Each area's
temperature comes from one representative JMA station
(``dim_area.representative_jma_station_id``).
"""

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
)

TASK = TaskSpec(
    name="demand",
    unit="kWh",
    history_lead_days=2,
    # Forecasts for delivery day D are issued at 09:30 JST on D-1.
    issue_offset=pd.Timedelta(days=-1, hours=9, minutes=30),
    forecast_table="pma_ml.demand_forecast",
    history_cls=AreaDemand,
    forecast_cls=DemandForecast,
    result_cls=DemandBacktestResult,
    records_cls=DemandForecastRecords,
)

MLFLOW_EXPERIMENT = TASK.name
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_demand_task.py tests/test_demand_frames.py -q && uv run ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A power_market_analytics/tasks/demand tests/test_demand_task.py tests/test_demand.py tests/test_demand_frames.py
git commit -m "$(cat <<'EOF'
Define the demand task (TaskSpec, frames, task definition)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Synthetic demand/weather in the test warehouse + demand datasets

**Files:**
- Modify: `tests/conftest.py`
- Create: `power_market_analytics/tasks/demand/datasets.py`
- Test: `tests/test_demand_datasets.py`

**Interfaces:**
- Consumes: `common.warehouse.query_pandas`, `demand.frames`.
- Produces: `AREA_CODES = ("tokyo", "kansai")`; `load_area_demand(area_code="tokyo", spark=None) -> AreaDemand` (drops null-demand rows, logs the count, kWh as float64); `load_area_temperature(area_code="tokyo", spark=None) -> AreaTemperature`.
- Fixture additions (`tests/conftest.py`): constants `DEMAND_DAYS`, `DEMAND_HOLE_DAY`, `DEMAND_HOLE_TIME_CODES`, `TOKYO_STATION_ID`, `KANSAI_STATION_ID`, `TEMPERATURE_MISSING_HOURS`; functions `synthetic_demand(day, time_code) -> int`, `synthetic_temperature(day, hour_ending) -> float`; `CuratedWarehouse` gains `demand: pd.DataFrame` (columns `date_key, time_code, area_key, demand_kwh` with NaN in the hole) and `weather: pd.DataFrame` (`station_id, date_key, hour_ending, temperature_c` with NaN for the missing hours); `dim_area` gains `representative_jma_station_id`; new tables `pma_curated.fct_area_demand_generation_actual` (tokyo only) and `pma_curated.fct_jma_weather_hourly` (s47662 only).

- [ ] **Step 1: Extend the fixture**

In `tests/conftest.py`, update the module docstring's second paragraph to "``curated_warehouse`` populates a small synthetic ``pma_curated`` star (the tables the spot-price and demand tasks read) …". After `UNMATCHED_DAYS = ...` add:

```python
#: Delivery days with demand actuals (tokyo only) — the same span as the prices.
DEMAND_DAYS = PRICE_DAYS
#: One partial-day hole like Tokyo 2025-06-14: time codes 11..48 have null demand.
DEMAND_HOLE_DAY = pd.Timestamp("2024-04-20")
DEMAND_HOLE_TIME_CODES = range(11, 49)
#: Representative JMA stations written to dim_area; only tokyo's has weather rows.
TOKYO_STATION_ID = "s47662"
KANSAI_STATION_ID = "s47772"
#: (observation day, hour_ending) pairs whose temperature is null.
TEMPERATURE_MISSING_HOURS = {(pd.Timestamp("2024-04-25"), 13), (pd.Timestamp("2024-04-25"), 14)}
```

Change `AREAS` to

```python
AREAS = pd.DataFrame(
    {
        "area_key": [1, 2],
        "area_code": ["tokyo", "kansai"],
        "representative_jma_station_id": [TOKYO_STATION_ID, KANSAI_STATION_ID],
    }
)
```

After `day_part` add:

```python
def synthetic_demand(day: pd.Timestamp, time_code: int) -> int:
    """Deterministic 30-minute demand in kWh: daily shape, weekend dip, slow drift.

    Multiples of 1,000 like TEPCO's published values.
    """
    day_index = (day - PRICE_DAYS[0]).days
    shape = 15_000_000 - 4_000_000 * math.cos(2 * math.pi * (time_code - 1) / 48)
    weekend = -1_000_000 if day.dayofweek >= 5 else 0
    return int(round((shape + weekend + 5_000 * day_index) / 1000) * 1000)


def synthetic_temperature(day: pd.Timestamp, hour_ending: int) -> float:
    """Deterministic hourly temperature in °C: diurnal cycle plus slow warming."""
    day_index = (day - PRICE_DAYS[0]).days
    return round(8.0 + 0.15 * day_index + 5.0 * math.sin(2 * math.pi * (hour_ending - 9) / 24), 1)
```

Extend `CuratedWarehouse` with two attributes (document them in its docstring: "demand — fct_area_demand_generation_actual (tokyo, demand_kwh NaN in the hole); weather — fct_jma_weather_hourly (s47662, hourly, temperature_c NaN for the missing hours)"):

```python
    demand: pd.DataFrame
    weather: pd.DataFrame
```

Inside `curated_warehouse`, before `spark.sql("CREATE DATABASE IF NOT EXISTS pma_curated")`, build the rows:

```python
    demand_rows: list[tuple] = []
    demand_records: list[dict] = []
    for day in DEMAND_DAYS:
        for tc in range(1, 49):
            in_hole = day == DEMAND_HOLE_DAY and tc in DEMAND_HOLE_TIME_CODES
            demand_kwh = None if in_hole else synthetic_demand(day, tc)
            demand_rows.append(
                (
                    day.date(),
                    tc,
                    TOKYO_AREA_KEY,
                    (day + pd.Timedelta(minutes=30 * (tc - 1))).to_pydatetime(),
                    demand_kwh,
                    synthetic_demand(day, tc) + 500_000,
                    1_000_000,
                )
            )
            demand_records.append(
                {
                    "date_key": day.date(),
                    "time_code": tc,
                    "area_key": TOKYO_AREA_KEY,
                    "demand_kwh": demand_kwh,
                }
            )
    demand = pd.DataFrame(demand_records).astype({"demand_kwh": "float64"})
    weather_rows: list[tuple] = []
    weather_records: list[dict] = []
    for day in DEMAND_DAYS:
        for hour in range(1, 25):
            temperature = (
                None
                if (day, hour) in TEMPERATURE_MISSING_HOURS
                else synthetic_temperature(day, hour)
            )
            weather_rows.append(
                (
                    TOKYO_STATION_ID,
                    (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                    (day + pd.Timedelta(hours=hour - 1)).to_pydatetime(),
                    day.date(),
                    temperature,
                )
            )
            weather_records.append(
                {
                    "station_id": TOKYO_STATION_ID,
                    "date_key": day.date(),
                    "hour_ending": hour,
                    "temperature_c": temperature,
                }
            )
    weather = pd.DataFrame(weather_records).astype({"temperature_c": "float64"})
```

Change the `dim_area` write to the three-column schema
`"area_key int, area_code string, representative_jma_station_id string"`, and after the accuracy table add:

```python
    spark.createDataFrame(
        demand_rows,
        "date_key date, time_code int, area_key int, delivery_datetime timestamp, "
        "demand_kwh bigint, generation_kwh bigint, wind_solar_generation_kwh bigint",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_area_demand_generation_actual")
    spark.createDataFrame(
        weather_rows,
        "station_id string, observed_at timestamp, observed_hour_start_at timestamp, "
        "date_key date, temperature_c double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_jma_weather_hourly")
```

and return `demand=demand, weather=weather` in the `CuratedWarehouse(...)` call. (Rows are Python tuples with `None`, not pandas NaN, so Spark stores real nulls in the bigint/double columns.)

Run `uv run pytest tests/test_spot_price_datasets.py tests/test_spot_price_scripts.py -q` to confirm the extended fixture breaks nothing.

- [ ] **Step 2: Write the failing dataset tests**

```python
# tests/test_demand_datasets.py
"""Tests for the warehouse readers feeding the demand task.

Read against the synthetic ``pma_curated`` star from ``curated_warehouse``:
tokyo has demand actuals for ``DEMAND_DAYS`` (with a partial-day hole) and
hourly temperature at its representative station; kansai has an area row
and a station id but no facts.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    AREA_CODES,
    load_area_demand,
    load_area_temperature,
)
from power_market_analytics.tasks.demand.frames import AreaDemand, AreaTemperature
from tests.conftest import (
    DEMAND_DAYS,
    DEMAND_HOLE_DAY,
    DEMAND_HOLE_TIME_CODES,
    TEMPERATURE_MISSING_HOURS,
    CuratedWarehouse,
)


def test_area_codes_are_the_areas_with_a_tso_feed():
    assert AREA_CODES == ("tokyo", "kansai")


def expected_demand(warehouse: CuratedWarehouse) -> pd.DataFrame:
    return (
        warehouse.demand.dropna(subset=["demand_kwh"])
        .assign(trade_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]"))[
            ["trade_date", "time_code", "demand_kwh"]
        ]
        .astype({"time_code": "int64", "demand_kwh": "float64"})
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )


class TestLoadAreaDemand:
    def test_tokyo_history_without_the_null_hole(self, spark, curated_warehouse):
        demand = load_area_demand("tokyo", spark=spark)
        assert isinstance(demand, AreaDemand)
        pd.testing.assert_frame_equal(demand.df, expected_demand(curated_warehouse))
        assert len(demand) == len(DEMAND_DAYS) * 48 - len(DEMAND_HOLE_TIME_CODES)
        hole = demand.df[demand.df["trade_date"] == DEMAND_HOLE_DAY]
        assert hole["time_code"].tolist() == list(range(1, 11))

    def test_area_without_actuals_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No demand actuals found for area_code='kansai'"):
            load_area_demand("kansai", spark=spark)

    def test_defaults_to_tokyo_and_the_active_session(self, spark, curated_warehouse):
        assert len(load_area_demand()) == len(DEMAND_DAYS) * 48 - len(DEMAND_HOLE_TIME_CODES)


def expected_temperature(warehouse: CuratedWarehouse) -> pd.DataFrame:
    return (
        warehouse.weather.assign(
            obs_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]")
        )[["obs_date", "hour_ending", "temperature_c"]]
        .astype({"hour_ending": "int64", "temperature_c": "float64"})
        .sort_values(["obs_date", "hour_ending"], ignore_index=True)
    )


class TestLoadAreaTemperature:
    def test_tokyo_temperature_by_observation_day_and_hour_ending(self, spark, curated_warehouse):
        temperature = load_area_temperature("tokyo", spark=spark)
        assert isinstance(temperature, AreaTemperature)
        pd.testing.assert_frame_equal(temperature.df, expected_temperature(curated_warehouse))
        # The 24:00 reading is hour_ending 24 of the observation day, not hour 0 of the next.
        assert set(temperature.df["hour_ending"]) == set(range(1, 25))
        assert len(temperature) == len(DEMAND_DAYS) * 24
        # Missing hours are kept as NaN, not dropped.
        assert temperature.df["temperature_c"].isna().sum() == len(TEMPERATURE_MISSING_HOURS)

    def test_area_whose_station_has_no_observations_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No temperature observations found for area_code='kansai'"
        ):
            load_area_temperature("kansai", spark=spark)
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_demand_datasets.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.tasks.demand.datasets'`.

- [ ] **Step 4: Write `demand/datasets.py`**

```python
# power_market_analytics/tasks/demand/datasets.py
"""Load demand history and temperature for the demand forecasting task."""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.frames import AreaDemand, AreaTemperature

# The areas whose TSO actuals feed fct_area_demand_generation_actual (one
# union branch per TSO). Extend together with that model.
AREA_CODES = (
    "tokyo",
    "kansai",
)


def load_area_demand(area_code: str = "tokyo", spark: SparkSession | None = None) -> AreaDemand:
    """Load the full half-hourly demand history for one area.

    Rows whose ``demand_kwh`` is null (a TSO hole such as Tokyo 2025-06-14
    time codes 11-48) are dropped, so the returned grain can be sparse on
    those days; the count is logged.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    AreaDemand

    Raises
    ------
    ValueError
        If the area returns no rows or the result violates the AreaDemand
        contract.
    """
    pdf = query_pandas(
        f"""
        select
          f.date_key as trade_date,
          f.time_code,
          f.demand_kwh
        from pma_curated.fct_area_demand_generation_actual f
        join pma_curated.dim_area a on f.area_key = a.area_key
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(f"No demand actuals found for area_code={area_code!r}")
    # Logged unconditionally (a zero is informative too, and it keeps the
    # function branch-free for the coverage gate).
    logger.info(
        "load_area_demand: {} of {} rows have null demand_kwh and are dropped ({})",
        int(pdf["demand_kwh"].isna().sum()),
        len(pdf),
        area_code,
    )
    pdf = (
        pdf.dropna(subset=["demand_kwh"])
        .assign(trade_date=lambda d: pd.to_datetime(d["trade_date"]))
        .astype({"time_code": "int64", "demand_kwh": "float64"})
        .sort_values(GRAIN_COLS, ignore_index=True)
    )
    return AreaDemand.from_df(pdf)


def load_area_temperature(
    area_code: str = "tokyo", spark: SparkSession | None = None
) -> AreaTemperature:
    """Load hourly temperature at the area's representative JMA station.

    Reads ``fct_jma_weather_hourly`` for the station named by
    ``dim_area.representative_jma_station_id``; ``hour_ending`` is derived
    from ``observed_hour_start_at`` so the 24:00 reading stays on its
    observation day as hour 24.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    AreaTemperature

    Raises
    ------
    ValueError
        If the area has no representative station, the station has no
        observations, or the result violates the AreaTemperature contract.
    """
    pdf = query_pandas(
        f"""
        select
          w.date_key as obs_date,
          hour(w.observed_hour_start_at) + 1 as hour_ending,
          w.temperature_c
        from pma_curated.fct_jma_weather_hourly w
        join pma_curated.dim_area a on w.station_id = a.representative_jma_station_id
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No temperature observations found for area_code={area_code!r} "
            "(no representative station, or no weather rows for it)"
        )
    pdf = (
        pdf.assign(obs_date=lambda d: pd.to_datetime(d["obs_date"]))
        .astype({"hour_ending": "int64", "temperature_c": "float64"})
        .sort_values(["obs_date", "hour_ending"], ignore_index=True)
    )
    return AreaTemperature.from_df(pdf)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_demand_datasets.py -q && uv run pytest -q -x && uv run ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py power_market_analytics/tasks/demand/datasets.py tests/test_demand_datasets.py
git commit -m "$(cat <<'EOF'
Add demand datasets (area demand + representative-station temperature)

The test warehouse gains synthetic fct_area_demand_generation_actual and
fct_jma_weather_hourly tables and dim_area.representative_jma_station_id.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Recency-weighted temperature feature (`demand/features.py`)

**Files:**
- Create: `power_market_analytics/tasks/demand/features.py`
- Test: `tests/test_demand_features.py`

**Interfaces:**
- Produces: `TEMPERATURE_LAG_DAYS = (2, 3, 4, 5, 6, 7, 8)`, `TEMPERATURE_HALF_LIFE_DAYS = 1.0`, `TEMPERATURE_FEATURE = "wavg_temperature_c"`; `hour_ending_of(time_code: pd.Series) -> pd.Series` (`(time_code + 1) // 2`, int64); `recency_weighted_temperature(points: pd.DataFrame, temperature: AreaTemperature, *, lag_days=TEMPERATURE_LAG_DAYS, half_life_days=TEMPERATURE_HALF_LIFE_DAYS, name=TEMPERATURE_FEATURE) -> pd.DataFrame` (returns `points` plus column `name`; row order preserved; NaN only when every lag is missing; weight of lag k = `0.5 ** ((k - min(lag_days)) / half_life_days)` renormalised over the non-missing lags).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_demand_features.py
"""Tests for the demand task's recency-weighted temperature feature."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.features import (
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    hour_ending_of,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import AreaTemperature

D = pd.Timestamp("2024-04-10").as_unit("ns")


def make_temperature(values: dict[tuple[int, int], float]) -> AreaTemperature:
    """AreaTemperature from {(lag_days_before_D, hour_ending): temperature_c}."""
    return AreaTemperature.from_df(
        pd.DataFrame(
            {
                "obs_date": [D - pd.Timedelta(days=k) for (k, _) in values],
                "hour_ending": np.array([h for (_, h) in values], dtype="int64"),
                "temperature_c": np.array(list(values.values()), dtype="float64"),
            }
        )
    )


def points(time_codes: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {"trade_date": [D] * len(time_codes), "time_code": np.array(time_codes, dtype="int64")}
    )


class TestConstants:
    def test_defaults(self):
        assert TEMPERATURE_LAG_DAYS == (2, 3, 4, 5, 6, 7, 8)
        assert TEMPERATURE_HALF_LIFE_DAYS == 1.0
        assert TEMPERATURE_FEATURE == "wavg_temperature_c"


class TestHourEndingOf:
    def test_period_maps_to_the_observation_hour_containing_its_start(self):
        tc = pd.Series([1, 2, 3, 4, 23, 24, 47, 48], dtype="int64")
        assert hour_ending_of(tc).tolist() == [1, 1, 2, 2, 12, 12, 24, 24]
        assert hour_ending_of(tc).dtype == "int64"


class TestRecencyWeightedTemperature:
    def test_all_seven_lags_present_and_equal_returns_that_value(self):
        temperature = make_temperature({(k, 1): 12.0 for k in range(2, 9)})
        out = recency_weighted_temperature(points([1]), temperature)
        assert list(out.columns) == ["trade_date", "time_code", TEMPERATURE_FEATURE]
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(12.0)

    def test_weights_halve_per_day_back(self):
        # D-2 = 10 (weight 1), D-3 = 20 (weight 0.5), D-4 = 40 (weight 0.25).
        temperature = make_temperature({(2, 1): 10.0, (3, 1): 20.0, (4, 1): 40.0})
        out = recency_weighted_temperature(points([1]), temperature)
        expected = (10 * 1 + 20 * 0.5 + 40 * 0.25) / (1 + 0.5 + 0.25)
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(expected)

    def test_missing_lags_are_dropped_and_weights_renormalised(self):
        # D-2 missing entirely, D-3 = 20, D-8 = 8: (20 * 0.5 + 8 * 2**-6) / (0.5 + 2**-6).
        temperature = make_temperature({(3, 1): 20.0, (8, 1): 8.0})
        out = recency_weighted_temperature(points([1]), temperature)
        expected = (20 * 0.5 + 8 * 2**-6) / (0.5 + 2**-6)
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(expected)

    def test_null_temperature_counts_as_missing(self):
        temperature = make_temperature({(2, 1): np.nan, (3, 1): 20.0})
        out = recency_weighted_temperature(points([1]), temperature)
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(20.0)

    def test_all_lags_missing_gives_nan(self):
        temperature = make_temperature({(9, 1): 5.0, (1, 1): 5.0})  # outside D-8..D-2
        out = recency_weighted_temperature(points([1]), temperature)
        assert np.isnan(out[TEMPERATURE_FEATURE].iloc[0])

    def test_each_period_uses_its_own_hour_and_row_order_is_kept(self):
        temperature = make_temperature({(2, 1): 10.0, (2, 2): 30.0, (2, 24): 50.0})
        out = recency_weighted_temperature(points([48, 3, 1, 2]), temperature)
        assert out["time_code"].tolist() == [48, 3, 1, 2]
        assert out[TEMPERATURE_FEATURE].tolist() == pytest.approx([50.0, 30.0, 10.0, 10.0])

    def test_lag_days_and_half_life_are_configurable(self):
        temperature = make_temperature({(1, 1): 10.0, (2, 1): 20.0})
        out = recency_weighted_temperature(
            points([1]), temperature, lag_days=(1, 2), half_life_days=2.0, name="t"
        )
        w2 = 0.5 ** (1 / 2.0)
        assert out["t"].iloc[0] == pytest.approx((10 + 20 * w2) / (1 + w2))

    def test_empty_lag_days_rejected(self):
        with pytest.raises(ValueError, match="lag_days must not be empty"):
            recency_weighted_temperature(points([1]), make_temperature({(2, 1): 1.0}), lag_days=())

    def test_extra_point_columns_pass_through(self):
        temperature = make_temperature({(2, 1): 10.0})
        out = recency_weighted_temperature(points([1]).assign(month=4), temperature)
        assert list(out.columns) == ["trade_date", "time_code", "month", TEMPERATURE_FEATURE]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_demand_features.py -q`
Expected: `ModuleNotFoundError: No module named 'power_market_analytics.tasks.demand.features'`.

- [ ] **Step 3: Write `demand/features.py`**

```python
# power_market_analytics/tasks/demand/features.py
"""Temperature features for the demand forecasting task."""

from __future__ import annotations

import numpy as np
import pandas as pd

from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.frames import AreaTemperature

#: Days before delivery day D whose same-hour temperature enters the feature:
#: the seven most recent *complete* observation days at 09:30 D-1 (D-1 is
#: still in progress, so it is excluded).
TEMPERATURE_LAG_DAYS: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8)
#: Weight halves for every ``half_life_days`` further back: D-2 -> 1, D-3 -> 1/2, ... D-8 -> 1/64.
TEMPERATURE_HALF_LIFE_DAYS = 1.0
TEMPERATURE_FEATURE = "wavg_temperature_c"


def hour_ending_of(time_code: pd.Series) -> pd.Series:
    """JMA observation hour (1..24, hour-ending) containing the start of a period.

    Period ``time_code`` starts at ``(time_code - 1) * 30`` minutes; the
    observation hour that contains that instant ends at hour
    ``(time_code + 1) // 2`` — the alignment ``fct_jma_weather_hourly``
    documents (broadcast each hour to its two delivery periods).

    Parameters
    ----------
    time_code : pandas.Series
        JEPX time codes 1..48.

    Returns
    -------
    pandas.Series
        int64 hour-ending values 1..24.
    """
    return ((time_code + 1) // 2).astype("int64")


def recency_weighted_temperature(
    points: pd.DataFrame,
    temperature: AreaTemperature,
    *,
    lag_days: tuple[int, ...] = TEMPERATURE_LAG_DAYS,
    half_life_days: float = TEMPERATURE_HALF_LIFE_DAYS,
    name: str = TEMPERATURE_FEATURE,
) -> pd.DataFrame:
    """Attach the recency-weighted mean of the same-hour temperature over past days.

    For a point (D, time_code) the feature is the weighted mean of the
    station's temperature at ``hour_ending_of(time_code)`` on days
    ``D - k`` for ``k`` in ``lag_days``, with weight
    ``0.5 ** ((k - min(lag_days)) / half_life_days)``. Weights are
    renormalised over the lags that have a value, so a missing hour lowers
    the effective sample rather than the result; the feature is NaN only when
    every lag is missing.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    temperature : AreaTemperature
        Hourly temperature at the area's representative station.
    lag_days : tuple of int, optional
        Days before D to average over; must not be empty.
    half_life_days : float, optional
        Days over which a lag's weight halves.
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name``, in the original row order.

    Raises
    ------
    ValueError
        If ``lag_days`` is empty.
    """
    if not lag_days:
        raise ValueError("lag_days must not be empty")
    keyed = points[GRAIN_COLS].assign(hour_ending=hour_ending_of(points["time_code"]))
    temp = temperature.df
    first = min(lag_days)
    columns = []
    weights = []
    for k in lag_days:
        lagged = temp.assign(trade_date=temp["obs_date"] + pd.Timedelta(days=k))[
            ["trade_date", "hour_ending", "temperature_c"]
        ]
        # Two periods share an hour, hence many_to_one; a left merge keeps
        # the left row order.
        joined = keyed.merge(
            lagged, how="left", on=["trade_date", "hour_ending"], validate="many_to_one"
        )
        columns.append(joined["temperature_c"].to_numpy(dtype="float64"))
        weights.append(0.5 ** ((k - first) / half_life_days))
    values = np.column_stack(columns)
    w = np.asarray(weights, dtype="float64")
    present = ~np.isnan(values)
    weighted_sum = np.nansum(values * w, axis=1)
    weight_sum = (present * w).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        feature = np.where(weight_sum > 0, weighted_sum / weight_sum, np.nan)
    return points.assign(**{name: feature})
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_demand_features.py -q && uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/features.py tests/test_demand_features.py
git commit -m "$(cat <<'EOF'
Add the recency-weighted same-hour temperature feature for demand

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Demand LightGBM strategy + registry

**Files:**
- Create: `power_market_analytics/tasks/demand/strategies/__init__.py`
- Create: `power_market_analytics/tasks/demand/strategies/lgbm.py`
- Test: `tests/test_demand_lgbm.py`, `tests/test_demand_strategies.py`

**Interfaces:**
- Produces (`strategies/lgbm.py`): `DEMAND_LAG_FEATURE = "lag_7d_demand_kwh"`, `FEATURE_COLS = (*CALENDAR_FEATURE_COLS, TEMPERATURE_FEATURE, DEMAND_LAG_FEATURE)`, `TARGET_COL = TASK.actual_col`, `FORECAST_COL = TASK.forecast_col`, `DemandLightGbmEvalSet(LightGbmEvalSetBase)`, `LightGbmStrategy(SlidingWindowLightGbmStrategy)` (`name="lightgbm"`, `task=TASK`, `lookback_days=8`, `__init__(temperature: AreaTemperature, **kwargs)`, attribute `temperature`).
- Produces (`strategies/__init__.py`): `STRATEGIES: dict[str, type[ForecastStrategy]] = {"lightgbm": LightGbmStrategy}`, `build_strategy(name, *, area_code, train_start_date=None, spark=None) -> ForecastStrategy`.

- [ ] **Step 1: Write the failing strategy tests**

```python
# tests/test_demand_lgbm.py
"""Tests for the demand LightGBM baseline (calendar + temperature + D-7 lag).

Everything runs for real — feature building, LightGBM fits, TreeSHAP records,
MLflow logging into the session's temp file store — on a small synthetic
demand/temperature history. Assertions are structural (row counts, feature
values that can be hand-derived, refit bookkeeping, SHAP additivity), never
on predicted numbers.
"""

from __future__ import annotations

import math

import mlflow
import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.backtest import BacktestRun, run_backtest
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.demand.features import (
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    hour_ending_of,
)
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaTemperature,
    DemandBacktestResult,
    DemandForecast,
)
from power_market_analytics.tasks.demand.strategies.lgbm import (
    DEMAND_LAG_FEATURE,
    FEATURE_COLS,
    DemandLightGbmEvalSet,
    LightGbmStrategy,
)


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    mlflow.set_experiment("test_demand_lgbm")


HISTORY_START = pd.Timestamp("2024-03-01")
#: 60 days: 2024-03-01 .. 2024-04-29.
HISTORY_DAYS = pd.date_range(HISTORY_START, periods=60, freq="D")


def demand_at(day: pd.Timestamp, time_code: int) -> float:
    day_index = (day - HISTORY_START).days
    shape = 15_000_000 - 4_000_000 * math.cos(2 * math.pi * (time_code - 1) / 48)
    weekend = -1_000_000 if day.dayofweek >= 5 else 0.0
    return float(round((shape + weekend + 5_000 * day_index) / 1000) * 1000)


def temperature_at(day: pd.Timestamp, hour_ending: int) -> float:
    day_index = (day - HISTORY_START).days
    return round(8.0 + 0.15 * day_index + 5.0 * math.sin(2 * math.pi * (hour_ending - 9) / 24), 1)


def make_demand(days=HISTORY_DAYS) -> AreaDemand:
    return AreaDemand.from_df(
        pd.DataFrame(
            [
                {"trade_date": day, "time_code": tc, "demand_kwh": demand_at(day, tc)}
                for day in days
                for tc in range(1, 49)
            ]
        )
    )


def make_temperature(days=HISTORY_DAYS) -> AreaTemperature:
    return AreaTemperature.from_df(
        pd.DataFrame(
            [
                {"obs_date": day, "hour_ending": h, "temperature_c": temperature_at(day, h)}
                for day in days
                for h in range(1, 25)
            ]
        ).astype({"hour_ending": "int64"})
    )


def visible(demand: AreaDemand, day: pd.Timestamp) -> AreaDemand:
    """History the strategy may see for target ``day`` (delivery days <= D-2)."""
    return AreaDemand.from_df(demand.df[demand.df["trade_date"] <= day - pd.Timedelta(days=2)])


def expected_wavg(day: pd.Timestamp, time_code: int) -> float:
    hour = int(hour_ending_of(pd.Series([time_code], dtype="int64")).iloc[0])
    weights = [0.5 ** ((k - TEMPERATURE_LAG_DAYS[0]) / TEMPERATURE_HALF_LIFE_DAYS) for k in TEMPERATURE_LAG_DAYS]
    temps = [temperature_at(day - pd.Timedelta(days=k), hour) for k in TEMPERATURE_LAG_DAYS]
    return sum(w * t for w, t in zip(weights, temps)) / sum(weights)


D = pd.Timestamp("2024-04-10")  # a Wednesday


@pytest.fixture(scope="module")
def demand() -> AreaDemand:
    return make_demand()


@pytest.fixture(scope="module")
def temperature() -> AreaTemperature:
    return make_temperature()


class TestClassAttributes:
    def test_features_and_frames(self):
        assert LightGbmStrategy.name == "lightgbm"
        assert LightGbmStrategy.task.name == "demand"
        assert FEATURE_COLS == (
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
        )
        assert LightGbmStrategy.feature_cols == FEATURE_COLS
        assert LightGbmStrategy.eval_set_cls is DemandLightGbmEvalSet
        assert LightGbmStrategy.lookback_days == 8
        assert DemandLightGbmEvalSet.target_col == "actual_demand_kwh"
        assert DemandLightGbmEvalSet.forecast_col == "forecast_demand_kwh"
        assert list(DemandLightGbmEvalSet.schema) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            "actual_demand_kwh",
            "forecast_demand_kwh",
        ]


class TestPredict:
    def test_returns_48_finite_demand_values(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        forecast = strategy.predict(D, visible(demand, D))
        assert isinstance(forecast, DemandForecast)
        assert forecast.df["trade_date"].eq(D).all()
        assert forecast.df["time_code"].tolist() == list(range(1, 49))
        assert np.isfinite(forecast.df["forecast_demand_kwh"]).all()

    def test_features_are_the_d7_lag_and_the_recency_weighted_temperature(
        self, demand, temperature
    ):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        forecast = strategy.predict(D, visible(demand, D))
        record = strategy._shap_records[pd.Timestamp(D).as_unit("ns")]
        assert list(record.columns) == [
            "trade_date",
            "time_code",
            "month",
            "day_of_week",
            TEMPERATURE_FEATURE,
            DEMAND_LAG_FEATURE,
            *[f"shap_{c}" for c in FEATURE_COLS],
            "shap_expected_value",
        ]
        assert record["month"].eq(4).all()
        assert record["day_of_week"].eq(2).all()
        assert record[DEMAND_LAG_FEATURE].tolist() == [
            demand_at(D - pd.Timedelta(days=7), tc) for tc in range(1, 49)
        ]
        np.testing.assert_allclose(
            record[TEMPERATURE_FEATURE].to_numpy(),
            [expected_wavg(D, tc) for tc in range(1, 49)],
        )
        # The forecast is the fitted model's own prediction on those rows.
        np.testing.assert_allclose(
            forecast.df["forecast_demand_kwh"].to_numpy(),
            strategy._model.predict(record[list(FEATURE_COLS)].astype("float64")),
        )
        reconstructed = record[list(strategy.shap_cols)].sum(axis=1) + record["shap_expected_value"]
        np.testing.assert_allclose(
            reconstructed.to_numpy(), forecast.df["forecast_demand_kwh"].to_numpy(), atol=1e-3
        )

    def test_missing_d7_demand_is_unforecastable(self, demand, temperature):
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        without_d7 = AreaDemand.from_df(
            demand.df[demand.df["trade_date"] != D - pd.Timedelta(days=7)]
        )
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"lightgbm: features \['{DEMAND_LAG_FEATURE}'\] unavailable for 2024-04-10",
        ):
            strategy.predict(D, visible(without_d7, D))

    def test_missing_temperature_window_is_unforecastable(self, demand):
        # Temperature stops before D-8: every lag is missing for D.
        short = make_temperature(pd.date_range(HISTORY_START, D - pd.Timedelta(days=9)))
        strategy = LightGbmStrategy(short, train_window_days=5)
        with pytest.raises(
            ForecastUnavailableError,
            match=rf"lightgbm: features \['{TEMPERATURE_FEATURE}'\] unavailable for 2024-04-10",
        ):
            strategy.predict(D, visible(demand, D))

    def test_training_window_needs_eight_days_of_lookback(self, demand, temperature):
        # 30-day window before D: 03-11 .. 04-08 all have a D-7 lag (history
        # from 03-01), so every window row is complete.
        strategy = LightGbmStrategy(temperature, train_window_days=30)
        strategy.predict(D, visible(demand, D))
        root = strategy._model.booster_.dump_model()["tree_info"][0]["tree_structure"]
        n_rows = root["internal_count"] if "internal_count" in root else root["leaf_count"]
        assert n_rows == 29 * 48  # 03-11 .. 04-08 inclusive
        assert strategy._trained_through == pd.Timestamp("2024-04-08")


WINDOW_START = pd.Timestamp("2024-04-01")
WINDOW_END = pd.Timestamp("2024-04-14")


class TestBacktestEvalAndEvaluate:
    @pytest.fixture(scope="class")
    def backtested(self, demand, temperature) -> tuple[LightGbmStrategy, BacktestRun]:
        strategy = LightGbmStrategy(temperature, train_window_days=30, refit_every_days=7)
        return strategy, run_backtest(strategy, demand, WINDOW_START, WINDOW_END)

    def test_backtest_covers_the_window(self, backtested):
        _, run = backtested
        assert isinstance(run.result, DemandBacktestResult)
        assert run.skipped_days == ()
        assert len(run.result) == 14 * 48

    def test_eval_set_replays_the_forecasts(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        assert type(eval_set) is DemandLightGbmEvalSet
        assert len(eval_set) == 14 * 48
        merged = eval_set.df.merge(
            run.result.df, how="inner", on=["trade_date", "time_code"], suffixes=("", "_bt"),
            validate="one_to_one",
        )
        assert merged["forecast_demand_kwh"].equals(merged["forecast_demand_kwh_bt"])
        assert merged["actual_demand_kwh"].equals(merged["actual_demand_kwh_bt"])

    def test_evaluate_logs_metrics_temperature_params_and_plots(self, backtested, demand):
        strategy, run = backtested
        eval_set = strategy.build_eval_set(demand, WINDOW_START, WINDOW_END, run=run)
        with mlflow.start_run() as active:
            evaluation = strategy.evaluate(eval_set, explainability_nsamples=20)
        finished = mlflow.get_run(active.info.run_id)
        assert evaluation.metrics["mean_absolute_error"] >= 0
        assert "mape_excl_zero_actuals" in evaluation.metrics
        params = finished.data.params
        assert params["lgbm_feature_cols"] == ",".join(FEATURE_COLS)
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"
        assert params["temperature_half_life_days"] == "1.0"
        assert finished.data.metrics["n_refits"] == 2.0
        artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(active.info.run_id)}
        assert {"shap_beeswarm_plot.png", "shap_feature_importance_plot.png"} <= artifacts
```

```python
# tests/test_demand_strategies.py
"""Tests for the demand strategy registry and factory."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.demand.strategies.lgbm import LightGbmStrategy
from tests.conftest import CuratedWarehouse


class TestRegistry:
    def test_registered_names(self):
        assert list(STRATEGIES) == ["lightgbm"]
        assert STRATEGIES["lightgbm"] is LightGbmStrategy


class TestBuildStrategy:
    def test_lightgbm_loads_the_areas_temperature(self, spark, curated_warehouse: CuratedWarehouse):
        strategy = build_strategy(
            "lightgbm", area_code="tokyo", train_start_date=pd.Timestamp("2024-04-01"), spark=spark
        )
        assert type(strategy) is LightGbmStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert len(strategy.temperature) == len(curated_warehouse.weather)

    def test_without_train_start_date(self, spark, curated_warehouse):
        assert build_strategy("lightgbm", area_code="tokyo", spark=spark).train_start_date is None

    def test_area_without_temperature_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No temperature observations found for area_code='kansai'"):
            build_strategy("lightgbm", area_code="kansai", spark=spark)

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError, match="arima"):
            build_strategy("arima", area_code="tokyo")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_demand_lgbm.py tests/test_demand_strategies.py -q`
Expected: `ModuleNotFoundError: ... tasks.demand.strategies`.

- [ ] **Step 3: Write `demand/strategies/lgbm.py`**

```python
# power_market_analytics/tasks/demand/strategies/lgbm.py
"""LightGBM baseline for area demand: calendar, temperature and the D-7 lag."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.features import join_lag
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.features import (
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import AreaTemperature

DEMAND_LAG_FEATURE = "lag_7d_demand_kwh"
FEATURE_COLS = (*CALENDAR_FEATURE_COLS, TEMPERATURE_FEATURE, DEMAND_LAG_FEATURE)
TARGET_COL = TASK.actual_col
FORECAST_COL = TASK.forecast_col


class DemandLightGbmEvalSet(LightGbmEvalSetBase):
    """Design matrix for evaluating :class:`LightGbmStrategy` with MLflow.

    One row per forecast point: the features knowable at 09:30 JST on D-1,
    the realized demand and the walk-forward forecast for that point.

    Grain: (trade_date, time_code).
    """

    feature_cols = FEATURE_COLS
    target_col = TARGET_COL
    forecast_col = FORECAST_COL
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    keys = list(GRAIN_COLS)
    non_null_cols = [*FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmStrategy(SlidingWindowLightGbmStrategy):
    """LightGBM regressor on time code, month, day of week, the recency-weighted
    same-hour temperature over D-8..D-2 and the D-7 demand at the same period.

    Both task-specific features are knowable at 09:30 on D-1: D-7 <= D-2 and
    the temperature window ends on D-2. Model parameters and refit cadence
    are :class:`SlidingWindowLightGbmStrategy`'s (shared with spot_price for
    comparability).

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly temperature at the area's representative JMA station.
    **kwargs
        Forwarded to :class:`SlidingWindowLightGbmStrategy`.
    """

    name = "lightgbm"
    task = TASK
    feature_cols = FEATURE_COLS
    eval_set_cls = DemandLightGbmEvalSet
    # The longest lag any feature reaches back: the temperature window's D-8.
    lookback_days = 8

    def __init__(self, temperature: AreaTemperature, **kwargs) -> None:
        super().__init__(**kwargs)
        self.temperature = temperature

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the D-7 demand lag and the recency-weighted temperature.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
        """
        featured = join_lag(
            featured, history, value_col=self.task.value_col, days=7, name=DEMAND_LAG_FEATURE
        )
        return recency_weighted_temperature(featured, self.temperature)

    def _extra_params(self) -> dict[str, object]:
        """Log the temperature window next to the ``lgbm_*`` params.

        Returns
        -------
        dict of str to object
        """
        return {
            "temperature_lag_days": ",".join(str(k) for k in TEMPERATURE_LAG_DAYS),
            "temperature_half_life_days": TEMPERATURE_HALF_LIFE_DAYS,
        }
```

- [ ] **Step 4: Write `demand/strategies/__init__.py`**

```python
# power_market_analytics/tasks/demand/strategies/__init__.py
"""Forecast strategy registry for the demand task."""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.forecasting.strategy import ForecastStrategy
from power_market_analytics.tasks.demand.datasets import load_area_temperature
from power_market_analytics.tasks.demand.strategies.lgbm import LightGbmStrategy

STRATEGIES: dict[str, type[ForecastStrategy]] = {
    LightGbmStrategy.name: LightGbmStrategy,
}


def build_strategy(
    name: str,
    *,
    area_code: str,
    train_start_date: pd.Timestamp | None = None,
    spark: SparkSession | None = None,
) -> ForecastStrategy:
    """Instantiate a registered strategy with the inputs it needs.

    Every registered strategy is a LightGBM model over the area's
    temperature, so the temperature is loaded from the warehouse here and
    ``train_start_date`` forwarded; callers only deal in registry names.

    Parameters
    ----------
    name : str
        Key in ``STRATEGIES``.
    area_code : str
        dim_area.area_code value being forecast; selects the representative
        station.
    train_start_date : pandas.Timestamp, optional
        First delivery day eligible as a training row.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse for warehouse reads.

    Returns
    -------
    ForecastStrategy

    Raises
    ------
    KeyError
        If ``name`` is not registered.
    ValueError
        If the area has no temperature observations.
    """
    cls = STRATEGIES[name]
    temperature = load_area_temperature(area_code, spark=spark)
    return cls(temperature, train_start_date=train_start_date)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_demand_lgbm.py tests/test_demand_strategies.py -q && uv run ruff check .`
Expected: all pass. If `test_training_window_needs_eight_days_of_lookback` disagrees on the row count, check the arithmetic against the fixture (window `D-30 .. D-2` = 2024-03-11 .. 2024-04-08 = 29 days; every day has a D-7 lag) before touching the strategy.

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/tasks/demand/strategies tests/test_demand_lgbm.py tests/test_demand_strategies.py
git commit -m "$(cat <<'EOF'
Add the demand LightGBM baseline strategy and registry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: `scripts/demand_backtest.py`

**Files:**
- Create: `scripts/demand_backtest.py`
- Test: `tests/test_demand_scripts.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> None`; CLI `--strategy {lightgbm}` (default `lightgbm`), `--area {tokyo,kansai}` (default `tokyo`), `--days` (default 365), `--start-date`, `--end-date`, `--train-start`, `--shap-nsamples` (default 500). Logs an MLflow run under experiment `demand` (run name `<strategy>-<area>`, tags `strategy`, `area`, `warehouse_table`), params `strategy, area, start_date, end_date, n_days, n_predictions, n_days_skipped` (+ the strategy's), artifacts `daily_errors.csv`, `predictions.csv`, `error_heatmaps_year_time_code.html`, the evaluation; publishes to `pma_ml.demand_forecast`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_demand_scripts.py
"""End-to-end CLI tests for the demand backtest script.

Runs for real against the synthetic ``pma_curated`` warehouse
(``curated_warehouse`` fixture) and the session's temp MLflow file store:
fits/scores, logs the MLflow run and publishes to ``pma_ml.demand_forecast``.
"""

from __future__ import annotations

import mlflow
import pandas as pd
import pytest
from pyspark.sql import functions as F

from tests.conftest import DEMAND_HOLE_DAY, DEMAND_HOLE_TIME_CODES
from tests.support import import_script

FORECAST_TABLE = "pma_ml.demand_forecast"


def last_run() -> mlflow.entities.Run:
    return mlflow.get_run(mlflow.last_active_run().info.run_id)


def artifact_names(run_id: str) -> set[str]:
    return {info.path for info in mlflow.MlflowClient().list_artifacts(run_id)}


def published_rows(spark, run_id: str) -> pd.DataFrame:
    return (
        spark.table(FORECAST_TABLE)
        .filter(F.col("run_id") == run_id)
        .toPandas()
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )


class TestBacktestScript:
    def test_lightgbm_over_a_pinned_window(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(
            [
                "--strategy",
                "lightgbm",
                "--area",
                "tokyo",
                "--start-date",
                "2024-04-10",
                "--end-date",
                "2024-04-12",
                "--shap-nsamples",
                "20",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "lightgbm-tokyo"
        assert mlflow.get_experiment(run.info.experiment_id).name == "demand"

        params = run.data.params
        assert params["strategy"] == "lightgbm"
        assert params["area"] == "tokyo"
        assert params["start_date"] == "2024-04-10"
        assert params["end_date"] == "2024-04-12"
        assert params["n_days"] == "3"
        assert params["n_predictions"] == "144"
        assert params["n_days_skipped"] == "0"
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"
        assert run.data.tags["strategy"] == "lightgbm"
        assert run.data.tags["area"] == "tokyo"
        assert run.data.tags["warehouse_table"] == FORECAST_TABLE
        assert run.data.metrics["n_refits"] == 1.0
        assert "mean_absolute_error" in run.data.metrics
        assert "mape_excl_zero_actuals" in run.data.metrics

        artifacts = artifact_names(run.info.run_id)
        assert {
            "daily_errors.csv",
            "predictions.csv",
            "error_heatmaps_year_time_code.html",
            "shap_beeswarm_plot.png",
            "shap_feature_importance_plot.png",
        } <= artifacts
        daily = pd.read_csv(
            mlflow.artifacts.download_artifacts(
                run_id=run.info.run_id, artifact_path="daily_errors.csv"
            )
        )
        assert daily["trade_date"].tolist() == ["2024-04-10", "2024-04-11", "2024-04-12"]
        assert list(daily.columns) == ["trade_date", "mae", "mape"]

        published = published_rows(spark, run.info.run_id)
        assert len(published) == 144
        assert set(published["strategy"]) == {"lightgbm"}
        assert set(published["area_code"]) == {"tokyo"}
        assert list(published.columns) == [
            "strategy",
            "area_code",
            "forecast_issued_ts",
            "trade_date",
            "time_code",
            "forecast_demand_kwh",
            "published_at",
            "run_id",
        ]
        first = published.iloc[0]
        assert first["trade_date"] == pd.Timestamp("2024-04-10").date()
        assert first["time_code"] == 1
        # Issued at 09:30 JST the day before delivery.
        assert first["forecast_issued_ts"] == pd.Timestamp("2024-04-09 09:30")

    def test_hole_day_is_partly_scored_and_its_d7_successor_skipped(self, spark, curated_warehouse):
        # 2024-04-20 has actuals for time codes 1..10 only (48 forecasts, 10 scored);
        # 2024-04-27 cannot be forecast (its D-7 lag is the hole) and is skipped.
        script = import_script("demand_backtest")
        start = DEMAND_HOLE_DAY - pd.Timedelta(days=1)
        end = DEMAND_HOLE_DAY + pd.Timedelta(days=7)
        script.main(
            ["--start-date", str(start.date()), "--end-date", str(end.date()), "--shap-nsamples", "20"]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        params = run.data.params
        assert params["n_days"] == "8"  # 9 calendar days, one skipped
        assert params["n_days_skipped"] == "1"
        assert params["n_predictions"] == str(8 * 48 - len(DEMAND_HOLE_TIME_CODES))
        published = published_rows(spark, run.info.run_id)
        assert published["trade_date"].nunique() == 8
        assert pd.Timestamp("2024-04-27").date() not in set(published["trade_date"])
        assert (published["trade_date"] == DEMAND_HOLE_DAY.date()).sum() == 10

    def test_days_window_ends_at_the_last_day_in_the_data(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(["--days", "2", "--shap-nsamples", "20"])
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.data.params["start_date"] == "2024-05-30"
        assert run.data.params["end_date"] == "2024-05-31"
        assert run.data.params["n_predictions"] == "96"

    def test_train_start_reaches_the_strategy(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(["--days", "2", "--train-start", "2024-04-01", "--shap-nsamples", "20"])
        assert last_run().data.params["lgbm_train_start_date"] == "2024-04-01"

    def test_end_date_after_the_data_is_rejected(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        with pytest.raises(SystemExit) as exc:
            script.main(["--end-date", "2030-01-01"])
        assert exc.value.code == 2
        assert last_run().info.status == "FAILED"

    def test_start_after_end_is_rejected(self, spark, curated_warehouse, capsys):
        script = import_script("demand_backtest")
        with pytest.raises(SystemExit) as exc:
            script.main(["--start-date", "2024-05-05", "--end-date", "2024-05-01"])
        assert exc.value.code == 2
        assert "start date 2024-05-05 is after end date 2024-05-01" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_demand_scripts.py -q`
Expected: `FileNotFoundError` from `import_script` (no `scripts/demand_backtest.py`).

- [ ] **Step 3: Write the script**

```python
# scripts/demand_backtest.py
"""Run an area demand forecasting backtest and log it to MLflow.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/demand_backtest.py --strategy lightgbm --area tokyo

Pin ``--start-date``/``--end-date`` (and ``--train-start``) when two runs
must be compared on identical delivery days and training rows, e.g. a
feature experiment against its matched baseline.
"""

import argparse

import mlflow
import pandas as pd
from loguru import logger

from power_market_analytics.common.tracking import MAPE_METRIC_NAME, log_dataframe, task_run
from power_market_analytics.forecasting.backtest import daily_metrics, run_backtest
from power_market_analytics.forecasting.plots import error_heatmaps
from power_market_analytics.forecasting.publish import (
    build_forecast_records,
    publish_forecast_records,
)
from power_market_analytics.tasks.demand import MLFLOW_EXPERIMENT, TASK
from power_market_analytics.tasks.demand.datasets import AREA_CODES, load_area_demand
from power_market_analytics.tasks.demand.strategies import STRATEGIES, build_strategy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="lightgbm")
    parser.add_argument(
        "--area", choices=AREA_CODES, default="tokyo", help="dim_area.area_code value."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Backtest window length in delivery days, ending at --end-date.",
    )
    parser.add_argument(
        "--start-date",
        type=pd.Timestamp,
        default=None,
        help="First delivery day to forecast (YYYY-MM-DD); overrides --days.",
    )
    parser.add_argument(
        "--end-date",
        type=pd.Timestamp,
        default=None,
        help="Last delivery day to forecast (YYYY-MM-DD); default: the last day in the data.",
    )
    parser.add_argument(
        "--train-start",
        type=pd.Timestamp,
        default=None,
        help="First delivery day eligible as a training row.",
    )
    parser.add_argument(
        "--shap-nsamples",
        type=int,
        default=500,
        help="Rows sampled for the SHAP beeswarm plot.",
    )
    args = parser.parse_args(argv)

    with task_run(
        MLFLOW_EXPERIMENT,
        run_name=f"{args.strategy}-{args.area}",
        tags={"strategy": args.strategy, "area": args.area},
    ) as mlflow_run:
        demand = load_area_demand(area_code=args.area)
        last_day = demand.df["trade_date"].max()
        end_date = last_day if args.end_date is None else args.end_date
        if end_date > last_day:
            parser.error(f"--end-date {end_date.date()} is after the last day in the data")
        start_date = (
            end_date - pd.DateOffset(days=args.days - 1)
            if args.start_date is None
            else args.start_date
        )
        if start_date > end_date:
            parser.error(f"start date {start_date.date()} is after end date {end_date.date()}")

        strategy = build_strategy(
            args.strategy, area_code=args.area, train_start_date=args.train_start
        )
        run = run_backtest(strategy, demand, start_date=start_date, end_date=end_date)
        result = run.result

        per_day = daily_metrics(result)

        mlflow.log_params(
            {
                "strategy": args.strategy,
                "area": args.area,
                "start_date": str(start_date.date()),
                "end_date": str(end_date.date()),
                "n_days": per_day["trade_date"].nunique(),
                "n_predictions": len(result),
                "n_days_skipped": len(run.skipped_days),
            }
        )
        log_dataframe(per_day, "daily_errors.csv")
        log_dataframe(result.df, "predictions.csv")
        records = build_forecast_records(
            TASK, result, run_id=mlflow_run.info.run_id, strategy=args.strategy, area_code=args.area
        )
        publish_forecast_records(TASK, records)
        mlflow.set_tag("warehouse_table", TASK.forecast_table)
        heatmaps = error_heatmaps(
            TASK, result, title=f"Error by year and time code — {args.strategy}, {args.area}"
        )
        mlflow.log_figure(heatmaps, "error_heatmaps_year_time_code.html")

        eval_set = strategy.build_eval_set(demand, start_date=start_date, end_date=end_date, run=run)
        evaluation = strategy.evaluate(eval_set, explainability_nsamples=args.shap_nsamples)

        run_id = mlflow_run.info.run_id

    logger.info(
        "strategy={} area={} window={}..{} days={} predictions={} skipped={}",
        args.strategy,
        args.area,
        start_date.date(),
        end_date.date(),
        per_day["trade_date"].nunique(),
        len(result),
        len(run.skipped_days),
    )
    logger.info(
        "MAE={:.0f} kWh  MAPE={:.2f}%",
        evaluation.metrics["mean_absolute_error"],
        evaluation.metrics[MAPE_METRIC_NAME],
    )
    logger.info("MLflow evaluation metrics:")
    for metric, value in sorted(evaluation.metrics.items()):
        logger.info("  {}={:.4f}", metric, value)
    logger.info("MLflow evaluation artifacts: {}", ", ".join(sorted(evaluation.artifacts)))
    logger.info("MLflow run: {} (experiment: {})", run_id, MLFLOW_EXPERIMENT)
    logger.info("Forecasts written to {} (partition run_id={})", TASK.forecast_table, run_id)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests and the full gate**

Run: `uv run pytest tests/test_demand_scripts.py -q && just test -q && uv run ruff check .`
Expected: all pass, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add scripts/demand_backtest.py tests/test_demand_scripts.py
git commit -m "$(cat <<'EOF'
Add scripts/demand_backtest.py

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: dbt — representative station on `dim_area`, demand forecast models

**Files:**
- Modify: `dbt/seeds/jepx_areas.csv`, `dbt/models/curated/dim_area.sql`, `dbt/models/curated/dim_area.yml`, `dbt/models/raw/ml.yml`
- Create: `dbt/models/staging/stg_ml__demand_forecast.sql` + `.yml`, `dbt/models/standardized/std_ml__demand_forecast.sql` + `.yml`, `dbt/models/curated/fct_demand_forecast.sql` + `.yml`, `dbt/models/curated/fct_demand_forecast_accuracy.sql` + `.yml`

**Interfaces:**
- Produces: `dim_area.representative_jma_station_id` (string, nullable, FK to `dim_jma_station.station_id`); source `ml.demand_forecast`; models `stg_ml__demand_forecast` → `std_ml__demand_forecast` → `fct_demand_forecast` → `fct_demand_forecast_accuracy`.

There is no unit-test harness for dbt; verification is `dbt build` (Step 6 here if the compose stack is up, otherwise Task 17).

- [ ] **Step 1: Seed column**

Rewrite `dbt/seeds/jepx_areas.csv` as:

```csv
area_key,area_code,area_name_en,area_name_ja,tso_name_en,grid_frequency,grid_region,representative_jma_station_id
0,system,System (Nationwide),システム,Not Applicable,Not Applicable,Not Applicable,
1,hokkaido,Hokkaido,北海道,Hokkaido Electric Power Network,50Hz,East,s47412
2,tohoku,Tohoku,東北,Tohoku Electric Power Network,50Hz,East,s47590
3,tokyo,Tokyo,東京,TEPCO Power Grid,50Hz,East,s47662
4,chubu,Chubu,中部,Chubu Electric Power Grid,60Hz,West,s47636
5,hokuriku,Hokuriku,北陸,Hokuriku Electric Power Transmission & Distribution,60Hz,West,s47607
6,kansai,Kansai,関西,Kansai Transmission and Distribution,60Hz,West,s47772
7,chugoku,Chugoku,中国,Chugoku Electric Power Transmission & Distribution,60Hz,West,s47765
8,shikoku,Shikoku,四国,Shikoku Electric Power Transmission & Distribution,60Hz,West,s47891
9,kyushu,Kyushu,九州,Kyushu Electric Power Transmission & Distribution,60Hz,West,s47807
```

(札幌, 仙台, 東京, 名古屋, 富山, 大阪, 広島, 高松, 福岡 — the prefectural-capital 気象官署; all nine ids exist in `dbt/seeds/jma_stations.csv`.)

- [ ] **Step 2: `dim_area`**

`dbt/models/curated/dim_area.sql` — add `representative_jma_station_id` after `grid_region` in the select list.

`dbt/models/curated/dim_area.yml` — append to the description paragraph: "representative_jma_station_id names the JMA 気象官署 whose observations stand in for the area's weather in the modeling tasks (the prefectural capital's staffed station; null for the system row)." and add the column at the end of `columns:`:

```yaml
      - name: representative_jma_station_id
        data_type: string
        description: >
          JMA station id (dim_jma_station.station_id) used as the area's
          representative weather station by the modeling tasks, e.g. s47662
          = 東京 for tokyo, s47772 = 大阪 for kansai. Null for the system row.
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_jma_station')
                field: station_id
```

- [ ] **Step 3: Source table in `dbt/models/raw/ml.yml`**

In the source description replace `power_market_analytics/tasks/spot_price/publish.py` with `power_market_analytics/forecasting/publish.py`. Append under `tables:`:

```yaml
      - name: demand_forecast
        description: >
          Day-ahead area demand forecasts from backtest runs
          (scripts/demand_backtest.py). One row per MLflow run, area and
          30-minute delivery period; partitioned by run_id and overwritten
          per run, so republishing a run replaces exactly its rows. Forecasts
          only — actuals live in fct_area_demand_generation_actual and are
          joined downstream.
        columns:
          - name: run_id
            description: >
              MLflow run id (experiment demand) that produced the forecast;
              the run's tag warehouse_table points back here.
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
            description: >
              When the forecast was made: 09:30 JST on the day before
              delivery, per the task definition (naive JST, like all
              warehouse timestamps).
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
          - name: forecast_demand_kwh
            description: Forecast area demand over the 30-minute period, kWh.
            data_tests:
              - not_null
          - name: published_at
            description: >
              When the run's rows were written to the warehouse (naive JST);
              one value per run, refreshed if a run is republished.
            data_tests:
              - not_null
```

- [ ] **Step 4: Staging and standardized models**

`dbt/models/staging/stg_ml__demand_forecast.sql`:

```sql
-- The source is written by a separate Spark application (the backtest
-- script), and inserts into an existing partitioned table don't invalidate
-- the thriftserver's cached file listing the way the raw loaders' full
-- table overwrites do — refresh before reading.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'demand_forecast') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    forecast_issued_ts,
    trade_date,
    time_code,
    forecast_demand_kwh,
    published_at
  from
    {{ source('ml', 'demand_forecast') }}
  )

select * from source
```

`dbt/models/staging/stg_ml__demand_forecast.yml`:

```yaml
models:
  - name: stg_ml__demand_forecast
    config:
      contract:
        enforced: true
    description: >
      As-is representation of pma_ml.demand_forecast (day-ahead area demand
      forecasts from backtest runs). One row per MLflow run, area and
      30-minute time_code. Column documentation lives on the source
      (models/raw/ml.yml).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - run_id
              - area_code
              - trade_date
              - time_code
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
      - name: forecast_demand_kwh
        data_type: double
        data_tests:
          - not_null
      - name: published_at
        data_type: timestamp
        data_tests:
          - not_null
```

`dbt/models/standardized/std_ml__demand_forecast.sql`:

```sql
with
  staging as (
  select
    *
  from
    {{ ref('stg_ml__demand_forecast') }}
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
    forecast_demand_kwh,
    published_at
  from
    staging
  )

select * from final
```

`dbt/models/standardized/std_ml__demand_forecast.yml` — derive it from the spot one and check the result reads correctly:

```bash
sed -e 's/std_ml__spot_price_forecast/std_ml__demand_forecast/g' \
    -e 's/stg_ml__spot_price_forecast/stg_ml__demand_forecast/g' \
    -e 's/forecast_price_jpy_kwh/forecast_demand_kwh/g' \
    dbt/models/standardized/std_ml__spot_price_forecast.yml \
    > dbt/models/standardized/std_ml__demand_forecast.yml
```

(model name, upstream ref in the description and the value column change; the composite uniqueness test and the `trade_datetime` description stay.)

- [ ] **Step 5: Curated facts**

`dbt/models/curated/fct_demand_forecast.sql`:

```sql
with
  forecast as (
  select
    *
  from
    {{ ref('std_ml__demand_forecast') }}
  ),

  final as (
  select
    forecast.trade_date as date_key,
    forecast.time_code,
    dim_area.area_key,
    forecast.run_id,
    forecast.strategy,
    forecast.trade_datetime,
    forecast.forecast_issued_ts,
    cast(
      (unix_timestamp(forecast.trade_datetime) - unix_timestamp(forecast.forecast_issued_ts)) / 3600
      as double
    ) as horizon_hours,
    forecast.forecast_demand_kwh,
    forecast.published_at
  from
    forecast
    left join {{ ref('dim_area') }} as dim_area
      on forecast.area_code = dim_area.area_code
  )

select * from final
```

`dbt/models/curated/fct_demand_forecast.yml` — start from `sed -e 's/fct_spot_price_forecast/fct_demand_forecast/g' -e 's/forecast_price_jpy_kwh/forecast_demand_kwh/g' dbt/models/curated/fct_spot_price_forecast.yml > dbt/models/curated/fct_demand_forecast.yml`, then replace the `description:` block with

```
      Day-ahead area demand forecasts produced by backtest runs
      (scripts/demand_backtest.py). Grain: one row per MLflow run x delivery
      period (trade date x 30-minute time code) x area. run_id is a
      degenerate dimension linking back to the MLflow run (experiment
      demand) that holds the strategy's params, metrics and artifacts;
      strategy is denormalized for convenient slicing. Forecast values only
      — actuals stay in fct_area_demand_generation_actual; drill across on
      (date_key, time_code, area_key) or use fct_demand_forecast_accuracy,
      which materializes that join. forecast_demand_kwh is energy per
      30-minute period (kWh) like the actual and is additive across periods
      within one run; never sum across runs. horizon_hours is the lead time
      from forecast_issued_ts (09:30 JST on the day before delivery) to the
      start of the delivery period, ~14.5-38 h.
```

and add to the `forecast_demand_kwh` column (already `double`, `not_null` after the sed) a `dbt_utils.accepted_range` test with `arguments: {min_value: 0}`. The composite uniqueness test and the FK `relationships` tests carry over unchanged.

`dbt/models/curated/fct_demand_forecast_accuracy.sql`:

```sql
with
  forecast as (
  select
    *
  from
    {{ ref('fct_demand_forecast') }}
  ),

  actual as (
  select
    date_key,
    time_code,
    area_key,
    demand_kwh
  from
    {{ ref('fct_area_demand_generation_actual') }}
  ),

  final as (
  select
    forecast.date_key,
    forecast.time_code,
    forecast.area_key,
    forecast.run_id,
    forecast.strategy,
    forecast.trade_datetime,
    forecast.forecast_issued_ts,
    forecast.horizon_hours,
    forecast.published_at,
    forecast.forecast_demand_kwh,
    actual.demand_kwh as actual_demand_kwh,
    forecast.forecast_demand_kwh - actual.demand_kwh as error_kwh,
    abs(forecast.forecast_demand_kwh - actual.demand_kwh) as abs_error_kwh,
    case
      when actual.demand_kwh > 0
      then 100 * (forecast.forecast_demand_kwh - actual.demand_kwh) / actual.demand_kwh
    end as pct_error,
    case
      when actual.demand_kwh > 0
      then 100 * abs(forecast.forecast_demand_kwh - actual.demand_kwh) / actual.demand_kwh
    end as abs_pct_error
  from
    forecast
    left join actual
      on forecast.date_key = actual.date_key
      and forecast.time_code = actual.time_code
      and forecast.area_key = actual.area_key
  )

select * from final
```

`dbt/models/curated/fct_demand_forecast_accuracy.yml` — start from `sed -e 's/fct_spot_price_forecast/fct_demand_forecast/g' -e 's/fct_jepx_spot_area_price/fct_area_demand_generation_actual/g' -e 's/forecast_price_jpy_kwh/forecast_demand_kwh/g' -e 's/actual_price_jpy_kwh/actual_demand_kwh/g' -e 's/error_jpy_kwh/error_kwh/g' dbt/models/curated/fct_spot_price_forecast_accuracy.yml > dbt/models/curated/fct_demand_forecast_accuracy.yml` (note `abs_error_jpy_kwh` becomes `abs_error_kwh` through the same substitution), then replace the `description:` block with

```
      Forecast accuracy mart: fct_demand_forecast drilled across to
      fct_area_demand_generation_actual on (date_key, time_code, area_key).
      Grain: one row per MLflow run x delivery period x area, same as the
      forecast fact. This is the intended BI surface — slice error by
      dim_date (holidays, fiscal year), dim_delivery_period (day parts) or
      weather. Error columns are null when the actual is missing (the TSO
      holes: Tokyo 2025-06-14 time codes 11-48, Kansai 2025-10-12); the
      percentage errors are additionally null when the actual is 0, so
      AVG(abs_pct_error) reproduces the run's mape_excl_zero_actuals metric
      and AVG(abs_error_kwh) its MAE. error_kwh is signed (forecast -
      actual, positive = over-forecast). Error measures are NON-ADDITIVE:
      average, never sum.
```

and change `actual_demand_kwh`'s `data_type` from `double` to `bigint` (the actual is the fact's bigint kWh). Resulting measure columns: `forecast_demand_kwh` (`double`, `not_null`), `actual_demand_kwh` (`bigint`), `error_kwh` (`double`), `abs_error_kwh` (`double`, `accepted_range min_value: 0`), `pct_error` (`double`), `abs_pct_error` (`double`, `accepted_range min_value: 0`); the leading key/degenerate columns are unchanged from the spot accuracy yml.

- [ ] **Step 6: Build (if the compose stack is up; otherwise defer to Task 17)**

Run: `just dbt build --select jepx_areas dim_area+ stg_ml__demand_forecast+`
Expected: seed, `dim_area` (with the relationships test passing), and — because `pma_ml.demand_forecast` does not exist yet — a *source* failure for the stg model. That is expected before the first run; `just dbt build --select jepx_areas dim_area` alone must pass now. Task 17 builds the rest after the first published run.

- [ ] **Step 7: Commit**

```bash
git add dbt/seeds/jepx_areas.csv dbt/models/curated/dim_area.sql dbt/models/curated/dim_area.yml dbt/models/raw/ml.yml dbt/models/staging/stg_ml__demand_forecast.sql dbt/models/staging/stg_ml__demand_forecast.yml dbt/models/standardized/std_ml__demand_forecast.sql dbt/models/standardized/std_ml__demand_forecast.yml dbt/models/curated/fct_demand_forecast.sql dbt/models/curated/fct_demand_forecast.yml dbt/models/curated/fct_demand_forecast_accuracy.sql dbt/models/curated/fct_demand_forecast_accuracy.yml
git commit -m "$(cat <<'EOF'
Add dim_area.representative_jma_station_id and the demand forecast dbt models

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (re-read it first — it has changed since the plan was written)

- [ ] **Step 1: Commands section**

After the `compare_spot_price_runs.py` bullet add:

```markdown
- `just python scripts/demand_backtest.py --strategy lightgbm --area tokyo` — day-ahead area
  demand backtest (strategies: `lightgbm`; areas: `tokyo`, `kansai` = the TSO feeds loaded into
  `fct_area_demand_generation_actual`). Same flags as the spot script (`--days` defaults to
  365); logs to the MLflow experiment `demand`, publishes to `pma_ml.demand_forecast`, then
  `just dbt build --select +fct_demand_forecast_accuracy`.
```

- [ ] **Step 2: Architecture section**

Replace the bare-package bullet added on 2026-08-18 ("Modeling tasks live under … TBD …") with:

```markdown
- Modeling tasks live under `power_market_analytics/tasks/<task>/` (`spot_price`, `demand`),
  each a thin configuration of the shared framework `power_market_analytics/forecasting/`:
  a frozen `TaskSpec` in the task's `__init__.py` (name = MLflow experiment, unit,
  `history_lead_days`, `issue_offset`, `forecast_table`, the task's four frame classes),
  frames as two-line subclasses of `forecasting.frames` (`HalfHourlySeries` / `DayAheadForecast`
  / `BacktestResult` / `ForecastRecords`, schema assembled from `value_col` /
  `forecast_col` / `actual_col`), `forecasting.backtest.run_backtest` (history the strategy
  sees = days ≤ `task.history_cutoff(D)`; a `ForecastUnavailableError` skips the day and is
  reported on `BacktestRun.skipped_days`; forecast points without an actual are dropped),
  `forecasting.lgbm.SlidingWindowLightGbmStrategy` (subclass sets `task`, `feature_cols`,
  `eval_set_cls`, `lookback_days`, implements `_add_features`), `forecasting.publish`
  and `forecasting.plots`. Adding a task = TaskSpec + frames + datasets + strategies +
  script + `pma_ml.<task>_forecast` dbt models.
- Demand task (`tasks/demand/`): at 09:30 JST on D-1 forecast the 48 half-hourly `demand_kwh`
  of `fct_area_demand_generation_actual` for D; usable history = days ≤ D-2
  (`history_lead_days = 2`, TSO files finalise after midnight). `lightgbm` features =
  `time_code, month, day_of_week, wavg_temperature_c, lag_7d_demand_kwh`;
  `wavg_temperature_c` = same-hour temperature at the area's representative JMA station
  (`dim_area.representative_jma_station_id`, seed `jepx_areas`; hour containing the period =
  `(time_code + 1) // 2`) over D-8..D-2, weights halving per day back (`demand/features.py`).
  Null-demand rows (TSO holes) are dropped at load; a target day whose D-7 lag falls in a hole
  is skipped. Write-back: `pma_ml.demand_forecast` → `stg/std_ml__demand_forecast` →
  `fct_demand_forecast` → `fct_demand_forecast_accuracy`.
```

Also update the existing forecast write-back bullet: `(`tasks/spot_price/publish.py`)` → `(`forecasting/publish.py`)`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
Document the forecasting framework and the demand task in CLAUDE.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

(The CLAUDE.md "Timestamps in tests" hunk the user added is committed alongside; mention it in the PR description.)

---

### Task 17: End-to-end verification in the devcontainer

**Files:** none (verification only; fix forward if anything fails, with a test first).

Requires the compose stack (`docker compose ps` shows the devcontainer, thriftserver and mlflow services up). If it is not running, stop and report — do not start services without the user.

- [ ] **Step 1: Rebuild the warehouse side**

Run: `just dbt build --select jepx_areas dim_area`
Expected: seed loaded, `dim_area` built, all tests pass (incl. the `relationships` test on the station column).

- [ ] **Step 2: Demand baseline, both areas**

Run: `just python scripts/demand_backtest.py --strategy lightgbm --area tokyo --days 60`
Expected: log ends with `MAE=… kWh  MAPE=…%`, `MLflow run: … (experiment: demand)`, `Forecasts written to pma_ml.demand_forecast`. Note the run id, MAE/MAPE and `n_days_skipped` (expected 0 for a 60-day window not touching 2025-06-21).

Run: `just python scripts/demand_backtest.py --strategy lightgbm --area kansai --days 60`
Expected: same shape (Kansai's representative station 大阪 s47772 must have weather rows loaded; if it raises `No temperature observations found`, report it — the JMA scrape for that station is a data prerequisite, not a code bug).

Run: `just python scripts/demand_backtest.py --area tokyo --start-date 2025-06-15 --end-date 2025-06-30`
Expected: finishes with `n_days_skipped=1` (2025-06-21) and a `Skipping 2025-06-21` warning in the log.

- [ ] **Step 3: dbt for the write-back**

Run: `just dbt build --select +fct_demand_forecast_accuracy`
Expected: `stg_ml__demand_forecast`, `std_ml__demand_forecast`, `fct_demand_forecast`, `fct_demand_forecast_accuracy` build and all their tests pass.

Spot check: `just dbt show --inline "select run_id, count(*) n, avg(abs_error_kwh) mae_kwh, avg(abs_pct_error) mape from pma_curated.fct_demand_forecast_accuracy group by run_id" --limit 10` — MAE/MAPE per run must equal the MLflow metrics of that run (MAPE to rounding).

- [ ] **Step 4: spot_price still works**

Run: `just python scripts/spot_price_backtest.py --strategy previous_day --area tokyo --days 30`
Expected: finishes; `n_days_skipped=0`; forecasts written to `pma_ml.spot_price_forecast`.
Run: `just dbt build --select +fct_spot_price_forecast_accuracy`
Expected: passes.

- [ ] **Step 5: Final gate and hand-off**

Run: `just test -q && uv run ruff check . && git status --short`
Expected: coverage 100 %, ruff clean, working tree clean apart from files the user has in flight (e.g. `docker/hive-metastore/Dockerfile`).

Report to the user: run ids + MAE/MAPE for the three demand runs and the spot run, anything skipped, and offer to open the PR (`superpowers:finishing-a-development-branch`).
