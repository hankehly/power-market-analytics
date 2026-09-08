"""Permutation feature importance of a walk-forward backtest.

scikit-learn's ``permutation_importance`` scores one estimator: it shuffles a
feature's column across every row, re-scores, and records the loss increase.
A sliding-window backtest has no single model — each refit forecast a block
of days — so :class:`WalkForwardPredictor` presents the refits as one
estimator that routes every row to the model that forecast its day. The
importance is then out of sample and its baseline is the run's own MAE.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.inspection import permutation_importance as sklearn_permutation_importance

from power_market_analytics.forecasting.frames import PermutationImportance

#: Column of ``rows`` naming, per row, the index into ``models`` of the model that forecast it.
MODEL_INDEX_COL = "model_index"
DEFAULT_N_REPEATS = 5
DEFAULT_SEED = 0


class Predictor(Protocol):
    """Anything with a scikit-learn style ``predict`` (a LightGBM regressor, a rule)."""

    def predict(self, X: pd.DataFrame) -> Any: ...


class WalkForwardPredictor(RegressorMixin, BaseEstimator):
    """One estimator over many models: each row is scored by the model that forecast it.

    Rows are routed by position (``model_of_row[i]`` is the model of row
    ``i``), which is what scikit-learn's permutation loop preserves — it
    shuffles a column's values, never the rows.

    Parameters
    ----------
    models : sequence of Predictor
        The refits, in the order ``model_of_row`` indexes them.
    model_of_row : array-like of int
        One model index per row of the design matrix.
    """

    def __init__(self, models: Sequence[Predictor] | None = None, model_of_row: Any = None) -> None:
        self.models = models
        self.model_of_row = model_of_row

    def fit(self, X: Any, y: Any = None) -> WalkForwardPredictor:
        """Nothing to fit: the models arrive fitted. Required by scikit-learn.

        Parameters
        ----------
        X, y : Any
            Ignored.

        Returns
        -------
        WalkForwardPredictor
            ``self``.
        """
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Score every row of ``X`` with its own model.

        Parameters
        ----------
        X : pandas.DataFrame
            Design matrix; row ``i`` belongs to ``models[model_of_row[i]]``.

        Returns
        -------
        numpy.ndarray
            One prediction per row, in row order.

        Raises
        ------
        ValueError
            If ``model_of_row`` does not have one entry per row, or names a
            model outside ``models``.
        """
        models = list(self.models or [])
        model_of_row = np.asarray(self.model_of_row, dtype="int64")
        if len(model_of_row) != len(X):
            raise ValueError(f"model_of_row has {len(model_of_row)} entries for {len(X)} rows")
        if model_of_row.min() < 0 or model_of_row.max() >= len(models):
            raise ValueError(f"model_of_row refers to models outside 0..{len(models) - 1}")
        out = np.empty(len(X), dtype="float64")
        for index, model in enumerate(models):
            rows = np.flatnonzero(model_of_row == index)
            if rows.size:
                out[rows] = np.asarray(model.predict(X.iloc[rows]), dtype="float64")
        return out


def permutation_importance(
    rows: pd.DataFrame,
    models: Sequence[Predictor],
    feature_cols: Sequence[str],
    *,
    actual_col: str,
    n_repeats: int = DEFAULT_N_REPEATS,
    seed: int = DEFAULT_SEED,
) -> PermutationImportance:
    """Permutation importance of every feature over the rows a backtest scored.

    Parameters
    ----------
    rows : pandas.DataFrame
        The scored rows: ``feature_cols`` as the models saw them,
        ``actual_col`` and :data:`MODEL_INDEX_COL`.
    models : sequence of Predictor
        The refits, indexed by ``rows[MODEL_INDEX_COL]``.
    feature_cols : sequence of str
        The model's features, in its order (``feature_order`` follows it).
    actual_col : str
        The realized-value column of ``rows``.
    n_repeats : int, optional
        Shuffles per feature.
    seed : int, optional
        ``random_state`` of the shuffles; the same seed reproduces the result.

    Returns
    -------
    PermutationImportance
        ``mae`` is the MAE of the routed predictions on ``rows`` (the run's
        own MAE); ``permuted_mae`` = ``mae`` + scikit-learn's importance,
        scored with ``neg_mean_absolute_error``.

    Raises
    ------
    ValueError
        If ``rows`` is empty, lacks a needed column, or ``n_repeats`` < 1.
    """
    needed = [*feature_cols, actual_col, MODEL_INDEX_COL]
    missing = [col for col in needed if col not in rows.columns]
    if missing:
        raise ValueError(f"rows lack columns {missing}")
    if rows.empty:
        raise ValueError("no rows to score")
    if n_repeats < 1:
        raise ValueError(f"n_repeats must be >= 1, got {n_repeats}")
    X = rows[list(feature_cols)].astype("float64").reset_index(drop=True)
    y = rows[actual_col].to_numpy(dtype="float64")
    estimator = WalkForwardPredictor(models, rows[MODEL_INDEX_COL].to_numpy())
    result = sklearn_permutation_importance(
        estimator,
        X,
        y,
        scoring="neg_mean_absolute_error",
        n_repeats=n_repeats,
        random_state=seed,
    )
    mae = float(np.abs(y - estimator.predict(X)).mean())
    n_features = len(feature_cols)
    frame = pd.DataFrame(
        {
            "feature": np.repeat(list(feature_cols), n_repeats),
            "feature_order": np.repeat(np.arange(1, n_features + 1), n_repeats),
            "repeat_index": np.tile(np.arange(n_repeats), n_features),
            "n_periods": len(rows),
            "mae": mae,
            # importances is (n_features, n_repeats); its row-major ravel lines
            # up with the repeat/tile above.
            "permuted_mae": mae + np.asarray(result.importances).ravel(),
        }
    ).astype({"feature_order": "int64", "repeat_index": "int64", "n_periods": "int64"})
    return PermutationImportance.from_df(frame)
