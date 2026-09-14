"""Tests for the learned similar-day selector (R-004 E-002)."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.lgbm import DEFAULT_TRAIN_WINDOW_DAYS
from power_market_analytics.tasks.demand.frames import (
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)
from power_market_analytics.tasks.demand.similar_day import (
    HOURS_PER_DAY,
    MIN_FIT_PAIRS,
    PERIODS_PER_HOUR,
    SIMILAR_DAY_BASELINE_LAG_DAYS,
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_FIT_WINDOW_DAYS,
    SIMILAR_DAY_POOL,
    SIMILAR_DAY_TOP_K,
    DayPairDifferences,
    SimilarDayPool,
    SimilarDayRanking,
    SimilarDayRetrieval,
    SimilarDaySelection,
    SimilarDaySelector,
    SimilarDayTrainingPairs,
    SimilarDayWeights,
    SpecialDayReferences,
    fit_similar_day_weights,
    inverse_distance_weights,
    load_difference,
    retrieval_metrics,
    special_day_references,
)

#: Calendar, observations and hourly load: 2023-01-01 .. 2024-04-30.
HISTORY_DAYS = pd.date_range("2023-01-01", "2024-04-30", freq="D")
#: Forecast profiles exist for these delivery days only.
FORECAST_DAYS = pd.date_range("2024-01-01", "2024-04-30", freq="D")
#: The synthetic calendar's holidays, each with its ``dim_date.holiday_name_ja``.
HOLIDAY_NAMES: dict[pd.Timestamp, str] = {
    pd.Timestamp("2023-01-09"): "成人の日",
    pd.Timestamp("2023-03-21"): "春分の日",
    pd.Timestamp("2023-05-03"): "憲法記念日",
    pd.Timestamp("2024-01-08"): "成人の日",
    pd.Timestamp("2024-03-20"): "春分の日",
    pd.Timestamp("2024-04-29"): "昭和の日",
}
#: The days of HOLIDAY_NAMES, in date order.
HOLIDAYS = tuple(HOLIDAY_NAMES)
D = pd.Timestamp("2024-04-10")  # a Wednesday; D - 364 = 2023-04-12, also a Wednesday
D_MINUS_364 = D - pd.Timedelta(days=364)


def temperature_at(day: pd.Timestamp, hour: int) -> float:
    doy = day.dayofyear
    return (
        10.0
        + 12.0 * math.sin(2 * math.pi * (doy - 100) / 365)
        + 4.0 * math.sin(2 * math.pi * (hour - 9) / 24)
    )


def humidity_at(day: pd.Timestamp, hour: int) -> float:
    return 60.0 + 10.0 * math.cos(2 * math.pi * hour / 24) + (day.dayofyear % 7)


def rain_at(day: pd.Timestamp, hour: int) -> float:
    return 1.0 if day.dayofyear % 9 == 0 and 12 <= hour <= 15 else 0.0


def load_at(day: pd.Timestamp, hour: int) -> float:
    weekend = -5_000_000.0 if day.dayofweek >= 5 or day in HOLIDAYS else 0.0
    return (
        30_000_000.0
        - 8_000_000.0 * math.cos(2 * math.pi * hour / 24)
        + weekend
        + 1_000.0 * (day - HISTORY_DAYS[0]).days
    )


def holiday_degree_at(day: pd.Timestamp) -> float:
    if day in HOLIDAYS or day.dayofweek == 6:
        return 1.0
    return 0.8 if day.dayofweek == 5 else 0.0


def make_calendar(days=HISTORY_DAYS) -> DayCalendar:
    holidays = sorted(HOLIDAYS)
    rows = []
    for day in days:
        before = [h for h in holidays if h <= day]
        after = [h for h in holidays if h >= day]
        if not before or not after:
            continue
        rows.append(
            {
                "trade_date": day,
                "is_holiday": day in HOLIDAYS,
                "holiday_name_ja": HOLIDAY_NAMES.get(day),
                "days_since_holiday": (day - before[-1]).days,
                "days_until_holiday": (after[0] - day).days,
                "holiday_degree": holiday_degree_at(day),
            }
        )
    return DayCalendar.from_df(
        pd.DataFrame(rows).astype(
            {"is_holiday": "bool", "days_since_holiday": "int64", "days_until_holiday": "int64"}
        )
    )


def make_named_calendar(names: dict[str, str], start: str, end: str) -> DayCalendar:
    """A calendar of ``start`` .. ``end`` whose holidays are ``names`` (ISO date -> name).

    Days before the first holiday or after the last have no holiday distance and are
    left out, as ``load_day_calendar`` does.
    """
    holidays = pd.DatetimeIndex(sorted(pd.Timestamp(day) for day in names))
    rows = []
    for day in pd.date_range(start, end, freq="D"):
        before = holidays[holidays <= day]
        after = holidays[holidays >= day]
        if before.empty or after.empty:
            continue
        is_holiday = day in holidays
        if is_holiday or day.dayofweek == 6:
            degree = 1.0
        else:
            degree = 0.8 if day.dayofweek == 5 else 0.0
        rows.append(
            {
                "trade_date": day,
                "is_holiday": is_holiday,
                "holiday_name_ja": names.get(str(day.date())),
                "days_since_holiday": (day - before[-1]).days,
                "days_until_holiday": (after[0] - day).days,
                "holiday_degree": degree,
            }
        )
    return DayCalendar.from_df(
        pd.DataFrame(rows).astype(
            {"is_holiday": "bool", "days_since_holiday": "int64", "days_until_holiday": "int64"}
        )
    )


def make_forecast(
    days=FORECAST_DAYS, *, drop: Collection[tuple[pd.Timestamp, int]] = ()
) -> AreaWeatherForecast:
    rows = [
        {
            "trade_date": day,
            "hour_ending": h,
            "forecast_temperature_c": temperature_at(day, h) + 0.5,
            "forecast_relative_humidity_pct": humidity_at(day, h) - 2.0,
            "forecast_precipitation_mm": 0.8 * rain_at(day, h),
            "available_at": forecast_available_at(day),
        }
        for day in days
        for h in range(1, 25)
        if (day, h) not in drop
    ]
    return AreaWeatherForecast.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


def forecast_available_at(day: pd.Timestamp) -> pd.Timestamp:
    """When D's forecast vintage is public: 01:00 on D-1 (the D-2 12 UTC run + 4 h)."""
    return day - pd.Timedelta(days=1) + pd.Timedelta(hours=1)


def make_observed(
    days=HISTORY_DAYS, *, null_hours: Collection[tuple[pd.Timestamp, int]] = ()
) -> AreaObservedWeather:
    rows = [
        {
            "obs_date": day,
            "hour_ending": h,
            "temperature_c": np.nan if (day, h) in null_hours else temperature_at(day, h),
            "humidity_pct": humidity_at(day, h),
            "precipitation_mm": rain_at(day, h),
        }
        for day in days
        for h in range(1, 25)
    ]
    return AreaObservedWeather.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


def make_hourly_load(
    days=HISTORY_DAYS,
    *,
    late: Collection[pd.Timestamp] = (),
    public_at: Mapping[pd.Timestamp, pd.Timestamp] | None = None,
) -> AreaHourlyLoad:
    """Loads public at midnight after the day (a daily file); two days after for ``late``
    days (the yearly files before 2022-04); at ``public_at[day]`` for the days it names
    (a re-issued file)."""
    public_at = public_at or {}
    rows = [
        {
            "load_date": day,
            "hour_ending": h,
            "demand_kwh": load_at(day, h),
            "available_at": public_at.get(day, day + pd.Timedelta(days=2 if day in late else 1)),
        }
        for day in days
        for h in range(1, 25)
    ]
    return AreaHourlyLoad.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


