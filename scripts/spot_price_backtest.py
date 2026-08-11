"""Run a spot price forecasting backtest and log it to MLflow.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/spot_price_backtest.py --strategy previous_day --area tokyo
"""

import argparse
import logging

import mlflow
import pandas as pd

from power_market_analytics.common.tracking import MAPE_METRIC_NAME, log_dataframe, task_run
from power_market_analytics.tasks.spot_price import MLFLOW_EXPERIMENT
from power_market_analytics.tasks.spot_price.backtest import daily_metrics, run_backtest
from power_market_analytics.tasks.spot_price.datasets import AREA_CODES, load_area_spot_prices
from power_market_analytics.tasks.spot_price.plots import error_heatmaps
from power_market_analytics.tasks.spot_price.publish import (
    FORECAST_TABLE,
    build_forecast_records,
    publish_forecast_records,
)
from power_market_analytics.tasks.spot_price.strategies import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="previous_day")
    parser.add_argument(
        "--area", choices=AREA_CODES, default="tokyo", help="dim_area.area_code value."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1825,
        help="Backtest window length in delivery days, ending at the last day in the data.",
    )
    parser.add_argument(
        "--shap-nsamples",
        type=int,
        default=500,
        help="Rows sampled for the SHAP plots in the MLflow evaluation.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    prices = load_area_spot_prices(area_code=args.area)
    end_date = prices.df["trade_date"].max()
    start_date = end_date - pd.DateOffset(days=args.days - 1)

    strategy = STRATEGIES[args.strategy]()
    result = run_backtest(strategy, prices, start_date=start_date, end_date=end_date)

    per_day = daily_metrics(result)

    with task_run(
        MLFLOW_EXPERIMENT,
        run_name=f"{args.strategy}-{args.area}",
        tags={"strategy": args.strategy, "area": args.area},
    ) as run:
        mlflow.log_params(
            {
                "strategy": args.strategy,
                "area": args.area,
                "start_date": str(start_date.date()),
                "end_date": str(end_date.date()),
                "n_days": per_day["trade_date"].nunique(),
                "n_predictions": len(result),
            }
        )
        log_dataframe(per_day, "daily_errors.csv")
        log_dataframe(result.df, "predictions.csv")
        records = build_forecast_records(
            result, run_id=run.info.run_id, strategy=args.strategy, area_code=args.area
        )
        publish_forecast_records(records)
        mlflow.set_tag("warehouse_table", FORECAST_TABLE)
        heatmaps = error_heatmaps(
            result, title=f"Error by year and time code — {args.strategy}, {args.area}"
        )
        mlflow.log_figure(heatmaps, "error_heatmaps_year_time_code.html")

        eval_set = strategy.build_eval_set(prices, start_date=start_date, end_date=end_date)
        evaluation = strategy.evaluate(eval_set, explainability_nsamples=args.shap_nsamples)

        run_id = run.info.run_id

    print(
        f"strategy={args.strategy} area={args.area} "
        f"window={start_date.date()}..{end_date.date()} "
        f"days={per_day['trade_date'].nunique()} predictions={len(result)}"
    )
    print(
        f"MAE={evaluation.metrics['mean_absolute_error']:.3f} JPY/kWh  "
        f"MAPE={evaluation.metrics[MAPE_METRIC_NAME]:.2f}%"
    )
    print("MLflow evaluation metrics:")
    for metric, value in sorted(evaluation.metrics.items()):
        print(f"  {metric}={value:.4f}")
    print(f"MLflow evaluation artifacts: {', '.join(sorted(evaluation.artifacts))}")
    print(f"MLflow run: {run_id} (experiment: {MLFLOW_EXPERIMENT})")
    print(f"Forecasts written to {FORECAST_TABLE} (partition run_id={run_id})")


if __name__ == "__main__":
    main()
