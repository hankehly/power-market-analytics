"""Feature helpers shared by forecast strategies."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.frames import GRAIN_COLS


def join_lag(
    left: pd.DataFrame, series: pd.DataFrame, *, value_col: str, days: int, name: str
) -> pd.DataFrame:
    """Attach the value from ``days`` calendar days earlier, same time code.

    Joins on calendar date rather than row position so that gaps in the
    history (e.g. Hokkaido's 2018 suspension) shift no rows; points whose
    lagged day is missing get NaN.

    Parameters
    ----------
    left : pandas.DataFrame
        Frame to attach the lag to; keyed on (trade_date, time_code).
    series : pandas.DataFrame
        Source history in a ``HalfHourlySeries`` layout with ``value_col``.
    value_col : str
        Column of ``series`` to lag, e.g. ``price_jpy_kwh``.
    days : int
        Lag length in calendar days.
    name : str
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
    """
    lagged = series[[*GRAIN_COLS, value_col]].assign(
        trade_date=series["trade_date"] + pd.Timedelta(days=days)
    )
    return left.merge(
        lagged.rename(columns={value_col: name}),
        how="left",
        on=GRAIN_COLS,
        validate="one_to_one",
    )
