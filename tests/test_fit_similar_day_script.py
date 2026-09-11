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
    def test_walks_forward_weekly_and_publishes_the_rows(self, spark, script):
        script.main(["--area", "tokyo"])
        run = last_run()
        assert run.info.status == "FINISHED"
        assert run.info.run_name == "similar_day-tokyo"
        assert run.data.tags["area"] == "tokyo"
        assert run.data.tags["feature_table"] == FEATURE_TABLE
        assert script.calls["observed"] == {"area_code": "tokyo", "census_year": 2020}

        params = run.data.params
        assert params["area"] == "tokyo"
        assert params["refit_every_days"] == "7"
        # Loads are public at midnight after the day, so the first fit runs on 02-08.
        assert params["first_fit_cutoff"] == "2024-02-08 00:00:00"
        assert params["last_fit_cutoff"] == "2024-04-25 00:00:00"
        assert params["n_fits"] == "12"
        assert params["first_day_scored"] == "2024-02-09"
        assert params["last_day_scored"] == str(HOLIDAYS[-1].date())
        assert params["population_weight_census_year"] == "2020"
        assert params["n_stations"] == "2"
        assert params["similar_day_center_lag_days"] == "364"
        assert params["similar_day_window_half_width_days"] == "30"
        assert params["similar_day_components"].startswith("calendar_days,temperature,")
        # The last fit's params.
        assert params["similar_day_fit_through"] == "2024-04-24"
        assert params["similar_day_fit_from"] == "2024-02-07"
        assert params["similar_day_first_selectable_day"] == "2024-02-07"
        assert params["similar_day_hourly_load_span"] == "2023-01-01..2024-04-30"
        assert params["similar_day_weights"].startswith("calendar_days=")

        scored = [d for d in FORECAST_DAYS if pd.Timestamp("2024-02-09") <= d <= HOLIDAYS[-1]]
        assert params["n_days_scored"] == str(len(scored))
        fits = artifact(run.info.run_id, "similar_day_fits.csv")
        assert len(fits) == 12
        assert fits["n_days_scored"].sum() == len(scored)
        selection = artifact(run.info.run_id, "similar_day_selection.csv")
        assert selection["trade_date"].tolist() == [str(d.date()) for d in scored]
        assert list(selection.columns)[-1] == "fit_cutoff"
        retrieval = artifact(run.info.run_id, "similar_day_retrieval.csv")
        assert retrieval["trade_date"].tolist() == [
            str(d.date()) for d in scored if d in HISTORY_DAYS
        ]
        assert set(RETRIEVAL_METRICS) <= set(run.data.metrics)
        assert run.data.metrics["similar_day_load_difference_selected"] == pytest.approx(
            retrieval["selected_load_difference"].mean()
        )

        rows = published_rows(spark, run.info.run_id)
        assert len(rows) == 48 * len(scored)
        assert set(rows["area_code"]) == {"tokyo"}
        chosen = selection.set_index("trade_date")
        by_period = rows.set_index(["trade_date", "time_code"]).sort_index()
        day = pd.Timestamp("2024-04-10")
        reference = pd.Timestamp(chosen.loc[str(day.date()), "reference_date"])
        row = by_period.loc[(day.date(), 7)]
        assert row["similar_day_reference_date"] == reference.date()
        assert row["similar_day_demand_kwh"] == load_at(reference, 4) / PERIODS_PER_HOUR
        assert row["similar_day_reference_lag_days"] == (day - reference).days
        assert row["similar_day_fit_cutoff"] == pd.Timestamp(
            chosen.loc[str(day.date()), "fit_cutoff"]
        )
        # The fit ran before the day's issue time, 09:30 the day before.
        assert row["similar_day_fit_cutoff"] <= day - pd.Timedelta(days=1) + pd.Timedelta(
            hours=9, minutes=30
        )
        assert row["available_at"] == forecast_available_at(day)
        assert rows["published_at"].nunique() == 1

    def test_cadence_reaches_the_job(self, spark, script):
        script.main(["--refit-every-days", "30"])
        run = last_run()
        assert run.data.params["refit_every_days"] == "30"
        assert run.data.params["n_fits"] == "3"
        assert len(published_rows(spark, run.info.run_id)) == 48 * int(
            run.data.params["n_days_scored"]
        )

    def test_window_half_width_reaches_the_selector(self, spark, script):
        script.main(["--window-half-width-days", "10"])
        run = last_run()
        assert run.data.params["similar_day_window_half_width_days"] == "10"
        rows = published_rows(spark, run.info.run_id)
        assert rows["similar_day_reference_lag_days"].between(354, 374).all()
        assert rows["similar_day_n_candidates"].max() <= 21

    def test_scored_days_without_a_load_yet_log_no_metrics(self, spark, script, monkeypatch):
        # Loads end on the first fittable day: every scored day is still unknown.
        monkeypatch.setattr(
            script,
            "load_area_hourly_load",
            lambda area_code="tokyo", spark=None: make_hourly_load(
                pd.date_range(HISTORY_DAYS[0], "2024-02-07")
            ),
        )
        script.main([])
        run = last_run()
        assert run.data.params["first_day_scored"] == "2024-02-09"
        assert not set(RETRIEVAL_METRICS) & set(run.data.metrics)
        assert artifact(run.info.run_id, "similar_day_retrieval.csv").empty
        assert len(published_rows(spark, run.info.run_id)) == 48 * int(
            run.data.params["n_days_scored"]
        )

    def test_cadence_below_one_is_rejected(self, script, capsys):
        with pytest.raises(SystemExit):
            script.main(["--refit-every-days", "0"])
        assert "--refit-every-days must be >= 1" in capsys.readouterr().err
