"""Tests for the rolling daily backtest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.strategy import ForecastStrategy
from power_market_analytics.tasks.spot_price.backtest import daily_metrics, run_backtest
from power_market_analytics.tasks.spot_price.frames import (
    BacktestResult,
    DayAheadForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy

D1, D2, D3, D4, D5, D6 = pd.date_range("2024-01-01", periods=6, freq="D", unit="ns")


def history(days: list[pd.Timestamp], time_codes: range = range(1, 49)) -> SpotPrices:
    """Price = day-of-month + time_code / 100, so every (day, time_code) is distinct."""
    return SpotPrices.from_df(
        pd.DataFrame(
            {
                "trade_date": [d for d in days for _ in time_codes],
                "time_code": np.array(list(time_codes) * len(days), dtype="int64"),
                "price_jpy_kwh": [d.day + tc / 100 for d in days for tc in time_codes],
            }
        )
    )


class ConstantStrategy(ForecastStrategy):
    """Forecasts 0.0 for every period and records what history it was shown."""

    name = "constant"

    def __init__(self) -> None:
        self.calls: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []

    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> DayAheadForecast:
        self.calls.append((target_date, history.df["trade_date"].max(), len(history)))
        return DayAheadForecast.from_df(
            pd.DataFrame(
                {
                    "trade_date": [target_date] * 48,
                    "time_code": np.arange(1, 49, dtype="int64"),
                    "forecast_price_jpy_kwh": [0.0] * 48,
                }
            )
        )

    def build_eval_set(self, prices, start_date, end_date, result=None) -> DomainFrame:
        raise NotImplementedError

    def evaluate(self, eval_set, **kwargs) -> EvaluationResult:
        raise NotImplementedError


class TestRunBacktest:
    def test_previous_day_forecast_equals_prior_day_actual(self):
        result = run_backtest(PreviousDayStrategy(), history([D1, D2, D3, D4]), D2, D4)
        assert isinstance(result, BacktestResult)
        df = result.df
        assert len(df) == 3 * 48
        assert sorted(df["trade_date"].unique()) == [D2, D3, D4]
        # Day k's price is k + tc/100, so the D-1 forecast is exactly 1 lower.
        np.testing.assert_allclose(
            df["forecast_price_jpy_kwh"], df["actual_price_jpy_kwh"] - 1.0, atol=1e-12
        )
        row = df[(df["trade_date"] == D3) & (df["time_code"] == 5)].iloc[0]
        assert row["actual_price_jpy_kwh"] == 3.05
        assert row["forecast_price_jpy_kwh"] == 2.05

    def test_only_days_inside_the_window_are_forecast(self):
        # History extends past the window (D5, D6) and before it (D1..D2).
        result = run_backtest(PreviousDayStrategy(), history([D1, D2, D3, D4, D5, D6]), D3, D4)
        assert sorted(result.df["trade_date"].unique()) == [D3, D4]
        assert len(result) == 96

    def test_window_bounds_are_clipped_to_available_days(self):
        result = run_backtest(PreviousDayStrategy(), history([D1, D2, D3]), D2, D6)
        assert sorted(result.df["trade_date"].unique()) == [D2, D3]

    def test_strategy_sees_only_history_before_the_target_day(self):
        strategy = ConstantStrategy()
        run_backtest(strategy, history([D1, D2, D3, D4]), D2, D4)
        assert [t for t, _, _ in strategy.calls] == [D2, D3, D4]
        # Newest history date is D-1 and it holds exactly the earlier days.
        assert [(latest, n) for _, latest, n in strategy.calls] == [
            (D1, 48),
            (D2, 96),
            (D3, 144),
        ]

    def test_empty_window_raises(self):
        with pytest.raises(ValueError, match="No delivery days between 2024-01-05 .* 2024-01-06"):
            run_backtest(PreviousDayStrategy(), history([D1, D2, D3]), D5, D6)

    def test_missing_actual_period_raises_join_mismatch(self):
        # D3 has only time codes 1..47, so the 48-row forecast for D3 joins to
        # 47 actuals: 48 + 47 = 95 rows instead of 2 x 48.
        prices = SpotPrices.from_df(
            pd.concat([history([D1, D2]).df, history([D3], range(1, 48)).df], ignore_index=True)
        )
        with pytest.raises(ValueError, match="Forecast/actual join produced 95 rows, expected 96"):
            run_backtest(ConstantStrategy(), prices, D2, D3)

    def test_result_columns_follow_the_backtest_result_schema(self):
        result = run_backtest(ConstantStrategy(), history([D1, D2]), D2, D2)
        assert list(result.df.columns) == [
            "trade_date",
            "time_code",
            "actual_price_jpy_kwh",
            "forecast_price_jpy_kwh",
        ]
        assert (result.df["forecast_price_jpy_kwh"] == 0.0).all()
        assert result.df["actual_price_jpy_kwh"].tolist()[:2] == [2.01, 2.02]


class TestDailyMetrics:
    def test_one_row_per_day_with_mae_and_mape(self):
        result = BacktestResult.from_df(
            pd.DataFrame(
                {
                    "trade_date": [D2, D2, D1, D1],
                    "time_code": np.array([1, 2, 1, 2], dtype="int64"),
                    # D1: errors 1 and 1 on actuals 2 and 4 -> MAE 1.0,
                    #     MAPE (0.5 + 0.25) / 2 = 37.5 %.
                    # D2: errors 2 and 1 on actuals 10 and 0 -> MAE 1.5,
                    #     MAPE 20 % (the zero actual is excluded).
                    "actual_price_jpy_kwh": [10.0, 0.0, 2.0, 4.0],
                    "forecast_price_jpy_kwh": [8.0, 1.0, 3.0, 3.0],
                }
            )
        )
        metrics = daily_metrics(result)
        assert list(metrics.columns) == ["trade_date", "mae", "mape"]
        assert metrics["trade_date"].tolist() == [D1, D2]
        assert metrics["mae"].tolist() == [1.0, 1.5]
        assert metrics["mape"].tolist() == [37.5, 20.0]
