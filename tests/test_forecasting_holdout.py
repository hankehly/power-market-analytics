"""The holdout boundary is derived from what has been scored, not configured."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.forecasting.holdout import (
    accuracy_table,
    holdout_opens,
    scored_through,
)
from power_market_analytics.tasks.demand import TASK

FLOOR = TASK.holdout_opens


def write_accuracy(spark, days: list[str]) -> None:
    spark.sql("create database if not exists pma_curated")
    spark.createDataFrame(
        pd.DataFrame({"date_key": pd.to_datetime(days).date, "run_id": ["r"] * len(days)}),
        "date_key date, run_id string",
    ).write.mode("overwrite").saveAsTable(accuracy_table(TASK))


class TestAccuracyTable:
    def test_names_the_tasks_curated_mart(self):
        assert accuracy_table(TASK) == "pma_curated.fct_demand_forecast_accuracy"


class TestScoredThrough:
    def test_a_missing_mart_means_nothing_was_scored(self, spark):
        spark.sql(f"drop table if exists {accuracy_table(TASK)}")
        assert scored_through(TASK, spark=spark) is None

    def test_an_empty_mart_means_nothing_was_scored(self, spark):
        write_accuracy(spark, [])
        assert scored_through(TASK, spark=spark) is None

    def test_the_last_day_any_run_scored(self, spark):
        write_accuracy(spark, ["2026-09-06", "2026-09-19", "2026-09-10"])
        assert scored_through(TASK, spark=spark) == pd.Timestamp("2026-09-19")


class TestHoldoutOpens:
    def test_without_a_run_it_is_the_tasks_floor(self, spark):
        spark.sql(f"drop table if exists {accuracy_table(TASK)}")
        assert holdout_opens(TASK, spark=spark) == FLOOR

    def test_a_run_inside_the_window_does_not_move_it(self, spark):
        # Scoring the evaluation window spends nothing: those days were never unseen.
        write_accuracy(spark, ["2026-03-31"])
        assert holdout_opens(TASK, spark=spark) == FLOOR

    def test_a_confirmation_run_spends_the_days_it_scored(self, spark):
        # The defect this closes: a second run over the same days is not independent.
        write_accuracy(spark, [str(FLOOR.date()), str((FLOOR + pd.Timedelta(days=3)).date())])
        assert holdout_opens(TASK, spark=spark) == FLOOR + pd.Timedelta(days=4)

    def test_an_unpinned_task_has_no_boundary(self, spark):
        import dataclasses

        unpinned = dataclasses.replace(TASK, eval_start=None, eval_end=None, holdout_start=None)
        with pytest.raises(ValueError, match="no evaluation window is pinned"):
            holdout_opens(unpinned, spark=spark)
