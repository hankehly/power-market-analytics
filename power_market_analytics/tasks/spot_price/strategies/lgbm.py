"""Gradient-boosted tree strategy over calendar and lag features."""

from __future__ import annotations

import logging

import lightgbm
import matplotlib.pyplot as plt
import mlflow
import pandas as pd
import shap
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.tracking import evaluate_predictions
from power_market_analytics.tasks.spot_price.features import join_lag
from power_market_analytics.tasks.spot_price.frames import (
    BacktestResult,
    DayAheadForecast,
    SpotPrices,
)
from power_market_analytics.tasks.spot_price.strategies.base import ForecastStrategy

logger = logging.getLogger(__name__)

FEATURE_COLS = ("time_code", "month", "day_of_week", "lag_1d_price")
TARGET_COL = "actual_price_jpy_kwh"
FORECAST_COL = "forecast_price_jpy_kwh"
SHAP_COLS = tuple(f"shap_{col}" for col in FEATURE_COLS)

# Modest, fixed hyperparameters: four low-cardinality features do not warrant
# tuning machinery yet. Logged to the MLflow run in `evaluate`.
LGBM_PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "random_state": 0,
    "verbose": -1,
}


class LightGbmEvalSet(DomainFrame):
    """Design matrix for evaluating :class:`LightGbmStrategy` with MLflow.

    One row per forecast point, holding the features knowable at 9:55 JST on
    D-1, the realized price, and the walk-forward forecast the backtest
    produced for that point. Unlike the naive eval set, ``time_code`` is a
    model feature here as well as a grain column.

    Grain: (trade_date, time_code).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "time_code": "int64",
        "month": "int64",
        "day_of_week": "int64",
        "lag_1d_price": "float64",
        TARGET_COL: "float64",
        FORECAST_COL: "float64",
    }
    keys = ["trade_date", "time_code"]
    non_null_cols = [*FEATURE_COLS, TARGET_COL, FORECAST_COL]

    def to_eval_frame(self) -> pd.DataFrame:
        """Features, target and forecast in the layout ``mlflow`` expects.

        Drops ``trade_date``, since MLflow treats every non-target,
        non-prediction column as a feature; ``time_code`` stays because it
        genuinely is one. Everything is cast to float64 so the SHAP plots
        and the dataset profile see a uniform numeric input.

        Returns
        -------
        pandas.DataFrame
        """
        return self.df[[*FEATURE_COLS, TARGET_COL, FORECAST_COL]].astype("float64")


class LightGbmStrategy(ForecastStrategy):
    """LightGBM regressor on time code, month, day of week and the 1-day lag.

    The model is refit every ``refit_every_days`` calendar days on a sliding
    window of the trailing ``train_window_days`` days of history (or as much
    of it as exists), so every delivery day is scored by a model fitted only
    on data published before it. Each :meth:`predict` call also records the
    exact TreeSHAP contributions of the model that scored it.

    Evaluation replays the backtest's own forecasts through MLflow's
    static-dataset mode instead of re-scoring with any single model: with
    periodic refits no one model spans the window, and any single refit
    would be partly in-sample there. The logged metrics are therefore
    exactly the backtest's numbers. The SHAP plots pool the recorded
    per-day contributions, so each row is explained by the model that
    actually forecast it, out-of-sample — at the price of mixing per-model
    baselines, which is fine for the distributional beeswarm and importance
    plots but is not a single-model decomposition.

    Parameters
    ----------
    train_window_days : int, optional
        Sliding training window length in calendar days.
    refit_every_days : int, optional
        Refit cadence in calendar days, anchored to the day that triggered
        the previous refit (so data gaps cannot drift it).
    """

    name = "lightgbm"

    def __init__(self, train_window_days: int = 730, refit_every_days: int = 7) -> None:
        self.train_window_days = train_window_days
        self.refit_every_days = refit_every_days
        self._model: lightgbm.LGBMRegressor | None = None
        self._trained_through: pd.Timestamp | None = None
        self._fit_anchor: pd.Timestamp | None = None
        self._n_fits = 0
        self._shap_records: dict[pd.Timestamp, pd.DataFrame] = {}

    def predict(self, target_date: pd.Timestamp, history: SpotPrices) -> DayAheadForecast:
        """Score the 48 periods of one delivery day, refitting first if due.

        Also records the model's TreeSHAP contributions for the 48 rows,
        keyed by delivery day, for pooling in :meth:`evaluate`. Predicting
        the same day again overwrites its recorded contributions.

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
            If the previous day is not fully present in the history, or the
            training window contains no complete rows.
        """
        # A string-parsed Timestamp carries second resolution; normalize so
        # the assigned trade_date column is datetime64[ns] per the contract.
        target_date = target_date.as_unit("ns")
        self._ensure_fitted(history.df, target_date)
        previous_day = target_date - pd.Timedelta(days=1)
        prev = history.df[history.df["trade_date"] == previous_day]
        if len(prev) == 0:
            raise ValueError(f"{self.name}: no history for previous day {previous_day.date()}")
        features = (
            prev[["time_code"]]
            .assign(
                month=target_date.month,
                day_of_week=target_date.dayofweek,
                lag_1d_price=prev["price_jpy_kwh"],
            )[list(FEATURE_COLS)]
            .astype("float64")
        )
        forecast = prev[["time_code"]].assign(
            trade_date=target_date,
            forecast_price_jpy_kwh=self._model.predict(features),
        )
        # Exact TreeSHAP from LightGBM itself: per-feature contributions
        # plus a trailing expected-value column that sum to the prediction.
        contributions = self._model.predict(features, pred_contrib=True)
        self._shap_records[target_date] = pd.concat(
            [
                forecast[["trade_date", "time_code"]],
                # time_code is already present as an int64 key column.
                features.drop(columns=["time_code"]),
                pd.DataFrame(
                    contributions[:, : len(FEATURE_COLS)],
                    columns=list(SHAP_COLS),
                    index=features.index,
                ),
            ],
            axis=1,
        ).assign(shap_expected_value=contributions[:, -1])
        return DayAheadForecast.from_df(forecast)

    def build_eval_set(
        self,
        prices: SpotPrices,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        result: BacktestResult | None = None,
    ) -> LightGbmEvalSet:
        """Assemble the MLflow design matrix for a backtest window.

        Joins the backtest's walk-forward forecasts onto the feature rows;
        the frame contract then enforces that every eval point has exactly
        one forecast. Points missing the lag — the first day of history, or
        the day after a gap — are dropped, since MLflow needs a complete
        numeric matrix.

        Parameters
        ----------
        prices : SpotPrices
            Full price history; must cover ``start_date`` minus 1 day.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.
        result : BacktestResult
            Walk-forward forecasts from ``run_backtest`` over the same
            window. Required: no single model produced them, so evaluation
            replays them rather than re-scoring.

        Returns
        -------
        LightGbmEvalSet

        Raises
        ------
        ValueError
            If ``result`` is missing, no complete rows remain in the window,
            or the forecasts do not cover every eval row.
        """
        if result is None:
            raise ValueError(
                f"{self.name}: build_eval_set requires the backtest result; "
                "run run_backtest over the same window first"
            )
        featured = self._design_matrix(prices.df)
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
        merged = complete.merge(
            result.df[["trade_date", "time_code", FORECAST_COL]],
            how="left",
            on=["trade_date", "time_code"],
            validate="one_to_one",
        )
        logger.info("%s eval set: %d rows, %d features", self.name, len(merged), len(FEATURE_COLS))
        return LightGbmEvalSet.from_df(merged)

    def evaluate(
        self,
        eval_set: LightGbmEvalSet,
        *,
        explainability_nsamples: int = 500,
    ) -> EvaluationResult:
        """Evaluate the walk-forward forecasts and explain them with MLflow.

        Must be called inside an active MLflow run, after a backtest. The
        metrics come from MLflow's static-dataset mode over the forecast
        column of ``eval_set``, so they are exactly the backtest's numbers;
        the SHAP plots pool the per-day contributions recorded by
        :meth:`predict`. The final refit's booster is logged for reference
        and serving, but computes nothing here.

        Parameters
        ----------
        eval_set : LightGbmEvalSet
            Design matrix from :meth:`build_eval_set`.
        explainability_nsamples : int, optional
            Rows sampled for the beeswarm plot (rendering cost only); the
            feature-importance plot always uses every row.

        Returns
        -------
        mlflow.models.EvaluationResult

        Raises
        ------
        RuntimeError
            If no backtest has recorded a model and contributions, or the
            recorded contributions do not cover the eval rows.
        """
        if self._model is None or not self._shap_records:
            raise RuntimeError(
                f"{self.name}: no fitted model or recorded contributions; run the backtest first"
            )
        eval_frame = eval_set.to_eval_frame()
        mlflow.log_params(
            {
                **{f"lgbm_{key}": value for key, value in LGBM_PARAMS.items()},
                "lgbm_train_window_days": self.train_window_days,
                "lgbm_refit_every_days": self.refit_every_days,
            }
        )
        mlflow.log_metric("n_refits", self._n_fits)
        mlflow.lightgbm.log_model(
            self._model,
            name=f"{self.name}_model",
            input_example=eval_frame[list(FEATURE_COLS)].head(),
        )
        self._log_shap_plots(eval_set, nsamples=explainability_nsamples)
        return evaluate_predictions(eval_frame, targets=TARGET_COL, predictions=FORECAST_COL)

    def _log_shap_plots(self, eval_set: LightGbmEvalSet, *, nsamples: int) -> None:
        """Pool the per-day TreeSHAP contributions and log summary plots.

        Every eval row is explained by the model that actually forecast it.
        Artifact names follow MLflow's shap evaluator so runs stay
        comparable across strategies.

        Parameters
        ----------
        eval_set : LightGbmEvalSet
            Eval rows to align the recorded contributions against.
        nsamples : int
            Beeswarm sample size; the importance bars use every row.

        Raises
        ------
        RuntimeError
            If the recorded contributions do not cover every eval row.
        """
        pooled = pd.concat(self._shap_records.values(), ignore_index=True)
        aligned = eval_set.df[["trade_date", "time_code"]].merge(
            pooled,
            how="inner",
            on=["trade_date", "time_code"],
            validate="one_to_one",
        )
        if len(aligned) != len(eval_set):
            raise RuntimeError(
                f"{self.name}: recorded contributions cover {len(aligned)} of "
                f"{len(eval_set)} eval rows; backtest and eval windows disagree"
            )
        sample = aligned.sample(n=min(nsamples, len(aligned)), random_state=0)
        shap.summary_plot(
            sample[list(SHAP_COLS)].to_numpy(), sample[list(FEATURE_COLS)], show=False
        )
        mlflow.log_figure(plt.gcf(), "shap_beeswarm_plot.png")
        plt.close("all")
        shap.summary_plot(
            aligned[list(SHAP_COLS)].to_numpy(),
            aligned[list(FEATURE_COLS)],
            plot_type="bar",
            show=False,
        )
        mlflow.log_figure(plt.gcf(), "shap_feature_importance_plot.png")
        plt.close("all")

    def _ensure_fitted(self, prices: pd.DataFrame, target_date: pd.Timestamp) -> None:
        """Refit the sliding-window model for a target day if due.

        A refit is due when there is no model yet, the refit cadence has
        elapsed since the day that triggered the last one, or the cached
        model saw data at or after ``target_date`` (i.e. reusing it would
        leak the future).

        Parameters
        ----------
        prices : pandas.DataFrame
            Price history in the ``SpotPrices`` layout; days at or after
            ``target_date`` must already be absent.
        target_date : pandas.Timestamp
            Delivery day about to be forecast.

        Raises
        ------
        ValueError
            If the training window contains no complete rows.
        """
        due = (
            self._model is None
            or target_date <= self._trained_through
            or target_date >= self._fit_anchor + pd.Timedelta(days=self.refit_every_days)
        )
        if not due:
            return
        window_start = target_date - pd.Timedelta(days=self.train_window_days)
        # One extra day of prices so the window's first day keeps its lag.
        recent = prices[prices["trade_date"] >= window_start - pd.Timedelta(days=1)]
        train = self._design_matrix(recent)
        train = train[train["trade_date"] >= window_start].dropna(
            subset=[*FEATURE_COLS, TARGET_COL]
        )
        if train.empty:
            raise ValueError(
                f"{self.name}: no complete training rows in the "
                f"{self.train_window_days} days before {target_date.date()}"
            )
        model = lightgbm.LGBMRegressor(**LGBM_PARAMS)
        model.fit(train[list(FEATURE_COLS)].astype("float64"), train[TARGET_COL])
        self._model = model
        self._trained_through = train["trade_date"].max()
        self._fit_anchor = target_date
        self._n_fits += 1
        logger.info(
            "%s: refit #%d on %d rows (%s..%s)",
            self.name,
            self._n_fits,
            len(train),
            train["trade_date"].min().date(),
            self._trained_through.date(),
        )

    def _design_matrix(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Features and target for every (trade_date, time_code) point.

        Rows whose lag day is missing keep a NaN ``lag_1d_price``; callers
        decide whether to drop them.

        Parameters
        ----------
        prices : pandas.DataFrame
            Price history in the ``SpotPrices`` layout.

        Returns
        -------
        pandas.DataFrame
            Grain columns, ``FEATURE_COLS`` and ``TARGET_COL``.
        """
        featured = prices.rename(columns={"price_jpy_kwh": TARGET_COL}).pipe(
            join_lag, prices, days=1, name="lag_1d_price"
        )
        return featured.assign(
            month=featured["trade_date"].dt.month.astype("int64"),
            day_of_week=featured["trade_date"].dt.dayofweek.astype("int64"),
        )
