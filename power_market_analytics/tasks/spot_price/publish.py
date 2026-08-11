"""Write backtest forecasts back to the Spark warehouse.

MLflow stays the system of record for the experiment (params, metrics,
artifacts); the warehouse holds the row-level forecasts so dbt can join them
to actuals and dimensions and Superset can chart them. ``run_id`` is the link
between the two systems.

The destination table is partitioned by ``run_id`` and written with dynamic
partition overwrite, so republishing a run replaces exactly that run's rows.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.spark import get_spark_session
from power_market_analytics.tasks.spot_price.frames import BacktestResult, ForecastRecords

FORECAST_TABLE = "pma_ml.spot_price_forecast"

# Forecasts for delivery day D are issued at 9:55 JST on D-1 (the task
# definition in tasks/spot_price/__init__.py): D 00:00 - 24h + 9h55m.
_ISSUE_OFFSET = pd.Timedelta(days=-1, hours=9, minutes=55)


def build_forecast_records(
    result: BacktestResult, *, run_id: str, strategy: str, area_code: str
) -> ForecastRecords:
    """Shape a backtest result into warehouse write-back records.

    Parameters
    ----------
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
    """
    df = result.df.assign(
        run_id=run_id,
        strategy=strategy,
        area_code=area_code,
        forecast_issued_ts=lambda d: d["trade_date"] + _ISSUE_OFFSET,
        # Naive JST like every other warehouse timestamp; one value per run so
        # BI tools can label runs (a republish refreshes it).
        published_at=pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None),
    ).astype({"published_at": "datetime64[ns]"})
    return ForecastRecords.from_df(df)


def publish_forecast_records(records: ForecastRecords, spark: SparkSession | None = None) -> int:
    """Idempotently write one run's forecasts to ``FORECAST_TABLE``.

    Creates the ``pma_ml`` database and the table (parquet, partitioned by
    ``run_id``) if they do not exist, then overwrites only the partitions
    present in ``records`` — republishing a run replaces its rows without
    touching other runs.

    Parameters
    ----------
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
    database = FORECAST_TABLE.split(".")[0]
    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    # Explicit DDL (rather than saveAsTable schema inference) so the table
    # schema is stable across writers; the partition column must come last to
    # line up with insertInto's positional semantics.
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {FORECAST_TABLE} (
          strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          forecast_price_jpy_kwh double,
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
        F.col("forecast_price_jpy_kwh").cast("double"),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    # "dynamic" scopes the overwrite to the partitions being written; the
    # default ("static") would truncate every other run's partition too.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    sdf.write.mode("overwrite").insertInto(FORECAST_TABLE)
    logger.info(
        "Published {} rows to {} (run_id={})",
        len(records),
        FORECAST_TABLE,
        records.df["run_id"].iloc[0],
    )
    return len(records)
