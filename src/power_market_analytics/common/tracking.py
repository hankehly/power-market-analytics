"""MLflow tracking conventions.

One MLflow experiment per modeling task (e.g. ``spot_price``), one run per
strategy/config execution. The tracking URI comes from the
``MLFLOW_TRACKING_URI`` environment variable (set to http://mlflow:5005 in
the devcontainer).
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import mlflow
import pandas as pd
from mlflow.metrics import MetricValue, make_metric
from mlflow.models import EvaluationResult

from power_market_analytics.common.metrics import mape


@contextmanager
def task_run(
    experiment: str,
    run_name: str | None = None,
    tags: dict[str, str] | None = None,
) -> Iterator[mlflow.ActiveRun]:
    """Start an MLflow run under the given task experiment.

    Parameters
    ----------
    experiment : str
        MLflow experiment name; created if it does not exist.
    run_name : str, optional
        Display name for the run.
    tags : dict of str to str, optional
        Tags to set on the run (e.g. strategy, universe).

    Yields
    ------
    mlflow.ActiveRun
    """
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name, tags=tags) as run:
        yield run


def log_dataframe(df: pd.DataFrame, filename: str) -> None:
    """Log a DataFrame to the active MLflow run as a CSV artifact.

    Parameters
    ----------
    df : pandas.DataFrame
        Data to log.
    filename : str
        Artifact file name, e.g. ``daily_errors.csv``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        df.to_csv(path, index=False)
        mlflow.log_artifact(str(path))


MAPE_METRIC_NAME = "mape_excl_zero_actuals"


def _mape_excl_zero_actuals(predictions: pd.Series, targets: pd.Series) -> MetricValue:
    """Backing function for the :func:`mape_metric` extra metric.

    Parameters
    ----------
    predictions : pandas.Series
        Model output. MLflow matches this argument by name.
    targets : pandas.Series
        Observed values. MLflow matches this argument by name.

    Returns
    -------
    mlflow.metrics.MetricValue
        Keyed by the metric name: MLflow logs an aggregate under the bare
        metric name only when the two match, and otherwise as
        ``<metric>/<key>``.
    """
    return MetricValue(aggregate_results={MAPE_METRIC_NAME: mape(targets, predictions)})


def mape_metric() -> mlflow.models.EvaluationMetric:
    """MAPE that drops zero actuals, as an ``extra_metrics`` entry.

    MLflow's built-in ``mean_absolute_percentage_error`` comes from
    scikit-learn, which divides by ``eps`` instead of skipping zero actuals
    and so returns ~1e15 on any window containing JEPX's genuine 0.00 JPY/kWh
    prices (FY2016, before the 0.01 floor). This metric applies
    :func:`power_market_analytics.common.metrics.mape` instead, so the run
    carries a number comparable to the backtest's own MAPE. It is reported in
    percent, unlike MLflow's built-in, which is a fraction.

    Returns
    -------
    mlflow.models.EvaluationMetric
    """
    return make_metric(
        eval_fn=_mape_excl_zero_actuals,
        greater_is_better=False,
        name=MAPE_METRIC_NAME,
    )


def evaluate_regressor(
    model_uri: str,
    data: pd.DataFrame,
    targets: str,
    *,
    explainability_algorithm: str = "exact",
    explainability_nsamples: int = 500,
    log_explainer: bool = False,
) -> EvaluationResult:
    """Run MLflow's unified evaluation API over a logged regression model.

    Logs the built-in regressor metric set (``mean_absolute_error``,
    ``root_mean_squared_error``, ``r2_score``, ``max_error``, ...) plus SHAP
    beeswarm, summary and feature-importance plots to the active run.

    Parameters
    ----------
    model_uri : str
        URI of a logged pyfunc model, e.g. ``ModelInfo.model_uri``. A model
        (rather than a static predictions column) is required: the SHAP
        evaluator has to call ``predict`` on perturbed feature rows.
    data : pandas.DataFrame
        Feature columns plus ``targets``. Every non-target column is treated
        as a feature and must be numeric, or the SHAP evaluator logs a
        warning and skips.
    targets : str
        Name of the observed-value column in ``data``.
    explainability_algorithm : str, optional
        SHAP algorithm: ``exact``, ``permutation``, ``partition`` or
        ``kernel``. ``exact`` is exponential in the feature count — switch to
        ``permutation`` beyond ~15 features.
    explainability_nsamples : int, optional
        Rows sampled from ``data`` for SHAP. The full frame is still used for
        the metrics.
    log_explainer : bool, optional
        Also log the fitted SHAP explainer as a model artifact.

    Returns
    -------
    mlflow.models.EvaluationResult
        ``.metrics`` and ``.artifacts`` for the evaluation.
    """
    return mlflow.models.evaluate(
        model=model_uri,
        data=data,
        targets=targets,
        model_type="regressor",
        # Naming the evaluators explicitly requires the nested per-evaluator
        # config form; a flat dict is only accepted when evaluators is None.
        evaluators=["regressor", "shap"],
        evaluator_config={
            "regressor": {},
            "shap": {
                "explainability_algorithm": explainability_algorithm,
                "explainability_nsamples": explainability_nsamples,
                "log_explainer": log_explainer,
            },
        },
        extra_metrics=[mape_metric()],
    )
