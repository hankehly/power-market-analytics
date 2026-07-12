"""Domain frames for the spot price forecasting task."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.common.frames import DomainFrame

N_PERIODS = 48


class SpotPrices(DomainFrame):
    """Half-hourly spot price history for one area.

    Grain: (trade_date, time_code).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "price_jpy_kwh": "float64",
    }
    keys = ["trade_date", "time_code"]
    non_null_cols = ["price_jpy_kwh"]


class DayAheadForecast(DomainFrame):
    """Forecast for one delivery day: exactly 48 half-hour prices.

    Grain: (trade_date, time_code); trade_date is the target delivery day.
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "forecast_price_jpy_kwh": "float64",
    }
    keys = ["trade_date", "time_code"]
    non_null_cols = ["forecast_price_jpy_kwh"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        if df["trade_date"].nunique() != 1:
            raise ValueError(
                f"{cls.__name__}: expected a single target day, got "
                f"{sorted(df['trade_date'].unique())}"
            )
        if len(df) != N_PERIODS or set(df["time_code"]) != set(range(1, N_PERIODS + 1)):
            raise ValueError(
                f"{cls.__name__}: expected exactly time codes 1..{N_PERIODS}, "
                f"got {len(df)} rows"
            )


class BacktestResult(DomainFrame):
    """Forecasts joined to actuals over a backtest window.

    Grain: (trade_date, time_code).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "actual_price_jpy_kwh": "float64",
        "forecast_price_jpy_kwh": "float64",
    }
    keys = ["trade_date", "time_code"]
    non_null_cols = ["actual_price_jpy_kwh", "forecast_price_jpy_kwh"]


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
                f"{sorted(bad.unique())}"
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
