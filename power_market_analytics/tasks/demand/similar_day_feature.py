"""The similar-day feature values, scored walking forward and written back.

``scripts/fit_similar_day.py`` walks through history with the selector of
``tasks/demand/similar_day.py``: every ``refit_every_days`` a fit runs at a
cutoff instant on the pairs of the selector's fit window (the 730 days before
it by default, the LightGBM strategies' training window) whose target load was
public by then, and scores the days whose issue time follows the cutoff until
the next one, so no day is scored with weights that saw a load that was not
yet public. The chosen
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
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.frames import AreaHourlyLoad, AreaWeatherForecast
from power_market_analytics.tasks.demand.similar_day import (
    MIN_FIT_PAIRS,
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
_DATE = "datetime64[ns]"


@dataclasses.dataclass(frozen=True)
class WalkForwardScoring:
    """The similar day of every scored day, each chosen by the latest fit before it.

    Attributes
    ----------
    selection : SimilarDaySelection
        One row per scored day.
    fit_cutoff : pandas.Series
        The cutoff of the fit that scored each day, indexed by ``trade_date``: the
        instant the fit ran, before the day's issue time.
    fits : pandas.DataFrame
        One row per fit: ``fit_cutoff``, ``fit_from``, ``fit_through`` (the target
        days it saw), ``n_pairs``, ``n_targets``, ``alpha``, ``beta``, ``fit_rmse``,
        ``n_days_scored``, then ``weight_<part>`` and ``scale_<part>`` for every part.
    cutoffs_without_fit : pandas.DatetimeIndex
        The cutoffs at which fewer than ``MIN_FIT_PAIRS`` public pairs lay inside
        the fit window, so no fit ran and the previous fit served on.
    """

    selection: SimilarDaySelection
    fit_cutoff: pd.Series
    fits: pd.DataFrame
    cutoffs_without_fit: pd.DatetimeIndex = dataclasses.field(
        default_factory=lambda: pd.DatetimeIndex([])
    )


def _fit_row(
    cutoff: pd.Timestamp, weights: SimilarDayWeights, n_days_scored: int
) -> dict[str, object]:
    return {
        "fit_cutoff": cutoff,
        "fit_from": weights.fit_from,
        "fit_through": weights.fit_through,
        "n_pairs": weights.n_pairs,
        "n_targets": weights.n_targets,
        "alpha": weights.alpha,
        "beta": weights.beta,
        "fit_rmse": weights.fit_rmse,
        "n_days_scored": n_days_scored,
        **{f"weight_{p}": w for p, w in zip(SIMILAR_DAY_COMPONENTS, weights.weights, strict=True)},
        **{f"scale_{p}": s for p, s in zip(SIMILAR_DAY_COMPONENTS, weights.scales, strict=True)},
    }


def issue_times(days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The demand task's issue time of each delivery day (09:30 on D-1, naive JST).

    Parameters
    ----------
    days : pandas.DatetimeIndex

    Returns
    -------
    pandas.DatetimeIndex
    """
    return pd.DatetimeIndex(days) + TASK.issue_offset


