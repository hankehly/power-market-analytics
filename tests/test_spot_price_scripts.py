"""End-to-end CLI tests for the spot-price backtest and run-comparison scripts.

Both scripts run for real against the synthetic ``pma_curated`` warehouse
(``curated_warehouse`` fixture) and the session's temp MLflow file store:
``spot_price_backtest.py`` fits/scores, logs the MLflow run and publishes the
forecasts to ``pma_ml.spot_price_forecast``; ``compare_spot_price_runs.py``
reads the accuracy fact and prints the markdown report.
"""

from __future__ import annotations

import mlflow
import pandas as pd
import pytest
from pyspark.sql import functions as F

from tests.conftest import BASELINE_RUN_ID, CANDIDATE_RUN_ID, synthetic_price
from tests.support import import_script

FORECAST_TABLE = "pma_ml.spot_price_forecast"


def last_run() -> mlflow.entities.Run:
    """The run the script just finished (or failed), re-read from the store."""
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


# --------------------------------------------------------------------------- compare script


class TestCompareScript:
    def test_both_run_ids_are_required(self):
        script = import_script("compare_spot_price_runs")
        with pytest.raises(SystemExit) as exc:
            script.main(["--baseline", BASELINE_RUN_ID])
        assert exc.value.code == 2

    def test_prints_every_section_as_markdown(self, spark, curated_warehouse, capsys):
        script = import_script("compare_spot_price_runs")
        script.main(["--baseline", BASELINE_RUN_ID, "--candidate", CANDIDATE_RUN_ID])
        out = capsys.readouterr().out
        lines = out.splitlines()

        assert lines[0] == "Baseline run: `run-baseline`  "
        assert lines[1] == "Candidate run: `run-candidate`"
        assert [line for line in lines if line.startswith("### ")] == [
            "### Overall",
            "### By day part",
            "### Near the OCCTO forecast maximum-demand hour",
            "### Mean error (forecast − actual)",
            "### By calendar month",
            "### High-price days",
        ]
        # every section carries a table header naming its metric, then the divider row
        assert out.count("| Segment | n | Baseline MAE (JPY/kWh) | Candidate MAE (JPY/kWh) |") == 5
        assert (
            out.count("| Segment | n | Baseline bias (JPY/kWh) | Candidate bias (JPY/kWh) |") == 1
        )
        assert out.count("|---|---:|---:|---:|---:|---:|") == 6
        # fixture errors: baseline +1.0/-0.5 alternating (MAE 0.75), candidate half of that
        assert "| all | 1,008 | 0.750 | 0.375 | -0.375 | -50.0% |" in lines
        assert "| all | 1,008 | 0.250 | 0.125 | -0.125 | — |" in lines
        assert "| within ±1 h of forecast peak hour |" in out
        assert "| top 10% price days (daily mean >=" in out
        assert "| 2024-04 | 1,008 |" in out

    def test_segment_options_reach_compare_runs(self, spark, curated_warehouse, capsys):
        script = import_script("compare_spot_price_runs")
        script.main(
            [
                "--baseline",
                BASELINE_RUN_ID,
                "--candidate",
                CANDIDATE_RUN_ID,
                "--near-peak-hours",
                "0",
                "--high-price-quantile",
                "0.5",
            ]
        )
        out = capsys.readouterr().out
        assert "| forecast peak hour only |" in out
        assert "within ±" not in out
        assert "| top 50% price days (daily mean >=" in out
        assert "| other 50% of days |" in out


# --------------------------------------------------------------------------- backtest script


