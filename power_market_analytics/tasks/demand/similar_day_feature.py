"""The similar-day features, scored walking forward and written back.

``scripts/fit_similar_day.py`` walks through history with the selector of
``tasks/demand/similar_day.py``: every ``refit_every_days`` a fit runs at a
cutoff instant on the pairs of the selector's fit window (the 730 days before
it by default, the LightGBM strategies' training window) whose target load was
public by then, and ranks the days whose issue time follows the cutoff until
the next one, so no day is ranked with weights that saw a load that was not
yet public. A day's pool is the paper's (Park, Song and Kwon 2020): the 30
recent days and 60 days one year back.

Four features per period are written to ``pma_ml.similar_day``, partitioned by
the job's MLflow run like the forecast tables: the hourly loads of the three
nearest days, halved per period, and their inverse-distance weighted mean. A
special day (``dim_date.is_holiday``) whose same holiday last year lies in the
pool's year-ago window, with its load public by the issue time, takes that day
instead: rank 1 and the mean carry its load, and the rest is null. A ranked
day's row is usable from the latest of its forecast's availability, its fit's
cutoff and its ranked days' load availability; a same-holiday day's row from
its reference's load availability. Every row is usable by its day's issue time.

The ``ftr_period_similar_day`` mart passes the rows through to Feast. For each
period Feast takes the row with the newest ``available_at`` at or before the
issue time, and the newest ``published_at`` only among rows tied on it. So a
re-run replaces an older run's row only where the two rows share
``available_at``. A same-holiday row, public about a year before its day, never
replaces an older run's ranked row. A re-run that scores a day another way (a
different method or fit cutoff) therefore needs the older run's partition
dropped; the rollout drops the whole table.
Design: docs/superpowers/specs/2026-09-14-similar-day-top-k-design.md.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Final

import numpy as np
import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.spark import get_spark_session
from power_market_analytics.forecasting.frames import N_PERIODS
from power_market_analytics.forecasting.publish import (
    create_run_partitioned_table,
    overwrite_run_partitions,
)
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.frames import AreaHourlyLoad, AreaWeatherForecast
from power_market_analytics.tasks.demand.similar_day import (
    MIN_FIT_PAIRS,
    PERIODS_PER_HOUR,
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_TOP_K,
    SimilarDayRanking,
    SimilarDaySelection,
    SimilarDaySelector,
    SimilarDayWeights,
    SpecialDayReferences,
)

#: The MLflow experiment of the fit-and-score runs, named after the feature.
MLFLOW_EXPERIMENT = "similar_day"
#: Where the feature values are written; the ``run_id`` partition is the job's MLflow run.
FEATURE_TABLE = "pma_ml.similar_day"
#: Days between two fits of the walk-forward job: the LightGBM strategies' refit cadence.
DEFAULT_REFIT_EVERY_DAYS = 7
#: The ranks that become columns: 1 .. SIMILAR_DAY_TOP_K.
RANKS: tuple[int, ...] = tuple(range(1, SIMILAR_DAY_TOP_K + 1))
#: Each rank's hourly load halved per period (kWh per 30-minute period).
RANK_LOAD_COLS: tuple[str, ...] = tuple(f"similar_day_rank{r}_demand_kwh" for r in RANKS)
#: The inverse-distance weighted mean of the rank loads (kWh per 30-minute period).
WEIGHTED_MEAN_COL = "wavg_similar_day_top3_demand_kwh"
#: Each rank's day.
RANK_DATE_COLS: tuple[str, ...] = tuple(f"similar_day_rank{r}_reference_date" for r in RANKS)
#: Each rank's distance under the fit that ranked it.
RANK_DISTANCE_COLS: tuple[str, ...] = tuple(f"similar_day_rank{r}_distance" for r in RANKS)
#: The four feature columns, in table order.
FEATURE_COLS: tuple[str, ...] = (*RANK_LOAD_COLS, WEIGHTED_MEAN_COL)
#: ``similar_day_method`` of a day ranked from its pool.
METHOD_SIMILARITY = "similarity"
#: ``similar_day_method`` of a special day that takes the same holiday last year.
METHOD_SAME_HOLIDAY = "same_holiday"
METHODS: tuple[str, ...] = (METHOD_SIMILARITY, METHOD_SAME_HOLIDAY)
#: The same columns for the pool variant, which ranks every day from its pool and
#: never takes the same holiday last year (expression parameter holidays=similarity).
#: They equal the columns above on every day but a same-holiday one.
POOL_RANK_LOAD_COLS: tuple[str, ...] = tuple(f"similar_day_pool_rank{r}_demand_kwh" for r in RANKS)
POOL_WEIGHTED_MEAN_COL = "wavg_similar_day_pool_top3_demand_kwh"
POOL_RANK_DATE_COLS: tuple[str, ...] = tuple(
    f"similar_day_pool_rank{r}_reference_date" for r in RANKS
)
POOL_RANK_DISTANCE_COLS: tuple[str, ...] = tuple(
    f"similar_day_pool_rank{r}_distance" for r in RANKS
)
POOL_N_CANDIDATES_COL = "similar_day_pool_n_candidates"
#: The pool always has the cutoff of the fit that ranked the day; the base column
#: is null where the same-holiday reference replaced the ranking.
POOL_FIT_CUTOFF_COL = "similar_day_pool_fit_cutoff"
POOL_FEATURE_COLS: tuple[str, ...] = (*POOL_RANK_LOAD_COLS, POOL_WEIGHTED_MEAN_COL)
#: base column -> its pool twin, for the frames that build one from the other.
POOL_OF: dict[str, str] = {
    **dict(zip(RANK_LOAD_COLS, POOL_RANK_LOAD_COLS, strict=True)),
    WEIGHTED_MEAN_COL: POOL_WEIGHTED_MEAN_COL,
    **dict(zip(RANK_DATE_COLS, POOL_RANK_DATE_COLS, strict=True)),
    **dict(zip(RANK_DISTANCE_COLS, POOL_RANK_DISTANCE_COLS, strict=True)),
    "similar_day_n_candidates": POOL_N_CANDIDATES_COL,
    "similar_day_fit_cutoff": POOL_FIT_CUTOFF_COL,
}

#: The columns only a ranked day fills.
_RANKED_ONLY_COLS: tuple[str, ...] = (
    *RANK_LOAD_COLS[1:],
    *RANK_DATE_COLS[1:],
    *RANK_DISTANCE_COLS,
    "similar_day_n_candidates",
    "similar_day_fit_cutoff",
)
#: The two ends of the weighted mean's range allow this relative slack.
_WEIGHTED_MEAN_TOLERANCE = 1e-9
_DATE: Final = "datetime64[ns]"


@dataclasses.dataclass(frozen=True)
class WalkForwardScoring:
    """The similar days of every scored day, each ranked by the latest fit before it.

    Attributes
    ----------
    selection : SimilarDaySelection
        Rank 1 of every ranked day: any scored day without a same-holiday reference.
    ranking : SimilarDayRanking
        The up to ``SIMILAR_DAY_TOP_K`` nearest pool days of every ranked day.
    special_days : SpecialDayReferences
        Every special day of the scored span (issue time on or after the first
        cutoff), with its same-holiday reference or none.
    fit_cutoff : pandas.Series
        The cutoff of the fit that ranked each ranked day, indexed by ``trade_date``:
        the instant the fit ran, before the day's issue time.
    fits : pandas.DataFrame
        One row per fit: ``fit_cutoff``, ``fit_from``, ``fit_through`` (the target
        days it saw), ``n_pairs``, ``n_targets``, ``alpha``, ``beta``, ``fit_rmse``,
        ``n_days_scored`` (the ranked days it served), then ``weight_<part>`` and
        ``scale_<part>`` for every part.
    cutoffs_without_fit : pandas.DatetimeIndex
        The cutoffs at which fewer than ``MIN_FIT_PAIRS`` public pairs lay inside
        the fit window, so no fit ran and the previous fit served on.
    """

    selection: SimilarDaySelection
    ranking: SimilarDayRanking
    special_days: SpecialDayReferences
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

    The scored span is every scorable day whose issue time is on or after the
    first cutoff, each ranked from its pool to ``SIMILAR_DAY_TOP_K`` days. That
    depth is a constant, not an argument, because the table's column names carry
    it. Its special days that take the same holiday last year
    (``SimilarDaySelector.special_day_references``: in the year-ago window, with
    its load public by the issue time) are ranked too, and the reference is what
    the base columns use instead of the ranking.

    Every scorable day is ranked, special or not, because both variants of the
    feature are published: the pool columns take the ranking on every day, and
    the base columns take it everywhere but on a day that has a same-holiday
    reference. Which days those are is on
    ``WalkForwardScoring.special_days.same_holiday_days``. Ranking the special
    days as well moves nothing else: the weight fits never see one as a target
    (``SimilarDaySelector.training_pairs``) and a pair's distance is a function
    of that pair alone.

    The first fit runs at the first instant a fit is possible (when
    ``MIN_FIT_PAIRS`` pairs were public) and every ``refit_every_days`` after
    it; a fit at cutoff C uses the pairs of the selector's fit window (its
    ``fit_window_days`` before C) whose target load was public by C
    (``SimilarDaySelector.training_pairs``). A cutoff whose window holds fewer
    than ``MIN_FIT_PAIRS`` public pairs (a gap in the targets longer than the
    fit window) makes no fit: the previous fit serves on, and the cutoff is
    listed in ``cutoffs_without_fit``. A day is ranked by the latest fit whose
    cutoff is on or before the day's issue time, so nothing the fit saw was
    published after the forecast would have been made.

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
        If ``refit_every_days`` is below one, no fit is possible, or no day can
        be scored.
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
    span = scorable[issued >= first]
    special = selector.special_day_references(span)
    ranked_days = span
    ranked_issued = issue_times(ranked_days)
    step = pd.Timedelta(days=refit_every_days)
    cutoffs = pd.date_range(first, issued.max(), freq=step)
    selections: list[pd.DataFrame] = []
    rankings: list[pd.DataFrame] = []
    fitted: list[tuple[pd.Timestamp, SimilarDayWeights]] = []
    n_served: list[int] = []
    skipped: list[pd.Timestamp] = []
    for k, cutoff in enumerate(cutoffs):
        served = ranked_issued >= cutoff
        if k + 1 < len(cutoffs):
            served &= ranked_issued < cutoffs[k + 1]
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
        # An empty block (a gap in the forecasts) gives empty frames.
        selection, ranking = selector.select_and_rank(ranked_days[served], SIMILAR_DAY_TOP_K)
        n_served[-1] += len(selection)
        if len(selection):
            selections.append(selection.df.assign(fit_cutoff=fitted[-1][0]))
            rankings.append(ranking.df)
    scored = (
        pd.concat(selections, ignore_index=True)
        if selections
        else SimilarDaySelection.empty_df().assign(fit_cutoff=pd.Series(dtype=_DATE))
    )
    ranked = pd.concat(rankings, ignore_index=True) if rankings else SimilarDayRanking.empty_df()
    fits = [_fit_row(cutoff, weights, n) for (cutoff, weights), n in zip(fitted, n_served)]
    logger.info(
        "score_walk_forward: {} days ranked ({}..{}) and {} same-holiday days by {} fits "
        "every {} days ({}..{}), {} cutoffs without a fit",
        len(scored),
        scored["trade_date"].min(),
        scored["trade_date"].max(),
        len(special.same_holiday_days),
        len(fits),
        refit_every_days,
        cutoffs[0],
        cutoffs[-1],
        len(skipped),
    )
    return WalkForwardScoring(
        selection=SimilarDaySelection.from_df(scored.drop(columns="fit_cutoff")),
        ranking=SimilarDayRanking.from_df(ranked),
        special_days=special,
        fit_cutoff=scored.set_index("trade_date")["fit_cutoff"],
        fits=pd.DataFrame(fits),
        cutoffs_without_fit=pd.DatetimeIndex(skipped),
    )


class SimilarDayFeatureRecords(DomainFrame):
    """One scoring run's similar-day features, shaped for ``pma_ml.similar_day``.

    Per delivery period, on a ranked day (``similar_day_method = 'similarity'``):
    the hourly loads of its up to three nearest pool days over the period's hour,
    halved (``similar_day_rank<r>_demand_kwh``); their inverse-distance weighted
    mean (``wavg_similar_day_top3_demand_kwh``); each rank's day and distance;
    the pool's size; and the cutoff of the fit that ranked the day (on or before
    its issue time). A rank beyond the pool's size is null. ``available_at`` is
    the latest of the day's forecast availability, that cutoff and its ranked
    days' load availability, and never follows the day's issue time.

    On a special day that takes the same holiday last year
    (``similar_day_method = 'same_holiday'``): rank 1 and the mean both hold that
    day's load, halved; rank 1's day is that day; ranks 2 and 3, every distance,
    the pool's size and the fit cutoff are null. ``available_at`` is its
    reference's load availability, which also never follows the issue time.

    ``similar_day_n_candidates`` is float64 so it can be null; it holds whole
    numbers.

    Grain: (area_code, trade_date, time_code); one run per frame.
    """

    schema = {
        "area_code": "object",
        "trade_date": _DATE,
        "time_code": "int64",
        **{col: "float64" for col in FEATURE_COLS},
        **{col: _DATE for col in RANK_DATE_COLS},
        **{col: "float64" for col in RANK_DISTANCE_COLS},
        "similar_day_n_candidates": "float64",
        **{col: "float64" for col in POOL_FEATURE_COLS},
        **{col: _DATE for col in POOL_RANK_DATE_COLS},
        **{col: "float64" for col in POOL_RANK_DISTANCE_COLS},
        POOL_N_CANDIDATES_COL: "float64",
        POOL_FIT_CUTOFF_COL: _DATE,
        "similar_day_fit_cutoff": _DATE,
        "similar_day_method": "object",
        "available_at": _DATE,
        "published_at": _DATE,
        "run_id": "object",
    }
    keys = ["area_code", "trade_date", "time_code"]
    non_null_cols = [
        RANK_LOAD_COLS[0],
        WEIGHTED_MEAN_COL,
        RANK_DATE_COLS[0],
        "similar_day_method",
        "available_at",
        "published_at",
        "run_id",
    ]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        name = cls.__name__
        if not df["time_code"].between(1, N_PERIODS).all():
            raise ValueError(f"{name}: time_code outside 1..{N_PERIODS}")
        for col in (*RANK_DATE_COLS, *POOL_RANK_DATE_COLS):
            if (df[col] >= df["trade_date"]).any():
                raise ValueError(f"{name}: {col} must precede trade_date")
        for col in (*FEATURE_COLS, *POOL_FEATURE_COLS):
            if (df[col] <= 0).any():
                raise ValueError(f"{name}: {col} must be positive")
        for load, date in zip(
            (*RANK_LOAD_COLS, *POOL_RANK_LOAD_COLS),
            (*RANK_DATE_COLS, *POOL_RANK_DATE_COLS),
            strict=True,
        ):
            if (df[load].isna() != df[date].isna()).any():
                raise ValueError(f"{name}: {date} must be present exactly where {load} is")
        issued = issue_times(pd.DatetimeIndex(df["trade_date"])).to_numpy()
        if (df["similar_day_fit_cutoff"].to_numpy() > issued).any():
            raise ValueError(f"{name}: similar_day_fit_cutoff must not follow the issue time")
        if (df["available_at"] < df["similar_day_fit_cutoff"]).any():
            raise ValueError(f"{name}: available_at must not precede the fit's cutoff")
        # A row published after its issue time is one Feast never serves.
        if (df["available_at"].to_numpy() > issued).any():
            raise ValueError(f"{name}: available_at must not follow the issue time")
        if df["run_id"].nunique() != 1:
            raise ValueError(f"{name}: one run per frame, got {df['run_id'].nunique()}")
        if not df["similar_day_method"].isin(METHODS).all():
            raise ValueError(f"{name}: similar_day_method must be one of {METHODS}")
        cls._validate_same_holiday(df[df["similar_day_method"] == METHOD_SAME_HOLIDAY])
        cls._validate_similarity(df[df["similar_day_method"] == METHOD_SIMILARITY])
        # The pool columns are a ranked day's, whatever the base did, wherever the
        # pool reached: drop the base columns before renaming onto their names.
        as_ranked = df.drop(columns=list(POOL_OF)).rename(
            columns={pool: base for base, pool in POOL_OF.items()}
        )
        cls._validate_similarity(as_ranked[as_ranked[RANK_LOAD_COLS[0]].notna()])
        # Where the override never fired the two variants are the same ranking.
        ranked_rows = df["similar_day_method"] == METHOD_SIMILARITY
        for base_col, pool_col in POOL_OF.items():
            base_values = df.loc[ranked_rows, base_col]
            pool_values = df.loc[ranked_rows, pool_col]
            agree = (base_values.isna() & pool_values.isna()) | (
                base_values.to_numpy() == pool_values.to_numpy()
            )
            if not agree.all():
                raise ValueError(
                    f"{name}: {pool_col} must equal {base_col} on a {METHOD_SIMILARITY} row"
                )

    @classmethod
    def _validate_same_holiday(cls, df: pd.DataFrame) -> None:
        """A same-holiday row carries rank 1 alone, and its mean is rank 1."""
        if (
            df[list(_RANKED_ONLY_COLS)].notna().to_numpy().any()
            or (df[WEIGHTED_MEAN_COL] != df[RANK_LOAD_COLS[0]]).any()
        ):
            raise ValueError(
                f"{cls.__name__}: a same_holiday row carries rank 1 alone, with "
                f"{WEIGHTED_MEAN_COL} equal to it"
            )

    @classmethod
    def _validate_similarity(cls, df: pd.DataFrame) -> None:
        """A ranked row's ranks run from 1 with no gap, as far as its pool reaches,
        nearest first, on distinct days, and its mean lies within their loads."""
        name = cls.__name__
        needed = [RANK_DISTANCE_COLS[0], "similar_day_n_candidates", "similar_day_fit_cutoff"]
        if df[needed].isna().to_numpy().any():
            raise ValueError(f"{name}: a similarity row needs {needed}")
        dates = df[list(RANK_DATE_COLS)].to_numpy()
        present = df[list(RANK_DATE_COLS)].notna().to_numpy()
        if (df[list(RANK_DISTANCE_COLS)].notna().to_numpy() != present).any():
            raise ValueError(
                f"{name}: on a similarity row a rank's distance must be present exactly "
                "where its reference_date is"
            )
        if (~present[:, :-1] & present[:, 1:]).any():
            raise ValueError(f"{name}: a null rank must be followed by null ranks only")
        n_candidates = df["similar_day_n_candidates"].to_numpy()
        if (n_candidates != np.floor(n_candidates)).any():
            raise ValueError(f"{name}: similar_day_n_candidates must be a whole number")
        if ((n_candidates[:, None] >= np.array(RANKS)[None, :]) != present).any():
            raise ValueError(
                f"{name}: a rank must be present exactly where similar_day_n_candidates reaches it"
            )
        distances = df[list(RANK_DISTANCE_COLS)].to_numpy(dtype="float64")
        if (distances[:, 1:] < distances[:, :-1]).any():
            raise ValueError(f"{name}: a rank's distance must not decrease by rank")
        for i in range(len(RANKS)):
            for j in range(i + 1, len(RANKS)):
                if (dates[:, i] == dates[:, j]).any():
                    raise ValueError(f"{name}: a reference day repeats within a row")
        loads = df[list(RANK_LOAD_COLS)].to_numpy(dtype="float64")
        mean = df[WEIGHTED_MEAN_COL].to_numpy(dtype="float64")
        # Rank 1 is never null, so every row has a smallest and a largest load.
        if (mean < np.nanmin(loads, axis=1) * (1 - _WEIGHTED_MEAN_TOLERANCE)).any() or (
            mean > np.nanmax(loads, axis=1) * (1 + _WEIGHTED_MEAN_TOLERANCE)
        ).any():
            raise ValueError(
                f"{name}: the weighted mean must lie between the smallest and largest rank load"
            )


def _ranked_days(
    scoring: WalkForwardScoring, forecast: AreaWeatherForecast, day_available_at: pd.Series
) -> pd.DataFrame:
    """One row per ranked day, before it is spread over its periods.

    Parameters
    ----------
    scoring : WalkForwardScoring
        Its ranking, selection and fit cutoffs.
    forecast : AreaWeatherForecast
        The forecasts the days were scored with.
    day_available_at : pandas.Series
        When each day's whole load was public, indexed by day.

    Returns
    -------
    pandas.DataFrame
        ``trade_date``, each rank's reference date, distance and ``weight_<r>``,
        ``similar_day_n_candidates``, ``similar_day_fit_cutoff``,
        ``similar_day_method`` and ``available_at``.

    Raises
    ------
    ValueError
        If the ranking holds ranks beyond ``SIMILAR_DAY_TOP_K``, or a ranked day
        has no forecast availability.
    """
    ranked = scoring.ranking.with_weights()
    if (ranked["rank"] > SIMILAR_DAY_TOP_K).any():
        raise ValueError(
            f"the ranking holds ranks beyond {SIMILAR_DAY_TOP_K}, the ranks the table has columns for"
        )
    days = pd.DataFrame({"trade_date": pd.DatetimeIndex(ranked["trade_date"].unique())})
    for r, date_col, distance_col in zip(RANKS, RANK_DATE_COLS, RANK_DISTANCE_COLS, strict=True):
        at_rank = ranked.loc[
            ranked["rank"] == r, ["trade_date", "reference_date", "distance", "weight"]
        ].rename(
            columns={"reference_date": date_col, "distance": distance_col, "weight": f"weight_{r}"}
        )
        days = days.merge(at_rank, how="left", on="trade_date", validate="one_to_one")
    forecast_available_at = days["trade_date"].map(
        forecast.df.groupby("trade_date")["available_at"].max()
    )
    if forecast_available_at.isna().any():
        unknown = sorted(d.date() for d in days.loc[forecast_available_at.isna(), "trade_date"])
        raise ValueError(f"{len(unknown)} day(s) have no forecast availability, e.g. {unknown[0]}")
    fit_cutoff = days["trade_date"].map(scoring.fit_cutoff).astype(_DATE)
    # Each rank's whole day of load is public before the row can be.
    availability = {
        "forecast": forecast_available_at,
        "fit_cutoff": fit_cutoff,
        **{col: days[col].map(day_available_at) for col in RANK_DATE_COLS},
    }
    n_candidates = scoring.selection.df.set_index("trade_date")["n_candidates"]
    return days.assign(
        similar_day_n_candidates=days["trade_date"].map(n_candidates).astype("float64"),
        similar_day_fit_cutoff=fit_cutoff,
        similar_day_method=METHOD_SIMILARITY,
        available_at=pd.DataFrame(availability).max(axis=1),
    )


def _empty_ranked_days() -> pd.DataFrame:
    """The columns of ``_ranked_days`` with no rows, for a scoring whose days all
    took their same-holiday reference."""
    columns: dict[str, pd.Series] = {"trade_date": pd.Series(dtype=_DATE)}
    for col in RANK_DATE_COLS:
        columns[col] = pd.Series(dtype=_DATE)
    for col in (*RANK_DISTANCE_COLS, *(f"weight_{r}" for r in RANKS)):
        columns[col] = pd.Series(dtype="float64")
    columns["similar_day_n_candidates"] = pd.Series(dtype="float64")
    columns["similar_day_fit_cutoff"] = pd.Series(dtype=_DATE)
    columns["similar_day_method"] = pd.Series(dtype="object")
    columns["available_at"] = pd.Series(dtype=_DATE)
    return pd.DataFrame(columns)


def _with_same_holiday(
    pool_days: pd.DataFrame, scoring: WalkForwardScoring, day_available_at: pd.Series
) -> pd.DataFrame:
    """``pool_days`` with the same-holiday reference put over the ranking.

    The base columns of a day that has a reference
    (``SpecialDayReferences.takes_reference``) carry that day's load alone: rank
    1's date is the reference, ranks 2 and 3, every distance, the pool's size and
    the fit cutoff are null, and ``available_at`` is the reference's load
    availability. Every other day keeps its ranking. The pool columns are built
    from ``pool_days`` untouched, so they hold the ranking on every day.

    Parameters
    ----------
    pool_days : pandas.DataFrame
        ``_ranked_days`` over every scored day.
    scoring : WalkForwardScoring
        Its special days; only those that take their reference are overridden.
    day_available_at : pandas.Series
        When each day's whole load was public, indexed by day.

    Returns
    -------
    pandas.DataFrame
        ``pool_days``' columns, the overridden days replaced.
    """
    references = scoring.special_days.df
    references = references[references["takes_reference"]]
    days = pool_days.copy()
    if references.empty:
        return days
    reference_of = references.set_index("trade_date")["last_year_date"]
    unranked = reference_of.index.difference(pd.DatetimeIndex(days["trade_date"]))
    if len(unranked):
        # Its pool had no candidate, so it has base columns and no pool ones.
        blank = pd.DataFrame({"trade_date": pd.DatetimeIndex(unranked)})
        for col in days.columns:
            if col == "trade_date":
                continue
            blank[col] = pd.Series(
                pd.NaT if days[col].dtype.kind == "M" else np.nan, index=blank.index
            ).astype(days[col].dtype if days[col].dtype.kind != "O" else "object")
        days = pd.concat([days, blank], ignore_index=True).sort_values(
            "trade_date", ignore_index=True
        )
    take = days["trade_date"].isin(reference_of.index).to_numpy()
    reference = days.loc[take, "trade_date"].map(reference_of)
    days.loc[take, RANK_DATE_COLS[0]] = reference.to_numpy()
    days.loc[take, "weight_1"] = 1.0
    for r, date_col in zip(RANKS[1:], RANK_DATE_COLS[1:], strict=True):
        days.loc[take, date_col] = pd.NaT
        days.loc[take, f"weight_{r}"] = np.nan
    for col in (*RANK_DISTANCE_COLS, "similar_day_n_candidates"):
        days.loc[take, col] = np.nan
    days.loc[take, "similar_day_fit_cutoff"] = pd.NaT
    days.loc[take, "similar_day_method"] = METHOD_SAME_HOLIDAY
    days.loc[take, "available_at"] = reference.map(day_available_at).to_numpy()
    return days


def _rank_loads(
    rows: pd.DataFrame, hourly_load: AreaHourlyLoad, date_cols: tuple[str, ...], prefix: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join each rank's hourly load onto ``rows``, and the periods that have none.

    Parameters
    ----------
    rows : pandas.DataFrame
        One row per day and period, carrying ``date_cols`` and ``hour_ending``.
    hourly_load : AreaHourlyLoad
    date_cols : tuple of str
        The rank date columns to join on, rank 1 first.
    prefix : str
        Name prefix of the load columns written, ``<prefix><rank>``.

    Returns
    -------
    tuple of (pandas.DataFrame, pandas.DataFrame)
        ``rows`` with the load columns, and the (day, period, reference) rows
        whose similar day has no load.
    """
    gaps = []
    for r, date_col in zip(RANKS, date_cols, strict=True):
        load = hourly_load.df[["load_date", "hour_ending", "demand_kwh"]].rename(
            columns={"load_date": date_col, "demand_kwh": f"{prefix}{r}"}
        )
        rows = rows.merge(load, how="left", on=[date_col, "hour_ending"], validate="many_to_one")
        missing = rows[date_col].notna() & rows[f"{prefix}{r}"].isna()
        gaps.append(
            rows.loc[missing, ["trade_date", "time_code", date_col]].set_axis(
                ["trade_date", "time_code", "reference_date"], axis=1
            )
        )
    return rows, pd.concat(gaps, ignore_index=True)