@pytest.fixture(scope="module")
def selector() -> SimilarDaySelector:
    return SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())


class TestConstants:
    def test_values(self):
        assert SIMILAR_DAY_POOL == SimilarDayPool(((2, 31), (335, 394)))
        assert SIMILAR_DAY_TOP_K == 3
        assert SIMILAR_DAY_BASELINE_LAG_DAYS == 7
        assert SIMILAR_DAY_FIT_WINDOW_DAYS == DEFAULT_TRAIN_WINDOW_DAYS == 730
        assert PERIODS_PER_HOUR == 2
        assert HOURS_PER_DAY == 24
        assert MIN_FIT_PAIRS == 8
        assert SIMILAR_DAY_COMPONENTS == (
            "calendar_days",
            "temperature",
            "humidity",
            "rain",
            "days_since_holiday",
            "days_until_holiday",
            "holiday_degree",
        )


class TestSimilarDayPool:
    def test_the_papers_pool(self):
        assert SIMILAR_DAY_POOL.windows == ((2, 31), (335, 394))
        assert SIMILAR_DAY_POOL.lags.tolist() == [*range(2, 32), *range(335, 395)]
        assert SIMILAR_DAY_POOL.lags.dtype == "int64"
        assert SIMILAR_DAY_POOL.year_ago == (335, 394)
        assert SIMILAR_DAY_POOL.as_param() == "2-31,335-394"
        assert SIMILAR_DAY_TOP_K == 3

    @pytest.mark.parametrize(
        "windows",
        [
            (),
            ((0, 5),),
            ((5, 4),),
            ((2, 10), (10, 20)),
            ((335, 394), (2, 31)),
            (2, 31),
            ((2, 10, 20),),
            ((2.0, 31),),
            ((True, 31),),
        ],
        ids=[
            "empty",
            "newest-below-one",
            "reversed",
            "overlapping",
            "descending",
            "not-a-sequence-of-windows",
            "not-a-pair",
            "float",
            "bool",
        ],
    )
    def test_a_bad_pool_is_rejected(self, windows):
        with pytest.raises(ValueError, match="pool"):
            SimilarDayPool(windows)

    def test_adjacent_windows_are_accepted(self):
        assert SimilarDayPool(((2, 10), (11, 20))).lags.tolist() == list(range(2, 21))

    def test_any_sequence_of_whole_number_pairs_is_stored_as_tuples(self):
        pool = SimilarDayPool([[2, 31], [np.int64(335), np.int64(394)]])
        assert pool == SIMILAR_DAY_POOL
        assert hash(pool) == hash(SIMILAR_DAY_POOL)
        assert all(type(lag) is int for window in pool.windows for lag in window)


class TestSelectorSetup:
    def test_pool_and_candidates(self, selector):
        assert selector.pool == SIMILAR_DAY_POOL
        assert selector.lags.tolist() == [*range(2, 32), *range(335, 395)]
        assert selector.calendar.df.equals(make_calendar().df)
        # Candidates need a calendar row: the calendar starts at the first holiday.
        assert selector.first_candidate_day == HOLIDAYS[0]
        assert selector.hourly_load_span == (HISTORY_DAYS[0], HISTORY_DAYS[-1])

    def test_first_scorable_day(self, selector):
        assert selector.first_scorable_day == HOLIDAYS[0] + pd.Timedelta(days=394)
        none = SimilarDaySelector(
            make_calendar(),
            make_forecast(pd.date_range("2024-01-01", "2024-01-05")),
            make_observed(),
            make_hourly_load(),
        )
        assert none.first_scorable_day is None

    def test_bad_fit_window_is_rejected(self):
        with pytest.raises(ValueError, match="fit window"):
            SimilarDaySelector(
                make_calendar(),
                make_forecast(),
                make_observed(),
                make_hourly_load(),
                fit_window_days=0,
            )

    def test_no_candidates_is_rejected(self):
        with pytest.raises(ValueError, match="no candidate days"):
            SimilarDaySelector(
                make_calendar(),
                make_forecast(),
                make_observed(pd.date_range("2022-01-01", "2022-01-05")),
                make_hourly_load(),
            )

    def test_scorable_days(self, selector):
        days = [
            D,
            pd.Timestamp("2023-12-31"),  # no forecast profile
            pd.Timestamp("2024-01-20"),  # oldest pool day 2022-12-22 precedes the first candidate
            pd.Timestamp("2024-04-30"),  # calendar ends at the last holiday 04-29
            D,  # duplicate
        ]
        assert selector.scorable_days(days).tolist() == [D]


class TestDifferences:
    def test_one_row_per_pool_day(self, selector):
        diffs = selector.differences([D])
        assert type(diffs) is DayPairDifferences
        # 90 pool days less the three holidays in it: 2023-03-21 (lag 386),
        # 2023-05-03 (lag 343) and 2024-03-20 (lag 21).
        assert len(diffs) == 87
        assert list(diffs.df.columns) == ["target_date", "candidate_date", *SIMILAR_DAY_COMPONENTS]
        lags = (diffs.df["target_date"] - diffs.df["candidate_date"]).dt.days
        year_ago = [lag for lag in range(394, 334, -1) if lag not in (386, 343)]
        recent = [lag for lag in range(31, 1, -1) if lag != 21]
        assert lags.tolist() == [*year_ago, *recent]

    def test_calendar_part_is_the_lag(self, selector):
        df = selector.differences([D]).df.set_index("candidate_date")
        assert df.loc[D_MINUS_364, "calendar_days"] == 364.0
        assert df.loc[D - pd.Timedelta(days=2), "calendar_days"] == 2.0
        assert df.loc[D - pd.Timedelta(days=394), "calendar_days"] == 394.0

    def test_a_holiday_is_not_a_candidate(self, selector):
        assert (
            pd.Timestamp("2024-03-20")
            not in selector.differences([D]).df["candidate_date"].tolist()
        )

    def test_a_special_target_keeps_its_pool(self, selector):
        # Only training drops special targets. 2024-03-20's pool is 2023-02-20 .. 2023-04-20
        # and 2024-02-18 .. 2024-03-18: 90 days less the holiday 2023-03-21 (lag 365).
        diffs = selector.differences([pd.Timestamp("2024-03-20")]).df
        assert len(diffs) == 89
        assert not diffs["candidate_date"].isin(HOLIDAYS).any()

    def test_a_candidate_public_after_the_issue_time_is_left_out(self, selector):
        # D - 2's load is public at 00:00 on D - 1, before the 09:30 issue time; a day
        # later it misses it.
        d_minus_2 = D - pd.Timedelta(days=2)
        assert d_minus_2 in selector.differences([D]).df["candidate_date"].tolist()
        late = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(late={d_minus_2}),
        )
        diffs = late.differences([D]).df
        assert len(diffs) == 86
        assert d_minus_2 not in diffs["candidate_date"].tolist()

    def test_weather_parts_are_hourly_rmse_of_forecast_against_observed(self, selector):
        row = selector.differences([D]).df.set_index("candidate_date").loc[D_MINUS_364]
        expected_t = math.sqrt(
            np.mean(
                [
                    (temperature_at(D, h) + 0.5 - temperature_at(D_MINUS_364, h)) ** 2
                    for h in range(1, 25)
                ]
            )
        )
        expected_h = math.sqrt(
            np.mean(
                [(humidity_at(D, h) - 2.0 - humidity_at(D_MINUS_364, h)) ** 2 for h in range(1, 25)]
            )
        )
        expected_r = math.sqrt(
            np.mean([(0.8 * rain_at(D, h) - rain_at(D_MINUS_364, h)) ** 2 for h in range(1, 25)])
        )
        assert row["temperature"] == pytest.approx(expected_t)
        assert row["humidity"] == pytest.approx(expected_h)
        assert row["rain"] == pytest.approx(expected_r)

    def test_holiday_parts_are_absolute_differences(self, selector):
        calendar = make_calendar().df.set_index("trade_date")
        row = selector.differences([D]).df.set_index("candidate_date").loc[D_MINUS_364]
        for col in ("days_since_holiday", "days_until_holiday", "holiday_degree"):
            assert row[col] == pytest.approx(
                abs(calendar.loc[D, col] - calendar.loc[D_MINUS_364, col])
            )

    def test_a_candidate_missing_an_observed_hour_is_left_out(self):
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(null_hours={(D_MINUS_364, 5)}),
            make_hourly_load(),
        )
        diffs = selector.differences([D]).df
        assert len(diffs) == 86
        assert D_MINUS_364 not in set(diffs["candidate_date"])

    def test_unscorable_days_yield_no_rows(self, selector):
        assert len(selector.differences([pd.Timestamp("2023-12-31")])) == 0


