"""The similar-day feature: walk-forward scoring and the rows written to pma_ml.similar_day."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.similar_day import (
    PERIODS_PER_HOUR,
    SIMILAR_DAY_COMPONENTS,
    SimilarDaySelection,
    SimilarDaySelector,
)
from power_market_analytics.tasks.demand.similar_day_feature import (
    DEFAULT_REFIT_EVERY_DAYS,
    FEATURE_TABLE,
    MLFLOW_EXPERIMENT,
    SimilarDayFeatureRecords,
    WalkForwardScoring,
    build_feature_records,
    issue_times,
    publish_feature_records,
    score_walk_forward,
)
from tests.test_demand_similar_day import (
    HOLIDAYS,
    D,
    forecast_available_at,
    load_at,
    make_calendar,
    make_forecast,
    make_hourly_load,
    make_observed,
)

PUBLISHED_AT = pd.Timestamp("2026-09-11 10:00:00")
OTHER = pd.Timestamp("2024-04-11")
#: The first instant a fit can run in the synthetic frames: when the first scorable
#: forecast day's load (2024-02-07, a daily file) became public.
FIRST_CUTOFF = pd.Timestamp("2024-02-08")
#: The last scorable day: the calendar ends at its last holiday.
LAST_SCORABLE = HOLIDAYS[-1]
#: A fit before every synthetic day's issue time.
CUTOFF = pd.Timestamp("2024-04-01")


def make_selector(**load_kwargs) -> SimilarDaySelector:
    return SimilarDaySelector(
        make_calendar(), make_forecast(), make_observed(), make_hourly_load(**load_kwargs)
    )


def make_scoring(days=(D, OTHER), lag: int = 364, fit_cutoff=CUTOFF) -> WalkForwardScoring:
    days = list(days)
    selection = SimilarDaySelection.from_df(
        pd.DataFrame(
            {
                "trade_date": pd.to_datetime(days),
                "reference_date": pd.to_datetime([d - pd.Timedelta(days=lag) for d in days]),
                "distance": [0.5] * len(days),
                "reference_lag_days": pd.Series([lag] * len(days), dtype="int64"),
                "n_candidates": pd.Series([61] * len(days), dtype="int64"),
                "lag_364_rank": [1.0] * len(days),
            }
        )
    )
    return WalkForwardScoring(
        selection=selection,
        fit_cutoff=pd.Series([fit_cutoff] * len(days), index=pd.to_datetime(days)),
        fits=pd.DataFrame({"fit_cutoff": [fit_cutoff]}),
    )


def make_records(scoring=None, run_id: str = "score-1", **kwargs) -> SimilarDayFeatureRecords:
    return build_feature_records(
        make_scoring() if scoring is None else scoring,
        kwargs.pop("hourly_load", make_hourly_load()),
        kwargs.pop("forecast", make_forecast()),
        run_id=run_id,
        area_code="tokyo",
        published_at=PUBLISHED_AT,
    )


def published_rows(spark, run_id: str) -> pd.DataFrame:
    return spark.table(FEATURE_TABLE).where(f"run_id = '{run_id}'").toPandas()


class TestConstants:
    def test_names_and_cadence(self):
        assert MLFLOW_EXPERIMENT == "similar_day"
        assert FEATURE_TABLE == "pma_ml.similar_day"
        assert DEFAULT_REFIT_EVERY_DAYS == 7

    def test_issue_times_are_the_demand_tasks(self):
        assert issue_times(pd.DatetimeIndex([D])).tolist() == [
            D - pd.Timedelta(days=1) + pd.Timedelta(hours=9, minutes=30)
        ]


class TestScoreWalkForward:
    @pytest.fixture(scope="class")
    def weekly(self) -> WalkForwardScoring:
        return score_walk_forward(make_selector(), make_forecast().df["trade_date"].unique())

    def test_every_day_is_scored_by_the_latest_fit_before_its_issue_time(self, weekly):
        scored = weekly.selection.df["trade_date"]
        # The first fit runs when the first scorable day's load is public (02-08 00:00);
        # the first day it can serve is issued after that: 02-09.
        assert scored.min() == pd.Timestamp("2024-02-09")
        assert scored.max() == LAST_SCORABLE
        assert scored.is_monotonic_increasing and scored.is_unique
        assert list(weekly.fit_cutoff.index) == scored.tolist()
        issued = issue_times(pd.DatetimeIndex(scored))
        gap = issued - pd.DatetimeIndex(weekly.fit_cutoff)
        assert gap.min() == pd.Timedelta(hours=9, minutes=30)
        assert gap.max() < pd.Timedelta(days=DEFAULT_REFIT_EVERY_DAYS)
        fits = weekly.fits
        assert fits["fit_cutoff"].tolist() == list(
            pd.date_range(FIRST_CUTOFF, issued.max(), freq="7D")
        )
        assert set(weekly.fit_cutoff) == set(fits["fit_cutoff"])
        assert fits["n_days_scored"].sum() == len(scored)
        assert list(fits.columns) == [
            "fit_cutoff",
            "fit_from",
            "fit_through",
            "n_pairs",
            "n_targets",
            "alpha",
            "beta",
            "fit_rmse",
            "n_days_scored",
            *[f"weight_{p}" for p in SIMILAR_DAY_COMPONENTS],
            *[f"scale_{p}" for p in SIMILAR_DAY_COMPONENTS],
        ]
        # Each fit sees the targets whose load was public by its cutoff, so the pairs grow.
        assert fits["fit_from"].eq(pd.Timestamp("2024-02-07")).all()
        assert (fits["fit_through"] == fits["fit_cutoff"] - pd.Timedelta(days=1)).all()
        assert (
            fits["n_pairs"].is_monotonic_increasing
            and fits["n_pairs"].iloc[0] < fits["n_pairs"].iloc[-1]
        )

    def test_a_days_choice_is_the_fits_own(self, weekly):
        # Re-fit at the day's cutoff and select the day again: the same choice.
        cutoff = weekly.fit_cutoff[D]
        selector = make_selector()
        selector.fit(cutoff)
        again = selector.select([D]).df.iloc[0]
        chosen = weekly.selection.df.set_index("trade_date").loc[D]
        assert again["reference_date"] == chosen["reference_date"]
        assert again["distance"] == chosen["distance"]

    def test_a_late_load_holds_a_fit_back(self):
        # The loads of 03-01..03-06 are public two days after their day (the yearly files):
        # the fit on 03-07 00:00 sees 03-05 (public 03-07 00:00) but not 03-06.
        late = set(pd.date_range("2024-03-01", "2024-03-06"))
        scoring = score_walk_forward(
            make_selector(late=late), make_forecast().df["trade_date"].unique()
        )
        fits = scoring.fits.set_index("fit_cutoff")
        assert fits.loc[pd.Timestamp("2024-03-07"), "fit_through"] == pd.Timestamp("2024-03-05")
        assert fits.loc[pd.Timestamp("2024-03-14"), "fit_through"] == pd.Timestamp("2024-03-13")
        # A late first day pushes the first cutoff, and so the first scored day, out.
        first = score_walk_forward(
            make_selector(late={pd.Timestamp("2024-02-07")}),
            make_forecast().df["trade_date"].unique(),
        )
        assert first.fits["fit_cutoff"].iloc[0] == pd.Timestamp("2024-02-09")
        assert first.selection.df["trade_date"].min() == pd.Timestamp("2024-02-10")

    def test_a_coarser_cadence_makes_fewer_fits(self):
        monthly = score_walk_forward(
            make_selector(), make_forecast().df["trade_date"].unique(), refit_every_days=30
        )
        assert len(monthly.fits) == 3
        assert monthly.fits["fit_cutoff"].tolist() == [
            FIRST_CUTOFF,
            FIRST_CUTOFF + pd.Timedelta(days=30),
            FIRST_CUTOFF + pd.Timedelta(days=60),
        ]
        weekly = score_walk_forward(make_selector(), make_forecast().df["trade_date"].unique())
        assert len(monthly.selection) == len(weekly.selection)

    def test_a_gap_in_the_forecasts_leaves_a_fit_without_days(self):
        # No forecast from 03-01 to 03-20: the fits of those weeks score nothing and
        # the days after the gap are scored by the fits that follow.
        gap = pd.date_range("2024-03-01", "2024-03-20")
        forecast = make_forecast(
            [d for d in make_forecast().df["trade_date"].unique() if d not in gap]
        )
        selector = SimilarDaySelector(
            make_calendar(), forecast, make_observed(), make_hourly_load()
        )
        scoring = score_walk_forward(selector, forecast.df["trade_date"].unique())
        assert not set(gap) & set(scoring.selection.df["trade_date"])
        assert (scoring.fits["n_days_scored"] == 0).sum() >= 2
        assert scoring.fits["n_days_scored"].sum() == len(scoring.selection)
        assert pd.Timestamp("2024-03-21") in set(scoring.selection.df["trade_date"])

    def test_days_issued_before_the_first_fit_are_left_out(self, weekly):
        # 02-07 and 02-08 are scorable, but their issue times precede the first cutoff.
        assert pd.Timestamp("2024-02-08") not in set(weekly.selection.df["trade_date"])
        assert pd.Timestamp("2024-02-09") in set(weekly.selection.df["trade_date"])

    def test_cadence_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="refit_every_days must be >= 1"):
            score_walk_forward(make_selector(), [D], refit_every_days=0)

    def test_no_fit_possible_is_rejected(self):
        # Loads end before any scorable day, so no pair exists.
        selector = make_selector(days=pd.date_range("2023-01-01", "2024-01-31"))
        with pytest.raises(ValueError, match="fewer than 8 training pairs"):
            score_walk_forward(selector, [D])

    def test_a_narrow_window_waits_for_eight_public_pairs(self):
        # A window of three days: the first fit runs once eight pairs are public, and
        # the first day it serves is issued after that.
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(),
            half_width_days=1,
        )
        days = make_forecast().df["trade_date"].unique()
        first_day = selector.scorable_days(days)[0]
        scoring = score_walk_forward(selector, days)
        cutoff = scoring.fits["fit_cutoff"].iloc[0]
        assert cutoff == selector.first_fit_cutoff
        assert cutoff > first_day + pd.Timedelta(days=1)
        assert scoring.fits["n_pairs"].iloc[0] >= 8
        assert (
            issue_times(pd.DatetimeIndex([scoring.selection.df["trade_date"].min()]))[0] >= cutoff
        )

    def test_no_day_issued_after_the_first_fit_is_rejected(self):
        with pytest.raises(ValueError, match="no day can be scored"):
            score_walk_forward(
                make_selector(), [pd.Timestamp("2024-02-07"), pd.Timestamp("2024-02-08")]
            )


class TestBuildFeatureRecords:
    def test_48_rows_per_day_with_the_reference_days_load_halved(self):
        records = make_records()
        assert type(records) is SimilarDayFeatureRecords
        assert records.keys == ["area_code", "trade_date", "time_code"]
        assert list(records.df.columns) == [
            "area_code",
            "trade_date",
            "time_code",
            "similar_day_demand_kwh",
            "similar_day_reference_date",
            "similar_day_reference_lag_days",
            "similar_day_distance",
            "similar_day_n_candidates",
            "similar_day_fit_cutoff",
            "available_at",
            "published_at",
            "run_id",
        ]
        assert len(records) == 96
        by_period = records.df.set_index(["trade_date", "time_code"])
        reference = D - pd.Timedelta(days=364)
        for tc in (1, 2, 24, 48):
            row = by_period.loc[(D, tc)]
            assert (
                row["similar_day_demand_kwh"]
                == load_at(reference, (tc + 1) // 2) / PERIODS_PER_HOUR
            )
            assert row["similar_day_reference_date"] == reference
            assert row["similar_day_reference_lag_days"] == 364
            assert row["similar_day_distance"] == 0.5
            assert row["similar_day_n_candidates"] == 61
            assert row["similar_day_fit_cutoff"] == CUTOFF
            # Usable once D's forecast vintage is (01:00 on D-1), the fit being older.
            assert row["available_at"] == forecast_available_at(D)
            assert row["published_at"] == PUBLISHED_AT
            assert row["run_id"] == "score-1"
            assert row["area_code"] == "tokyo"
        assert records.df["trade_date"].tolist() == [D] * 48 + [OTHER] * 48
        assert records.df["time_code"].tolist() == list(range(1, 49)) * 2
        assert records.df["similar_day_reference_lag_days"].dtype == "int64"
        assert records.df["available_at"].dtype == "datetime64[ns]"

    def test_a_fit_after_the_forecast_sets_the_availability(self):
        # A fit at 08:00 on D-1, after the forecast's 01:00 and before the 09:30 issue.
        cutoff = D - pd.Timedelta(days=1) + pd.Timedelta(hours=8)
        records = make_records(make_scoring(days=(D,), fit_cutoff=cutoff))
        assert records.df["available_at"].eq(cutoff).all()

    def test_an_empty_scoring_is_rejected(self):
        with pytest.raises(ValueError, match="no scored day to publish"):
            make_records(make_scoring(days=()))

    def test_a_similar_day_without_a_load_is_rejected(self):
        # 2023-01-01 is the first load day: a lag reaching before it has no load.
        scoring = make_scoring(
            days=(pd.Timestamp("2023-12-31"),), lag=365, fit_cutoff=pd.Timestamp("2023-12-01")
        )
        with pytest.raises(ValueError, match=r"48 period\(s\) have no load on their similar day"):
            make_records(scoring)

    def test_a_day_without_a_forecast_is_rejected(self):
        forecast = make_forecast(pd.date_range("2024-04-11", "2024-04-12"))
        with pytest.raises(
            ValueError, match=r"1 day\(s\) have no forecast availability, e.g. 2024-04-10"
        ):
            make_records(forecast=forecast)

    def test_the_frame_checks_its_rows(self):
        df = make_records().df
        with pytest.raises(ValueError, match="similar_day_reference_date must precede"):
            SimilarDayFeatureRecords.from_df(df.assign(similar_day_reference_date=df["trade_date"]))
        with pytest.raises(ValueError, match="must equal the date gap"):
            SimilarDayFeatureRecords.from_df(df.assign(similar_day_reference_lag_days=363))
        with pytest.raises(ValueError, match="must be positive"):
            SimilarDayFeatureRecords.from_df(df.assign(similar_day_demand_kwh=0.0))
        with pytest.raises(ValueError, match="time_code outside 1..48"):
            SimilarDayFeatureRecords.from_df(df.assign(time_code=df["time_code"] + 48))
        with pytest.raises(ValueError, match="must not follow the issue time"):
            SimilarDayFeatureRecords.from_df(
                df.assign(
                    similar_day_fit_cutoff=issue_times(df["trade_date"]) + pd.Timedelta(minutes=1)
                )
            )
        with pytest.raises(ValueError, match="must not precede the fit's cutoff"):
            SimilarDayFeatureRecords.from_df(
                df.assign(available_at=pd.to_datetime(["2024-03-01"] * len(df)))
            )
        with pytest.raises(ValueError, match="one run per frame, got 2"):
            SimilarDayFeatureRecords.from_df(df.assign(run_id=["a"] * 48 + ["b"] * 48))


class TestPublishFeatureRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        assert publish_feature_records(make_records(run_id="score-create"), spark=spark) == 96
        assert spark.catalog.tableExists(FEATURE_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(FEATURE_TABLE)}
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        assert list(columns) == list(make_records().df.columns)
        assert columns["trade_date"].dataType == "date"
        assert columns["similar_day_fit_cutoff"].dataType == "timestamp"
        assert columns["similar_day_reference_lag_days"].dataType == "int"
        assert columns["similar_day_demand_kwh"].dataType == "double"
        assert columns["available_at"].dataType == "timestamp"
        rows = published_rows(spark, "score-create").sort_values(["trade_date", "time_code"])
        assert len(rows) == 96
        first = rows.iloc[0]
        assert first["trade_date"] == D.date()
        assert first["time_code"] == 1
        assert first["similar_day_reference_date"] == (D - pd.Timedelta(days=364)).date()
        assert first["similar_day_demand_kwh"] == load_at(D - pd.Timedelta(days=364), 1) / 2
        assert first["similar_day_fit_cutoff"] == CUTOFF
        assert first["available_at"] == forecast_available_at(D)
        assert first["published_at"] == PUBLISHED_AT

    def test_republishing_a_run_replaces_only_its_rows(self, spark):
        publish_feature_records(make_records(run_id="score-keep"), spark=spark)
        publish_feature_records(make_records(run_id="score-replace"), spark=spark)
        publish_feature_records(
            make_records(make_scoring(days=(D,)), run_id="score-replace"), spark=spark
        )
        assert len(published_rows(spark, "score-replace")) == 48
        assert len(published_rows(spark, "score-keep")) == 96

    def test_defaults_to_the_active_spark_session(self, spark):
        assert publish_feature_records(make_records(run_id="score-default")) == 96
        assert len(published_rows(spark, "score-default")) == 96
