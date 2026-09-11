"""The similar-day feature rows: one scoring run's values, written to pma_ml.similar_day."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.similar_day import PERIODS_PER_HOUR, SimilarDaySelection
from power_market_analytics.tasks.demand.similar_day_feature import (
    FEATURE_TABLE,
    MLFLOW_EXPERIMENT,
    SimilarDayFeatureRecords,
    build_feature_records,
    publish_feature_records,
)
from tests.test_demand_similar_day import (
    D,
    forecast_available_at,
    load_at,
    make_forecast,
    make_hourly_load,
)

PUBLISHED_AT = pd.Timestamp("2026-09-11 10:00:00")
OTHER = pd.Timestamp("2024-04-11")


def make_selection(days=(D, OTHER), lag: int = 364) -> SimilarDaySelection:
    days = list(days)
    return SimilarDaySelection.from_df(
        pd.DataFrame(
            {
                "trade_date": pd.to_datetime(days),
                "reference_date": pd.to_datetime([d - pd.Timedelta(days=lag) for d in days]),
                "distance": [0.5] * len(days),
                "reference_lag_days": pd.Series([lag] * len(days), dtype="int64"),
                "n_candidates": pd.Series([61] * len(days), dtype="int64"),
                "lag_364_rank": [1.0] * len(days),
            }
        )
    )


def make_records(selection=None, run_id: str = "score-1", **kwargs) -> SimilarDayFeatureRecords:
    return build_feature_records(
        make_selection() if selection is None else selection,
        kwargs.pop("hourly_load", make_hourly_load()),
        kwargs.pop("forecast", make_forecast()),
        run_id=run_id,
        area_code="tokyo",
        published_at=PUBLISHED_AT,
    )


def published_rows(spark, run_id: str) -> pd.DataFrame:
    return spark.table(FEATURE_TABLE).where(f"run_id = '{run_id}'").toPandas()


class TestConstants:
    def test_names(self):
        assert MLFLOW_EXPERIMENT == "similar_day"
        assert FEATURE_TABLE == "pma_ml.similar_day"


class TestBuildFeatureRecords:
    def test_48_rows_per_day_with_the_reference_days_load_halved(self):
        records = make_records()
        assert type(records) is SimilarDayFeatureRecords
        assert records.keys == ["area_code", "trade_date", "time_code"]
        assert list(records.df.columns) == [
            "area_code",
            "trade_date",
            "time_code",
            "similar_day_demand_kwh",
            "similar_day_reference_date",
            "similar_day_reference_lag_days",
            "similar_day_distance",
            "similar_day_n_candidates",
            "available_at",
            "published_at",
            "run_id",
        ]
        assert len(records) == 96
        by_period = records.df.set_index(["trade_date", "time_code"])
        reference = D - pd.Timedelta(days=364)
        for tc in (1, 2, 24, 48):
            row = by_period.loc[(D, tc)]
            assert (
                row["similar_day_demand_kwh"]
                == load_at(reference, (tc + 1) // 2) / PERIODS_PER_HOUR
            )
            assert row["similar_day_reference_date"] == reference
            assert row["similar_day_reference_lag_days"] == 364
            assert row["similar_day_distance"] == 0.5
            assert row["similar_day_n_candidates"] == 61
            # The row is usable once D's forecast vintage is: 01:00 on D-1.
            assert row["available_at"] == forecast_available_at(D)
            assert row["published_at"] == PUBLISHED_AT
            assert row["run_id"] == "score-1"
            assert row["area_code"] == "tokyo"
        assert records.df["trade_date"].tolist() == [D] * 48 + [OTHER] * 48
        assert records.df["time_code"].tolist() == list(range(1, 49)) * 2
        assert records.df["similar_day_reference_lag_days"].dtype == "int64"
        assert records.df["available_at"].dtype == "datetime64[ns]"

    def test_an_empty_selection_is_rejected(self):
        with pytest.raises(ValueError, match="no scorable day to publish"):
            make_records(make_selection(days=()))

    def test_a_similar_day_without_a_load_is_rejected(self):
        # 2023-01-01 is the first load day: a lag reaching before it has no load.
        selection = make_selection(days=(pd.Timestamp("2023-12-31"),), lag=365)
        with pytest.raises(ValueError, match=r"48 period\(s\) have no load on their similar day"):
            make_records(selection)

    def test_a_day_without_a_forecast_is_rejected(self):
        forecast = make_forecast(pd.date_range("2024-04-11", "2024-04-12"))
        with pytest.raises(
            ValueError, match=r"1 day\(s\) have no forecast availability, e.g. 2024-04-10"
        ):
            make_records(forecast=forecast)

    def test_the_frame_checks_its_rows(self):
        df = make_records().df
        with pytest.raises(ValueError, match="similar_day_reference_date must precede"):
            SimilarDayFeatureRecords.from_df(df.assign(similar_day_reference_date=df["trade_date"]))
        with pytest.raises(ValueError, match="must equal the date gap"):
            SimilarDayFeatureRecords.from_df(df.assign(similar_day_reference_lag_days=363))
        with pytest.raises(ValueError, match="must be positive"):
            SimilarDayFeatureRecords.from_df(df.assign(similar_day_demand_kwh=0.0))
        with pytest.raises(ValueError, match="time_code outside 1..48"):
            SimilarDayFeatureRecords.from_df(df.assign(time_code=df["time_code"] + 48))
        with pytest.raises(ValueError, match="one run per frame, got 2"):
            SimilarDayFeatureRecords.from_df(df.assign(run_id=["a"] * 48 + ["b"] * 48))


class TestPublishFeatureRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        assert publish_feature_records(make_records(run_id="score-create"), spark=spark) == 96
        assert spark.catalog.tableExists(FEATURE_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(FEATURE_TABLE)}
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        assert list(columns) == list(make_records().df.columns)
        assert columns["trade_date"].dataType == "date"
        assert columns["similar_day_reference_lag_days"].dataType == "int"
        assert columns["similar_day_demand_kwh"].dataType == "double"
        assert columns["available_at"].dataType == "timestamp"
        rows = published_rows(spark, "score-create").sort_values(["trade_date", "time_code"])
        assert len(rows) == 96
        first = rows.iloc[0]
        assert first["trade_date"] == D.date()
        assert first["time_code"] == 1
        assert first["similar_day_reference_date"] == (D - pd.Timedelta(days=364)).date()
        assert first["similar_day_demand_kwh"] == load_at(D - pd.Timedelta(days=364), 1) / 2
        assert first["available_at"] == forecast_available_at(D)
        assert first["published_at"] == PUBLISHED_AT

    def test_republishing_a_run_replaces_only_its_rows(self, spark):
        publish_feature_records(make_records(run_id="score-keep"), spark=spark)
        publish_feature_records(make_records(run_id="score-replace"), spark=spark)
        publish_feature_records(
            make_records(make_selection(days=(D,)), run_id="score-replace"), spark=spark
        )
        assert len(published_rows(spark, "score-replace")) == 48
        assert len(published_rows(spark, "score-keep")) == 96

    def test_defaults_to_the_active_spark_session(self, spark):
        assert publish_feature_records(make_records(run_id="score-default")) == 96
        assert len(published_rows(spark, "score-default")) == 96