class TestPairFrames:
    def test_negative_part_is_rejected(self, selector):
        df = selector.differences([D]).df.copy()
        df.loc[0, "rain"] = -0.1
        with pytest.raises(ValueError, match="rain must be >= 0"):
            DayPairDifferences.from_df(df)

    def test_candidate_after_target_is_rejected(self, selector):
        df = selector.differences([D]).df.copy()
        df.loc[0, "candidate_date"] = D
        with pytest.raises(ValueError, match="candidate_date must precede target_date"):
            DayPairDifferences.from_df(df)

    def test_training_pairs_need_a_non_negative_load_difference(self, selector):
        df = selector.differences([D]).df.assign(load_difference=-1.0)
        with pytest.raises(ValueError, match="load_difference must be >= 0"):
            SimilarDayTrainingPairs.from_df(df)

    def test_selection_rejects_a_reference_on_or_after_the_day(self):
        base = {
            "distance": [1.0],
            "reference_lag_days": np.array([0], dtype="int64"),
            "n_candidates": np.array([87], dtype="int64"),
            "lag_7_rank": [1.0],
        }
        with pytest.raises(ValueError, match="reference_date must precede trade_date"):
            SimilarDaySelection.from_df(
                pd.DataFrame({"trade_date": [D], "reference_date": [D], **base})
            )
        future = {**base, "reference_lag_days": np.array([-1], dtype="int64")}
        with pytest.raises(ValueError, match="reference_date must precede trade_date"):
            SimilarDaySelection.from_df(
                pd.DataFrame(
                    {"trade_date": [D], "reference_date": [D + pd.Timedelta(days=1)], **future}
                )
            )

    def test_retrieval_rejects_a_reference_on_or_after_the_day(self):
        row = {
            "trade_date": [D],
            "reference_date": [D_MINUS_364],
            "distance": [1.0],
            "selected_load_difference": [0.1],
            "lag_7_load_difference": [0.1],
            "oracle_date": [D],
            "oracle_load_difference": [0.05],
            "selected_rank_by_outcome": np.array([2], dtype="int64"),
        }
        with pytest.raises(ValueError, match="oracle_date must precede trade_date"):
            SimilarDayRetrieval.from_df(pd.DataFrame(row))

    def test_selection_checks_the_lag(self):
        df = pd.DataFrame(
            {
                "trade_date": [D],
                "reference_date": [D_MINUS_364],
                "distance": [1.0],
                "reference_lag_days": np.array([363], dtype="int64"),
                "n_candidates": np.array([87], dtype="int64"),
                "lag_7_rank": [1.0],
            }
        )
        with pytest.raises(ValueError, match="reference_lag_days must equal"):
            SimilarDaySelection.from_df(df)

    def test_ranking_accepts_a_valid_frame(self):
        assert len(SimilarDayRanking.from_df(ranking_rows())) == 3

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            (
                {
                    "reference_date": [D - pd.Timedelta(days=364), D - pd.Timedelta(days=7), D],
                    "reference_lag_days": np.array([364, 7, 0], dtype="int64"),
                },
                "reference_date must precede trade_date",
            ),
            (
                {"reference_lag_days": np.array([364, 7, 3], dtype="int64")},
                "reference_lag_days must equal",
            ),
            ({"rank": np.array([1, 2, 4], dtype="int64")}, "rank must run 1"),
            ({"distance": [0.5, 2.0, 1.0]}, "distance must not decrease by rank"),
            ({"distance": [-0.5, 1.0, 2.0]}, "distance must be >= 0"),
            (
                {
                    "reference_date": [
                        D - pd.Timedelta(days=364),
                        D - pd.Timedelta(days=7),
                        D - pd.Timedelta(days=7),
                    ],
                    "reference_lag_days": np.array([364, 7, 7], dtype="int64"),
                },
                "a reference day repeats within a day",
            ),
        ],
        ids=[
            "reference-on-the-day",
            "lag",
            "ranks-with-a-gap",
            "decreasing-distance",
            "negative-distance",
            "repeat",
        ],
    )
    def test_ranking_rejects_a_bad_row(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            SimilarDayRanking.from_df(ranking_rows(**overrides))

    def test_ranking_ranks_start_at_one_on_every_day(self):
        # A second day that holds rank 2 alone.
        df = pd.concat(
            [
                ranking_rows(),
                pd.DataFrame(
                    {
                        "trade_date": [D + pd.Timedelta(days=1)],
                        "rank": np.array([2], dtype="int64"),
                        "reference_date": [D - pd.Timedelta(days=1)],
                        "reference_lag_days": np.array([2], dtype="int64"),
                        "distance": [1.0],
                    }
                ),
            ],
            ignore_index=True,
        )
        with pytest.raises(ValueError, match="rank must run 1"):
            SimilarDayRanking.from_df(df)

    def test_special_day_references_accept_a_valid_frame(self):
        assert len(SpecialDayReferences.from_df(special_day_rows())) == 1

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            (
                {"last_year_date": [pd.NaT], "last_year_lag_days": [np.nan]},
                "takes_reference needs last_year_date",
            ),
            (
                {"last_year_date": [pd.Timestamp("2024-03-20")], "last_year_lag_days": [0.0]},
                "last_year_date must precede trade_date",
            ),
            ({"last_year_lag_days": [364.0]}, "last_year_lag_days must equal"),
            (
                {"last_year_date": [pd.NaT], "takes_reference": [False]},
                "last_year_lag_days must equal",
            ),
        ],
        ids=["takes-without-a-date", "date-on-the-day", "lag", "lag-without-a-date"],
    )
    def test_special_day_references_reject_a_bad_row(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            SpecialDayReferences.from_df(special_day_rows(**overrides))


def ranking_rows(**overrides) -> pd.DataFrame:
    """D's valid three ranks at lags 364, 7 and 2 with distances 0.5, 1 and 2."""
    columns = {
        "trade_date": [D] * 3,
        "rank": np.array([1, 2, 3], dtype="int64"),
        "reference_date": [
            D - pd.Timedelta(days=364),
            D - pd.Timedelta(days=7),
            D - pd.Timedelta(days=2),
        ],
        "reference_lag_days": np.array([364, 7, 2], dtype="int64"),
        "distance": [0.5, 1.0, 2.0],
    }
    return pd.DataFrame({**columns, **overrides})


def special_day_rows(**overrides) -> pd.DataFrame:
    """A valid same-holiday row: 春分の日 2024-03-20 takes 2023-03-21, 365 days back."""
    columns = {
        "trade_date": [pd.Timestamp("2024-03-20")],
        "holiday_name_ja": ["春分の日"],
        "last_year_date": [pd.Timestamp("2023-03-21")],
        "last_year_lag_days": [365.0],
        "takes_reference": [True],
    }
    return pd.DataFrame({**columns, **overrides})


class TestInverseDistanceWeights:
    def test_hand_weights(self):
        w = inverse_distance_weights(np.array([[0.5, 1.0, 2.0]]))
        np.testing.assert_allclose(w, [[4 / 7, 2 / 7, 1 / 7]])

    def test_equal_distances_share_equally(self):
        np.testing.assert_allclose(
            inverse_distance_weights(np.array([[0.3, 0.3, 0.3]])), [[1 / 3] * 3]
        )

    def test_a_zero_distance_takes_all_the_weight(self):
        np.testing.assert_allclose(
            inverse_distance_weights(np.array([[0.0, 0.0, 1.0]])), [[0.5, 0.5, 0.0]]
        )

    def test_a_missing_rank_is_skipped(self):
        w = inverse_distance_weights(np.array([[1.0, 1.0, np.nan]]))
        np.testing.assert_allclose(w[:, :2], [[0.5, 0.5]])
        assert np.isnan(w[0, 2])

    def test_rows_are_independent_and_sum_to_one(self):
        w = inverse_distance_weights(np.array([[0.2, 0.4, 0.8], [1.0, np.nan, np.nan]]))
        np.testing.assert_allclose(np.nansum(w, axis=1), [1.0, 1.0])
        np.testing.assert_allclose(w[1, 0], 1.0)

    def test_a_zero_distance_on_one_day_leaves_the_others_alone(self):
        w = inverse_distance_weights(np.array([[0.0, 1.0, 2.0], [0.5, 1.0, 2.0]]))
        np.testing.assert_allclose(w, [[1.0, 0.0, 0.0], [4 / 7, 2 / 7, 1 / 7]])

    def test_a_day_without_any_rank_is_rejected(self):
        with pytest.raises(ValueError, match="no distance"):
            inverse_distance_weights(np.array([[np.nan, np.nan, np.nan]]))

    def test_a_negative_distance_is_rejected(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            inverse_distance_weights(np.array([[-0.1, 1.0, 2.0]]))

    def test_the_input_must_be_days_by_ranks(self):
        with pytest.raises(ValueError, match="days × k"):
            inverse_distance_weights(np.array([0.5, 1.0, 2.0]))


class TestRankingWeights:
    def test_each_days_weights_come_from_its_own_ranks(self):
        other = D + pd.Timedelta(days=1)
        second_day = pd.DataFrame(
            {
                "trade_date": [other, other],
                "rank": np.array([1, 2], dtype="int64"),
                "reference_date": [other - pd.Timedelta(days=364), other - pd.Timedelta(days=7)],
                "reference_lag_days": np.array([364, 7], dtype="int64"),
                "distance": [0.5, 1.0],
            }
        )
        # Rows out of order: the weights follow each day's ranks, not the row order.
        ranking = SimilarDayRanking.from_df(
            pd.concat([second_day, ranking_rows()], ignore_index=True)
        )
        weighted = ranking.with_weights()
        assert list(weighted.columns) == [*SimilarDayRanking.schema, "weight"]
        assert weighted["trade_date"].tolist() == [D, D, D, other, other]
        assert weighted["rank"].tolist() == [1, 2, 3, 1, 2]
        np.testing.assert_allclose(weighted["weight"], [4 / 7, 2 / 7, 1 / 7, 2 / 3, 1 / 3])

    def test_an_empty_ranking_has_an_empty_weight_column(self):
        empty = SimilarDayRanking.from_df(ranking_rows().iloc[:0])
        weighted = empty.with_weights()
        assert list(weighted.columns) == [*SimilarDayRanking.schema, "weight"]
        assert weighted["weight"].dtype == "float64"
        assert weighted.empty


class TestSpecialDayReferences:
    def test_the_same_holiday_last_year_inside_the_window(self):
        calendar = make_named_calendar(
            {"2025-01-13": "成人の日", "2026-01-12": "成人の日"}, "2025-01-01", "2026-01-31"
        )
        refs = special_day_references(calendar, [pd.Timestamp("2026-01-12")], SIMILAR_DAY_POOL)
        assert type(refs) is SpecialDayReferences
        row = refs.df.iloc[0]
        assert row["holiday_name_ja"] == "成人の日"
        assert row["last_year_date"] == pd.Timestamp("2025-01-13")
        assert row["last_year_lag_days"] == 364.0
        assert bool(row["takes_reference"])
        assert refs.same_holiday_days.tolist() == [pd.Timestamp("2026-01-12")]

    def test_a_holiday_moved_outside_the_window_is_ranked(self):
        calendar = make_named_calendar(
            {"2019-10-14": "スポーツの日", "2020-07-24": "スポーツの日"}, "2019-10-01", "2020-08-31"
        )
        refs = special_day_references(calendar, [pd.Timestamp("2020-07-24")], SIMILAR_DAY_POOL)
        row = refs.df.iloc[0]
        assert row["last_year_date"] == pd.Timestamp("2019-10-14")
        assert row["last_year_lag_days"] == 284.0
        assert not bool(row["takes_reference"])
        assert refs.same_holiday_days.empty

    def test_a_name_missing_last_year_is_ranked(self):
        calendar = make_named_calendar(
            {"2019-02-11": "建国記念の日", "2020-02-23": "天皇誕生日（令和）"},
            "2019-02-01",
            "2020-03-31",
        )
        refs = special_day_references(calendar, [pd.Timestamp("2020-02-23")], SIMILAR_DAY_POOL)
        row = refs.df.iloc[0]
        assert pd.isna(row["last_year_date"]) and np.isnan(row["last_year_lag_days"])
        assert not bool(row["takes_reference"])
        assert refs.same_holiday_days.empty

    def test_a_substitute_holiday_without_last_years_name_is_ranked(self):
        calendar = make_named_calendar(
            {"2023-05-05": "こどもの日", "2024-05-06": "こどもの日（振替休日）"},
            "2023-05-01",
            "2024-05-31",
        )
        refs = special_day_references(calendar, [pd.Timestamp("2024-05-06")], SIMILAR_DAY_POOL)
        assert not bool(refs.df.iloc[0]["takes_reference"])

    def test_ordinary_days_and_days_outside_the_calendar_have_no_row(self):
        refs = special_day_references(
            make_calendar(),
            [D, pd.Timestamp("2030-01-01"), pd.Timestamp("2024-03-20")],
            SIMILAR_DAY_POOL,
        )
        assert refs.df["trade_date"].tolist() == [pd.Timestamp("2024-03-20")]

    def test_no_special_day_gives_an_empty_frame(self):
        refs = special_day_references(make_calendar(), [D], SIMILAR_DAY_POOL)
        assert refs.df.empty
        assert list(refs.df.columns) == list(SpecialDayReferences.schema)
        assert refs.df.dtypes.astype(str).to_dict() == SpecialDayReferences.schema
        assert refs.same_holiday_days.empty
        assert special_day_references(make_calendar(), [], SIMILAR_DAY_POOL).df.empty

    def test_the_synthetic_calendar(self):
        refs = special_day_references(
            make_calendar(), [HOLIDAYS[-1], pd.Timestamp("2024-03-20")], SIMILAR_DAY_POOL
        )
        # Sorted by day; 昭和の日 has no 2023 row in the synthetic calendar.
        assert refs.df["trade_date"].tolist() == [pd.Timestamp("2024-03-20"), HOLIDAYS[-1]]
        assert refs.same_holiday_days.tolist() == [pd.Timestamp("2024-03-20")]
        by_day = refs.df.set_index("trade_date")
        assert by_day.loc[pd.Timestamp("2024-03-20"), "last_year_date"] == pd.Timestamp(
            "2023-03-21"
        )
        assert by_day.loc[pd.Timestamp("2024-03-20"), "last_year_lag_days"] == 365.0

    def test_the_window_comes_from_the_pool(self):
        calendar = make_named_calendar(
            {"2025-01-13": "成人の日", "2026-01-12": "成人の日"}, "2025-01-01", "2026-01-31"
        )
        refs = special_day_references(
            calendar, [pd.Timestamp("2026-01-12")], SimilarDayPool(((2, 31), (365, 400)))
        )
        assert refs.same_holiday_days.empty
        # Both bounds are inclusive.
        for pool in (SimilarDayPool(((2, 31), (364, 400))), SimilarDayPool(((2, 31), (300, 364)))):
            assert not special_day_references(
                calendar, [pd.Timestamp("2026-01-12")], pool
            ).same_holiday_days.empty

    def test_a_reference_published_after_the_issue_time_is_ranked(self):
        calendar = make_named_calendar(
            {"2025-01-13": "成人の日", "2026-01-12": "成人の日"}, "2025-01-01", "2026-01-31"
        )
        day = pd.Timestamp("2026-01-12")
        issued = pd.Timestamp("2026-01-11 09:30")
        # Public at the issue time counts; a minute later (a re-issued file) does not.
        for public_at, takes in ((issued, True), (issued + pd.Timedelta(minutes=1), False)):
            refs = special_day_references(
                calendar,
                [day],
                SIMILAR_DAY_POOL,
                load_available_at=pd.Series({pd.Timestamp("2025-01-13"): public_at}),
            )
            row = refs.df.iloc[0]
            assert bool(row["takes_reference"]) is takes
            assert row["last_year_date"] == pd.Timestamp("2025-01-13")
        # A reference with no load has no availability: it still counts, so building
        # its rows raises.
        refs = special_day_references(
            calendar,
            [day],
            SIMILAR_DAY_POOL,
            load_available_at=pd.Series({pd.Timestamp("2025-01-14"): issued}),
        )
        assert refs.same_holiday_days.tolist() == [day]

    def test_the_selector_applies_its_loads_availability(self, selector):
        day = pd.Timestamp("2024-03-20")
        assert selector.special_day_references([day]).same_holiday_days.tolist() == [day]
        # 2023-03-21's load re-issued after 2024-03-20's issue time: 03-20 is ranked.
        late = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(
                public_at={pd.Timestamp("2023-03-21"): pd.Timestamp("2024-03-19 10:00")}
            ),
        )
        refs = late.special_day_references([day])
        assert refs.same_holiday_days.empty
        assert refs.df["last_year_date"].tolist() == [pd.Timestamp("2023-03-21")]


class TestWindowPairCounts:
    def test_the_first_fits_pairs_by_hand(self, selector):
        # The pool of a target T is lags 2..31 and 335..394 less the holidays in it
        # (2023-01-09, 2023-03-21, 2024-01-08). 02-07: 01-08 in the recent window,
        # 2023-01-09 in the year-ago one → 88; 02-08: 01-08 only → 89; 02-09..02-14:
        # none → 90 each; the fit of 02-15 therefore holds 88 + 89 + 6 × 90 = 717 pairs.
        pairs = selector.training_pairs(pd.Timestamp("2024-02-15")).df
        per_target = pairs.groupby("target_date").size()
        assert per_target.tolist() == [88, 89, 90, 90, 90, 90, 90, 90]
        assert len(pairs) == 717


class TestLoadDifference:
    def test_mean_absolute_relative_difference_per_row(self):
        target = np.array([[100.0] * 24, [200.0] * 24])
        candidate = np.array([[110.0] * 24, [150.0] * 24])
        assert load_difference(target, candidate).tolist() == pytest.approx([0.1, 0.25])


class TestTrainingPairs:
    def test_targets_whose_load_was_public_by_the_instant(self, selector):
        # Loads are public at midnight after the day: a fit on 04-01 sees targets to 03-31.
        through = pd.Timestamp("2024-03-31")
        pairs = selector.training_pairs(through + pd.Timedelta(days=1))
        assert type(pairs) is SimilarDayTrainingPairs
        # The first scorable forecast day: its oldest lag must reach the first candidate.
        first = HOLIDAYS[0] + pd.Timedelta(days=394)
        targets = pairs.df["target_date"].unique()
        assert targets.min() == first
        assert targets.max() == through
        # 02-07 .. 03-31 is 54 days; the holiday 03-20 is no target.
        assert len(targets) == 53
        assert pd.Timestamp("2024-03-20") not in targets
        # 53 × 90 pool days, less each holiday on the targets whose pool holds it:
        # 2023-01-09 (02-07), 2023-03-21 (41 targets 02-19 .. 03-31), 2024-01-08
        # (02-07, 02-08) and 2024-03-20 (03-22 .. 03-31): 1 + 41 + 2 + 10 = 54.
        assert len(pairs) == 53 * 90 - 54 == 4_716
        holidays = set(HOLIDAYS)
        assert not pairs.df["candidate_date"].isin(holidays).any()
        # A minute earlier, 03-31's load is not public yet.
        earlier = selector.training_pairs(through + pd.Timedelta(days=1) - pd.Timedelta(minutes=1))
        assert earlier.df["target_date"].max() == through - pd.Timedelta(days=1)
        t, c = D - pd.Timedelta(days=14), D_MINUS_364 - pd.Timedelta(days=14)
        row = pairs.df.set_index(["target_date", "candidate_date"]).loc[(t, c)]
        expected = np.mean(
            [abs(load_at(t, h) - load_at(c, h)) / load_at(t, h) for h in range(1, 25)]
        )
        assert row["load_difference"] == pytest.approx(expected)

    def test_the_pairs_are_computed_once_and_sliced_by_availability(self, selector):
        everything = selector.training_pairs(HISTORY_DAYS[-1] + pd.Timedelta(days=1))
        early = selector.training_pairs(pd.Timestamp("2024-03-01"))
        assert len(early) < len(everything)
        assert early.df["target_date"].max() == pd.Timestamp("2024-02-29")
        # The first fit can run once the first scorable day's load is public.
        assert selector.first_fit_cutoff == HOLIDAYS[0] + pd.Timedelta(days=395)
        # The same frame object serves every call.
        assert selector._all_training_pairs() is selector._all_training_pairs()

    def test_a_late_load_joins_the_pairs_later(self):
        first = HOLIDAYS[0] + pd.Timedelta(days=394)
        selector = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load(late={first})
        )
        assert selector.first_fit_cutoff == first + pd.Timedelta(days=2)
        assert len(selector.training_pairs(first + pd.Timedelta(days=1))) == 0
        # Two days on, the late first day (88 pairs: 2023-01-09 and 2024-01-08 are in its
        # pool) and the timely next day (89: 2024-01-08 at lag 31) are both public.
        assert len(selector.training_pairs(first + pd.Timedelta(days=2))) == 88 + 89

    def test_no_pairs_before_the_first_scorable_day(self, selector):
        assert len(selector.training_pairs(pd.Timestamp("2024-01-31"))) == 0

    def test_first_fit_cutoff_is_none_without_pairs(self):
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(pd.date_range("2023-01-01", "2024-01-31")),
        )
        assert selector.first_fit_cutoff is None

    def test_first_fit_cutoff_waits_for_enough_pairs(self):
        # A pool of three days: the first scorable days give too few pairs for a
        # fit, so the cutoff is the eighth public pair's, not the first day's.
        narrow = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(),
            pool=SimilarDayPool(((363, 365),)),
        )
        first_day = narrow.scorable_days(FORECAST_DAYS)[0]
        assert first_day == pd.Timestamp("2024-01-09")
        cutoff = narrow.first_fit_cutoff
        # 01-09 holds 2 pairs (its lag 365 is the holiday 2023-01-09), 01-10 and 01-11
        # three each: the eighth pair is public when 01-11's load is.
        assert cutoff == pd.Timestamp("2024-01-12")
        assert len(narrow.training_pairs(cutoff)) == 2 + 3 + 3 >= MIN_FIT_PAIRS
        assert len(narrow.training_pairs(cutoff - pd.Timedelta(minutes=1))) < MIN_FIT_PAIRS
        # Fewer than eight pairs in total: no fit, ever.
        tiny = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(pd.date_range("2023-01-01", first_day)),
            pool=SimilarDayPool(((363, 365),)),
        )
        assert len(tiny.training_pairs(HISTORY_DAYS[-1])) < MIN_FIT_PAIRS
        assert tiny.first_fit_cutoff is None

    def test_the_fit_window_keeps_the_targets_of_the_days_before_the_cutoff(self):
        # A window of ten days: a fit on 04-01 sees the targets 03-22..03-31.
        windowed = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(),
            fit_window_days=10,
        )
        assert windowed.fit_window_days == 10
        pairs = windowed.training_pairs(pd.Timestamp("2024-04-01"))
        targets = pairs.df["target_date"].unique()
        assert targets.min() == pd.Timestamp("2024-03-22")
        assert targets.max() == pd.Timestamp("2024-03-31")
        # Each target's pool loses 2023-03-21 (lag 367 .. 376) and 2024-03-20 (lag 2 .. 11).
        assert len(pairs) == 10 * 88
        # The window counts calendar days before the cutoff's day, whatever the hour.
        later = windowed.training_pairs(pd.Timestamp("2024-04-01 10:15"))
        assert later.df["target_date"].unique().tolist() == targets.tolist()
        # Until the window fills, the pairs are the ones without a window: 02-07 .. 02-11,
        # less 2023-01-09 and 2024-01-08 on 02-07 and 2024-01-08 on 02-08.
        assert len(windowed.training_pairs(pd.Timestamp("2024-02-12"))) == 5 * 90 - 3 == 447

    def test_first_fit_cutoff_counts_the_pairs_inside_the_fit_window(self):
        # At most three candidates a day and a window of two days: at most six pairs
        # are ever inside the window, so no fit can run. Three days hold eight.
        def narrow(fit_window_days: int) -> SimilarDaySelector:
            return SimilarDaySelector(
                make_calendar(),
                make_forecast(),
                make_observed(),
                make_hourly_load(),
                pool=SimilarDayPool(((363, 365),)),
                fit_window_days=fit_window_days,
            )

        assert narrow(2).first_fit_cutoff is None
        cutoff = narrow(3).first_fit_cutoff
        assert cutoff == narrow(SIMILAR_DAY_FIT_WINDOW_DAYS).first_fit_cutoff
        assert len(narrow(3).training_pairs(cutoff)) == 8

    def test_a_special_target_leaves_the_training_pairs(self, selector):
        pairs = selector.training_pairs(pd.Timestamp("2024-04-30"))
        assert pd.Timestamp("2024-03-20") not in pairs.df["target_date"].tolist()
        # The day after the holiday is still a target.
        assert pd.Timestamp("2024-03-21") in pairs.df["target_date"].tolist()

    def test_a_training_candidate_public_after_its_targets_issue_time_is_left_out(self):
        # 03-25's load is public at 00:00 on 03-27: after 03-27's issue time (09:30 on
        # 03-26), before 03-28's (09:30 on 03-27).
        late_day = pd.Timestamp("2024-03-25")
        selector = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load(late={late_day})
        )
        pairs = selector.training_pairs(pd.Timestamp("2024-04-30")).df.set_index(
            ["target_date", "candidate_date"]
        )
        assert (pd.Timestamp("2024-03-27"), late_day) not in pairs.index
        assert (pd.Timestamp("2024-03-28"), late_day) in pairs.index


