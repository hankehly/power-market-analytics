"""Gradient-boosted tree strategies over calendar, lag and OCCTO features."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.forecasting.features import join_lag
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.forecasting.lgbm import (
    CALENDAR_FEATURE_COLS,
    LightGbmEvalSetBase,
    SlidingWindowLightGbmStrategy,
)
from power_market_analytics.tasks.spot_price import TASK
from power_market_analytics.tasks.spot_price.frames import OcctoDemandForecast

BASE_FEATURE_COLS: tuple[str, ...] = (*CALENDAR_FEATURE_COLS, "lag_1d_price")
OCCTO_FEATURE_COLS: tuple[str, ...] = (
    "max_demand_hour_ending",
    "max_demand_mw",
    "max_supply_capacity_mw",
)
TARGET_COL = TASK.actual_col
FORECAST_COL = TASK.forecast_col


class LightGbmEvalSet(LightGbmEvalSetBase):
    """Design matrix for evaluating :class:`LightGbmStrategy` with MLflow.

    One row per forecast point, holding the features knowable at 9:55 JST on
    D-1, the realized price, and the walk-forward forecast the backtest
    produced for that point. Unlike the naive eval set, ``time_code`` is a
    model feature here as well as a grain column.

    Grain: (trade_date, time_code).
    """

    feature_cols = BASE_FEATURE_COLS
    target_col = TARGET_COL
    forecast_col = FORECAST_COL
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        "lag_1d_price": "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    keys = list(GRAIN_COLS)
    non_null_cols = [*BASE_FEATURE_COLS, TARGET_COL, FORECAST_COL]


class LightGbmOcctoEvalSet(LightGbmEvalSet):
    """Design matrix for :class:`LightGbmOcctoStrategy`: the base features
    plus the OCCTO 翌々日 peak-demand/supply forecast for the delivery day.

    Grain: (trade_date, time_code).
    """

    feature_cols = (*BASE_FEATURE_COLS, *OCCTO_FEATURE_COLS)
    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        "lag_1d_price": "float64",
        "max_demand_hour_ending": "int64",
        "max_demand_mw": "int64",
        "max_supply_capacity_mw": "int64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    non_null_cols = [*feature_cols, TARGET_COL, FORECAST_COL]


class LightGbmStrategy(SlidingWindowLightGbmStrategy):
    """LightGBM regressor on time code, month, day of week and the 1-day lag.

    See :class:`SlidingWindowLightGbmStrategy` for the refit schedule, the
    TreeSHAP records and the evaluation. A subclass that adds per-day
    exogenous features — see :class:`LightGbmOcctoStrategy` — overrides
    :meth:`_join_daily_features` plus the ``feature_cols`` /
    ``eval_set_cls`` class attributes.
    """

    name = "lightgbm"
    task = TASK
    feature_cols = BASE_FEATURE_COLS
    eval_set_cls = LightGbmEvalSet
    # The 1-day lag needs one extra day before the training window.
    lookback_days = 1

    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the D-1 price lag, then any per-day exogenous features.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the calendar features.
        history : pandas.DataFrame
            Price history in the ``SpotPrices`` layout.

        Returns
        -------
        pandas.DataFrame
        """
        featured = join_lag(
            featured, history, value_col=self.task.value_col, days=1, name="lag_1d_price"
        )
        return self._join_daily_features(featured)

    def _join_daily_features(self, featured: pd.DataFrame) -> pd.DataFrame:
        """Hook for per-delivery-day exogenous features; identity here.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the base features.

        Returns
        -------
        pandas.DataFrame
        """
        return featured


class LightGbmOcctoStrategy(LightGbmStrategy):
    """:class:`LightGbmStrategy` plus OCCTO 翌々日 peak-demand/supply features.

    Experiment E-001 of docs/research/spot_price/R-001-supply-demand-tightness.md: the
    OCCTO forecast for delivery day D (published D-2 ~17:45 JST, before the
    D-1 09:55 cutoff) is joined to D's 48 rows, adding
    ``max_demand_hour_ending``, ``max_demand_mw`` and
    ``max_supply_capacity_mw`` to the feature set. Model parameters, refit
    cadence and the base features are unchanged.

    Because rows without an OCCTO forecast are dropped from training, this
    strategy's training set starts on the first OCCTO day (2024-04-01)
    however long the price history is; a matched baseline must be run with
    the same ``train_start_date`` explicitly.

    Parameters
    ----------
    occto : OcctoDemandForecast
        OCCTO forecasts for the same area as the prices being forecast.
    **kwargs
        Forwarded to :class:`LightGbmStrategy`.
    """

    name = "lightgbm_occto"
    feature_cols = (*BASE_FEATURE_COLS, *OCCTO_FEATURE_COLS)
    eval_set_cls = LightGbmOcctoEvalSet

    def __init__(self, occto: OcctoDemandForecast, **kwargs) -> None:
        super().__init__(**kwargs)
        self.occto = occto

    def _join_daily_features(self, featured: pd.DataFrame) -> pd.DataFrame:
        """Left-join the delivery day's OCCTO forecast onto every row.

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) with the base features.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus ``OCCTO_FEATURE_COLS`` (NaN on days without a
            forecast).
        """
        return featured.merge(self.occto.df, how="left", on="trade_date", validate="many_to_one")
