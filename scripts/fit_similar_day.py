"""Fit the demand task's similar-day weights, score every day and publish the feature.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/fit_similar_day.py --area tokyo --fit-through 2024-08-16

The seven weights of the similar-day distance are fitted on every (target,
candidate) pair whose target day is on or before ``--fit-through`` and whose
load is known (``tasks/demand/similar_day.py``; Park, Song and Kwon 2020).
The fitted selector then picks the similar day of every delivery day that
can be scored, and the chosen day's hourly load halved per period is written
to ``pma_ml.similar_day`` (``tasks/demand/similar_day_feature.py``) with the
day's forecast availability as ``available_at``. The ``ftr_period_similar_day``
mart passes the rows to Feast after ``dbt build``; the
``lightgbm_msm_popw_daytype_simday`` preset reads them. A refit is a new run
whose rows win by ``published_at`` wherever it scored.

The run logs the fit to the MLflow experiment ``similar_day`` with the
selection of every scorable day (``similar_day_selection.csv``), the
retrieval check of every day whose load is known
(``similar_day_retrieval.csv``, ``in_fit`` marking the fit's days) and, when
days after the fit have a load, the four ``similar_day_*`` metrics over those
out-of-sample days.
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
from power_market_analytics.tasks.demand.similar_day_feature import (
    FEATURE_TABLE,
    MLFLOW_EXPERIMENT,
    build_feature_records,
    publish_feature_records,
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
        hourly_load = load_area_hourly_load(args.area)
        selector = SimilarDaySelector(
            load_day_calendar(),
            weather.forecast,
            observed.weather,
            hourly_load,
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

        selection = selector.select(weather.forecast.df["trade_date"].unique())
        records = build_feature_records(
            selection,
            hourly_load,
            weather.forecast,
            run_id=mlflow_run.info.run_id,
            area_code=args.area,
            published_at=pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None),
        )
        publish_feature_records(records)
        mlflow.set_tag("feature_table", FEATURE_TABLE)
        mlflow.log_params(
            {
                "n_days_scored": len(selection),
                "first_day_scored": str(selection.df["trade_date"].min().date()),
                "last_day_scored": str(selection.df["trade_date"].max().date()),
            }
        )

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
        "area={} fit_through={} pairs={} targets={} rmse={:.4f} scored={} days ({} rows) "
        "checked={} ({} out of sample)",
        args.area,
        fit_through.date(),
        weights.n_pairs,
        weights.n_targets,
        weights.fit_rmse,
        len(selection),
        len(records),
        len(checked),
        len(out_of_sample),
    )
    logger.info("MLflow run: {} (experiment: {})", run_id, MLFLOW_EXPERIMENT)
    logger.info("Feature written to {} (partition run_id={})", FEATURE_TABLE, run_id)


if __name__ == "__main__":
    main()
