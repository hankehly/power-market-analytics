"""Spark session factory for the devcontainer environment."""

from __future__ import annotations

import logging

from pydantic_settings import BaseSettings, SettingsConfigDict
from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


class SparkSettings(BaseSettings):
    """Per-JVM memory defaults for the devcontainer Python session.

    Override via env vars or ``.env``. The thriftserver uses its own values
    via docker-compose ``--conf`` flags.
    """

    model_config = SettingsConfigDict(
        env_prefix="", case_sensitive=True, env_file=".env", extra="ignore"
    )

    SPARK_CONF_DIR: str = ""
    SPARK_DRIVER_MEMORY: str = ""
    SPARK_DRIVER_MAX_RESULT_SIZE: str = ""


def get_spark_session(
    app_name: str = "PMA",
    extra_configs: dict[str, str] | None = None,
) -> SparkSession:
    """Return a SparkSession sharing the thriftserver's Hive metastore.

    Reads ``conf/spark`` settings via ``SPARK_CONF_DIR`` so tables written
    here land in the same metastore/warehouse the thriftserver (and dbt)
    serve. ``spark.driver.memory`` must be set before the JVM starts —
    restart the kernel to apply a new value if a session already exists.

    Parameters
    ----------
    app_name : str, default "PMA"
        Spark application name.
    extra_configs : dict of str to str, optional
        Additional ``builder.config`` key/value pairs, applied last.

    Returns
    -------
    pyspark.sql.SparkSession
    """
    spark_settings = SparkSettings()

    if not spark_settings.SPARK_CONF_DIR:
        logger.warning(
            "SPARK_CONF_DIR is not set; Python SparkSession will not inherit "
            "thriftserver tuning from conf/spark/spark-defaults.conf"
        )

    builder = SparkSession.builder.appName(app_name)

    # Per-JVM overrides that must differ from the thriftserver (which gets
    # its values from spark-defaults.conf / docker-compose --conf flags).
    per_jvm_overrides = {
        "spark.master": "local[*]",
        "spark.driver.memory": spark_settings.SPARK_DRIVER_MEMORY,
        "spark.driver.maxResultSize": spark_settings.SPARK_DRIVER_MAX_RESULT_SIZE,
        "spark.ui.port": "4041",  # avoid collision with thriftserver on 4040
    }
    for key, value in per_jvm_overrides.items():
        if value:
            builder = builder.config(key, value)

    # Arrow config for toPandas()/createDataFrame() — not relevant to thriftserver.
    pyspark_only = {
        "spark.sql.execution.arrow.pyspark.enabled": "true",
        "spark.sql.execution.arrow.pyspark.fallback.enabled": "true",
        "spark.sql.execution.arrow.maxRecordsPerBatch": "10000",
    }
    for key, value in pyspark_only.items():
        builder = builder.config(key, value)

    if extra_configs:
        for key, value in extra_configs.items():
            builder = builder.config(key, value)

    return builder.enableHiveSupport().getOrCreate()
