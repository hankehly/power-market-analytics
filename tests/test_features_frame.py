"""FeatureFrame: the retrieved features of one area's prediction rows."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.features.frame import FeatureFrame, feature_frame, feature_frame_class


def retrieved() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "area_code": "tokyo",
            "trade_date": pd.to_datetime(["2025-03-10", "2025-03-10", "2025-03-11"]),
            "time_code": [2, 1, 1],
            "trade_date_key": [20250310, 20250310, 20250311],
            "month": [3, 3, 3],
            "lag_1d_price": [10.5, np.nan, 11.0],
        }
    )


class TestFeatureFrameClass:
    def test_schema_is_the_grain_plus_float64_columns(self):
        cls = feature_frame_class(["month", "lag_1d_price"])
        assert issubclass(cls, FeatureFrame)
        assert cls.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "month": "float64",
            "lag_1d_price": "float64",
        }
        assert cls.keys == ["trade_date", "time_code"] and cls.non_null_cols == []

    def test_rejects_duplicates_and_grain_collisions(self):
        with pytest.raises(ValueError, match="duplicate feature columns"):
            feature_frame_class(["month", "month"])
        with pytest.raises(ValueError, match=r"\['time_code'\] collide with the grain"):
            feature_frame_class(["time_code"])


class TestFeatureFrame:
    def test_keeps_the_grain_and_columns_sorted_with_nan_kept(self):
        frame = feature_frame(retrieved(), ["month", "lag_1d_price"])
        assert frame.feature_cols == ("month", "lag_1d_price")
        assert list(frame.df.columns) == ["trade_date", "time_code", "month", "lag_1d_price"]
        assert frame.df["time_code"].tolist() == [1, 2, 1]
        assert frame.df["month"].dtype == "float64" and frame.df["month"].tolist() == [
            3.0,
            3.0,
            3.0,
        ]
        assert (
            np.isnan(frame.df["lag_1d_price"].iloc[0]) and frame.df["lag_1d_price"].iloc[1] == 10.5
        )

    def test_a_missing_column_is_rejected(self):
        with pytest.raises(ValueError, match=r"lack columns \['nope'\]"):
            feature_frame(retrieved(), ["month", "nope"])

    def test_a_duplicate_grain_is_rejected(self):
        twice = pd.concat([retrieved(), retrieved()])
        with pytest.raises(ValueError, match="grain .* not unique"):
            feature_frame(twice, ["month"])
