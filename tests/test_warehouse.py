"""Tests for ``power_market_analytics.common.warehouse.query_pandas``."""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from power_market_analytics.common.warehouse import query_pandas


@pytest.fixture(scope="module")
def warehouse_table(spark) -> str:
    """A tiny ``test_warehouse.prices`` table with mixed column types."""
    spark.sql("CREATE DATABASE IF NOT EXISTS test_warehouse")
    spark.createDataFrame(
        [
            (datetime.date(2024, 1, 5), 1, "tokyo", 12.5),
            (datetime.date(2024, 1, 5), 2, "tokyo", 13.0),
            (datetime.date(2024, 1, 5), 1, "kansai", 11.25),
        ],
        "trade_date date, time_code int, area string, price double",
    ).write.mode("overwrite").saveAsTable("test_warehouse.prices")
    return "test_warehouse.prices"


class TestQueryPandas:
    def test_returns_query_result_with_pandas_dtypes(self, spark, warehouse_table):
        pdf = query_pandas(
            f"select time_code, area, price from {warehouse_table} "
            "where area = 'tokyo' order by time_code",
            spark=spark,
        )
        assert isinstance(pdf, pd.DataFrame)
        # Spark ``int`` is 32-bit, so it comes back as int32 (not pandas' default int64).
        expected = pd.DataFrame(
            {"time_code": [1, 2], "area": ["tokyo", "tokyo"], "price": [12.5, 13.0]}
        ).astype({"time_code": "int32"})
        pd.testing.assert_frame_equal(pdf, expected)
        assert pdf.dtypes.astype(str).to_dict() == {
            "time_code": "int32",
            "area": "object",
            "price": "float64",
        }

    def test_aggregation_and_empty_result(self, spark, warehouse_table):
        pdf = query_pandas(
            f"select area, count(*) as n, sum(price) as total from {warehouse_table} "
            "group by area order by area",
            spark=spark,
        )
        assert pdf.to_dict("records") == [
            {"area": "kansai", "n": 1, "total": 11.25},
            {"area": "tokyo", "n": 2, "total": 25.5},
        ]
        empty = query_pandas(f"select * from {warehouse_table} where 1 = 0", spark=spark)
        assert empty.shape == (0, 4)
        assert list(empty.columns) == ["trade_date", "time_code", "area", "price"]

    def test_default_session_is_the_active_one(self, spark, warehouse_table):
        pdf = query_pandas(f"select count(*) as n from {warehouse_table}")
        assert pdf.to_dict("records") == [{"n": 3}]

    def test_bad_sql_propagates(self, spark):
        with pytest.raises(Exception, match="no_such_table"):
            query_pandas("select * from test_warehouse.no_such_table", spark=spark)
