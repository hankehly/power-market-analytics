"""Tests for the forecast write-back to ``pma_ml.spot_price_forecast``.

The publish tests write real parquet partitions into the session's temp Spark
warehouse. Run ids are unique to this module so the assertions filter on them
and never depend on what else the session published to the same table.
"""

from __future__ import annotations

import pandas as pd

from power_market_analytics.tasks.spot_price.frames import BacktestResult, ForecastRecords
from power_market_analytics.tasks.spot_price.publish import (
    FORECAST_TABLE,
    build_forecast_records,
    publish_forecast_records,
)


def make_result(days: list[str], time_codes: list[int], base: float = 10.0) -> BacktestResult:
    """A small BacktestResult; forecast = base + time_code / 10, actual = base."""
    return BacktestResult.from_df(
        pd.DataFrame(
            [
                {
                    "trade_date": pd.Timestamp(day),
                    "time_code": tc,
                    "actual_price_jpy_kwh": base,
                    "forecast_price_jpy_kwh": base + tc / 10,
                }
                for day in days
                for tc in time_codes
            ]
        )
    )


class TestBuildForecastRecords:
    def test_stamps_run_strategy_area_and_issue_time(self):
        records = build_forecast_records(
            make_result(["2024-04-10", "2024-04-11"], [1, 2, 48]),
            run_id="run-123",
            strategy="previous_day",
            area_code="tokyo",
        )
        assert isinstance(records, ForecastRecords)
        assert len(records) == 6
        assert list(records.df.columns) == [
            "run_id",
            "strategy",
            "area_code",
            "forecast_issued_ts",
            "trade_date",
            "time_code",
            "forecast_price_jpy_kwh",
            "published_at",
        ]
        assert "actual_price_jpy_kwh" not in records.df.columns
        assert records.df["run_id"].eq("run-123").all()
        assert records.df["strategy"].eq("previous_day").all()
        assert records.df["area_code"].eq("tokyo").all()
        # Issued at 9:55 JST on D-1.
        issued = records.df.set_index(["trade_date", "time_code"])["forecast_issued_ts"]
        assert issued.loc[(pd.Timestamp("2024-04-10"), 1)] == pd.Timestamp("2024-04-09 09:55")
        assert issued.loc[(pd.Timestamp("2024-04-11"), 48)] == pd.Timestamp("2024-04-10 09:55")
        assert records.df["forecast_issued_ts"].dtype == "datetime64[ns]"
        # Forecast values pass through untouched.
        assert records.df["forecast_price_jpy_kwh"].tolist() == [
            10.1,
            10.2,
            14.8,
            10.1,
            10.2,
            14.8,
        ]

    def test_published_at_is_one_naive_jst_instant_per_run(self):
        before = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)
        records = build_forecast_records(
            make_result(["2024-04-10"], [1, 2]), run_id="r", strategy="s", area_code="tokyo"
        )
        after = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)
        published_at = records.df["published_at"]
        assert published_at.dtype == "datetime64[ns]"
        assert published_at.dt.tz is None
        assert published_at.nunique() == 1
        # JST wall-clock, not UTC (which would be 9 h behind).
        assert before <= published_at.iloc[0] <= after


def published_rows(spark, run_id: str) -> pd.DataFrame:
    """Rows of ``FORECAST_TABLE`` for one run, timestamps rendered in the session tz."""
    return (
        spark.sql(
            f"""
            select
              strategy, area_code,
              date_format(forecast_issued_ts, 'yyyy-MM-dd HH:mm') as forecast_issued_ts,
              cast(trade_date as string) as trade_date,
              time_code, forecast_price_jpy_kwh,
              date_format(published_at, 'yyyy-MM-dd HH:mm:ss') as published_at,
              run_id
            from {FORECAST_TABLE}
            where run_id = '{run_id}'
            order by trade_date, time_code
            """
        )
        .toPandas()
        .reset_index(drop=True)
    )


class TestPublishForecastRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        records = build_forecast_records(
            make_result(["2024-04-10", "2024-04-11"], [1, 2]),
            run_id="pub-create",
            strategy="lightgbm",
            area_code="tokyo",
        )
        assert publish_forecast_records(records, spark=spark) == 4

        assert spark.catalog.tableExists(FORECAST_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(FORECAST_TABLE)}
        assert {name: c.dataType for name, c in columns.items()} == {
            "strategy": "string",
            "area_code": "string",
            "forecast_issued_ts": "timestamp",
            "trade_date": "date",
            "time_code": "int",
            "forecast_price_jpy_kwh": "double",
            "published_at": "timestamp",
            "run_id": "string",
        }
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]

        rows = published_rows(spark, "pub-create")
        assert rows[["trade_date", "time_code", "forecast_issued_ts"]].values.tolist() == [
            ["2024-04-10", 1, "2024-04-09 09:55"],
            ["2024-04-10", 2, "2024-04-09 09:55"],
            ["2024-04-11", 1, "2024-04-10 09:55"],
            ["2024-04-11", 2, "2024-04-10 09:55"],
        ]
        assert rows["forecast_price_jpy_kwh"].tolist() == [10.1, 10.2, 10.1, 10.2]
        assert rows["strategy"].eq("lightgbm").all()
        assert rows["area_code"].eq("tokyo").all()
        assert (
            rows["published_at"].tolist()
            == [records.df["published_at"].iloc[0].strftime("%Y-%m-%d %H:%M:%S")] * 4
        )

    def test_republishing_a_run_replaces_only_that_runs_partition(self, spark):
        keep = build_forecast_records(
            make_result(["2024-04-10"], [1, 2], base=20.0),
            run_id="pub-keep",
            strategy="previous_day",
            area_code="tokyo",
        )
        first = build_forecast_records(
            make_result(["2024-04-10", "2024-04-11"], [1, 2], base=10.0),
            run_id="pub-replace",
            strategy="lightgbm",
            area_code="tokyo",
        )
        publish_forecast_records(keep, spark=spark)
        assert publish_forecast_records(first, spark=spark) == 4

        # Same run id, different window and values: the old four rows must go.
        second = build_forecast_records(
            make_result(["2024-04-12"], [1], base=30.0),
            run_id="pub-replace",
            strategy="lightgbm",
            area_code="tokyo",
        )
        assert publish_forecast_records(second, spark=spark) == 1

        replaced = published_rows(spark, "pub-replace")
        assert replaced[["trade_date", "time_code", "forecast_price_jpy_kwh"]].values.tolist() == [
            ["2024-04-12", 1, 30.1]
        ]
        kept = published_rows(spark, "pub-keep")
        assert kept[["trade_date", "time_code", "forecast_price_jpy_kwh"]].values.tolist() == [
            ["2024-04-10", 1, 20.1],
            ["2024-04-10", 2, 20.2],
        ]

    def test_defaults_to_the_active_spark_session(self, spark):
        records = build_forecast_records(
            make_result(["2024-04-10"], [7]),
            run_id="pub-default-session",
            strategy="s",
            area_code="kansai",
        )
        assert publish_forecast_records(records) == 1
        rows = published_rows(spark, "pub-default-session")
        assert rows[
            ["area_code", "trade_date", "time_code", "forecast_price_jpy_kwh"]
        ].values.tolist() == [["kansai", "2024-04-10", 7, 10.7]]