def planted_pairs(
    n: int = 400, seed: int = 0
) -> tuple[SimilarDayTrainingPairs, np.ndarray, float, float]:
    rng = np.random.default_rng(seed)
    parts = np.abs(rng.normal(size=(n, 7))) * np.array([10, 3, 8, 0.5, 5, 5, 0.4])
    planted = np.array([0.30, 0.25, 0.05, 0.05, 0.15, 0.10, 0.10])
    scales = np.sqrt(np.mean(parts**2, axis=0))
    distance = np.sqrt(((parts / scales) ** 2) @ planted)
    alpha, beta = 2.0, 0.1
    y = alpha * distance + beta
    days = pd.date_range("2024-02-07", periods=n, freq="D")
    df = pd.DataFrame(parts, columns=list(SIMILAR_DAY_COMPONENTS)).assign(
        target_date=days, candidate_date=days - pd.Timedelta(days=364), load_difference=y
    )
    return SimilarDayTrainingPairs.from_df(df), planted, alpha, beta


class TestFitSimilarDayWeights:
    def test_recovers_planted_weights(self):
        pairs, planted, alpha, beta = planted_pairs()
        fitted = fit_similar_day_weights(pairs)
        assert type(fitted) is SimilarDayWeights
        assert fitted.components == SIMILAR_DAY_COMPONENTS
        np.testing.assert_allclose(fitted.weights, planted, atol=1e-3)
        assert fitted.weights.sum() == pytest.approx(1.0)
        assert (fitted.weights >= 0).all()
        assert fitted.alpha == pytest.approx(alpha, abs=1e-3)
        assert fitted.beta == pytest.approx(beta, abs=1e-3)
        assert fitted.fit_rmse == pytest.approx(0.0, abs=1e-6)
        assert fitted.n_pairs == 400
        assert fitted.n_targets == 400
        assert fitted.fit_from == pd.Timestamp("2024-02-07")
        assert fitted.fit_through == pd.Timestamp("2024-02-07") + pd.Timedelta(days=399)
        # distance() reproduces the planted distance up to the fitted weights.
        np.testing.assert_allclose(
            fitted.distance(pairs), (pairs.df["load_difference"] - beta) / alpha, atol=1e-3
        )

    def test_as_params(self):
        fitted = fit_similar_day_weights(planted_pairs()[0])
        params = fitted.as_params()
        assert set(params) == {
            "similar_day_weights",
            "similar_day_scales",
            "similar_day_alpha",
            "similar_day_beta",
            "similar_day_fit_n_pairs",
            "similar_day_fit_n_targets",
            "similar_day_fit_from",
            "similar_day_fit_through",
            "similar_day_fit_rmse",
        }
        assert str(params["similar_day_weights"]).startswith("calendar_days=0.30")
        assert params["similar_day_fit_from"] == "2024-02-07"
        assert params["similar_day_fit_n_pairs"] == 400

    def test_too_few_pairs(self):
        pairs, _, _, _ = planted_pairs(n=MIN_FIT_PAIRS - 1)
        with pytest.raises(
            ValueError, match=f"{MIN_FIT_PAIRS - 1} training pairs; at least {MIN_FIT_PAIRS}"
        ):
            fit_similar_day_weights(pairs)

    def test_all_parts_zero_fits_the_mean(self):
        days = pd.date_range("2024-02-07", periods=MIN_FIT_PAIRS, freq="D")
        df = pd.DataFrame(
            np.zeros((MIN_FIT_PAIRS, 7)), columns=list(SIMILAR_DAY_COMPONENTS)
        ).assign(
            target_date=days,
            candidate_date=days - pd.Timedelta(days=364),
            load_difference=np.arange(MIN_FIT_PAIRS) / 10,
        )
        fitted = fit_similar_day_weights(SimilarDayTrainingPairs.from_df(df))
        assert fitted.beta == pytest.approx(np.mean(np.arange(MIN_FIT_PAIRS) / 10), abs=1e-6)
        assert (fitted.scales == 1.0).all()

    def test_solver_failure(self, monkeypatch):
        import power_market_analytics.tasks.demand.similar_day as module

        class Failed:
            success = False
            message = "maximum iterations"

        monkeypatch.setattr(module, "least_squares", lambda *a, **k: Failed())
        with pytest.raises(RuntimeError, match="similar-day weight fit failed: maximum iterations"):
            fit_similar_day_weights(planted_pairs()[0])


