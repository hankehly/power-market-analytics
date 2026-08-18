"""Tests for the demand task definition (TaskSpec + experiment name)."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks import demand, spot_price
from power_market_analytics.tasks.demand import MLFLOW_EXPERIMENT, TASK
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
)


class TestDemandTask:
    def test_spec(self):
        assert isinstance(TASK, TaskSpec)
        assert TASK.name == "demand"
        assert MLFLOW_EXPERIMENT == "demand"
        assert TASK.unit == "kWh"
        assert TASK.history_lead_days == 2
        assert TASK.issue_offset == pd.Timedelta(days=-1, hours=9, minutes=30)
        assert TASK.forecast_table == "pma_ml.demand_forecast"
        assert TASK.history_cls is AreaDemand
        assert TASK.forecast_cls is DemandForecast
        assert TASK.result_cls is DemandBacktestResult
        assert TASK.records_cls is DemandForecastRecords

    def test_column_names(self):
        assert TASK.value_col == "demand_kwh"
        assert TASK.actual_col == "actual_demand_kwh"
        assert TASK.forecast_col == "forecast_demand_kwh"

    def test_history_visible_at_9_30_on_d_minus_1_ends_at_d_minus_2(self):
        # A TSO's file for D-1 only finalises after midnight of D.
        assert TASK.history_cutoff(pd.Timestamp("2024-04-10")) == pd.Timestamp("2024-04-08")

    def test_experiment_is_distinct_from_spot_price(self):
        assert demand.MLFLOW_EXPERIMENT != spot_price.MLFLOW_EXPERIMENT
