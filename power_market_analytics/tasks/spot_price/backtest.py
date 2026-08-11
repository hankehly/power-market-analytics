"""Rolling daily backtest engine for the spot price task."""

from __future__ import annotations

import logging

import pandas as pd

from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.tasks.spot_price.frames import BacktestResult, SpotPrices
from power_market_analytics.tasks.spot_price.strategies.base import ForecastStrategy

logger = logging.getLogger(__name__)


def run_backtest(
    strategy: ForecastStrategy,
    prices: SpotPrices,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> BacktestResult:
    """Backtest a strategy over each delivery day in a window.

    For each target day D in [start_date, end_date], the strategy receives
    only history through D-1 — everything published by the forecast time of
    9:55 JST on D-1 (D-1's own prices were published ~noon on D-2) — and its
    48 predictions are joined to the realized prices.

    Parameters
    ----------
    strategy : ForecastStrategy
        Strategy under test.
    prices : SpotPrices
        Full price history; must cover the window plus whatever lookback the
        strategy needs before ``start_date``.
    start_date, end_date : pandas.Timestamp
        First and last delivery days to forecast, inclusive.

    Returns
    -------
    BacktestResult

    Raises
    ------
    ValueError
        If the window contains no delivery days.
    """
    df = prices.df
    target_days = sorted(
        df.loc[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date), "trade_date"]
        .unique()
    )
    if not target_days:
        raise ValueError(f"No delivery days between {start_date} and {end_date}")
    logger.info(
        "Backtesting %s over %d days (%s..%s)",
        strategy.name,
        len(target_days),
        pd.Timestamp(target_days[0]).date(),
        pd.Timestamp(target_days[-1]).date(),
    )

    forecasts = []
    for target_day in target_days:
        history = SpotPrices.from_df(df[df["trade_date"] < target_day])
        forecasts.append(strategy.predict(pd.Timestamp(target_day), history).df)

    result = pd.merge(
        df.rename(columns={"price_jpy_kwh": "actual_price_jpy_kwh"}),
        pd.concat(forecasts, ignore_index=True),
        how="inner",
        on=["trade_date", "time_code"],
        validate="one_to_one",
    )
    n_expected = len(forecasts) * 48
    if len(result) != n_expected:
        raise ValueError(
            f"Forecast/actual join produced {len(result)} rows, expected {n_expected}"
        )
    return BacktestResult.from_df(result)


def daily_metrics(result: BacktestResult) -> pd.DataFrame:
    """Per-delivery-day error metrics.

    Parameters
    ----------
    result : BacktestResult

    Returns
    -------
    pandas.DataFrame
        One row per trade_date with ``mae`` and ``mape`` columns.
    """
    return (
        result.df.groupby("trade_date")
        .apply(
            lambda g: pd.Series(
                {
                    "mae": mae(g["actual_price_jpy_kwh"], g["forecast_price_jpy_kwh"]),
                    "mape": mape(g["actual_price_jpy_kwh"], g["forecast_price_jpy_kwh"]),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