def _weighted_mean(rows: pd.DataFrame, load_prefix: str, weight_prefix: str) -> np.ndarray:
    """Sum weight x load one rank at a time, in rank order, so a re-run gives the
    same value to the bit; a null rank contributes nothing, and a row no rank
    reached is null, not zero."""
    total = np.zeros(len(rows))
    contributed = np.zeros(len(rows), dtype=bool)
    for r in RANKS:
        term = rows[f"{weight_prefix}{r}"].to_numpy(dtype="float64") * rows[
            f"{load_prefix}{r}"
        ].to_numpy(dtype="float64")
        present = ~np.isnan(term)
        total = total + np.where(present, term, 0.0)
        contributed |= present
    # A row no rank reached has no mean; summing its nulls would read as zero.
    return np.where(contributed, total, np.nan)


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

    A ranked day's rank loads are its ranked days' hourly loads over the period's
    hour, halved; its weighted mean sums weight × load one rank at a time, in rank
    order, so a re-run gives the same value to the bit. A same-holiday day's rank
    1 and mean are its reference's load, halved.

    Parameters
    ----------
    scoring : WalkForwardScoring
        The ranked days with the fits that ranked them, and the special days.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load the rank loads come from; a day's load is public
        when its newest hour is.
    forecast : AreaWeatherForecast
        The forecast profiles the ranked days were scored with; their
        ``available_at`` enters the ranked days' rows.
    run_id : str
        The job's MLflow run id.
    area_code : str
        dim_area.area_code value the feature was scored for.
    published_at : pandas.Timestamp
        When the rows are written (naive JST).

    Returns
    -------
    SimilarDayFeatureRecords
        48 rows per ranked or same-holiday day, sorted by day and period.

    Raises
    ------
    ValueError
        If the scoring has no ranked and no same-holiday day, the ranking holds
        ranks beyond ``SIMILAR_DAY_TOP_K``, a ranked or reference day lacks an
        hourly load, a ranked day has no forecast availability, or a row fails
        a ``SimilarDayFeatureRecords`` check (e.g. it is usable only after its
        issue time).
    """
    day_available_at = hourly_load.df.groupby("load_date")["available_at"].max()
    if not len(scoring.ranking) and not len(scoring.special_days.same_holiday_days):
        raise ValueError("no scored day to publish")
    pool_days = (
        _ranked_days(scoring, forecast, day_available_at)
        if len(scoring.ranking)
        else _empty_ranked_days()
    )
    days = _with_same_holiday(pool_days, scoring, day_available_at)
    pool_weights = {f"weight_{r}": f"pool_weight_{r}" for r in RANKS}
    carried = pool_days.rename(
        columns={**POOL_OF, **pool_weights, "available_at": "pool_available_at"}
    )[
        [
            "trade_date",
            *POOL_RANK_DATE_COLS,
            *POOL_RANK_DISTANCE_COLS,
            POOL_N_CANDIDATES_COL,
            POOL_FIT_CUTOFF_COL,
            *pool_weights.values(),
            "pool_available_at",
        ]
    ]
    days = days.merge(carried, how="left", on="trade_date", validate="one_to_one")
    # One row, one availability: the later of the two variants' inputs, so neither
    # set of columns is served before everything behind it was public.
    days["available_at"] = days[["available_at", "pool_available_at"]].max(axis=1)
    days = days.drop(columns="pool_available_at")
    rows = days.merge(
        pd.DataFrame({"time_code": np.arange(1, N_PERIODS + 1, dtype="int64")}), how="cross"
    )
    rows["hour_ending"] = (rows["time_code"] + 1) // 2
    rows, base_gap = _rank_loads(rows, hourly_load, RANK_DATE_COLS, "load_")
    rows, pool_gap = _rank_loads(rows, hourly_load, POOL_RANK_DATE_COLS, "pool_load_")
    gap = pd.concat([base_gap, pool_gap], ignore_index=True).sort_values(
        ["trade_date", "time_code"]
    )
    if not gap.empty:
        n_periods = len(gap.drop_duplicates(["trade_date", "time_code"]))
        first = gap.iloc[0]
        raise ValueError(
            f"{n_periods} period(s) have no load on their similar day, e.g. "
            f"{first['trade_date'].date()} time_code {first['time_code']} "
            f"(similar day {first['reference_date'].date()})"
        )
    rows = rows.assign(
        area_code=area_code,
        **{
            col: rows[f"load_{r}"].to_numpy(dtype="float64") / PERIODS_PER_HOUR
            for r, col in zip(RANKS, RANK_LOAD_COLS, strict=True)
        },
        **{
            col: rows[f"pool_load_{r}"].to_numpy(dtype="float64") / PERIODS_PER_HOUR
            for r, col in zip(RANKS, POOL_RANK_LOAD_COLS, strict=True)
        },
        **{WEIGHTED_MEAN_COL: _weighted_mean(rows, "load_", "weight_") / PERIODS_PER_HOUR},
        **{
            POOL_WEIGHTED_MEAN_COL: _weighted_mean(rows, "pool_load_", "pool_weight_")
            / PERIODS_PER_HOUR
        },
        published_at=pd.Timestamp(published_at),
        run_id=run_id,
    )
    df = (
        rows[list(SimilarDayFeatureRecords.schema)]
        .astype(
            {
                **{
                    col: _DATE
                    for col in (
                        *RANK_DATE_COLS,
                        *POOL_RANK_DATE_COLS,
                        "similar_day_fit_cutoff",
                        POOL_FIT_CUTOFF_COL,
                    )
                },
                "available_at": _DATE,
                "published_at": _DATE,
                "similar_day_method": "object",
            }
        )
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )
    return SimilarDayFeatureRecords.from_df(df)


#: Spark SQL types of the published columns; a float64 column without an entry is a double,
#: and any other column needs one.
_SQL_TYPES: dict[str, str] = {
    "area_code": "string",
    "trade_date": "date",
    "time_code": "int",
    **{col: "date" for col in (*RANK_DATE_COLS, *POOL_RANK_DATE_COLS)},
    "similar_day_n_candidates": "int",
    POOL_N_CANDIDATES_COL: "int",
    POOL_FIT_CUTOFF_COL: "timestamp",
    "similar_day_fit_cutoff": "timestamp",
    "similar_day_method": "string",
    "available_at": "timestamp",
    "published_at": "timestamp",
}


def _sql_type(col: str) -> str:
    """The Spark SQL type a published column is created and cast as.

    Parameters
    ----------
    col : str
        A column of ``SimilarDayFeatureRecords.schema``.

    Returns
    -------
    str
        Its ``_SQL_TYPES`` entry; ``double`` for a float64 column without one.

    Raises
    ------
    KeyError
        If a column that is not float64 has no ``_SQL_TYPES`` entry.
    """
    if SimilarDayFeatureRecords.schema[col] == "float64" and col not in _SQL_TYPES:
        return "double"
    return _SQL_TYPES[col]


def publish_feature_records(
    records: SimilarDayFeatureRecords, spark: SparkSession | None = None
) -> int:
    """Idempotently write one run's feature rows to ``FEATURE_TABLE``.

    The table (parquet, partitioned by ``run_id``) is created on first use and
    only the run's partition is overwritten, as for the forecast tables. The
    table is never altered, so a table with other columns must be dropped first.

    Parameters
    ----------
    records : SimilarDayFeatureRecords
        The run's rows.
    spark : pyspark.sql.SparkSession, optional
        Existing session; defaults to
        :func:`power_market_analytics.common.spark.get_spark_session`.

    Returns
    -------
    int
        Number of rows written.
    """
    spark = spark if spark is not None else get_spark_session()
    columns = [col for col in SimilarDayFeatureRecords.schema if col != "run_id"]
    create_run_partitioned_table(
        spark,
        FEATURE_TABLE,
        ",\n          ".join(f"{col} {_sql_type(col)}" for col in columns),
    )
    selected = []
    for col in columns:
        value = F.col(col)
        if SimilarDayFeatureRecords.schema[col] == "float64":
            # pandas NaN arrives as a double NaN (Arrow off, or its fallback), not SQL null.
            value = F.when(F.isnan(value), F.lit(None)).otherwise(value)
        selected.append(value.cast(_sql_type(col)).alias(col))
    sdf = spark.createDataFrame(records.df).select(*selected, F.col("run_id").cast("string"))
    overwrite_run_partitions(spark, FEATURE_TABLE, sdf)
    logger.info(
        "Published {} rows to {} (run_id={})",
        len(records),
        FEATURE_TABLE,
        records.df["run_id"].iloc[0],
    )
    return len(records)
