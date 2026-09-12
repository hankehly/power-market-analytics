"""Matched comparison of two backtest runs, shared by every task.

A comparison reads a task's accuracy mart for a baseline and a candidate run,
checks that both scored exactly the same (delivery day, time code) points, and
summarizes a metric per segment. What differs per task is only the accuracy
mart's SQL, the segments its research log's decision rules use, and the value
columns — which come from the ``TaskSpec``. Everything else lives here, so the
two tasks cannot drift apart in what "matched comparison" means.

A task module owns its ``RunErrors`` frame (its own segment attributes), its
``load_run_errors`` SQL and its ``compare_runs`` segment list, and re-exports
the names its script uses.
"""

from __future__ import annotations

from typing import NamedTuple, TypeVar

import numpy as np
import pandas as pd

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.task import TaskSpec

#: A task's ``RunErrors`` class, preserved through :func:`run_errors_from_pandas`.
RunErrorsT = TypeVar("RunErrorsT", bound=DomainFrame)

DAY_PARTS = ("Overnight", "Morning", "Daytime", "Evening")
#: Grain both runs must agree on, point for point.
GRAIN = ["trade_date", "time_code"]


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
        In the target's unit, per 30-minute period.
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


def run_errors_from_pandas(
    pdf: pd.DataFrame,
    *,
    run_ids: list[str],
    frame_cls: type[RunErrorsT],
    float_cols: list[str],
) -> RunErrorsT:
    """Validate and type the rows a task's accuracy query returned.

    Parameters
    ----------
    pdf : pandas.DataFrame
        Query result: one row per (run, delivery day, time code).
    run_ids : list of str
        The run ids that were asked for; every one must be present.
    frame_cls : type
        The task's ``RunErrors`` class.
    float_cols : list of str
        Columns to cast to float64 (the task's value columns, plus any
        nullable segment attribute).

    Returns
    -------
    RunErrorsT
        An instance of ``frame_cls``.

    Raises
    ------
    ValueError
        If any run id returns no rows.
    """
    missing = sorted(set(run_ids) - set(pdf["run_id"].unique()))
    if missing:
        raise ValueError(f"No accuracy rows for run ids {missing}; publish + dbt build first?")
    casts = {"time_code": "int64"} | {col: "float64" for col in float_cols}
    typed = pdf.assign(trade_date=pd.to_datetime(pdf["trade_date"])).astype(casts)
    return frame_cls.from_df(typed)


def uncommon_days(
    df: pd.DataFrame, baseline_run_id: str, candidate_run_id: str
) -> dict[str, list[pd.Timestamp]]:
    """The delivery days only one of the two runs scored.

    Parameters
    ----------
    df : pandas.DataFrame
        Rows of both runs.
    baseline_run_id, candidate_run_id : str

    Returns
    -------
    dict of str to list of pandas.Timestamp
        ``baseline``: the days the baseline scored and the candidate did not;
        ``candidate``: the reverse. Both sorted.
    """
    base = {pd.Timestamp(d) for d in df.loc[df["run_id"] == baseline_run_id, "trade_date"]}
    cand = {pd.Timestamp(d) for d in df.loc[df["run_id"] == candidate_run_id, "trade_date"]}
    return {"baseline": sorted(base - cand), "candidate": sorted(cand - base)}


def matched_rows(
    errors: DomainFrame,
    *,
    task: TaskSpec,
    baseline_run_id: str,
    candidate_run_id: str,
    common_days: bool = False,
) -> pd.DataFrame:
    """The two runs' rows with ``role``, ``error`` and ``abs_error`` columns, matched.

    Parameters
    ----------
    errors : DomainFrame
        The task's ``RunErrors``, containing both runs.
    task : TaskSpec
        Supplies the actual and forecast column names.
    baseline_run_id, candidate_run_id : str
        The two runs; they must have scored identical points.
    common_days : bool, optional
        Drop the delivery days only one run scored before matching, so two
        runs that skipped different days compare on the days both scored
        (``uncommon_days`` lists them). A day both scored must still match
        period by period.

    Returns
    -------
    pandas.DataFrame

    Raises
    ------
    ValueError
        If the two runs do not cover exactly the same points.
    """
    df = errors.df[errors.df["run_id"].isin([baseline_run_id, candidate_run_id])]
    if common_days:
        only = uncommon_days(df, baseline_run_id, candidate_run_id)
        df = df[~df["trade_date"].isin([*only["baseline"], *only["candidate"]])]
    assert_matched(df, baseline_run_id, candidate_run_id)
    error = df[task.forecast_col] - df[task.actual_col]
    return df.assign(
        role=np.where(df["run_id"] == baseline_run_id, "baseline", "candidate"),
        error=error,
        abs_error=error.abs(),
    )


def assert_matched(df: pd.DataFrame, baseline_run_id: str, candidate_run_id: str) -> None:
    """Raise unless both runs scored exactly the same points.

    Parameters
    ----------
    df : pandas.DataFrame
        Rows of both runs.
    baseline_run_id, candidate_run_id : str

    Raises
    ------
    ValueError
        If either run is absent, or the two do not cover the same points.
    """
    base = df.loc[df["run_id"] == baseline_run_id, GRAIN]
    cand = df.loc[df["run_id"] == candidate_run_id, GRAIN]
    if base.empty or cand.empty:
        raise ValueError("Both runs must be present in the error rows")
    merged = base.merge(cand, how="outer", on=GRAIN, indicator=True, validate="one_to_one")
    unmatched = merged["_merge"] != "both"
    if unmatched.any():
        # astype(str): the indicator is categorical and would report zero counts too.
        counts = merged.loc[unmatched, "_merge"].astype(str).value_counts().to_dict()
        raise ValueError(
            f"Runs are not matched: {counts} points are not in both "
            f"(left_only = baseline only, right_only = candidate only)"
        )


