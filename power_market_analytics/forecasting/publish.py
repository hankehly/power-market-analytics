"""Write backtest forecasts back to the Spark warehouse.

MLflow stays the system of record for the experiment (params, metrics,
artifacts); the warehouse holds the row-level forecasts so dbt can join them
to actuals and dimensions and Superset can chart them. ``run_id`` is the link
between the two systems.

Each task has two destination tables — the forecasts (``TaskSpec.forecast_table``)
and, for strategies that explain themselves, their per-component contributions
(``TaskSpec.contribution_table``) — both partitioned by ``run_id`` and written
with dynamic partition overwrite, so republishing a run replaces exactly that
run's rows.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from power_market_analytics.forecasting.frames import (
    GRAIN_COLS,
    BacktestResult,
    ForecastContributionRecords,
    ForecastContributions,
    ForecastRecords,
)
from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.spark import get_spark_session


def _create_run_partitioned_table(spark: SparkSession, table: str, columns_ddl: str) -> None:
    """Create ``table`` (parquet, partitioned by ``run_id``) and its database if absent.

    Explicit DDL rather than ``saveAsTable`` schema inference, so the table
    schema is stable across writers; the partition column comes last to line
    up with ``insertInto``'s positional semantics.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession
    table : str
        Fully qualified ``database.table``.
    columns_ddl : str
        Comma-separated ``name type`` list of the non-partition columns.
    """
    database = table.split(".")[0]
    spark.sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
          {columns_ddl},
          run_id string
        )
        USING parquet
        PARTITIONED BY (run_id)
        """
    )


def _overwrite_run_partitions(spark: SparkSession, table: str, sdf: DataFrame) -> None:
    """Insert ``sdf`` into ``table``, replacing only the partitions it carries.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession
    table : str
    sdf : pyspark.sql.DataFrame
        Columns in the table's positional order, ``run_id`` last.
    """
    # "dynamic" scopes the overwrite to the partitions being written; the
    # default ("static") would truncate every other run's partition too.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    sdf.write.mode("overwrite").insertInto(table)


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
    _create_run_partitioned_table(
        spark,
        table,
        f"""strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          {task.forecast_col} double,
          published_at timestamp""",
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
    _overwrite_run_partitions(spark, table, sdf)
    logger.info(
        "Published {} rows to {} (run_id={})", len(records), table, records.df["run_id"].iloc[0]
    )
    return len(records)


def build_contribution_records(
    task: TaskSpec,
    contributions: ForecastContributions,
    result: BacktestResult,
    *,
    run_id: str,
    strategy: str,
    area_code: str,
    published_at: pd.Timestamp,
) -> ForecastContributionRecords:
    """Shape a strategy's contributions into warehouse write-back records.

    Keeps the periods of ``result`` only: the backtest drops forecast points
    without an actual and the forecast table holds just the remaining rows,
    so the contribution table stays congruent with it (one forecast row per
    explained period, joinable 1:1). Stamps the run identity like
    :func:`build_forecast_records`.

    Parameters
    ----------
    task : TaskSpec
        Task the contributions belong to; supplies the issue offset.
    contributions : ForecastContributions
        Every period the strategy predicted (``strategy.contributions()``).
    result : BacktestResult
        The backtest result being published; fixes the periods to keep.
    run_id, strategy, area_code : str
        As for :func:`build_forecast_records`.
    published_at : pandas.Timestamp
        The instant stamped on the run's forecast records (naive JST), reused
        so both tables label the run identically.

    Returns
    -------
    ForecastContributionRecords

    Raises
    ------
    ValueError
        If a period of ``result`` has no contributions.
    """
    aligned = result.df[GRAIN_COLS].merge(
        contributions.df, how="left", on=GRAIN_COLS, validate="one_to_many", indicator=True
    )
    missing = aligned.loc[aligned["_merge"] == "left_only", GRAIN_COLS]
    if not missing.empty:
        first = missing.iloc[0]
        raise ValueError(
            f"{task.name}: {len(missing)} scored period(s) have no contributions, e.g. "
            f"{first['trade_date'].date()} time_code {first['time_code']}"
        )
    df = (
        aligned.drop(columns="_merge")
        .assign(
            run_id=run_id,
            strategy=strategy,
            area_code=area_code,
            forecast_issued_ts=lambda d: d["trade_date"] + task.issue_offset,
            published_at=pd.Timestamp(published_at),
        )
        .astype({"published_at": "datetime64[ns]"})
    )
    return ForecastContributionRecords.from_df(df)


def publish_contribution_records(
    task: TaskSpec, records: ForecastContributionRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's forecast contributions to ``task.contribution_table``.

    Same mechanics as :func:`publish_forecast_records`: the table (parquet,
    partitioned by ``run_id``) is created on first use and only the run's
    partition is overwritten. The generic ``contribution`` column is written
    as the task's unit-suffixed ``contribution_col``; a NaN feature value (the
    base rows) is written as a SQL null.

    Parameters
    ----------
    task : TaskSpec
    records : ForecastContributionRecords
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
    table = task.contribution_table
    _create_run_partitioned_table(
        spark,
        table,
        f"""strategy string,
          area_code string,
          forecast_issued_ts timestamp,
          trade_date date,
          time_code int,
          component string,
          component_order int,
          feature_value double,
          {task.contribution_col} double,
          published_at timestamp""",
    )
    feature_value = F.col("feature_value").cast("double")
    sdf = spark.createDataFrame(records.df).select(
        F.col("strategy").cast("string"),
        F.col("area_code").cast("string"),
        F.col("forecast_issued_ts").cast("timestamp"),
        F.col("trade_date").cast("date"),
        F.col("time_code").cast("int"),
        F.col("component").cast("string"),
        F.col("component_order").cast("int"),
        # pandas NaN arrives as a double NaN, which is not SQL null.
        F.when(F.isnan(feature_value), F.lit(None)).otherwise(feature_value).alias("feature_value"),
        F.col("contribution").cast("double").alias(task.contribution_col),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    _overwrite_run_partitions(spark, table, sdf)
    logger.info(
        "Published {} contribution rows to {} (run_id={})",
        len(records),
        table,
        records.df["run_id"].iloc[0],
    )
    return len(records)
