"""Forecast strategy interface for the spot price task."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from power_market_analytics.tasks.spot_price.frames import DayAheadForecast, SpotPrices


class ForecastStrategy(ABC):
    """Produces a 48-period day-ahead price forecast for one delivery day.

    Attributes
    ----------
    name : str
        Registry key and MLflow tag for the strategy.
    """

    name: str

    @abstractmethod
    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> DayAheadForecast:
        """Forecast all 48 prices for one delivery day.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : SpotPrices
            Price history available at forecast time (9:55 JST on D-1),
            i.e. delivery days <= D-1 only. The backtest engine enforces
            this cutoff; strategies must not assume anything newer exists.

        Returns
        -------
        DayAheadForecast
        """
