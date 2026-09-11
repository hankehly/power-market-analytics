"""The similar-day feature values, scored walking forward and written back.

``scripts/fit_similar_day.py`` walks through history with the selector of
``tasks/demand/similar_day.py``: every ``refit_every_days`` it refits the
weights on the days before that step and scores the days that follow with
them, so no day is scored with weights that saw its own load. The chosen
day's hourly load halved per period is written to ``pma_ml.similar_day``,
partitioned by the job's MLflow run like the forecast tables, each row
usable from the later of its forecast's availability and its fit's cutoff.
The ``ftr_period_similar_day`` mart passes the rows through to Feast; a
re-run's rows win by ``published_at`` wherever they overlap.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import N_PERIODS
from power_market_analytics.forecasting.publish import (
    create_run_partitioned_table,
    overwrite_run_partitions,
)
from power_market_analytics.spark import get_spark_session
from power_market_analytics.tasks.demand.frames import AreaHourlyLoad, AreaWeatherForecast
from power_market_analytics.tasks.demand.similar_day import (
    PERIODS_PER_HOUR,
    SIMILAR_DAY_COMPONENTS,
    SimilarDaySelection,
    SimilarDaySelector,
    SimilarDayWeights,
)

#: The MLflow experiment of the fit-and-score runs, named after the feature.
MLFLOW_EXPERIMENT = "similar_day"
#: Where the feature values are written; the ``run_id`` partition is the job's MLflow run.
FEATURE_TABLE = "pma_ml.similar_day"
#: Days between two fits of the walk-forward job: the LightGBM strategies' refit cadence.
DEFAULT_REFIT_EVERY_DAYS = 7
#: The gap between a fit's last target day and the first day it may score: the
#: day's issue time (09:30 on D-1) must follow the fit's cutoff (00:00 after
#: its last target day), so the fit runs through D-2 at the latest.
FIT_LEAD_DAYS = 2
_DATE = "datetime64[ns]"


@dataclasses.dataclass(frozen=True)
class WalkForwardScoring:
    """The similar day of every scored day, each chosen by the latest fit before it.

    Attributes
    ----------
    selection : SimilarDaySelection
        One row per scored day.
    fit_through : pandas.Series
        The last target day of the fit that scored each day, indexed by ``trade_date``.
    fits : pandas.DataFrame
        One row per fit: ``fit_through``, ``fit_from``, ``n_pairs``, ``n_targets``,
        ``alpha``, ``beta``, ``fit_rmse``, ``n_days_scored``, then ``weight_<part>`` and
        ``scale_<part>`` for every part.
    """

    selection: SimilarDaySelection
    fit_through: pd.Series
    fits: pd.DataFrame


def _fit_row(weights: SimilarDayWeights, n_days_scored: int) -> dict[str, object]:
    return {
        "fit_through": weights.fit_through,
        "fit_from": weights.fit_from,
        "n_pairs": weights.n_pairs,
        "n_targets": weights.n_targets,
        "alpha": weights.alpha,
        "beta": weights.beta,
        "fit_rmse": weights.fit_rmse,
        "n_days_scored": n_days_scored,
        **{f"weight_{p}": w for p, w in zip(SIMILAR_DAY_COMPONENTS, weights.weights, strict=True)},
        **{f"scale_{p}": s for p, s in zip(SIMILAR_DAY_COMPONENTS, weights.scales, strict=True)},
    }


def score_walk_forward(
    selector: SimilarDaySelector,
    days: Iterable[pd.Timestamp],
    *,
    refit_every_days: int = DEFAULT_REFIT_EVERY_DAYS,
) -> WalkForwardScoring:
    """Score the scorable days among ``days``, refitting the weights as time passes.

    The first fit runs through the first day a fit is possible (the first
    scorable day with a known load); every ``refit_every_days`` after it a new
    fit runs through that day. A day D is scored by the latest fit whose last
    target day is on or before D − ``FIT_LEAD_DAYS``, so the fit's cutoff
    precedes D's issue time. Days before the first fit can score are left out.

    Parameters
    ----------
    selector : SimilarDaySelector
        Over the area's calendar, forecasts, observations and loads; fitted
        repeatedly here, and left with the last fit.
    days : iterable of pandas.Timestamp
        Candidate delivery days, e.g. every day with a forecast.
    refit_every_days : int, optional
        Days between two fits.

    Returns
    -------
    WalkForwardScoring

    Raises
    ------
    ValueError
        If ``refit_every_days`` is below one, no fit is possible, or no day
        can be scored.
    """
    if refit_every_days < 1:
        raise ValueError(f"refit_every_days must be >= 1, got {refit_every_days}")
    first_fit = selector.first_fit_day
    if first_fit is None:
        raise ValueError("no training pairs: no scorable day has a known load")
    scorable = selector.scorable_days(days)
    lead = pd.Timedelta(days=FIT_LEAD_DAYS)
    step = pd.Timedelta(days=refit_every_days)
    if scorable.empty or scorable.max() < first_fit + lead:
        raise ValueError(f"no day can be scored: the first fit runs through {first_fit.date()}")
    frames: list[pd.DataFrame] = []
    fits: list[dict[str, object]] = []
    fit_through = first_fit
    while fit_through + lead <= scorable.max():
        block = scorable[(scorable >= fit_through + lead) & (scorable < fit_through + step + lead)]
        weights = selector.fit(fit_through)
        # An empty block (a gap in the forecasts) gives an empty selection.
        selection = selector.select(block).df
        fits.append(_fit_row(weights, len(selection)))
        if not selection.empty:
            frames.append(selection.assign(fit_through=fit_through))
        fit_through = fit_through + step
    scored = pd.concat(frames, ignore_index=True)
    logger.info(
        "score_walk_forward: {} days scored ({}..{}) by {} fits every {} days ({}..{})",
        len(scored),
        scored["trade_date"].min().date(),
        scored["trade_date"].max().date(),
        len(fits),
        refit_every_days,
        first_fit.date(),
        (fit_through - step).date(),
    )
    return WalkForwardScoring(
        selection=SimilarDaySelection.from_df(scored.drop(columns="fit_through")),
        fit_through=scored.set_index("trade_date")["fit_through"],
        fits=pd.DataFrame(fits),
    )


class SimilarDayFeatureRecords(DomainFrame):
    """One scoring run's similar-day feature, shaped for ``pma_ml.similar_day``.

    Per delivery period: the chosen day's hourly load over the period's hour
    halved (``similar_day_demand_kwh``), the chosen day, its lag in days, its
    distance, the candidate count, the last target day of the fit that chose
    it (at least ``FIT_LEAD_DAYS`` before the delivery day) and
    ``available_at``: the later of the day's forecast availability and the
    fit's cutoff, the midnight after its last target day. The candidates are
    at least 334 days older.

    Grain: (area_code, trade_date, time_code); one run per frame.
    """

    schema = {
        "area_code": "object",
        "trade_date": _DATE,
        "time_code": "int64",
        "similar_day_demand_kwh": "float64",
        "similar_day_reference_date": _DATE,
        "similar_day_reference_lag_days": "int64",
        "similar_day_distance": "float64",
        "similar_day_n_candidates": "int64",
        "similar_day_fit_through": _DATE,
        "available_at": _DATE,
        "published_at": _DATE,
        "run_id": "object",
    }
    keys = ["area_code", "trade_date", "time_code"]
    non_null_cols = [col for col in schema if col not in ("area_code", "trade_date", "time_code")]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        name = cls.__name__
        if not df["time_code"].between(1, N_PERIODS).all():
            raise ValueError(f"{name}: time_code outside 1..{N_PERIODS}")
        if (df["similar_day_reference_date"] >= df["trade_date"]).any():
            raise ValueError(f"{name}: similar_day_reference_date must precede trade_date")
        lag = (df["trade_date"] - df["similar_day_reference_date"]).dt.days
        if (lag != df["similar_day_reference_lag_days"]).any():
            raise ValueError(f"{name}: similar_day_reference_lag_days must equal the date gap")
        if (df["similar_day_demand_kwh"] <= 0).any():
            raise ValueError(f"{name}: similar_day_demand_kwh must be positive")
        latest_fit = df["trade_date"] - pd.Timedelta(days=FIT_LEAD_DAYS)
        if (df["similar_day_fit_through"] > latest_fit).any():
            raise ValueError(
                f"{name}: similar_day_fit_through must be at least {FIT_LEAD_DAYS} days "
                "before trade_date"
            )
        cutoff = df["similar_day_fit_through"] + pd.Timedelta(days=1)
        if (df["available_at"] < cutoff).any():
            raise ValueError(f"{name}: available_at must not precede the fit's cutoff")
        if df["run_id"].nunique() != 1:
            raise ValueError(f"{name}: one run per frame, got {df['run_id'].nunique()}")


def build_feature_records(
    scoring: WalkForwardScoring,
    hourly_load: AreaHourlyLoad,
    forecast: AreaWeatherForecast,
    *,
    run_id: str,
    area_code: str,
    published_at: pd.Timestamp,
) -> SimilarDayFeatureRecords:
    """Shape a walk-forward scoring into the feature rows of every period of its days.

    Parameters
    ----------
    scoring : WalkForwardScoring
        The similar day of every scored day and the fit that chose it.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load the chosen days' loads come from.
    forecast : AreaWeatherForecast
        The forecast profiles the days were scored with; their ``available_at``
        and the fit's cutoff give the rows' availability.
    run_id : str
        The job's MLflow run id.
    area_code : str
        dim_area.area_code value the feature was scored for.
    published_at : pandas.Timestamp
        When the rows are written (naive JST).

    Returns
    -------
    SimilarDayFeatureRecords
        48 rows per scored day, sorted by day and period.

    Raises
    ------
    ValueError
        If the scoring is empty, a chosen day lacks an hourly load, or a day
        has no forecast availability.
    """
    selection = scoring.selection
    if len(selection) == 0:
        raise ValueError("no scored day to publish")
    days = selection.df.merge(
        pd.DataFrame({"time_code": np.arange(1, N_PERIODS + 1, dtype="int64")}), how="cross"
    )
    days["hour_ending"] = (days["time_code"] + 1) // 2
    load = hourly_load.df.rename(columns={"load_date": "reference_date"})
    rows = days.merge(
        load, how="left", on=["reference_date", "hour_ending"], validate="many_to_one"
    )
    missing = rows["demand_kwh"].isna()
    if missing.any():
        first = rows.loc[missing].iloc[0]
        raise ValueError(
            f"{int(missing.sum())} period(s) have no load on their similar day, e.g. "
            f"{first['trade_date'].date()} time_code {first['time_code']} "
            f"(similar day {first['reference_date'].date()})"
        )
    availability = forecast.df.groupby("trade_date")["available_at"].max()
    forecast_available_at = rows["trade_date"].map(availability)
    if forecast_available_at.isna().any():
        unknown = sorted(
            d.date() for d in rows.loc[forecast_available_at.isna(), "trade_date"].unique()
        )
        raise ValueError(f"{len(unknown)} day(s) have no forecast availability, e.g. {unknown[0]}")
    fit_through = rows["trade_date"].map(scoring.fit_through)
    fit_cutoff = fit_through + pd.Timedelta(days=1)
    df = (
        pd.DataFrame(
            {
                "area_code": area_code,
                "trade_date": rows["trade_date"],
                "time_code": rows["time_code"],
                "similar_day_demand_kwh": rows["demand_kwh"].to_numpy(dtype="float64")
                / PERIODS_PER_HOUR,
                "similar_day_reference_date": rows["reference_date"],
                "similar_day_reference_lag_days": rows["reference_lag_days"],
                "similar_day_distance": rows["distance"],
                "similar_day_n_candidates": rows["n_candidates"],
                "similar_day_fit_through": fit_through,
                "available_at": np.maximum(forecast_available_at, fit_cutoff),
                "published_at": pd.Timestamp(published_at),
                "run_id": run_id,
            }
        )
        .astype({"similar_day_fit_through": _DATE, "available_at": _DATE, "published_at": _DATE})
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )
    return SimilarDayFeatureRecords.from_df(df)


def publish_feature_records(
    records: SimilarDayFeatureRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's feature rows to ``FEATURE_TABLE``.

    The table (parquet, partitioned by ``run_id``) is created on first use and
    only the run's partition is overwritten, as for the forecast tables.

    Parameters
    ----------
    records : SimilarDayFeatureRecords
        The run's rows.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.spark.get_spark_session`.

    Returns
    -------
    int
        Number of rows written.
    """
    spark = spark if spark is not None else get_spark_session()
    create_run_partitioned_table(
        spark,
        FEATURE_TABLE,
        """area_code string,
          trade_date date,
          time_code int,
          similar_day_demand_kwh double,
          similar_day_reference_date date,
          similar_day_reference_lag_days int,
          similar_day_distance double,
          similar_day_n_candidates int,
          similar_day_fit_through date,
          available_at timestamp,
          published_at timestamp""",
    )
    sdf = spark.createDataFrame(records.df).select(
        F.col("area_code").cast("string"),
        F.col("trade_date").cast("date"),
        F.col("time_code").cast("int"),
        F.col("similar_day_demand_kwh").cast("double"),
        F.col("similar_day_reference_date").cast("date"),
        F.col("similar_day_reference_lag_days").cast("int"),
        F.col("similar_day_distance").cast("double"),
        F.col("similar_day_n_candidates").cast("int"),
        F.col("similar_day_fit_through").cast("date"),
        F.col("available_at").cast("timestamp"),
        F.col("published_at").cast("timestamp"),
        F.col("run_id").cast("string"),
    )
    overwrite_run_partitions(spark, FEATURE_TABLE, sdf)
    logger.info(
        "Published {} rows to {} (run_id={})",
        len(records),
        FEATURE_TABLE,
        records.df["run_id"].iloc[0],
    )
    return len(records)
