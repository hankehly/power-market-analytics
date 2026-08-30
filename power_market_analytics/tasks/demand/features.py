"""Temperature, calendar and year-ago load features for the demand forecasting task."""

from __future__ import annotations

import numpy as np
import pandas as pd

from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.frames import (
    DAY_TYPE_LEVELS,
    AreaHourlyLoad,
    AreaTemperature,
    AreaTemperatureForecast,
    DayTypeCalendar,
    PriorYearCalendar,
)

#: Days before delivery day D whose same-hour temperature enters the feature:
#: the seven most recent *complete* observation days at 09:30 D-1 (D-1 is
#: still in progress, so it is excluded).
TEMPERATURE_LAG_DAYS: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8)
#: Weight halves for every ``half_life_days`` further back: D-2 -> 1, D-3 -> 1/2, ... D-8 -> 1/64.
TEMPERATURE_HALF_LIFE_DAYS = 1.0
TEMPERATURE_FEATURE = "wavg_temperature_c"
#: The MSM forecast temperature for the delivery-day hour containing the period.
FORECAST_TEMPERATURE_FEATURE = "forecast_temperature_c"
#: The same, population-weighted over the area's staffed stations instead of
#: taken at the representative station.
POPW_FORECAST_TEMPERATURE_FEATURE = "popw_forecast_temperature_c"
#: The delivery day's type as a LightGBM categorical: the code of its level in
#: ``DAY_TYPE_LEVELS`` (0 = Weekday, 1 = Weekend, 2 = Holiday).
DAY_TYPE_FEATURE = "day_type"
#: Code of each day-type level (its index in ``DAY_TYPE_LEVELS``).
DAY_TYPE_CODES: dict[str, int] = {level: code for code, level in enumerate(DAY_TYPE_LEVELS)}
#: The year-ago load: the hourly load on the delivery day's
#: ``dim_date.prior_year_reference_date`` at the hour containing the period,
#: expressed per delivery period.
LAG_1Y_FEATURE = "lag_1y_demand_kwh"
#: Delivery periods per hour: an hour's energy is spread evenly over its two
#: 30-minute periods, which puts the hourly series on the target's scale.
PERIODS_PER_HOUR = 2


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


def join_forecast_temperature(
    points: pd.DataFrame,
    forecast: AreaTemperatureForecast,
    *,
    name: str = FORECAST_TEMPERATURE_FEATURE,
) -> pd.DataFrame:
    """Attach the forecast temperature of the delivery-day hour containing each period.

    For a point (D, time_code) the feature is the station's forecast
    temperature for delivery day D at ``hour_ending_of(time_code)`` — the
    same period-to-hour alignment as :func:`recency_weighted_temperature`.
    It is NaN where the forecast has no row (or a null value) for that hour.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    forecast : AreaTemperatureForecast
        Hourly forecast temperature at the area's representative station.
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name``, in the original row order.
    """
    keyed = points[GRAIN_COLS].assign(hour_ending=hour_ending_of(points["time_code"]))
    # Two periods share an hour, hence many_to_one; a left merge keeps the
    # left row order.
    joined = keyed.merge(
        forecast.df, how="left", on=["trade_date", "hour_ending"], validate="many_to_one"
    )
    return points.assign(**{name: joined["forecast_temperature_c"].to_numpy(dtype="float64")})


def day_type_code(is_weekend: pd.Series, is_holiday: pd.Series) -> pd.Series:
    """Code each day's type from ``dim_date``'s weekend and holiday flags.

    A holiday is ``Holiday`` whatever weekday it falls on; otherwise a
    Saturday/Sunday is ``Weekend`` and anything else ``Weekday`` — the
    precedence of the demand compare script's day-type segment.

    Parameters
    ----------
    is_weekend, is_holiday : pandas.Series
        Boolean flags on the same index.

    Returns
    -------
    pandas.Series
        int64 codes (indices into ``DAY_TYPE_LEVELS``) on ``is_holiday``'s index.
    """
    codes = np.where(
        is_holiday.to_numpy(dtype=bool),
        DAY_TYPE_CODES["Holiday"],
        np.where(
            is_weekend.to_numpy(dtype=bool), DAY_TYPE_CODES["Weekend"], DAY_TYPE_CODES["Weekday"]
        ),
    )
    return pd.Series(codes.astype("int64"), index=is_holiday.index)


def join_day_type(
    points: pd.DataFrame, calendar: DayTypeCalendar, *, name: str = DAY_TYPE_FEATURE
) -> pd.DataFrame:
    """Attach the delivery day's day-type code to each period.

    The code is NaN where the calendar has no row for the day, so a target
    day outside ``dim_date`` is unforecastable rather than silently a weekday.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    calendar : DayTypeCalendar
        Day type of every calendar day.
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name`` (float64: the code, NaN where unavailable),
        in the original row order.
    """
    # The 48 periods of a day share its row, hence many_to_one; a left merge
    # keeps the left row order.
    joined = points[["trade_date"]].merge(
        calendar.df, how="left", on="trade_date", validate="many_to_one"
    )
    return points.assign(**{name: joined["day_type"].to_numpy(dtype="float64")})


def join_prior_year_load(
    points: pd.DataFrame,
    calendar: PriorYearCalendar,
    hourly_load: AreaHourlyLoad,
    *,
    name: str = LAG_1Y_FEATURE,
) -> pd.DataFrame:
    """Attach the year-ago load of the hour containing each period.

    For a point (D, time_code) the feature is the hourly load on D's
    ``prior_year_reference_date`` (per ``calendar``) at
    ``hour_ending_of(time_code)``, divided by :data:`PERIODS_PER_HOUR` so the
    hour's energy is spread evenly over its two delivery periods and the
    value sits on the target's scale (kWh per 30-minute period). It is NaN
    where D has no calendar row or the reference hour has no load.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    calendar : PriorYearCalendar
        Prior-year reference date of every delivery day.
    hourly_load : AreaHourlyLoad
        Hourly load history of the area.
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name`` (float64), in the original row order.
    """
    # The 48 periods of a day share its reference, hence many_to_one; a left
    # merge keeps the left row order.
    keyed = points[GRAIN_COLS].merge(
        calendar.df[["trade_date", "prior_year_reference_date"]],
        how="left",
        on="trade_date",
        validate="many_to_one",
    )
    keyed = keyed.assign(hour_ending=hour_ending_of(keyed["time_code"]))
    load = hourly_load.df.rename(columns={"load_date": "prior_year_reference_date"})
    # Two periods share an hour, hence many_to_one again.
    joined = keyed.merge(
        load, how="left", on=["prior_year_reference_date", "hour_ending"], validate="many_to_one"
    )
    per_period = joined["demand_kwh"].to_numpy(dtype="float64") / PERIODS_PER_HOUR
    return points.assign(**{name: per_period})
