"""Where a task's holdout opens: the first delivery day no run has scored.

A holdout is only evidence while nobody has looked at it, and a run that scores
a day puts that day's errors in the accuracy mart, where the comparison scripts
and the dashboards read them. So the boundary is not a constant to maintain by
hand -- it moves every time a run reaches past it, and the first confirmation
run spends the days it scores.

``TaskSpec.holdout_start`` is the floor, for a warehouse that holds no run at
all; ``holdout_opens`` moves it past whatever has been scored since.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.task import TaskSpec


def accuracy_table(task: TaskSpec) -> str:
    """The curated accuracy mart of ``task``.

    Parameters
    ----------
    task : TaskSpec

    Returns
    -------
    str
        ``pma_curated.fct_<task>_forecast_accuracy``.
    """
    return f"pma_curated.fct_{task.name}_forecast_accuracy"


def scored_through(task: TaskSpec, spark: SparkSession | None = None) -> pd.Timestamp | None:
    """The last delivery day any run of ``task`` has scored.

    Parameters
    ----------
    task : TaskSpec
    spark : pyspark.sql.SparkSession, optional

    Returns
    -------
    pandas.Timestamp or None
        None when the mart holds no row, or does not exist yet: a warehouse that
        has never published a run has nothing to have read.
    """
    table = accuracy_table(task)
    try:
        rows = query_pandas(f"select max(date_key) as last_day from {table}", spark=spark)
    except Exception as exc:  # noqa: BLE001 - any failure to read means nothing was scored
        logger.warning("{} could not be read ({}): treating it as empty", table, type(exc).__name__)
        return None
    if rows.empty or pd.isna(rows["last_day"].iloc[0]):
        return None
    return pd.Timestamp(rows["last_day"].iloc[0])


def holdout_opens(task: TaskSpec, spark: SparkSession | None = None) -> pd.Timestamp:
    """The first delivery day of ``task`` that no run has scored.

    The task's floor, moved past anything already in the accuracy mart.

    Parameters
    ----------
    task : TaskSpec
    spark : pyspark.sql.SparkSession, optional

    Returns
    -------
    pandas.Timestamp

    Raises
    ------
    ValueError
        If the task has no pinned evaluation window.
    """
    floor = task.holdout_opens
    scored = scored_through(task, spark=spark)
    if scored is None:
        return floor
    return max(floor, scored + pd.Timedelta(days=1))
