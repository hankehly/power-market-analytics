"""Tests for the demand task's features: the temperature features (recency-weighted
observed, forecast) and the day-type categorical."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.features import (
    DAY_CALENDAR_FEATURE_COLS,
    DAY_TYPE_FEATURE,
    DAY_TYPE_LEVELS,
    FORECAST_TEMPERATURE_FEATURE,
    HOLIDAY_DEGREE_FEATURE_COLS,
    HOLIDAY_DISTANCE_FEATURE_COLS,
    POPW_FORECAST_TEMPERATURE_FEATURE,
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    day_type_code,
    hour_ending_of,
    join_day_calendar,
    join_day_type,
    join_forecast_temperature,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import (
    AreaTemperature,
    AreaTemperatureForecast,
    DayCalendar,
    DayTypeCalendar,
)
from tests.conftest import synthetic_calendar_counts

D = pd.Timestamp("2024-04-10").as_unit("ns")


def make_temperature(values: dict[tuple[int, int], float]) -> AreaTemperature:
    """AreaTemperature from {(lag_days_before_D, hour_ending): temperature_c}."""
    return AreaTemperature.from_df(
        pd.DataFrame(
            {
                "obs_date": [D - pd.Timedelta(days=k) for (k, _) in values],
                "hour_ending": np.array([h for (_, h) in values], dtype="int64"),
                "temperature_c": np.array(list(values.values()), dtype="float64"),
            }
        )
    )


def points(time_codes: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {"trade_date": [D] * len(time_codes), "time_code": np.array(time_codes, dtype="int64")}
    )


def make_forecast(values: dict[tuple[int, int], float]) -> AreaTemperatureForecast:
    """AreaTemperatureForecast from {(days_after_D, hour_ending): forecast_temperature_c}."""
    return AreaTemperatureForecast.from_df(
        pd.DataFrame(
            {
                "trade_date": [D + pd.Timedelta(days=k) for (k, _) in values],
                "hour_ending": np.array([h for (_, h) in values], dtype="int64"),
                "forecast_temperature_c": np.array(list(values.values()), dtype="float64"),
            }
        )
    )


class TestConstants:
    def test_defaults(self):
        assert TEMPERATURE_LAG_DAYS == (2, 3, 4, 5, 6, 7, 8)
        assert TEMPERATURE_HALF_LIFE_DAYS == 1.0
        assert TEMPERATURE_FEATURE == "wavg_temperature_c"
        assert FORECAST_TEMPERATURE_FEATURE == "forecast_temperature_c"
        assert POPW_FORECAST_TEMPERATURE_FEATURE == "popw_forecast_temperature_c"
        assert DAY_TYPE_FEATURE == "day_type"
        assert DAY_TYPE_LEVELS == ("Weekday", "Weekend", "Holiday")


class TestHourEndingOf:
    def test_period_maps_to_the_observation_hour_containing_its_start(self):
        tc = pd.Series([1, 2, 3, 4, 23, 24, 47, 48], dtype="int64")
        assert hour_ending_of(tc).tolist() == [1, 1, 2, 2, 12, 12, 24, 24]
        assert hour_ending_of(tc).dtype == "int64"


class TestRecencyWeightedTemperature:
    def test_all_seven_lags_present_and_equal_returns_that_value(self):
        temperature = make_temperature({(k, 1): 12.0 for k in range(2, 9)})
        out = recency_weighted_temperature(points([1]), temperature)
        assert list(out.columns) == ["trade_date", "time_code", TEMPERATURE_FEATURE]
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(12.0)

    def test_weights_halve_per_day_back(self):
        # D-2 = 10 (weight 1), D-3 = 20 (weight 0.5), D-4 = 40 (weight 0.25).
        temperature = make_temperature({(2, 1): 10.0, (3, 1): 20.0, (4, 1): 40.0})
        out = recency_weighted_temperature(points([1]), temperature)
        expected = (10 * 1 + 20 * 0.5 + 40 * 0.25) / (1 + 0.5 + 0.25)
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(expected)

    def test_missing_lags_are_dropped_and_weights_renormalised(self):
        # D-2 missing entirely, D-3 = 20, D-8 = 8: (20 * 0.5 + 8 * 2**-6) / (0.5 + 2**-6).
        temperature = make_temperature({(3, 1): 20.0, (8, 1): 8.0})
        out = recency_weighted_temperature(points([1]), temperature)
        expected = (20 * 0.5 + 8 * 2**-6) / (0.5 + 2**-6)
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(expected)

    def test_null_temperature_counts_as_missing(self):
        temperature = make_temperature({(2, 1): np.nan, (3, 1): 20.0})
        out = recency_weighted_temperature(points([1]), temperature)
        assert out[TEMPERATURE_FEATURE].iloc[0] == pytest.approx(20.0)

    def test_all_lags_missing_gives_nan(self):
        temperature = make_temperature({(9, 1): 5.0, (1, 1): 5.0})  # outside D-8..D-2
        out = recency_weighted_temperature(points([1]), temperature)
        assert np.isnan(out[TEMPERATURE_FEATURE].iloc[0])

    def test_each_period_uses_its_own_hour_and_row_order_is_kept(self):
        temperature = make_temperature({(2, 1): 10.0, (2, 2): 30.0, (2, 24): 50.0})
        out = recency_weighted_temperature(points([48, 3, 1, 2]), temperature)
        assert out["time_code"].tolist() == [48, 3, 1, 2]
        assert out[TEMPERATURE_FEATURE].tolist() == pytest.approx([50.0, 30.0, 10.0, 10.0])

    def test_lag_days_and_half_life_are_configurable(self):
        temperature = make_temperature({(1, 1): 10.0, (2, 1): 20.0})
        out = recency_weighted_temperature(
            points([1]), temperature, lag_days=(1, 2), half_life_days=2.0, name="t"
        )
        w2 = 0.5 ** (1 / 2.0)
        assert out["t"].iloc[0] == pytest.approx((10 + 20 * w2) / (1 + w2))

    def test_empty_lag_days_rejected(self):
        with pytest.raises(ValueError, match="lag_days must not be empty"):
            recency_weighted_temperature(points([1]), make_temperature({(2, 1): 1.0}), lag_days=())

    def test_extra_point_columns_pass_through(self):
        temperature = make_temperature({(2, 1): 10.0})
        out = recency_weighted_temperature(points([1]).assign(month=4), temperature)
        assert list(out.columns) == ["trade_date", "time_code", "month", TEMPERATURE_FEATURE]


class TestJoinForecastTemperature:
    def test_each_period_gets_the_forecast_of_the_hour_containing_it(self):
        forecast = make_forecast({(0, 1): 10.0, (0, 2): 30.0, (0, 24): 50.0})
        out = join_forecast_temperature(points([1, 2, 3, 48]), forecast)
        assert list(out.columns) == ["trade_date", "time_code", FORECAST_TEMPERATURE_FEATURE]
        assert out[FORECAST_TEMPERATURE_FEATURE].tolist() == [10.0, 10.0, 30.0, 50.0]

    def test_row_order_is_kept(self):
        forecast = make_forecast({(0, 1): 10.0, (0, 2): 30.0})
        out = join_forecast_temperature(points([3, 1]), forecast)
        assert out["time_code"].tolist() == [3, 1]
        assert out[FORECAST_TEMPERATURE_FEATURE].tolist() == [30.0, 10.0]

    def test_day_without_a_forecast_gives_nan(self):
        forecast = make_forecast({(1, 1): 10.0})  # D+1 only
        out = join_forecast_temperature(points([1]), forecast)
        assert np.isnan(out[FORECAST_TEMPERATURE_FEATURE].iloc[0])

    def test_missing_hour_gives_nan_for_its_periods_only(self):
        forecast = make_forecast({(0, 1): 10.0})  # hour 2 absent
        out = join_forecast_temperature(points([1, 2, 3, 4]), forecast)
        assert out[FORECAST_TEMPERATURE_FEATURE].isna().tolist() == [False, False, True, True]

    def test_name_is_configurable_and_extra_point_columns_pass_through(self):
        forecast = make_forecast({(0, 1): 10.0})
        out = join_forecast_temperature(points([1]).assign(month=4), forecast, name="t")
        assert list(out.columns) == ["trade_date", "time_code", "month", "t"]
        assert out["t"].iloc[0] == 10.0


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


def make_calendar(codes: dict[int, int]) -> DayTypeCalendar:
    """DayTypeCalendar from {days_after_D: day_type code}."""
    return DayTypeCalendar.from_df(
        pd.DataFrame(
            {
                "trade_date": [D + pd.Timedelta(days=k) for k in codes],
                "day_type": np.array(list(codes.values()), dtype="int64"),
            }
        )
    )


class TestJoinDayType:
    def test_each_period_gets_its_days_code_as_float64(self):
        calendar = make_calendar({0: 2, 1: 0})
        out = join_day_type(points([1, 2, 48]), calendar)
        assert list(out.columns) == ["trade_date", "time_code", DAY_TYPE_FEATURE]
        assert out[DAY_TYPE_FEATURE].tolist() == [2.0, 2.0, 2.0]
        assert out[DAY_TYPE_FEATURE].dtype == "float64"

    def test_day_without_a_calendar_row_gives_nan(self):
        calendar = make_calendar({1: 0})  # D+1 only
        out = join_day_type(points([1]), calendar)
        assert np.isnan(out[DAY_TYPE_FEATURE].iloc[0])

    def test_row_order_is_kept_across_days(self):
        next_day = D + pd.Timedelta(days=1)
        mixed = pd.DataFrame(
            {"trade_date": [next_day, D, next_day], "time_code": np.array([1, 1, 2], dtype="int64")}
        )
        out = join_day_type(mixed, make_calendar({0: 2, 1: 1}))
        assert out["trade_date"].tolist() == [next_day, D, next_day]
        assert out["time_code"].tolist() == [1, 1, 2]
        assert out[DAY_TYPE_FEATURE].tolist() == [1.0, 2.0, 1.0]

    def test_name_is_configurable_and_extra_point_columns_pass_through(self):
        out = join_day_type(points([1]).assign(month=4), make_calendar({0: 1}), name="dt")
        assert list(out.columns) == ["trade_date", "time_code", "month", "dt"]
        assert out["dt"].iloc[0] == 1.0


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


class TestHolidayFeatureColumnSubsets:
    def test_the_two_subsets_of_the_calendar_features(self):
        assert HOLIDAY_DEGREE_FEATURE_COLS == ("holiday_degree",)
        assert HOLIDAY_DISTANCE_FEATURE_COLS == ("days_since_holiday", "days_until_holiday")
        assert set(HOLIDAY_DEGREE_FEATURE_COLS) < set(DAY_CALENDAR_FEATURE_COLS)
        assert set(HOLIDAY_DISTANCE_FEATURE_COLS) < set(DAY_CALENDAR_FEATURE_COLS)

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
