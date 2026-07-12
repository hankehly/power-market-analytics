"""Naive baseline strategies."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.tasks.spot_price.frames import DayAheadForecast, SpotPrices
from power_market_analytics.tasks.spot_price.strategies.base import ForecastStrategy


class PreviousDayStrategy(ForecastStrategy):
    """Forecast each time code with the same time code's price from D-1."""

    name = "previous_day"

    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> DayAheadForecast:
        """Copy the previous delivery day's 48 prices onto the target day.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : SpotPrices
            Price history through D-1.

        Returns
        -------
        DayAheadForecast

        Raises
        ------
        ValueError
            If the previous day is not fully present in the history.
        """
        previous_day = target_date - pd.Timedelta(days=1)
        prev = history.df[history.df["trade_date"] == previous_day]
        if len(prev) == 0:
            raise ValueError(f"{self.name}: no history for previous day {previous_day.date()}")
        forecast = prev.assign(trade_date=target_date).rename(
            columns={"price_jpy_kwh": "forecast_price_jpy_kwh"}
        )
        return DayAheadForecast.from_df(forecast)