class TestSelectorFit:
    def test_weights_before_fit_raise(self):
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.weights

    def test_fit_once_and_reuse(self):
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        first = fresh.ensure_fitted(pd.Timestamp("2024-04-01"))
        assert fresh.weights is first
        assert fresh.ensure_fitted(pd.Timestamp("2024-04-16")) is first
        assert first.fit_through == pd.Timestamp("2024-03-31")
        assert first.n_pairs == len(fresh.training_pairs(pd.Timestamp("2024-04-01")))

    def test_fit_without_pairs_raises(self, selector):
        with pytest.raises(ValueError, match="no training pairs public by 2024-01-31 00:00:00"):
            selector.fit(pd.Timestamp("2024-01-31"))


@pytest.fixture(scope="module")
def fitted(selector) -> SimilarDaySelector:
    selector.ensure_fitted(pd.Timestamp("2024-04-01"))
    return selector


def hand_weights(**shares: float) -> SimilarDayWeights:
    weights = np.array([shares.get(c, 0.0) for c in SIMILAR_DAY_COMPONENTS])
    return SimilarDayWeights(
        components=SIMILAR_DAY_COMPONENTS,
        weights=weights / weights.sum(),
        scales=np.ones(7),
        alpha=1.0,
        beta=0.0,
        n_pairs=8,
        n_targets=8,
        fit_from=pd.Timestamp("2024-02-07"),
        fit_through=pd.Timestamp("2024-02-14"),
        fit_rmse=0.0,
    )


