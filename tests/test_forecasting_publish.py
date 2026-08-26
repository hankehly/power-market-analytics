"""Tests for the forecast write-back, exercised through the spot task (``pma_ml.spot_price_forecast``).

The publish tests write real parquet partitions into the session's temp Spark
warehouse. Run ids are unique to this module so the assertions filter on them
and never depend on what else the session published to the same table.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.frames import (
    BASE_COMPONENT,
    ForecastContributionRecords,
    ForecastContributions,
)
from power_market_analytics.forecasting.publish import (
    build_contribution_records,
    build_forecast_records,
    publish_contribution_records,
    publish_forecast_records,
)
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecastRecords,
)

FORECAST_TABLE = TASK.forecast_table


def make_result(
    days: list[str], time_codes: list[int], base: float = 10.0
) -> SpotPriceBacktestResult:
    """A small BacktestResult; forecast = base + time_code / 10, actual = base."""
    return SpotPriceBacktestResult.from_df(
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
            TASK,
            make_result(["2024-04-10", "2024-04-11"], [1, 2, 48]),
            run_id="run-123",
            strategy="previous_day",
            area_code="tokyo",
        )
        assert isinstance(records, SpotPriceForecastRecords)
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
            TASK, make_result(["2024-04-10"], [1, 2]), run_id="r", strategy="s", area_code="tokyo"
        )
        after = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)
        published_at = records.df["published_at"]
        assert published_at.dtype == "datetime64[ns]"
        assert published_at.dt.tz is None
        assert published_at.nunique() == 1
        # JST wall-clock, not UTC (which would be 9 h behind).
        assert before <= published_at.iloc[0] <= after

    def test_issue_time_comes_from_the_task_spec(self):
        # A task issuing at 09:30 two days ahead stamps that instead of spot's 09:55.
        other = dataclasses.replace(TASK, issue_offset=pd.Timedelta(days=-2, hours=9, minutes=30))
        records = build_forecast_records(
            other, make_result(["2024-04-10"], [1]), run_id="r", strategy="s", area_code="tokyo"
        )
        assert records.df["forecast_issued_ts"].iloc[0] == pd.Timestamp("2024-04-08 09:30")


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
            TASK,
            make_result(["2024-04-10", "2024-04-11"], [1, 2]),
            run_id="pub-create",
            strategy="lightgbm",
            area_code="tokyo",
        )
        assert publish_forecast_records(TASK, records, spark=spark) == 4

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
            TASK,
            make_result(["2024-04-10"], [1, 2], base=20.0),
            run_id="pub-keep",
            strategy="previous_day",
            area_code="tokyo",
        )
        first = build_forecast_records(
            TASK,
            make_result(["2024-04-10", "2024-04-11"], [1, 2], base=10.0),
            run_id="pub-replace",
            strategy="lightgbm",
            area_code="tokyo",
        )
        publish_forecast_records(TASK, keep, spark=spark)
        assert publish_forecast_records(TASK, first, spark=spark) == 4

        # Same run id, different window and values: the old four rows must go.
        second = build_forecast_records(
            TASK,
            make_result(["2024-04-12"], [1], base=30.0),
            run_id="pub-replace",
            strategy="lightgbm",
            area_code="tokyo",
        )
        assert publish_forecast_records(TASK, second, spark=spark) == 1

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
            TASK,
            make_result(["2024-04-10"], [7]),
            run_id="pub-default-session",
            strategy="s",
            area_code="kansai",
        )
        assert publish_forecast_records(TASK, records) == 1
        rows = published_rows(spark, "pub-default-session")
        assert rows[
            ["area_code", "trade_date", "time_code", "forecast_price_jpy_kwh"]
        ].values.tolist() == [["kansai", "2024-04-10", 7, 10.7]]


CONTRIBUTION_TABLE = TASK.contribution_table  # pma_ml.spot_price_forecast_contribution
PUBLISHED_AT = pd.Timestamp("2026-08-26 10:00:00")


def make_contributions(
    days: list[str], time_codes: list[int], base: float = 10.0
) -> ForecastContributions:
    """Contributions matching ``make_result``: the base plus one feature contributing tc / 10."""
    rows = []
    for day in days:
        for tc in time_codes:
            rows.append(
                {
                    "trade_date": pd.Timestamp(day),
                    "time_code": tc,
                    "component": BASE_COMPONENT,
                    "component_order": 0,
                    "feature_value": np.nan,
                    "contribution": base,
                }
            )
            rows.append(
                {
                    "trade_date": pd.Timestamp(day),
                    "time_code": tc,
                    "component": "time_code",
                    "component_order": 1,
                    "feature_value": float(tc),
                    "contribution": tc / 10,
                }
            )
    return ForecastContributions.from_df(
        pd.DataFrame(rows).astype({"time_code": "int64", "component_order": "int64"})
    )


class TestBuildContributionRecords:
    def test_keeps_the_scored_periods_and_stamps_the_run(self):
        result = make_result(["2024-04-10"], [1, 2])
        # More predicted periods than scored ones: only the scored survive.
        contributions = make_contributions(["2024-04-10", "2024-04-11"], [1, 2, 3])

        records = build_contribution_records(
            TASK,
            contributions,
            result,
            run_id="run-123",
            strategy="lightgbm",
            area_code="tokyo",
            published_at=PUBLISHED_AT,
        )

        assert isinstance(records, ForecastContributionRecords)
        assert list(records.df.columns) == list(ForecastContributionRecords.schema)
        assert len(records) == 4  # 2 periods x (base + 1 feature)
        assert set(zip(records.df["trade_date"], records.df["time_code"])) == {
            (pd.Timestamp("2024-04-10"), 1),
            (pd.Timestamp("2024-04-10"), 2),
        }
        assert records.df["run_id"].eq("run-123").all()
        assert records.df["strategy"].eq("lightgbm").all()
        assert records.df["area_code"].eq("tokyo").all()
        assert records.df["forecast_issued_ts"].eq(pd.Timestamp("2024-04-09 09:55")).all()
        assert records.df["published_at"].eq(PUBLISHED_AT).all()
        assert records.df["published_at"].dtype == "datetime64[ns]"
        sums = records.df.groupby("time_code")["contribution"].sum()
        assert sums.to_dict() == pytest.approx({1: 10.1, 2: 10.2})

    def test_a_scored_period_without_contributions_is_an_error(self):
        result = make_result(["2024-04-10"], [1, 2, 48])
        contributions = make_contributions(["2024-04-10"], [1, 2])
        with pytest.raises(
            ValueError,
            match=r"spot_price: 1 scored period\(s\) have no contributions, "
            r"e\.g\. 2024-04-10 time_code 48",
        ):
            build_contribution_records(
                TASK,
                contributions,
                result,
                run_id="r",
                strategy="s",
                area_code="tokyo",
                published_at=PUBLISHED_AT,
            )


def published_contribution_rows(spark, run_id: str) -> pd.DataFrame:
    """Rows of ``CONTRIBUTION_TABLE`` for one run, timestamps rendered in the session tz."""
    return (
        spark.sql(
            f"""
            select
              strategy, area_code,
              date_format(forecast_issued_ts, 'yyyy-MM-dd HH:mm') as forecast_issued_ts,
              cast(trade_date as string) as trade_date,
              time_code, component, component_order, feature_value,
              contribution_price_jpy_kwh,
              date_format(published_at, 'yyyy-MM-dd HH:mm:ss') as published_at,
              run_id
            from {CONTRIBUTION_TABLE}
            where run_id = '{run_id}'
            order by trade_date, time_code, component_order
            """
        )
        .toPandas()
        .reset_index(drop=True)
    )


def contribution_records(days, time_codes, *, run_id, base=10.0, strategy="lightgbm"):
    return build_contribution_records(
        TASK,
        make_contributions(days, time_codes, base=base),
        make_result(days, time_codes, base=base),
        run_id=run_id,
        strategy=strategy,
        area_code="tokyo",
        published_at=PUBLISHED_AT,
    )


class TestPublishContributionRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        records = contribution_records(["2024-04-10"], [1, 2], run_id="contrib-create")

        assert publish_contribution_records(TASK, records, spark=spark) == 4

        assert spark.catalog.tableExists(CONTRIBUTION_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(CONTRIBUTION_TABLE)}
        assert {name: c.dataType for name, c in columns.items()} == {
            "strategy": "string",
            "area_code": "string",
            "forecast_issued_ts": "timestamp",
            "trade_date": "date",
            "time_code": "int",
            "component": "string",
            "component_order": "int",
            "feature_value": "double",
            "contribution_price_jpy_kwh": "double",
            "published_at": "timestamp",
            "run_id": "string",
        }
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        rows = published_contribution_rows(spark, "contrib-create")
        assert rows[
            ["trade_date", "time_code", "component", "component_order"]
        ].values.tolist() == [
            ["2024-04-10", 1, "base", 0],
            ["2024-04-10", 1, "time_code", 1],
            ["2024-04-10", 2, "base", 0],
            ["2024-04-10", 2, "time_code", 1],
        ]
        assert rows["contribution_price_jpy_kwh"].tolist() == [10.0, 0.1, 10.0, 0.2]
        assert rows["feature_value"].tolist()[1::2] == [1.0, 2.0]
        # The base row's feature value is a SQL null, not a NaN double.
        n_null = spark.sql(
            f"select count(*) as n from {CONTRIBUTION_TABLE} "
            "where run_id = 'contrib-create' and feature_value is null"
        ).collect()[0]["n"]
        assert n_null == 2
        assert rows["forecast_issued_ts"].eq("2024-04-09 09:55").all()
        assert rows["published_at"].eq("2026-08-26 10:00:00").all()
        assert rows["strategy"].eq("lightgbm").all()
        assert rows["area_code"].eq("tokyo").all()

    def test_republishing_a_run_replaces_only_that_runs_partition(self, spark):
        keep = contribution_records(["2024-04-10"], [1], run_id="contrib-keep", base=20.0)
        first = contribution_records(["2024-04-10", "2024-04-11"], [1], run_id="contrib-replace")
        publish_contribution_records(TASK, keep, spark=spark)
        assert publish_contribution_records(TASK, first, spark=spark) == 4

        second = contribution_records(["2024-04-12"], [1], run_id="contrib-replace", base=30.0)
        assert publish_contribution_records(TASK, second, spark=spark) == 2

        replaced = published_contribution_rows(spark, "contrib-replace")
        assert replaced[
            ["trade_date", "component", "contribution_price_jpy_kwh"]
        ].values.tolist() == [
            ["2024-04-12", "base", 30.0],
            ["2024-04-12", "time_code", 0.1],
        ]
        kept = published_contribution_rows(spark, "contrib-keep")
        assert kept["contribution_price_jpy_kwh"].tolist() == [20.0, 0.1]

    def test_defaults_to_the_active_spark_session(self, spark):
        records = contribution_records(["2024-04-10"], [7], run_id="contrib-default-session")
        assert publish_contribution_records(TASK, records) == 2
        assert published_contribution_rows(spark, "contrib-default-session")[
            "time_code"
        ].tolist() == [7, 7]
