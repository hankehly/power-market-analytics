"""The similar-day features: walk-forward scoring and the rows written to pma_ml.similar_day."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.similar_day import (
    PERIODS_PER_HOUR,
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_TOP_K,
    SimilarDayPool,
    SimilarDayRanking,
    SimilarDaySelection,
    SimilarDaySelector,
    SpecialDayReferences,
)
from power_market_analytics.tasks.demand.similar_day_feature import (
    DEFAULT_REFIT_EVERY_DAYS,
    FEATURE_TABLE,
    METHOD_SAME_HOLIDAY,
    METHOD_SIMILARITY,
    MLFLOW_EXPERIMENT,
    RANK_DATE_COLS,
    RANK_DISTANCE_COLS,
    RANK_LOAD_COLS,
    WEIGHTED_MEAN_COL,
    SimilarDayFeatureRecords,
    WalkForwardScoring,
    build_feature_records,
    issue_times,
    publish_feature_records,
    score_walk_forward,
)
from tests.test_demand_similar_day import (
    HISTORY_DAYS,
    HOLIDAY_NAMES,
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
#: 春分の日 2024-03-20 takes 春分の日 2023-03-21, 365 days back (inside 335 .. 394).
SAME_HOLIDAY = pd.Timestamp("2024-03-20")
SAME_HOLIDAY_REFERENCE = pd.Timestamp("2023-03-21")
#: The hand-made ranks of make_scoring: (lag in days, distance), nearest first.
REFS = ((364, 0.5), (7, 1.0), (2, 2.0))
#: Every fit's cutoff in the synthetic frames, weekly from the first.
WEEKLY_CUTOFFS = pd.date_range(FIRST_CUTOFF, "2024-04-25", freq="7D")


def make_selector(**load_kwargs) -> SimilarDaySelector:
    return SimilarDaySelector(
        make_calendar(), make_forecast(), make_observed(), make_hourly_load(**load_kwargs)
    )


def make_scoring(
    days=(D, OTHER), refs=REFS, fit_cutoff=CUTOFF, same_holiday=()
) -> WalkForwardScoring:
    """A scoring whose ranked ``days`` all take ``refs`` and whose ``same_holiday``
    days, ``(day, reference day)`` pairs, take their reference."""
    days = pd.DatetimeIndex(list(days))
    lags = np.array([lag for lag, _ in refs], dtype="int64")
    # A pool of at least three days; a smaller one holds exactly the ranks given.
    n_candidates = 87 if len(refs) >= SIMILAR_DAY_TOP_K else len(refs)
    ranking = SimilarDayRanking.from_df(
        pd.DataFrame(
            {
                "trade_date": np.repeat(days.to_numpy(), len(refs)),
                "rank": np.tile(np.arange(1, len(refs) + 1, dtype="int64"), len(days)),
                "reference_date": pd.to_datetime(
                    [day - pd.Timedelta(days=int(lag)) for day in days for lag in lags]
                ),
                "reference_lag_days": np.tile(lags, len(days)),
                "distance": np.tile(np.array([d for _, d in refs], dtype="float64"), len(days)),
            }
        )
    )
    selection = SimilarDaySelection.from_df(
        pd.DataFrame(
            {
                "trade_date": days,
                "reference_date": days - pd.Timedelta(days=int(lags[0])),
                "distance": np.full(len(days), refs[0][1], dtype="float64"),
                "reference_lag_days": np.full(len(days), lags[0], dtype="int64"),
                "n_candidates": np.full(len(days), n_candidates, dtype="int64"),
                "lag_7_rank": np.full(len(days), 2.0),
            }
        )
    )
    holidays = pd.DatetimeIndex([day for day, _ in same_holiday])
    references = pd.DatetimeIndex([reference for _, reference in same_holiday])
    special_days = SpecialDayReferences.from_df(
        pd.DataFrame(
            {
                "trade_date": holidays,
                "holiday_name_ja": pd.Series(
                    [HOLIDAY_NAMES.get(day, "祝日") for day in holidays], dtype="object"
                ),
                "last_year_date": references,
                "last_year_lag_days": (holidays - references).days.to_numpy(dtype="float64"),
                "takes_reference": np.ones(len(holidays), dtype="bool"),
            }
        )
    )
    return WalkForwardScoring(
        selection=selection,
        ranking=ranking,
        special_days=special_days,
        fit_cutoff=pd.Series([fit_cutoff] * len(days), index=days, dtype="datetime64[ns]"),
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

    def test_column_sets(self):
        assert RANK_LOAD_COLS == (
            "similar_day_rank1_demand_kwh",
            "similar_day_rank2_demand_kwh",
            "similar_day_rank3_demand_kwh",
        )
        assert WEIGHTED_MEAN_COL == "wavg_similar_day_top3_demand_kwh"
        assert RANK_DATE_COLS == (
            "similar_day_rank1_reference_date",
            "similar_day_rank2_reference_date",
            "similar_day_rank3_reference_date",
        )
        assert RANK_DISTANCE_COLS == (
            "similar_day_rank1_distance",
            "similar_day_rank2_distance",
            "similar_day_rank3_distance",
        )
        assert (METHOD_SIMILARITY, METHOD_SAME_HOLIDAY) == ("similarity", "same_holiday")

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
        # the first day it can serve is issued after that: 02-09. 春分の日 2024-03-20
        # takes 2023-03-21 and is not ranked; 昭和の日 2024-04-29 has no 2023 namesake
        # and is ranked: 81 days from 02-09 to 04-29, 80 of them ranked.
        expected = pd.date_range("2024-02-09", LAST_SCORABLE).drop(SAME_HOLIDAY)
        assert scored.tolist() == expected.tolist()
        assert len(scored) == 80
        assert LAST_SCORABLE in set(scored)
        assert weekly.special_days.same_holiday_days.tolist() == [SAME_HOLIDAY]
        special = weekly.special_days.df
        assert special["trade_date"].tolist() == [SAME_HOLIDAY, LAST_SCORABLE]
        assert special["takes_reference"].tolist() == [True, False]
        assert special["last_year_date"].iloc[0] == SAME_HOLIDAY_REFERENCE
        assert list(weekly.fit_cutoff.index) == scored.tolist()
        issued = issue_times(pd.DatetimeIndex(scored))
        gap = issued - pd.DatetimeIndex(weekly.fit_cutoff)
        assert gap.min() == pd.Timedelta(hours=9, minutes=30)
        assert gap.max() < pd.Timedelta(days=DEFAULT_REFIT_EVERY_DAYS)
        fits = weekly.fits
        assert fits["fit_cutoff"].tolist() == list(WEEKLY_CUTOFFS)
        assert set(weekly.fit_cutoff) == set(fits["fit_cutoff"])
        assert fits["n_days_scored"].sum() == 80
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
        # Each fit sees the targets whose load was public by its cutoff, so the pairs
        # grow; a special day is never a target, so the fit of 03-21 ends at 03-19.
        assert fits["fit_from"].eq(pd.Timestamp("2024-02-07")).all()
        assert fits["fit_through"].tolist() == expected_fit_through(fits["fit_cutoff"])
        assert (
            fits["n_pairs"].is_monotonic_increasing
            and fits["n_pairs"].iloc[0] < fits["n_pairs"].iloc[-1]
        )
        # Every ranked day holds three ranks, and its rank 1 is its selection.
        ranking = weekly.ranking.df
        assert ranking.groupby("trade_date")["rank"].apply(list).eq([[1, 2, 3]] * 80).all()
        rank1 = ranking[ranking["rank"] == 1].set_index("trade_date")
        selection = weekly.selection.df.set_index("trade_date")
        assert rank1.index.tolist() == selection.index.tolist()
        assert (rank1["reference_date"] == selection["reference_date"]).all()
        assert (rank1["distance"] == selection["distance"]).all()

    def test_same_holiday_days_are_not_ranked(self, weekly):
        assert SAME_HOLIDAY not in set(weekly.ranking.df["trade_date"])
        assert SAME_HOLIDAY not in weekly.fit_cutoff.index

    def test_a_days_choice_is_the_fits_own(self, weekly):
        # Re-fit at the day's cutoff and rank the day again: the same choice.
        cutoff = weekly.fit_cutoff[D]
        selector = make_selector()
        selector.fit(cutoff)
        selection, ranking = selector.select_and_rank([D], SIMILAR_DAY_TOP_K)
        chosen = weekly.selection.df[weekly.selection.df["trade_date"] == D]
        pd.testing.assert_frame_equal(selection.df, chosen.reset_index(drop=True))
        ranked = weekly.ranking.df[weekly.ranking.df["trade_date"] == D]
        pd.testing.assert_frame_equal(ranking.df, ranked.reset_index(drop=True))

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

    def test_a_coarser_cadence_makes_fewer_fits(self, weekly):
        monthly = score_walk_forward(
            make_selector(), make_forecast().df["trade_date"].unique(), refit_every_days=30
        )
        assert len(monthly.fits) == 3
        assert monthly.fits["fit_cutoff"].tolist() == [
            FIRST_CUTOFF,
            FIRST_CUTOFF + pd.Timedelta(days=30),
            FIRST_CUTOFF + pd.Timedelta(days=60),
        ]
        assert len(monthly.selection) == len(weekly.selection)
        assert len(monthly.ranking) == len(weekly.ranking)

    def test_a_fit_window_bounds_what_each_fit_sees(self, weekly):
        # A window of 14 days: a fit sees the targets of the 14 days before its
        # cutoff's day, so the targets stop growing once the window is full (from the
        # third fit, 02-22, on); until then the fits are the unbounded ones. The fits
        # of 03-21 and 03-28 see 13 targets: 03-20 is a special day.
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(),
            fit_window_days=14,
        )
        scoring = score_walk_forward(selector, make_forecast().df["trade_date"].unique())
        fits = scoring.fits
        assert fits["fit_cutoff"].tolist() == weekly.fits["fit_cutoff"].tolist()
        window_start = fits["fit_cutoff"] - pd.Timedelta(days=14)
        first_scorable = FIRST_CUTOFF - pd.Timedelta(days=1)
        assert fits["fit_from"].tolist() == window_start.clip(lower=first_scorable).tolist()
        assert fits["fit_through"].tolist() == expected_fit_through(fits["fit_cutoff"])
        assert fits["n_targets"].tolist() == [1, 8, 14, 14, 14, 14, 13, 13, 14, 14, 14, 14]
        # A target's pool is 90 days less the holidays in it (see the derivation in
        # tests/test_demand_similar_day.py's TestWindowPairCounts).
        assert fits["n_pairs"].tolist() == [
            88, 717, 1256, 1250, 1246, 1246, 1157, 1151, 1231, 1223, 1218, 1228
        ]  # fmt: skip
        assert (fits["n_targets"] < weekly.fits["n_targets"]).iloc[2:].all()
        pd.testing.assert_frame_equal(fits.iloc[:2], weekly.fits.iloc[:2])
        # The same days are scored, each by the fit of its own block.
        assert scoring.selection.df["trade_date"].tolist() == (
            weekly.selection.df["trade_date"].tolist()
        )
        assert scoring.fit_cutoff.tolist() == weekly.fit_cutoff.tolist()
        assert scoring.cutoffs_without_fit.empty and weekly.cutoffs_without_fit.empty

    def test_a_cutoff_without_enough_pairs_makes_no_fit(self):
        # A window of 7 days and no forecast from 03-01 to 03-20: the cutoffs 03-14
        # and 03-21 find no pair inside the window, so the fit of 03-07 serves the
        # days up to 03-28 and the fit of 03-28 the days after.
        gap = pd.date_range("2024-03-01", "2024-03-20")
        forecast = make_forecast(
            [d for d in make_forecast().df["trade_date"].unique() if d not in gap]
        )
        selector = SimilarDaySelector(
            make_calendar(), forecast, make_observed(), make_hourly_load(), fit_window_days=7
        )
        scoring = score_walk_forward(selector, forecast.df["trade_date"].unique())
        skipped = [pd.Timestamp("2024-03-14"), pd.Timestamp("2024-03-21")]
        assert scoring.cutoffs_without_fit.tolist() == skipped
        assert not set(skipped) & set(scoring.fits["fit_cutoff"])
        served = scoring.fit_cutoff["2024-03-21":"2024-03-28"]
        assert len(served) == 8 and served.eq(pd.Timestamp("2024-03-07")).all()
        assert scoring.fit_cutoff[pd.Timestamp("2024-03-29")] == pd.Timestamp("2024-03-28")
        fits = scoring.fits.set_index("fit_cutoff")
        assert fits.loc[pd.Timestamp("2024-03-07"), "n_days_scored"] == 8
        assert scoring.fits["n_days_scored"].sum() == len(scoring.selection)
        # The rows still carry a cutoff before their issue time.
        records = build_feature_records(
            scoring,
            make_hourly_load(),
            forecast,
            run_id="r",
            area_code="tokyo",
            published_at=PUBLISHED_AT,
        )
        assert len(records) == 48 * (
            len(scoring.selection) + len(scoring.special_days.same_holiday_days)
        )
        assert_usable_by_the_issue_time(records)

    def test_every_published_row_is_usable_by_its_issue_time(self, weekly):
        records = build_feature_records(
            weekly,
            make_hourly_load(),
            make_forecast(),
            run_id="r",
            area_code="tokyo",
            published_at=PUBLISHED_AT,
        )
        # 80 ranked days and 春分の日.
        assert len(records) == 48 * 81
        assert set(records.df["similar_day_method"]) == {METHOD_SIMILARITY, METHOD_SAME_HOLIDAY}
        assert_usable_by_the_issue_time(records)

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
        # 03-20 has no forecast, so it is not a scored special day either.
        assert scoring.special_days.same_holiday_days.empty

    def test_days_issued_before_the_first_fit_are_left_out(self, weekly):
        # 02-07 and 02-08 are scorable, but their issue times precede the first cutoff.
        assert pd.Timestamp("2024-02-08") not in set(weekly.selection.df["trade_date"])
        assert pd.Timestamp("2024-02-09") in set(weekly.selection.df["trade_date"])

    def test_only_same_holiday_days_still_score(self):
        # The first fit cutoff comes from every training pair (02-08); the one day asked
        # for is a same-holiday day, so nothing is ranked and nothing raises.
        scoring = score_walk_forward(make_selector(), [SAME_HOLIDAY])
        assert len(scoring.selection) == 0
        assert scoring.selection.df.dtypes.astype(str).to_dict() == SimilarDaySelection.schema
        assert len(scoring.ranking) == 0
        assert scoring.ranking.df.dtypes.astype(str).to_dict() == SimilarDayRanking.schema
        assert scoring.fit_cutoff.empty
        assert scoring.special_days.same_holiday_days.tolist() == [SAME_HOLIDAY]
        assert scoring.fits["n_days_scored"].eq(0).all()
        records = build_feature_records(
            scoring,
            make_hourly_load(),
            make_forecast(),
            run_id="r",
            area_code="tokyo",
            published_at=PUBLISHED_AT,
        )
        assert len(records) == 48
        assert records.df["similar_day_method"].eq(METHOD_SAME_HOLIDAY).all()

    def test_a_reference_published_after_the_issue_time_is_ranked(self):
        # 2023-03-21's load re-issued at 10:00 on 2024-03-19, after 03-20's 09:30 issue
        # time: 春分の日 takes no reference and is ranked from its pool instead.
        reissued = {SAME_HOLIDAY_REFERENCE: pd.Timestamp("2024-03-19 10:00")}
        scoring = score_walk_forward(make_selector(public_at=reissued), [SAME_HOLIDAY])
        assert scoring.special_days.same_holiday_days.empty
        assert scoring.special_days.df["trade_date"].tolist() == [SAME_HOLIDAY]
        assert scoring.selection.df["trade_date"].tolist() == [SAME_HOLIDAY]
        assert scoring.ranking.df["rank"].tolist() == [1, 2, 3]
        records = build_feature_records(
            scoring,
            make_hourly_load(public_at=reissued),
            make_forecast(),
            run_id="r",
            area_code="tokyo",
            published_at=PUBLISHED_AT,
        )
        assert len(records) == 48
        assert records.df["similar_day_method"].eq(METHOD_SIMILARITY).all()
        assert_usable_by_the_issue_time(records)

    def test_cadence_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="refit_every_days must be >= 1"):
            score_walk_forward(make_selector(), [D], refit_every_days=0)

    def test_no_fit_possible_is_rejected(self):
        # Loads end before any scorable day, so no pair exists.
        selector = make_selector(days=pd.date_range("2023-01-01", "2024-01-31"))
        with pytest.raises(ValueError, match="fewer than 8 training pairs"):
            score_walk_forward(selector, [D])

    def test_a_narrow_window_waits_for_eight_public_pairs(self):
        # A pool of three days: the first fit runs once eight pairs are public, and
        # the first day it serves is issued after that.
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(),
            pool=SimilarDayPool(((363, 365),)),
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


def expected_fit_through(cutoffs: pd.Series) -> list[pd.Timestamp]:
    """The last target of each weekly fit: the day before its cutoff, or the day before
    that when it is the special day 2024-03-20, which is never a target."""
    through = [cutoff - pd.Timedelta(days=1) for cutoff in cutoffs]
    return [day - pd.Timedelta(days=1) if day == SAME_HOLIDAY else day for day in through]


def assert_usable_by_the_issue_time(records: SimilarDayFeatureRecords) -> None:
    """Every row is public by its day's issue time, so Feast can serve it."""
    issued = issue_times(pd.DatetimeIndex(records.df["trade_date"]))
    assert (records.df["available_at"].to_numpy() <= issued.to_numpy()).all()


