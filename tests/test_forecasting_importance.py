"""Tests for the walk-forward permutation importance (scikit-learn over routed models)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.base import is_regressor

from power_market_analytics.forecasting.frames import PermutationImportance
from power_market_analytics.forecasting.importance import (
    DEFAULT_N_REPEATS,
    DEFAULT_SEED,
    MODEL_INDEX_COL,
    WalkForwardPredictor,
    permutation_importance,
)


class Linear:
    """A stand-in model: ``slope * signal + intercept`` (ignores every other column)."""

    def __init__(self, slope: float, intercept: float) -> None:
        self.slope, self.intercept = slope, intercept

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.slope * X["signal"].to_numpy() + self.intercept


MODELS = [Linear(3.0, 0.0), Linear(3.0, 0.05)]


def make_rows(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    return pd.DataFrame(
        {
            "signal": signal,
            "noise": rng.normal(size=n),
            "actual": 3.0 * signal + 0.1 * rng.normal(size=n),
            MODEL_INDEX_COL: np.repeat([0, 1], n // 2),
        }
    )


class TestWalkForwardPredictor:
    def test_routes_rows_by_position_not_by_index(self):
        X = pd.DataFrame({"signal": [1.0, 2.0, 3.0, 4.0]}, index=[9, 8, 7, 6])
        predictor = WalkForwardPredictor([Linear(1.0, 0.0), Linear(1.0, 100.0)], [0, 1, 0, 1])
        np.testing.assert_array_equal(predictor.predict(X), [1.0, 102.0, 3.0, 104.0])

    def test_a_model_without_rows_is_skipped(self):
        X = pd.DataFrame({"signal": [1.0, 2.0]})
        predictor = WalkForwardPredictor([Linear(2.0, 0.0), Linear(1.0, 100.0)], [0, 0])
        np.testing.assert_array_equal(predictor.predict(X), [2.0, 4.0])

    def test_row_count_must_match_the_routing(self):
        predictor = WalkForwardPredictor([Linear(1.0, 0.0)], [0, 0, 0])
        with pytest.raises(ValueError, match="model_of_row has 3 entries for 2 rows"):
            predictor.predict(pd.DataFrame({"signal": [1.0, 2.0]}))

    def test_model_index_must_exist(self):
        predictor = WalkForwardPredictor([Linear(1.0, 0.0)], [0, 1])
        with pytest.raises(ValueError, match=r"model_of_row refers to models outside 0\.\.0"):
            predictor.predict(pd.DataFrame({"signal": [1.0, 2.0]}))

    def test_is_a_fitted_regressor_for_scikit_learn(self):
        predictor = WalkForwardPredictor([Linear(1.0, 0.0)], [0])
        assert predictor.fit(pd.DataFrame({"signal": [1.0]}), [1.0]) is predictor
        assert is_regressor(predictor)


class TestPermutationImportance:
    def test_the_signal_feature_matters_and_the_noise_feature_does_not(self):
        rows = make_rows()
        importance = permutation_importance(
            rows, MODELS, ("signal", "noise"), actual_col="actual", n_repeats=5, seed=0
        )
        assert isinstance(importance, PermutationImportance)
        assert importance.df["feature"].tolist() == ["signal"] * 5 + ["noise"] * 5
        assert importance.df["feature_order"].tolist() == [1] * 5 + [2] * 5
        assert importance.df["repeat_index"].tolist() == list(range(5)) * 2
        assert importance.df["n_periods"].unique().tolist() == [400]
        expected_mae = np.abs(
            rows["actual"] - WalkForwardPredictor(MODELS, rows[MODEL_INDEX_COL]).predict(rows)
        ).mean()
        assert importance.df["mae"].unique().tolist() == pytest.approx([expected_mae])
        summary = importance.summary().df.set_index("feature")
        assert summary.loc["signal", "importance_mae"] > 1.0
        assert summary.loc["noise", "importance_mae"] == 0.0
        assert summary.loc["noise", "importance_std"] == 0.0

    def test_the_same_seed_reproduces_and_another_seed_changes_the_shuffles(self):
        rows = make_rows()
        first = permutation_importance(rows, MODELS, ("signal",), actual_col="actual", seed=0)
        again = permutation_importance(rows, MODELS, ("signal",), actual_col="actual", seed=0)
        other = permutation_importance(rows, MODELS, ("signal",), actual_col="actual", seed=1)
        pd.testing.assert_frame_equal(first.df, again.df)
        assert not first.df["permuted_mae"].equals(other.df["permuted_mae"])

    def test_defaults_are_five_repeats_and_seed_zero(self):
        assert (DEFAULT_N_REPEATS, DEFAULT_SEED) == (5, 0)
        rows = make_rows()
        by_default = permutation_importance(rows, MODELS, ("signal",), actual_col="actual")
        explicit = permutation_importance(
            rows, MODELS, ("signal",), actual_col="actual", n_repeats=5, seed=0
        )
        pd.testing.assert_frame_equal(by_default.df, explicit.df)

    def test_rows_must_carry_the_features_the_actual_and_the_model_index(self):
        rows = make_rows().drop(columns=[MODEL_INDEX_COL])
        with pytest.raises(ValueError, match=r"rows lack columns \['model_index'\]"):
            permutation_importance(rows, MODELS, ("signal",), actual_col="actual")

    def test_needs_rows_and_at_least_one_repeat(self):
        rows = make_rows()
        with pytest.raises(ValueError, match="no rows to score"):
            permutation_importance(rows.iloc[:0], MODELS, ("signal",), actual_col="actual")
        with pytest.raises(ValueError, match="n_repeats must be >= 1, got 0"):
            permutation_importance(rows, MODELS, ("signal",), actual_col="actual", n_repeats=0)
