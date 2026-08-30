"""Tests for the demand task's frames (the generic bases are tested elsewhere)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    DAY_TYPE_LEVELS,
    PRIOR_YEAR_REFERENCE_RULES,
    AreaDemand,
    AreaHourlyLoad,
    AreaTemperature,
    AreaTemperatureForecast,
    DayTypeCalendar,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
    PriorYearCalendar,
)

D1 = pd.Timestamp("2024-04-01").as_unit("ns")


class TestSeriesFrames:
    def test_area_demand_contract(self):
        assert AreaDemand.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "demand_kwh": "float64",
        }
        assert AreaDemand.non_null_cols == ["demand_kwh"]

    def test_forecast_result_and_records_share_the_forecast_column(self):
        assert DemandForecast.forecast_col == "forecast_demand_kwh"
        assert DemandBacktestResult.forecast_col == "forecast_demand_kwh"
        assert DemandBacktestResult.actual_col == "actual_demand_kwh"
        assert DemandForecastRecords.forecast_col == "forecast_demand_kwh"
        assert "forecast_demand_kwh" in DemandForecastRecords.schema

    def test_area_demand_accepts_a_day(self):
        df = pd.DataFrame(
            {
                "trade_date": [D1] * 48,
                "time_code": np.arange(1, 49, dtype="int64"),
                "demand_kwh": np.full(48, 15_000_000.0),
            }
        )
        assert len(AreaDemand.from_df(df)) == 48


def temperature_df(hours: list[int], temps: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "obs_date": [D1] * len(hours),
            "hour_ending": np.array(hours, dtype="int64"),
            "temperature_c": np.array(temps, dtype="float64"),
        }
    )


class TestAreaTemperature:
    def test_grain_is_observation_day_and_hour_ending(self):
        assert AreaTemperature.keys == ["obs_date", "hour_ending"]
        out = AreaTemperature.from_df(temperature_df([1, 24], [5.0, 7.5]))
        assert list(out.df.columns) == ["obs_date", "hour_ending", "temperature_c"]

    def test_missing_temperature_is_allowed(self):
        out = AreaTemperature.from_df(temperature_df([1, 2], [5.0, np.nan]))
        assert out.df["temperature_c"].isna().tolist() == [False, True]

    @pytest.mark.parametrize("hour", [0, 25])
    def test_hour_ending_outside_1_24_rejected(self, hour):
        with pytest.raises(
            ValueError, match=rf"hour_ending outside 1\.\.24: \[[^\]]*\b{hour}\b[^\]]*\]$"
        ):
            AreaTemperature.from_df(temperature_df([hour], [5.0]))

    def test_duplicate_day_hour_rejected(self):
        with pytest.raises(ValueError, match="grain .* not unique"):
            AreaTemperature.from_df(temperature_df([1, 1], [5.0, 6.0]))


def forecast_df(hours: list[int], temps: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [D1] * len(hours),
            "hour_ending": np.array(hours, dtype="int64"),
            "forecast_temperature_c": np.array(temps, dtype="float64"),
        }
    )


class TestAreaTemperatureForecast:
    def test_grain_is_delivery_day_and_hour_ending(self):
        assert AreaTemperatureForecast.keys == ["trade_date", "hour_ending"]
        out = AreaTemperatureForecast.from_df(forecast_df([1, 24], [5.0, 7.5]))
        assert list(out.df.columns) == ["trade_date", "hour_ending", "forecast_temperature_c"]

    def test_missing_forecast_is_allowed(self):
        out = AreaTemperatureForecast.from_df(forecast_df([1, 2], [5.0, np.nan]))
        assert out.df["forecast_temperature_c"].isna().tolist() == [False, True]

    @pytest.mark.parametrize("hour", [0, 25])
    def test_hour_ending_outside_1_24_rejected(self, hour):
        with pytest.raises(
            ValueError, match=rf"hour_ending outside 1\.\.24: \[[^\]]*\b{hour}\b[^\]]*\]$"
        ):
            AreaTemperatureForecast.from_df(forecast_df([hour], [5.0]))

    def test_duplicate_day_hour_rejected(self):
        with pytest.raises(ValueError, match="grain .* not unique"):
            AreaTemperatureForecast.from_df(forecast_df([1, 1], [5.0, 6.0]))


def calendar_df(days: list[pd.Timestamp], codes: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": days, "day_type": np.array(codes, dtype="int64")})


class TestDayTypeCalendar:
    def test_levels_in_code_order(self):
        assert DAY_TYPE_LEVELS == ("Weekday", "Weekend", "Holiday")

    def test_grain_is_the_delivery_day(self):
        assert DayTypeCalendar.schema == {"trade_date": "datetime64[ns]", "day_type": "int64"}
        assert DayTypeCalendar.keys == ["trade_date"]
        assert DayTypeCalendar.non_null_cols == ["day_type"]
        out = DayTypeCalendar.from_df(calendar_df([D1, D1 + pd.Timedelta(days=1)], [0, 2]))
        assert list(out.df.columns) == ["trade_date", "day_type"]
        assert len(out) == 2

    @pytest.mark.parametrize("code", [-1, 3])
    def test_code_outside_the_levels_rejected(self, code):
        with pytest.raises(
            ValueError, match=rf"DayTypeCalendar: day_type outside 0\.\.2: \[{code}\]$"
        ):
            DayTypeCalendar.from_df(calendar_df([D1], [code]))

    def test_float_codes_rejected(self):
        df = calendar_df([D1], [1]).astype({"day_type": "float64"})
        with pytest.raises(ValueError, match="dtype mismatch"):
            DayTypeCalendar.from_df(df)

    def test_duplicate_day_rejected(self):
        with pytest.raises(ValueError, match="grain .* not unique"):
            DayTypeCalendar.from_df(calendar_df([D1, D1], [0, 1]))


def hourly_load_df(hours: list[int], loads: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "load_date": [D1] * len(hours),
            "hour_ending": np.array(hours, dtype="int64"),
            "demand_kwh": np.array(loads, dtype="float64"),
        }
    )


class TestAreaHourlyLoad:
    def test_grain_is_load_day_and_hour_ending(self):
        assert AreaHourlyLoad.schema == {
            "load_date": "datetime64[ns]",
            "hour_ending": "int64",
            "demand_kwh": "float64",
        }
        assert AreaHourlyLoad.keys == ["load_date", "hour_ending"]
        assert AreaHourlyLoad.non_null_cols == ["demand_kwh"]
        out = AreaHourlyLoad.from_df(hourly_load_df([1, 24], [30_000_000.0, 28_000_000.0]))
        assert list(out.df.columns) == ["load_date", "hour_ending", "demand_kwh"]
        assert len(out) == 2

    @pytest.mark.parametrize("hour", [0, 25])
    def test_hour_ending_outside_1_24_rejected(self, hour):
        with pytest.raises(
            ValueError,
            match=rf"AreaHourlyLoad: hour_ending outside 1\.\.24: \[[^\]]*\b{hour}\b[^\]]*\]$",
        ):
            AreaHourlyLoad.from_df(hourly_load_df([hour], [30_000_000.0]))

    def test_null_load_rejected(self):
        with pytest.raises(ValueError, match="column 'demand_kwh' has 1 null values"):
            AreaHourlyLoad.from_df(hourly_load_df([1], [np.nan]))

    def test_non_positive_load_rejected(self):
        # The hourly fact never publishes 0 (TEPCO's not-yet-final sentinel); a
        # zero here would be a load error, not a reading.
        with pytest.raises(
            ValueError, match=r"AreaHourlyLoad: demand_kwh must be positive; 1 row\(s\) are not"
        ):
            AreaHourlyLoad.from_df(hourly_load_df([1, 2], [0.0, 1.0]))

    def test_duplicate_day_hour_rejected(self):
        with pytest.raises(ValueError, match="grain .* not unique"):
            AreaHourlyLoad.from_df(hourly_load_df([1, 1], [1.0, 2.0]))


def prior_year_df(
    days: list[pd.Timestamp], references: list[pd.Timestamp], rules: list[str]
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": days,
            "prior_year_reference_date": references,
            "prior_year_reference_rule": rules,
        }
    )


class TestPriorYearCalendar:
    def test_rules_are_dim_dates(self):
        assert PRIOR_YEAR_REFERENCE_RULES == (
            "same_weekday",
            "same_weekday_shifted",
            "same_holiday",
            "nearest_non_working_day",
        )

    def test_grain_is_the_delivery_day(self):
        assert PriorYearCalendar.schema == {
            "trade_date": "datetime64[ns]",
            "prior_year_reference_date": "datetime64[ns]",
            "prior_year_reference_rule": "object",
        }
        assert PriorYearCalendar.keys == ["trade_date"]
        assert PriorYearCalendar.non_null_cols == [
            "prior_year_reference_date",
            "prior_year_reference_rule",
        ]
        d2 = D1 + pd.Timedelta(days=1)
        out = PriorYearCalendar.from_df(
            prior_year_df(
                [D1, d2],
                [D1 - pd.Timedelta(days=364), d2 - pd.Timedelta(days=357)],
                ["same_weekday", "same_weekday_shifted"],
            )
        )
        assert list(out.df.columns) == [
            "trade_date",
            "prior_year_reference_date",
            "prior_year_reference_rule",
        ]
        assert len(out) == 2

    def test_unknown_rule_rejected(self):
        with pytest.raises(
            ValueError,
            match=r"PriorYearCalendar: unknown prior_year_reference_rule: \['bogus'\]$",
        ):
            PriorYearCalendar.from_df(
                prior_year_df([D1], [D1 - pd.Timedelta(days=364)], ["bogus"])
            )

    def test_reference_not_before_the_day_rejected(self):
        with pytest.raises(
            ValueError,
            match=r"PriorYearCalendar: prior_year_reference_date is not before trade_date on "
            r"1 day\(s\) \(e\.g\. 2024-04-01\)",
        ):
            PriorYearCalendar.from_df(prior_year_df([D1], [D1], ["same_weekday"]))

    def test_null_reference_rejected(self):
        with pytest.raises(ValueError, match="column 'prior_year_reference_date' has 1 null"):
            PriorYearCalendar.from_df(prior_year_df([D1], [pd.NaT], ["same_weekday"]))

    def test_duplicate_day_rejected(self):
        ref = D1 - pd.Timedelta(days=364)
        with pytest.raises(ValueError, match="grain .* not unique"):
            PriorYearCalendar.from_df(
                prior_year_df([D1, D1], [ref, ref], ["same_weekday", "same_weekday"])
            )
