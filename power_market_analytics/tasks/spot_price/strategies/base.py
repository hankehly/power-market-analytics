"""Forecast strategy interface for the spot price task."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
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

    @abstractmethod
    def build_eval_set(
        self,
        prices: SpotPrices,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> DomainFrame:
        """Assemble the design matrix MLflow evaluates this strategy on.

        Every strategy must be evaluable: the backtest script always runs
        MLflow's regressor evaluation, so this is part of the contract rather
        than an optional extra.

        Parameters
        ----------
        prices : SpotPrices
            Full price history, including whatever lookback the features need
            before ``start_date``.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.

        Returns
        -------
        DomainFrame
            A frame exposing ``to_eval_frame()``: numeric feature columns plus
            the target column, with no non-numeric columns (the SHAP evaluator
            skips otherwise).
        """

    @abstractmethod
    def evaluate(self, eval_set: DomainFrame, **kwargs: object) -> EvaluationResult:
        """Log this strategy as a pyfunc model and evaluate it with MLflow.

        Called inside an active MLflow run; the model, metrics and SHAP plots
        land in that run.

        Parameters
        ----------
        eval_set : DomainFrame
            Design matrix from :meth:`build_eval_set`.
        **kwargs
            Forwarded to
            :func:`power_market_analytics.common.tracking.evaluate_regressor`.

        Returns
        -------
        mlflow.models.EvaluationResult
        """
