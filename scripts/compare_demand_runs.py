"""Compare a candidate demand backtest run with its matched baseline, by segment.

Run inside the devcontainer after both runs have been published and the
forecast lineage rebuilt (``just dbt build --select +fct_demand_forecast_accuracy``):

    python scripts/compare_demand_runs.py --baseline <run_id> --candidate <run_id>

Prints markdown tables (overall MAE, MAPE, bias, day part, day type, calendar
month, season, actual-demand band, high-demand days) and the daily paired
comparison (share of days the candidate is lower, a seeded bootstrap interval
of the mean daily-MAE difference, and how much of the gain the most-improved
days carry), ready to paste into the research log; ``--mae-by-month-png``
also writes the MAE-by-month figure the investigation documents cite.
"""

import argparse

import numpy as np
from loguru import logger

from power_market_analytics.tasks.demand.compare import (
    SegmentComparison,
    compare_runs,
    daily_paired_comparison,
    load_run_errors,
    paired_to_markdown,
    to_markdown,
)

SECTIONS = {
    "overall": ("Overall", "MAE", "kWh", 0),
    "mape": ("Mean absolute percentage error", "MAPE", "%", 2),
    "bias": ("Mean error (forecast − actual)", "bias", "kWh", 0),
    "day_part": ("By day part", "MAE", "kWh", 0),
    "day_type": ("By day type", "MAE", "kWh", 0),
    "month": ("By calendar month", "MAE", "kWh", 0),
    "season": ("By season", "MAE", "kWh", 0),
    "demand_band": ("By actual-demand band", "MAE", "kWh", 0),
    "demand_days": ("High-demand days", "MAE", "kWh", 0),
}
# Categorical palette validated for CVD separation and contrast (light surface).
BASELINE_COLOR = "#2a78d6"
CANDIDATE_COLOR = "#eb6834"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="MLflow run id of the baseline.")
    parser.add_argument("--candidate", required=True, help="MLflow run id of the candidate.")
    parser.add_argument(
        "--high-demand-quantile",
        type=float,
        default=0.9,
        help="Daily-mean-demand quantile above which a delivery day counts as high-demand.",
    )
    parser.add_argument(
        "--band-mwh",
        type=int,
        default=2000,
        help="Width of the actual-demand bands in MWh per 30-minute period.",
    )
    parser.add_argument(
        "--resamples", type=int, default=10_000, help="Bootstrap resamples of the days."
    )
    parser.add_argument("--seed", type=int, default=0, help="Seed of the bootstrap.")
    parser.add_argument(
        "--top-days",
        type=int,
        default=10,
        help="Most-improved days whose share of the total error reduction is reported.",
    )
    parser.add_argument(
        "--mae-by-month-png",
        default=None,
        help="If given, write the MAE-by-month bar chart (baseline vs candidate) to this path.",
    )
    parser.add_argument(
        "--figure-title",
        default="Demand MAE by calendar month, matched window",
        help="Title of the MAE-by-month figure.",
    )
    parser.add_argument("--baseline-label", default="Baseline", help="Legend label.")
    parser.add_argument("--candidate-label", default="Candidate", help="Legend label.")
    args = parser.parse_args(argv)

    errors = load_run_errors([args.baseline, args.candidate])
    tables = compare_runs(
        errors,
        baseline_run_id=args.baseline,
        candidate_run_id=args.candidate,
        high_demand_quantile=args.high_demand_quantile,
        band_mwh=args.band_mwh,
    )
    paired = daily_paired_comparison(
        errors,
        baseline_run_id=args.baseline,
        candidate_run_id=args.candidate,
        resamples=args.resamples,
        seed=args.seed,
        top_days=args.top_days,
    )
    print(f"Baseline run: `{args.baseline}`  ")
    print(f"Candidate run: `{args.candidate}`\n")
    for key, (title, metric, unit, decimals) in SECTIONS.items():
        print(f"### {title}\n")
        print(to_markdown(tables[key], metric=metric, unit=unit, decimals=decimals))
        print()
    print("### Daily paired comparison (candidate − baseline)\n")
    print(paired_to_markdown(paired))
    print()
    months = tables["month"].df
    improved = int((months["abs_change"] < 0).sum())
    logger.info("Candidate improves MAE in {} of {} calendar months", improved, len(months))
    if args.mae_by_month_png is not None:
        write_mae_by_month_figure(
            tables["month"],
            args.mae_by_month_png,
            title=args.figure_title,
            baseline_label=args.baseline_label,
            candidate_label=args.candidate_label,
        )
        logger.info("MAE-by-month figure written to {}", args.mae_by_month_png)


def write_mae_by_month_figure(
    month: SegmentComparison,
    path: str,
    *,
    title: str,
    baseline_label: str,
    candidate_label: str,
) -> None:
    """Write the grouped MAE-by-month bar chart (MWh per 30-minute period).

    Parameters
    ----------
    month : SegmentComparison
        The ``month`` table of :func:`compare_runs`.
    path : str
        Output PNG path.
    title, baseline_label, candidate_label : str
        Figure title and legend labels.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = month.df
    x = np.arange(len(df))
    width = 0.38
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    ax.bar(x - width / 2, df["baseline"] / 1000, width, color=BASELINE_COLOR, label=baseline_label)
    ax.bar(
        x + width / 2, df["candidate"] / 1000, width, color=CANDIDATE_COLOR, label=candidate_label
    )
    ax.set_xticks(x)
    ax.set_xticklabels(df["segment"], rotation=45, ha="right", fontsize=8, color="#52514e")
    ax.set_ylabel("MAE (MWh per 30-min period)", fontsize=9, color="#52514e")
    ax.set_title(title, fontsize=10, color="#0b0b0b", loc="left")
    ax.tick_params(axis="y", labelsize=8, colors="#52514e")
    ax.yaxis.grid(True, color="#e6e5e1", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
