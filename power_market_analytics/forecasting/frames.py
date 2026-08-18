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
                f"{cls.__name__}: time_code outside 1..{N_PERIODS}: "
                f"{[int(x) for x in sorted(bad.unique())]}"
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
