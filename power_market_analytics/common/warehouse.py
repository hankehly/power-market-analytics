"""Read data from the Spark warehouse into pandas."""

from __future__ import annotations

import logging

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.spark import get_spark_session

logger = logging.getLogger(__name__)


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
        "query_pandas: shape=%s, schema: %s",
        pdf.shape,
        ", ".join(f"{c}:{t}" for c, t in pdf.dtypes.astype(str).items()),
    )
    return pdf
