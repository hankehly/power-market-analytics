"""Shared pytest fixtures.

The Spark fixture is a plain local session (no Hive metastore) with its
warehouse and catalog rooted in a temporary directory, so loader tests can
``saveAsTable`` without touching the devcontainer warehouse.
"""

from __future__ import annotations

import pytest
from pyspark.sql import SparkSession


@pytest.fixture(scope="session")
def spark(tmp_path_factory: pytest.TempPathFactory) -> SparkSession:
    """Session-scoped local SparkSession writing to a temp warehouse."""
    warehouse = tmp_path_factory.mktemp("warehouse")
    session = (
        SparkSession.builder.master("local[1]")
        .appName("pma-tests")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.sql.session.timeZone", "Asia/Tokyo")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    yield session
    session.stop()
