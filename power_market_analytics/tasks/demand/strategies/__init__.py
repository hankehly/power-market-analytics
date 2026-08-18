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
