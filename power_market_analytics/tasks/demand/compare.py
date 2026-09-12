"""Matched comparison of two demand backtest runs on the warehouse's error rows.

Reads ``fct_demand_forecast_accuracy`` for a baseline and a candidate run,
checks that both scored exactly the same (delivery day, time code) points,
and summarizes MAE, MAPE and bias overall and by the segments the demand
research log's decision rules use — day part, day type, calendar month,
season, actual-demand band and high-demand days — plus a daily paired
comparison (share of days the candidate is lower, a bootstrap interval of
the mean daily-MAE difference and how concentrated the gain is).

The machinery — matching, the per-segment tables, the daily paired
comparison and the markdown — is the shared
:mod:`power_market_analytics.forecasting.compare`; this module owns only
what is specific to the demand task.
"""

from __future__ import annotations

import numpy as np
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.compare import (
    DAY_PARTS,
    DailyPairedComparison,
    SegmentComparison,
    matched_rows,
    paired_to_markdown,
    run_errors_from_pandas,
    segment_bias,
    segment_mae,
    segment_mape,
    segment_overall,
    to_markdown,
)
from power_market_analytics.forecasting.compare import (
    daily_paired_comparison as _daily_paired_comparison,
)
from power_market_analytics.tasks.demand import TASK

__all__ = [
    "DAY_PARTS",
    "DAY_TYPES",
    "SEASONS",
    "DailyPairedComparison",
    "RunErrors",
    "SegmentComparison",
    "compare_runs",
    "daily_paired_comparison",
    "load_run_errors",
    "paired_to_markdown",
    "to_markdown",
]

DAY_TYPES = ("Weekday", "Weekend", "Holiday")
SEASONS = ("Winter (Dec–Feb)", "Spring (Mar–May)", "Summer (Jun–Aug)", "Autumn (Sep–Nov)")
_SEASON_OF_MONTH = {
    12: SEASONS[0],
    1: SEASONS[0],
    2: SEASONS[0],
    3: SEASONS[1],
    4: SEASONS[1],
    5: SEASONS[1],
    6: SEASONS[2],
    7: SEASONS[2],
    8: SEASONS[2],
    9: SEASONS[3],
    10: SEASONS[3],
    11: SEASONS[3],
}
#: Value columns the accuracy query returns as floats.
_FLOAT_COLS = [TASK.actual_col, TASK.forecast_col]


class RunErrors(DomainFrame):
    """Row-level forecast errors of demand backtest runs, with segment attributes.

    ``day_type`` is ``Holiday`` on a ``dim_date`` holiday (a national holiday
    or a customary non-working day: 年末年始, ゴールデンウィーク, お盆), else
    ``Weekend`` on a Saturday/Sunday, else ``Weekday``.

    Grain: (run_id, trade_date, time_code).
    """

    schema = {
        "run_id": "object",
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "day_part": "object",
        "day_type": "object",
        "actual_demand_kwh": "float64",
        "forecast_demand_kwh": "float64",
    }
    keys = ["run_id", "trade_date", "time_code"]
    non_null_cols = ["day_part", "day_type", "actual_demand_kwh", "forecast_demand_kwh"]


def load_run_errors(run_ids: list[str], spark: SparkSession | None = None) -> RunErrors:
    """Load the accuracy rows of one or more runs with their segment attributes.

    Parameters
    ----------
    run_ids : list of str
        MLflow run ids present in ``fct_demand_forecast_accuracy``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    RunErrors

    Raises
    ------
    ValueError
        If any run id returns no rows.
    """
    in_list = ", ".join(f"'{run_id}'" for run_id in run_ids)
    pdf = query_pandas(
        f"""
        select
          acc.run_id,
          acc.date_key as trade_date,
          acc.time_code,
          period.day_part,
          case
            when d.is_holiday then 'Holiday'
            when d.is_weekend then 'Weekend'
            else 'Weekday'
          end as day_type,
          acc.actual_demand_kwh,
          acc.forecast_demand_kwh
        from pma_curated.fct_demand_forecast_accuracy acc
        join pma_curated.dim_delivery_period period
          on acc.time_code = period.time_code
        join pma_curated.dim_date d
          on acc.date_key = d.date_key
        where acc.run_id in ({in_list})
        """,
        spark=spark,
    )
    return run_errors_from_pandas(pdf, run_ids=run_ids, frame_cls=RunErrors, float_cols=_FLOAT_COLS)


