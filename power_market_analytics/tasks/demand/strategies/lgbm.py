"""LightGBM strategies for area demand: the calendar + temperature + D-7 lag
baseline, the same model plus the MSM forecast temperature at the representative
station, the same again with that forecast population-weighted over the area's
stations, that one plus the delivery day's type as a categorical, and that one
plus the load of a learned similar day one year earlier."""

from __future__ import annotations

import math
from typing import ClassVar

import mlflow
import pandas as pd

from power_market_analytics.forecasting.backtest import BacktestRun
from power_market_analytics.forecasting.features import join_lag
from power_market_analytics.forecasting.frames import GRAIN_COLS, DayAheadForecast, HalfHourlySeries
from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.forecasting.strategy import ForecastUnavailableError
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.features import (
    DAY_TYPE_FEATURE,
    FORECAST_TEMPERATURE_FEATURE,
    POPW_FORECAST_TEMPERATURE_FEATURE,
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    join_day_type,
    join_forecast_temperature,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import (
    DAY_TYPE_LEVELS,
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaTemperature,
    AreaTemperatureForecast,
    AreaWeatherForecast,
    DayCalendar,
    DayTypeCalendar,
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

DEMAND_LAG_FEATURE = "lag_7d_demand_kwh"
FEATURE_COLS = (*CALENDAR_FEATURE_COLS, TEMPERATURE_FEATURE, DEMAND_LAG_FEATURE)
MSM_FEATURE_COLS = (*FEATURE_COLS, FORECAST_TEMPERATURE_FEATURE)
MSM_POPW_FEATURE_COLS = (*FEATURE_COLS, POPW_FORECAST_TEMPERATURE_FEATURE)
MSM_POPW_DAY_TYPE_FEATURE_COLS = (*MSM_POPW_FEATURE_COLS, DAY_TYPE_FEATURE)
SIMILAR_DAY_FEATURE_COLS = (*MSM_POPW_DAY_TYPE_FEATURE_COLS, SIMILAR_DAY_FEATURE)
TARGET_COL = TASK.actual_col
FORECAST_COL = TASK.forecast_col


class DemandLightGbmEvalSet(LightGbmEvalSetBase):
    """Design matrix for evaluating :class:`LightGbmStrategy` with MLflow.

    One row per forecast point: the features knowable at 09:30 JST on D-1,
    the realized demand and the walk-forward forecast for that point.

    Grain: (trade_date, time_code).
    """

    feature_cols = FEATURE_COLS
    target_col = TARGET_COL
    forecast_col = FORECAST_COL
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    keys = list(GRAIN_COLS)
    non_null_cols = [*FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmStrategy(SlidingWindowLightGbmStrategy):
    """LightGBM regressor on time code, month, day of week, the recency-weighted
    same-hour temperature over D-8..D-2 and the D-7 demand at the same period.

    Both task-specific features are knowable at 09:30 on D-1: D-7 <= D-2 and
    the temperature window ends on D-2. Model parameters and refit cadence
    are :class:`SlidingWindowLightGbmStrategy`'s (shared with spot_price for
    comparability).

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly temperature at the area's representative JMA station.
    **kwargs
        Forwarded to :class:`SlidingWindowLightGbmStrategy`.
    """

    name = "lightgbm"
    task = TASK
    feature_cols = FEATURE_COLS
    eval_set_cls = DemandLightGbmEvalSet
    # Extra demand history the training window's first row needs: >= the 7-day lag (8 is a
    # safe superset; the temperature frame is not sliced by this).
    lookback_days = 8

    def __init__(self, temperature: AreaTemperature, **kwargs) -> None:
        super().__init__(**kwargs)
        self.temperature = temperature

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the D-7 demand lag and the recency-weighted temperature.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
        """
        featured = join_lag(
            featured, history, value_col=self.task.value_col, days=7, name=DEMAND_LAG_FEATURE
        )
        return recency_weighted_temperature(featured, self.temperature)

    def _extra_params(self) -> dict[str, object]:
        """Log the temperature window next to the ``lgbm_*`` params.

        Returns
        -------
        dict of str to object
        """
        return {
            "temperature_lag_days": ",".join(str(k) for k in TEMPERATURE_LAG_DAYS),
            "temperature_half_life_days": TEMPERATURE_HALF_LIFE_DAYS,
        }


class DemandLightGbmMsmEvalSet(DemandLightGbmEvalSet):
    """Design matrix for :class:`LightGbmMsmStrategy`: the baseline features plus
    the MSM forecast temperature for the delivery-day hour of each period.

    Grain: (trade_date, time_code).
    """

    feature_cols = MSM_FEATURE_COLS
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        FORECAST_TEMPERATURE_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*MSM_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmMsmStrategy(LightGbmStrategy):
    """:class:`LightGbmStrategy` plus the MSM forecast temperature for delivery day D.

    Experiment E-001 of docs/research/demand/R-001-forecast-temperature.md:
    the JMA MSM point forecast at the area's representative station (the
    12 UTC run of D-2, available well before the 09:30 JST D-1 issue time)
    is joined to D's rows at the hour containing each period, adding
    ``forecast_temperature_c`` to the feature set. Model parameters, refit
    cadence and the baseline features are unchanged.

    Training rows without a forecast are dropped, so this strategy's training
    set starts on the first forecast day however long the demand history is;
    a matched baseline must be run with the same ``train_start_date``
    explicitly when the forecast history is the shorter of the two.

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly observed temperature at the area's representative JMA station.
    temperature_forecast : AreaTemperatureForecast
        Hourly MSM forecast temperature at the same station, by delivery day.
    **kwargs
        Forwarded to :class:`LightGbmStrategy`.
    """

    name = "lightgbm_msm"
    feature_cols = MSM_FEATURE_COLS
    # Declared at the demand eval-set base so a subclass may swap in a sibling
    # design matrix (the population-weighted one) rather than a subclass of this one.
    eval_set_cls: ClassVar[type[DemandLightGbmEvalSet]] = DemandLightGbmMsmEvalSet
    #: Column the delivery-day forecast temperature is attached as.
    forecast_feature: ClassVar[str] = FORECAST_TEMPERATURE_FEATURE

    def __init__(
        self,
        temperature: AreaTemperature,
        temperature_forecast: AreaTemperatureForecast,
        **kwargs,
    ) -> None:
        super().__init__(temperature, **kwargs)
        self.temperature_forecast = temperature_forecast

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the baseline features, then the delivery day's forecast temperature.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's ``feature_cols`` (the forecast
            temperature NaN on hours without a forecast).
        """
        featured = super()._add_features(featured, history)
        return join_forecast_temperature(
            featured, self.temperature_forecast, name=self.forecast_feature
        )


class DemandLightGbmMsmPopWeightedEvalSet(DemandLightGbmEvalSet):
    """Design matrix for :class:`LightGbmMsmPopWeightedStrategy`: the baseline
    features plus the population-weighted MSM forecast temperature.

    Grain: (trade_date, time_code).
    """

    feature_cols = MSM_POPW_FEATURE_COLS
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        POPW_FORECAST_TEMPERATURE_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*MSM_POPW_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmMsmPopWeightedStrategy(LightGbmMsmStrategy):
    """:class:`LightGbmMsmStrategy` with the delivery-day MSM forecast temperature
    population-weighted over the area's staffed stations instead of taken at the
    single representative station.

    Experiment E-001 of docs/research/demand/R-002-population-weighted-temperature.md:
    ``popw_forecast_temperature_c`` replaces ``forecast_temperature_c`` — the
    weights are each station's share of the area's census population
    (``fct_census_population_jma_station``, one vintage applied to the whole
    history). Everything else (model parameters, refit cadence, the baseline
    features including the single-station observed temperature) is unchanged.

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly observed temperature at the area's representative JMA station.
    temperature_forecast : AreaTemperatureForecast
        Population-weighted hourly MSM forecast temperature for the area, by
        delivery day (``load_area_temperature_forecast_population_weighted``).
    census_year : int
        Census vintage of the weights, logged to the MLflow run.
    **kwargs
        Forwarded to :class:`LightGbmMsmStrategy`.
    """

    name = "lightgbm_msm_popw"
    feature_cols = MSM_POPW_FEATURE_COLS
    eval_set_cls = DemandLightGbmMsmPopWeightedEvalSet
    forecast_feature = POPW_FORECAST_TEMPERATURE_FEATURE

    def __init__(
        self,
        temperature: AreaTemperature,
        temperature_forecast: AreaTemperatureForecast,
        *,
        census_year: int,
        **kwargs,
    ) -> None:
        super().__init__(temperature, temperature_forecast, **kwargs)
        self.census_year = census_year

    def _extra_params(self) -> dict[str, object]:
        """Log the weights' census vintage next to the temperature-window params.

        Returns
        -------
        dict of str to object
        """
        return {**super()._extra_params(), "population_weight_census_year": self.census_year}


class DemandLightGbmMsmPopWeightedDayTypeEvalSet(DemandLightGbmMsmPopWeightedEvalSet):
    """Design matrix for :class:`LightGbmMsmPopWeightedDayTypeStrategy`: the
    population-weighted design matrix plus the day-type code.

    Grain: (trade_date, time_code).
    """

    feature_cols = MSM_POPW_DAY_TYPE_FEATURE_COLS
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        POPW_FORECAST_TEMPERATURE_FEATURE: "float64",
        DAY_TYPE_FEATURE: "int64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*MSM_POPW_DAY_TYPE_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmMsmPopWeightedDayTypeStrategy(LightGbmMsmPopWeightedStrategy):
    """:class:`LightGbmMsmPopWeightedStrategy` plus the delivery day's type as a
    LightGBM categorical feature.

    Experiment E-001 of docs/research/demand/R-003-day-type-feature.md:
    ``day_type`` — 0 = Weekday, 1 = Weekend, 2 = Holiday per ``dim_date``
    (``DAY_TYPE_LEVELS``; a holiday whatever weekday it falls on, the customary
    年末年始 / ゴールデンウィーク / お盆 days included) — joins the
    population-weighted feature set and is declared to LightGBM as a
    categorical column, so a tree splits on the category set directly rather
    than on an ordinal threshold. Everything else (model parameters, refit
    cadence, the other features) is unchanged. Training rows without a
    calendar day are dropped and a target day without one is unforecastable.

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly observed temperature at the area's representative JMA station.
    temperature_forecast : AreaTemperatureForecast
        Population-weighted hourly MSM forecast temperature for the area, by
        delivery day.
    day_types : DayTypeCalendar
        Day type of every calendar day (``load_day_types``).
    census_year : int
        Census vintage of the population weights, logged to the MLflow run.
    **kwargs
        Forwarded to :class:`LightGbmMsmPopWeightedStrategy`.
    """

    name = "lightgbm_msm_popw_daytype"
    feature_cols = MSM_POPW_DAY_TYPE_FEATURE_COLS
    categorical_feature_cols = (DAY_TYPE_FEATURE,)
    eval_set_cls = DemandLightGbmMsmPopWeightedDayTypeEvalSet

    def __init__(
        self,
        temperature: AreaTemperature,
        temperature_forecast: AreaTemperatureForecast,
        day_types: DayTypeCalendar,
        *,
        census_year: int,
        **kwargs,
    ) -> None:
        super().__init__(temperature, temperature_forecast, census_year=census_year, **kwargs)
        self.day_types = day_types

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the population-weighted strategy's features, then the day-type code.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's ``feature_cols`` (the day type
            NaN on days the calendar lacks).
        """
        featured = super()._add_features(featured, history)
        return join_day_type(featured, self.day_types, name=DAY_TYPE_FEATURE)

    def _extra_params(self) -> dict[str, object]:
        """Log the day-type coding next to the inherited params.

        Returns
        -------
        dict of str to object
        """
        levels = ",".join(f"{code}={level}" for code, level in enumerate(DAY_TYPE_LEVELS))
        return {**super()._extra_params(), "day_type_levels": levels}


class DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet(
    DemandLightGbmMsmPopWeightedDayTypeEvalSet
):
    """Design matrix for :class:`LightGbmMsmPopWeightedDayTypeSimilarDayStrategy`:
    the day-type design matrix plus the similar day's load per period.

    Grain: (trade_date, time_code).
    """

    feature_cols = SIMILAR_DAY_FEATURE_COLS
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        POPW_FORECAST_TEMPERATURE_FEATURE: "float64",
        DAY_TYPE_FEATURE: "int64",
        SIMILAR_DAY_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*SIMILAR_DAY_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(LightGbmMsmPopWeightedDayTypeStrategy):
    """:class:`LightGbmMsmPopWeightedDayTypeStrategy` plus the load of a learned
    similar day one year earlier (``similar_day_demand_kwh``).

    Experiment E-002 of docs/research/demand/R-004-prior-year-load-lag.md. A
    :class:`SimilarDaySelector` picks, for every delivery day, the nearest
    day in D − 364 ± 30 under a seven-part weighted distance whose weights are
    fitted once per run — on the first :meth:`predict`, over every pair whose
    target day the strategy may see — then frozen. The chosen day's でんき予報
    hourly load, halved per period, is the feature. Training rows and target
    days that cannot be scored get NaN and are dropped or skipped as usual.

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly observed temperature at the representative station.
    weather_forecast : AreaWeatherForecast
        Population-weighted MSM forecast of temperature, humidity and rain by
        delivery day; its temperature is the parent's forecast feature.
    day_calendar : DayCalendar
        Holiday attributes of every day; its day types are the parent's.
    weather_observed : AreaObservedWeather
        Population-weighted observed temperature, humidity and rain.
    hourly_load : AreaHourlyLoad
        The でんき予報 hourly load.
    census_year : int
        Census vintage of the population weights, logged to the run.
    window_half_width_days : int, optional
        Half width of the candidate window around D − 364.
    **kwargs
        Forwarded to the parent.
    """

    name = "lightgbm_msm_popw_daytype_simday"
    feature_cols = SIMILAR_DAY_FEATURE_COLS
    eval_set_cls = DemandLightGbmMsmPopWeightedDayTypeSimilarDayEvalSet

    def __init__(
        self,
        temperature: AreaTemperature,
        weather_forecast: AreaWeatherForecast,
        day_calendar: DayCalendar,
        weather_observed: AreaObservedWeather,
        hourly_load: AreaHourlyLoad,
        *,
        census_year: int,
        window_half_width_days: int = SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS,
        **kwargs,
    ) -> None:
        super().__init__(
            temperature,
            weather_forecast.temperature_forecast(),
            day_calendar.day_types(),
            census_year=census_year,
            **kwargs,
        )
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
        """The parent's features, then the selected similar day's load per period.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's ``feature_cols`` (the similar
            day's load NaN on days that cannot be scored).
        """
        featured = super()._add_features(featured, history)
        selection = self.selector.select(featured["trade_date"].unique())
        return join_similar_day_load(
            featured, selection, self.hourly_load, name=SIMILAR_DAY_FEATURE
        )

    def _extra_params(self) -> dict[str, object]:
        """Log the window, the parts and the fitted weights next to the inherited params.

        Returns
        -------
        dict of str to object
        """
        first = self.selector.first_scorable_day
        start, end = self.selector.hourly_load_span
        return {
            **super()._extra_params(),
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
