"""Domain frames for the spot price forecasting task."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)


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


class SpotPriceForecast(DayAheadForecast):
    """Forecast for one delivery day: exactly 48 half-hour prices.

    Grain: (trade_date, time_code); trade_date is the target delivery day.
    """

    forecast_col = "forecast_price_jpy_kwh"


class SpotPriceBacktestResult(BacktestResult):
    """Forecasts joined to actual prices over a backtest window.

    Grain: (trade_date, time_code).
    """

    actual_col = "actual_price_jpy_kwh"
    forecast_col = "forecast_price_jpy_kwh"


class SpotPriceForecastRecords(ForecastRecords):
    """One backtest run's price forecasts shaped for ``pma_ml.spot_price_forecast``.

    Grain: (run_id, area_code, trade_date, time_code).
    """

    forecast_col = "forecast_price_jpy_kwh"
