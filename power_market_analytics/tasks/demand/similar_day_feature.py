"""The similar-day feature values, written back by the fit-and-score job.

``scripts/fit_similar_day.py`` fits the weights of ``tasks/demand/similar_day.py``,
selects the similar day of every scorable delivery day and writes the feature
— the chosen day's hourly load halved per period — to ``pma_ml.similar_day``,
partitioned by the job's MLflow run like the forecast tables. The
``ftr_period_similar_day`` mart passes the rows through to Feast; a re-score is
a new run whose rows win by ``published_at``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import N_PERIODS
from power_market_analytics.forecasting.publish import (
    create_run_partitioned_table,
    overwrite_run_partitions,
)
from power_market_analytics.spark import get_spark_session
from power_market_analytics.tasks.demand.frames import AreaHourlyLoad, AreaWeatherForecast
from power_market_analytics.tasks.demand.similar_day import PERIODS_PER_HOUR, SimilarDaySelection

#: The MLflow experiment of the fit-and-score runs, named after the feature.
MLFLOW_EXPERIMENT = "similar_day"
#: Where the feature values are written; the ``run_id`` partition is the job's MLflow run.
FEATURE_TABLE = "pma_ml.similar_day"
_DATE = "datetime64[ns]"


class SimilarDayFeatureRecords(DomainFrame):
    """One scoring run's similar-day feature, shaped for ``pma_ml.similar_day``.

    Per delivery period: the chosen day's hourly load over the period's hour
    halved (``similar_day_demand_kwh``), the chosen day, its lag in days, its
    distance and the candidate count, and ``available_at``: when the row's
    newest input, the day's forecast vintage, became public. The candidates
    are at least 334 days older.

    Grain: (area_code, trade_date, time_code); one run per frame.
    """

    schema = {
        "area_code": "object",
        "trade_date": _DATE,
        "time_code": "int64",
        "similar_day_demand_kwh": "float64",
        "similar_day_reference_date": _DATE,
        "similar_day_reference_lag_days": "int64",
        "similar_day_distance": "float64",
        "similar_day_n_candidates": "int64",
        "available_at": _DATE,
        "published_at": _DATE,
        "run_id": "object",
    }
    keys = ["area_code", "trade_date", "time_code"]
    non_null_cols = [
        col for col in schema if col not in ("area_code", "trade_date", "time_code")
    ]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        name = cls.__name__
        if not df["time_code"].between(1, N_PERIODS).all():
            raise ValueError(f"{name}: time_code outside 1..{N_PERIODS}")
        if (df["similar_day_reference_date"] >= df["trade_date"]).any():
            raise ValueError(f"{name}: similar_day_reference_date must precede trade_date")
        lag = (df["trade_date"] - df["similar_day_reference_date"]).dt.days
        if (lag != df["similar_day_reference_lag_days"]).any():
            raise ValueError(f"{name}: similar_day_reference_lag_days must equal the date gap")
        if (df["similar_day_demand_kwh"] <= 0).any():
            raise ValueError(f"{name}: similar_day_demand_kwh must be positive")
        if df["run_id"].nunique() != 1:
            raise ValueError(f"{name}: one run per frame, got {df['run_id'].nunique()}")


def build_feature_records(
    selection: SimilarDaySelection,
    hourly_load: AreaHourlyLoad,
    forecast: AreaWeatherForecast,
    *,
    run_id: str,
    area_code: str,
    published_at: pd.Timestamp,
) -> SimilarDayFeatureRecords:
    """Shape a selection into the feature rows of every period of its days.

    Parameters
    ----------
    selection : SimilarDaySelection
        The similar day of every scorable delivery day.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load the chosen days' loads come from.
    forecast : AreaWeatherForecast
        The forecast profiles the days were scored with; their ``available_at``
        is the rows'.
    run_id : str
        The job's MLflow run id.
    area_code : str
        dim_area.area_code value the feature was scored for.
    published_at : pandas.Timestamp
        When the rows are written (naive JST).

    Returns
    -------
    SimilarDayFeatureRecords
        48 rows per selected day, sorted by day and period.

    Raises
    ------
    ValueError
        If the selection is empty, a chosen day lacks an hourly load, or a
        day has no forecast availability.
    """
    if len(selection) == 0:
        raise ValueError("no scorable day to publish")
    days = selection.df.merge(
        pd.DataFrame({"time_code": np.arange(1, N_PERIODS + 1, dtype="int64")}), how="cross"
    )
    days["hour_ending"] = (days["time_code"] + 1) // 2
    load = hourly_load.df.rename(columns={"load_date": "reference_date"})
    rows = days.merge(
        load, how="left", on=["reference_date", "hour_ending"], validate="many_to_one"
    )
    missing = rows["demand_kwh"].isna()
    if missing.any():
        first = rows.loc[missing].iloc[0]
        raise ValueError(
            f"{int(missing.sum())} period(s) have no load on their similar day, e.g. "
            f"{first['trade_date'].date()} time_code {first['time_code']} "
            f"(similar day {first['reference_date'].date()})"
        )
    availability = forecast.df.groupby("trade_date")["available_at"].max()
    rows["available_at"] = rows["trade_date"].map(availability)
    if rows["available_at"].isna().any():
        unknown = sorted(
            d.date() for d in rows.loc[rows["available_at"].isna(), "trade_date"].unique()
        )
        raise ValueError(f"{len(unknown)} day(s) have no forecast availability, e.g. {unknown[0]}")
    df = (
        pd.DataFrame(
            {
                "area_code": area_code,
                "trade_date": rows["trade_date"],
                "time_code": rows["time_code"],
                "similar_day_demand_kwh": rows["demand_kwh"].to_numpy(dtype="float64")
                / PERIODS_PER_HOUR,
                "similar_day_reference_date": rows["reference_date"],
                "similar_day_reference_lag_days": rows["reference_lag_days"],
                "similar_day_distance": rows["distance"],
                "similar_day_n_candidates": rows["n_candidates"],
                "available_at": rows["available_at"],
                "published_at": pd.Timestamp(published_at),
                "run_id": run_id,
            }
        )
        .astype({"available_at": _DATE, "published_at": _DATE})
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )
    return SimilarDayFeatureRecords.from_df(df)


def publish_feature_records(
    records: SimilarDayFeatureRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's feature rows to ``FEATURE_TABLE``.

    The table (parquet, partitioned by ``run_id``) is created on first use and
    only the run's partition is overwritten, as for the forecast tables.

    Parameters
    ----------
    records : SimilarDayFeatureRecords
        The run's rows.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.spark.get_spark_session`.

    Returns
    -------
    int
        Number of rows written.
    """
    spark = spark if spark is not None else get_spark_session()
    create_run_partitioned_table(
        spark,
        FEATURE_TABLE,
        """area_code string,
          trade_date date,
          time_code int,
          similar_day_demand_kwh double,
          similar_day_reference_date date,
          similar_day_reference_lag_days int,
          similar_day_distance double,
          similar_day_n_candidates int,
          available_at timestamp,
          published_at timestamp""",
    )
    sdf = spark.createDataFrame(records.df).select(
        F.col("area_code").cast("string"),
        F.col("trade_date").cast("date"),
        F.col("time_code").cast("int"),
        F.col("similar_day_demand_kwh").cast("double"),
        F.col("similar_day_reference_date").cast("date"),
        F.col("similar_day_reference_lag_days").cast("int"),
        F.col("similar_day_distance").cast("double"),
        F.col("similar_day_n_candidates").cast("int"),
        F.col("available_at").cast("timestamp"),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    overwrite_run_partitions(spark, FEATURE_TABLE, sdf)
    logger.info(
        "Published {} rows to {} (run_id={})",
        len(records),
        FEATURE_TABLE,
        records.df["run_id"].iloc[0],
    )
    return len(records)