class TestBacktestScript:
    def test_previous_day_over_a_pinned_window(self, spark, curated_warehouse):
        script = import_script("spot_price_backtest")
        script.main(
            [
                "--strategy",
                "previous_day",
                "--area",
                "tokyo",
                "--start-date",
                "2024-05-01",
                "--end-date",
                "2024-05-03",
                "--shap-nsamples",
                "20",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "previous_day-tokyo"
        assert mlflow.get_experiment(run.info.experiment_id).name == "spot_price"

        params = run.data.params
        assert params["strategy"] == "previous_day"
        assert params["area"] == "tokyo"
        assert params["start_date"] == "2024-05-01"
        assert params["end_date"] == "2024-05-03"
        assert params["n_days"] == "3"
        assert params["n_predictions"] == "144"
        assert params["n_days_skipped"] == "0"
        assert run.data.tags["strategy"] == "previous_day"
        assert run.data.tags["area"] == "tokyo"
        assert run.data.tags["warehouse_table"] == "pma_ml.spot_price_forecast"

        artifacts = artifact_names(run.info.run_id)
        assert {
            "daily_errors.csv",
            "predictions.csv",
            "error_heatmaps_year_time_code.html",
        } <= artifacts
        # the MLflow evaluation's SHAP plots land in the same run
        assert {"shap_beeswarm_plot.png", "shap_summary_plot.png"} <= artifacts
        daily = pd.read_csv(
            mlflow.artifacts.download_artifacts(
                run_id=run.info.run_id, artifact_path="daily_errors.csv"
            )
        )
        assert daily["trade_date"].tolist() == ["2024-05-01", "2024-05-02", "2024-05-03"]
        assert list(daily.columns) == ["trade_date", "mae", "mape"]

        # The MLflow evaluation re-scores the same rule, so its MAE is the mean absolute
        # day-over-day price change of the window (from the fixture's price function).
        days = pd.date_range("2024-05-01", "2024-05-03", freq="D")
        expected_mae = sum(
            abs(synthetic_price(d - pd.Timedelta(days=1), tc) - synthetic_price(d, tc))
            for d in days
            for tc in range(1, 49)
        ) / (3 * 48)
        assert run.data.metrics["mean_absolute_error"] == pytest.approx(expected_mae)
        assert "mape_excl_zero_actuals" in run.data.metrics
        assert run.data.metrics["mape_excl_zero_actuals"] > 0

        published = published_rows(spark, run.info.run_id)
        assert len(published) == 144
        assert set(published["strategy"]) == {"previous_day"}
        assert set(published["area_code"]) == {"tokyo"}
        assert published["trade_date"].min() == pd.Timestamp("2024-05-01").date()
        assert published["trade_date"].max() == pd.Timestamp("2024-05-03").date()
        first = published.iloc[0]
        # 2024-05-01 tc 1 forecast = 2024-04-30 tc 1 price: day_index 60, weekday, shape
        # 12.0, wobble (420 + 13) % 11 = 4 -> 12.4; issued at 09:55 JST the day before.
        assert first["time_code"] == 1
        assert first["forecast_price_jpy_kwh"] == 12.4
        assert first["forecast_issued_ts"] == pd.Timestamp("2024-04-30 09:55")

    def test_days_window_ends_at_the_last_day_in_the_data(self, spark, curated_warehouse):
        script = import_script("spot_price_backtest")
        script.main(["--days", "2", "--shap-nsamples", "20"])
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.data.params["strategy"] == "previous_day"
        assert run.data.params["start_date"] == "2024-05-30"
        assert run.data.params["end_date"] == "2024-05-31"
        assert run.data.params["n_days"] == "2"
        assert run.data.params["n_predictions"] == "96"
        assert len(published_rows(spark, run.info.run_id)) == 96

    def test_lightgbm_receives_train_start(self, spark, curated_warehouse):
        script = import_script("spot_price_backtest")
        script.main(
            [
                "--strategy",
                "lightgbm",
                "--days",
                "2",
                "--train-start",
                "2024-04-01",
                "--shap-nsamples",
                "20",
            ]
        )
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "lightgbm-tokyo"
        assert run.data.params["strategy"] == "lightgbm"
        assert run.data.params["lgbm_train_start_date"] == "2024-04-01"
        assert run.data.params["n_predictions"] == "96"
        assert run.data.metrics["n_refits"] == 1.0
        assert "mean_absolute_error" in run.data.metrics
        artifacts = artifact_names(run.info.run_id)
        assert {
            "shap_beeswarm_plot.png",
            "shap_feature_importance_plot.png",
            "predictions.csv",
        } <= artifacts
        published = published_rows(spark, run.info.run_id)
        assert len(published) == 96
        assert set(published["strategy"]) == {"lightgbm"}

    def test_end_date_after_the_data_is_rejected(self, spark, curated_warehouse):
        script = import_script("spot_price_backtest")
        with pytest.raises(SystemExit) as exc:
            script.main(["--end-date", "2030-01-01"])
        assert exc.value.code == 2
        assert last_run().info.status == "FAILED"

    def test_start_after_end_is_rejected(self, spark, curated_warehouse, capsys):
        script = import_script("spot_price_backtest")
        with pytest.raises(SystemExit) as exc:
            script.main(["--start-date", "2024-05-05", "--end-date", "2024-05-01"])
        assert exc.value.code == 2
        assert "start date 2024-05-05 is after end date 2024-05-01" in capsys.readouterr().err
