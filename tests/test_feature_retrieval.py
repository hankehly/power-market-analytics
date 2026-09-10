"""As-of retrieval of a prediction frame through Feast on the local Spark session."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from feast import FeatureView, Field
from feast.infra.offline_stores.contrib.spark_offline_store.spark_source import SparkSource
from feast.types import Float64
from pyspark.sql import SparkSession

from power_market_analytics.features.entities import ENTITIES, GRAIN_ENTITIES
from power_market_analytics.features.retrieval import (
    ENTITY_COLS,
    ENTITY_TIME_ZONE,
    EVENT_TIMESTAMP_COL,
    entity_frame,
    historical_features,
    is_utc,
)
from power_market_analytics.features.store import open_store
from tests.support import write_feature_store_yaml

ISSUE = pd.Timedelta(days=-1, hours=9, minutes=30)


@pytest.fixture
def utc_session(spark):
    """The fixture's session in UTC for the duration of a test, as the devcontainer's is."""
    zone = spark.conf.get("spark.sql.session.timeZone")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    yield spark
    spark.conf.set("spark.sql.session.timeZone", zone)


@pytest.fixture
def day_store(utc_session, tmp_path):
    """A day-grain view with two vintages of Tokyo's 2025-03-10 row and a Kansai row."""
    utc_session.sql(
        """
        select * from values
          ('tokyo', 20250310L, 1.0d, timestamp '2025-03-09 01:00:00'),
          ('tokyo', 20250310L, 2.0d, timestamp '2025-03-09 02:00:00'),
          ('kansai', 20250310L, 9.0d, timestamp '2025-03-09 01:00:00')
          as t(area_code, trade_date_key, x, available_at)
        """
    ).createOrReplaceTempView("ftr_test_day")
    view = FeatureView(
        name="ftr_test_day",
        entities=list(GRAIN_ENTITIES["day"]),
        schema=[Field(name="x", dtype=Float64)],
        source=SparkSource(
            name="ftr_test_day", table="ftr_test_day", timestamp_field="available_at"
        ),
        online=False,
    )
    return open_store(write_feature_store_yaml(tmp_path), definitions=[*ENTITIES, view])


class TestEntityFrame:
    def test_one_row_per_period_with_keys_and_issue_time(self):
        days = pd.DatetimeIndex(["2025-03-10", "2025-03-11"])
        df = entity_frame("tokyo", days, ISSUE)
        assert list(df.columns) == [
            "area_code",
            "trade_date",
            *ENTITY_COLS[3:4],
            *ENTITY_COLS[1:3],
            EVENT_TIMESTAMP_COL,
        ] or set(df.columns) == {"area_code", "trade_date", *ENTITY_COLS}
        assert len(df) == 96
        assert df["time_code"].tolist()[:48] == list(range(1, 49))
        assert (
            df["trade_date_key"].iloc[0] == 20250310 and df["trade_date_key"].iloc[-1] == 20250311
        )
        assert df["hour_ending"].tolist()[:4] == [1, 1, 2, 2] and df["hour_ending"].iloc[47] == 24
        assert df[EVENT_TIMESTAMP_COL].iloc[0] == pd.Timestamp("2025-03-09 09:30", tz="UTC")
        assert df[EVENT_TIMESTAMP_COL].iloc[-1] == pd.Timestamp("2025-03-10 09:30", tz="UTC")
        assert (df["area_code"] == "tokyo").all()


def test_is_utc_accepts_fixed_utc_zone_names_only():
    assert is_utc("UTC") and is_utc("Etc/UTC") and is_utc("GMT") and is_utc("+00:00")
    assert not is_utc("Asia/Tokyo") and not is_utc("Europe/London")
    # UTC in the year 2000 but not today, or only in some seasons: never accepted.
    assert not is_utc("Africa/Casablanca") and not is_utc("Antarctica/Troll")


