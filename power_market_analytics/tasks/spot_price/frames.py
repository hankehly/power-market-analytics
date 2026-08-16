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
                f"{cls.__name__}: expected exactly time codes 1..{N_PERIODS}, got {len(df)} rows"
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


class ForecastRecords(DomainFrame):
    """One backtest run's forecasts shaped for the warehouse write-back table.

    Grain: (run_id, area_code, trade_date, time_code). A run currently covers
    a single area, but the declared grain is the business grain of
    ``pma_ml.spot_price_forecast`` — one forecast per run, area and delivery
    period. Forecasts only; actuals stay in the JEPX fact and are joined
    downstream by dbt.
    """

    schema = {
        "run_id": "object",
        "strategy": "object",
        "area_code": "object",
        "forecast_issued_ts": "datetime64[ns]",
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "forecast_price_jpy_kwh": "float64",
        "published_at": "datetime64[ns]",
    }
    keys = ["run_id", "area_code", "trade_date", "time_code"]
    non_null_cols = ["strategy", "forecast_issued_ts", "forecast_price_jpy_kwh", "published_at"]


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
