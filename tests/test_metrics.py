"""Tests for the generic forecast error metrics."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from power_market_analytics.common.metrics import mae, mape


class TestMae:
    def test_mean_of_absolute_errors(self):
        # |1-1| + |2-4| + |3-6| = 0 + 2 + 3 = 5 over 3 rows.
        assert mae(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 4.0, 6.0])) == pytest.approx(5 / 3)

    def test_sign_of_error_is_ignored(self):
        # +2 and -2 must not cancel: (2 + 2) / 2 = 2.
        assert mae(pd.Series([10.0, 10.0]), pd.Series([12.0, 8.0])) == 2.0

    def test_returns_plain_float(self):
        result = mae(pd.Series([1.0]), pd.Series([1.0]))
        assert type(result) is float
        assert result == 0.0


class TestMape:
    def test_reported_in_percent(self):
        # |2-3|/2 = 0.5, |4-5|/4 = 0.25 -> mean 0.375 -> 37.5 %.
        assert mape(pd.Series([2.0, 4.0]), pd.Series([3.0, 5.0])) == 37.5

    def test_zero_actuals_excluded_from_the_mean(self):
        # The zero row would be undefined; the remaining two average to 37.5 %.
        assert mape(pd.Series([0.0, 2.0, 4.0]), pd.Series([1.0, 3.0, 5.0])) == 37.5

    def test_negative_actual_uses_absolute_value_in_denominator(self):
        # |-2 - (-1)| / |-2| = 0.5, |4-5|/4 = 0.25 -> 37.5 %, not negative.
        assert mape(pd.Series([-2.0, 4.0]), pd.Series([-1.0, 5.0])) == 37.5

    def test_over_and_under_forecast_count_the_same(self):
        assert mape(pd.Series([2.0]), pd.Series([1.0])) == 50.0
        assert mape(pd.Series([2.0]), pd.Series([3.0])) == 50.0

    def test_all_zero_actuals_is_undefined(self):
        assert math.isnan(mape(pd.Series([0.0, 0.0]), pd.Series([1.0, 2.0])))

    def test_returns_plain_float(self):
        assert type(mape(pd.Series([1.0]), pd.Series([1.0]))) is float
