"""Fit the demand task's similar-day weights and publish them as a parameter vintage.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/fit_similar_day.py --area tokyo --fit-through 2024-08-16

The seven weights of the similar-day distance are fitted on every (target,
candidate) pair whose target day is on or before ``--fit-through`` and whose
load is known (``tasks/demand/similar_day.py``; Park, Song and Kwon 2020).
The fit is logged to the MLflow experiment ``similar_day`` and written as one
row of ``pma_ml.similar_day_parameters``; ``dbt build`` then scores every
delivery day with it in ``ftr_period_similar_day``, the mart the
``lightgbm_msm_popw_daytype_simday`` preset reads. A refit is a new row: the
mart keeps every vintage and the as-of join picks the newest one available
at each row's issue time.

The run also logs the selection of every scorable day
(``similar_day_selection.csv``), the retrieval check of every day whose load
is known (``similar_day_retrieval.csv``, ``in_fit`` marking the fit's days)
and, when days after the fit have a load, the four ``similar_day_*`` metrics
over those out-of-sample days.
"""

import argparse
import math

import mlflow
import pandas as pd
from loguru import logger

from power_market_analytics.common.tracking import log_dataframe, task_run
from power_market_analytics.tasks.demand.datasets import (
    AREA_CODES,
    load_area_hourly_load,
    load_area_observed_weather_population_weighted,
    load_area_weather_forecast_population_weighted,
    load_day_calendar,
)
from power_market_analytics.tasks.demand.similar_day import (
    SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    SimilarDayRetrieval,
    SimilarDaySelector,
    retrieval_metrics,
)
from power_market_analytics.tasks.demand.similar_day_parameters import (
    MLFLOW_EXPERIMENT,
    PARAMETERS_TABLE,
    build_parameter_records,
    publish_parameter_records,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--area", choices=AREA_CODES, default="tokyo", help="dim_area.area_code value."
    )
    parser.add_argument(
        "--fit-through",
        type=pd.Timestamp,
        default=None,
        help="Last target day of the fit (YYYY-MM-DD); default: the last day with an hourly load.",
    )
    parser.add_argument(
        "--window-half-width-days",
        type=int,
        default=SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
        help="Half width of the candidate window around D - 364.",
    )
    args = parser.parse_args(argv)

    with task_run(
        MLFLOW_EXPERIMENT, run_name=f"{MLFLOW_EXPERIMENT}-{args.area}", tags={"area": args.area}
    ) as mlflow_run:
        weather = load_area_weather_forecast_population_weighted(args.area)
        observed = load_area_observed_weather_population_weighted(
            args.area, census_year=weather.census_year
        )
        selector = SimilarDaySelector(
            load_day_calendar(),
            weather.forecast,
            observed.weather,
            load_area_hourly_load(args.area),
            half_width_days=args.window_half_width_days,
        )
        fit_through = selector.hourly_load_span[1] if args.fit_through is None else args.fit_through
        weights = selector.fit(fit_through)
        mlflow.log_params(
            {
                "area": args.area,
                "fit_through": str(fit_through.date()),
                "population_weight_census_year": weather.census_year,
                "n_stations": weather.n_stations,
                **selector.as_params(),
            }
        )
        records = build_parameter_records(
            weights,
            run_id=mlflow_run.info.run_id,
            area_code=args.area,
            center_lag_days=selector.center_lag_days,
            window_half_width_days=selector.half_width_days,
            census_year=weather.census_year,
            published_at=pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None),
        )
        publish_parameter_records(records)
        mlflow.set_tag("parameters_table", PARAMETERS_TABLE)

        selection = selector.select(weather.forecast.df["trade_date"].unique())
        retrieval = selector.retrieval(selection)
        checked = retrieval.df.assign(in_fit=retrieval.df["trade_date"] <= fit_through)
        log_dataframe(selection.df, "similar_day_selection.csv")
        log_dataframe(checked, "similar_day_retrieval.csv")
        out_of_sample = checked[~checked["in_fit"]].drop(columns="in_fit")
        if not out_of_sample.empty:
            mlflow.log_metrics(
                {
                    key: value
                    for key, value in retrieval_metrics(
                        SimilarDayRetrieval.from_df(out_of_sample)
                    ).items()
                    if not math.isnan(value)
                }
            )
        run_id = mlflow_run.info.run_id

    logger.info(
        "area={} fit_through={} pairs={} targets={} rmse={:.4f} selected={} checked={} ({} out of sample)",
        args.area,
        fit_through.date(),
        weights.n_pairs,
        weights.n_targets,
        weights.fit_rmse,
        len(selection),
        len(checked),
        len(out_of_sample),
    )
    logger.info("MLflow run: {} (experiment: {})", run_id, MLFLOW_EXPERIMENT)
    logger.info("Parameters written to {} (partition run_id={})", PARAMETERS_TABLE, run_id)


if __name__ == "__main__":
    main()