def compare_runs(
    errors: RunErrors,
    *,
    baseline_run_id: str,
    candidate_run_id: str,
    high_demand_quantile: float = 0.9,
    band_mwh: int = 2000,
) -> dict[str, SegmentComparison]:
    """Compare a candidate run against its matched baseline by segment.

    Parameters
    ----------
    errors : RunErrors
        Error rows containing both runs.
    baseline_run_id, candidate_run_id : str
        The two runs. They must have scored identical (trade_date,
        time_code) points.
    high_demand_quantile : float, optional
        Delivery days whose mean actual demand is at or above this quantile
        of the window are the "high-demand days" band.
    band_mwh : int, optional
        Width of the actual-demand bands in MWh per 30-minute period (the
        Superset dashboard's 2,000-MWh bands by default).

    Returns
    -------
    dict of str to SegmentComparison
        Keys: ``overall``, ``mape``, ``bias``, ``day_part``, ``day_type``,
        ``month``, ``season``, ``demand_band``, ``demand_days``. ``mape``
        compares the mean absolute percentage error over points with a
        positive actual, ``bias`` the mean error (forecast − actual) overall
        and in the Daytime day part, everything else MAE.

    Raises
    ------
    ValueError
        If the two runs do not cover exactly the same points.
    """
    df = matched_rows(
        errors, task=TASK, baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id
    )
    daily_mean = df.loc[df["role"] == "baseline"].groupby("trade_date")[TASK.actual_col].mean()
    threshold = daily_mean.quantile(high_demand_quantile)
    high_days = set(daily_mean.index[daily_mean >= threshold])
    pct = round(100 * (1 - high_demand_quantile))
    high_label = f"top {pct}% demand days (daily mean >= {threshold / 1000:,.0f} MWh)"
    other_label = f"other {100 - pct}% of days"
    band_floor = (df[TASK.actual_col] // (band_mwh * 1000) * band_mwh).astype("int64")
    df = df.assign(
        month=df["trade_date"].dt.strftime("%Y-%m"),
        season=df["trade_date"].dt.month.map(_SEASON_OF_MONTH),
        demand_band=[f"{lo:,}–{lo + band_mwh:,} MWh" for lo in band_floor],
        demand_days=np.where(df["trade_date"].isin(high_days), high_label, other_label),
    )
    logger.info(
        "compare_runs: {} points per run over {} days, {} high-demand days",
        (df["role"] == "baseline").sum(),
        daily_mean.size,
        len(high_days),
    )
    return {
        "overall": segment_overall(df),
        "mape": segment_mape(df, actual_col=TASK.actual_col),
        "bias": segment_bias(df),
        "day_part": segment_mae(df, df["day_part"], order=DAY_PARTS),
        "day_type": segment_mae(df, df["day_type"], order=DAY_TYPES),
        "month": segment_mae(df, df["month"]),
        "season": segment_mae(df, df["season"], order=SEASONS),
        "demand_band": segment_mae(df, df["demand_band"], order=_band_order(df["demand_band"])),
        "demand_days": segment_mae(df, df["demand_days"], order=(high_label, other_label)),
    }


def daily_paired_comparison(
    errors: RunErrors,
    *,
    baseline_run_id: str,
    candidate_run_id: str,
    resamples: int = 10_000,
    seed: int = 0,
    top_days: int = 10,
) -> DailyPairedComparison:
    """Compare the two runs day by day and bootstrap the mean daily-MAE difference.

    Parameters
    ----------
    errors : RunErrors
        Error rows containing both runs.
    baseline_run_id, candidate_run_id : str
        The two runs; they must have scored identical points.
    resamples : int, optional
        Bootstrap resamples of the days (with replacement).
    seed : int, optional
        Seed of the bootstrap's random generator, so the interval is
        reproducible.
    top_days : int, optional
        How many of the most-improved days to attribute the gain to; capped
        at the number of days.

    Returns
    -------
    DailyPairedComparison

    Raises
    ------
    ValueError
        If the two runs do not cover exactly the same points.
    """
    return _daily_paired_comparison(
        errors,
        task=TASK,
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        resamples=resamples,
        seed=seed,
        top_days=top_days,
    )


def _band_order(bands) -> list[str]:
    """Band labels sorted by their lower bound (a plain sort would order lexically)."""
    return sorted(bands.unique(), key=lambda label: int(label.split("–")[0].replace(",", "")))
