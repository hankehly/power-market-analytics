"""Tests for the learned similar-day selector (R-004 E-002)."""

from __future__ import annotations

import math
from collections.abc import Collection

import numpy as np
import pandas as pd
import pytest

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
    SIMILAR_DAY_CENTER_LAG_DAYS,
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_FEATURE,
    SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    DayPairDifferences,
    SimilarDaySelection,
    SimilarDaySelector,
    SimilarDayTrainingPairs,
)

#: Calendar, observations and hourly load: 2023-01-01 .. 2024-04-30.
HISTORY_DAYS = pd.date_range("2023-01-01", "2024-04-30", freq="D")
#: Forecast profiles exist for these delivery days only.
FORECAST_DAYS = pd.date_range("2024-01-01", "2024-04-30", freq="D")
HOLIDAYS = (
    pd.Timestamp("2023-01-09"),
    pd.Timestamp("2023-03-21"),
    pd.Timestamp("2023-05-03"),
    pd.Timestamp("2024-01-08"),
    pd.Timestamp("2024-03-20"),
    pd.Timestamp("2024-04-29"),
)
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
                "day_type": 2 if day in HOLIDAYS else (1 if day.dayofweek >= 5 else 0),
                "days_since_holiday": (day - before[-1]).days,
                "days_until_holiday": (after[0] - day).days,
                "holiday_degree": holiday_degree_at(day),
            }
        )
    return DayCalendar.from_df(
        pd.DataFrame(rows).astype(
            {"day_type": "int64", "days_since_holiday": "int64", "days_until_holiday": "int64"}
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
        }
        for day in days
        for h in range(1, 25)
        if (day, h) not in drop
    ]
    return AreaWeatherForecast.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


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


def make_hourly_load(days=HISTORY_DAYS) -> AreaHourlyLoad:
    rows = [
        {"load_date": day, "hour_ending": h, "demand_kwh": load_at(day, h)}
        for day in days
        for h in range(1, 25)
    ]
    return AreaHourlyLoad.from_df(pd.DataFrame(rows).astype({"hour_ending": "int64"}))


@pytest.fixture(scope="module")
def selector() -> SimilarDaySelector:
    return SimilarDaySelector(make_calendar(), make_forecast(), make_observed(), make_hourly_load())


class TestConstants:
    def test_values(self):
        assert SIMILAR_DAY_CENTER_LAG_DAYS == 364
        assert SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS == 30
        assert SIMILAR_DAY_FEATURE == "similar_day_demand_kwh"
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


class TestSelectorSetup:
    def test_window_and_candidates(self, selector):
        assert selector.lags.tolist() == list(range(334, 395))
        # Candidates need a calendar row: the calendar starts at the first holiday.
        assert selector.first_candidate_day == HOLIDAYS[0]
        assert selector.hourly_load_span == (HISTORY_DAYS[0], HISTORY_DAYS[-1])

    def test_bad_window_is_rejected(self):
        with pytest.raises(ValueError, match="window"):
            SimilarDaySelector(
                make_calendar(),
                make_forecast(),
                make_observed(),
                make_hourly_load(),
                center_lag_days=10,
                half_width_days=10,
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
            pd.Timestamp("2024-01-20"),  # window starts 2022-12-22, before the first candidate
            pd.Timestamp("2024-04-30"),  # calendar ends at the last holiday 04-29
            D,  # duplicate
        ]
        assert selector.scorable_days(days).tolist() == [D]


class TestDifferences:
    def test_one_row_per_window_day(self, selector):
        diffs = selector.differences([D])
        assert type(diffs) is DayPairDifferences
        assert len(diffs) == 61
        assert list(diffs.df.columns) == ["target_date", "candidate_date", *SIMILAR_DAY_COMPONENTS]
        lags = (diffs.df["target_date"] - diffs.df["candidate_date"]).dt.days
        assert lags.tolist() == list(range(394, 333, -1))

    def test_calendar_days_from_the_same_weekday_a_year_back(self, selector):
        df = selector.differences([D]).df.set_index("candidate_date")
        assert df.loc[D_MINUS_364, "calendar_days"] == 0.0
        assert df.loc[D - pd.Timedelta(days=394), "calendar_days"] == 30.0
        assert df.loc[D - pd.Timedelta(days=334), "calendar_days"] == 30.0

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
        assert len(diffs) == 60
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

    def test_selection_checks_the_lag(self):
        df = pd.DataFrame(
            {
                "trade_date": [D],
                "reference_date": [D_MINUS_364],
                "distance": [1.0],
                "reference_lag_days": np.array([363], dtype="int64"),
                "n_candidates": np.array([61], dtype="int64"),
                "lag_364_rank": [1.0],
            }
        )
        with pytest.raises(ValueError, match="reference_lag_days must equal"):
            SimilarDaySelection.from_df(df)
