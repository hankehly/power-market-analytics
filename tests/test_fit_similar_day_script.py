"""scripts/fit_similar_day.py: fit the similar-day weights, score every day, publish.

The warehouse loaders are swapped for the synthetic frames of
``tests.test_demand_similar_day`` in the script's namespace; the fit, the
ranking, the special days, the retrieval check and the write-back run for real.
"""

from __future__ import annotations

import dataclasses

import mlflow
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    PopulationWeightedObservedWeather,
    PopulationWeightedWeatherForecast,
)
from power_market_analytics.tasks.demand.similar_day import (
    PERIODS_PER_HOUR,
    SimilarDayRanking,
    SimilarDaySelection,
)
from power_market_analytics.tasks.demand.similar_day_feature import (
    FEATURE_TABLE,
    METHOD_SAME_HOLIDAY,
    METHOD_SIMILARITY,
)
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
    "similar_day_load_difference_lag_7",
    "similar_day_load_difference_oracle",
    "similar_day_share_better_than_lag_7",
)
#: The synthetic calendar's special days in the scored span: 春分の日 takes 2023-03-21,
#: 365 days back; 昭和の日 has no 2023 day of that name, so it is ranked.
SAME_HOLIDAY_DAY = pd.Timestamp("2024-03-20")
SAME_HOLIDAY_REFERENCE = pd.Timestamp("2023-03-21")
RANKED_SPECIAL_DAY = pd.Timestamp("2024-04-29")


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
        assert params["n_cutoffs_without_fit"] == "0"
        assert params["first_day_scored"] == "2024-02-09"
        assert params["last_day_scored"] == str(HOLIDAYS[-1].date())
        assert params["population_weight_census_year"] == "2020"
        assert params["n_stations"] == "2"
        assert params["similar_day_pool"] == "2-31,335-394"
        assert params["similar_day_top_k"] == "3"
        assert "similar_day_center_lag_days" not in params
        assert "similar_day_window_half_width_days" not in params
        assert params["similar_day_fit_window_days"] == "730"
        assert params["similar_day_components"].startswith("calendar_days,temperature,")
        # The last fit's params.
        assert params["similar_day_fit_through"] == "2024-04-24"
        assert params["similar_day_fit_from"] == "2024-02-07"
        assert params["similar_day_first_selectable_day"] == "2024-02-07"
        assert params["similar_day_hourly_load_span"] == "2023-01-01..2024-04-30"
        assert params["similar_day_weights"].startswith("calendar_days=")

        # Every forecast day from 02-09 to the calendar's last day, 04-29, is published
        # and ranked; 03-20's base columns take its same holiday, its pool columns
        # keep the ranking.
        published = [d for d in FORECAST_DAYS if pd.Timestamp("2024-02-09") <= d <= HOLIDAYS[-1]]
        ranked = published
        assert len(published) == 81
        assert params["n_days_scored"] == "81"
        # Every scored day is ranked now; the same-holiday days are a subset of
        # them, the days whose base columns took a reference instead.
        assert params["n_days_ranked"] == "81"
        assert params["n_special_days_ranked"] == "2"
        assert params["n_days_same_holiday"] == "1"
        fits = artifact(run.info.run_id, "similar_day_fits.csv")
        assert len(fits) == 12
        assert fits["n_days_scored"].sum() == len(ranked)
        selection = artifact(run.info.run_id, "similar_day_selection.csv")
        assert selection["trade_date"].tolist() == [str(d.date()) for d in ranked]
        assert list(selection.columns)[-1] == "fit_cutoff"
        retrieval = artifact(run.info.run_id, "similar_day_retrieval.csv")
        assert retrieval["trade_date"].tolist() == [
            str(d.date()) for d in ranked if d in HISTORY_DAYS
        ]
        assert set(RETRIEVAL_METRICS) <= set(run.data.metrics)
        assert run.data.metrics["similar_day_load_difference_selected"] == pytest.approx(
            retrieval["selected_load_difference"].mean()
        )

        ranking = artifact(run.info.run_id, "similar_day_ranking.csv")
        assert list(ranking.columns) == [
            "trade_date",
            "rank",
            "reference_date",
            "reference_lag_days",
            "distance",
            "weight",
        ]
        assert len(ranking) == 243
        assert ranking["trade_date"].unique().tolist() == [str(d.date()) for d in ranked]
        assert ranking.groupby("trade_date")["rank"].apply(list).eq([[1, 2, 3]] * 81).all()
        assert ranking.groupby("trade_date")["weight"].sum().to_numpy() == pytest.approx(
            [1.0] * 81, rel=1e-12
        )
        rank1 = ranking[ranking["rank"] == 1].set_index("trade_date")["reference_date"]
        assert rank1.to_dict() == selection.set_index("trade_date")["reference_date"].to_dict()

        special = artifact(run.info.run_id, "similar_day_special_days.csv").set_index("trade_date")
        assert list(special.columns) == [
            "holiday_name_ja",
            "last_year_date",
            "last_year_lag_days",
            "takes_reference",
            "similar_day_method",
        ]
        assert special.index.tolist() == [
            str(SAME_HOLIDAY_DAY.date()),
            str(RANKED_SPECIAL_DAY.date()),
        ]
        taken = special.loc[str(SAME_HOLIDAY_DAY.date())]
        assert taken["holiday_name_ja"] == "春分の日"
        assert taken["last_year_date"] == str(SAME_HOLIDAY_REFERENCE.date())
        assert taken["last_year_lag_days"] == 365.0
        assert bool(taken["takes_reference"])
        assert taken["similar_day_method"] == METHOD_SAME_HOLIDAY
        left = special.loc[str(RANKED_SPECIAL_DAY.date())]
        assert left["holiday_name_ja"] == "昭和の日"
        assert pd.isna(left["last_year_date"]) and pd.isna(left["last_year_lag_days"])
        assert not bool(left["takes_reference"])
        assert left["similar_day_method"] == METHOD_SIMILARITY

        rows = published_rows(spark, run.info.run_id)
        assert len(rows) == 48 * 81
        assert set(rows["area_code"]) == {"tokyo"}
        chosen = selection.set_index("trade_date")
        by_period = rows.set_index(["trade_date", "time_code"]).sort_index()
        day = pd.Timestamp("2024-04-10")
        reference = pd.Timestamp(chosen.loc[str(day.date()), "reference_date"])
        row = by_period.loc[(day.date(), 7)]
        assert row["similar_day_rank1_reference_date"] == reference.date()
        assert row["similar_day_rank1_demand_kwh"] == load_at(reference, 4) / PERIODS_PER_HOUR
        # Ranks 2 and 3 and the weighted mean follow the logged ranking.
        day_ranking = ranking[ranking["trade_date"] == str(day.date())].set_index("rank")
        references = {r: pd.Timestamp(day_ranking.loc[r, "reference_date"]) for r in (1, 2, 3)}
        assert references[1] == reference
        for r in (2, 3):
            assert row[f"similar_day_rank{r}_reference_date"] == references[r].date()
            assert (
                row[f"similar_day_rank{r}_demand_kwh"]
                == load_at(references[r], 4) / PERIODS_PER_HOUR
            )
        assert row["wavg_similar_day_top3_demand_kwh"] == pytest.approx(
            sum(day_ranking.loc[r, "weight"] * load_at(references[r], 4) for r in (1, 2, 3))
            / PERIODS_PER_HOUR,
            rel=1e-12,
        )
        assert row["similar_day_method"] == METHOD_SIMILARITY
        assert row["similar_day_fit_cutoff"] == pd.Timestamp(
            chosen.loc[str(day.date()), "fit_cutoff"]
        )
        # The fit ran before the day's issue time, 09:30 the day before.
        assert row["similar_day_fit_cutoff"] <= day - pd.Timedelta(days=1) + pd.Timedelta(
            hours=9, minutes=30
        )
        assert row["available_at"] == forecast_available_at(day)
        # The special day without a same-holiday reference is published like any ranked day.
        ranked_special = by_period.loc[(RANKED_SPECIAL_DAY.date(), 7)]
        assert ranked_special["similar_day_method"] == METHOD_SIMILARITY
        for r in (1, 2, 3):
            assert pd.notna(ranked_special[f"similar_day_rank{r}_reference_date"])
            assert pd.notna(ranked_special[f"similar_day_rank{r}_demand_kwh"])
        holiday = by_period.loc[(SAME_HOLIDAY_DAY.date(), 7)]
        expected = load_at(SAME_HOLIDAY_REFERENCE, 4) / PERIODS_PER_HOUR
        assert holiday["similar_day_rank1_reference_date"] == SAME_HOLIDAY_REFERENCE.date()
        assert holiday["similar_day_rank1_demand_kwh"] == expected
        assert holiday["wavg_similar_day_top3_demand_kwh"] == expected
        assert holiday["similar_day_method"] == METHOD_SAME_HOLIDAY
        assert pd.isna(holiday["similar_day_fit_cutoff"])
        # Its pool columns hold the ranking, so both variants are on the row.
        assert pd.notna(holiday["similar_day_pool_rank1_reference_date"])
        assert holiday["similar_day_pool_rank1_reference_date"] != SAME_HOLIDAY_REFERENCE.date()
        assert pd.notna(holiday["similar_day_pool_fit_cutoff"])
        # The reference's load was public at midnight after it (2023-03-22), but the
        # row carries the pool columns too, so it waits for the later of the two.
        assert holiday["available_at"] > pd.Timestamp("2023-03-22 00:00")
        assert holiday["available_at"] <= pd.Timestamp("2024-03-19 09:30")
        assert rows["published_at"].nunique() == 1

    def test_a_special_day_left_unranked_is_neither_counted_nor_called_similarity(
        self, spark, script, monkeypatch
    ):
        # A special day whose pool had no candidate is left out of the ranking. The
        # synthetic pool is never empty, so the ranked special day is dropped by hand.
        score = script.score_walk_forward

        def score_without_the_ranked_special_day(*args, **kwargs):
            scoring = score(*args, **kwargs)
            keep = scoring.selection.df["trade_date"] != RANKED_SPECIAL_DAY
            ranking = scoring.ranking.df
            return dataclasses.replace(
                scoring,
                selection=SimilarDaySelection.from_df(scoring.selection.df[keep]),
                ranking=SimilarDayRanking.from_df(
                    ranking[ranking["trade_date"] != RANKED_SPECIAL_DAY]
                ),
                fit_cutoff=scoring.fit_cutoff.drop(RANKED_SPECIAL_DAY),
            )

        monkeypatch.setattr(script, "score_walk_forward", score_without_the_ranked_special_day)
        script.main([])
        run = last_run()
        params = run.data.params
        assert params["n_days_scored"] == "80"
        assert params["n_days_ranked"] == "80"
        assert params["n_special_days_ranked"] == "1"
        assert params["n_days_same_holiday"] == "1"
        # The day dropped by hand had no pool and took no reference, so it is the
        # one scorable day with no row at all.
        assert params["n_days_ranked"] == params["n_days_scored"]
        special = artifact(run.info.run_id, "similar_day_special_days.csv").set_index("trade_date")
        assert special.loc[str(SAME_HOLIDAY_DAY.date()), "similar_day_method"] == (
            METHOD_SAME_HOLIDAY
        )
        left = special.loc[str(RANKED_SPECIAL_DAY.date())]
        assert not bool(left["takes_reference"])
        assert pd.isna(left["similar_day_method"])
        rows = published_rows(spark, run.info.run_id)
        assert RANKED_SPECIAL_DAY.date() not in set(rows["trade_date"])

    def test_cadence_reaches_the_job(self, spark, script):
        script.main(["--refit-every-days", "30"])
        run = last_run()
        assert run.data.params["refit_every_days"] == "30"
        assert run.data.params["n_fits"] == "3"
        assert len(published_rows(spark, run.info.run_id)) == 48 * int(
            run.data.params["n_days_scored"]
        )

    def test_the_pool_has_no_flag(self, script, capsys):
        with pytest.raises(SystemExit):
            script.main(["--window-half-width-days", "10"])
        assert "unrecognized arguments: --window-half-width-days" in capsys.readouterr().err

    def test_fit_window_reaches_the_selector(self, spark, script):
        script.main(["--fit-window-days", "14"])
        run = last_run()
        assert run.data.params["similar_day_fit_window_days"] == "14"
        # The last fit, on 04-25, saw the targets of the 14 days before it.
        assert run.data.params["similar_day_fit_from"] == "2024-04-11"
        assert run.data.params["similar_day_fit_through"] == "2024-04-24"
        fits = artifact(run.info.run_id, "similar_day_fits.csv")
        assert fits["n_targets"].max() == 14
        assert len(published_rows(spark, run.info.run_id)) == 48 * int(
            run.data.params["n_days_scored"]
        )

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

    def test_fit_window_below_one_is_rejected(self, script, capsys):
        with pytest.raises(SystemExit):
            script.main(["--fit-window-days", "0"])
        assert "--fit-window-days must be >= 1" in capsys.readouterr().err