class TestSelect:
    def test_nearest_candidate(self, fitted):
        selection = fitted.select([D, pd.Timestamp("2023-12-31")])
        assert type(selection) is SimilarDaySelection
        assert len(selection) == 1
        row = selection.df.iloc[0]
        assert row["trade_date"] == D
        assert row["reference_lag_days"] in SIMILAR_DAY_POOL.lags
        assert row["n_candidates"] == 87
        assert 1 <= row["lag_7_rank"] <= 87
        diffs = fitted.differences([D])
        distances = pd.Series(fitted.weights.distance(diffs), index=diffs.df["candidate_date"])
        assert row["distance"] == pytest.approx(distances.min())
        assert row["reference_date"] == distances.idxmin()
        # D - 7's rank counts the candidates strictly nearer than it.
        at_lag_7 = distances[D - pd.Timedelta(days=SIMILAR_DAY_BASELINE_LAG_DAYS)]
        assert row["lag_7_rank"] == (distances < at_lag_7).sum() + 1

    def test_rank_returns_the_three_nearest(self, fitted):
        ranking = fitted.rank([D, pd.Timestamp("2023-12-31")])
        assert type(ranking) is SimilarDayRanking
        df = ranking.df
        assert df["trade_date"].tolist() == [D] * SIMILAR_DAY_TOP_K
        assert df["rank"].tolist() == [1, 2, 3]
        diffs = fitted.differences([D])
        nearest = np.sort(fitted.weights.distance(diffs))[:3]
        np.testing.assert_array_equal(df["distance"].to_numpy(), nearest)
        assert (df["reference_lag_days"] == (D - df["reference_date"]).dt.days).all()
        selected = fitted.select([D]).df.iloc[0]
        assert df.iloc[0]["reference_date"] == selected["reference_date"]
        assert df.iloc[0]["distance"] == selected["distance"]
        # One call gives both, and they agree with the two wrappers.
        selection, both = fitted.select_and_rank([D])
        assert selection.df.equals(fitted.select([D]).df)
        assert both.df.equals(df)
        assert len(fitted.rank([D], 5)) == 5

    def test_tie_goes_to_the_smaller_lag(self):
        selector = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        # A lag-only distance: D - 2 is nearest.
        selector._weights = hand_weights(calendar_days=1.0)
        assert selector.select([D]).df.iloc[0]["reference_date"] == D - pd.Timedelta(days=2)
        # A holiday-degree-only distance: every weekday candidate of the Wednesday D is at
        # distance 0, so the smaller lags win: 04-08 (Mon, lag 2), then 04-05 (Fri, 5) and
        # 04-04 (Thu, 6); the weekend 04-06 and 04-07 are further.
        selector._weights = hand_weights(holiday_degree=1.0)
        ranking = selector.rank([D]).df
        assert ranking["reference_lag_days"].tolist() == [2, 5, 6]
        assert ranking["distance"].tolist() == [0.0, 0.0, 0.0]
        selected = selector.select([D]).df.iloc[0]
        assert selected["reference_date"] == D - pd.Timedelta(days=2)
        # D - 7 (04-03, a Wednesday) is at distance 0 too: tied days share the smallest
        # rank, 1, not the tie-broken 4 it would take after lags 2, 5 and 6.
        assert selected["lag_7_rank"] == 1.0

    def test_lag_7_rank_is_nan_when_d_minus_7_is_not_a_candidate(self):
        without_lag_7 = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(null_hours={(D - pd.Timedelta(days=7), 1)}),
            make_hourly_load(),
        )
        without_lag_7._weights = hand_weights(calendar_days=1.0)
        row = without_lag_7.select([D]).df.iloc[0]
        assert row["reference_date"] == D - pd.Timedelta(days=2)
        assert np.isnan(row["lag_7_rank"])
        assert row["n_candidates"] == 86

    def test_fewer_candidates_than_k(self):
        narrow = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(),
            pool=SimilarDayPool(((364, 365),)),
        )
        narrow._weights = hand_weights(calendar_days=1.0)
        ranking = narrow.rank([D], 3).df
        assert ranking["rank"].tolist() == [1, 2]
        assert ranking["reference_lag_days"].tolist() == [364, 365]
        row = narrow.select([D]).df.iloc[0]
        assert row["n_candidates"] == 2
        assert np.isnan(row["lag_7_rank"])

    def test_a_ranked_special_day_skips_the_holidays_in_its_pool(self, fitted):
        # 昭和の日 2024-04-29 has no 2023 namesake, so it is ranked. Its pool, 2024-03-29 ..
        # 04-27 and 2023-04-01 .. 05-30, holds the holiday 2023-05-03 (lag 362).
        day = HOLIDAYS[-1]
        assert special_day_references(fitted.calendar, [day], fitted.pool).same_holiday_days.empty
        ranking = fitted.rank([day], 90).df
        assert len(ranking) == 89
        assert not ranking["reference_date"].isin(HOLIDAYS).any()
        assert fitted.select([day]).df.iloc[0]["n_candidates"] == 89

    def test_nothing_scorable_gives_empty_frames(self, fitted):
        selection, ranking = fitted.select_and_rank([pd.Timestamp("2023-12-31")])
        assert len(selection) == 0
        assert list(selection.df.columns) == list(SimilarDaySelection.schema)
        assert len(ranking) == 0
        assert ranking.df.dtypes.astype(str).to_dict() == SimilarDayRanking.schema
        assert len(fitted.rank([pd.Timestamp("2023-12-31")])) == 0

    def test_k_must_be_at_least_one(self, fitted):
        with pytest.raises(ValueError, match="k must be at least 1"):
            fitted.rank([D], 0)

    def test_select_and_rank_before_fit_raise(self):
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.select([D])
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.rank([D])
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.rank([pd.Timestamp("2023-12-31")])


