"""Naive baseline strategies."""

from __future__ import annotations

import logging

import mlflow
import numpy as np
import pandas as pd
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.tracking import evaluate_regressor
from power_market_analytics.tasks.spot_price.features import join_lag
from power_market_analytics.tasks.spot_price.frames import (
    BacktestResult,
    DayAheadForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.strategies.base import ForecastStrategy

logger = logging.getLogger(__name__)

FEATURE_COLS = ("lag_1d_price",)
TARGET_COL = "actual_price_jpy_kwh"


class PreviousDayEvalSet(DomainFrame):
    """Design matrix for evaluating :class:`PreviousDayStrategy` with MLflow.

    One row per forecast point, holding the features knowable at 9:55 JST on
    D-1 alongside the realized price. ``lag_1d_price`` is by construction the
    value :meth:`PreviousDayStrategy.predict` returns, so MLflow's
    ``mean_absolute_error`` over this frame matches the backtest MAE.

    Grain: (trade_date, time_code).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "lag_1d_price": "float64",
        TARGET_COL: "float64",
    }
    keys = ["trade_date", "time_code"]
    non_null_cols = [*FEATURE_COLS, TARGET_COL]

    def to_eval_frame(self) -> pd.DataFrame:
        """Feature columns plus the target, in the layout ``mlflow`` expects.

        Drops the grain columns ``trade_date`` and ``time_code``, since
        MLflow treats every non-target column as a feature.

        Returns
        -------
        pandas.DataFrame
        """
        return self.df[[*FEATURE_COLS, TARGET_COL]].astype("float64")


class PreviousDayModel(mlflow.pyfunc.PythonModel):
    """Pyfunc view of :class:`PreviousDayStrategy` over a feature matrix.

    MLflow's evaluation API explains a function of features, while the
    strategy is expressed as a function of a price history. This wrapper
    restates the same rule — take D-1's price for the same time code — so
    that SHAP has a callable to perturb.
    """

    def predict(
        self,
        context: object,
        model_input: pd.DataFrame,
        params: dict | None = None,
    ) -> np.ndarray:
        """Return the previous day's price for each row.

        Parameters
        ----------
        context : object
            Unused pyfunc model context.
        model_input : pandas.DataFrame
            Rows with at least the ``lag_1d_price`` column.
        params : dict, optional
            Unused inference parameters.

        Returns
        -------
        numpy.ndarray
        """
        return model_input["lag_1d_price"].to_numpy()


class PreviousDayStrategy(ForecastStrategy):
    """Forecast each time code with the same time code's price from D-1."""

    name = "previous_day"

    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> DayAheadForecast:
        """Copy the previous delivery day's 48 prices onto the target day.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : SpotPrices
            Price history through D-1.

        Returns
        -------
        DayAheadForecast

        Raises
        ------
        ValueError
            If the previous day is not fully present in the history.
        """
        previous_day = target_date - pd.Timedelta(days=1)
        prev = history.df[history.df["trade_date"] == previous_day]
        if len(prev) == 0:
            raise ValueError(f"{self.name}: no history for previous day {previous_day.date()}")
        forecast = prev.assign(trade_date=target_date).rename(
            columns={"price_jpy_kwh": "forecast_price_jpy_kwh"}
        )
        return DayAheadForecast.from_df(forecast)

    def build_eval_set(
        self,
        prices: SpotPrices,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        result: BacktestResult | None = None,
    ) -> PreviousDayEvalSet:
        """Assemble the MLflow design matrix for a backtest window.

        Lags are joined on calendar date rather than row position so that
        gaps in the history (e.g. Hokkaido's 2018 suspension) shift no rows.
        Points missing the lag — the first day of history, or the day after
        a gap — are dropped, since MLflow needs a complete numeric matrix.

        Parameters
        ----------
        prices : SpotPrices
            Full price history; must cover ``start_date`` minus 1 day.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.
        result : BacktestResult, optional
            Unused: the logged pyfunc model restates this strategy's rule
            exactly, so evaluation re-scores the model instead of replaying
            the backtest's predictions.

        Returns
        -------
        PreviousDayEvalSet

        Raises
        ------
        ValueError
            If no complete rows remain in the window.
        """
        df = prices.df
        featured = df.rename(columns={"price_jpy_kwh": TARGET_COL}).pipe(
            join_lag, df, days=1, name="lag_1d_price"
        )

        window = featured[featured["trade_date"].between(start_date, end_date)]
        complete = window.dropna(subset=[*FEATURE_COLS, TARGET_COL])
        n_dropped = len(window) - len(complete)
        if n_dropped:
            logger.info(
                "%s eval set: dropped %d of %d rows with incomplete lags",
                self.name,
                n_dropped,
                len(window),
            )
        if complete.empty:
            raise ValueError(
                f"{self.name}: no complete feature rows between "
                f"{start_date.date()} and {end_date.date()}"
            )
        logger.info(
            "%s eval set: %d rows, %d features", self.name, len(complete), len(FEATURE_COLS)
        )
        return PreviousDayEvalSet.from_df(complete)

    def evaluate(
        self,
        eval_set: PreviousDayEvalSet,
        **kwargs: object,
    ) -> EvaluationResult:
        """Log this strategy as a pyfunc model and evaluate it with MLflow.

        Must be called inside an active MLflow run; the model, metrics and
        SHAP plots all land in that run. With ``lag_1d_price`` as the only
        feature, SHAP attributes the whole output to it — trivially true for
        a naive baseline, but it doubles as a check that the plumbing works
        before a real model is plugged in.

        Parameters
        ----------
        eval_set : PreviousDayEvalSet
            Design matrix from :meth:`build_eval_set`.
        **kwargs
            Forwarded to
            :func:`power_market_analytics.common.tracking.evaluate_regressor`
            (``explainability_algorithm``, ``explainability_nsamples``,
            ``log_explainer``).

        Returns
        -------
        mlflow.models.EvaluationResult
        """
        eval_frame = eval_set.to_eval_frame()
        model_info = mlflow.pyfunc.log_model(
            name=f"{self.name}_model",
            python_model=PreviousDayModel(),
            input_example=eval_frame.drop(columns=[TARGET_COL]).head(),
        )
        return evaluate_regressor(
            model_info.model_uri,
            eval_frame,
            targets=TARGET_COL,
            **kwargs,
        )
