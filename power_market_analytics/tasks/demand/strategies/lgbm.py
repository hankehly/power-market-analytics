"""The demand task's similar-day strategies.

LightGBM over the ``lightgbm_msm_popw_daytype`` preset's features, retrieved
through Feast, plus the load of a learned similar day one year earlier joined
in Python — and, for the four calendar variants, the delivery day's
``dim_date`` calendar attributes. They stay strategies until the similar day
is a mart column of its own (the feature catalogue's PR 7); the presets
without it run as plain
:class:`~power_market_analytics.forecasting.preset_lgbm.PresetLightGbmStrategy`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import ClassVar

import mlflow
import pandas as pd

from power_market_analytics.features.frame import FeatureFrame
from power_market_analytics.features.presets import Preset
from power_market_analytics.forecasting.backtest import BacktestRun
from power_market_analytics.forecasting.frames import DayAheadForecast, HalfHourlySeries
from power_market_analytics.forecasting.preset_lgbm import (
    PresetLightGbmStrategy,
    preset_eval_set_cls,
)
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.features import (
    CALENDAR_COUNT_FEATURE_COLS,
    DAY_CALENDAR_FEATURE_COLS,
    HOLIDAY_DEGREE_FEATURE_COLS,
    HOLIDAY_DISTANCE_FEATURE_COLS,
    join_day_calendar,
)
from power_market_analytics.tasks.demand.frames import (
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)
from power_market_analytics.tasks.demand.similar_day import (
    PERIODS_PER_HOUR,
    SIMILAR_DAY_COMPONENTS,
    SIMILAR_DAY_FEATURE,
    SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
    SimilarDaySelection,
    SimilarDaySelector,
    join_similar_day_load,
    retrieval_metrics,
)

#: Contract dtypes of the calendar features in the eval set: the counts, the
#: working-day flag and the holiday distances as integers once only complete
#: rows remain, the holiday degree as a float.
DAY_CALENDAR_FEATURE_DTYPES: dict[str, str] = {
    col: "float64" if col == "holiday_degree" else "int64" for col in DAY_CALENDAR_FEATURE_COLS
}


class LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(PresetLightGbmStrategy):
    """The ``lightgbm_msm_popw_daytype`` preset plus the load of a learned
    similar day one year earlier (``similar_day_demand_kwh``).

    Experiment E-002 of docs/research/demand/R-004-prior-year-load-lag.md, the
    Tokyo demand baseline. The preset's features come from the retrieved
    frame like any preset strategy's. A :class:`SimilarDaySelector` picks,
    for every delivery day, the nearest day in D − 364 ± 30 under a
    seven-part weighted distance whose weights are fitted once per run — on
    the first :meth:`predict`, over every pair whose target day the strategy
    may see — then frozen. The chosen day's でんき予報 hourly load, halved per
    period, is the feature. Training rows and target days that cannot be
    scored get NaN and are dropped or skipped as usual. A subclass that sets
    ``calendar_feature_cols`` joins those ``DayCalendar`` columns after it.

    Parameters
    ----------
    preset : Preset
        The feature list retrieved through Feast (``lightgbm_msm_popw_daytype``
        or a set changed from it).
    features : FeatureFrame
        The preset's features for every day the run may train on or forecast.
    weather_forecast : AreaWeatherForecast
        Population-weighted MSM forecast of temperature, humidity and rain by
        delivery day, for the selector.
    day_calendar : DayCalendar
        Calendar attributes of every day: the selector's inputs and the
        calendar variants' features.
    weather_observed : AreaObservedWeather
        Population-weighted observed temperature, humidity and rain.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load.
    dtypes : dict of str to str
        Contract dtype per preset column (``feature_dtypes``).
    categorical : sequence of str, optional
        The preset columns LightGBM treats as categorical (``categorical_columns``).
    census_year : int
        Census vintage of the population weights, logged to the run.
    window_half_width_days : int, optional
        Half width of the candidate window around D − 364.
    name : str, optional
        The strategy label when it differs from ``strategy_name``.
    **kwargs
        Forwarded to :class:`PresetLightGbmStrategy`.
    """

    #: The registry name; the run's label unless ``name`` is given.
    strategy_name: ClassVar[str] = "lightgbm_msm_popw_daytype_simday"
    #: The ``DayCalendar`` columns joined after the similar day's load, in
    #: feature order; empty here, set by the calendar variants.
    calendar_feature_cols: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        preset: Preset,
        features: FeatureFrame,
        weather_forecast: AreaWeatherForecast,
        day_calendar: DayCalendar,
        weather_observed: AreaObservedWeather,
        hourly_load: AreaHourlyLoad,
        *,
        dtypes: dict[str, str],
        categorical: Sequence[str] = (),
        census_year: int,
        window_half_width_days: int = SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
        name: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            TASK,
            preset,
            features,
            dtypes=dtypes,
            categorical=categorical,
            name=name or self.strategy_name,
            **kwargs,
        )
        extra = {
            SIMILAR_DAY_FEATURE: "float64",
            **{col: DAY_CALENDAR_FEATURE_DTYPES[col] for col in self.calendar_feature_cols},
        }
        self.feature_cols = (*preset.feature_cols, *extra)
        self.eval_set_cls = preset_eval_set_cls(TASK, preset, dtypes, extra_dtypes=extra)
        self.census_year = census_year
        self.day_calendar = day_calendar
        self.hourly_load = hourly_load
        self.selector = SimilarDaySelector(
            day_calendar,
            weather_forecast,
            weather_observed,
            hourly_load,
            half_width_days=window_half_width_days,
        )
        self._selections: dict[pd.Timestamp, pd.DataFrame] = {}

    def predict(self, target_date: pd.Timestamp, history: HalfHourlySeries) -> DayAheadForecast:
        """Fit the selector once on the visible history, then score the day.

        Parameters
        ----------
        target_date : pandas.Timestamp
        history : HalfHourlySeries

        Returns
        -------
        DayAheadForecast

        Raises
        ------
        ForecastUnavailableError
            If no training pair exists by the day's cutoff, or the parent
            cannot forecast the day.
        """
        target_date = pd.Timestamp(target_date).as_unit("ns")
        try:
            self.selector.ensure_fitted(history.df["trade_date"].max())
        except ValueError as exc:
            raise ForecastUnavailableError(f"{self.name}: {exc}") from exc
        forecast = super().predict(target_date, history)
        self._selections[target_date] = self.selector.select([target_date]).df
        return forecast

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """The preset's features, the selected similar day's load per period, then
        the calendar columns of ``calendar_feature_cols``.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code).
        history : pandas.DataFrame
            Unused: the frame holds the preset's features.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's ``feature_cols`` (the similar
            day's load NaN on days that cannot be scored, the calendar columns
            NaN on days outside the calendar).
        """
        featured = super()._add_features(featured, history)
        selection = self.selector.select(featured["trade_date"].unique())
        featured = join_similar_day_load(
            featured, selection, self.hourly_load, name=SIMILAR_DAY_FEATURE
        )
        if self.calendar_feature_cols:
            featured = join_day_calendar(
                featured, self.day_calendar, cols=self.calendar_feature_cols
            )
        return featured

    def _extra_params(self) -> dict[str, object]:
        """The census vintage, the window, the parts and the fitted weights next to
        the preset params.

        Returns
        -------
        dict of str to object
        """
        first = self.selector.first_scorable_day
        start, end = self.selector.hourly_load_span
        return {
            **super()._extra_params(),
            "population_weight_census_year": self.census_year,
            "similar_day_center_lag_days": self.selector.center_lag_days,
            "similar_day_window_half_width_days": self.selector.half_width_days,
            "similar_day_components": ",".join(SIMILAR_DAY_COMPONENTS),
            **self.selector.weights.as_params(),
            "similar_day_first_selectable_day": "none" if first is None else str(first.date()),
            "similar_day_hourly_load_span": f"{start.date()}..{end.date()}",
            "similar_day_periods_per_hour": PERIODS_PER_HOUR,
        }

    def diagnostics(self, history: HalfHourlySeries, run: BacktestRun) -> dict[str, pd.DataFrame]:
        """The forecast days' selections and the retrieval check, with four metrics logged.

        Parameters
        ----------
        history : HalfHourlySeries
            Unused: the selector holds the hourly load itself.
        run : BacktestRun

        Returns
        -------
        dict of str to pandas.DataFrame
            ``similar_day_selection`` and ``similar_day_retrieval``; empty
            when no forecast day was recorded.
        """
        days = [
            d for d in pd.to_datetime(run.result.df["trade_date"].unique()) if d in self._selections
        ]
        if not days:
            return {}
        selection = SimilarDaySelection.from_df(
            pd.concat([self._selections[d] for d in sorted(days)], ignore_index=True)
        )
        retrieval = self.selector.retrieval(selection)
        mlflow.log_metrics(
            {
                key: value
                for key, value in retrieval_metrics(retrieval).items()
                if not math.isnan(value)
            }
        )
        return {"similar_day_selection": selection.df, "similar_day_retrieval": retrieval.df}


class LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy(
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
):
    """The similar-day strategy plus the delivery day's ten calendar attributes
    from ``dim_date`` (``DAY_CALENDAR_FEATURE_COLS``), as plain numeric columns.

    Experiment E-001 of docs/research/demand/R-005-calendar-features.md,
    rejected; kept as a reference strategy. Same constructor and inputs.
    """

    strategy_name = "lightgbm_msm_popw_daytype_simday_calendar"
    calendar_feature_cols = DAY_CALENDAR_FEATURE_COLS


class LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy(
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
):
    """The similar-day strategy plus ``holiday_degree`` alone.

    Experiment E-002 of docs/research/demand/R-005-calendar-features.md,
    rejected; kept as a reference strategy. Same constructor and inputs.
    """

    strategy_name = "lightgbm_msm_popw_daytype_simday_holidaydegree"
    calendar_feature_cols = HOLIDAY_DEGREE_FEATURE_COLS


class LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy(
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
):
    """The similar-day strategy plus the days since and until the nearest holiday.

    Experiment E-003 of docs/research/demand/R-005-calendar-features.md,
    rejected; kept as a reference strategy. Same constructor and inputs.
    """

    strategy_name = "lightgbm_msm_popw_daytype_simday_holidaydistance"
    calendar_feature_cols = HOLIDAY_DISTANCE_FEATURE_COLS


class LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy(
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
):
    """The similar-day strategy plus the six calendar counts.

    Experiment E-004 of docs/research/demand/R-005-calendar-features.md,
    rejected; kept as a reference strategy. Same constructor and inputs.
    """

    strategy_name = "lightgbm_msm_popw_daytype_simday_calendarcounts"
    calendar_feature_cols = CALENDAR_COUNT_FEATURE_COLS