class TestRetrieval:
    def test_outcomes_per_forecast_day(self, fitted):
        days = [D, D + pd.Timedelta(days=1), pd.Timestamp("2024-04-30")]  # 04-30: no calendar row
        selection = fitted.select(days)
        retrieval = fitted.retrieval(selection)
        assert type(retrieval) is SimilarDayRetrieval
        assert retrieval.df["trade_date"].tolist() == [D, D + pd.Timedelta(days=1)]
        row = retrieval.df.set_index("trade_date").loc[D]
        sel = selection.df.set_index("trade_date").loc[D]
        assert row["reference_date"] == sel["reference_date"]
        assert row["distance"] == sel["distance"]
        candidates = fitted.differences([D]).df["candidate_date"]
        realised = {
            c: np.mean([abs(load_at(D, h) - load_at(c, h)) / load_at(D, h) for h in range(1, 25)])
            for c in candidates
        }
        assert row["selected_load_difference"] == pytest.approx(realised[sel["reference_date"]])
        assert row["lag_7_load_difference"] == pytest.approx(realised[D - pd.Timedelta(days=7)])
        assert row["oracle_load_difference"] == pytest.approx(min(realised.values()))
        # Ties go to the smaller lag.
        assert row["oracle_date"] == min(realised, key=lambda c: (realised[c], D - c))
        assert row["oracle_load_difference"] <= row["selected_load_difference"]
        assert row["selected_rank_by_outcome"] >= 1

    def test_lag_7_is_nan_when_it_was_not_a_candidate(self):
        selector = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(null_hours={(D - pd.Timedelta(days=7), 1)}),
            make_hourly_load(),
        )
        selector.ensure_fitted(pd.Timestamp("2024-04-01"))
        retrieval = selector.retrieval(selector.select([D]))
        assert np.isnan(retrieval.df.iloc[0]["lag_7_load_difference"])

    def test_days_without_a_known_load_are_left_out(self):
        # A forecast day after the hourly load ends: selectable, not checkable.
        beyond = SimilarDaySelector(
            make_calendar(),
            make_forecast(),
            make_observed(),
            make_hourly_load(pd.date_range("2023-01-01", "2024-04-09")),
        )
        beyond.ensure_fitted(pd.Timestamp("2024-04-01"))
        retrieval = beyond.retrieval(beyond.select([D]))
        assert len(retrieval) == 0
        assert list(retrieval.df.columns) == list(SimilarDayRetrieval.schema)


