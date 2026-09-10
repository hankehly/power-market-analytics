"""Calendar features and hour alignment for the demand forecasting task.

The temperature and lag features live in the feature marts since the feature
catalogue's PR 6; the similar-day strategies still join the ``DayCalendar``
columns here."""

from __future__ import annotations

import numpy as np
import pandas as pd

from power_market_analytics.tasks.demand.frames import DAY_TYPE_LEVELS, DayCalendar

#: Code of each day-type level (its index in ``DAY_TYPE_LEVELS``): the
#: ``day_type`` feature, 0 = Weekday, 1 = Weekend, 2 = Holiday.
DAY_TYPE_CODES: dict[str, int] = {level: code for code, level in enumerate(DAY_TYPE_LEVELS)}
#: The delivery day's calendar attributes, read from ``dim_date`` through
#: ``DayCalendar`` (research demand/R-005): the calendar counts, the graded
#: holiday degree, the working-day flag (1 / 0) and the distances in days to
#: the nearest named holiday. All plain numeric features.
DAY_CALENDAR_FEATURE_COLS: tuple[str, ...] = (
    "half",
    "quarter",
    "day_of_month",
    "day_of_quarter",
    "day_of_year",
    "holiday_degree",
    "is_business_day",
    "fiscal_quarter",
    "days_since_holiday",
    "days_until_holiday",
)
#: Subsets of ``DAY_CALENDAR_FEATURE_COLS`` tried on their own after R-005
#: E-001: the graded holiday degree alone (E-002), the two distances in days
#: to the nearest named holiday (E-003) and the six calendar counts (E-004).
HOLIDAY_DEGREE_FEATURE_COLS: tuple[str, ...] = ("holiday_degree",)
HOLIDAY_DISTANCE_FEATURE_COLS: tuple[str, ...] = ("days_since_holiday", "days_until_holiday")
CALENDAR_COUNT_FEATURE_COLS: tuple[str, ...] = (
    "half",
    "quarter",
    "day_of_month",
    "day_of_quarter",
    "day_of_year",
    "fiscal_quarter",
)


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


def join_day_calendar(
    points: pd.DataFrame,
    calendar: DayCalendar,
    *,
    cols: tuple[str, ...] = DAY_CALENDAR_FEATURE_COLS,
) -> pd.DataFrame:
    """Attach the delivery day's calendar attributes to each period.

    Every attribute is float64 (``is_business_day`` as 1.0 / 0.0) and NaN
    where the calendar has no row for the day, so a target day outside
    ``dim_date`` is unforecastable, as with the day type.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    calendar : DayCalendar
        Calendar attributes of every day.
    cols : tuple of str, optional
        ``DayCalendar`` columns to attach, in this order.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``cols`` (float64, NaN where unavailable), in the
        original row order.
    """
    joined = points[["trade_date"]].merge(
        calendar.df[["trade_date", *cols]], how="left", on="trade_date", validate="many_to_one"
    )
    return points.assign(**{col: joined[col].to_numpy(dtype="float64") for col in cols})
