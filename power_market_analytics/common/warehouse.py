"""Read data from the Spark warehouse into pandas."""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.spark import get_spark_session


def query_pandas(sql: str, spark: SparkSession | None = None) -> pd.DataFrame:
    """Run a Spark SQL query and return the result as pandas.

    Parameters
    ----------
    sql : str
        Spark SQL statement.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.spark.get_spark_session`.

    Returns
    -------
    pandas.DataFrame
    """
    spark = spark if spark is not None else get_spark_session()
    pdf = spark.sql(sql).toPandas()
    logger.info(
        "query_pandas: shape={}, schema: {}",
        pdf.shape,
        ", ".join(f"{c}:{t}" for c, t in pdf.dtypes.astype(str).items()),
    )
    return pdf
