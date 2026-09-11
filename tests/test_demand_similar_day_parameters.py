"""The similar-day parameters: one row per fit, written to pma_ml.similar_day_parameters."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.similar_day import (
    SIMILAR_DAY_COMPONENTS,
    SimilarDayWeights,
)
from power_market_analytics.tasks.demand.similar_day_parameters import (
    MLFLOW_EXPERIMENT,
    PARAMETERS_TABLE,
    SimilarDayParameterRecords,
    build_parameter_records,
    publish_parameter_records,
)

PUBLISHED_AT = pd.Timestamp("2026-09-11 10:00:00")


def make_weights(**overrides) -> SimilarDayWeights:
    fields = {
        "components": SIMILAR_DAY_COMPONENTS,
        "weights": np.array([0.05, 0.43, 0.0, 0.0003, 0.04, 0.0, 0.4797]),
        "scales": np.array([17.6, 4.56, 19.7, 1.27, 19.3, 19.4, 0.617]),
        "alpha": 0.108,
        "beta": 0.022,
        "n_pairs": 119_865,
        "n_targets": 1_965,
        "fit_from": pd.Timestamp("2019-04-01"),
        "fit_through": pd.Timestamp("2024-08-16"),
        "fit_rmse": 0.0753,
    }
    return SimilarDayWeights(**{**fields, **overrides})


def make_records(run_id: str = "fit-1", **overrides) -> SimilarDayParameterRecords:
    return build_parameter_records(
        make_weights(**overrides),
        run_id=run_id,
        area_code="tokyo",
        center_lag_days=364,
        window_half_width_days=30,
        census_year=2020,
        published_at=PUBLISHED_AT,
    )


def published_rows(spark, run_id: str) -> pd.DataFrame:
    return spark.table(PARAMETERS_TABLE).where(f"run_id = '{run_id}'").toPandas()


class TestConstants:
    def test_names(self):
        assert MLFLOW_EXPERIMENT == "similar_day"
        assert PARAMETERS_TABLE == "pma_ml.similar_day_parameters"


class TestBuildParameterRecords:
    def test_one_row_with_the_fit_in_component_order(self):
        records = make_records()
        assert type(records) is SimilarDayParameterRecords
        assert records.keys == ["run_id"]
        assert len(records) == 1
        row = records.df.iloc[0]
        assert list(records.df.columns) == [
            "area_code",
            "fit_from",
            "fit_through",
            "center_lag_days",
            "window_half_width_days",
            "census_year",
            *[f"weight_{part}" for part in SIMILAR_DAY_COMPONENTS],
            *[f"scale_{part}" for part in SIMILAR_DAY_COMPONENTS],
            "alpha",
            "beta",
            "n_pairs",
            "n_targets",
            "fit_rmse",
            "available_at",
            "published_at",
            "run_id",
        ]
        assert row["area_code"] == "tokyo"
        assert row["fit_from"] == pd.Timestamp("2019-04-01")
        assert row["fit_through"] == pd.Timestamp("2024-08-16")
        assert row["center_lag_days"] == 364
        assert row["window_half_width_days"] == 30
        assert row["census_year"] == 2020
        assert row["weight_temperature"] == 0.43
        assert row["scale_holiday_degree"] == 0.617
        assert row["alpha"] == 0.108
        assert row["beta"] == 0.022
        assert row["n_pairs"] == 119_865
        assert row["n_targets"] == 1_965
        assert row["fit_rmse"] == 0.0753
        # Public once every load the fit used was: the midnight after the last target day.
        assert row["available_at"] == pd.Timestamp("2024-08-17 00:00:00")
        assert row["published_at"] == PUBLISHED_AT
        assert row["run_id"] == "fit-1"
        assert records.df["n_pairs"].dtype == "int64"
        assert records.df["weight_rain"].dtype == "float64"
        assert records.df["fit_from"].dtype == "datetime64[ns]"

    def test_weights_must_be_non_negative_shares(self):
        with pytest.raises(ValueError, match="weights must be >= 0 and sum to one"):
            make_records(weights=np.array([0.5, 0.6, 0.0, 0.0, 0.0, 0.0, 0.0]))
        with pytest.raises(ValueError, match="weights must be >= 0 and sum to one"):
            make_records(weights=np.array([-0.1, 1.1, 0.0, 0.0, 0.0, 0.0, 0.0]))

    def test_scales_alpha_and_the_window_are_checked(self):
        with pytest.raises(ValueError, match="scales must be > 0"):
            make_records(scales=np.array([0.0, 4.56, 19.7, 1.27, 19.3, 19.4, 0.617]))
        with pytest.raises(ValueError, match="alpha must be >= 0"):
            make_records(alpha=-0.5)
        with pytest.raises(ValueError, match="fit_from must not follow fit_through"):
            make_records(fit_from=pd.Timestamp("2024-08-17"))

    def test_available_at_is_the_midnight_after_fit_through(self):
        df = make_records().df.assign(
            available_at=pd.Series([pd.Timestamp("2024-08-16 23:00:00")], dtype="datetime64[ns]")
        )
        with pytest.raises(ValueError, match="available_at must be the midnight after"):
            SimilarDayParameterRecords.from_df(df)


class TestPublishParameterRecords:
    def test_creates_the_partitioned_table_and_writes_the_row(self, spark):
        assert publish_parameter_records(make_records("fit-create"), spark=spark) == 1
        assert spark.catalog.tableExists(PARAMETERS_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(PARAMETERS_TABLE)}
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        assert list(columns) == list(make_records().df.columns)
        assert columns["fit_from"].dataType == "date"
        assert columns["n_pairs"].dataType == "bigint"
        assert columns["n_targets"].dataType == "int"
        assert columns["weight_calendar_days"].dataType == "double"
        assert columns["available_at"].dataType == "timestamp"
        row = published_rows(spark, "fit-create").iloc[0]
        assert row["area_code"] == "tokyo"
        assert row["fit_through"] == pd.Timestamp("2024-08-16").date()
        assert row["weight_holiday_degree"] == 0.4797
        assert row["available_at"] == pd.Timestamp("2024-08-17 00:00:00")
        assert row["published_at"] == PUBLISHED_AT

    def test_republishing_a_run_replaces_only_its_row(self, spark):
        publish_parameter_records(make_records("fit-keep"), spark=spark)
        publish_parameter_records(make_records("fit-replace", alpha=0.1), spark=spark)
        publish_parameter_records(make_records("fit-replace", alpha=0.2), spark=spark)
        assert published_rows(spark, "fit-replace")["alpha"].tolist() == [0.2]
        assert published_rows(spark, "fit-keep")["alpha"].tolist() == [0.108]

    def test_defaults_to_the_active_spark_session(self, spark):
        assert publish_parameter_records(make_records("fit-default")) == 1
        assert len(published_rows(spark, "fit-default")) == 1
