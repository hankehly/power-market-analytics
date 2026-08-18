"""Tests for the spot-price TaskSpec."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks.spot_price import MLFLOW_EXPERIMENT, TASK
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPriceForecastRecords,
    SpotPrices,
)


class TestSpotPriceTask:
    def test_spec(self):
        assert isinstance(TASK, TaskSpec)
        assert TASK.name == "spot_price"
        assert MLFLOW_EXPERIMENT == "spot_price"
        assert TASK.unit == "JPY/kWh"
        assert TASK.history_lead_days == 1
        assert TASK.issue_offset == pd.Timedelta(days=-1, hours=9, minutes=55)
        assert TASK.forecast_table == "pma_ml.spot_price_forecast"
        assert TASK.history_cls is SpotPrices
        assert TASK.forecast_cls is SpotPriceForecast
        assert TASK.result_cls is SpotPriceBacktestResult
        assert TASK.records_cls is SpotPriceForecastRecords

    def test_column_names_are_the_historical_spot_price_names(self):
        assert TASK.value_col == "price_jpy_kwh"
        assert TASK.actual_col == "actual_price_jpy_kwh"
        assert TASK.forecast_col == "forecast_price_jpy_kwh"

    def test_history_visible_at_9_55_on_d_minus_1_ends_at_d_minus_1(self):
        assert TASK.history_cutoff(pd.Timestamp("2024-04-10")) == pd.Timestamp("2024-04-09")