def ranked_rows(df: pd.DataFrame) -> pd.Series:
    return df["similar_day_method"] == METHOD_SIMILARITY


def with_values(df: pd.DataFrame, where: pd.Series, **columns) -> pd.DataFrame:
    """``df`` with ``columns`` set on the rows ``where`` holds."""
    out = df.copy()
    for col, value in columns.items():
        out.loc[where, col] = value
    return out


class TestBuildFeatureRecords:
    def test_48_rows_per_ranked_day_with_three_loads_and_their_weighted_mean(self):
        records = make_records()
        assert type(records) is SimilarDayFeatureRecords
        assert records.keys == ["area_code", "trade_date", "time_code"]
        assert list(records.df.columns) == [
            "area_code",
            "trade_date",
            "time_code",
            "similar_day_rank1_demand_kwh",
            "similar_day_rank2_demand_kwh",
            "similar_day_rank3_demand_kwh",
            "wavg_similar_day_top3_demand_kwh",
            "similar_day_rank1_reference_date",
            "similar_day_rank2_reference_date",
            "similar_day_rank3_reference_date",
            "similar_day_rank1_distance",
            "similar_day_rank2_distance",
            "similar_day_rank3_distance",
            "similar_day_n_candidates",
            "similar_day_fit_cutoff",
            "similar_day_method",
            "available_at",
            "published_at",
            "run_id",
        ]
        assert len(records) == 96
        by_period = records.df.set_index(["trade_date", "time_code"])
        references = [D - pd.Timedelta(days=lag) for lag, _ in REFS]
        for tc in (1, 2, 24, 48):
            row = by_period.loc[(D, tc)]
            hour = (tc + 1) // 2
            loads = [load_at(reference, hour) / PERIODS_PER_HOUR for reference in references]
            for col, load in zip(RANK_LOAD_COLS, loads, strict=True):
                assert row[col] == load
            # Inverse-distance weights of 0.5, 1 and 2: 4/7, 2/7 and 1/7.
            assert row[WEIGHTED_MEAN_COL] == pytest.approx(
                4 / 7 * loads[0] + 2 / 7 * loads[1] + 1 / 7 * loads[2], rel=1e-12
            )
            assert [row[col] for col in RANK_DATE_COLS] == references
            assert [row[col] for col in RANK_DISTANCE_COLS] == [0.5, 1.0, 2.0]
            assert row["similar_day_n_candidates"] == 87.0
            assert row["similar_day_fit_cutoff"] == CUTOFF
            assert row["similar_day_method"] == METHOD_SIMILARITY
            # Usable once D's forecast vintage is (01:00 on D-1): the fit and the loads
            # (D - 2's public at 00:00 on D-1) are older.
            assert row["available_at"] == forecast_available_at(D)
            assert row["published_at"] == PUBLISHED_AT
            assert row["run_id"] == "score-1"
            assert row["area_code"] == "tokyo"
        assert records.df["trade_date"].tolist() == [D] * 48 + [OTHER] * 48
        assert records.df["time_code"].tolist() == list(range(1, 49)) * 2
        assert records.df.dtypes.astype(str).to_dict() == SimilarDayFeatureRecords.schema

    def test_a_same_holiday_days_rows(self):
        # No forecast for 03-20 is needed: its row waits for its reference's load only.
        scoring = make_scoring(days=(D,), same_holiday=((SAME_HOLIDAY, SAME_HOLIDAY_REFERENCE),))
        records = make_records(scoring, forecast=make_forecast(pd.date_range("2024-04-01", D)))
        assert len(records) == 96
        rows = records.df[records.df["trade_date"] == SAME_HOLIDAY]
        assert rows["time_code"].tolist() == list(range(1, 49))
        expected = [load_at(SAME_HOLIDAY_REFERENCE, (tc + 1) // 2) / 2 for tc in range(1, 49)]
        assert rows["similar_day_rank1_demand_kwh"].tolist() == expected
        assert rows[WEIGHTED_MEAN_COL].tolist() == expected
        assert rows["similar_day_rank1_reference_date"].eq(SAME_HOLIDAY_REFERENCE).all()
        null_cols = [
            *RANK_LOAD_COLS[1:],
            *RANK_DATE_COLS[1:],
            *RANK_DISTANCE_COLS,
            "similar_day_n_candidates",
            "similar_day_fit_cutoff",
        ]
        assert rows[null_cols].isna().all(axis=None)
        assert rows["similar_day_method"].eq(METHOD_SAME_HOLIDAY).all()
        # The reference's load is public at midnight after it.
        assert rows["available_at"].eq(pd.Timestamp("2023-03-22 00:00")).all()
        assert (
            records.df.loc[records.df["trade_date"] == D, "similar_day_method"]
            .eq(METHOD_SIMILARITY)
            .all()
        )

    def test_a_ranked_special_day(self):
        # 昭和の日 2024-04-29 has no 2023 namesake: ranked like any day. A special day
        # that does not take a reference adds no row of its own.
        day = HOLIDAYS[-1]
        scoring = make_scoring(days=(day,))
        not_taken = SpecialDayReferences.from_df(
            pd.DataFrame(
                {
                    "trade_date": [day],
                    "holiday_name_ja": ["昭和の日"],
                    "last_year_date": pd.to_datetime([pd.NaT]),
                    "last_year_lag_days": [np.nan],
                    "takes_reference": [False],
                }
            )
        )
        records = make_records(dataclasses.replace(scoring, special_days=not_taken))
        assert len(records) == 48
        assert records.df["similar_day_method"].eq(METHOD_SIMILARITY).all()
        assert records.df[list(RANK_LOAD_COLS)].notna().all(axis=None)

    def test_fewer_than_three_ranks(self):
        records = make_records(make_scoring(days=(D,), refs=REFS[:2]))
        row = records.df.set_index("time_code").loc[7]
        loads = [load_at(D - pd.Timedelta(days=lag), 4) / 2 for lag, _ in REFS[:2]]
        assert row["similar_day_rank2_demand_kwh"] == loads[1]
        assert np.isnan(row["similar_day_rank3_demand_kwh"])
        assert pd.isna(row["similar_day_rank3_reference_date"])
        assert np.isnan(row["similar_day_rank3_distance"])
        assert row["similar_day_n_candidates"] == 2.0
        assert row[WEIGHTED_MEAN_COL] == pytest.approx(2 / 3 * loads[0] + 1 / 3 * loads[1])

    def test_a_zero_distance_takes_all_the_weight(self):
        records = make_records(make_scoring(days=(D,), refs=((364, 0.0), (7, 1.0), (2, 2.0))))
        assert (records.df[WEIGHTED_MEAN_COL] == records.df["similar_day_rank1_demand_kwh"]).all()

    def test_a_ranking_deeper_than_the_columns_is_rejected(self):
        scoring = make_scoring(days=(D,), refs=(*REFS, (3, 3.0)))
        with pytest.raises(ValueError, match="ranks beyond 3"):
            make_records(scoring)

    @pytest.mark.parametrize("rank", [1, 2, 3])
    @pytest.mark.parametrize("shape", ["public_at", "last_hour_public_at"])
    def test_the_latest_ranked_load_sets_the_availability(self, rank, shape):
        # Rank r's day (lags 364, 7, 2 in REFS) had its load re-issued at 05:00 on 04-09,
        # the whole day or its hour 24 alone: later than the forecast's 04-09 01:00 and
        # every other rank's load, before the 09:30 issue time.
        reissued = pd.Timestamp("2024-04-09 05:00")
        day = D - pd.Timedelta(days=REFS[rank - 1][0])
        hourly_load = make_hourly_load(**{shape: {day: reissued}})
        records = make_records(make_scoring(days=(D,)), hourly_load=hourly_load)
        assert records.df["available_at"].eq(reissued).all()
        assert records.df["available_at"].gt(forecast_available_at(D)).all()

    def test_a_same_holiday_reference_waits_for_its_last_hour(self):
        # 2023-03-21's hours 1-23 are public at 00:00 on 2023-03-22; hour 24 re-issued at
        # 05:00 on 2024-03-19, before 03-20's 09:30 issue time, dates the whole row.
        reissued = pd.Timestamp("2024-03-19 05:00")
        scoring = make_scoring(days=(D,), same_holiday=((SAME_HOLIDAY, SAME_HOLIDAY_REFERENCE),))
        hourly_load = make_hourly_load(last_hour_public_at={SAME_HOLIDAY_REFERENCE: reissued})
        records = make_records(scoring, hourly_load=hourly_load)
        rows = records.df[records.df["trade_date"] == SAME_HOLIDAY]
        assert len(rows) == 48
        assert rows["available_at"].eq(reissued).all()

    def test_a_ranked_load_public_after_the_issue_time_is_rejected(self):
        # Rank 3's day (04-08) public two days after it (a yearly-file day), 04-10 00:00,
        # follows the issue time: the pool never ranks such a day, and the frame rejects
        # the rows.
        late = make_hourly_load(late={D - pd.Timedelta(days=2)})
        with pytest.raises(ValueError, match="available_at must not follow the issue time"):
            make_records(make_scoring(days=(D,)), hourly_load=late)

    def test_a_fit_after_the_forecast_sets_the_availability(self):
        # A fit at 08:00 on D-1, after the forecast's 01:00 and before the 09:30 issue.
        cutoff = D - pd.Timedelta(days=1) + pd.Timedelta(hours=8)
        records = make_records(make_scoring(days=(D,), fit_cutoff=cutoff))
        assert records.df["available_at"].eq(cutoff).all()

    def test_an_empty_scoring_is_rejected(self):
        with pytest.raises(ValueError, match="no scored day to publish"):
            make_records(make_scoring(days=()))

    def test_a_similar_day_without_a_load_is_rejected(self):
        # Rank 2 of D lies at lag 7, whose load is missing.
        without = make_hourly_load(days=HISTORY_DAYS.drop(D - pd.Timedelta(days=7)))
        with pytest.raises(
            ValueError,
            match=r"48 period\(s\) have no load on their similar day, e.g. 2024-04-10 "
            r"time_code 1 \(similar day 2024-04-03\)",
        ):
            make_records(make_scoring(days=(D,)), hourly_load=without)
        # A same-holiday day's reference without a load.
        scoring = make_scoring(days=(), same_holiday=((SAME_HOLIDAY, SAME_HOLIDAY_REFERENCE),))
        without = make_hourly_load(days=HISTORY_DAYS.drop(SAME_HOLIDAY_REFERENCE))
        with pytest.raises(ValueError, match=r"48 period\(s\) have no load on their similar day"):
            make_records(scoring, hourly_load=without)

    def test_a_day_without_a_forecast_is_rejected(self):
        forecast = make_forecast(pd.date_range("2024-04-11", "2024-04-12"))
        with pytest.raises(
            ValueError, match=r"1 day\(s\) have no forecast availability, e.g. 2024-04-10"
        ):
            make_records(forecast=forecast)

    def test_the_frame_checks_its_rows(self):
        df = make_records(
            make_scoring(days=(D,), same_holiday=((SAME_HOLIDAY, SAME_HOLIDAY_REFERENCE),))
        ).df
        ranked = ranked_rows(df)
        same = ~ranked
        issued = issue_times(pd.DatetimeIndex(df["trade_date"]))
        cases = [
            (
                "must precede trade_date",
                with_values(df, ranked, similar_day_rank2_reference_date=D),
            ),
            ("must be positive", with_values(df, ranked, similar_day_rank3_demand_kwh=0.0)),
            ("time_code outside 1..48", df.assign(time_code=df["time_code"] + 48)),
            (
                "similar_day_fit_cutoff must not follow the issue time",
                df.assign(similar_day_fit_cutoff=issued + pd.Timedelta(minutes=1)),
            ),
            (
                "must not precede the fit's cutoff",
                with_values(df, ranked, available_at=pd.Timestamp("2024-03-01")),
            ),
            # D's issue time is 09:30 on 04-09; a row usable a minute later is never served.
            (
                "available_at must not follow the issue time",
                with_values(df, ranked, available_at=pd.Timestamp("2024-04-09 09:31")),
            ),
            ("one run per frame, got 2", df.assign(run_id=["a"] * 48 + ["b"] * 48)),
            ("similar_day_method must be one of", df.assign(similar_day_method="other")),
            ("carries rank 1 alone", with_values(df, same, similar_day_rank1_distance=1.0)),
            ("carries rank 1 alone", with_values(df, same, **{WEIGHTED_MEAN_COL: 1.0})),
            ("a similarity row needs", with_values(df, ranked, similar_day_n_candidates=np.nan)),
            (
                "distance must be present",
                with_values(df, ranked, similar_day_rank3_distance=np.nan),
            ),
            ("distance must not decrease", with_values(df, ranked, similar_day_rank1_distance=1.5)),
            (
                "a null rank must be followed by null ranks only",
                with_values(
                    df,
                    ranked,
                    similar_day_rank2_demand_kwh=np.nan,
                    similar_day_rank2_reference_date=pd.NaT,
                    similar_day_rank2_distance=np.nan,
                ),
            ),
            (
                "present exactly where similar_day_n_candidates reaches it",
                with_values(df, ranked, similar_day_n_candidates=2.0),
            ),
            (
                "must be a whole number",
                with_values(df, ranked, similar_day_n_candidates=87.5),
            ),
            (
                "reference day repeats within a row",
                with_values(df, ranked, similar_day_rank3_reference_date=D - pd.Timedelta(days=7)),
            ),
            (
                "weighted mean must lie between",
                with_values(
                    df,
                    ranked,
                    **{WEIGHTED_MEAN_COL: df.loc[ranked, "similar_day_rank3_demand_kwh"] * 2},
                ),
            ),
            (
                "similar_day_rank3_reference_date must be present exactly where",
                with_values(df, ranked, similar_day_rank3_reference_date=pd.NaT),
            ),
        ]
        SimilarDayFeatureRecords.from_df(df)
        for message, bad in cases:
            with pytest.raises(ValueError, match=message):
                SimilarDayFeatureRecords.from_df(bad)


class TestPublishFeatureRecords:
    def test_creates_the_partitioned_table_and_writes_the_rows(self, spark):
        scoring = make_scoring(same_holiday=((SAME_HOLIDAY, SAME_HOLIDAY_REFERENCE),))
        records = make_records(scoring, run_id="score-create")
        assert publish_feature_records(records, spark=spark) == 144
        assert spark.catalog.tableExists(FEATURE_TABLE)
        columns = {c.name: c for c in spark.catalog.listColumns(FEATURE_TABLE)}
        assert [name for name, c in columns.items() if c.isPartition] == ["run_id"]
        assert list(columns) == list(records.df.columns)
        types = {name: c.dataType for name, c in columns.items()}
        assert types == {
            "area_code": "string",
            "trade_date": "date",
            "time_code": "int",
            **{col: "double" for col in (*RANK_LOAD_COLS, WEIGHTED_MEAN_COL)},
            **{col: "date" for col in RANK_DATE_COLS},
            **{col: "double" for col in RANK_DISTANCE_COLS},
            "similar_day_n_candidates": "int",
            "similar_day_fit_cutoff": "timestamp",
            "similar_day_method": "string",
            "available_at": "timestamp",
            "published_at": "timestamp",
            "run_id": "string",
        }
        rows = published_rows(spark, "score-create").sort_values(["trade_date", "time_code"])
        assert len(rows) == 144
        first = rows.iloc[0]
        assert first["trade_date"] == SAME_HOLIDAY.date()
        assert first["similar_day_rank1_reference_date"] == SAME_HOLIDAY_REFERENCE.date()
        assert first["similar_day_rank1_demand_kwh"] == load_at(SAME_HOLIDAY_REFERENCE, 1) / 2
        ranked = rows[rows["trade_date"] == D.date()].iloc[0]
        assert ranked["time_code"] == 1
        assert ranked["similar_day_rank1_reference_date"] == (D - pd.Timedelta(days=364)).date()
        assert ranked["similar_day_rank3_demand_kwh"] == load_at(D - pd.Timedelta(days=2), 1) / 2
        assert ranked["similar_day_n_candidates"] == 87
        assert ranked["similar_day_fit_cutoff"] == CUTOFF
        assert ranked["similar_day_method"] == METHOD_SIMILARITY
        assert ranked["available_at"] == forecast_available_at(D)
        assert ranked["published_at"] == PUBLISHED_AT
        # The null columns of a same-holiday day are SQL nulls, not NaN doubles.
        count = spark.sql(
            f"select count(*) from {FEATURE_TABLE} where run_id = 'score-create' "
            "and similar_day_method = 'same_holiday' "
            "and similar_day_rank2_demand_kwh is null and similar_day_rank3_reference_date is null "
            "and similar_day_rank2_distance is null and similar_day_n_candidates is null "
            "and similar_day_fit_cutoff is null"
        ).first()[0]
        assert count == 48
        nan_doubles = " or ".join(
            f"isnan({col})" for col in (*RANK_LOAD_COLS, WEIGHTED_MEAN_COL, *RANK_DISTANCE_COLS)
        )
        assert (
            spark.sql(
                f"select count(*) from {FEATURE_TABLE} where run_id = 'score-create' "
                f"and ({nan_doubles})"
            ).first()[0]
            == 0
        )

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
