"""Tests for the demand task's temperature features (recency-weighted observed, forecast)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.tasks.demand.features import (
    FORECAST_TEMPERATURE_FEATURE,
    POPW_FORECAST_TEMPERATURE_FEATURE,
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    hour_ending_of,
    join_forecast_temperature,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import AreaTemperature, AreaTemperatureForecast

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
