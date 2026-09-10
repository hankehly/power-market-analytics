"""Domain frames for the spot price forecasting task."""

from __future__ import annotations

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
