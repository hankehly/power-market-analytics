# tests/test_forecasting_backtest.py
"""Tests for the rolling daily backtest engine, exercised through the spot task."""

from __future__ import annotations

import contextlib
from typing import Iterator

import numpy as np
import pandas as pd
import pytest
from loguru import logger
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.backtest import BacktestRun, daily_metrics, run_backtest
from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy

D1, D2, D3, D4, D5, D6 = pd.date_range("2024-01-01", periods=6, freq="D", unit="ns")


@contextlib.contextmanager
def captured_logs(level: str = "INFO") -> Iterator[list[str]]:
    """Collect loguru messages at ``level`` and above (the repo's test idiom)."""
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(m.record["message"]), level=level)
    try:
        yield messages
    finally:
        logger.remove(sink)


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
    """Forecasts 0.0 for every period and records what history it was shown.

    Days listed in ``unavailable`` raise ForecastUnavailableError instead.
    """

    name = "constant"
    task = TASK

    def __init__(self, unavailable: tuple[pd.Timestamp, ...] = ()) -> None:
        self.calls: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
        self.unavailable = unavailable

    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> SpotPriceForecast:
        self.calls.append((target_date, history.df["trade_date"].max(), len(history)))
        if target_date in self.unavailable:
            raise ForecastUnavailableError(f"constant: no features for {target_date.date()}")
        return SpotPriceForecast.from_df(
            pd.DataFrame(
                {
                    "trade_date": [target_date] * 48,
                    "time_code": np.arange(1, 49, dtype="int64"),
                    "forecast_price_jpy_kwh": [0.0] * 48,
                }
            )
        )

    def build_eval_set(self, history, start_date, end_date, run=None) -> DomainFrame:
        raise NotImplementedError

    def evaluate(self, eval_set, **kwargs) -> EvaluationResult:
        raise NotImplementedError


class TestRunBacktest:
    def test_previous_day_forecast_equals_prior_day_actual(self):
        run = run_backtest(PreviousDayStrategy(), history([D1, D2, D3, D4]), D2, D4)
        assert isinstance(run, BacktestRun)
        assert run.skipped_days == ()
        result = run.result
        assert isinstance(result, SpotPriceBacktestResult)
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
        run = run_backtest(PreviousDayStrategy(), history([D1, D2, D3, D4, D5, D6]), D3, D4)
        assert sorted(run.result.df["trade_date"].unique()) == [D3, D4]
        assert len(run.result) == 96

    def test_window_bounds_are_clipped_to_available_days(self):
        run = run_backtest(PreviousDayStrategy(), history([D1, D2, D3]), D2, D6)
        assert sorted(run.result.df["trade_date"].unique()) == [D2, D3]

    def test_strategy_sees_history_through_the_task_cutoff_only(self):
        # spot_price: history_lead_days = 1, so the newest visible day is D-1.
        strategy = ConstantStrategy()
        run_backtest(strategy, history([D1, D2, D3, D4]), D2, D4)
        assert [t for t, _, _ in strategy.calls] == [D2, D3, D4]
        assert [(latest, n) for _, latest, n in strategy.calls] == [
            (D1, 48),
            (D2, 96),
            (D3, 144),
        ]

    def test_target_days_are_nanosecond_timestamps(self):
        strategy = ConstantStrategy()
        run_backtest(strategy, history([D1, D2]), D2, D2)
        (target, _, _) = strategy.calls[0]
        assert isinstance(target, pd.Timestamp)
        assert target.unit == "ns"

    def test_empty_window_raises(self):
        with pytest.raises(ValueError, match="No delivery days between 2024-01-05 .* 2024-01-06"):
            run_backtest(PreviousDayStrategy(), history([D1, D2, D3]), D5, D6)

    def test_unforecastable_days_are_skipped_and_reported(self):
        strategy = ConstantStrategy(unavailable=(D3,))
        with captured_logs("WARNING") as messages:
            run = run_backtest(strategy, history([D1, D2, D3, D4]), D2, D4)
        assert run.skipped_days == (D3,)
        assert sorted(run.result.df["trade_date"].unique()) == [D2, D4]
        assert len(run.result) == 96
        assert "Skipping 2024-01-03: constant: no features for 2024-01-03" in messages

    def test_all_days_skipped_raises(self):
        strategy = ConstantStrategy(unavailable=(D2, D3))
        with pytest.raises(
            ValueError,
            match=(
                "No delivery day between 2024-01-02 and 2024-01-03 could be forecast "
                r"\(2 skipped; last reason: constant: no features for 2024-01-03\)"
            ),
        ):
            run_backtest(strategy, history([D1, D2, D3]), D2, D3)

    def test_previous_day_missing_is_a_skip_not_a_crash(self):
        # D2 is absent from history: D3 has no D-1 and is skipped; D2 itself is
        # not a target day (no actuals) and D4 forecasts from D3.
        run = run_backtest(PreviousDayStrategy(), history([D1, D3, D4]), D2, D4)
        assert run.skipped_days == (D3,)
        assert sorted(run.result.df["trade_date"].unique()) == [D4]

    def test_forecast_points_without_an_actual_are_dropped(self):
        # D3 has only time codes 1..47: the 48-row forecast for D3 joins to 47
        # actuals; the 48th forecast point is dropped and logged.
        prices = SpotPrices.from_df(
            pd.concat([history([D1, D2]).df, history([D3], range(1, 48)).df], ignore_index=True)
        )
        with captured_logs("INFO") as messages:
            run = run_backtest(ConstantStrategy(), prices, D2, D3)
        assert len(run.result) == 48 + 47
        assert run.skipped_days == ()
        assert "1 forecast points have no actual and were dropped" in messages

    def test_result_columns_follow_the_backtest_result_schema(self):
        run = run_backtest(ConstantStrategy(), history([D1, D2]), D2, D2)
        assert list(run.result.df.columns) == [
            "trade_date",
            "time_code",
            "actual_price_jpy_kwh",
            "forecast_price_jpy_kwh",
        ]
        assert (run.result.df["forecast_price_jpy_kwh"] == 0.0).all()
        assert run.result.df["actual_price_jpy_kwh"].tolist()[:2] == [2.01, 2.02]


class TestDailyMetrics:
    def test_one_row_per_day_with_mae_and_mape(self):
        result = SpotPriceBacktestResult.from_df(
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
