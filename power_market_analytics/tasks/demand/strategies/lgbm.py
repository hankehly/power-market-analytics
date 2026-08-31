"""LightGBM strategies for area demand: the calendar + temperature + D-7 lag
baseline, the same model plus the MSM forecast temperature at the representative
station, the same again with that forecast population-weighted over the area's
stations, that one plus the delivery day's type as a categorical, and that one
plus the year-ago load on the day's prior-year reference date."""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from power_market_analytics.forecasting.features import join_lag
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.features import (
    DAY_TYPE_FEATURE,
    FORECAST_TEMPERATURE_FEATURE,
    LAG_1Y_FEATURE,
    PERIODS_PER_HOUR,
    POPW_FORECAST_TEMPERATURE_FEATURE,
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    join_day_type,
    join_forecast_temperature,
    join_prior_year_load,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import (
    DAY_TYPE_LEVELS,
    PRIOR_YEAR_REFERENCE_RULES,
    AreaHourlyLoad,
    AreaTemperature,
    AreaTemperatureForecast,
    DayTypeCalendar,
    PriorYearCalendar,
)

DEMAND_LAG_FEATURE = "lag_7d_demand_kwh"
FEATURE_COLS = (*CALENDAR_FEATURE_COLS, TEMPERATURE_FEATURE, DEMAND_LAG_FEATURE)
MSM_FEATURE_COLS = (*FEATURE_COLS, FORECAST_TEMPERATURE_FEATURE)
MSM_POPW_FEATURE_COLS = (*FEATURE_COLS, POPW_FORECAST_TEMPERATURE_FEATURE)
MSM_POPW_DAY_TYPE_FEATURE_COLS = (*MSM_POPW_FEATURE_COLS, DAY_TYPE_FEATURE)
MSM_POPW_DAY_TYPE_LAG1Y_FEATURE_COLS = (*MSM_POPW_DAY_TYPE_FEATURE_COLS, LAG_1Y_FEATURE)
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


class DemandLightGbmMsmPopWeightedDayTypeLag1yEvalSet(DemandLightGbmMsmPopWeightedDayTypeEvalSet):
    """Design matrix for :class:`LightGbmMsmPopWeightedDayTypeLag1yStrategy`: the
    day-type design matrix plus the year-ago load.

    Grain: (trade_date, time_code).
    """

    feature_cols = MSM_POPW_DAY_TYPE_LAG1Y_FEATURE_COLS
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        TEMPERATURE_FEATURE: "float64",
        DEMAND_LAG_FEATURE: "float64",
        POPW_FORECAST_TEMPERATURE_FEATURE: "float64",
        DAY_TYPE_FEATURE: "int64",
        LAG_1Y_FEATURE: "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*MSM_POPW_DAY_TYPE_LAG1Y_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmMsmPopWeightedDayTypeLag1yStrategy(LightGbmMsmPopWeightedDayTypeStrategy):
    """:class:`LightGbmMsmPopWeightedDayTypeStrategy` plus the year-ago load.

    Experiment E-001 of docs/research/demand/R-004-prior-year-load-lag.md:
    ``lag_1y_demand_kwh`` — the hourly load on the delivery day's
    ``dim_date.prior_year_reference_date`` (the same weekday 52 weeks back,
    shifted a week when that day is a holiday; the same-named holiday a year
    earlier for a holiday) at the hour containing the period, spread evenly
    over the hour's two periods so it sits on the target's scale — joins the
    day-type feature set. The load is the TSO でんき予報 hourly series
    (``fct_area_power_usage_hourly``; Tokyo from 2016-04-01), the single
    source for the whole history by research decision, which is what gives
    the first year of A-1 training rows (2022-04 onward) a year-ago value.
    Everything else (model parameters, refit cadence, the other features, the
    categorical day type) is unchanged. A training row without a year-ago
    hour is dropped and a target day without one is unforecastable.

    Parameters
    ----------
    temperature : AreaTemperature
        Hourly observed temperature at the area's representative JMA station.
    temperature_forecast : AreaTemperatureForecast
        Population-weighted hourly MSM forecast temperature for the area, by
        delivery day.
    day_types : DayTypeCalendar
        Day type of every calendar day (``load_day_types``).
    prior_year_calendar : PriorYearCalendar
        Prior-year reference of every calendar day (``load_prior_year_calendar``).
    hourly_load : AreaHourlyLoad
        Hourly load history of the area (``load_area_hourly_load``).
    census_year : int
        Census vintage of the population weights, logged to the MLflow run.
    **kwargs
        Forwarded to :class:`LightGbmMsmPopWeightedDayTypeStrategy`.
    """

    name = "lightgbm_msm_popw_daytype_lag1y"
    feature_cols = MSM_POPW_DAY_TYPE_LAG1Y_FEATURE_COLS
    eval_set_cls = DemandLightGbmMsmPopWeightedDayTypeLag1yEvalSet

    def __init__(
        self,
        temperature: AreaTemperature,
        temperature_forecast: AreaTemperatureForecast,
        day_types: DayTypeCalendar,
        prior_year_calendar: PriorYearCalendar,
        hourly_load: AreaHourlyLoad,
        *,
        census_year: int,
        **kwargs,
    ) -> None:
        super().__init__(
            temperature, temperature_forecast, day_types, census_year=census_year, **kwargs
        )
        self.prior_year_calendar = prior_year_calendar
        self.hourly_load = hourly_load

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the day-type strategy's features, then the year-ago load.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Demand history in the ``AreaDemand`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's ``feature_cols`` (the year-ago
            load NaN where the day has no reference or the reference hour no
            load).
        """
        featured = super()._add_features(featured, history)
        return join_prior_year_load(
            featured, self.prior_year_calendar, self.hourly_load, name=LAG_1Y_FEATURE
        )

    def _extra_params(self) -> dict[str, object]:
        """Log the calendar's rule mix and the hourly history's span next to the
        inherited params.

        Returns
        -------
        dict of str to object
        """
        rules = self.prior_year_calendar.df["prior_year_reference_rule"].value_counts()
        load_dates = self.hourly_load.df["load_date"]
        return {
            **super()._extra_params(),
            "prior_year_reference_rules": ",".join(
                f"{rule}={int(rules.get(rule, 0))}" for rule in PRIOR_YEAR_REFERENCE_RULES
            ),
            "lag_1y_periods_per_hour": PERIODS_PER_HOUR,
            "lag_1y_hourly_load_span": f"{load_dates.min().date()}..{load_dates.max().date()}",
        }
