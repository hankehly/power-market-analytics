"""Tests for the TaskSpec that parameterises the forecasting framework."""

from __future__ import annotations

import dataclasses
from typing import Any

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
    kwargs: dict[str, Any] = dict(
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

    def test_an_unpinned_task_has_no_evaluation_window(self):
        spec = make_spec()
        assert spec.eval_start is None and spec.eval_end is None

    def test_an_evaluation_window_is_pinned_at_both_ends_or_neither(self):
        with pytest.raises(ValueError, match="pin both eval_start and eval_end, or neither"):
            make_spec(eval_start=pd.Timestamp("2024-04-01"))
        with pytest.raises(ValueError, match="pin both eval_start and eval_end, or neither"):
            make_spec(eval_end=pd.Timestamp("2026-03-31"))

    def test_an_evaluation_window_must_not_run_backwards(self):
        with pytest.raises(ValueError, match="eval_start 2026-03-31 is after eval_end 2024-04-01"):
            make_spec(
                eval_start=pd.Timestamp("2026-03-31"), eval_end=pd.Timestamp("2024-04-01")
            )

    def test_eval_window_refuses_an_unpinned_task(self):
        # A caller that needs the window says so; an unpinned task cannot be
        # mistaken for one whose window happens to be missing.
        with pytest.raises(ValueError, match="no evaluation window is pinned"):
            _ = make_spec().eval_window

    def test_eval_window_returns_both_ends_of_a_pinned_one(self):
        spec = make_spec(
            eval_start=pd.Timestamp("2024-04-01"), eval_end=pd.Timestamp("2026-03-31")
        )
        assert spec.eval_window == (pd.Timestamp("2024-04-01"), pd.Timestamp("2026-03-31"))

    def test_a_task_may_reserve_no_holdout(self):
        spec = make_spec(
            eval_start=pd.Timestamp("2024-04-01"), eval_end=pd.Timestamp("2026-03-31")
        )
        assert spec.holdout_start is None and spec.holdout_end is None
        with pytest.raises(ValueError, match="no holdout is reserved"):
            _ = spec.holdout_window

    def test_a_reserved_holdout_returns_both_ends(self):
        spec = make_spec(
            eval_start=pd.Timestamp("2024-04-01"),
            eval_end=pd.Timestamp("2026-03-31"),
            holdout_start=pd.Timestamp("2026-09-06"),
            holdout_end=pd.Timestamp("2027-03-31"),
        )
        assert spec.holdout_window == (
            pd.Timestamp("2026-09-06"),
            pd.Timestamp("2027-03-31"),
        )

    def test_a_holdout_is_reserved_at_both_ends_or_neither(self):
        for field in ("holdout_start", "holdout_end"):
            with pytest.raises(
                ValueError, match="reserve both holdout_start and holdout_end, or neither"
            ):
                make_spec(
                    eval_start=pd.Timestamp("2024-04-01"),
                    eval_end=pd.Timestamp("2026-03-31"),
                    **{field: pd.Timestamp("2026-09-06")},
                )

    def test_a_reserved_holdout_needs_a_pinned_window(self):
        with pytest.raises(ValueError, match="a reserved holdout needs a pinned eval_end"):
            make_spec(
                holdout_start=pd.Timestamp("2026-09-06"),
                holdout_end=pd.Timestamp("2027-03-31"),
            )

    def test_a_holdout_must_follow_the_evaluation_window(self):
        with pytest.raises(ValueError, match="holdout_start 2026-03-31 must follow eval_end"):
            make_spec(
                eval_start=pd.Timestamp("2024-04-01"),
                eval_end=pd.Timestamp("2026-03-31"),
                holdout_start=pd.Timestamp("2026-03-31"),
                holdout_end=pd.Timestamp("2027-03-31"),
            )

    def test_a_holdout_must_not_run_backwards(self):
        with pytest.raises(ValueError, match="holdout_end 2026-09-05 is before holdout_start"):
            make_spec(
                eval_start=pd.Timestamp("2024-04-01"),
                eval_end=pd.Timestamp("2026-03-31"),
                holdout_start=pd.Timestamp("2026-09-06"),
                holdout_end=pd.Timestamp("2026-09-05"),
            )

    def test_a_one_day_window_is_allowed(self):
        day = pd.Timestamp("2024-04-01")
        spec = make_spec(eval_start=day, eval_end=day)
        assert spec.eval_start == spec.eval_end == day

    def test_frames_must_agree_on_the_forecast_column(self):
        with pytest.raises(
            ValueError,
            match=r"load: forecast column differs across frames: "
            r"\['forecast_load_mw', 'forecast_something_else'\]",
        ):
            make_spec(records_cls=OtherRecords)

    def test_contribution_table_and_column_derive_from_the_forecast_ones(self):
        spec = make_spec()
        assert spec.contribution_table == "pma_ml.load_forecast_contribution"
        assert spec.contribution_col == "contribution_load_mw"

    def test_importance_table_and_mae_columns_derive_from_the_forecast_ones(self):
        spec = make_spec()
        assert spec.importance_table == "pma_ml.load_forecast_importance"
        assert spec.mae_col == "mae_load_mw"
        assert spec.permuted_mae_col == "permuted_mae_load_mw"

    def test_forecast_column_must_carry_the_forecast_prefix(self):
        class BareForecast(DayAheadForecast):
            forecast_col = "yhat_load_mw"

        class BareResult(BacktestResult):
            actual_col = "actual_load_mw"
            forecast_col = "yhat_load_mw"

        class BareRecords(ForecastRecords):
            forecast_col = "yhat_load_mw"

        with pytest.raises(
            ValueError, match="load: forecast column 'yhat_load_mw' must start with 'forecast_'"
        ):
            make_spec(forecast_cls=BareForecast, result_cls=BareResult, records_cls=BareRecords)
