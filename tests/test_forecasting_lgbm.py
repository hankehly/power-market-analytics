"""Tests for the generic sliding-window LightGBM base (its own guards).

The full behaviour — refits, TreeSHAP records, eval sets, MLflow logging — is
exercised through the concrete spot_price and demand strategies in
tests/test_spot_price_lgbm.py and tests/test_demand_lgbm.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LGBM_PARAMS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.spot_price import TASK


class TestConstants:
    def test_calendar_features(self):
        assert CALENDAR_FEATURE_COLS == ("time_code", "month", "day_of_week")

    def test_lgbm_params_are_fixed_and_deterministic(self):
        assert LGBM_PARAMS == {
            "n_estimators": 500,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "random_state": 0,
            "verbose": -1,
        }


class TestEvalSetBase:
    def test_to_eval_frame_uses_the_class_columns_as_float64(self):
        class EvalSet(LightGbmEvalSetBase):
            feature_cols = ("time_code", "x")
            target_col = "y"
            forecast_col = "yhat"
            schema = {
                "trade_date": "datetime64[ns]",
                "time_code": "int64",
                "x": "int64",
                "y": "float64",
                "yhat": "float64",
            }
            keys = ["trade_date", "time_code"]
            non_null_cols = ["x", "y", "yhat"]

        es = EvalSet.from_df(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(["2024-01-01"]),
                    "time_code": [1],
                    "x": [3],
                    "y": [1.0],
                    "yhat": [1.5],
                }
            )
        )
        frame = es.to_eval_frame()
        assert list(frame.columns) == ["time_code", "x", "y", "yhat"]
        assert frame.dtypes.astype(str).unique().tolist() == ["float64"]


class TestSlidingWindowLightGbmStrategy:
    def test_add_features_is_abstract(self):
        class NoFeatures(SlidingWindowLightGbmStrategy):
            name = "n"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

        with pytest.raises(TypeError, match="abstract"):
            NoFeatures()

    def test_extra_params_default_to_empty(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _add_features(self, featured, history_df):
                return featured

        assert Minimal()._extra_params() == {}

    def test_categorical_feature_cols_default_to_none(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = CALENDAR_FEATURE_COLS
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _add_features(self, featured, history_df):
                return featured

        assert SlidingWindowLightGbmStrategy.categorical_feature_cols == ()
        assert Minimal().categorical_feature_cols == ()
