"""End-to-end CLI tests for the demand backtest script.

Runs for real against the synthetic ``pma_curated`` warehouse
(``curated_warehouse`` fixture) and the session's temp MLflow file store:
fits/scores, logs the MLflow run and publishes to ``pma_ml.demand_forecast``.
"""

from __future__ import annotations

import mlflow
import pandas as pd
import pytest
from pyspark.sql import functions as F

from tests.conftest import DEMAND_HOLE_DAY, DEMAND_HOLE_TIME_CODES, FORECAST_MISSING_DAY
from tests.support import import_script

FORECAST_TABLE = "pma_ml.demand_forecast"


def last_run() -> mlflow.entities.Run:
    run = mlflow.last_active_run()
    assert run is not None
    return mlflow.get_run(run.info.run_id)


def artifact_names(run_id: str) -> set[str]:
    return {info.path for info in mlflow.MlflowClient().list_artifacts(run_id)}


def published_rows(spark, run_id: str) -> pd.DataFrame:
    return (
        spark.table(FORECAST_TABLE)
        .filter(F.col("run_id") == run_id)
        .toPandas()
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )


class TestBacktestScript:
    def test_lightgbm_over_a_pinned_window(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(
            [
                "--strategy",
                "lightgbm",
                "--area",
                "tokyo",
                "--start-date",
                "2024-04-10",
                "--end-date",
                "2024-04-12",
                "--shap-nsamples",
                "20",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "lightgbm-tokyo"
        assert mlflow.get_experiment(run.info.experiment_id).name == "demand"

        params = run.data.params
        assert params["strategy"] == "lightgbm"
        assert params["area"] == "tokyo"
        assert params["start_date"] == "2024-04-10"
        assert params["end_date"] == "2024-04-12"
        assert params["n_days"] == "3"
        assert params["n_predictions"] == "144"
        assert params["n_days_skipped"] == "0"
        assert params["temperature_lag_days"] == "2,3,4,5,6,7,8"
        assert run.data.tags["strategy"] == "lightgbm"
        assert run.data.tags["area"] == "tokyo"
        assert run.data.tags["warehouse_table"] == FORECAST_TABLE
        assert run.data.metrics["n_refits"] == 1.0
        assert "mean_absolute_error" in run.data.metrics
        assert "mape_excl_zero_actuals" in run.data.metrics

        artifacts = artifact_names(run.info.run_id)
        assert {
            "daily_errors.csv",
            "predictions.csv",
            "error_heatmaps_year_time_code.html",
            "shap_beeswarm_plot.png",
            "shap_feature_importance_plot.png",
        } <= artifacts
        daily = pd.read_csv(
            mlflow.artifacts.download_artifacts(
                run_id=run.info.run_id, artifact_path="daily_errors.csv"
            )
        )
        assert daily["trade_date"].tolist() == ["2024-04-10", "2024-04-11", "2024-04-12"]
        assert list(daily.columns) == ["trade_date", "mae", "mape"]

        published = published_rows(spark, run.info.run_id)
        assert len(published) == 144
        assert set(published["strategy"]) == {"lightgbm"}
        assert set(published["area_code"]) == {"tokyo"}
        assert list(published.columns) == [
            "strategy",
            "area_code",
            "forecast_issued_ts",
            "trade_date",
            "time_code",
            "forecast_demand_kwh",
            "published_at",
            "run_id",
        ]
        first = published.iloc[0]
        assert first["trade_date"] == pd.Timestamp("2024-04-10").date()
        assert first["time_code"] == 1
        # Issued at 09:30 JST the day before delivery.
        assert first["forecast_issued_ts"] == pd.Timestamp("2024-04-09 09:30")

    def test_hole_day_is_partly_scored_and_its_d7_successor_skipped(self, spark, curated_warehouse):
        # 2024-04-20 has actuals for time codes 1..10 only (48 forecasts, 10 scored);
        # 2024-04-27 cannot be forecast (its D-7 lag is the hole) and is skipped.
        script = import_script("demand_backtest")
        start = DEMAND_HOLE_DAY - pd.Timedelta(days=1)
        end = DEMAND_HOLE_DAY + pd.Timedelta(days=7)
        script.main(
            [
                "--start-date",
                str(start.date()),
                "--end-date",
                str(end.date()),
                "--shap-nsamples",
                "20",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        params = run.data.params
        assert params["n_days"] == "8"  # 9 calendar days, one skipped
        assert params["n_days_skipped"] == "1"
        assert params["n_predictions"] == str(8 * 48 - len(DEMAND_HOLE_TIME_CODES))
        published = published_rows(spark, run.info.run_id)
        assert published["trade_date"].nunique() == 8
        assert pd.Timestamp("2024-04-27").date() not in set(published["trade_date"])
        assert (published["trade_date"] == DEMAND_HOLE_DAY.date()).sum() == 10

    def test_lightgbm_msm_skips_the_day_without_a_temperature_forecast(
        self, spark, curated_warehouse
    ):
        # 2024-05-15 has no MSM forecast rows: the candidate strategy cannot
        # forecast it and skips it, while the surrounding days are scored.
        script = import_script("demand_backtest")
        start = FORECAST_MISSING_DAY - pd.Timedelta(days=1)
        end = FORECAST_MISSING_DAY + pd.Timedelta(days=1)
        script.main(
            [
                "--strategy",
                "lightgbm_msm",
                "--area",
                "tokyo",
                "--start-date",
                str(start.date()),
                "--end-date",
                str(end.date()),
                "--shap-nsamples",
                "20",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "lightgbm_msm-tokyo"
        params = run.data.params
        assert params["strategy"] == "lightgbm_msm"
        assert params["n_days"] == "2"
        assert params["n_days_skipped"] == "1"
        assert params["n_predictions"] == "96"
        assert params["lgbm_feature_cols"] == (
            "time_code,month,day_of_week,wavg_temperature_c,lag_7d_demand_kwh,"
            "forecast_temperature_c"
        )
        assert run.data.tags["strategy"] == "lightgbm_msm"
        published = published_rows(spark, run.info.run_id)
        assert len(published) == 96
        assert set(published["strategy"]) == {"lightgbm_msm"}
        assert FORECAST_MISSING_DAY.date() not in set(published["trade_date"])

    def test_days_window_ends_at_the_last_day_in_the_data(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(["--days", "2", "--shap-nsamples", "20"])
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.data.params["start_date"] == "2024-05-30"
        assert run.data.params["end_date"] == "2024-05-31"
        assert run.data.params["n_predictions"] == "96"

    def test_train_start_reaches_the_strategy(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        script.main(["--days", "2", "--train-start", "2024-04-01", "--shap-nsamples", "20"])
        assert last_run().data.params["lgbm_train_start_date"] == "2024-04-01"

    def test_end_date_after_the_data_is_rejected(self, spark, curated_warehouse):
        script = import_script("demand_backtest")
        with pytest.raises(SystemExit) as exc:
            script.main(["--end-date", "2030-01-01"])
        assert exc.value.code == 2
        assert last_run().info.status == "FAILED"

    def test_start_after_end_is_rejected(self, spark, curated_warehouse, capsys):
        script = import_script("demand_backtest")
        with pytest.raises(SystemExit) as exc:
            script.main(["--start-date", "2024-05-05", "--end-date", "2024-05-01"])
        assert exc.value.code == 2
        assert "start date 2024-05-05 is after end date 2024-05-01" in capsys.readouterr().err
