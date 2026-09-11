"""Forecast strategy registry for the demand task.

A strategy name is a preset of ``tasks.demand.presets``, built as a
:class:`~power_market_analytics.forecasting.preset_lgbm.PresetLightGbmStrategy`
over the features Feast retrieves for the run's days, each row as of its own
issue time.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from power_market_analytics.features.frame import feature_frame
from power_market_analytics.features.presets import categorical_columns, feature_dtypes
from power_market_analytics.features.retrieval import entity_frame, historical_features
from power_market_analytics.features.store import open_store
from power_market_analytics.forecasting.preset_lgbm import PresetLightGbmStrategy
from power_market_analytics.forecasting.strategy import ForecastStrategy
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.presets import PRESETS

#: Every strategy name the backtest script accepts: the presets.
STRATEGIES: tuple[str, ...] = tuple(PRESETS)


def build_strategy(
    name: str,
    *,
    area_code: str,
    days: pd.DatetimeIndex | None = None,
    train_start_date: pd.Timestamp | None = None,
    add: Sequence[str] = (),
    drop: Sequence[str] = (),
    label: str | None = None,
) -> ForecastStrategy:
    """Instantiate a registered strategy with the inputs it needs.

    A preset's features are retrieved once here, for every delivery period
    of ``days`` as of its issue time, so callers only deal in names. Which
    features are categorical is read off the views, so an added one is
    treated as its mart declares it.

    Parameters
    ----------
    name : str
        A preset name.
    area_code : str
        dim_area.area_code value being forecast.
    days : pandas.DatetimeIndex, optional
        The delivery days the strategy may train on or forecast; required.
    train_start_date : pandas.Timestamp, optional
        First delivery day eligible as a training row.
    add, drop : sequence of str, optional
        Feature references added to or dropped from the preset; need ``label``.
    label : str, optional
        The strategy label of the run when it differs from the preset's name;
        required with ``add`` or ``drop``.

    Returns
    -------
    ForecastStrategy

    Raises
    ------
    KeyError
        If ``name`` is not registered.
    ValueError
        If ``add`` / ``drop`` come without ``label``, or ``days`` is missing.
    """
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