class TestHistoricalFeatures:
    def test_a_day_feature_as_of_the_issue_time_repeats_over_the_periods(self, day_store):
        df = entity_frame("tokyo", pd.DatetimeIndex(["2025-03-10"]), ISSUE)
        got = historical_features(day_store, df, ["ftr_test_day:x"])
        assert len(got) == 48
        assert (got["x"] == 2.0).all()
        assert got["time_code"].tolist() == list(range(1, 49))
        assert got[EVENT_TIMESTAMP_COL].dt.tz is None
        assert got[EVENT_TIMESTAMP_COL].iloc[0] == pd.Timestamp("2025-03-09 09:30")
        assert "trade_date" in got.columns

    def test_a_row_is_usable_at_its_available_at_and_not_one_minute_before(self, day_store):
        probe = pd.DataFrame(
            {
                "area_code": "tokyo",
                "trade_date_key": 20250310,
                "hour_ending": 1,
                "time_code": 1,
                EVENT_TIMESTAMP_COL: pd.to_datetime(
                    ["2025-03-09 00:59", "2025-03-09 01:00", "2025-03-09 02:00"]
                ).tz_localize(ENTITY_TIME_ZONE),
            }
        )
        got = historical_features(day_store, probe, ["ftr_test_day:x"])
        assert np.isnan(got["x"].iloc[0])
        assert got["x"].tolist()[1:] == [1.0, 2.0]

    def test_an_unknown_key_yields_nulls_and_another_area_its_own_row(self, day_store):
        probe = pd.DataFrame(
            {
                "area_code": ["kansai", "kansai"],
                "trade_date_key": [20250310, 20250311],
                "hour_ending": 1,
                "time_code": 1,
                EVENT_TIMESTAMP_COL: pd.to_datetime(["2025-03-09 09:30"] * 2).tz_localize(
                    ENTITY_TIME_ZONE
                ),
            }
        )
        got = historical_features(day_store, probe, ["ftr_test_day:x"])
        assert got["x"].iloc[0] == 9.0 and np.isnan(got["x"].iloc[1])

    def test_duplicate_entity_rows_are_rejected(self, day_store):
        df = entity_frame("tokyo", pd.DatetimeIndex(["2025-03-10"]), ISSUE)
        with pytest.raises(ValueError, match="unique on"):
            historical_features(day_store, pd.concat([df, df]), ["ftr_test_day:x"])

    def test_a_frame_not_stamped_as_utc_is_rejected(self, day_store):
        df = entity_frame("tokyo", pd.DatetimeIndex(["2025-03-10"]), ISSUE)
        shifted = df.assign(
            **{EVENT_TIMESTAMP_COL: df[EVENT_TIMESTAMP_COL].dt.tz_convert("Asia/Tokyo")}
        )
        with pytest.raises(ValueError, match="stamped as UTC"):
            historical_features(day_store, shifted, ["ftr_test_day:x"])

    def test_a_session_outside_utc_is_refused(self, spark):
        assert spark.conf.get("spark.sql.session.timeZone") == "Asia/Tokyo"
        df = entity_frame("tokyo", pd.DatetimeIndex(["2025-03-10"]), ISSUE)
        with pytest.raises(RuntimeError, match="needs a UTC session"):
            historical_features(object(), df, ["v:x"])  # type: ignore[arg-type]

    def test_needs_an_active_spark_session(self, monkeypatch):
        monkeypatch.setattr(SparkSession, "getActiveSession", classmethod(lambda cls: None))
        with pytest.raises(RuntimeError, match="no active SparkSession"):
            historical_features(object(), pd.DataFrame(), ["v:x"])  # type: ignore[arg-type]

    def test_timezone_aware_timestamps_from_the_store_are_returned_naive(self, utc_session):
        df = entity_frame("tokyo", pd.DatetimeIndex(["2025-03-10"]), ISSUE).head(2)

        class Job:
            def to_df(self):
                out = df[ENTITY_COLS].copy()
                out[EVENT_TIMESTAMP_COL] = out[EVENT_TIMESTAMP_COL].dt.tz_convert("Asia/Tokyo")
                return out.assign(x=[1.0, 2.0])

        class Store:
            def get_historical_features(self, entity_df, features):
                assert list(entity_df.columns) == ENTITY_COLS and features == ["v:x"]
                return Job()

        got = historical_features(Store(), df, ["v:x"])  # type: ignore[arg-type]
        assert got[EVENT_TIMESTAMP_COL].tolist() == [pd.Timestamp("2025-03-09 09:30")] * 2
        assert got["x"].tolist() == [1.0, 2.0]
