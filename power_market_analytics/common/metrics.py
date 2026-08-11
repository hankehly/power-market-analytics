"""Generic forecast error metrics."""

from __future__ import annotations

import pandas as pd


def mae(actual: pd.Series, forecast: pd.Series) -> float:
    """Mean absolute error.

    Parameters
    ----------
    actual : pandas.Series
        Observed values.
    forecast : pandas.Series
        Predicted values, aligned with ``actual``.

    Returns
    -------
    float
    """
    return float((actual - forecast).abs().mean())


def mape(actual: pd.Series, forecast: pd.Series) -> float:
    """Mean absolute percentage error, in percent.

    Rows where ``actual`` is zero are excluded (the ratio is undefined).
    Note that near-zero actuals — real in JEPX data, where the price floor
    is 0.01 JPY/kWh — can still dominate this metric.

    Parameters
    ----------
    actual : pandas.Series
        Observed values.
    forecast : pandas.Series
        Predicted values, aligned with ``actual``.

    Returns
    -------
    float
    """
    nonzero = actual != 0
    return float(((actual - forecast).abs()[nonzero] / actual.abs()[nonzero]).mean() * 100)
