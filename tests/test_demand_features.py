"""Tests for the demand task's feature helpers: the hour alignment, the day-type
coding and the calendar-attribute join of the similar-day strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from power_market_analytics.tasks.demand.features import (
    CALENDAR_COUNT_FEATURE_COLS,
    DAY_CALENDAR_FEATURE_COLS,
    DAY_TYPE_CODES,
    DAY_TYPE_LEVELS,
    HOLIDAY_DEGREE_FEATURE_COLS,
    HOLIDAY_DISTANCE_FEATURE_COLS,
    day_type_code,
    hour_ending_of,
    join_day_calendar,
)
from power_market_analytics.tasks.demand.frames import DayCalendar
from tests.conftest import synthetic_calendar_counts

D = pd.Timestamp("2024-04-10").as_unit("ns")


def points(time_codes: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {"trade_date": [D] * len(time_codes), "time_code": np.array(time_codes, dtype="int64")}
    )


class TestConstants:
    def test_day_type_levels_and_codes(self):
        assert DAY_TYPE_LEVELS == ("Weekday", "Weekend", "Holiday")
        assert DAY_TYPE_CODES == {"Weekday": 0, "Weekend": 1, "Holiday": 2}


class TestHourEndingOf:
    def test_period_maps_to_the_observation_hour_containing_its_start(self):
        tc = pd.Series([1, 2, 3, 4, 23, 24, 47, 48], dtype="int64")
        assert hour_ending_of(tc).tolist() == [1, 1, 2, 2, 12, 12, 24, 24]
        assert hour_ending_of(tc).dtype == "int64"


class TestDayTypeCode:
    def test_holiday_wins_over_weekend(self):
        is_weekend = pd.Series([False, True, False, True])
        is_holiday = pd.Series([False, False, True, True])
        out = day_type_code(is_weekend, is_holiday)
        assert out.tolist() == [0, 1, 2, 2]
        assert out.dtype == "int64"

    def test_index_is_kept(self):
        index = [10, 20]
        out = day_type_code(
            pd.Series([True, False], index=index), pd.Series([False, False], index=index)
        )
        assert out.index.tolist() == index
        assert out.tolist() == [1, 0]


def make_day_calendar(days: dict[int, dict]) -> DayCalendar:
    """Build a DayCalendar around ``D``.

    Parameters
    ----------
    days : dict of int to dict
        Day offset from ``D`` mapped to column overrides; the calendar counts
        follow the date, the other columns default to a plain working day.

    Returns
    -------
    DayCalendar
    """
    rows = []
    for k, overrides in days.items():
        day = D + pd.Timedelta(days=k)
        rows.append(
            {
                "trade_date": day,
                "day_type": 0,
                "days_since_holiday": 2,
                "days_until_holiday": 3,
                "holiday_degree": 0.0,
                **synthetic_calendar_counts(day),
                "is_business_day": True,
                **overrides,
            }
        )
    counts = ("half", "quarter", "day_of_month", "day_of_quarter", "day_of_year", "fiscal_quarter")
    return DayCalendar.from_df(
        pd.DataFrame(rows).astype(
            {col: "int64" for col in ("day_type", "days_since_holiday", "days_until_holiday")}
            | {col: "int64" for col in counts}
        )
    )


class TestJoinDayCalendar:
    def test_feature_columns(self):
        assert DAY_CALENDAR_FEATURE_COLS == (
            "half",
            "quarter",
            "day_of_month",
            "day_of_quarter",
            "day_of_year",
            "holiday_degree",
            "is_business_day",
            "fiscal_quarter",
            "days_since_holiday",
            "days_until_holiday",
        )

    def test_each_period_gets_its_days_attributes_as_float64(self):
        # D = 2024-04-10: half 1, Q2, day 10 of the month and quarter, day 101 of
        # the year, fiscal Q1; the flag and the distances are the row's.
        calendar = make_day_calendar(
            {0: {"holiday_degree": 0.5, "days_since_holiday": 1, "days_until_holiday": 1}}
        )
        out = join_day_calendar(points([1, 2, 48]), calendar)
        assert list(out.columns) == ["trade_date", "time_code", *DAY_CALENDAR_FEATURE_COLS]
        assert all(out[col].dtype == "float64" for col in DAY_CALENDAR_FEATURE_COLS)
        expected = {
            "half": 1.0,
            "quarter": 2.0,
            "day_of_month": 10.0,
            "day_of_quarter": 10.0,
            "day_of_year": 101.0,
            "holiday_degree": 0.5,
            "is_business_day": 1.0,
            "fiscal_quarter": 1.0,
            "days_since_holiday": 1.0,
            "days_until_holiday": 1.0,
        }
        for col, value in expected.items():
            assert out[col].tolist() == [value] * 3, col

    def test_a_non_business_day_is_zero(self):
        out = join_day_calendar(points([1]), make_day_calendar({0: {"is_business_day": False}}))
        assert out["is_business_day"].iloc[0] == 0.0

    def test_day_without_a_calendar_row_gives_nan(self):
        out = join_day_calendar(points([1]), make_day_calendar({1: {}}))  # D+1 only
        assert out[list(DAY_CALENDAR_FEATURE_COLS)].isna().all(axis=None)

    def test_row_order_is_kept_across_days(self):
        next_day = D + pd.Timedelta(days=1)
        mixed = pd.DataFrame(
            {"trade_date": [next_day, D, next_day], "time_code": np.array([1, 1, 2], dtype="int64")}
        )
        out = join_day_calendar(mixed, make_day_calendar({0: {}, 1: {}}))
        assert out["trade_date"].tolist() == [next_day, D, next_day]
        assert out["time_code"].tolist() == [1, 1, 2]
        assert out["day_of_year"].tolist() == [102.0, 101.0, 102.0]

    def test_extra_point_columns_pass_through(self):
        out = join_day_calendar(points([1]).assign(month=4), make_day_calendar({0: {}}))
        assert list(out.columns) == ["trade_date", "time_code", "month", *DAY_CALENDAR_FEATURE_COLS]
        assert out["month"].iloc[0] == 4


class TestCalendarFeatureColumnSubsets:
    def test_the_three_subsets_of_the_calendar_features(self):
        assert HOLIDAY_DEGREE_FEATURE_COLS == ("holiday_degree",)
        assert HOLIDAY_DISTANCE_FEATURE_COLS == ("days_since_holiday", "days_until_holiday")
        assert CALENDAR_COUNT_FEATURE_COLS == (
            "half",
            "quarter",
            "day_of_month",
            "day_of_quarter",
            "day_of_year",
            "fiscal_quarter",
        )
        subsets = (HOLIDAY_DEGREE_FEATURE_COLS, HOLIDAY_DISTANCE_FEATURE_COLS)
        subsets += (CALENDAR_COUNT_FEATURE_COLS,)
        assert all(set(subset) < set(DAY_CALENDAR_FEATURE_COLS) for subset in subsets)
        # The three subsets are disjoint and leave out only the working-day flag.
        assert sum(len(subset) for subset in subsets) == len(DAY_CALENDAR_FEATURE_COLS) - 1
        assert set().union(*subsets) == set(DAY_CALENDAR_FEATURE_COLS) - {"is_business_day"}

    def test_join_day_calendar_with_the_counts_subset(self):
        # D = 2024-04-10: half 1, Q2, day 10 of the month and quarter, day 101 of
        # the year, fiscal Q1.
        out = join_day_calendar(
            points([1]), make_day_calendar({0: {}}), cols=CALENDAR_COUNT_FEATURE_COLS
        )
        assert list(out.columns) == ["trade_date", "time_code", *CALENDAR_COUNT_FEATURE_COLS]
        assert out.iloc[0][list(CALENDAR_COUNT_FEATURE_COLS)].tolist() == [
            1.0,
            2.0,
            10.0,
            10.0,
            101.0,
            1.0,
        ]

    def test_join_day_calendar_attaches_only_the_requested_columns(self):
        calendar = make_day_calendar(
            {0: {"holiday_degree": 0.5, "days_since_holiday": 1, "days_until_holiday": 4}}
        )
        out = join_day_calendar(points([1, 2]), calendar, cols=HOLIDAY_DISTANCE_FEATURE_COLS)
        assert list(out.columns) == [
            "trade_date",
            "time_code",
            "days_since_holiday",
            "days_until_holiday",
        ]
        assert out["days_since_holiday"].tolist() == [1.0, 1.0]
        assert out["days_until_holiday"].tolist() == [4.0, 4.0]
        degree = join_day_calendar(points([1]), calendar, cols=HOLIDAY_DEGREE_FEATURE_COLS)
        assert list(degree.columns) == ["trade_date", "time_code", "holiday_degree"]
        assert degree["holiday_degree"].iloc[0] == 0.5
