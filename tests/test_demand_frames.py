"""Tests for the demand task's frames (the generic bases are tested elsewhere)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaTemperature,
    AreaTemperatureForecast,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
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