def segment_overall(df: pd.DataFrame) -> SegmentComparison:
    """MAE over every matched point, as a one-row ``all`` segment."""
    return segment_mae(df, pd.Series("all", index=df.index))


def segment_mae(
    df: pd.DataFrame, segment: pd.Series, order: tuple[str, ...] | list[str] | None = None
) -> SegmentComparison:
    """Compare MAE per segment.

    Parameters
    ----------
    df : pandas.DataFrame
        Matched rows from :func:`matched_rows`.
    segment : pandas.Series
        Segment label per row.
    order : tuple or list of str, optional
        Canonical segment order; labels absent from the data are dropped.
        Sorted lexically when omitted.

    Returns
    -------
    SegmentComparison
    """
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
    return comparison(
        n=grouped[("size", "baseline")],
        baseline=grouped[("mean", "baseline")],
        candidate=grouped[("mean", "candidate")],
        relative=True,
    )


def segment_bias(df: pd.DataFrame) -> SegmentComparison:
    """Compare the mean signed error (forecast − actual) overall and in Daytime.

    Parameters
    ----------
    df : pandas.DataFrame
        Matched rows from :func:`matched_rows`.

    Returns
    -------
    SegmentComparison
    """
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
    return comparison(
        n=stacked["n"], baseline=stacked["baseline"], candidate=stacked["candidate"], relative=False
    )


def segment_mape(df: pd.DataFrame, *, actual_col: str) -> SegmentComparison:
    """Compare the mean absolute percentage error over points with a positive actual.

    Parameters
    ----------
    df : pandas.DataFrame
        Matched rows from :func:`matched_rows`.
    actual_col : str
        The task's actual-value column, e.g. ``task.actual_col``.

    Returns
    -------
    SegmentComparison
    """
    positive = df[df[actual_col] > 0]
    pct = 100 * positive["abs_error"] / positive[actual_col]
    grouped = positive.assign(pct=pct).groupby("role")["pct"].agg(["mean", "size"])
    stacked = pd.DataFrame(
        {
            "n": [grouped.loc["baseline", "size"]],
            "baseline": [grouped.loc["baseline", "mean"]],
            "candidate": [grouped.loc["candidate", "mean"]],
        },
        index=["all"],
    )
    return comparison(
        n=stacked["n"], baseline=stacked["baseline"], candidate=stacked["candidate"], relative=True
    )


def comparison(
    *, n: pd.Series, baseline: pd.Series, candidate: pd.Series, relative: bool
) -> SegmentComparison:
    """Assemble a :class:`SegmentComparison` from per-segment aggregates.

    Parameters
    ----------
    n, baseline, candidate : pandas.Series
        Point count and the two runs' metric values, indexed by segment.
    relative : bool
        Whether a relative change is meaningful; NaN when it is not.

    Returns
    -------
    SegmentComparison
    """
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


def daily_paired_comparison(
    errors: DomainFrame,
    *,
    task: TaskSpec,
    baseline_run_id: str,
    candidate_run_id: str,
    resamples: int = 10_000,
    seed: int = 0,
    top_days: int = 10,
    common_days: bool = False,
) -> DailyPairedComparison:
    """Compare the two runs day by day and bootstrap the mean daily-MAE difference.

    Parameters
    ----------
    errors : DomainFrame
        The task's ``RunErrors``, containing both runs.
    task : TaskSpec
        Supplies the actual and forecast column names.
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
    common_days : bool, optional
        Compare on the delivery days both runs scored (``matched_rows``).

    Returns
    -------
    DailyPairedComparison

    Raises
    ------
    ValueError
        If the two runs do not cover exactly the same points.
    """
    df = matched_rows(
        errors,
        task=task,
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        common_days=common_days,
    )
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


def fmt(value: float, decimals: int, *, signed: bool) -> str:
    """Thousands-separated number with a typographic minus sign.

    Parameters
    ----------
    value : float
    decimals : int
    signed : bool
        Whether to print a leading ``+`` on positive values.

    Returns
    -------
    str
    """
    text = f"{value:+,.{decimals}f}" if signed else f"{value:,.{decimals}f}"
    return text.replace("-", "−")


def to_markdown(table: SegmentComparison, *, metric: str, unit: str, decimals: int) -> str:
    """Render a segment comparison as a GitHub-flavored markdown table.

    A comparison without relative changes (bias) is a signed metric, so its
    baseline and candidate values are printed with their sign.

    Parameters
    ----------
    table : SegmentComparison
    metric : str
        Metric name for the header, e.g. ``MAE``.
    unit : str
        Unit appended to the value columns' header.
    decimals : int
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
        rel = "—" if pd.isna(rel_change_pct) else fmt(rel_change_pct, 1, signed=True) + " %"
        lines.append(
            f"| {segment} | {n:,} | {fmt(baseline, decimals, signed=signed_values)} | "
            f"{fmt(candidate, decimals, signed=signed_values)} | "
            f"{fmt(abs_change, decimals, signed=True)} | {rel} |"
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
            f"- mean daily-MAE difference {fmt(paired.mean_diff, 0, signed=True)} {unit}; "
            f"95 % bootstrap CI over days [{fmt(paired.ci_low, 0, signed=True)}, "
            f"{fmt(paired.ci_high, 0, signed=True)}] "
            f"({paired.resamples:,} resamples, seed {paired.seed})",
            f"- median daily-MAE difference {fmt(paired.median_diff, 0, signed=True)} {unit}",
            f"- the {paired.top_days} most-improved day(s) account for {share} of the total "
            "absolute-error reduction",
        ]
    )
