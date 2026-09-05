"""Learned similar-day reference load for the demand task (R-004 E-002).

For a delivery day D the selector scores every day in a window one year back
(D − 364 ± 30) by a weighted distance over seven parts — calendar days from
D − 364, the 24-hour RMSE of D's MSM forecast against the candidate's
observation for temperature, humidity and rain, and the absolute differences
of three ``dim_date`` holiday attributes — and picks the nearest. The weights
are fitted once per run on past pairs (Park, Song and Kwon 2020, §2.2). The
chosen day's でんき予報 hourly load, halved per period, is the feature
``similar_day_demand_kwh``. Design:
docs/superpowers/specs/2026-09-05-demand-similar-day-reference-design.md.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

import numpy as np
import pandas as pd
from loguru import logger
from scipy.optimize import least_squares

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.features import hour_ending_of
from power_market_analytics.tasks.demand.frames import (
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)

SIMILAR_DAY_CENTER_LAG_DAYS = 364
SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS = 30
SIMILAR_DAY_FEATURE = "similar_day_demand_kwh"
#: An hour's energy is spread evenly over its two delivery periods.
PERIODS_PER_HOUR = 2
HOURS_PER_DAY = 24
#: The distance parts, in the fixed order the weights and the fit use.
SIMILAR_DAY_COMPONENTS: tuple[str, ...] = (
    "calendar_days",
    "temperature",
    "humidity",
    "rain",
    "days_since_holiday",
    "days_until_holiday",
    "holiday_degree",
)
#: Fewer training pairs than this cannot pin down seven weights, α and β.
MIN_FIT_PAIRS = 8
#: (part, forecast column of the target, observed column of the candidate).
_WEATHER_MEASURES: tuple[tuple[str, str, str], ...] = (
    ("temperature", "forecast_temperature_c", "temperature_c"),
    ("humidity", "forecast_relative_humidity_pct", "humidity_pct"),
    ("rain", "forecast_precipitation_mm", "precipitation_mm"),
)
_CALENDAR_ATTRIBUTES: tuple[str, ...] = (
    "days_since_holiday",
    "days_until_holiday",
    "holiday_degree",
)
_DATE = "datetime64[ns]"


def _check_non_negative(name: str, df: pd.DataFrame, columns: Iterable[str]) -> None:
    for col in columns:
        if (df[col] < 0).any():
            raise ValueError(f"{name}: {col} must be >= 0")


class DayPairDifferences(DomainFrame):
    """The seven distance parts of (target day, candidate day) pairs.

    Every part is non-negative: calendar days from the window's centre, the
    24-hour RMSE of the target's forecast against the candidate's observation
    for temperature (°C), humidity (%) and rain (mm/h), and the absolute
    differences of the days since and until a named holiday and of the holiday
    degree. The candidate always precedes the target.

    Grain: (target_date, candidate_date).
    """

    schema = {
        "target_date": _DATE,
        "candidate_date": _DATE,
        **{part: "float64" for part in SIMILAR_DAY_COMPONENTS},
    }
    keys = ["target_date", "candidate_date"]
    non_null_cols = list(SIMILAR_DAY_COMPONENTS)

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_non_negative(cls.__name__, df, SIMILAR_DAY_COMPONENTS)
        if (df["candidate_date"] >= df["target_date"]).any():
            raise ValueError(f"{cls.__name__}: candidate_date must precede target_date")


class SimilarDayTrainingPairs(DayPairDifferences):
    """Pair differences plus the realised load difference the weights are fitted to.

    ``load_difference`` is the paper's Eq. (3): the mean over the 24 hours of
    the absolute relative difference of the two hourly load curves.

    Grain: (target_date, candidate_date).
    """

    schema = {**DayPairDifferences.schema, "load_difference": "float64"}
    non_null_cols = [*SIMILAR_DAY_COMPONENTS, "load_difference"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        super()._validate_extra(df)
        _check_non_negative(cls.__name__, df, ["load_difference"])


class SimilarDaySelection(DomainFrame):
    """The similar day chosen for each delivery day.

    ``reference_lag_days`` = ``trade_date − reference_date`` in days;
    ``n_candidates`` the window days that could be scored; ``lag_364_rank``
    the distance rank (1 = nearest) of the plain same-weekday day one year
    back, NaN when it was not a candidate.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": _DATE,
        "reference_date": _DATE,
        "distance": "float64",
        "reference_lag_days": "int64",
        "n_candidates": "int64",
        "lag_364_rank": "float64",
    }
    keys = ["trade_date"]
    non_null_cols = ["reference_date", "distance", "reference_lag_days", "n_candidates"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        lag = (df["trade_date"] - df["reference_date"]).dt.days
        if (lag != df["reference_lag_days"]).any():
            raise ValueError(
                f"{cls.__name__}: reference_lag_days must equal trade_date - reference_date"
            )


class SimilarDayRetrieval(DomainFrame):
    """After-the-fact check of a selection once the delivery day's load is known.

    Per day: the selected day's realised load difference, the plain D − 364
    day's (NaN when not a candidate), the best candidate (``oracle_date``) and
    its load difference, and where the selected day ranked by that outcome
    (1 = the oracle).

    Grain: (trade_date).
    """

    schema = {
        "trade_date": _DATE,
        "reference_date": _DATE,
        "distance": "float64",
        "selected_load_difference": "float64",
        "lag_364_load_difference": "float64",
        "oracle_date": _DATE,
        "oracle_load_difference": "float64",
        "selected_rank_by_outcome": "int64",
    }
    keys = ["trade_date"]
    non_null_cols = [
        "reference_date",
        "distance",
        "selected_load_difference",
        "oracle_date",
        "oracle_load_difference",
        "selected_rank_by_outcome",
    ]


def _empty(frame_cls: type[DomainFrame]) -> pd.DataFrame:
    """An empty frame with ``frame_cls``'s columns and dtypes."""
    return pd.DataFrame({col: pd.Series(dtype=dtype) for col, dtype in frame_cls.schema.items()})


@dataclasses.dataclass(frozen=True)
class _Profiles:
    """Complete 24-hour profiles of one or more measures, one row per day."""

    days: pd.DatetimeIndex
    values: dict[str, np.ndarray]


def _complete_profiles(df: pd.DataFrame, date_col: str, columns: dict[str, str]) -> _Profiles:
    """Pivot an hourly frame into (days × 24) matrices, keeping complete days only.

    Parameters
    ----------
    df : pandas.DataFrame
        Hourly rows with ``date_col``, ``hour_ending`` and the value columns.
    date_col : str
        The day column.
    columns : dict of str to str
        Part name -> value column.

    Returns
    -------
    _Profiles
        Days with all 24 hours present and non-null for every column, sorted.
    """
    hours = range(1, HOURS_PER_DAY + 1)
    wide = df.pivot(index=date_col, columns="hour_ending", values=list(columns.values()))
    wide = wide.reindex(columns=pd.MultiIndex.from_product([list(columns.values()), hours]))
    complete = wide.dropna().sort_index()
    days = pd.DatetimeIndex(complete.index)
    return _Profiles(
        days=days,
        values={name: complete[col].to_numpy(dtype="float64") for name, col in columns.items()},
    )


def load_difference(target: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """The paper's Eq. (3): mean absolute relative difference of two hourly load curves.

    Parameters
    ----------
    target, candidate : numpy.ndarray
        Shape (n, 24); the target's loads are positive.

    Returns
    -------
    numpy.ndarray
        Shape (n,).
    """
    return np.mean(np.abs(target - candidate) / target, axis=1)


def _softmax(scores: np.ndarray) -> np.ndarray:
    """Weights of the parts: softmax of the free scores with the last part's score fixed at 0."""
    full = np.append(scores, 0.0)
    exp = np.exp(full - full.max())
    return exp / exp.sum()


def _distance(scaled: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """``sqrt(Σ_j w_j · scaled_j²)`` per row."""
    return np.sqrt((scaled**2) @ weights)


@dataclasses.dataclass(frozen=True)
class SimilarDayWeights:
    """A fitted similar-day distance: ``d = sqrt(Σ_j w_j (Δ_j / s_j)²)``, ``ŷ = α d + β``.

    Attributes
    ----------
    components : tuple of str
        The parts, in ``SIMILAR_DAY_COMPONENTS`` order.
    weights : numpy.ndarray
        Non-negative, summing to one: each part's share of the squared distance.
    scales : numpy.ndarray
        The RMS of each part over the training pairs (1 where a part was all zero).
    alpha, beta : float
        The straight line from distance to load difference (``alpha >= 0``).
    n_pairs, n_targets : int
        Training pairs and distinct target days.
    fit_from, fit_through : pandas.Timestamp
        First and last target day of the fit.
    fit_rmse : float
        RMSE of ``ŷ`` against the realised load differences.
    """

    components: tuple[str, ...]
    weights: np.ndarray
    scales: np.ndarray
    alpha: float
    beta: float
    n_pairs: int
    n_targets: int
    fit_from: pd.Timestamp
    fit_through: pd.Timestamp
    fit_rmse: float

    def distance(self, differences: DayPairDifferences) -> np.ndarray:
        """The distance of every pair.

        Parameters
        ----------
        differences : DayPairDifferences

        Returns
        -------
        numpy.ndarray
            One value per row of ``differences``.
        """
        parts = differences.df[list(self.components)].to_numpy(dtype="float64")
        return _distance(parts / self.scales, self.weights)

    def as_params(self) -> dict[str, object]:
        """The fit as MLflow run params.

        Returns
        -------
        dict of str to object
        """
        return {
            "similar_day_weights": ",".join(
                f"{c}={w:.4f}" for c, w in zip(self.components, self.weights, strict=True)
            ),
            "similar_day_scales": ",".join(
                f"{c}={s:.4g}" for c, s in zip(self.components, self.scales, strict=True)
            ),
            "similar_day_alpha": round(self.alpha, 6),
            "similar_day_beta": round(self.beta, 6),
            "similar_day_fit_n_pairs": self.n_pairs,
            "similar_day_fit_n_targets": self.n_targets,
            "similar_day_fit_from": str(self.fit_from.date()),
            "similar_day_fit_through": str(self.fit_through.date()),
            "similar_day_fit_rmse": round(self.fit_rmse, 6),
        }


def fit_similar_day_weights(pairs: SimilarDayTrainingPairs) -> SimilarDayWeights:
    """Fit the distance weights by nonlinear least squares on the paper's cost.

    Minimises ``Σ_k (y_k − (α d_k + β))²`` over the free softmax scores (the
    last part's score is fixed at 0 so the weights are identified), ``α ≥ 0``
    and ``β``, with ``scipy.optimize.least_squares`` (``trf``). Every part is
    first divided by its RMS over the pairs, so a weight reads as a share.
    The start is equal weights with ``α``, ``β`` from the straight-line fit
    of ``y`` on that distance; nothing is random.

    Parameters
    ----------
    pairs : SimilarDayTrainingPairs

    Returns
    -------
    SimilarDayWeights

    Raises
    ------
    ValueError
        Fewer than ``MIN_FIT_PAIRS`` pairs.
    RuntimeError
        The solver did not converge.
    """
    df = pairs.df
    if len(df) < MIN_FIT_PAIRS:
        raise ValueError(f"{len(df)} training pairs; at least {MIN_FIT_PAIRS} are needed")
    parts = df[list(SIMILAR_DAY_COMPONENTS)].to_numpy(dtype="float64")
    y = df["load_difference"].to_numpy(dtype="float64")
    rms = np.sqrt(np.mean(parts**2, axis=0))
    scales = np.where(rms > 0, rms, 1.0)
    scaled = parts / scales
    n_free = len(SIMILAR_DAY_COMPONENTS) - 1
    start_distance = _distance(scaled, _softmax(np.zeros(n_free)))
    spread = float(np.var(start_distance))
    alpha0 = (
        max(float(np.cov(start_distance, y, bias=True)[0, 1] / spread), 0.0) if spread > 0 else 0.0
    )
    beta0 = float(np.mean(y) - alpha0 * np.mean(start_distance))

    def residuals(theta: np.ndarray) -> np.ndarray:
        weights = _softmax(theta[:n_free])
        return y - (theta[n_free] * _distance(scaled, weights) + theta[n_free + 1])

    lower = np.concatenate([np.full(n_free, -np.inf), [0.0, -np.inf]])
    result = least_squares(
        residuals,
        np.concatenate([np.zeros(n_free), [alpha0, beta0]]),
        bounds=(lower, np.inf),
        method="trf",
    )
    if not result.success:
        raise RuntimeError(f"similar-day weight fit failed: {result.message}")
    return SimilarDayWeights(
        components=SIMILAR_DAY_COMPONENTS,
        weights=_softmax(result.x[:n_free]),
        scales=scales,
        alpha=float(result.x[n_free]),
        beta=float(result.x[n_free + 1]),
        n_pairs=len(df),
        n_targets=int(df["target_date"].nunique()),
        fit_from=df["target_date"].min(),
        fit_through=df["target_date"].max(),
        fit_rmse=float(np.sqrt(np.mean(result.fun**2))),
    )


class SimilarDaySelector:
    """Scores, fits and selects similar days for the demand task.

    Parameters
    ----------
    calendar : DayCalendar
        Holiday attributes of every day (targets and candidates).
    weather_forecast : AreaWeatherForecast
        The target side of the weather parts: population-weighted MSM
        forecasts by delivery day.
    weather_observed : AreaObservedWeather
        The candidate side: population-weighted observations by day.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load (candidates' loads; targets' too once known).
    center_lag_days : int, optional
        Window centre, the same weekday one year back.
    half_width_days : int, optional
        Window half width in days.

    Raises
    ------
    ValueError
        If the window is empty or reaches the target day, or no day has an
        observed profile, a load profile and a calendar row.
    """

    def __init__(
        self,
        calendar: DayCalendar,
        weather_forecast: AreaWeatherForecast,
        weather_observed: AreaObservedWeather,
        hourly_load: AreaHourlyLoad,
        *,
        center_lag_days: int = SIMILAR_DAY_CENTER_LAG_DAYS,
        half_width_days: int = SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    ) -> None:
        if half_width_days < 0 or center_lag_days - half_width_days < 1:
            raise ValueError(
                f"window {center_lag_days} ± {half_width_days} days must lie strictly in the past"
            )
        self.center_lag_days = center_lag_days
        self.half_width_days = half_width_days
        self._calendar = calendar.df.set_index("trade_date").sort_index()
        self._forecast = _complete_profiles(
            weather_forecast.df, "trade_date", {name: col for name, col, _ in _WEATHER_MEASURES}
        )
        self._observed = _complete_profiles(
            weather_observed.df, "obs_date", {name: col for name, _, col in _WEATHER_MEASURES}
        )
        self._load = _complete_profiles(hourly_load.df, "load_date", {"load": "demand_kwh"})
        candidates = self._observed.days.intersection(self._load.days).intersection(
            pd.DatetimeIndex(self._calendar.index)
        )
        if candidates.empty:
            raise ValueError(
                "no candidate days: no day has an observed profile, a load profile and a "
                "calendar row"
            )
        self._candidates = pd.DatetimeIndex(candidates).sort_values()
        self._weights: SimilarDayWeights | None = None
        self.first_candidate_day: pd.Timestamp = self._candidates[0]
        self.hourly_load_span: tuple[pd.Timestamp, pd.Timestamp] = (
            self._load.days[0],
            self._load.days[-1],
        )
        logger.info(
            "SimilarDaySelector: {} candidate days ({}..{}), {} forecast days, window {} ± {}",
            len(self._candidates),
            self.first_candidate_day.date(),
            self._candidates[-1].date(),
            len(self._forecast.days),
            center_lag_days,
            half_width_days,
        )

    @property
    def lags(self) -> np.ndarray:
        """The window's lags in days, ascending."""
        return np.arange(
            self.center_lag_days - self.half_width_days,
            self.center_lag_days + self.half_width_days + 1,
            dtype="int64",
        )

    def scorable_days(self, days: Iterable[pd.Timestamp]) -> pd.DatetimeIndex:
        """The delivery days among ``days`` that can be scored.

        A day needs a complete forecast profile, a calendar row, and a window
        that starts on or after the first candidate day.

        Parameters
        ----------
        days : iterable of pandas.Timestamp

        Returns
        -------
        pandas.DatetimeIndex
            Unique, sorted.
        """
        index = pd.DatetimeIndex(pd.to_datetime(list(days))).unique().sort_values()
        earliest_window_start = index - pd.Timedelta(days=int(self.lags.max()))
        ok = (
            index.isin(self._forecast.days)
            & index.isin(self._calendar.index)
            & (earliest_window_start >= self.first_candidate_day)
        )
        return index[ok]

    def _pairs(self, targets: pd.DatetimeIndex) -> pd.DataFrame:
        """Every (target, candidate) pair inside the window, with the lag in days."""
        lags = self.lags
        pairs = pd.DataFrame(
            {
                "target_date": np.repeat(targets.to_numpy(), len(lags)),
                "lag_days": np.tile(lags, len(targets)),
            }
        )
        pairs["candidate_date"] = pairs["target_date"] - pd.to_timedelta(
            pairs["lag_days"], unit="D"
        )
        return pairs[pairs["candidate_date"].isin(self._candidates)].reset_index(drop=True)

    def differences(self, days: Iterable[pd.Timestamp]) -> DayPairDifferences:
        """The seven parts for every window pair of the scorable days among ``days``.

        Parameters
        ----------
        days : iterable of pandas.Timestamp

        Returns
        -------
        DayPairDifferences
            Sorted by target day, then candidate day; empty when nothing is scorable.
        """
        pairs = self._pairs(self.scorable_days(days))
        t_pos = self._forecast.days.get_indexer(pd.DatetimeIndex(pairs["target_date"]))
        c_pos = self._observed.days.get_indexer(pd.DatetimeIndex(pairs["candidate_date"]))
        parts: dict[str, np.ndarray] = {
            "calendar_days": np.abs(pairs["lag_days"].to_numpy() - self.center_lag_days).astype(
                "float64"
            )
        }
        for name, _, _ in _WEATHER_MEASURES:
            gap = self._forecast.values[name][t_pos] - self._observed.values[name][c_pos]
            parts[name] = np.sqrt(np.mean(gap**2, axis=1))
        target_attrs = self._calendar.loc[pairs["target_date"], list(_CALENDAR_ATTRIBUTES)]
        candidate_attrs = self._calendar.loc[pairs["candidate_date"], list(_CALENDAR_ATTRIBUTES)]
        for col in _CALENDAR_ATTRIBUTES:
            parts[col] = np.abs(
                target_attrs[col].to_numpy(dtype="float64")
                - candidate_attrs[col].to_numpy(dtype="float64")
            )
        out = pairs[["target_date", "candidate_date"]].assign(**parts)
        out = out.sort_values(["target_date", "candidate_date"], ignore_index=True)
        return DayPairDifferences.from_df(out[list(DayPairDifferences.schema)])

    def training_pairs(self, through: pd.Timestamp) -> SimilarDayTrainingPairs:
        """Every window pair of the scorable forecast days on or before ``through``
        whose own hourly load is known, with the realised load difference.

        Parameters
        ----------
        through : pandas.Timestamp
            Last target day allowed (the newest day the strategy may see).

        Returns
        -------
        SimilarDayTrainingPairs
        """
        through = pd.Timestamp(through)
        targets = self._forecast.days[self._forecast.days <= through]
        diffs = self.differences(targets[targets.isin(self._load.days)]).df
        loads = self._load.values["load"]
        realised = load_difference(
            loads[self._load.days.get_indexer(pd.DatetimeIndex(diffs["target_date"]))],
            loads[self._load.days.get_indexer(pd.DatetimeIndex(diffs["candidate_date"]))],
        )
        return SimilarDayTrainingPairs.from_df(diffs.assign(load_difference=realised))

    def fit(self, through: pd.Timestamp) -> SimilarDayWeights:
        """Fit and store the weights on the training pairs up to ``through``.

        Parameters
        ----------
        through : pandas.Timestamp

        Returns
        -------
        SimilarDayWeights

        Raises
        ------
        ValueError
            No pair has a target day on or before ``through`` (or too few).
        RuntimeError
            The solver did not converge.
        """
        pairs = self.training_pairs(through)
        if len(pairs) == 0:
            raise ValueError(
                f"no training pairs with a target day on or before {pd.Timestamp(through).date()}"
            )
        self._weights = fit_similar_day_weights(pairs)
        logger.info("SimilarDaySelector: fitted on {}", self._weights.as_params())
        return self._weights

    def ensure_fitted(self, through: pd.Timestamp) -> SimilarDayWeights:
        """Fit once; later calls return the stored weights whatever ``through`` is.

        Parameters
        ----------
        through : pandas.Timestamp

        Returns
        -------
        SimilarDayWeights
        """
        return self._weights if self._weights is not None else self.fit(through)

    @property
    def weights(self) -> SimilarDayWeights:
        """The fitted weights.

        Raises
        ------
        RuntimeError
            Before any fit.
        """
        if self._weights is None:
            raise RuntimeError("similar-day weights are not fitted; call fit(through) first")
        return self._weights

    def _scored(self, days: Iterable[pd.Timestamp]) -> pd.DataFrame:
        """Window pairs with their distance, lag and gap from the window's centre."""
        diffs = self.differences(days)
        df = diffs.df.assign(distance=self.weights.distance(diffs))
        lag = (df["target_date"] - df["candidate_date"]).dt.days
        return df.assign(lag_days=lag, centre_gap=(lag - self.center_lag_days).abs())

    def select(self, days: Iterable[pd.Timestamp]) -> SimilarDaySelection:
        """Pick the nearest window day for every scorable day among ``days``.

        Ties go to the candidate nearest the window's centre, then the earlier
        date.

        Parameters
        ----------
        days : iterable of pandas.Timestamp

        Returns
        -------
        SimilarDaySelection
            One row per scorable day; empty when none is.

        Raises
        ------
        RuntimeError
            Before any fit.
        """
        scored = self._scored(days)
        if scored.empty:
            return SimilarDaySelection.from_df(_empty(SimilarDaySelection))
        best = (
            scored.sort_values(["target_date", "distance", "centre_gap", "candidate_date"])
            .groupby("target_date", sort=True)
            .head(1)
            .set_index("target_date")
        )
        ranks = scored.assign(rank=scored.groupby("target_date")["distance"].rank(method="min"))
        at_centre = ranks[ranks["lag_days"] == self.center_lag_days].set_index("target_date")[
            "rank"
        ]
        counts = scored.groupby("target_date").size()
        out = pd.DataFrame(
            {
                "trade_date": best.index,
                "reference_date": best["candidate_date"].to_numpy(),
                "distance": best["distance"].to_numpy(dtype="float64"),
                "reference_lag_days": best["lag_days"].to_numpy(dtype="int64"),
                "n_candidates": counts.loc[best.index].to_numpy(dtype="int64"),
                "lag_364_rank": at_centre.reindex(best.index).to_numpy(dtype="float64"),
            }
        )
        return SimilarDaySelection.from_df(out)

    def retrieval(self, selection: SimilarDaySelection) -> SimilarDayRetrieval:
        """Judge a selection against what every candidate's load turned out to be.

        Only the selected days whose own hourly load is known are checked.

        Parameters
        ----------
        selection : SimilarDaySelection

        Returns
        -------
        SimilarDayRetrieval
            Empty when no selected day has a known load yet.
        """
        known = selection.df[selection.df["trade_date"].isin(self._load.days)]
        scored = self._scored(known["trade_date"]) if not known.empty else pd.DataFrame()
        if scored.empty:
            return SimilarDayRetrieval.from_df(_empty(SimilarDayRetrieval))
        loads = self._load.values["load"]
        scored = scored.assign(
            load_difference=load_difference(
                loads[self._load.days.get_indexer(pd.DatetimeIndex(scored["target_date"]))],
                loads[self._load.days.get_indexer(pd.DatetimeIndex(scored["candidate_date"]))],
            )
        )
        scored = scored.assign(
            outcome_rank=scored.groupby("target_date")["load_difference"].rank(method="min")
        )
        chosen = scored.merge(
            known[["trade_date", "reference_date"]].rename(
                columns={"trade_date": "target_date", "reference_date": "candidate_date"}
            ),
            how="inner",
            on=["target_date", "candidate_date"],
            validate="one_to_one",
        ).set_index("target_date")
        at_centre = scored[scored["lag_days"] == self.center_lag_days].set_index("target_date")[
            "load_difference"
        ]
        oracle = (
            scored.sort_values(["target_date", "load_difference", "candidate_date"])
            .groupby("target_date", sort=True)
            .head(1)
            .set_index("target_date")
        )
        out = pd.DataFrame(
            {
                "trade_date": chosen.index,
                "reference_date": chosen["candidate_date"].to_numpy(),
                "distance": chosen["distance"].to_numpy(dtype="float64"),
                "selected_load_difference": chosen["load_difference"].to_numpy(dtype="float64"),
                "lag_364_load_difference": at_centre.reindex(chosen.index).to_numpy(
                    dtype="float64"
                ),
                "oracle_date": oracle.loc[chosen.index, "candidate_date"].to_numpy(),
                "oracle_load_difference": oracle.loc[chosen.index, "load_difference"].to_numpy(
                    dtype="float64"
                ),
                "selected_rank_by_outcome": chosen["outcome_rank"].to_numpy(dtype="int64"),
            }
        )
        return SimilarDayRetrieval.from_df(out)


def join_similar_day_load(
    points: pd.DataFrame,
    selection: SimilarDaySelection,
    hourly_load: AreaHourlyLoad,
    *,
    name: str = SIMILAR_DAY_FEATURE,
) -> pd.DataFrame:
    """Attach the selected similar day's hourly load, halved per period.

    For a point (D, time_code) the feature is the hourly load on D's
    ``reference_date`` at ``hour_ending_of(time_code)`` divided by
    ``PERIODS_PER_HOUR`` (kWh per 30-minute period, the target's scale); NaN
    where D has no selection or the reference hour has no load.

    Parameters
    ----------
    points : pandas.DataFrame
        Rows keyed on (trade_date, time_code); other columns pass through.
    selection : SimilarDaySelection
    hourly_load : AreaHourlyLoad
    name : str, optional
        Name for the new column.

    Returns
    -------
    pandas.DataFrame
        ``points`` plus ``name`` (float64), in the original row order.
    """
    keyed = points[GRAIN_COLS].merge(
        selection.df[["trade_date", "reference_date"]],
        how="left",
        on="trade_date",
        validate="many_to_one",
    )
    keyed = keyed.assign(hour_ending=hour_ending_of(keyed["time_code"]))
    load = hourly_load.df.rename(columns={"load_date": "reference_date"})
    joined = keyed.merge(
        load, how="left", on=["reference_date", "hour_ending"], validate="many_to_one"
    )
    return points.assign(
        **{name: joined["demand_kwh"].to_numpy(dtype="float64") / PERIODS_PER_HOUR}
    )


def retrieval_metrics(retrieval: SimilarDayRetrieval) -> dict[str, float]:
    """Mean realised load differences of the selected, D − 364 and oracle days,
    and the share of days the selected day beat D − 364 (over days where the
    latter was a candidate). NaN where a mean has no rows.

    Parameters
    ----------
    retrieval : SimilarDayRetrieval

    Returns
    -------
    dict of str to float
    """
    df = retrieval.df
    comparable = df.dropna(subset=["lag_364_load_difference"])
    return {
        "similar_day_load_difference_selected": float(df["selected_load_difference"].mean()),
        "similar_day_load_difference_lag_364": float(df["lag_364_load_difference"].mean()),
        "similar_day_load_difference_oracle": float(df["oracle_load_difference"].mean()),
        "similar_day_share_better_than_lag_364": float(
            (comparable["selected_load_difference"] < comparable["lag_364_load_difference"]).mean()
        ),
    }
