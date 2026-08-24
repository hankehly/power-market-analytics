"""LightGBM strategies for area demand: the calendar + temperature + D-7 lag
baseline, the same model plus the MSM forecast temperature at the representative
station, and the same again with that forecast population-weighted over the
area's stations."""

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
    FORECAST_TEMPERATURE_FEATURE,
    POPW_FORECAST_TEMPERATURE_FEATURE,
    TEMPERATURE_FEATURE,
    TEMPERATURE_HALF_LIFE_DAYS,
    TEMPERATURE_LAG_DAYS,
    join_forecast_temperature,
    recency_weighted_temperature,
)
from power_market_analytics.tasks.demand.frames import AreaTemperature, AreaTemperatureForecast

DEMAND_LAG_FEATURE = "lag_7d_demand_kwh"
FEATURE_COLS = (*CALENDAR_FEATURE_COLS, TEMPERATURE_FEATURE, DEMAND_LAG_FEATURE)
MSM_FEATURE_COLS = (*FEATURE_COLS, FORECAST_TEMPERATURE_FEATURE)
MSM_POPW_FEATURE_COLS = (*FEATURE_COLS, POPW_FORECAST_TEMPERATURE_FEATURE)
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
