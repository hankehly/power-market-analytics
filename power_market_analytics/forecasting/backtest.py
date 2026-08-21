# power_market_analytics/forecasting/backtest.py
"""Rolling daily backtest engine shared by every task."""

from __future__ import annotations

import dataclasses

import pandas as pd
from loguru import logger

from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.forecasting.frames import GRAIN_COLS, BacktestResult, HalfHourlySeries
from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError


@dataclasses.dataclass(frozen=True)
class BacktestRun:
    """What a walk-forward backtest produced.

    Attributes
    ----------
    result : BacktestResult
        Forecasts joined to actuals for every day that was forecast.
    skipped_days : tuple of pandas.Timestamp
        Target days the strategy could not forecast (it raised
        ``ForecastUnavailableError``); they have no rows in ``result``.
    """

    result: BacktestResult
    skipped_days: tuple[pd.Timestamp, ...]


def run_backtest(
    strategy: ForecastStrategy,
    history: HalfHourlySeries,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> BacktestRun:
    """Backtest a strategy over each delivery day in a window.

    For each target day D in [start_date, end_date] that has actuals, the
    strategy receives only history through ``task.history_cutoff(D)`` —
    everything published by the task's issue time — and its 48 predictions
    are joined to the realized values.

    Gaps policy: a day the strategy cannot forecast (it raises
    ``ForecastUnavailableError``) is skipped with a warning and reported in
    ``skipped_days``; forecast points whose actual is missing are dropped
    from the result (count logged). Only a window in which *no* day could be
    forecast is an error.

    Parameters
    ----------
    strategy : ForecastStrategy
        Strategy under test; ``strategy.task`` fixes the cutoff and frames.
    history : HalfHourlySeries
        Full history; must cover the window plus whatever lookback the
        strategy needs before ``start_date``.
    start_date, end_date : pandas.Timestamp
        First and last delivery days to forecast, inclusive.

    Returns
    -------
    BacktestRun

    Raises
    ------
    ValueError
        If the window contains no delivery days, or none could be forecast.
    """
    task = strategy.task
    df = history.df
    target_days = sorted(
        df.loc[
            (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date), "trade_date"
        ].unique()
    )
    if not target_days:
        raise ValueError(f"No delivery days between {start_date} and {end_date}")
    logger.info(
        "Backtesting {} over {} days ({}..{})",
        strategy.name,
        len(target_days),
        pd.Timestamp(target_days[0]).date(),
        pd.Timestamp(target_days[-1]).date(),
    )

    forecasts: list[pd.DataFrame] = []
    skipped: list[pd.Timestamp] = []
    last_reason = ""
    for day in target_days:
        target_day = pd.Timestamp(day).as_unit("ns")
        visible = task.history_cls.from_df(df[df["trade_date"] <= task.history_cutoff(target_day)])
        try:
            forecasts.append(strategy.predict(target_day, visible).df)
        except ForecastUnavailableError as exc:
            last_reason = str(exc)
            logger.warning("Skipping {}: {}", target_day.date(), exc)
            skipped.append(target_day)
    if not forecasts:
        raise ValueError(
            f"No delivery day between {pd.Timestamp(target_days[0]).date()} and "
            f"{pd.Timestamp(target_days[-1]).date()} could be forecast "
            f"({len(skipped)} skipped; last reason: {last_reason})"
        )

    forecast_df = pd.concat(forecasts, ignore_index=True)
    actuals = df[df["trade_date"].isin(forecast_df["trade_date"].unique())].rename(
        columns={task.value_col: task.actual_col}
    )
    result = actuals.merge(forecast_df, how="inner", on=GRAIN_COLS, validate="one_to_one")
    n_unscored = len(forecast_df) - len(result)
    if n_unscored:
        logger.info("{} forecast points have no actual and were dropped", n_unscored)
    return BacktestRun(result=task.result_cls.from_df(result), skipped_days=tuple(skipped))


def daily_metrics(result: BacktestResult) -> pd.DataFrame:
    """Per-delivery-day error metrics.

    Parameters
    ----------
    result : BacktestResult
        Any task's backtest result; the actual/forecast columns are read off
        its class.

    Returns
    -------
    pandas.DataFrame
        One row per trade_date with ``mae`` and ``mape`` columns.
    """
    actual_col, forecast_col = type(result).actual_col, type(result).forecast_col
    return (
        result.df.groupby("trade_date")[[actual_col, forecast_col]]
        .apply(
            lambda g: pd.Series(
                {
                    "mae": mae(g[actual_col], g[forecast_col]),
                    "mape": mape(g[actual_col], g[forecast_col]),
                }
            )
        )
        .reset_index()
    )
