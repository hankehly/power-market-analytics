"""Shared feature engineering for spot price forecast strategies."""

from __future__ import annotations

import pandas as pd


def join_lag(
    left: pd.DataFrame, prices: pd.DataFrame, *, days: int, name: str
) -> pd.DataFrame:
    """Attach the price from ``days`` calendar days earlier, same time code.

    Joins on calendar date rather than row position so that gaps in the
    history (e.g. Hokkaido's 2018 suspension) shift no rows; points whose
    lagged day is missing get NaN.

    Parameters
    ----------
    left : pandas.DataFrame
        Frame to attach the lag to; keyed on (trade_date, time_code).
    prices : pandas.DataFrame
        Source price history with a ``price_jpy_kwh`` column.
    days : int
        Lag length in calendar days.
    name : str
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
    """
    lagged = prices[["trade_date", "time_code", "price_jpy_kwh"]].assign(
        trade_date=prices["trade_date"] + pd.Timedelta(days=days)
    )
    return left.merge(
        lagged.rename(columns={"price_jpy_kwh": name}),
        how="left",
        on=["trade_date", "time_code"],
        validate="one_to_one",
    )
