"""Tests for the generic sliding-window LightGBM base (its own guards).

The full behaviour — refits, TreeSHAP records, eval sets, MLflow logging — is
exercised through the preset strategy in tests/test_forecasting_preset_lgbm.py
and the demand similar-day strategies in tests/test_demand_lgbm.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.forecasting.lgbm import (
    LGBM_PARAMS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.spot_price import TASK


class TestConstants:
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
    def test_features_is_abstract(self):
        class NoFeatures(SlidingWindowLightGbmStrategy):
            name = "n"
            task = TASK
            feature_cols = ("time_code",)
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

        with pytest.raises(TypeError, match="abstract"):
            NoFeatures()

    def test_extra_params_default_to_empty(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = ("time_code",)
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _features(self, points, history_df):
                return points

        assert Minimal()._extra_params() == {}

    def test_categorical_feature_cols_default_to_none(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = ("time_code",)
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _features(self, points, history_df):
                return points

        assert SlidingWindowLightGbmStrategy.categorical_feature_cols == ()
        assert Minimal().categorical_feature_cols == ()

    def test_contributions_need_a_prediction_first(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = ("time_code",)
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _features(self, points, history_df):
                return points

        with pytest.raises(
            RuntimeError, match="m: no recorded contributions; run the backtest first"
        ):
            Minimal().contributions()

    def test_permutation_importance_needs_a_backtest_first(self):
        class Minimal(SlidingWindowLightGbmStrategy):
            name = "m"
            task = TASK
            feature_cols = ("time_code",)
            eval_set_cls = LightGbmEvalSetBase
            lookback_days = 0

            def _features(self, points, history_df):
                return points

        with pytest.raises(RuntimeError, match="m: no recorded forecasts; run the backtest first"):
            Minimal().permutation_importance(run=None)
