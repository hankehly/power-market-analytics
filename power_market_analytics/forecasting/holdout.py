"""Where a task's holdout opens: the first delivery day no run has scored.

A holdout is only evidence while nobody has looked at it, and a run that scores
a day writes that day's forecasts to the task's ``pma_ml`` table, from which the
accuracy mart, the comparison scripts and the dashboards all read. So the
boundary is not a constant to maintain by hand -- it moves every time a run
reaches past it, and the first confirmation run spends the days it scores.

The boundary is read from ``TaskSpec.forecast_table``, which every run writes
before it finishes, not from the curated accuracy mart, which is a dbt table and
so stays stale until the next ``dbt build``. A second confirmation run started
between a run and its rebuild would otherwise reuse the days the first had just
scored, and record them as unseen.

``TaskSpec.holdout_start`` is the floor, for a warehouse that holds no run at
all; ``holdout_opens`` moves it past whatever has been scored since.
"""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.common.spark import get_spark_session
from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.task import TaskSpec


def scored_through(task: TaskSpec, spark: SparkSession | None = None) -> pd.Timestamp | None:
    """The last delivery day any run of ``task`` has scored.

    Reads ``task.forecast_table``, which a run writes itself, so the answer does
    not wait for a dbt build.

    Parameters
    ----------
    task : TaskSpec
    spark : pyspark.sql.SparkSession, optional

    Returns
    -------
    pandas.Timestamp or None
        None only when the table does not exist or holds no row: a warehouse
        that has never published a run has nothing to have read. Any other
        failure to read is raised, because treating it as "nothing was scored"
        would move the boundary back and let a run reread days it has already
        seen while calling them unseen.
    """
    session = get_spark_session() if spark is None else spark
    if not session.catalog.tableExists(task.forecast_table):
        return None
    rows = query_pandas(
        f"select max(trade_date) as last_day from {task.forecast_table}", spark=session
    )
    if rows.empty or pd.isna(rows["last_day"].iloc[0]):
        return None
    return pd.Timestamp(rows["last_day"].iloc[0])


def holdout_opens(task: TaskSpec, spark: SparkSession | None = None) -> pd.Timestamp:
    """The first delivery day of ``task`` that no run has scored.

    The task's floor, moved past anything already published.

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
