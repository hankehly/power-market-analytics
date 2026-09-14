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
            "available_at": pd.to_datetime([DAY + pd.Timedelta(days=1)] * 2),
        }
    )
    return df.assign(**overrides)


class TestAreaHourlyLoad:
    def test_keys_and_columns(self):
        frame = AreaHourlyLoad.from_df(hourly())
        assert frame.keys == ["load_date", "hour_ending"]
        assert list(frame.df.columns) == ["load_date", "hour_ending", "demand_kwh", "available_at"]

    def test_availability_is_required(self):
        with pytest.raises(ValueError, match="'available_at' has 1 null"):
            AreaHourlyLoad.from_df(hourly(available_at=pd.to_datetime([DAY, pd.NaT])))

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
            "available_at": pd.to_datetime([DAY - pd.Timedelta(hours=23)] * 2),
        }
    )
    return df.assign(**overrides)


class TestAreaWeatherForecast:
    def test_nullable_measures_and_keys(self):
        frame = AreaWeatherForecast.from_df(weather_forecast())
        assert frame.keys == ["trade_date", "hour_ending"]
        assert frame.df["forecast_relative_humidity_pct"].isna().tolist() == [False, True]

    def test_availability_is_required(self):
        with pytest.raises(ValueError, match="'available_at' has 1 null"):
            AreaWeatherForecast.from_df(
                weather_forecast(available_at=pd.to_datetime([DAY, pd.NaT]))
            )

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
            "is_holiday": [False, True],
            "holiday_name_ja": [None, "春分の日"],
            "days_since_holiday": np.array([3, 0], dtype="int64"),
            "days_until_holiday": np.array([1, 0], dtype="int64"),
            "holiday_degree": [0.0, 1.0],
        }
    )
    return df.assign(**overrides)


class TestDayCalendar:
    def test_levels(self):
        assert HOLIDAY_DEGREE_LEVELS == (0.0, 0.3, 0.5, 0.8, 1.0)

    def test_keys_and_columns_in_schema_order(self):
        frame = DayCalendar.from_df(calendar())
        assert frame.keys == ["trade_date"]
        assert list(frame.df.columns) == [
            "trade_date",
            "is_holiday",
            "holiday_name_ja",
            "days_since_holiday",
            "days_until_holiday",
            "holiday_degree",
        ]
        assert frame.df["days_since_holiday"].tolist() == [3, 0]
        assert "holiday_name_ja" not in DayCalendar.non_null_cols

    def test_a_null_attribute_is_rejected(self):
        with pytest.raises(ValueError, match="'holiday_degree' has 1 null"):
            DayCalendar.from_df(calendar(holiday_degree=[0.0, np.nan]))

    def test_negative_holiday_distance_is_rejected(self):
        with pytest.raises(ValueError, match="days_since_holiday must be >= 0"):
            DayCalendar.from_df(calendar(days_since_holiday=np.array([-1, 0], dtype="int64")))
        with pytest.raises(ValueError, match="days_until_holiday must be >= 0"):
            DayCalendar.from_df(calendar(days_until_holiday=np.array([1, -2], dtype="int64")))

    def test_holiday_degree_outside_levels_is_rejected(self):
        with pytest.raises(ValueError, match=r"holiday_degree outside \(0.0, 0.3, 0.5, 0.8, 1.0\)"):
            DayCalendar.from_df(calendar(holiday_degree=[0.0, 0.9]))

    def test_a_name_repeated_within_a_calendar_year_is_rejected(self):
        df = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-01-08", "2024-03-20"]),
                "is_holiday": [True, True],
                "holiday_name_ja": ["成人の日", "成人の日"],
                "days_since_holiday": np.array([0, 0], dtype="int64"),
                "days_until_holiday": np.array([0, 0], dtype="int64"),
                "holiday_degree": [1.0, 1.0],
            }
        )
        with pytest.raises(
            ValueError,
            match="holiday_name_ja repeats within a calendar year: 2024 成人の日$",
        ):
            DayCalendar.from_df(df)

    def test_the_same_name_in_two_years_is_accepted(self):
        df = calendar(
            trade_date=pd.to_datetime(["2023-03-21", "2024-03-20"]),
            is_holiday=[True, True],
            holiday_name_ja=["春分の日", "春分の日"],
            days_since_holiday=np.array([0, 0], dtype="int64"),
            days_until_holiday=np.array([0, 0], dtype="int64"),
            holiday_degree=[1.0, 1.0],
        )
        assert len(DayCalendar.from_df(df)) == 2

    def test_a_holiday_without_a_name_is_rejected(self):
        with pytest.raises(ValueError, match="a name exactly on holidays"):
            DayCalendar.from_df(calendar(holiday_name_ja=[None, None]))

    def test_a_named_ordinary_day_is_rejected(self):
        with pytest.raises(ValueError, match="a name exactly on holidays"):
            DayCalendar.from_df(calendar(holiday_name_ja=["平日", "春分の日"]))