def score_walk_forward(
    selector: SimilarDaySelector,
    days: Iterable[pd.Timestamp],
    *,
    refit_every_days: int = DEFAULT_REFIT_EVERY_DAYS,
) -> WalkForwardScoring:
    """Score the scorable days among ``days``, refitting the weights as time passes.

    The first fit runs at the first instant a fit is possible (when
    ``MIN_FIT_PAIRS`` pairs were public) and every ``refit_every_days`` after
    it; a fit at cutoff C uses the pairs of the selector's fit window (its
    ``fit_window_days`` before C) whose target load was public by C
    (``SimilarDaySelector.training_pairs``). A cutoff whose window holds fewer
    than ``MIN_FIT_PAIRS`` public pairs (a gap in the targets longer than the
    fit window) makes no fit: the previous fit serves on, and the cutoff is
    listed in ``cutoffs_without_fit``. A day is scored by the latest fit whose
    cutoff is on or before the day's issue time, so nothing the fit saw was
    published after the forecast would have been made. Days whose issue time
    precedes the first cutoff are left out.

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
    first = selector.first_fit_cutoff
    if first is None:
        raise ValueError(
            f"fewer than {MIN_FIT_PAIRS} training pairs are ever public inside the fit window"
        )
    scorable = selector.scorable_days(days)
    issued = issue_times(scorable)
    if scorable.empty or issued.max() < first:
        raise ValueError(f"no day can be scored: the first fit can run at {first}")
    step = pd.Timedelta(days=refit_every_days)
    cutoffs = pd.date_range(first, issued.max(), freq=step)
    frames: list[pd.DataFrame] = []
    fitted: list[tuple[pd.Timestamp, SimilarDayWeights]] = []
    n_served: list[int] = []
    skipped: list[pd.Timestamp] = []
    for k, cutoff in enumerate(cutoffs):
        served = issued >= cutoff
        if k + 1 < len(cutoffs):
            served &= issued < cutoffs[k + 1]
        # The first cutoff has enough pairs by construction; a later one may not.
        if len(selector.training_pairs(cutoff)) >= MIN_FIT_PAIRS:
            fitted.append((cutoff, selector.fit(cutoff)))
            n_served.append(0)
        else:
            skipped.append(cutoff)
            logger.info(
                "score_walk_forward: no fit at {}: fewer than {} pairs public inside the "
                "fit window; the fit of {} serves on",
                cutoff,
                MIN_FIT_PAIRS,
                fitted[-1][0],
            )
        # An empty block (a gap in the forecasts) gives an empty selection.
        selection = selector.select(scorable[served]).df
        n_served[-1] += len(selection)
        if not selection.empty:
            frames.append(selection.assign(fit_cutoff=fitted[-1][0]))
    scored = pd.concat(frames, ignore_index=True)
    fits = [_fit_row(cutoff, weights, n) for (cutoff, weights), n in zip(fitted, n_served)]
    logger.info(
        "score_walk_forward: {} days scored ({}..{}) by {} fits every {} days ({}..{}), "
        "{} cutoffs without a fit",
        len(scored),
        scored["trade_date"].min().date(),
        scored["trade_date"].max().date(),
        len(fits),
        refit_every_days,
        cutoffs[0],
        cutoffs[-1],
        len(skipped),
    )
    return WalkForwardScoring(
        selection=SimilarDaySelection.from_df(scored.drop(columns="fit_cutoff")),
        fit_cutoff=scored.set_index("trade_date")["fit_cutoff"],
        fits=pd.DataFrame(fits),
        cutoffs_without_fit=pd.DatetimeIndex(skipped),
    )


class SimilarDayFeatureRecords(DomainFrame):
    """One scoring run's similar-day feature, shaped for ``pma_ml.similar_day``.

    Per delivery period: the chosen day's hourly load over the period's hour
    halved (``similar_day_demand_kwh``), the chosen day, its lag in days, its
    distance, the candidate count, the cutoff of the fit that chose it (on or
    before the day's issue time) and ``available_at``: the latest of the day's
    forecast availability, that cutoff and the chosen day's load availability
    (its observations are public before its load). Under the default window
    the chosen day is at least 334 days old, so the first two decide.

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
        "similar_day_fit_cutoff": _DATE,
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
        issued = issue_times(pd.DatetimeIndex(df["trade_date"])).to_numpy()
        if (df["similar_day_fit_cutoff"].to_numpy() > issued).any():
            raise ValueError(f"{name}: similar_day_fit_cutoff must not follow the issue time")
        if (df["available_at"] < df["similar_day_fit_cutoff"]).any():
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
        The forecast profiles the days were scored with; their ``available_at``,
        the fit's cutoff and the chosen day's load availability give the rows'.
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
    fit_cutoff = rows["trade_date"].map(scoring.fit_cutoff)
    # The chosen day's load over the period's hour: public before the row can be.
    load_available_at = rows["available_at"]
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
                "similar_day_fit_cutoff": fit_cutoff,
                "available_at": np.maximum(
                    np.maximum(forecast_available_at, fit_cutoff), load_available_at
                ),
                "published_at": pd.Timestamp(published_at),
                "run_id": run_id,
            }
        )
        .astype({"similar_day_fit_cutoff": _DATE, "available_at": _DATE, "published_at": _DATE})
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
          similar_day_fit_cutoff timestamp,
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
        F.col("similar_day_fit_cutoff").cast("timestamp"),
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
