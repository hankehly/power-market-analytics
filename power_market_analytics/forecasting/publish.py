"""Write backtest forecasts back to the Spark warehouse.

MLflow stays the system of record for the experiment (params, metrics,
artifacts); the warehouse holds the row-level forecasts so dbt can join them
to actuals and dimensions and Superset can chart them. ``run_id`` is the link
between the two systems.

Each task has its own destination table (``TaskSpec.forecast_table``) with
its own forecast column name; the table is partitioned by ``run_id`` and
written with dynamic partition overwrite, so republishing a run replaces
exactly that run's rows.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.forecasting.frames import BacktestResult, ForecastRecords
from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.spark import get_spark_session


def build_forecast_records(
    task: TaskSpec, result: BacktestResult, *, run_id: str, strategy: str, area_code: str
) -> ForecastRecords:
    """Shape a backtest result into warehouse write-back records.

    Parameters
    ----------
    task : TaskSpec
        Task the result belongs to; supplies the issue offset and the
        records frame class.
    result : BacktestResult
        Forecasts joined to actuals; the actuals column is dropped here —
        the warehouse table stores forecasts only.
    run_id : str
        MLflow run id the forecasts belong to.
    strategy : str
        Strategy registry key, e.g. ``previous_day``.
    area_code : str
        dim_area.area_code value the run forecast, e.g. ``tokyo``.

    Returns
    -------
    ForecastRecords
        An instance of ``task.records_cls``.
    """
    df = result.df.assign(
        run_id=run_id,
        strategy=strategy,
        area_code=area_code,
        forecast_issued_ts=lambda d: d["trade_date"] + task.issue_offset,
        # Naive JST like every other warehouse timestamp; one value per run so
        # BI tools can label runs (a republish refreshes it).
        published_at=pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None),
    ).astype({"published_at": "datetime64[ns]"})
    return task.records_cls.from_df(df)


def publish_forecast_records(
    task: TaskSpec, records: ForecastRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's forecasts to ``task.forecast_table``.

    Creates the table's database and the table (parquet, partitioned by
    ``run_id``) if they do not exist, then overwrites only the partitions
    present in ``records`` — republishing a run replaces its rows without
    touching other runs.

    Parameters
    ----------
    task : TaskSpec
        Task being published; supplies the table and forecast column names.
    records : ForecastRecords
        Validated records for a single run.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.spark.get_spark_session`.

    Returns
    -------
    int
        Number of rows written.
    """
    spark = spark if spark is not None else get_spark_session()
    table = task.forecast_table
    database = table.split(".")[0]
    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    # Explicit DDL (rather than saveAsTable schema inference) so the table
    # schema is stable across writers; the partition column must come last to
    # line up with insertInto's positional semantics.
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
          strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          {task.forecast_col} double,
          published_at timestamp,
          run_id string
        )
        USING parquet
        PARTITIONED BY (run_id)
        """
    )
    sdf = spark.createDataFrame(records.df).select(
        F.col("strategy").cast("string"),
        F.col("area_code").cast("string"),
        F.col("forecast_issued_ts").cast("timestamp"),
        F.col("trade_date").cast("date"),
        F.col("time_code").cast("int"),
        F.col(task.forecast_col).cast("double"),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    # "dynamic" scopes the overwrite to the partitions being written; the
    # default ("static") would truncate every other run's partition too.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    sdf.write.mode("overwrite").insertInto(table)
    logger.info(
        "Published {} rows to {} (run_id={})", len(records), table, records.df["run_id"].iloc[0]
    )
    return len(records)
