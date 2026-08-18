"""Forecast strategy registry for the spot price task."""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.forecasting.strategy import ForecastStrategy
from power_market_analytics.tasks.spot_price.datasets import load_occto_demand_forecast
from power_market_analytics.tasks.spot_price.strategies.lgbm import (
    LightGbmOcctoStrategy,
    LightGbmStrategy,
)
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy

STRATEGIES: dict[str, type[ForecastStrategy]] = {
    PreviousDayStrategy.name: PreviousDayStrategy,
    LightGbmStrategy.name: LightGbmStrategy,
    LightGbmOcctoStrategy.name: LightGbmOcctoStrategy,
}


def build_strategy(
    name: str,
    *,
    area_code: str,
    train_start_date: pd.Timestamp | None = None,
    spark: SparkSession | None = None,
) -> ForecastStrategy:
    """Instantiate a registered strategy with the inputs it needs.

    Strategies that fit a model accept ``train_start_date``; strategies that
    consume exogenous data get it loaded from the warehouse here, so callers
    only deal in registry names.

    Parameters
    ----------
    name : str
        Key in ``STRATEGIES``.
    area_code : str
        dim_area.area_code value being forecast; scopes any exogenous data.
    train_start_date : pandas.Timestamp, optional
        First delivery day eligible as a training row (model strategies
        only).
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
        If ``train_start_date`` is given for a strategy without a training
        step.
    """
    cls = STRATEGIES[name]
    kwargs: dict[str, object] = {}
    if issubclass(cls, LightGbmStrategy):
        kwargs["train_start_date"] = train_start_date
    elif train_start_date is not None:
        raise ValueError(f"{name!r} has no training step; train_start_date does not apply")
    if issubclass(cls, LightGbmOcctoStrategy):
        kwargs["occto"] = load_occto_demand_forecast(area_code, spark=spark)
    return cls(**kwargs)
