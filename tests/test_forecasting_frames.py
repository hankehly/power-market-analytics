"""Tests for the generic day-ahead frame bases and MetricByYearTimeCode."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.frames import (
    GRAIN_COLS,
    N_PERIODS,
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
    MetricByYearTimeCode,
)

D1 = pd.Timestamp("2024-01-01").as_unit("ns")
D2 = pd.Timestamp("2024-01-02").as_unit("ns")


class Series(HalfHourlySeries):
    value_col = "load_mw"


class Forecast(DayAheadForecast):
    forecast_col = "forecast_load_mw"


class Result(BacktestResult):
    actual_col = "actual_load_mw"
    forecast_col = "forecast_load_mw"


class Records(ForecastRecords):
    forecast_col = "forecast_load_mw"


def full_day(day: pd.Timestamp, col: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [day] * N_PERIODS,
            "time_code": np.arange(1, N_PERIODS + 1, dtype="int64"),
            col: np.linspace(1.0, 2.0, N_PERIODS),
        }
    )


class TestGrainConstants:
    def test_grain_is_trade_date_and_time_code(self):
        assert GRAIN_COLS == ["trade_date", "time_code"]
        assert N_PERIODS == 48


class TestHalfHourlySeries:
    def test_schema_keys_and_non_null_come_from_value_col(self):
        assert Series.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "load_mw": "float64",
        }
        assert Series.keys == ["trade_date", "time_code"]
        assert Series.non_null_cols == ["load_mw"]

    def test_accepts_a_valid_history_and_keeps_schema_order(self):
        df = full_day(D1, "load_mw")[["load_mw", "time_code", "trade_date"]]
        out = Series.from_df(df)
        assert isinstance(out, Series)
        assert list(out.df.columns) == ["trade_date", "time_code", "load_mw"]

    def test_null_value_rejected(self):
        df = full_day(D1, "load_mw")
        df.loc[0, "load_mw"] = np.nan
        with pytest.raises(ValueError, match="Series: column 'load_mw' has 1 null values"):
            Series.from_df(df)

    def test_duplicate_grain_rejected(self):
        df = pd.concat([full_day(D1, "load_mw")] * 2, ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique"):
            Series.from_df(df)

    def test_subclass_without_value_col_is_rejected_at_definition(self):
        with pytest.raises(TypeError, match="Broken must set the class attribute 'value_col'"):

            class Broken(HalfHourlySeries):
                pass

    def test_sub_subclass_inherits_the_value_col(self):
        class Narrower(Series):
            pass

        assert Narrower.schema == Series.schema
        assert Narrower.non_null_cols == ["load_mw"]


class TestDayAheadForecast:
    def test_exactly_time_codes_1_to_48_for_one_day_accepted(self):
        out = Forecast.from_df(full_day(D1, "forecast_load_mw"))
        assert Forecast.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "forecast_load_mw": "float64",
        }
        assert len(out) == N_PERIODS

    def test_two_target_days_rejected(self):
        df = pd.concat(
            [full_day(D1, "forecast_load_mw"), full_day(D2, "forecast_load_mw")],
            ignore_index=True,
        )
        with pytest.raises(ValueError, match="expected a single target day"):
            Forecast.from_df(df)

    def test_47_rows_rejected(self):
        with pytest.raises(ValueError, match="expected exactly time codes 1..48, got 47 rows"):
            Forecast.from_df(full_day(D1, "forecast_load_mw").iloc[:-1])

    def test_wrong_time_codes_with_48_rows_rejected(self):
        df = full_day(D1, "forecast_load_mw")
        df["time_code"] = np.arange(2, N_PERIODS + 2, dtype="int64")
        with pytest.raises(ValueError, match="expected exactly time codes 1..48"):
            Forecast.from_df(df)

    def test_subclass_without_forecast_col_is_rejected_at_definition(self):
        with pytest.raises(TypeError, match="must set the class attribute 'forecast_col'"):

            class Broken(DayAheadForecast):
                pass


class TestBacktestResult:
    def test_schema_from_actual_and_forecast_cols(self):
        assert Result.schema == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "actual_load_mw": "float64",
            "forecast_load_mw": "float64",
        }
        assert Result.keys == ["trade_date", "time_code"]
        assert Result.non_null_cols == ["actual_load_mw", "forecast_load_mw"]

    def test_accepts_joined_rows(self):
        df = full_day(D1, "actual_load_mw").assign(forecast_load_mw=1.5)
        assert len(Result.from_df(df)) == N_PERIODS

    @pytest.mark.parametrize("col", ["actual_load_mw", "forecast_load_mw"])
    def test_null_measure_rejected(self, col):
        df = full_day(D1, "actual_load_mw").assign(forecast_load_mw=1.5)
        df.loc[3, col] = np.nan
        with pytest.raises(ValueError, match=f"column '{col}' has 1 null values"):
            Result.from_df(df)

    def test_subclass_missing_either_col_is_rejected(self):
        with pytest.raises(TypeError, match="must set the class attribute 'actual_col'"):

            class NoActual(BacktestResult):
                forecast_col = "f"

        with pytest.raises(TypeError, match="must set the class attribute 'forecast_col'"):

            class NoForecast(BacktestResult):
                actual_col = "a"


def records_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": ["r", "r"],
            "strategy": ["s", "s"],
            "area_code": ["tokyo", "tokyo"],
            "forecast_issued_ts": [pd.Timestamp("2023-12-31 09:30")] * 2,
            "trade_date": [D1, D1],
            "time_code": np.array([1, 2], dtype="int64"),
            "forecast_load_mw": [1.0, 2.0],
            "published_at": [pd.Timestamp("2024-01-05 12:00")] * 2,
        }
    )


class TestForecastRecords:
    def test_grain_and_schema(self):
        assert Records.keys == ["run_id", "area_code", "trade_date", "time_code"]
        assert list(Records.schema) == [
            "run_id",
            "strategy",
            "area_code",
            "forecast_issued_ts",
            "trade_date",
            "time_code",
            "forecast_load_mw",
            "published_at",
        ]
        assert Records.non_null_cols == [
            "strategy",
            "forecast_issued_ts",
            "forecast_load_mw",
            "published_at",
        ]
        assert len(Records.from_df(records_df())) == 2

    def test_same_run_area_date_time_code_twice_rejected(self):
        df = pd.concat([records_df(), records_df().iloc[[0]]], ignore_index=True)
        with pytest.raises(ValueError, match="grain .* not unique"):
            Records.from_df(df)

    def test_null_forecast_rejected(self):
        df = records_df()
        df.loc[0, "forecast_load_mw"] = np.nan
        with pytest.raises(ValueError, match="'forecast_load_mw' has 1 null values"):
            Records.from_df(df)


class TestMetricByYearTimeCode:
    def make(self, year, time_code, value):
        return pd.DataFrame(
            {
                "year": np.array(year, dtype="int64"),
                "time_code": np.array(time_code, dtype="int64"),
                "value": np.array(value, dtype="float64"),
            }
        )

    def test_time_code_bounds_1_and_48_accepted(self):
        out = MetricByYearTimeCode.from_df(self.make([2024, 2024], [1, 48], [1.0, 2.0]))
        assert len(out) == 2

    @pytest.mark.parametrize("bad", [0, 49])
    def test_time_code_outside_1_48_rejected(self, bad):
        with pytest.raises(
            ValueError, match=rf"time_code outside 1\.\.48: \[[^\]]*\b{bad}\b[^\]]*\]$"
        ):
            MetricByYearTimeCode.from_df(self.make([2024], [bad], [1.0]))

    def test_nan_value_allowed(self):
        out = MetricByYearTimeCode.from_df(self.make([2024], [1], [np.nan]))
        assert np.isnan(out.df["value"].iloc[0])

    def test_to_matrix_sorts_years_and_time_codes(self):
        out = MetricByYearTimeCode.from_df(
            self.make([2024, 2023, 2024], [2, 1, 1], [3.0, 1.0, 2.0])
        )
        matrix = out.to_matrix()
        assert matrix.index.tolist() == [2023, 2024]
        assert matrix.columns.tolist() == [1, 2]
        assert matrix.loc[2024, 2] == 3.0
        assert np.isnan(matrix.loc[2023, 2])
