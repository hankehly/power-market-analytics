"""Tests for the demand task's hourly-load, weather-profile and calendar frames."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    HOLIDAY_DEGREE_LEVELS,
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)

DAY = pd.Timestamp("2024-04-10")


def hourly(**overrides) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "load_date": [DAY, DAY],
            "hour_ending": np.array([1, 2], dtype="int64"),
            "demand_kwh": [30_000_000.0, 29_000_000.0],
        }
    )
    return df.assign(**overrides)


class TestAreaHourlyLoad:
    def test_keys_and_columns(self):
        frame = AreaHourlyLoad.from_df(hourly())
        assert frame.keys == ["load_date", "hour_ending"]
        assert list(frame.df.columns) == ["load_date", "hour_ending", "demand_kwh"]

    def test_hour_outside_1_24_is_rejected(self):
        with pytest.raises(ValueError, match="hour_ending outside 1..24"):
            AreaHourlyLoad.from_df(hourly(hour_ending=np.array([0, 25], dtype="int64")))

    def test_non_positive_load_is_rejected(self):
        with pytest.raises(ValueError, match=r"demand_kwh must be positive; 1 row\(s\)"):
            AreaHourlyLoad.from_df(hourly(demand_kwh=[30_000_000.0, 0.0]))


def weather_forecast(**overrides) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "trade_date": [DAY, DAY],
            "hour_ending": np.array([1, 2], dtype="int64"),
            "forecast_temperature_c": [10.0, 11.0],
            "forecast_relative_humidity_pct": [60.0, np.nan],
            "forecast_precipitation_mm": [0.0, 0.5],
        }
    )
    return df.assign(**overrides)


class TestAreaWeatherForecast:
    def test_nullable_measures_and_keys(self):
        frame = AreaWeatherForecast.from_df(weather_forecast())
        assert frame.keys == ["trade_date", "hour_ending"]
        assert frame.df["forecast_relative_humidity_pct"].isna().tolist() == [False, True]

    def test_hour_outside_1_24_is_rejected(self):
        with pytest.raises(ValueError, match="hour_ending outside 1..24"):
            AreaWeatherForecast.from_df(
                weather_forecast(hour_ending=np.array([1, 25], dtype="int64"))
            )


class TestAreaObservedWeather:
    def test_keys_and_nullable_measures(self):
        frame = AreaObservedWeather.from_df(
            pd.DataFrame(
                {
                    "obs_date": [DAY],
                    "hour_ending": np.array([24], dtype="int64"),
                    "temperature_c": [np.nan],
                    "humidity_pct": [70.0],
                    "precipitation_mm": [0.0],
                }
            )
        )
        assert frame.keys == ["obs_date", "hour_ending"]
        assert np.isnan(frame.df["temperature_c"].iloc[0])

    def test_hour_outside_1_24_is_rejected(self):
        with pytest.raises(ValueError, match="hour_ending outside 1..24"):
            AreaObservedWeather.from_df(
                pd.DataFrame(
                    {
                        "obs_date": [DAY],
                        "hour_ending": np.array([0], dtype="int64"),
                        "temperature_c": [1.0],
                        "humidity_pct": [1.0],
                        "precipitation_mm": [0.0],
                    }
                )
            )


def calendar(**overrides) -> pd.DataFrame:
    # 2024-04-10 (a working Wednesday) and 04-11 (a holiday in this frame).
    df = pd.DataFrame(
        {
            "trade_date": [DAY, DAY + pd.Timedelta(days=1)],
            "day_type": np.array([0, 2], dtype="int64"),
            "days_since_holiday": np.array([3, 0], dtype="int64"),
            "days_until_holiday": np.array([1, 0], dtype="int64"),
            "holiday_degree": [0.0, 1.0],
            "half": np.array([1, 1], dtype="int64"),
            "quarter": np.array([2, 2], dtype="int64"),
            "day_of_month": np.array([10, 11], dtype="int64"),
            "day_of_quarter": np.array([10, 11], dtype="int64"),
            "day_of_year": np.array([101, 102], dtype="int64"),
            "is_business_day": [True, False],
            "fiscal_quarter": np.array([1, 1], dtype="int64"),
        }
    )
    return df.assign(**overrides)


class TestDayCalendar:
    def test_levels(self):
        assert HOLIDAY_DEGREE_LEVELS == (0.0, 0.3, 0.5, 0.8, 1.0)

    def test_keys(self):
        frame = DayCalendar.from_df(calendar())
        assert frame.keys == ["trade_date"]
        assert frame.df["day_type"].tolist() == [0, 2]

    def test_columns_in_schema_order(self):
        frame = DayCalendar.from_df(calendar())
        assert list(frame.df.columns) == [
            "trade_date",
            "day_type",
            "days_since_holiday",
            "days_until_holiday",
            "holiday_degree",
            "half",
            "quarter",
            "day_of_month",
            "day_of_quarter",
            "day_of_year",
            "is_business_day",
            "fiscal_quarter",
        ]
        assert frame.df["is_business_day"].tolist() == [True, False]
        assert frame.df["day_of_year"].dtype == "int64"

    @pytest.mark.parametrize(
        ("column", "bad", "message"),
        [
            ("half", 3, "half outside 1..2"),
            ("quarter", 0, "quarter outside 1..4"),
            ("day_of_month", 32, "day_of_month outside 1..31"),
            ("day_of_quarter", 93, "day_of_quarter outside 1..92"),
            ("day_of_year", 0, "day_of_year outside 1..366"),
            ("fiscal_quarter", 5, "fiscal_quarter outside 1..4"),
        ],
    )
    def test_calendar_count_outside_its_range_is_rejected(self, column, bad, message):
        with pytest.raises(ValueError, match=message):
            DayCalendar.from_df(calendar(**{column: np.array([1, bad], dtype="int64")}))

    def test_day_type_outside_levels_is_rejected(self):
        with pytest.raises(ValueError, match="day_type outside 0..2"):
            DayCalendar.from_df(calendar(day_type=np.array([0, 3], dtype="int64")))

    def test_negative_holiday_distance_is_rejected(self):
        with pytest.raises(ValueError, match="days_since_holiday must be >= 0"):
            DayCalendar.from_df(calendar(days_since_holiday=np.array([-1, 0], dtype="int64")))
        with pytest.raises(ValueError, match="days_until_holiday must be >= 0"):
            DayCalendar.from_df(calendar(days_until_holiday=np.array([1, -2], dtype="int64")))

    def test_holiday_degree_outside_levels_is_rejected(self):
        with pytest.raises(ValueError, match=r"holiday_degree outside \(0.0, 0.3, 0.5, 0.8, 1.0\)"):
            DayCalendar.from_df(calendar(holiday_degree=[0.0, 0.9]))
