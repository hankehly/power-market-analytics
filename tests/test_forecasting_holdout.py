"""The holdout boundary is derived from what has been scored, not configured."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.forecasting.holdout import holdout_opens, scored_through
from power_market_analytics.tasks.demand import TASK

FLOOR = TASK.holdout_opens


def write_forecasts(spark, days: list[str]) -> None:
    """Write the table a run writes itself, not the dbt mart built from it."""
    spark.sql("create database if not exists pma_ml")
    spark.createDataFrame(
        pd.DataFrame({"trade_date": pd.to_datetime(days).date, "run_id": ["r"] * len(days)}),
        "trade_date date, run_id string",
    ).write.mode("overwrite").saveAsTable(TASK.forecast_table)


class TestScoredThrough:
    def test_the_source_is_the_table_a_run_writes_not_the_dbt_mart(self, spark):
        # The accuracy mart is a dbt table and stays stale until the next build,
        # so a run started before it would reuse days the last run just scored.
        assert TASK.forecast_table == "pma_ml.demand_forecast"

    def test_a_read_failure_is_raised_rather_than_read_as_empty(self, spark, monkeypatch):
        # Failing open would move the boundary back and call scored days unseen.
        write_forecasts(spark, ["2026-09-06"])
        import power_market_analytics.forecasting.holdout as module

        def boom(*args, **kwargs):
            raise RuntimeError("metastore is down")

        monkeypatch.setattr(module, "query_pandas", boom)
        with pytest.raises(RuntimeError, match="metastore is down"):
            scored_through(TASK, spark=spark)

    def test_a_missing_table_means_nothing_was_scored(self, spark):
        spark.sql(f"drop table if exists {TASK.forecast_table}")
        assert scored_through(TASK, spark=spark) is None

    def test_an_empty_table_means_nothing_was_scored(self, spark):
        write_forecasts(spark, [])
        assert scored_through(TASK, spark=spark) is None

    def test_the_last_day_any_run_scored(self, spark):
        write_forecasts(spark, ["2026-09-06", "2026-09-19", "2026-09-10"])
        assert scored_through(TASK, spark=spark) == pd.Timestamp("2026-09-19")


class TestHoldoutOpens:
    def test_without_a_run_it_is_the_tasks_floor(self, spark):
        spark.sql(f"drop table if exists {TASK.forecast_table}")
        assert holdout_opens(TASK, spark=spark) == FLOOR

    def test_a_run_inside_the_window_does_not_move_it(self, spark):
        # Scoring the evaluation window spends nothing: those days were never unseen.
        write_forecasts(spark, ["2026-03-31"])
        assert holdout_opens(TASK, spark=spark) == FLOOR

    def test_a_confirmation_run_spends_the_days_it_scored(self, spark):
        # The defect this closes: a second run over the same days is not independent.
        write_forecasts(spark, [str(FLOOR.date()), str((FLOOR + pd.Timedelta(days=3)).date())])
        assert holdout_opens(TASK, spark=spark) == FLOOR + pd.Timedelta(days=4)

    def test_an_unpinned_task_has_no_boundary(self, spark):
        import dataclasses

        unpinned = dataclasses.replace(TASK, eval_start=None, eval_end=None, holdout_start=None)
        with pytest.raises(ValueError, match="no evaluation window is pinned"):
            holdout_opens(unpinned, spark=spark)
