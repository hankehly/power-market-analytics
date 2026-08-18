"""Tests for the SparkSession factory (``power_market_analytics.spark``)."""

from __future__ import annotations

import pytest
from loguru import logger

from power_market_analytics.spark import SparkSettings, get_spark_session, spark_session_builder

#: The Arrow settings every builder gets, independent of the environment.
PYSPARK_ONLY = {
    "spark.sql.execution.arrow.pyspark.enabled": "true",
    "spark.sql.execution.arrow.pyspark.fallback.enabled": "true",
    "spark.sql.execution.arrow.maxRecordsPerBatch": "10000",
}


class TestSparkSettings:
    def test_reads_uppercase_env_vars_verbatim(self, monkeypatch):
        monkeypatch.setenv("SPARK_CONF_DIR", "/etc/spark")
        monkeypatch.setenv("SPARK_DRIVER_MEMORY", "3g")
        monkeypatch.setenv("SPARK_DRIVER_MAX_RESULT_SIZE", "2g")
        settings = SparkSettings()
        assert (
            settings.SPARK_CONF_DIR,
            settings.SPARK_DRIVER_MEMORY,
            settings.SPARK_DRIVER_MAX_RESULT_SIZE,
        ) == ("/etc/spark", "3g", "2g")

    def test_is_case_sensitive(self, monkeypatch):
        # A lower-case variable must NOT populate the upper-case field.
        monkeypatch.delenv("SPARK_CONF_DIR", raising=False)
        monkeypatch.setenv("spark_conf_dir", "/lower")
        assert SparkSettings(_env_file=None).SPARK_CONF_DIR == ""

    def test_extra_fields_are_ignored(self):
        settings = SparkSettings(_env_file=None, SOMETHING_ELSE="x")  # type: ignore[call-arg]
        assert not hasattr(settings, "SOMETHING_ELSE")


class TestSparkSessionBuilder:
    def test_full_settings_produce_exact_config(self):
        settings = SparkSettings(
            _env_file=None,
            SPARK_CONF_DIR="/conf/spark",
            SPARK_DRIVER_MEMORY="4g",
            SPARK_DRIVER_MAX_RESULT_SIZE="2g",
        )
        builder = spark_session_builder(settings=settings)
        assert builder._options == {
            "spark.app.name": "PMA",
            "spark.master": "local[*]",
            "spark.driver.memory": "4g",
            "spark.driver.maxResultSize": "2g",
            "spark.ui.port": "4041",
            **PYSPARK_ONLY,
        }

    def test_empty_memory_values_are_not_set(self):
        # An empty string would make the JVM reject the config at start-up.
        settings = SparkSettings(
            _env_file=None,
            SPARK_CONF_DIR="/conf/spark",
            SPARK_DRIVER_MEMORY="",
            SPARK_DRIVER_MAX_RESULT_SIZE="",
        )
        builder = spark_session_builder(app_name="other", settings=settings)
        assert builder._options == {
            "spark.app.name": "other",
            "spark.master": "local[*]",
            "spark.ui.port": "4041",
            **PYSPARK_ONLY,
        }

    def test_extra_configs_are_applied_last_and_override(self):
        settings = SparkSettings(_env_file=None, SPARK_CONF_DIR="/c", SPARK_DRIVER_MEMORY="4g")
        builder = spark_session_builder(
            extra_configs={
                "spark.driver.memory": "9g",
                "spark.sql.execution.arrow.pyspark.enabled": "false",
                "spark.custom.flag": "on",
            },
            settings=settings,
        )
        assert builder._options["spark.driver.memory"] == "9g"
        assert builder._options["spark.sql.execution.arrow.pyspark.enabled"] == "false"
        assert builder._options["spark.custom.flag"] == "on"
        # Everything else is untouched.
        assert builder._options["spark.master"] == "local[*]"
        assert builder._options["spark.sql.execution.arrow.maxRecordsPerBatch"] == "10000"

    def test_missing_conf_dir_warns(self):
        messages: list[str] = []
        sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            spark_session_builder(settings=SparkSettings(_env_file=None, SPARK_CONF_DIR=""))
            spark_session_builder(settings=SparkSettings(_env_file=None, SPARK_CONF_DIR="/x"))
        finally:
            logger.remove(sink_id)
        assert len(messages) == 1
        assert "SPARK_CONF_DIR is not set" in messages[0]

    def test_settings_default_reads_environment(self, monkeypatch):
        monkeypatch.setenv("SPARK_CONF_DIR", "/conf/spark")
        monkeypatch.setenv("SPARK_DRIVER_MEMORY", "7g")
        monkeypatch.setenv("SPARK_DRIVER_MAX_RESULT_SIZE", "5g")
        builder = spark_session_builder()
        assert builder._options["spark.driver.memory"] == "7g"
        assert builder._options["spark.driver.maxResultSize"] == "5g"
        assert builder._options["spark.app.name"] == "PMA"


class TestGetSparkSession:
    def test_reuses_the_active_session(self, spark):
        # PySpark's getOrCreate returns the active session (only warning about
        # static confs), so no second JVM is started and callers with
        # ``spark=None`` share the test session.
        assert get_spark_session() is spark


@pytest.mark.parametrize("app_name", ["PMA", "backtest"])
def test_app_name_flows_into_options(app_name):
    settings = SparkSettings(_env_file=None, SPARK_CONF_DIR="/c")
    assert spark_session_builder(app_name, settings=settings)._options["spark.app.name"] == app_name
