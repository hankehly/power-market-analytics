"""Compare a candidate backtest run with its matched baseline, by segment.

Run inside the devcontainer after both runs have been published and the
forecast lineage rebuilt (``just dbt build --select +fct_spot_price_forecast_accuracy``):

    python scripts/compare_spot_price_runs.py --baseline <run_id> --candidate <run_id>

Prints markdown tables (overall, day part, near the OCCTO forecast peak
hour, bias, calendar month, high-price days) ready to paste into the
research log.
"""

import argparse

from loguru import logger

from power_market_analytics.tasks.spot_price.compare import (
    compare_runs,
    load_run_errors,
    to_markdown,
)

SECTIONS = {
    "overall": ("Overall", "MAE"),
    "day_part": ("By day part", "MAE"),
    "near_peak": ("Near the OCCTO forecast maximum-demand hour", "MAE"),
    "bias": ("Mean error (forecast − actual)", "bias"),
    "month": ("By calendar month", "MAE"),
    "price_band": ("High-price days", "MAE"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="MLflow run id of the baseline.")
    parser.add_argument("--candidate", required=True, help="MLflow run id of the candidate.")
    parser.add_argument(
        "--near-peak-hours",
        type=int,
        default=1,
        help="Half-width in hours of the near-peak window around max_demand_hour_ending.",
    )
    parser.add_argument(
        "--high-price-quantile",
        type=float,
        default=0.9,
        help="Daily-mean-price quantile above which a delivery day counts as high-price.",
    )
    args = parser.parse_args()

    errors = load_run_errors([args.baseline, args.candidate])
    tables = compare_runs(
        errors,
        baseline_run_id=args.baseline,
        candidate_run_id=args.candidate,
        near_peak_hours=args.near_peak_hours,
        high_price_quantile=args.high_price_quantile,
    )
    print(f"Baseline run: `{args.baseline}`  ")
    print(f"Candidate run: `{args.candidate}`\n")
    for key, (title, metric) in SECTIONS.items():
        print(f"### {title}\n")
        print(to_markdown(tables[key], metric=metric))
        print()
    months = tables["month"].df
    improved = int((months["abs_change"] < 0).sum())
    logger.info("Candidate improves MAE in {} of {} calendar months", improved, len(months))


if __name__ == "__main__":
    main()
