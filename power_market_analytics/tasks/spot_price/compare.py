"""Matched comparison of two backtest runs on the warehouse's error rows.

Reads ``fct_spot_price_forecast_accuracy`` for a baseline and a candidate
run, checks that both scored exactly the same (delivery day, time code)
points, and summarizes MAE and bias overall and by the segments the research
log's decision rules use: day part, the periods around the OCCTO forecast
peak-demand hour, calendar month, and high-price days.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.warehouse import query_pandas

DAY_PARTS = ("Overnight", "Morning", "Daytime", "Evening")


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


class SegmentComparison(DomainFrame):
    """One metric compared between a baseline and a candidate run per segment.

    ``rel_change_pct`` is NaN where a relative change is meaningless (bias,
    which can be zero or change sign).

    Grain: (segment).
    """

    schema = {
        "segment": "object",
        "n": "int64",
        "baseline": "float64",
        "candidate": "float64",
        "abs_change": "float64",
        "rel_change_pct": "float64",
    }
    keys = ["segment"]
    non_null_cols = ["n", "baseline", "candidate", "abs_change"]


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
    missing = sorted(set(run_ids) - set(pdf["run_id"].unique()))
    if missing:
        raise ValueError(f"No accuracy rows for run ids {missing}; publish + dbt build first?")
    pdf = pdf.assign(trade_date=pd.to_datetime(pdf["trade_date"])).astype(
        {
            "time_code": "int64",
            "max_demand_hour_ending": "float64",
            "actual_price_jpy_kwh": "float64",
            "forecast_price_jpy_kwh": "float64",
        }
    )
    return RunErrors.from_df(pdf)


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
    df = errors.df[errors.df["run_id"].isin([baseline_run_id, candidate_run_id])]
    _assert_matched(df, baseline_run_id, candidate_run_id)
    df = df.assign(
        role=np.where(df["run_id"] == baseline_run_id, "baseline", "candidate"),
        error=df["forecast_price_jpy_kwh"] - df["actual_price_jpy_kwh"],
        abs_error=(df["forecast_price_jpy_kwh"] - df["actual_price_jpy_kwh"]).abs(),
        month=df["trade_date"].dt.strftime("%Y-%m"),
        # Hour-ending H covers hour-of-day H-1; NaN peak hours compare False.
        near_peak=(
            ((df["time_code"] - 1) // 2 - (df["max_demand_hour_ending"] - 1)).abs()
            <= near_peak_hours
        ),
    )
    if not df["near_peak"].any():
        raise ValueError("No rows carry an OCCTO peak hour; the near-peak segment is undefined")
    daily_mean = (
        df.loc[df["role"] == "baseline"].groupby("trade_date")["actual_price_jpy_kwh"].mean()
    )
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
        "overall": _mae_by(df, pd.Series("all", index=df.index)),
        "day_part": _mae_by(df, df["day_part"], order=DAY_PARTS),
        "near_peak": _mae_by(
            df,
            pd.Series(np.where(df["near_peak"], peak_label, "other periods"), index=df.index),
            order=(peak_label, "other periods"),
        ),
        "bias": _bias_by(df),
        "month": _mae_by(df, df["month"]),
        "price_band": _mae_by(df, df["price_band"], order=sorted(df["price_band"].unique())[::-1]),
    }


def _assert_matched(df: pd.DataFrame, baseline_run_id: str, candidate_run_id: str) -> None:
    keys = ["trade_date", "time_code"]
    base = df.loc[df["run_id"] == baseline_run_id, keys]
    cand = df.loc[df["run_id"] == candidate_run_id, keys]
    if base.empty or cand.empty:
        raise ValueError("Both runs must be present in the error rows")
    merged = base.merge(cand, how="outer", on=keys, indicator=True, validate="one_to_one")
    unmatched = merged["_merge"] != "both"
    if unmatched.any():
        counts = merged.loc[unmatched, "_merge"].value_counts().to_dict()
        raise ValueError(
            f"Runs are not matched: {counts} points are not in both "
            f"(left_only = baseline only, right_only = candidate only)"
        )


def _mae_by(
    df: pd.DataFrame, segment: pd.Series, order: tuple[str, ...] | list[str] | None = None
) -> SegmentComparison:
    grouped = (
        df.assign(segment=segment.to_numpy())
        .groupby(["segment", "role"], sort=False)["abs_error"]
        .agg(["mean", "size"])
        .unstack("role")
    )
    grouped = (
        grouped.sort_index()
        if order is None
        else grouped.reindex([s for s in order if s in grouped.index])
    )
    return _comparison(
        n=grouped[("size", "baseline")],
        baseline=grouped[("mean", "baseline")],
        candidate=grouped[("mean", "candidate")],
        relative=True,
    )


def _bias_by(df: pd.DataFrame) -> SegmentComparison:
    segment = pd.Series(
        np.where(df["day_part"] == "Daytime", "Daytime", "other day parts"), index=df.index
    )
    frames = []
    for label, part in (("all", df), ("Daytime", df[segment == "Daytime"])):
        grouped = part.groupby("role")["error"].agg(["mean", "size"])
        frames.append(
            pd.DataFrame(
                {
                    "n": [grouped.loc["baseline", "size"]],
                    "baseline": [grouped.loc["baseline", "mean"]],
                    "candidate": [grouped.loc["candidate", "mean"]],
                },
                index=[label],
            )
        )
    stacked = pd.concat(frames)
    return _comparison(
        n=stacked["n"], baseline=stacked["baseline"], candidate=stacked["candidate"], relative=False
    )


def _comparison(
    *, n: pd.Series, baseline: pd.Series, candidate: pd.Series, relative: bool
) -> SegmentComparison:
    out = pd.DataFrame(
        {
            "segment": n.index.astype(str),
            "n": n.to_numpy().astype("int64"),
            "baseline": baseline.to_numpy().astype("float64"),
            "candidate": candidate.to_numpy().astype("float64"),
        }
    )
    out["abs_change"] = out["candidate"] - out["baseline"]
    out["rel_change_pct"] = 100 * out["abs_change"] / out["baseline"] if relative else np.nan
    return SegmentComparison.from_df(out.astype({"rel_change_pct": "float64"}))


def to_markdown(table: SegmentComparison, *, metric: str, unit: str = "JPY/kWh") -> str:
    """Render a segment comparison as a GitHub-flavored markdown table.

    Parameters
    ----------
    table : SegmentComparison
    metric : str
        Metric name for the header, e.g. ``MAE``.
    unit : str, optional
        Unit appended to the value columns' header.

    Returns
    -------
    str
    """
    header = [
        "Segment",
        "n",
        f"Baseline {metric} ({unit})",
        f"Candidate {metric} ({unit})",
        "Absolute change",
        "Relative change",
    ]
    lines = ["| " + " | ".join(header) + " |", "|---|---:|---:|---:|---:|---:|"]
    # Plain tuples in schema order (the frame contract fixes the column order).
    for segment, n, baseline, candidate, abs_change, rel_change_pct in table.df.itertuples(
        index=False, name=None
    ):
        rel = "—" if pd.isna(rel_change_pct) else f"{rel_change_pct:+.1f}%"
        lines.append(
            f"| {segment} | {n:,} | {baseline:.3f} | {candidate:.3f} | {abs_change:+.3f} | {rel} |"
        )
    return "\n".join(lines)
