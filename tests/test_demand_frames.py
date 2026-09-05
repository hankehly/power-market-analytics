"""Tests for the demand task's hourly-load, weather-profile and calendar frames."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    HOLIDAY_DEGREE_LEVELS,
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaTemperatureForecast,
    AreaWeatherForecast,
    DayCalendar,
    DayTypeCalendar,
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

    def test_temperature_forecast_view(self):
        view = AreaWeatherForecast.from_df(weather_forecast()).temperature_forecast()
        assert type(view) is AreaTemperatureForecast
        assert list(view.df.columns) == ["trade_date", "hour_ending", "forecast_temperature_c"]
        assert view.df["forecast_temperature_c"].tolist() == [10.0, 11.0]

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
    df = pd.DataFrame(
        {
            "trade_date": [DAY, DAY + pd.Timedelta(days=1)],
            "day_type": np.array([0, 2], dtype="int64"),
            "days_since_holiday": np.array([3, 0], dtype="int64"),
            "days_until_holiday": np.array([1, 0], dtype="int64"),
            "holiday_degree": [0.0, 1.0],
        }
    )
    return df.assign(**overrides)


class TestDayCalendar:
    def test_levels(self):
        assert HOLIDAY_DEGREE_LEVELS == (0.0, 0.3, 0.5, 0.8, 1.0)

    def test_keys_and_day_types_view(self):
        frame = DayCalendar.from_df(calendar())
        assert frame.keys == ["trade_date"]
        view = frame.day_types()
        assert type(view) is DayTypeCalendar
        assert view.df["day_type"].tolist() == [0, 2]

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


class TestDayTypeCalendar:
    def test_code_outside_levels_is_rejected(self):
        with pytest.raises(ValueError, match="day_type outside 0..2"):
            DayTypeCalendar.from_df(
                pd.DataFrame({"trade_date": [DAY], "day_type": np.array([3], dtype="int64")})
            )
