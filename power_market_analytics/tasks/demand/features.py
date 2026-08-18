"""Temperature features for the demand forecasting task."""

from __future__ import annotations

import numpy as np
import pandas as pd

from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.frames import AreaTemperature

#: Days before delivery day D whose same-hour temperature enters the feature:
#: the seven most recent *complete* observation days at 09:30 D-1 (D-1 is
#: still in progress, so it is excluded).
TEMPERATURE_LAG_DAYS: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8)
#: Weight halves for every ``half_life_days`` further back: D-2 -> 1, D-3 -> 1/2, ... D-8 -> 1/64.
TEMPERATURE_HALF_LIFE_DAYS = 1.0
TEMPERATURE_FEATURE = "wavg_temperature_c"


def hour_ending_of(time_code: pd.Series) -> pd.Series:
    """JMA observation hour (1..24, hour-ending) containing the start of a period.

    Period ``time_code`` starts at ``(time_code - 1) * 30`` minutes; the
    observation hour that contains that instant ends at hour
    ``(time_code + 1) // 2`` — the alignment ``fct_jma_weather_hourly``
    documents (broadcast each hour to its two delivery periods).

    Parameters
    ----------
    time_code : pandas.Series
        JEPX time codes 1..48.

    Returns
    -------
    pandas.Series
        int64 hour-ending values 1..24.
    """
    return ((time_code + 1) // 2).astype("int64")


def recency_weighted_temperature(
    points: pd.DataFrame,
    temperature: AreaTemperature,
    *,
    lag_days: tuple[int, ...] = TEMPERATURE_LAG_DAYS,
    half_life_days: float = TEMPERATURE_HALF_LIFE_DAYS,
    name: str = TEMPERATURE_FEATURE,
) -> pd.DataFrame:
    """Attach the recency-weighted mean of the same-hour temperature over past days.

    For a point (D, time_code) the feature is the weighted mean of the
    station's temperature at ``hour_ending_of(time_code)`` on days
    ``D - k`` for ``k`` in ``lag_days``, with weight
    ``0.5 ** ((k - min(lag_days)) / half_life_days)``. Weights are
    renormalised over the lags that have a value, so a missing hour lowers
    the effective sample rather than the result; the feature is NaN only when
    every lag is missing.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    temperature : AreaTemperature
        Hourly temperature at the area's representative station.
    lag_days : tuple of int, optional
        Days before D to average over; must not be empty.
    half_life_days : float, optional
        Days over which a lag's weight halves.
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name``, in the original row order.

    Raises
    ------
    ValueError
        If ``lag_days`` is empty.
    """
    if not lag_days:
        raise ValueError("lag_days must not be empty")
    keyed = points[GRAIN_COLS].assign(hour_ending=hour_ending_of(points["time_code"]))
    temp = temperature.df
    first = min(lag_days)
    columns = []
    weights = []
    for k in lag_days:
        lagged = temp.assign(trade_date=temp["obs_date"] + pd.Timedelta(days=k))[
            ["trade_date", "hour_ending", "temperature_c"]
        ]
        # Two periods share an hour, hence many_to_one; a left merge keeps
        # the left row order.
        joined = keyed.merge(
            lagged, how="left", on=["trade_date", "hour_ending"], validate="many_to_one"
        )
        columns.append(joined["temperature_c"].to_numpy(dtype="float64"))
        weights.append(0.5 ** ((k - first) / half_life_days))
    values = np.column_stack(columns)
    w = np.asarray(weights, dtype="float64")
    present = ~np.isnan(values)
    weighted_sum = np.nansum(values * w, axis=1)
    weight_sum = (present * w).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        feature = np.where(weight_sum > 0, weighted_sum / weight_sum, np.nan)
    return points.assign(**{name: feature})
