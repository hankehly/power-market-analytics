"""Tests for the MLflow tracking conventions in ``common/tracking.py``.

Everything runs against the session's temp MLflow store (``mlflow_store`` in
conftest); nothing is mocked.
"""

from __future__ import annotations

import mlflow
import mlflow.shap
import numpy as np
import pandas as pd
import pytest
import shap
from mlflow.metrics import MetricValue

from power_market_analytics.common.tracking import (
    MAPE_METRIC_NAME,
    _mape_excl_zero_actuals,
    evaluate_predictions,
    evaluate_regressor,
    log_dataframe,
    mape_metric,
    task_run,
)
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayModel

EXPERIMENT = "test_tracking"


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    """Give this module its own MLflow experiment in the session store."""
    mlflow.set_experiment(EXPERIMENT)


class TestTaskRun:
    def test_creates_experiment_and_yields_the_active_run(self):
        name = "test_tracking_task_run"
        assert mlflow.get_experiment_by_name(name) is None
        with task_run(name, run_name="r1", tags={"strategy": "naive"}) as run:
            assert mlflow.active_run() is run
        experiment = mlflow.get_experiment_by_name(name)
        assert experiment is not None
        assert run.info.experiment_id == experiment.experiment_id

    def test_run_name_and_tags_are_recorded(self):
        with task_run(
            EXPERIMENT, run_name="tagged", tags={"strategy": "naive", "area": "tokyo"}
        ) as run:
            run_id = run.info.run_id
        stored = mlflow.get_run(run_id)
        assert stored.info.run_name == "tagged"
        assert stored.data.tags["strategy"] == "naive"
        assert stored.data.tags["area"] == "tokyo"

    def test_run_is_closed_on_exit(self):
        with task_run(EXPERIMENT) as run:
            run_id = run.info.run_id
        assert mlflow.active_run() is None
        assert mlflow.get_run(run_id).info.status == "FINISHED"

    def test_existing_experiment_is_reused(self):
        with task_run(EXPERIMENT) as first:
            pass
        with task_run(EXPERIMENT) as second:
            pass
        assert first.info.experiment_id == second.info.experiment_id
        assert first.info.run_id != second.info.run_id


class TestLogDataframe:
    def test_logs_csv_artifact_without_index(self):
        df = pd.DataFrame({"trade_date": ["2024-01-01", "2024-01-02"], "mae": [1.5, 2.0]})
        with task_run(EXPERIMENT) as run:
            log_dataframe(df, "daily_errors.csv")
        run_id = run.info.run_id
        assert [f.path for f in mlflow.MlflowClient().list_artifacts(run_id)] == [
            "daily_errors.csv"
        ]
        path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="daily_errors.csv")
        pd.testing.assert_frame_equal(pd.read_csv(path), df)


class TestMapeMetric:
    def test_metric_definition(self):
        metric = mape_metric()
        assert metric.name == "mape_excl_zero_actuals" == MAPE_METRIC_NAME
        assert metric.greater_is_better is False
        assert metric.eval_fn is _mape_excl_zero_actuals

    def test_eval_fn_returns_metric_value_keyed_by_metric_name(self):
        # targets [0, 2, 4] vs predictions [1, 3, 5]: the zero row is dropped,
        # (0.5 + 0.25) / 2 -> 37.5 %.
        value = _mape_excl_zero_actuals(
            predictions=pd.Series([1.0, 3.0, 5.0]), targets=pd.Series([0.0, 2.0, 4.0])
        )
        assert isinstance(value, MetricValue)
        assert value.aggregate_results == {"mape_excl_zero_actuals": 37.5}
        assert value.scores is None

    def test_eval_fn_divides_by_targets_not_predictions(self):
        # mape(targets=[1, 2], predictions=[2, 4]) = (1/1 + 2/2) / 2 = 100 %;
        # swapped it would be (1/2 + 2/4) / 2 = 50 %.
        value = _mape_excl_zero_actuals(
            predictions=pd.Series([2.0, 4.0]), targets=pd.Series([1.0, 2.0])
        )
        assert value.aggregate_results["mape_excl_zero_actuals"] == 100.0


