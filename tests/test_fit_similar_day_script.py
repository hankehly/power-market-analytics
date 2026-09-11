"""scripts/fit_similar_day.py: fit the similar-day weights, score every day, publish.

The warehouse loaders are swapped for the synthetic frames of
``tests.test_demand_similar_day`` in the script's namespace; the fit, the
selection, the retrieval check and the write-back run for real.
"""

from __future__ import annotations

import mlflow
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    PopulationWeightedObservedWeather,
    PopulationWeightedWeatherForecast,
)
from power_market_analytics.tasks.demand.similar_day import PERIODS_PER_HOUR
from power_market_analytics.tasks.demand.similar_day_feature import FEATURE_TABLE
from tests.support import import_script
from tests.test_demand_similar_day import (
    FORECAST_DAYS,
    HISTORY_DAYS,
    HOLIDAYS,
    forecast_available_at,
    load_at,
    make_calendar,
    make_forecast,
    make_hourly_load,
    make_observed,
)

RETRIEVAL_METRICS = (
    "similar_day_load_difference_selected",
    "similar_day_load_difference_lag_364",
    "similar_day_load_difference_oracle",
    "similar_day_share_better_than_lag_364",
)


def last_run() -> mlflow.entities.Run:
    run = mlflow.last_active_run()
    assert run is not None
    assert mlflow.get_experiment(run.info.experiment_id).name == "similar_day"
    return mlflow.get_run(run.info.run_id)


def artifact(run_id: str, name: str) -> pd.DataFrame:
    return pd.read_csv(mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=name))


def published_rows(spark, run_id: str) -> pd.DataFrame:
    return spark.table(FEATURE_TABLE).where(f"run_id = '{run_id}'").toPandas()


@pytest.fixture
def script(monkeypatch):
    module = import_script("fit_similar_day")
    calls: dict[str, dict] = {}

    def forecast_loader(area_code="tokyo", census_year=None, spark=None):
        calls["forecast"] = {"area_code": area_code, "census_year": census_year}
        return PopulationWeightedWeatherForecast(make_forecast(), census_year=2020, n_stations=2)

    def observed_loader(area_code="tokyo", census_year=None, spark=None):
        calls["observed"] = {"area_code": area_code, "census_year": census_year}
        return PopulationWeightedObservedWeather(make_observed(), census_year=2020, n_stations=2)

    monkeypatch.setattr(module, "load_area_weather_forecast_population_weighted", forecast_loader)
    monkeypatch.setattr(module, "load_area_observed_weather_population_weighted", observed_loader)
    monkeypatch.setattr(module, "load_day_calendar", lambda spark=None: make_calendar())
    monkeypatch.setattr(
        module, "load_area_hourly_load", lambda area_code="tokyo", spark=None: make_hourly_load()
    )
    module.calls = calls
    return module


class TestFitScript:
    def test_fits_through_the_given_day_and_publishes_the_row(self, spark, script):
        script.main(["--area", "tokyo", "--fit-through", "2024-03-31"])
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "similar_day-tokyo"
        assert run.data.tags["area"] == "tokyo"
        assert run.data.tags["feature_table"] == FEATURE_TABLE
        assert script.calls["observed"] == {"area_code": "tokyo", "census_year": 2020}

        params = run.data.params
        assert params["area"] == "tokyo"
        assert params["fit_through"] == "2024-03-31"
        assert params["population_weight_census_year"] == "2020"
        assert params["n_stations"] == "2"
        assert params["similar_day_center_lag_days"] == "364"
        assert params["similar_day_window_half_width_days"] == "30"
        assert params["similar_day_components"].startswith("calendar_days,temperature,")
        assert params["similar_day_fit_through"] == "2024-03-31"
        assert params["similar_day_fit_from"] == "2024-02-07"
        assert params["similar_day_first_selectable_day"] == "2024-02-07"
        assert params["similar_day_hourly_load_span"] == "2023-01-01..2024-04-30"
        assert params["similar_day_periods_per_hour"] == "2"
        assert params["similar_day_weights"].startswith("calendar_days=")

        # Every scorable day is selected and written: 48 periods of its similar
        # day's load halved, available once its forecast is.
        scorable = [d for d in FORECAST_DAYS if pd.Timestamp("2024-02-07") <= d <= HOLIDAYS[-1]]
        assert params["n_days_scored"] == str(len(scorable))
        assert params["first_day_scored"] == "2024-02-07"
        assert params["last_day_scored"] == str(HOLIDAYS[-1].date())
        selection = artifact(run.info.run_id, "similar_day_selection.csv")
        assert selection["trade_date"].tolist() == [str(d.date()) for d in scorable]
        rows = published_rows(spark, run.info.run_id)
        assert len(rows) == 48 * len(scorable)
        assert set(rows["area_code"]) == {"tokyo"}
        chosen = selection.set_index("trade_date")["reference_date"]
        by_period = rows.set_index(["trade_date", "time_code"]).sort_index()
        day = pd.Timestamp("2024-04-10")
        reference = pd.Timestamp(chosen[str(day.date())])
        row = by_period.loc[(day.date(), 7)]
        assert row["similar_day_reference_date"] == reference.date()
        assert row["similar_day_demand_kwh"] == load_at(reference, 4) / PERIODS_PER_HOUR
        assert row["similar_day_reference_lag_days"] == (day - reference).days
        assert row["available_at"] == forecast_available_at(day)
        assert rows["published_at"].nunique() == 1
        retrieval = artifact(run.info.run_id, "similar_day_retrieval.csv")
        assert retrieval["trade_date"].tolist() == [
            str(d.date()) for d in scorable if d in HISTORY_DAYS
        ]
        assert list(retrieval.columns)[-1] == "in_fit"
        assert retrieval["in_fit"].tolist() == [
            d <= pd.Timestamp("2024-03-31") for d in pd.to_datetime(retrieval["trade_date"])
        ]
        # The four metrics judge the selector on the days after the fit.
        assert set(RETRIEVAL_METRICS) <= set(run.data.metrics)
        out_of_sample = retrieval[~retrieval["in_fit"]]
        assert run.data.metrics["similar_day_load_difference_selected"] == pytest.approx(
            out_of_sample["selected_load_difference"].mean()
        )

    def test_defaults_to_the_last_loaded_day_and_logs_no_out_of_sample_metrics(self, spark, script):
        script.main([])
        run = last_run()
        assert run.data.params["area"] == "tokyo"
        assert run.data.params["fit_through"] == "2024-04-30"
        # The last day with a pair: the calendar ends at its last holiday, 04-29.
        assert run.data.params["similar_day_fit_through"] == "2024-04-29"
        assert not set(RETRIEVAL_METRICS) & set(run.data.metrics)
        retrieval = artifact(run.info.run_id, "similar_day_retrieval.csv")
        assert retrieval["in_fit"].all()
        assert len(published_rows(spark, run.info.run_id)) == 48 * 83

    def test_window_half_width_reaches_the_selector_and_the_row(self, spark, script):
        script.main(["--fit-through", "2024-03-31", "--window-half-width-days", "10"])
        run = last_run()
        assert run.data.params["similar_day_window_half_width_days"] == "10"
        rows = published_rows(spark, run.info.run_id)
        assert rows["similar_day_reference_lag_days"].between(354, 374).all()
        assert rows["similar_day_n_candidates"].max() <= 21

    def test_a_fit_without_pairs_fails_before_publishing(self, spark, script):
        with pytest.raises(ValueError, match="no training pairs"):
            script.main(["--fit-through", "2023-12-31"])