class TestRetrievalMetrics:
    def test_means_and_share(self):
        df = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-04-10", "2024-04-11", "2024-04-12"]),
                "reference_date": pd.to_datetime(["2023-04-12", "2023-04-13", "2023-04-14"]),
                "distance": [1.0, 1.0, 1.0],
                "selected_load_difference": [0.02, 0.05, 0.03],
                "lag_7_load_difference": [0.04, 0.04, np.nan],
                "oracle_date": pd.to_datetime(["2023-04-12", "2023-04-20", "2023-04-14"]),
                "oracle_load_difference": [0.02, 0.01, 0.03],
                "selected_rank_by_outcome": np.array([1, 5, 1], dtype="int64"),
            }
        )
        metrics = retrieval_metrics(SimilarDayRetrieval.from_df(df))
        assert metrics == {
            "similar_day_load_difference_selected": pytest.approx(0.1 / 3),
            "similar_day_load_difference_lag_7": pytest.approx(0.04),
            "similar_day_load_difference_oracle": pytest.approx(0.02),
            "similar_day_share_better_than_lag_7": pytest.approx(0.5),
        }


class TestSelectorParams:
    def test_the_pool_the_parts_the_weights_and_the_span(self, fitted):
        params = fitted.as_params()
        assert params["similar_day_pool"] == "2-31,335-394"
        assert "similar_day_center_lag_days" not in params
        assert "similar_day_window_half_width_days" not in params
        assert params["similar_day_fit_window_days"] == SIMILAR_DAY_FIT_WINDOW_DAYS
        assert params["similar_day_components"] == ",".join(SIMILAR_DAY_COMPONENTS)
        assert params["similar_day_weights"] == fitted.weights.as_params()["similar_day_weights"]
        assert params["similar_day_first_selectable_day"] == "2024-02-07"
        assert params["similar_day_hourly_load_span"] == "2023-01-01..2024-04-30"
        assert params["similar_day_periods_per_hour"] == PERIODS_PER_HOUR

    def test_before_a_fit_raises(self):
        fresh = SimilarDaySelector(
            make_calendar(), make_forecast(), make_observed(), make_hourly_load()
        )
        with pytest.raises(RuntimeError, match="not fitted"):
            fresh.as_params()

    def test_a_selector_without_a_scorable_day_reports_none(self):
        none = SimilarDaySelector(
            make_calendar(),
            make_forecast(pd.date_range("2024-01-01", "2024-01-05")),
            make_observed(),
            make_hourly_load(),
        )
        none._weights = fit_similar_day_weights(planted_pairs()[0])
        assert none.as_params()["similar_day_first_selectable_day"] == "none"
