"""Tests for the calendar-lag feature join."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.features import join_lag

D1 = pd.Timestamp("2024-01-01")
D2 = pd.Timestamp("2024-01-02")
D3 = pd.Timestamp("2024-01-03")


def prices(days: list[pd.Timestamp]) -> pd.DataFrame:
    """Two time codes per day; price = day-of-month + time_code / 10."""
    return pd.DataFrame(
        {
            "trade_date": [d for d in days for _ in (1, 2)],
            "time_code": np.array([1, 2] * len(days), dtype="int64"),
            "price_jpy_kwh": [d.day + tc / 10 for d in days for tc in (1, 2)],
        }
    )


class TestJoinLag:
    def test_one_day_lag_attaches_previous_day_same_time_code(self):
        df = prices([D1, D2, D3])
        out = join_lag(df, df, value_col="price_jpy_kwh", days=1, name="lag_1d")
        assert list(out.columns) == ["trade_date", "time_code", "price_jpy_kwh", "lag_1d"]
        assert len(out) == 6
        # First day has no D-1: NaN. D2 <- D1 (1.1, 1.2), D3 <- D2 (2.1, 2.2).
        assert out["lag_1d"].isna().tolist() == [True, True, False, False, False, False]
        assert out["lag_1d"].tolist()[2:] == [1.1, 1.2, 2.1, 2.2]

    def test_time_codes_are_not_crossed(self):
        df = prices([D1, D2])
        out = join_lag(df, df, value_col="price_jpy_kwh", days=1, name="lag_1d")
        d2 = out[out["trade_date"] == D2].set_index("time_code")["lag_1d"]
        assert d2.loc[1] == 1.1
        assert d2.loc[2] == 1.2

    def test_gap_day_yields_nan_without_shifting_rows(self):
        # D2 is missing: a row-position lag would hand D1's price to D3.
        df = prices([D1, D3])
        out = join_lag(df, df, value_col="price_jpy_kwh", days=1, name="lag_1d")
        assert len(out) == 4
        assert out["lag_1d"].isna().all()

    def test_lag_length_is_in_calendar_days(self):
        df = prices([D1, D2, D3])
        out = join_lag(df, df, value_col="price_jpy_kwh", days=2, name="lag_2d")
        d3 = out[out["trade_date"] == D3]["lag_2d"].tolist()
        assert d3 == [1.1, 1.2]
        assert out.loc[out["trade_date"] < D3, "lag_2d"].isna().all()

    def test_left_frame_may_differ_from_price_source(self):
        history = prices([D1, D2, D3])
        left = pd.DataFrame(
            {"trade_date": [D3, D3], "time_code": np.array([2, 1], dtype="int64"), "x": [0, 1]}
        )
        out = join_lag(left, history, value_col="price_jpy_kwh", days=1, name="lag_1d")
        # Left row order and columns preserved; only the lag column is added.
        assert list(out.columns) == ["trade_date", "time_code", "x", "lag_1d"]
        assert out["lag_1d"].tolist() == [2.2, 2.1]

    def test_source_price_frame_is_not_mutated(self):
        df = prices([D1, D2])
        before = df.copy()
        join_lag(df, df, value_col="price_jpy_kwh", days=1, name="lag_1d")
        pd.testing.assert_frame_equal(df, before)

    def test_duplicate_keys_in_price_source_rejected(self):
        df = prices([D1, D2])
        dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
        with pytest.raises(pd.errors.MergeError):
            join_lag(df, dup, value_col="price_jpy_kwh", days=1, name="lag_1d")

    def test_duplicate_keys_in_left_rejected(self):
        df = prices([D1, D2])
        dup = pd.concat([df, df.iloc[[2]]], ignore_index=True)
        with pytest.raises(pd.errors.MergeError):
            join_lag(dup, df, value_col="price_jpy_kwh", days=1, name="lag_1d")

    def test_value_col_names_the_series_column(self):
        df = prices([D1, D2]).rename(columns={"price_jpy_kwh": "demand_kwh"})
        out = join_lag(df, df, value_col="demand_kwh", days=1, name="lag_1d_demand_kwh")
        assert list(out.columns) == ["trade_date", "time_code", "demand_kwh", "lag_1d_demand_kwh"]
        assert out.loc[out["trade_date"] == D2, "lag_1d_demand_kwh"].tolist() == [1.1, 1.2]
