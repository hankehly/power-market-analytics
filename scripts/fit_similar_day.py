"""Score the demand task's similar day walking forward through history, and publish it.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/fit_similar_day.py --area tokyo

The job walks through every delivery day that can be scored. Every
``--refit-every-days`` (default 7, the LightGBM strategies' refit cadence) a
fit of the seven weights of the similar-day distance runs at a cutoff
instant on the (target, candidate) pairs of the ``--fit-window-days`` days
before it (default 730, the LightGBM strategies' training window) whose
target load was public by then (``tasks/demand/similar_day.py``; Park, Song
and Kwon 2020), and scores the days whose 09:30 D-1 issue time follows the
cutoff until the next one, so no day is scored with weights that saw a load
that was not yet public when the forecast would have been made. The chosen
day's hourly load halved per period is written to ``pma_ml.similar_day``
(``tasks/demand/similar_day_feature.py``) with
``available_at`` = the later of the day's forecast availability and its
fit's cutoff. The ``ftr_period_similar_day`` mart passes the rows to Feast
after ``dbt build``; the ``lightgbm_msm_popw_daytype_simday`` preset reads
them. A re-run's rows win by ``published_at`` wherever they overlap.

The run logs every fit's weights (``similar_day_fits.csv``), the selection
of every scored day (``similar_day_selection.csv``), the retrieval check of
every scored day whose load is known (``similar_day_retrieval.csv``) and the
four ``similar_day_*`` metrics over those days, to the MLflow experiment
``similar_day``.
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
    SIMILAR_DAY_FIT_WINDOW_DAYS,
    SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    SimilarDaySelector,
    retrieval_metrics,
)
from power_market_analytics.tasks.demand.similar_day_feature import (
    DEFAULT_REFIT_EVERY_DAYS,
    FEATURE_TABLE,
    MLFLOW_EXPERIMENT,
    build_feature_records,
    publish_feature_records,
    score_walk_forward,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--area", choices=AREA_CODES, default="tokyo", help="dim_area.area_code value."
    )
    parser.add_argument(
        "--refit-every-days",
        type=int,
        default=DEFAULT_REFIT_EVERY_DAYS,
        help="Days between two fits of the weights as the job walks forward.",
    )
    parser.add_argument(
        "--fit-window-days",
        type=int,
        default=SIMILAR_DAY_FIT_WINDOW_DAYS,
        help="Days of target days before its cutoff a fit sees.",
    )
    parser.add_argument(
        "--window-half-width-days",
        type=int,
        default=SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
        help="Half width of the candidate window around D - 364.",
    )
    args = parser.parse_args(argv)
    if args.refit_every_days < 1:
        parser.error(f"--refit-every-days must be >= 1, got {args.refit_every_days}")
    if args.fit_window_days < 1:
        parser.error(f"--fit-window-days must be >= 1, got {args.fit_window_days}")

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
            fit_window_days=args.fit_window_days,
        )
        scoring = score_walk_forward(
            selector,
            weather.forecast.df["trade_date"].unique(),
            refit_every_days=args.refit_every_days,
        )
        records = build_feature_records(
            scoring,
            hourly_load,
            weather.forecast,
            run_id=mlflow_run.info.run_id,
            area_code=args.area,
            published_at=pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None),
        )
        publish_feature_records(records)
        mlflow.set_tag("feature_table", FEATURE_TABLE)
        selection = scoring.selection
        mlflow.log_params(
            {
                "area": args.area,
                "refit_every_days": args.refit_every_days,
                "n_fits": len(scoring.fits),
                "first_fit_cutoff": str(scoring.fits["fit_cutoff"].iloc[0]),
                "last_fit_cutoff": str(scoring.fits["fit_cutoff"].iloc[-1]),
                "n_days_scored": len(selection),
                "first_day_scored": str(selection.df["trade_date"].min().date()),
                "last_day_scored": str(selection.df["trade_date"].max().date()),
                "population_weight_census_year": weather.census_year,
                "n_stations": weather.n_stations,
                # The window, the parts and the last fit's weights.
                **selector.as_params(),
            }
        )
        log_dataframe(scoring.fits, "similar_day_fits.csv")
        log_dataframe(
            selection.df.assign(fit_cutoff=scoring.fit_cutoff.to_numpy()),
            "similar_day_selection.csv",
        )
        # The outcomes do not depend on the weights; the distances are the last fit's.
        retrieval = selector.retrieval(selection)
        log_dataframe(retrieval.df, "similar_day_retrieval.csv")
        if len(retrieval):
            mlflow.log_metrics(
                {
                    key: value
                    for key, value in retrieval_metrics(retrieval).items()
                    if not math.isnan(value)
                }
            )
        run_id = mlflow_run.info.run_id

    logger.info(
        "area={} fits={} every {} days ({}..{}) scored={} days ({} rows, {}..{}) checked={}",
        args.area,
        len(scoring.fits),
        args.refit_every_days,
        scoring.fits["fit_cutoff"].iloc[0],
        scoring.fits["fit_cutoff"].iloc[-1],
        len(selection),
        len(records),
        selection.df["trade_date"].min().date(),
        selection.df["trade_date"].max().date(),
        len(retrieval),
    )
    logger.info("MLflow run: {} (experiment: {})", run_id, MLFLOW_EXPERIMENT)
    logger.info("Feature written to {} (partition run_id={})", FEATURE_TABLE, run_id)


if __name__ == "__main__":
    main()
