"""Forecast strategy interface shared by every task."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import pandas as pd
from mlflow.models import EvaluationResult

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import DayAheadForecast, HalfHourlySeries
from power_market_analytics.forecasting.task import TaskSpec

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from power_market_analytics.forecasting.backtest import BacktestRun


class ForecastUnavailableError(ValueError):
    """The strategy cannot forecast the target day from the history it was given.

    Raised by :meth:`ForecastStrategy.predict` when a feature of the target
    day is missing (a lag lands on a gap, an exogenous row is absent) or when
    the visible history holds no complete training rows. The backtest engine
    treats it as "skip this day" rather than as a failure of the run.
    """


class ForecastStrategy[HistoryT: HalfHourlySeries, EvalSetT: DomainFrame](ABC):
    """Produces a 48-period day-ahead forecast for one delivery day.

    Generic over the history frame the strategy consumes (``HistoryT``) and
    the design-matrix frame its evaluation runs on (``EvalSetT``), so a
    concrete strategy can declare its task-specific frames without violating
    this base contract.

    Attributes
    ----------
    name : str
        Registry key and MLflow tag for the strategy.
    task : TaskSpec
        The task this strategy forecasts; fixes the frame classes, the
        history cutoff and the column names the engine reads.
    """

    name: ClassVar[str]
    task: ClassVar[TaskSpec]

    @abstractmethod
    def predict(self, target_date: pd.Timestamp, history: HistoryT) -> DayAheadForecast:
        """Forecast all 48 values for one delivery day.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D being forecast.
        history : HalfHourlySeries
            History available at the task's issue time, i.e. delivery days
            ``<= task.history_cutoff(D)`` only. The backtest engine enforces
            this cutoff; strategies must not assume anything newer exists.

        Returns
        -------
        DayAheadForecast

        Raises
        ------
        ForecastUnavailableError
            If the day cannot be forecast from ``history``.
        """

    @abstractmethod
    def build_eval_set(
        self,
        history: HistoryT,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        run: BacktestRun | None = None,
    ) -> EvalSetT:
        """Assemble the design matrix MLflow evaluates this strategy on.

        Every strategy must be evaluable: the backtest scripts always run
        MLflow's regressor evaluation, so this is part of the contract rather
        than an optional extra.

        Parameters
        ----------
        history : HalfHourlySeries
            Full history, including whatever lookback the features need
            before ``start_date``.
        start_date, end_date : pandas.Timestamp
            First and last delivery days, inclusive.
        run : BacktestRun, optional
            Walk-forward forecasts (and skipped days) from ``run_backtest``
            over the same window. Strategies whose evaluation replays the
            backtest's own predictions (because no single model produced
            them) require it and raise ``ValueError`` without it; strategies
            whose logged model reproduces its predictions exactly ignore it.

        Returns
        -------
        DomainFrame
            A frame exposing ``to_eval_frame()``: numeric feature columns plus
            the target column, with no non-numeric columns (the SHAP evaluator
            skips otherwise).
        """

    @abstractmethod
    def evaluate(self, eval_set: EvalSetT, **kwargs: Any) -> EvaluationResult:
        """Log this strategy as a model and evaluate it with MLflow.

        Called inside an active MLflow run; the model, metrics and SHAP plots
        land in that run.

        Parameters
        ----------
        eval_set : DomainFrame
            Design matrix from :meth:`build_eval_set`.
        **kwargs
            Strategy-specific evaluation options.

        Returns
        -------
        mlflow.models.EvaluationResult
        """
