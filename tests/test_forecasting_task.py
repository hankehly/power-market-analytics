"""Tests for the TaskSpec that parameterises the forecasting framework."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)
from power_market_analytics.forecasting.task import TaskSpec


class Series(HalfHourlySeries):
    value_col = "load_mw"


class Forecast(DayAheadForecast):
    forecast_col = "forecast_load_mw"


class Result(BacktestResult):
    actual_col = "actual_load_mw"
    forecast_col = "forecast_load_mw"


class Records(ForecastRecords):
    forecast_col = "forecast_load_mw"


class OtherRecords(ForecastRecords):
    forecast_col = "forecast_something_else"


def make_spec(**overrides) -> TaskSpec:
    kwargs = dict(
        name="load",
        unit="MW",
        history_lead_days=2,
        issue_offset=pd.Timedelta(days=-1, hours=9, minutes=30),
        forecast_table="pma_ml.load_forecast",
        history_cls=Series,
        forecast_cls=Forecast,
        result_cls=Result,
        records_cls=Records,
    )
    kwargs.update(overrides)
    return TaskSpec(**kwargs)


class TestTaskSpec:
    def test_column_names_are_read_off_the_frame_classes(self):
        spec = make_spec()
        assert spec.value_col == "load_mw"
        assert spec.forecast_col == "forecast_load_mw"
        assert spec.actual_col == "actual_load_mw"

    def test_history_cutoff_is_lead_days_before_the_target(self):
        spec = make_spec(history_lead_days=2)
        assert spec.history_cutoff(pd.Timestamp("2024-04-10")) == pd.Timestamp("2024-04-08")
        # numpy datetime64 (as produced by Series.unique()) is accepted too
        assert spec.history_cutoff(pd.Timestamp("2024-04-10").to_datetime64()) == pd.Timestamp(
            "2024-04-08"
        )

    def test_is_frozen(self):
        spec = make_spec()
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "other"

    def test_lead_days_below_one_rejected(self):
        with pytest.raises(ValueError, match="history_lead_days must be >= 1, got 0"):
            make_spec(history_lead_days=0)

    def test_frames_must_agree_on_the_forecast_column(self):
        with pytest.raises(
            ValueError,
            match=r"load: forecast column differs across frames: "
            r"\['forecast_load_mw', 'forecast_something_else'\]",
        ):
            make_spec(records_cls=OtherRecords)
