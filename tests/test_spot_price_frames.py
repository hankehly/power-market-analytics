"""Tests for the spot price task's domain frames."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.frames import N_PERIODS
from power_market_analytics.tasks.spot_price.frames import (
    OcctoDemandForecast,
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPriceForecastRecords,
    SpotPrices,
)

D1 = pd.Timestamp("2024-01-01")
D2 = pd.Timestamp("2024-01-02")


def test_n_periods_is_48_half_hours():
    assert N_PERIODS == 48


# --------------------------------------------------------------------------- SpotPrices
def prices_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [D1, D1, D2],
            "time_code": np.array([1, 2, 1], dtype="int64"),
            "price_jpy_kwh": [10.0, 11.0, 12.0],
        }
    )


class TestSpotPrices:
    def test_accepts_valid_history_in_schema_order(self):
        frame = SpotPrices.from_df(prices_df()[["price_jpy_kwh", "time_code", "trade_date"]])
        assert list(frame.df.columns) == ["trade_date", "time_code", "price_jpy_kwh"]
        assert frame.grain == ("trade_date", "time_code")

    def test_duplicate_date_time_code_rejected(self):
        df = pd.concat([prices_df(), prices_df().iloc[[0]]], ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique \\(1 duplicate rows\\)"):
            SpotPrices.from_df(df)

    def test_null_price_rejected(self):
        df = prices_df()
        df.loc[1, "price_jpy_kwh"] = np.nan
        with pytest.raises(ValueError, match="column 'price_jpy_kwh' has 1 null values"):
            SpotPrices.from_df(df)

    def test_integer_price_dtype_rejected(self):
        df = prices_df().astype({"price_jpy_kwh": "int64"})
        with pytest.raises(ValueError, match="dtype mismatch"):
            SpotPrices.from_df(df)


# --------------------------------------------------------------------------- OcctoDemandForecast
def occto_df(hours: list[int]) -> pd.DataFrame:
    n = len(hours)
    return pd.DataFrame(
        {
            "trade_date": pd.date_range(D1, periods=n, freq="D"),
            "max_demand_hour_ending": np.array(hours, dtype="int64"),
            "max_demand_mw": np.array([40_000] * n, dtype="int64"),
            "max_supply_capacity_mw": np.array([46_000] * n, dtype="int64"),
        }
    )


class TestOcctoDemandForecast:
    def test_hour_ending_bounds_1_and_24_accepted(self):
        frame = OcctoDemandForecast.from_df(occto_df([1, 24]))
        assert frame.grain == ("trade_date",)
        assert len(frame) == 2

    @pytest.mark.parametrize("hour", [0, 25])
    def test_hour_ending_outside_1_24_rejected(self, hour):
        # The offending value is listed (numpy scalar repr aside); the valid
        # hour 12 in the same frame is not.
        with pytest.raises(
            ValueError,
            match=rf"max_demand_hour_ending outside 1\.\.24: \[[^\]]*\b{hour}\b[^\]]*\]$",
        ) as excinfo:
            OcctoDemandForecast.from_df(occto_df([hour, 12]))
        assert "12" not in str(excinfo.value).split(": [", 1)[1]

    def test_all_offending_hours_listed_sorted_unique(self):
        with pytest.raises(ValueError, match=r"outside 1\.\.24: \[") as excinfo:
            OcctoDemandForecast.from_df(occto_df([25, 0, 25]))
        listed = str(excinfo.value).split(": [", 1)[1]
        # Sorted and de-duplicated: 0 before 25, and 25 only once. The values
        # may be rendered as numpy scalars (``np.int64(0)``), hence the loose parse.
        assert [v for v in re.findall(r"\d+", listed) if v != "64"] == ["0", "25"]

    def test_duplicate_trade_date_rejected(self):
        df = occto_df([12, 13])
        df.loc[1, "trade_date"] = D1
        with pytest.raises(ValueError, match="grain \\['trade_date'\\] not unique"):
            OcctoDemandForecast.from_df(df)

    def test_missing_measure_column_rejected(self):
        with pytest.raises(
            ValueError, match="missing required columns \\['max_supply_capacity_mw'\\]"
        ):
            OcctoDemandForecast.from_df(occto_df([12]).drop(columns=["max_supply_capacity_mw"]))


# --------------------------------------------------------------------------- SpotPriceForecast
def forecast_df(day: pd.Timestamp, time_codes: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [day] * len(time_codes),
            "time_code": np.array(time_codes, dtype="int64"),
            "forecast_price_jpy_kwh": [float(tc) for tc in time_codes],
        }
    )


class TestSpotPriceForecast:
    def test_exactly_time_codes_1_to_48_accepted(self):
        frame = SpotPriceForecast.from_df(forecast_df(D2, list(range(1, 49))))
        assert len(frame) == 48

    def test_two_target_days_rejected(self):
        df = pd.concat(
            [forecast_df(D1, list(range(1, 25))), forecast_df(D2, list(range(25, 49)))],
            ignore_index=True,
        )
        with pytest.raises(ValueError, match="expected a single target day"):
            SpotPriceForecast.from_df(df)

    def test_47_rows_rejected(self):
        with pytest.raises(ValueError, match="expected exactly time codes 1..48, got 47 rows"):
            SpotPriceForecast.from_df(forecast_df(D2, list(range(1, 48))))

    def test_wrong_time_codes_with_48_rows_rejected(self):
        with pytest.raises(ValueError, match="expected exactly time codes 1..48, got 48 rows"):
            SpotPriceForecast.from_df(forecast_df(D2, list(range(0, 48))))

    def test_null_forecast_rejected(self):
        df = forecast_df(D2, list(range(1, 49)))
        df.loc[5, "forecast_price_jpy_kwh"] = np.nan
        with pytest.raises(ValueError, match="column 'forecast_price_jpy_kwh' has 1 null values"):
            SpotPriceForecast.from_df(df)


# --------------------------------------------------------------------------- SpotPriceBacktestResult
def result_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [D1, D1, D2],
            "time_code": np.array([1, 2, 1], dtype="int64"),
            "actual_price_jpy_kwh": [10.0, 11.0, 12.0],
            "forecast_price_jpy_kwh": [9.0, 11.5, 12.0],
        }
    )


class TestSpotPriceBacktestResult:
    def test_accepts_joined_rows(self):
        frame = SpotPriceBacktestResult.from_df(result_df())
        assert frame.grain == ("trade_date", "time_code")
        assert list(frame.df.columns) == [
            "trade_date",
            "time_code",
            "actual_price_jpy_kwh",
            "forecast_price_jpy_kwh",
        ]

    @pytest.mark.parametrize("col", ["actual_price_jpy_kwh", "forecast_price_jpy_kwh"])
    def test_null_measure_rejected(self, col):
        df = result_df()
        df.loc[2, col] = np.nan
        with pytest.raises(ValueError, match=f"column '{col}' has 1 null values"):
            SpotPriceBacktestResult.from_df(df)

    def test_duplicate_grain_rejected(self):
        df = pd.concat([result_df(), result_df().iloc[[2]]], ignore_index=True)
        with pytest.raises(ValueError, match="not unique \\(1 duplicate rows\\)"):
            SpotPriceBacktestResult.from_df(df)


# --------------------------------------------------------------------------- SpotPriceForecastRecords
TS = pd.Timestamp("2024-01-01 09:55:00")


def records_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": ["run-a", "run-a", "run-b"],
            "strategy": ["naive", "naive", "naive"],
            "area_code": ["tokyo", "kansai", "tokyo"],
            "forecast_issued_ts": [TS, TS, TS],
            "trade_date": [D2, D2, D2],
            "time_code": np.array([1, 1, 1], dtype="int64"),
            "forecast_price_jpy_kwh": [10.0, 11.0, 12.0],
            "published_at": [TS, TS, TS],
        }
    )


class TestSpotPriceForecastRecords:
    def test_grain_is_run_area_date_time_code(self):
        # Same (area, date, time_code) under two runs, and two areas under one
        # run, are distinct rows.
        frame = SpotPriceForecastRecords.from_df(records_df())
        assert frame.grain == ("run_id", "area_code", "trade_date", "time_code")
        assert len(frame) == 3

    def test_same_run_area_date_time_code_twice_rejected(self):
        df = pd.concat([records_df(), records_df().iloc[[0]]], ignore_index=True)
        with pytest.raises(ValueError, match="not unique \\(1 duplicate rows\\)"):
            SpotPriceForecastRecords.from_df(df)

    @pytest.mark.parametrize("col", ["run_id", "area_code", "strategy"])
    def test_null_string_column_rejected(self, col):
        df = records_df()
        df.loc[0, col] = None
        with pytest.raises(ValueError, match=f"column '{col}' has 1 null values"):
            SpotPriceForecastRecords.from_df(df)

    @pytest.mark.parametrize("col", ["forecast_issued_ts", "trade_date", "published_at"])
    def test_null_timestamp_column_rejected(self, col):
        df = records_df()
        df.loc[1, col] = pd.NaT
        with pytest.raises(ValueError, match=f"column '{col}' has 1 null values"):
            SpotPriceForecastRecords.from_df(df)

    def test_null_forecast_rejected(self):
        df = records_df()
        df.loc[2, "forecast_price_jpy_kwh"] = np.nan
        with pytest.raises(ValueError, match="column 'forecast_price_jpy_kwh' has 1 null values"):
            SpotPriceForecastRecords.from_df(df)
