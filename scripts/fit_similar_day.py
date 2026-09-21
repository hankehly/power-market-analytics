"""Score the demand task's similar days walking forward through history, and publish them.

Run inside the devcontainer (needs the Spark warehouse and the MLflow
server):

    python scripts/fit_similar_day.py --area tokyo

The job walks through every delivery day that can be scored. Every
``--refit-every-days`` (default 7, the LightGBM strategies' refit cadence) a
fit of the seven weights of the similar-day distance runs at a cutoff
instant on the (target, candidate) pairs of the ``--fit-window-days`` days
before it (default 730, the LightGBM strategies' training window) whose
target load was public by then (``tasks/demand/similar_day.py``; Park, Song
and Kwon 2020), and ranks the days whose 09:30 D-1 issue time follows the
cutoff until the next one, so no day is ranked with weights that saw a load
that was not yet public when the forecast would have been made.

A day's pool is the paper's: D-2 … D-31 and D-335 … D-394, without special
days (``dim_date.is_holiday``) and without a day whose load was not public by
the issue time. The three nearest days' hourly loads, halved per period, and
their inverse-distance weighted mean are written to ``pma_ml.similar_day``
(``tasks/demand/similar_day_feature.py``). A special day whose same holiday
last year lies in the pool's year-ago window, with that day's load public by
the issue time, takes that day instead: rank 1 and the mean carry its load.
``--no-same-holiday`` turns that off and ranks every special day from its pool.
The pool has no flags. The
``ftr_period_similar_day`` mart passes the rows to Feast after ``dbt build``;
the similar-day presets read rank 1. A re-run's rows win by ``published_at``
wherever they share ``available_at``.

The run logs, to the MLflow experiment ``similar_day``: every fit's weights
(``similar_day_fits.csv``); rank 1 of every ranked day
(``similar_day_selection.csv``); every ranked day's ranks with their weights
(``similar_day_ranking.csv``); every special day with last year's day of its
name, whether it took that day and how it was published — empty when it was
neither ranked nor took that day (``similar_day_special_days.csv``); the
retrieval check of every ranked day whose load is known, against D-7 and the
oracle (``similar_day_retrieval.csv``); and the four ``similar_day_*``
metrics over those days.
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
    SIMILAR_DAY_TOP_K,
    SimilarDaySelector,
    retrieval_metrics,
)
from power_market_analytics.tasks.demand.similar_day_feature import (
    DEFAULT_REFIT_EVERY_DAYS,
    FEATURE_TABLE,
    METHOD_SAME_HOLIDAY,
    METHOD_SIMILARITY,
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
        "--no-same-holiday",
        dest="same_holiday",
        action="store_false",
        help=(
            "Rank every special day from its pool instead of letting it take the "
            "same holiday of last year."
        ),
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
            fit_window_days=args.fit_window_days,
        )
        scoring = score_walk_forward(
            selector,
            weather.forecast.df["trade_date"].unique(),
            refit_every_days=args.refit_every_days,
            same_holiday=args.same_holiday,
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
        special = scoring.special_days.df
        n_same_holiday = len(scoring.special_days.same_holiday_days)
        # A special day is ranked only when its pool had a candidate.
        special_ranked = special["trade_date"].isin(selection.df["trade_date"])
        # Every published day: the ranked days and the same-holiday days.
        published_days = pd.DatetimeIndex(records.df["trade_date"].unique()).sort_values()
        mlflow.log_params(
            {
                "area": args.area,
                "refit_every_days": args.refit_every_days,
                "same_holiday": args.same_holiday,
                "n_fits": len(scoring.fits),
                "n_cutoffs_without_fit": len(scoring.cutoffs_without_fit),
                "first_fit_cutoff": str(scoring.fits["fit_cutoff"].iloc[0]),
                "last_fit_cutoff": str(scoring.fits["fit_cutoff"].iloc[-1]),
                "n_days_scored": len(published_days),
                "first_day_scored": str(published_days[0].date()),
                "last_day_scored": str(published_days[-1].date()),
                "n_days_ranked": len(selection),
                "n_special_days_ranked": int(special_ranked.sum()),
                "n_days_same_holiday": n_same_holiday,
                "similar_day_top_k": SIMILAR_DAY_TOP_K,
                "population_weight_census_year": weather.census_year,
                "n_stations": weather.n_stations,
                # The pool, the parts and the last fit's weights.
                **selector.as_params(),
            }
        )
        log_dataframe(scoring.fits, "similar_day_fits.csv")
        log_dataframe(
            selection.df.assign(fit_cutoff=scoring.fit_cutoff.to_numpy()),
            "similar_day_selection.csv",
        )
        log_dataframe(scoring.ranking.with_weights(), "similar_day_ranking.csv")
        # Null when a special day was neither ranked nor took its reference.
        method = (
            pd.Series(None, index=special.index, dtype="object")
            .mask(special_ranked, METHOD_SIMILARITY)
            .mask(special["takes_reference"], METHOD_SAME_HOLIDAY)
        )
        log_dataframe(special.assign(similar_day_method=method), "similar_day_special_days.csv")
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
        "area={} fits={} every {} days ({}..{}) published={} days ({} ranked, {} same-holiday; "
        "{} rows, {}..{}) checked={}",
        args.area,
        len(scoring.fits),
        args.refit_every_days,
        scoring.fits["fit_cutoff"].iloc[0],
        scoring.fits["fit_cutoff"].iloc[-1],
        len(published_days),
        len(selection),
        n_same_holiday,
        len(records),
        published_days[0].date(),
        published_days[-1].date(),
        len(retrieval),
    )
    logger.info("MLflow run: {} (experiment: {})", run_id, MLFLOW_EXPERIMENT)
    logger.info("Feature written to {} (partition run_id={})", FEATURE_TABLE, run_id)


if __name__ == "__main__":
    main()
