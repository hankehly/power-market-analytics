"""Run a spot price forecasting backtest and log it to MLflow.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/spot_price_backtest.py --strategy previous_day --area tokyo
"""

import argparse
import logging

import mlflow
import pandas as pd

from power_market_analytics.common.metrics import mae, mape
from power_market_analytics.common.tracking import log_dataframe, task_run
from power_market_analytics.tasks.spot_price import MLFLOW_EXPERIMENT
from power_market_analytics.tasks.spot_price.backtest import daily_metrics, run_backtest
from power_market_analytics.tasks.spot_price.datasets import load_area_spot_prices
from power_market_analytics.tasks.spot_price.plots import error_heatmaps
from power_market_analytics.tasks.spot_price.strategies import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="previous_day")
    parser.add_argument("--area", default="tokyo", help="dim_area.area_code value.")
    parser.add_argument(
        "--days",
        type=int,
        default=1825,
        help="Backtest window length in delivery days, ending at the last day in the data.",
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

    overall_mae = mae(result.df["actual_price_jpy_kwh"], result.df["forecast_price_jpy_kwh"])
    overall_mape = mape(result.df["actual_price_jpy_kwh"], result.df["forecast_price_jpy_kwh"])
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
        mlflow.log_metrics({"mae": overall_mae, "mape": overall_mape})
        log_dataframe(per_day, "daily_errors.csv")
        log_dataframe(result.df, "predictions.csv")
        heatmaps = error_heatmaps(
            result, title=f"Error by year and time code — {args.strategy}, {args.area}"
        )
        mlflow.log_figure(heatmaps, "error_heatmaps_year_time_code.html")
        run_id = run.info.run_id

    print(
        f"strategy={args.strategy} area={args.area} "
        f"window={start_date.date()}..{end_date.date()} "
        f"days={per_day['trade_date'].nunique()} predictions={len(result)}"
    )
    print(f"MAE={overall_mae:.3f} JPY/kWh  MAPE={overall_mape:.2f}%")
    print(f"MLflow run: {run_id} (experiment: {MLFLOW_EXPERIMENT})")


if __name__ == "__main__":
    main()
