"""Run a spot price forecasting backtest and log it to MLflow.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/spot_price_backtest.py --strategy previous_day --area tokyo

Pin ``--start-date``/``--end-date`` (and ``--train-start`` for model
strategies) when two runs must be compared on identical delivery days and
training rows, e.g. a feature experiment against its matched baseline.
"""

import argparse

import mlflow
import pandas as pd
from loguru import logger

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
from power_market_analytics.tasks.spot_price.strategies import STRATEGIES, build_strategy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="previous_day")
    parser.add_argument(
        "--area", choices=AREA_CODES, default="tokyo", help="dim_area.area_code value."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1825,
        help="Backtest window length in delivery days, ending at --end-date.",
    )
    parser.add_argument(
        "--start-date",
        type=pd.Timestamp,
        default=None,
        help="First delivery day to forecast (YYYY-MM-DD); overrides --days.",
    )
    parser.add_argument(
        "--end-date",
        type=pd.Timestamp,
        default=None,
        help="Last delivery day to forecast (YYYY-MM-DD); default: the last day in the data.",
    )
    parser.add_argument(
        "--train-start",
        type=pd.Timestamp,
        default=None,
        help=(
            "First delivery day eligible as a training row (model strategies only), e.g. "
            "2024-04-01 to fit a baseline on exactly the rows an OCCTO-feature candidate can use."
        ),
    )
    parser.add_argument(
        "--shap-nsamples",
        type=int,
        default=500,
        help="Rows sampled for the SHAP plots in the MLflow evaluation.",
    )
    args = parser.parse_args(argv)

    with task_run(
        MLFLOW_EXPERIMENT,
        run_name=f"{args.strategy}-{args.area}",
        tags={"strategy": args.strategy, "area": args.area},
    ) as run:
        prices = load_area_spot_prices(area_code=args.area)
        last_day = prices.df["trade_date"].max()
        end_date = last_day if args.end_date is None else args.end_date
        if end_date > last_day:
            parser.error(f"--end-date {end_date.date()} is after the last day in the data")
        start_date = (
            end_date - pd.DateOffset(days=args.days - 1)
            if args.start_date is None
            else args.start_date
        )
        if start_date > end_date:
            parser.error(f"start date {start_date.date()} is after end date {end_date.date()}")

        strategy = build_strategy(
            args.strategy, area_code=args.area, train_start_date=args.train_start
        )
        result = run_backtest(strategy, prices, start_date=start_date, end_date=end_date)

        per_day = daily_metrics(result)

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

        eval_set = strategy.build_eval_set(
            prices, start_date=start_date, end_date=end_date, result=result
        )
        evaluation = strategy.evaluate(eval_set, explainability_nsamples=args.shap_nsamples)

        run_id = run.info.run_id

    logger.info(
        "strategy={} area={} window={}..{} days={} predictions={}",
        args.strategy,
        args.area,
        start_date.date(),
        end_date.date(),
        per_day["trade_date"].nunique(),
        len(result),
    )
    logger.info(
        "MAE={:.3f} JPY/kWh  MAPE={:.2f}%",
        evaluation.metrics["mean_absolute_error"],
        evaluation.metrics[MAPE_METRIC_NAME],
    )
    logger.info("MLflow evaluation metrics:")
    for metric, value in sorted(evaluation.metrics.items()):
        logger.info("  {}={:.4f}", metric, value)
    logger.info("MLflow evaluation artifacts: {}", ", ".join(sorted(evaluation.artifacts)))
    logger.info("MLflow run: {} (experiment: {})", run_id, MLFLOW_EXPERIMENT)
    logger.info("Forecasts written to {} (partition run_id={})", FORECAST_TABLE, run_id)


if __name__ == "__main__":
    main()
