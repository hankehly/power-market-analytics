"""Sliding-window LightGBM strategy base shared by every task.

A concrete strategy sets ``task``, ``name``, ``feature_cols``,
``eval_set_cls`` and ``lookback_days`` and implements ``_add_features`` (lags,
exogenous columns); everything else — the calendar features, periodic refits
on a trailing window, TreeSHAP recording per forecast day, replaying the
walk-forward forecasts through MLflow's static-dataset evaluation and the SHAP
summary plots — lives here.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

import lightgbm
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
import shap
from loguru import logger
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.common.tracking import evaluate_predictions
from power_market_analytics.forecasting.backtest import BacktestRun
from power_market_analytics.forecasting.frames import (
    GRAIN_COLS,
    N_PERIODS,
    DayAheadForecast,
    HalfHourlySeries,
)
from power_market_analytics.forecasting.strategy import ForecastStrategy, ForecastUnavailableError

CALENDAR_FEATURE_COLS: tuple[str, ...] = ("time_code", "month", "day_of_week")

# Modest, fixed hyperparameters: a handful of low-cardinality features does
# not warrant tuning machinery yet. Logged to the MLflow run in `evaluate`.
LGBM_PARAMS: dict[str, Any] = {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "random_state": 0,
    "verbose": -1,
}


class LightGbmEvalSetBase(DomainFrame):
    """Design matrix base for evaluating a sliding-window LightGBM strategy.

    One row per forecast point: the features knowable at the task's issue
    time, the realized value (``target_col``, the task's actual column) and
    the walk-forward forecast (``forecast_col``). Concrete subclasses declare
    the explicit ``schema`` (grain, features, target, forecast) plus
    ``keys`` / ``non_null_cols``.

    Grain: (trade_date, time_code).
    """

    feature_cols: ClassVar[tuple[str, ...]]
    target_col: ClassVar[str]
    forecast_col: ClassVar[str]

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
        return self.df[[*self.feature_cols, self.target_col, self.forecast_col]].astype("float64")


class SlidingWindowLightGbmStrategy(ForecastStrategy[HalfHourlySeries, LightGbmEvalSetBase]):
    """LightGBM regressor over calendar features plus task-specific features.

    The model is refit every ``refit_every_days`` calendar days on a sliding
    window of the trailing ``train_window_days`` days of history (or as much
    of it as exists), so every delivery day is scored by a model fitted only
    on data published before it. Each :meth:`predict` call also records the
    exact TreeSHAP contributions of the model that scored it.

    Training and prediction rows go through the same feature builder
    (:meth:`_features`), so the two can never disagree: the base adds
    ``month`` and ``day_of_week`` and the subclass's :meth:`_add_features`
    adds its lags and exogenous columns.

    Evaluation replays the backtest's own forecasts through MLflow's
    static-dataset mode instead of re-scoring with any single model: with
    periodic refits no one model spans the window, and any single refit
    would be partly in-sample there. The logged metrics are therefore
    exactly the backtest's numbers. The SHAP plots pool the recorded
    per-day contributions, so each row is explained by the model that
    actually forecast it, out-of-sample — at the price of mixing per-model
    baselines, which is fine for the distributional beeswarm and importance
    plots but is not a single-model decomposition.

    Class Attributes
    ----------------
    feature_cols : tuple of str
        Model features, in order; must start with ``CALENDAR_FEATURE_COLS``
        or otherwise include every column :meth:`_features` produces.
    eval_set_cls : type
        ``LightGbmEvalSetBase`` subclass for this strategy's design matrix.
    lookback_days : int
        Extra days of history before the training window's first day that
        its features need (the longest lag).

    Parameters
    ----------
    train_window_days : int, optional
        Sliding training window length in calendar days.
    refit_every_days : int, optional
        Refit cadence in calendar days, anchored to the day that triggered
        the previous refit (so data gaps cannot drift it).
    train_start_date : pandas.Timestamp, optional
        First delivery day eligible as a training row. Earlier history is
        still used for lag features, it just never becomes a target. Lets a
        baseline be fitted on exactly the rows a feature-limited candidate
        can use.
    """

    feature_cols: ClassVar[tuple[str, ...]]
    eval_set_cls: ClassVar[type[LightGbmEvalSetBase]]
    lookback_days: ClassVar[int]

    def __init__(
        self,
        train_window_days: int = 730,
        refit_every_days: int = 7,
        train_start_date: pd.Timestamp | None = None,
    ) -> None:
        self.train_window_days = train_window_days
        self.refit_every_days = refit_every_days
        self.train_start_date = (
            None if train_start_date is None else pd.Timestamp(train_start_date).as_unit("ns")
        )
        self._model: lightgbm.LGBMRegressor | None = None
        self._trained_through: pd.Timestamp | None = None
        self._fit_anchor: pd.Timestamp | None = None
        self._n_fits = 0
        self._shap_records: dict[pd.Timestamp, pd.DataFrame] = {}

    @property
    def shap_cols(self) -> tuple[str, ...]:
        """Per-feature SHAP contribution column names, aligned with ``feature_cols``."""
        return tuple(f"shap_{col}" for col in self.feature_cols)

    @property
    def target_col(self) -> str:
        """The realized-value column of the design matrix (the task's actual column)."""
        return self.eval_set_cls.target_col

    @property
    def forecast_col(self) -> str:
        """The forecast column of the design matrix (the task's forecast column)."""
        return self.eval_set_cls.forecast_col

    def predict(self, target_date: pd.Timestamp, history: HalfHourlySeries) -> DayAheadForecast:
        """Score the 48 periods of one delivery day, refitting first if due.

        Also records the model's TreeSHAP contributions for the 48 rows,
        keyed by delivery day, for pooling in :meth:`evaluate`. Predicting
        the same day again overwrites its recorded contributions.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : HalfHourlySeries
            History through the task's cutoff for D.

        Returns
        -------
        DayAheadForecast
            An instance of ``task.forecast_cls``.

        Raises
        ------
        ForecastUnavailableError
            If any feature is unavailable for the target day, or the
            training window contains no complete rows.
        """
        # A string-parsed Timestamp carries second resolution; normalize so
        # the assigned trade_date column is datetime64[ns] per the contract.
        target_date = pd.Timestamp(target_date).as_unit("ns")
        model = self._ensure_fitted(history.df, target_date)
        points = pd.DataFrame(
            {"trade_date": target_date, "time_code": np.arange(1, N_PERIODS + 1, dtype="int64")}
        )
        featured = self._features(points, history.df)
        missing = featured[list(self.feature_cols)].isna().any()
        if missing.any():
            raise ForecastUnavailableError(
                f"{self.name}: features {list(missing[missing].index)} unavailable for "
                f"{target_date.date()}"
            )
        features = featured[list(self.feature_cols)].astype("float64")
        forecast = featured[GRAIN_COLS].assign(**{self.forecast_col: model.predict(features)})
        # Exact TreeSHAP from LightGBM itself: per-feature contributions
        # plus a trailing expected-value column that sum to the prediction.
        contributions = np.asarray(model.predict(features, pred_contrib=True))
        self._shap_records[target_date] = pd.concat(
            [
                forecast[GRAIN_COLS],
                # time_code is already present as an int64 key column.
                features.drop(columns=["time_code"]),
                pd.DataFrame(
                    contributions[:, : len(self.feature_cols)],
                    columns=list(self.shap_cols),
                    index=features.index,
                ),
            ],
            axis=1,
        ).assign(shap_expected_value=contributions[:, -1])
        return self.task.forecast_cls.from_df(forecast)

    def build_eval_set(
        self,
        history: HalfHourlySeries,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        run: BacktestRun | None = None,
    ) -> LightGbmEvalSetBase:
        """Assemble the MLflow design matrix for a backtest window.

        Joins the backtest's walk-forward forecasts onto the feature rows;
        the frame contract then enforces that every eval point has exactly
        one forecast. Points missing any feature — the first days of history,
        the days after a gap, a day without exogenous data — and the days
        the backtest skipped are dropped, since MLflow needs a complete
        numeric matrix and skipped days have nothing to replay.

        Parameters
        ----------
        history : HalfHourlySeries
            Full history; must cover ``start_date`` minus ``lookback_days``.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.
        run : BacktestRun
            Walk-forward forecasts from ``run_backtest`` over the same
            window. Required: no single model produced them, so evaluation
            replays them rather than re-scoring.

        Returns
        -------
        LightGbmEvalSetBase
            An instance of ``eval_set_cls``.

        Raises
        ------
        ValueError
            If ``run`` is missing, no complete rows remain in the window,
            or the forecasts do not cover every eval row.
        """
        if run is None:
            raise ValueError(
                f"{self.name}: build_eval_set requires the backtest run; "
                "run run_backtest over the same window first"
            )
        featured = self._design_matrix(history.df)
        window = featured[featured["trade_date"].between(start_date, end_date)]
        complete = window.dropna(subset=[*self.feature_cols, self.target_col])
        n_dropped = len(window) - len(complete)
        if n_dropped:
            logger.info(
                "{} eval set: dropped {} of {} rows with incomplete features",
                self.name,
                n_dropped,
                len(window),
            )
        if run.skipped_days:
            n_before = len(complete)
            complete = complete[~complete["trade_date"].isin(run.skipped_days)]
            logger.info(
                "{} eval set: dropped {} rows on {} skipped days",
                self.name,
                n_before - len(complete),
                len(run.skipped_days),
            )
        if complete.empty:
            raise ValueError(
                f"{self.name}: no complete feature rows between "
                f"{start_date.date()} and {end_date.date()}"
            )
        # A left-joined feature is float64 whenever any row of the full
        # history lacked it; restore the contract dtype now that only
        # complete rows remain.
        schema = self.eval_set_cls.schema
        complete = complete.astype({col: schema[col] for col in self.feature_cols})
        merged = complete.merge(
            run.result.df[[*GRAIN_COLS, self.forecast_col]],
            how="left",
            on=GRAIN_COLS,
            validate="one_to_one",
        )
        logger.info(
            "{} eval set: {} rows, {} features", self.name, len(merged), len(self.feature_cols)
        )
        return self.eval_set_cls.from_df(merged)

    def evaluate(
        self,
        eval_set: LightGbmEvalSetBase,
        *,
        explainability_nsamples: int = 500,
        **kwargs: Any,
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
        eval_set : LightGbmEvalSetBase
            Design matrix from :meth:`build_eval_set`.
        explainability_nsamples : int, optional
            Rows sampled for the beeswarm plot (rendering cost only); the
            feature-importance plot always uses every row.
        **kwargs
            Rejected: the base contract allows strategy-specific options and
            this strategy has none beyond ``explainability_nsamples``.

        Returns
        -------
        mlflow.models.EvaluationResult

        Raises
        ------
        TypeError
            If an unknown keyword argument is passed.
        RuntimeError
            If no backtest has recorded a model and contributions, or the
            recorded contributions do not cover the eval rows.
        """
        if kwargs:
            raise TypeError(
                f"{self.name}.evaluate got unexpected keyword arguments {sorted(kwargs)}"
            )
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
                "lgbm_train_start_date": (
                    "none" if self.train_start_date is None else str(self.train_start_date.date())
                ),
                "lgbm_feature_cols": ",".join(self.feature_cols),
                **self._extra_params(),
            }
        )
        mlflow.log_metric("n_refits", self._n_fits)
        mlflow.lightgbm.log_model(
            self._model,
            name=f"{self.name}_model",
            input_example=eval_frame[list(self.feature_cols)].head(),
        )
        self._log_shap_plots(eval_set, nsamples=explainability_nsamples)
        return evaluate_predictions(
            eval_frame, targets=self.target_col, predictions=self.forecast_col
        )

    def _extra_params(self) -> dict[str, object]:
        """Strategy-specific run params logged next to the ``lgbm_*`` ones.

        Returns
        -------
        dict of str to object
            Empty by default.
        """
        return {}

    def _log_shap_plots(self, eval_set: LightGbmEvalSetBase, *, nsamples: int) -> None:
        """Pool the per-day TreeSHAP contributions and log summary plots.

        Every eval row is explained by the model that actually forecast it.
        Artifact names follow MLflow's shap evaluator so runs stay
        comparable across strategies.

        Parameters
        ----------
        eval_set : LightGbmEvalSetBase
            Eval rows to align the recorded contributions against.
        nsamples : int
            Beeswarm sample size; the importance bars use every row.

        Raises
        ------
        RuntimeError
            If the recorded contributions do not cover every eval row.
        """
        pooled = pd.concat(self._shap_records.values(), ignore_index=True)
        aligned = eval_set.df[GRAIN_COLS].merge(
            pooled,
            how="inner",
            on=GRAIN_COLS,
            validate="one_to_one",
        )
        if len(aligned) != len(eval_set):
            raise RuntimeError(
                f"{self.name}: recorded contributions cover {len(aligned)} of "
                f"{len(eval_set)} eval rows; backtest and eval windows disagree"
            )
        feature_cols = list(self.feature_cols)
        shap_cols = list(self.shap_cols)
        sample = aligned.sample(n=min(nsamples, len(aligned)), random_state=0)
        shap.summary_plot(sample[shap_cols].to_numpy(), sample[feature_cols], show=False)
        mlflow.log_figure(plt.gcf(), "shap_beeswarm_plot.png")
        plt.close("all")
        shap.summary_plot(
            aligned[shap_cols].to_numpy(),
            aligned[feature_cols],
            plot_type="bar",
            show=False,
        )
        mlflow.log_figure(plt.gcf(), "shap_feature_importance_plot.png")
        plt.close("all")

    def _ensure_fitted(
        self, history: pd.DataFrame, target_date: pd.Timestamp
    ) -> lightgbm.LGBMRegressor:
        """Refit the sliding-window model for a target day if due.

        A refit is due when there is no model yet, the refit cadence has
        elapsed since the day that triggered the last one, or the cached
        model saw data at or after ``target_date`` (i.e. reusing it would
        leak the future). The window never reaches back before
        ``train_start_date``.

        Parameters
        ----------
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout; days after
            the task's cutoff for ``target_date`` must already be absent.
        target_date : pandas.Timestamp
            Delivery day about to be forecast.

        Returns
        -------
        lightgbm.LGBMRegressor
            The model to score ``target_date`` with (cached or just refit).

        Raises
        ------
        ForecastUnavailableError
            If the training window contains no complete rows.
        """
        cached = self._model
        if (
            cached is not None
            and self._trained_through is not None
            and self._fit_anchor is not None
            and target_date > self._trained_through
            and target_date < self._fit_anchor + pd.Timedelta(days=self.refit_every_days)
        ):
            return cached
        window_start = target_date - pd.Timedelta(days=self.train_window_days)
        if self.train_start_date is not None:
            window_start = max(window_start, self.train_start_date)
        # Extra days of history so the window's first day keeps its lags.
        recent = history[
            history["trade_date"] >= window_start - pd.Timedelta(days=self.lookback_days)
        ]
        train = self._design_matrix(recent)
        train = train[train["trade_date"] >= window_start].dropna(
            subset=[*self.feature_cols, self.target_col]
        )
        if train.empty:
            raise ForecastUnavailableError(
                f"{self.name}: no complete training rows in the "
                f"{self.train_window_days} days before {target_date.date()}"
            )
        model = lightgbm.LGBMRegressor(**LGBM_PARAMS)
        model.fit(train[list(self.feature_cols)].astype("float64"), train[self.target_col])
        trained_through = train["trade_date"].max()
        self._model = model
        self._trained_through = trained_through
        self._fit_anchor = target_date
        self._n_fits += 1
        logger.info(
            "{}: refit #{} on {} rows ({}..{})",
            self.name,
            self._n_fits,
            len(train),
            train["trade_date"].min().date(),
            trained_through.date(),
        )
        return model

    def _design_matrix(self, history: pd.DataFrame) -> pd.DataFrame:
        """Features and target for every (trade_date, time_code) point of ``history``.

        Rows missing a feature (no lag day, no exogenous row) keep NaN
        there; callers decide whether to drop them.

        Parameters
        ----------
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout.

        Returns
        -------
        pandas.DataFrame
            Grain columns, ``feature_cols`` and ``target_col``.
        """
        return self._features(
            history.rename(columns={self.task.value_col: self.target_col}), history
        )

    def _features(self, points: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach every feature column to a set of (trade_date, time_code) points.

        The single feature path for both training rows and the target day's
        48 prediction rows, so the two can never disagree.

        Parameters
        ----------
        points : pandas.DataFrame
            Rows keyed on (trade_date, time_code); other columns pass through.
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout, for lags.

        Returns
        -------
        pandas.DataFrame
            ``points`` plus ``feature_cols`` (NaN where unavailable).
        """
        featured = points.assign(
            month=points["trade_date"].dt.month.astype("int64"),
            day_of_week=points["trade_date"].dt.dayofweek.astype("int64"),
        )
        return self._add_features(featured, history)

    @abstractmethod
    def _add_features(self, featured: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
        """Attach the strategy's own features (lags, exogenous columns).

        Parameters
        ----------
        featured : pandas.DataFrame
            Rows keyed on (trade_date, time_code) carrying the calendar
            features.
        history : pandas.DataFrame
            History in the task's ``HalfHourlySeries`` layout.

        Returns
        -------
        pandas.DataFrame
            ``featured`` plus this strategy's remaining ``feature_cols``
            (NaN where unavailable).
        """