class TestEvaluatePredictions:
    def test_static_dataset_metrics_and_zero_actual_exclusion(self):
        # |0-1| + |2-3| + |4-5| + |5-5| = 3 over 4 rows -> MAE 0.75.
        # MAPE excluding the zero actual: (1/2 + 1/4 + 0/5) / 3 = 25 %.
        data = pd.DataFrame(
            {
                "feature": [1.0, 2.0, 3.0, 4.0],
                "actual": [0.0, 2.0, 4.0, 5.0],
                "pred": [1.0, 3.0, 5.0, 5.0],
            }
        )
        with task_run(EXPERIMENT) as run:
            result = evaluate_predictions(data, targets="actual", predictions="pred")
        assert result.metrics["mean_absolute_error"] == 0.75
        assert result.metrics["mape_excl_zero_actuals"] == 25.0
        assert result.metrics["example_count"] == 4
        # No model, hence no SHAP artifacts in static mode.
        assert result.artifacts == {}
        # The extra metric lands on the run under its bare name.
        logged = mlflow.get_run(run.info.run_id).data.metrics
        assert logged["mape_excl_zero_actuals"] == 25.0
        assert logged["mean_absolute_error"] == 0.75


def lag_frame(n: int = 60) -> pd.DataFrame:
    """Actual 10..69; the lag over-forecasts by 10 % on even rows, under on odd.

    Every |error| / actual is 0.1, so MAPE = 10 % and
    MAE = 0.1 * mean(actual) = 0.1 * 39.5 = 3.95.
    """
    actual = np.array([10.0 + i for i in range(n)])
    lag = actual * np.where(np.arange(n) % 2 == 0, 1.1, 0.9)
    return pd.DataFrame(
        {"lag_1d_price": lag, "lag_7d_price": lag + 1.0, "actual_price_jpy_kwh": actual}
    )


@pytest.fixture(scope="module")
def previous_day_model_uri() -> str:
    """A logged pyfunc model whose prediction is the ``lag_1d_price`` column."""
    with task_run(EXPERIMENT):
        info = mlflow.pyfunc.log_model(
            name="pdm",
            python_model=PreviousDayModel(),
            input_example=lag_frame().drop(columns=["actual_price_jpy_kwh"]).head(),
        )
    return info.model_uri


class TestEvaluateRegressor:
    def test_metrics_and_shap_artifacts(self, previous_day_model_uri):
        with task_run(EXPERIMENT) as run:
            result = evaluate_regressor(
                previous_day_model_uri,
                lag_frame(),
                targets="actual_price_jpy_kwh",
                explainability_nsamples=30,
            )
        assert result.metrics["mean_absolute_error"] == pytest.approx(3.95)
        assert result.metrics["mape_excl_zero_actuals"] == pytest.approx(10.0)
        assert result.metrics["example_count"] == 60
        assert sorted(result.artifacts) == [
            "shap_beeswarm_plot",
            "shap_feature_importance_plot",
            "shap_summary_plot",
        ]
        run_id = run.info.run_id
        assert sorted(f.path for f in mlflow.MlflowClient().list_artifacts(run_id)) == [
            "shap_beeswarm_plot.png",
            "shap_feature_importance_plot.png",
            "shap_summary_plot.png",
        ]
        # log_explainer defaults to False: nothing but the plots is logged.
        assert logged_model_names(run) == []

    def test_explainer_kwargs_are_forwarded(self, previous_day_model_uri):
        with task_run(EXPERIMENT) as run:
            result = evaluate_regressor(
                previous_day_model_uri,
                lag_frame(),
                targets="actual_price_jpy_kwh",
                explainability_algorithm="permutation",
                explainability_nsamples=12,
                log_explainer=True,
            )
        assert result.metrics["mean_absolute_error"] == pytest.approx(3.95)
        assert logged_model_names(run) == ["explainer"]
        explainer = mlflow.shap.load_explainer(f"models:/{logged_models(run)[0].model_id}")
        assert isinstance(explainer, shap.explainers.Permutation)
        # The background data is the requested sample of the 2 feature columns.
        assert explainer.masker.data.shape == (12, 2)


def logged_models(run: mlflow.ActiveRun) -> list:
    return mlflow.search_logged_models(
        experiment_ids=[run.info.experiment_id],
        filter_string=f"source_run_id = '{run.info.run_id}'",
        output_format="list",
    )


def logged_model_names(run: mlflow.ActiveRun) -> list[str]:
    return sorted(m.name for m in logged_models(run))
