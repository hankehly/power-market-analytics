"""Tests for the naive previous-day strategy and the strategy interface."""

from __future__ import annotations

import mlflow
import numpy as np
import pandas as pd
import pytest

from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError
from power_market_analytics.tasks.spot_price.frames import SpotPriceForecast, SpotPrices
from power_market_analytics.tasks.spot_price.strategies.naive import (
    FEATURE_COLS,
    TARGET_COL,
    PreviousDayEvalSet,
    PreviousDayModel,
    PreviousDayStrategy,
)

# Nanosecond-resolution days, like the values ``run_backtest`` hands to
# ``predict`` (a bare ``pd.Timestamp("2024-01-01")`` is second-resolution in
# pandas 2 and would broadcast to a ``datetime64[s]`` column).
D1, D2, D3, D4, D5 = pd.date_range("2024-01-01", periods=5, freq="D", unit="ns")


@pytest.fixture(scope="module", autouse=True)
def experiment() -> None:
    """Give this module its own MLflow experiment in the session store."""
    mlflow.set_experiment("test_spot_price_naive")


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


class TestForecastStrategyInterface:
    def test_abstract_base_cannot_be_instantiated(self):
        with pytest.raises(TypeError, match="abstract"):
            ForecastStrategy()  # type: ignore[abstract]

    def test_previous_day_is_a_registered_concrete_strategy(self):
        strategy = PreviousDayStrategy()
        assert isinstance(strategy, ForecastStrategy)
        assert strategy.name == "previous_day"


class TestPreviousDayStrategyPredict:
    def test_copies_previous_day_prices_onto_target_day(self):
        forecast = PreviousDayStrategy().predict(D3, history([D1, D2]))
        assert isinstance(forecast, SpotPriceForecast)
        df = forecast.df
        assert list(df.columns) == ["trade_date", "time_code", "forecast_price_jpy_kwh"]
        assert (df["trade_date"] == D3).all()
        assert sorted(df["time_code"]) == list(range(1, 49))
        # D2's prices are 2.01..2.48, not D1's 1.01..1.48.
        by_tc = df.set_index("time_code")["forecast_price_jpy_kwh"]
        assert by_tc.loc[1] == 2.01
        assert by_tc.loc[48] == 2.48

    def test_second_resolution_target_date_is_normalised_to_ns(self):
        # A bare pd.Timestamp("...") is second-resolution in pandas 2; the
        # forecast column must still meet the datetime64[ns] contract.
        forecast = PreviousDayStrategy().predict(pd.Timestamp("2024-01-03"), history([D1, D2]))
        assert str(forecast.df["trade_date"].dtype) == "datetime64[ns]"
        assert (forecast.df["trade_date"] == D3).all()

    def test_uses_the_day_before_the_target_not_the_latest_day(self):
        # History runs to D3, but the target is D3 itself -> D2's prices.
        forecast = PreviousDayStrategy().predict(D3, history([D1, D2, D3]))
        assert forecast.df["forecast_price_jpy_kwh"].iloc[0] == 2.01

    def test_missing_previous_day_raises(self):
        with pytest.raises(
            ForecastUnavailableError, match="previous_day: no history for previous day 2024-01-03"
        ):
            PreviousDayStrategy().predict(D4, history([D1, D2]))

    def test_incomplete_previous_day_raises(self):
        with pytest.raises(ValueError, match="expected exactly time codes 1..48, got 47 rows"):
            PreviousDayStrategy().predict(D3, history([D1, D2], time_codes=range(1, 48)))


