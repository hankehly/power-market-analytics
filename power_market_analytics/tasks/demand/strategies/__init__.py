"""Forecast strategy registry for the demand task.

A strategy name is a preset of ``tasks.demand.presets``, built as a
:class:`~power_market_analytics.forecasting.preset_lgbm.PresetLightGbmStrategy`
over the features Feast retrieves for the run's days, each row as of its own
issue time; or one of the similar-day strategies, which still build their
features in Python until the similar day is a mart column of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.features.frame import feature_frame
from power_market_analytics.features.presets import categorical_columns, feature_dtypes
from power_market_analytics.features.retrieval import entity_frame, historical_features
from power_market_analytics.features.store import open_store
from power_market_analytics.forecasting.preset_lgbm import PresetLightGbmStrategy
from power_market_analytics.forecasting.strategy import ForecastStrategy
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.datasets import (
    load_area_hourly_load,
    load_area_observed_weather_population_weighted,
    load_area_temperature,
    load_area_weather_forecast_population_weighted,
    load_day_calendar,
)
from power_market_analytics.tasks.demand.presets import PRESETS
from power_market_analytics.tasks.demand.strategies.lgbm import (
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy,
)

#: The similar-day strategies by name: the Tokyo baseline and its four calendar variants.
SIMILAR_DAY_STRATEGIES: dict[str, type[LightGbmMsmPopWeightedDayTypeSimilarDayStrategy]] = {
    cls.name: cls
    for cls in (
        LightGbmMsmPopWeightedDayTypeSimilarDayStrategy,
        LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy,
        LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
        LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
        LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
    )
}

#: Every strategy name the backtest script accepts: the presets, then the similar-day ones.
STRATEGIES: tuple[str, ...] = (*PRESETS, *SIMILAR_DAY_STRATEGIES)


def build_strategy(
    name: str,
    *,
    area_code: str,
    days: pd.DatetimeIndex | None = None,
    train_start_date: pd.Timestamp | None = None,
    add: Sequence[str] = (),
    drop: Sequence[str] = (),
    label: str | None = None,
    spark: SparkSession | None = None,
) -> ForecastStrategy:
    """Instantiate a registered strategy with the inputs it needs.

    A preset's features are retrieved once here, for every delivery period
    of ``days`` as of its issue time, so callers only deal in names. Which
    features are categorical is read off the views. A similar-day strategy
    loads its inputs from the warehouse as before.

    Parameters
    ----------
    name : str
        A preset name or a similar-day strategy name.
    area_code : str
        dim_area.area_code value being forecast.
    days : pandas.DatetimeIndex, optional
        The delivery days a preset strategy may train on or forecast; required
        for a preset.
    train_start_date : pandas.Timestamp, optional
        First delivery day eligible as a training row.
    add, drop : sequence of str, optional
        Feature references added to or dropped from the preset; need ``label``.
    label : str, optional
        The strategy label of the run when it differs from the preset's name;
        required with ``add`` or ``drop``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse for the similar-day strategies' warehouse reads.

    Returns
    -------
    ForecastStrategy

    Raises
    ------
    KeyError
        If ``name`` is not registered.
    ValueError
        If ``add``, ``drop`` or ``label`` is given for a similar-day strategy,
        ``add`` / ``drop`` come without ``label``, or a preset is built
        without ``days``.
    """
    if name in SIMILAR_DAY_STRATEGIES:
        if add or drop or label:
            raise ValueError(f"{name!r} takes no feature changes until it is a preset")
        return _build_similar_day(
            SIMILAR_DAY_STRATEGIES[name], area_code, train_start_date=train_start_date, spark=spark
        )
    preset = PRESETS[name]
    if add or drop:
        if not label:
            raise ValueError(f"{name!r} with features added or dropped needs a label")
        preset = preset.with_changes(add=add, drop=drop, name=label)
    if days is None:
        raise ValueError(f"{name!r} needs the days to retrieve its features for")
    store = open_store()
    retrieved = historical_features(
        store, entity_frame(area_code, days, TASK.issue_offset), preset.features
    )
    return PresetLightGbmStrategy(
        TASK,
        preset,
        feature_frame(retrieved, preset.columns),
        dtypes=feature_dtypes(preset),
        categorical=categorical_columns(preset),
        name=label,
        train_start_date=train_start_date,
    )


def _build_similar_day(
    cls: type[LightGbmMsmPopWeightedDayTypeSimilarDayStrategy],
    area_code: str,
    *,
    train_start_date: pd.Timestamp | None,
    spark: SparkSession | None,
) -> LightGbmMsmPopWeightedDayTypeSimilarDayStrategy:
    """A similar-day strategy over the area's temperature, weather, calendar and hourly load.

    Raises
    ------
    ValueError
        If the area has no temperature observations or forecasts.
    """
    temperature = load_area_temperature(area_code, spark=spark)
    weather = load_area_weather_forecast_population_weighted(area_code, spark=spark)
    observed = load_area_observed_weather_population_weighted(
        area_code, census_year=weather.census_year, spark=spark
    )
    return cls(
        temperature,
        weather.forecast,
        load_day_calendar(spark=spark),
        observed.weather,
        load_area_hourly_load(area_code, spark=spark),
        census_year=weather.census_year,
        train_start_date=train_start_date,
    )
