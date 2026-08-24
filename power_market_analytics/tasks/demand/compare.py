"""Matched comparison of two demand backtest runs on the warehouse's error rows.

Reads ``fct_demand_forecast_accuracy`` for a baseline and a candidate run,
checks that both scored exactly the same (delivery day, time code) points,
and summarizes MAE, MAPE and bias overall and by the segments the demand
research log's decision rules use — day part, day type, calendar month,
season, actual-demand band and high-demand days — plus a daily paired
comparison (share of days the candidate is lower, a bootstrap interval of
the mean daily-MAE difference and how concentrated the gain is).
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.warehouse import query_pandas

DAY_PARTS = ("Overnight", "Morning", "Daytime", "Evening")
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
_GRAIN = ["trade_date", "time_code"]


class RunErrors(DomainFrame):
    """Row-level forecast errors of demand backtest runs, with segment attributes.

    ``day_type`` is ``Holiday`` on a national holiday, else ``Weekend`` on a
    Saturday/Sunday, else ``Weekday`` (``dim_date``).

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


class DailyPairedComparison(NamedTuple):
    """Daily paired comparison of two matched runs (candidate minus baseline).

    Each delivery day's MAE is one observation; ``mean_diff`` is the mean of the
    daily differences and ``ci_low``/``ci_high`` its 95 % percentile bootstrap
    interval over days (days resampled with replacement, treated as
    exchangeable). ``top_days_share_pct`` is the share of the total absolute-
    error reduction (sum over all points of |baseline error| − |candidate
    error|) contributed by the ``top_days`` most-improved days — a measure of
    how concentrated the gain is; NaN when there is no net reduction.

    Attributes
    ----------
    n_days : int
    n_candidate_lower : int
    share_candidate_lower_pct : float
    mean_diff, median_diff, ci_low, ci_high : float
        In the target's unit (kWh per 30-minute period).
    resamples, seed : int
    top_days : int
        Days actually used (``min(requested, n_days)``).
    top_days_share_pct : float
    """

    n_days: int
    n_candidate_lower: int
    share_candidate_lower_pct: float
    mean_diff: float
    median_diff: float
    ci_low: float
    ci_high: float
    resamples: int
    seed: int
    top_days: int
    top_days_share_pct: float


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
    missing = sorted(set(run_ids) - set(pdf["run_id"].unique()))
    if missing:
        raise ValueError(f"No accuracy rows for run ids {missing}; publish + dbt build first?")
    pdf = pdf.assign(trade_date=pd.to_datetime(pdf["trade_date"])).astype(
        {
            "time_code": "int64",
            "actual_demand_kwh": "float64",
            "forecast_demand_kwh": "float64",
        }
    )
    return RunErrors.from_df(pdf)


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
    df = _matched_rows(errors, baseline_run_id, candidate_run_id)
    daily_mean = df.loc[df["role"] == "baseline"].groupby("trade_date")["actual_demand_kwh"].mean()
    threshold = daily_mean.quantile(high_demand_quantile)
    high_days = set(daily_mean.index[daily_mean >= threshold])
    pct = round(100 * (1 - high_demand_quantile))
    high_label = f"top {pct}% demand days (daily mean >= {threshold / 1000:,.0f} MWh)"
    other_label = f"other {100 - pct}% of days"
    band_floor = (df["actual_demand_kwh"] // (band_mwh * 1000) * band_mwh).astype("int64")
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
        "overall": _mae_by(df, pd.Series("all", index=df.index)),
        "mape": _mape(df),
        "bias": _bias_by(df),
        "day_part": _mae_by(df, df["day_part"], order=DAY_PARTS),
        "day_type": _mae_by(df, df["day_type"], order=DAY_TYPES),
        "month": _mae_by(df, df["month"]),
        "season": _mae_by(df, df["season"], order=SEASONS),
        "demand_band": _mae_by(df, df["demand_band"], order=_band_order(df["demand_band"])),
        "demand_days": _mae_by(df, df["demand_days"], order=(high_label, other_label)),
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
    df = _matched_rows(errors, baseline_run_id, candidate_run_id)
    daily = df.groupby(["trade_date", "role"])["abs_error"].agg(["mean", "sum"]).unstack("role")
    diff = (daily[("mean", "candidate")] - daily[("mean", "baseline")]).to_numpy(dtype="float64")
    rng = np.random.default_rng(seed)
    means = np.array(
        [rng.choice(diff, size=diff.size, replace=True).mean() for _ in range(resamples)]
    )
    ci_low, ci_high = np.percentile(means, [2.5, 97.5])
    reduction = (daily[("sum", "baseline")] - daily[("sum", "candidate")]).sort_values(
        ascending=False
    )
    k = min(top_days, diff.size)
    total = float(reduction.sum())
    share = 100 * float(reduction.head(k).sum()) / total if total > 0 else float("nan")
    n_lower = int((diff < 0).sum())
    return DailyPairedComparison(
        n_days=int(diff.size),
        n_candidate_lower=n_lower,
        share_candidate_lower_pct=100 * n_lower / diff.size,
        mean_diff=float(diff.mean()),
        median_diff=float(np.median(diff)),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        resamples=resamples,
        seed=seed,
        top_days=k,
        top_days_share_pct=share,
    )


def _matched_rows(errors: RunErrors, baseline_run_id: str, candidate_run_id: str) -> pd.DataFrame:
    """The two runs' rows with ``role``, ``error`` and ``abs_error`` columns, matched."""
    df = errors.df[errors.df["run_id"].isin([baseline_run_id, candidate_run_id])]
    _assert_matched(df, baseline_run_id, candidate_run_id)
    error = df["forecast_demand_kwh"] - df["actual_demand_kwh"]
    return df.assign(
        role=np.where(df["run_id"] == baseline_run_id, "baseline", "candidate"),
        error=error,
        abs_error=error.abs(),
    )


def _assert_matched(df: pd.DataFrame, baseline_run_id: str, candidate_run_id: str) -> None:
    base = df.loc[df["run_id"] == baseline_run_id, _GRAIN]
    cand = df.loc[df["run_id"] == candidate_run_id, _GRAIN]
    if base.empty or cand.empty:
        raise ValueError("Both runs must be present in the error rows")
    merged = base.merge(cand, how="outer", on=_GRAIN, indicator=True, validate="one_to_one")
    unmatched = merged["_merge"] != "both"
    if unmatched.any():
        # astype(str): the indicator is categorical and would report zero counts too.
        counts = merged.loc[unmatched, "_merge"].astype(str).value_counts().to_dict()
        raise ValueError(
            f"Runs are not matched: {counts} points are not in both "
            f"(left_only = baseline only, right_only = candidate only)"
        )


def _band_order(bands: pd.Series) -> list[str]:
    """Band labels sorted by their lower bound (a plain sort would order lexically)."""
    return sorted(bands.unique(), key=lambda label: int(label.split("–")[0].replace(",", "")))


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


def _mape(df: pd.DataFrame) -> SegmentComparison:
    positive = df[df["actual_demand_kwh"] > 0]
    pct = 100 * positive["abs_error"] / positive["actual_demand_kwh"]
    grouped = positive.assign(pct=pct).groupby("role")["pct"].agg(["mean", "size"])
    stacked = pd.DataFrame(
        {
            "n": [grouped.loc["baseline", "size"]],
            "baseline": [grouped.loc["baseline", "mean"]],
            "candidate": [grouped.loc["candidate", "mean"]],
        },
        index=["all"],
    )
    return _comparison(
        n=stacked["n"], baseline=stacked["baseline"], candidate=stacked["candidate"], relative=True
    )


def _bias_by(df: pd.DataFrame) -> SegmentComparison:
    frames = []
    for label, part in (("all", df), ("Daytime", df[df["day_part"] == "Daytime"])):
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


def _fmt(value: float, decimals: int, *, signed: bool) -> str:
    """Thousands-separated number with a typographic minus sign."""
    text = f"{value:+,.{decimals}f}" if signed else f"{value:,.{decimals}f}"
    return text.replace("-", "−")


def to_markdown(
    table: SegmentComparison, *, metric: str, unit: str = "kWh", decimals: int = 0
) -> str:
    """Render a segment comparison as a GitHub-flavored markdown table.

    A comparison without relative changes (bias) is a signed metric, so its
    baseline and candidate values are printed with their sign.

    Parameters
    ----------
    table : SegmentComparison
    metric : str
        Metric name for the header, e.g. ``MAE``.
    unit : str, optional
        Unit appended to the value columns' header.
    decimals : int, optional
        Decimal places of the value columns.

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
    signed_values = bool(table.df["rel_change_pct"].isna().all())
    lines = ["| " + " | ".join(header) + " |", "|---|---:|---:|---:|---:|---:|"]
    # Plain tuples in schema order (the frame contract fixes the column order).
    for segment, n, baseline, candidate, abs_change, rel_change_pct in table.df.itertuples(
        index=False, name=None
    ):
        rel = "—" if pd.isna(rel_change_pct) else _fmt(rel_change_pct, 1, signed=True) + " %"
        lines.append(
            f"| {segment} | {n:,} | {_fmt(baseline, decimals, signed=signed_values)} | "
            f"{_fmt(candidate, decimals, signed=signed_values)} | "
            f"{_fmt(abs_change, decimals, signed=True)} | {rel} |"
        )
    return "\n".join(lines)


def paired_to_markdown(paired: DailyPairedComparison, *, unit: str = "kWh") -> str:
    """Render the daily paired comparison as markdown bullet lines.

    Parameters
    ----------
    paired : DailyPairedComparison
    unit : str, optional
        Unit of the differences.

    Returns
    -------
    str
    """
    share = "—" if np.isnan(paired.top_days_share_pct) else f"{paired.top_days_share_pct:.0f} %"
    return "\n".join(
        [
            f"- candidate lower on {paired.share_candidate_lower_pct:.1f} % of days "
            f"({paired.n_candidate_lower} of {paired.n_days})",
            f"- mean daily-MAE difference {_fmt(paired.mean_diff, 0, signed=True)} {unit}; "
            f"95 % bootstrap CI over days [{_fmt(paired.ci_low, 0, signed=True)}, "
            f"{_fmt(paired.ci_high, 0, signed=True)}] "
            f"({paired.resamples:,} resamples, seed {paired.seed})",
            f"- median daily-MAE difference {_fmt(paired.median_diff, 0, signed=True)} {unit}",
            f"- the {paired.top_days} most-improved day(s) account for {share} of the total "
            "absolute-error reduction",
        ]
    )
