"""Forecast strategy registry for the demand task."""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.forecasting.strategy import ForecastStrategy
from power_market_analytics.tasks.demand.datasets import (
    load_area_temperature,
    load_area_temperature_forecast,
    load_area_temperature_forecast_population_weighted,
)
from power_market_analytics.tasks.demand.strategies.lgbm import (
    LightGbmMsmPopWeightedStrategy,
    LightGbmMsmStrategy,
    LightGbmStrategy,
)

# Typed to the concrete base because build_strategy instantiates entries with
# LightGbmStrategy's constructor signature (temperature + train_start_date).
STRATEGIES: dict[str, type[LightGbmStrategy]] = {
    LightGbmStrategy.name: LightGbmStrategy,
    LightGbmMsmStrategy.name: LightGbmMsmStrategy,
    LightGbmMsmPopWeightedStrategy.name: LightGbmMsmPopWeightedStrategy,
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
    ``train_start_date`` forwarded; strategies that also consume the MSM
    forecast temperature get it loaded too — at the representative station,
    or population-weighted over the area's stations with the latest census
    vintage. Callers only deal in registry names.

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
        If the area has no temperature observations (or, for a strategy that
        needs them, no temperature forecasts).
    """
    cls = STRATEGIES[name]
    temperature = load_area_temperature(area_code, spark=spark)
    if issubclass(cls, LightGbmMsmPopWeightedStrategy):
        weighted = load_area_temperature_forecast_population_weighted(area_code, spark=spark)
        return cls(
            temperature,
            weighted.forecast,
            census_year=weighted.census_year,
            train_start_date=train_start_date,
        )
    if issubclass(cls, LightGbmMsmStrategy):
        forecast = load_area_temperature_forecast(area_code, spark=spark)
        return cls(temperature, forecast, train_start_date=train_start_date)
    return cls(temperature, train_start_date=train_start_date)