class TestPreviousDayEvalSet:
    def test_to_eval_frame_keeps_only_numeric_features_and_target(self):
        eval_set = PreviousDayEvalSet.from_df(
            pd.DataFrame(
                {
                    "trade_date": [D2, D2],
                    "time_code": np.array([1, 2], dtype="int64"),
                    "lag_1d_price": [1.01, 1.02],
                    TARGET_COL: [2.01, 2.02],
                }
            )
        )
        frame = eval_set.to_eval_frame()
        assert list(frame.columns) == ["lag_1d_price", "actual_price_jpy_kwh"]
        assert frame.dtypes.tolist() == ["float64", "float64"]
        assert frame["lag_1d_price"].tolist() == [1.01, 1.02]

    def test_null_lag_rejected(self):
        with pytest.raises(ValueError, match="column 'lag_1d_price' has 1 null values"):
            PreviousDayEvalSet.from_df(
                pd.DataFrame(
                    {
                        "trade_date": [D2],
                        "time_code": np.array([1], dtype="int64"),
                        "lag_1d_price": [np.nan],
                        TARGET_COL: [2.01],
                    }
                )
            )

    def test_feature_and_target_names(self):
        assert FEATURE_COLS == ("lag_1d_price",)
        assert TARGET_COL == "actual_price_jpy_kwh"


class TestPreviousDayModel:
    def test_predict_returns_the_lag_column_as_array(self):
        frame = pd.DataFrame({"other": [9.0, 9.0], "lag_1d_price": [1.5, 2.5]})
        out = PreviousDayModel().predict(None, frame)
        assert isinstance(out, np.ndarray)
        assert out.tolist() == [1.5, 2.5]


class TestPreviousDayStrategyBuildEvalSet:
    def test_drops_first_day_and_day_after_gap(self):
        # D4 is missing: D1 has no lag (start of history) and D5's lag day is
        # absent, so only D2 and D3 survive.
        prices = history([D1, D2, D3, D5], time_codes=range(1, 3))
        eval_set = PreviousDayStrategy().build_eval_set(prices, D1, D5)
        assert isinstance(eval_set, PreviousDayEvalSet)
        df = eval_set.df
        assert sorted(df["trade_date"].unique()) == [D2, D3]
        assert len(df) == 4
        row = df[(df["trade_date"] == D3) & (df["time_code"] == 2)].iloc[0]
        assert row["lag_1d_price"] == 2.02
        assert row[TARGET_COL] == 3.02

    def test_window_bounds_are_inclusive_and_nothing_dropped_inside(self):
        prices = history([D1, D2, D3, D4], time_codes=range(1, 3))
        eval_set = PreviousDayStrategy().build_eval_set(prices, D2, D3)
        assert sorted(eval_set.df["trade_date"].unique()) == [D2, D3]
        assert len(eval_set) == 4

    def test_raises_when_no_complete_rows_remain(self):
        prices = history([D1, D2], time_codes=range(1, 3))
        with pytest.raises(
            ValueError,
            match="previous_day: no complete feature rows between 2024-01-01 and 2024-01-01",
        ):
            PreviousDayStrategy().build_eval_set(prices, D1, D1)


class TestPreviousDayStrategyEvaluate:
    def test_logs_model_and_scores_lag_against_actual(self):
        # Flat daily prices 10 -> 12.5 -> 15.625: the lag misses by 2.5 then
        # 3.125 (both 20 % of the actual), so MAE = 2.8125 and MAPE = 20 %.
        prices = SpotPrices.from_df(
            pd.DataFrame(
                {
                    "trade_date": [d for d in (D1, D2, D3) for _ in range(48)],
                    "time_code": np.array(list(range(1, 49)) * 3, dtype="int64"),
                    "price_jpy_kwh": [p for p in (10.0, 12.5, 15.625) for _ in range(48)],
                }
            )
        )
        strategy = PreviousDayStrategy()
        eval_set = strategy.build_eval_set(prices, D2, D3)
        with mlflow.start_run() as run:
            result = strategy.evaluate(eval_set, explainability_nsamples=20)
        assert result.metrics["mean_absolute_error"] == pytest.approx(2.8125)
        assert result.metrics["mape_excl_zero_actuals"] == pytest.approx(20.0)
        assert any("shap" in key for key in result.artifacts)

        model = mlflow.pyfunc.load_model(f"runs:/{run.info.run_id}/previous_day_model")
        features = eval_set.to_eval_frame().drop(columns=[TARGET_COL])
        assert model.predict(features).tolist() == features["lag_1d_price"].tolist()
