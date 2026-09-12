"""Matched comparison of two spot price backtest runs on the warehouse's error rows.

Reads ``fct_spot_price_forecast_accuracy`` for a baseline and a candidate
run, checks that both scored exactly the same (delivery day, time code)
points, and summarizes MAE and bias overall and by the segments the research
log's decision rules use: day part, the periods around the OCCTO forecast
peak-demand hour, calendar month, and high-price days.

The machinery — matching, the per-segment tables and the markdown — is the
shared :mod:`power_market_analytics.forecasting.compare`; this module owns
only what is specific to the spot task.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.compare import (
    DAY_PARTS,
    SegmentComparison,
    assert_matched,
    matched_rows,
    run_errors_from_pandas,
    segment_bias,
    segment_mae,
    segment_overall,
    to_markdown,
)
from power_market_analytics.tasks.spot_price import TASK

__all__ = [
    "DAY_PARTS",
    "RunErrors",
    "SegmentComparison",
    "assert_matched",
    "compare_runs",
    "load_run_errors",
    "to_markdown",
]

#: Value columns the accuracy query returns as floats.
_FLOAT_COLS = ["max_demand_hour_ending", TASK.actual_col, TASK.forecast_col]


class RunErrors(DomainFrame):
    """Row-level forecast errors of backtest runs, with segment attributes.

    ``max_demand_hour_ending`` is the OCCTO 翌々日 forecast peak hour for the
    delivery day (1-24, hour-ending) and is NaN on days without an OCCTO
    forecast (before 2024-04-01), hence float64 and nullable.

    Grain: (run_id, trade_date, time_code).
    """

    schema = {
        "run_id": "object",
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "day_part": "object",
        "max_demand_hour_ending": "float64",
        "actual_price_jpy_kwh": "float64",
        "forecast_price_jpy_kwh": "float64",
    }
    keys = ["run_id", "trade_date", "time_code"]
    non_null_cols = ["day_part", "actual_price_jpy_kwh", "forecast_price_jpy_kwh"]


def load_run_errors(run_ids: list[str], spark: SparkSession | None = None) -> RunErrors:
    """Load the accuracy rows of one or more runs with their segment attributes.

    Parameters
    ----------
    run_ids : list of str
        MLflow run ids present in ``fct_spot_price_forecast_accuracy``.
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
          occto.max_demand_hour_ending,
          acc.actual_price_jpy_kwh,
          acc.forecast_price_jpy_kwh
        from pma_curated.fct_spot_price_forecast_accuracy acc
        join pma_curated.dim_delivery_period period
          on acc.time_code = period.time_code
        left join pma_curated.fct_occto_demand_supply_forecast_daily occto
          on acc.date_key = occto.date_key
          and acc.area_key = occto.area_key
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
    near_peak_hours: int = 1,
    high_price_quantile: float = 0.9,
) -> dict[str, SegmentComparison]:
    """Compare a candidate run against its matched baseline by segment.

    Parameters
    ----------
    errors : RunErrors
        Error rows containing both runs.
    baseline_run_id, candidate_run_id : str
        The two runs. They must have scored identical (trade_date,
        time_code) points.
    near_peak_hours : int, optional
        Half-width in hours of the "near the forecast peak" window: a
        period counts when its hour of day is within this many hours of the
        OCCTO ``max_demand_hour_ending`` hour (0 = the peak hour only, 1 = the
        peak hour and one hour either side, i.e. six 30-min periods).
    high_price_quantile : float, optional
        Delivery days whose mean actual price is at or above this quantile
        of the window are the "high-price days" band.

    Returns
    -------
    dict of str to SegmentComparison
        Keys: ``overall``, ``day_part``, ``near_peak``, ``bias``, ``month``,
        ``price_band``. All but ``bias`` compare MAE; ``bias`` compares the
        mean error (forecast - actual).

    Raises
    ------
    ValueError
        If the two runs do not cover exactly the same points, or no row has
        an OCCTO peak hour to define the near-peak segment.
    """
    df = matched_rows(
        errors, task=TASK, baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id
    )
    df = df.assign(
        month=df["trade_date"].dt.strftime("%Y-%m"),
        # Hour-ending H covers hour-of-day H-1; NaN peak hours compare False.
        near_peak=(
            ((df["time_code"] - 1) // 2 - (df["max_demand_hour_ending"] - 1)).abs()
            <= near_peak_hours
        ),
    )
    if not df["near_peak"].any():
        raise ValueError("No rows carry an OCCTO peak hour; the near-peak segment is undefined")
    daily_mean = df.loc[df["role"] == "baseline"].groupby("trade_date")[TASK.actual_col].mean()
    threshold = daily_mean.quantile(high_price_quantile)
    high_days = set(daily_mean.index[daily_mean >= threshold])
    pct = round(100 * (1 - high_price_quantile))
    df = df.assign(
        price_band=np.where(
            df["trade_date"].isin(high_days),
            f"top {pct}% price days (daily mean >= {threshold:.2f})",
            f"other {100 - pct}% of days",
        )
    )
    logger.info(
        "compare_runs: {} points per run, {} high-price days, {} near-peak rows per run",
        (df["role"] == "baseline").sum(),
        len(high_days),
        (df["near_peak"] & (df["role"] == "baseline")).sum(),
    )
    peak_label = (
        "forecast peak hour only"
        if near_peak_hours == 0
        else f"within ±{near_peak_hours} h of forecast peak hour"
    )
    return {
        "overall": segment_overall(df),
        "day_part": segment_mae(df, df["day_part"], order=DAY_PARTS),
        "near_peak": segment_mae(
            df,
            pd.Series(np.where(df["near_peak"], peak_label, "other periods"), index=df.index),
            order=(peak_label, "other periods"),
        ),
        "bias": segment_bias(df),
        "month": segment_mae(df, df["month"]),
        "price_band": segment_mae(
            df, df["price_band"], order=sorted(df["price_band"].unique())[::-1]
        ),
    }
