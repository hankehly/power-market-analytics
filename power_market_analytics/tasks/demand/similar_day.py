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

from power_market_analytics.common.frames import DomainFrame
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
