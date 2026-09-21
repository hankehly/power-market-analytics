"""The spec that turns the generic framework into one concrete task."""

from __future__ import annotations

import dataclasses

import pandas as pd

from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)


@dataclasses.dataclass(frozen=True)
class TaskSpec:
    """Everything the generic engine, strategies, publish and plots need to know
    about one modeling task.

    The contribution table and column (``contribution_table``,
    ``contribution_col``) and the importance table and MAE columns
    (``importance_table``, ``mae_col``, ``permuted_mae_col``) are derived
    rather than stored.

    Column names are not stored twice: they are read off the frame classes,
    which own the contracts.

    Attributes
    ----------
    name : str
        Task name; doubles as the MLflow experiment name.
    unit : str
        Unit of the forecast value for labels, e.g. ``"JPY/kWh"``.
    history_lead_days : int
        How many days before delivery day D the newest usable history day
        lies: 1 when D-1 is fully known at issue time, 2 when only D-2 is.
    issue_offset : pandas.Timedelta
        Issue time relative to D 00:00, e.g. ``Timedelta(days=-1, hours=9,
        minutes=30)`` for 09:30 on D-1.
    forecast_table : str
        Warehouse table the run's forecasts are published to.
    history_cls, forecast_cls, result_cls, records_cls : type
        The task's ``HalfHourlySeries``, ``DayAheadForecast``,
        ``BacktestResult`` and ``ForecastRecords`` subclasses.
    eval_start, eval_end : pandas.Timestamp or None
        The task's pinned evaluation window: the delivery days every experiment
        scores, so two runs made months apart compare on the same days. The days
        after ``eval_end`` are the holdout, kept out of the decisions that pick a
        baseline; a backtest refuses to score into them without being told to.
        Both None leaves a task unpinned, and its backtest ends at the last day
        in the data.
    holdout_start : pandas.Timestamp or None
        A floor for the first unseen day, when the day after ``eval_end`` is too
        early: runs made before the window was pinned scored past it. None means
        the floor is the day after ``eval_end``. It is only a floor — a day a run
        has scored is no longer unseen, so
        ``forecasting.holdout.holdout_opens`` moves the boundary past whatever
        the accuracy mart already holds. Read it through that, not through this.
    """

    name: str
    unit: str
    history_lead_days: int
    issue_offset: pd.Timedelta
    forecast_table: str
    history_cls: type[HalfHourlySeries]
    forecast_cls: type[DayAheadForecast]
    result_cls: type[BacktestResult]
    records_cls: type[ForecastRecords]
    eval_start: pd.Timestamp | None = None
    eval_end: pd.Timestamp | None = None
    holdout_start: pd.Timestamp | None = None

    def __post_init__(self) -> None:
        if (self.eval_start is None) != (self.eval_end is None):
            raise ValueError(f"{self.name}: pin both eval_start and eval_end, or neither")
        if (
            self.eval_start is not None
            and self.eval_end is not None
            and self.eval_start > self.eval_end
        ):
            raise ValueError(
                f"{self.name}: eval_start {self.eval_start.date()} is after "
                f"eval_end {self.eval_end.date()}"
            )
        if self.holdout_start is not None:
            if self.eval_end is None:
                raise ValueError(f"{self.name}: holdout_start needs a pinned eval_end")
            if self.holdout_start <= self.eval_end:
                raise ValueError(
                    f"{self.name}: holdout_start {self.holdout_start.date()} must follow "
                    f"eval_end {self.eval_end.date()}"
                )
        if self.history_lead_days < 1:
            raise ValueError(f"history_lead_days must be >= 1, got {self.history_lead_days}")
        forecast_cols = {
            self.forecast_cls.forecast_col,
            self.result_cls.forecast_col,
            self.records_cls.forecast_col,
        }
        if len(forecast_cols) != 1:
            raise ValueError(
                f"{self.name}: forecast column differs across frames: {sorted(forecast_cols)}"
            )
        if not self.forecast_col.startswith("forecast_"):
            raise ValueError(
                f"{self.name}: forecast column {self.forecast_col!r} must start with "
                "'forecast_' (the contribution column is derived from it)"
            )

    @property
    def eval_window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """The pinned evaluation window, for a task that has one.

        Returns
        -------
        tuple of (pandas.Timestamp, pandas.Timestamp)
            ``eval_start`` and ``eval_end``.

        Raises
        ------
        ValueError
            If the task is unpinned, so a caller cannot mistake an unpinned task
            for one whose window happens to be missing.
        """
        if self.eval_start is None or self.eval_end is None:
            raise ValueError(f"{self.name}: no evaluation window is pinned")
        return self.eval_start, self.eval_end

    @property
    def holdout_opens(self) -> pd.Timestamp:
        """The first day that is genuinely unseen.

        The day after ``eval_end``, unless ``holdout_start`` puts it later
        because earlier runs already scored those days.

        Returns
        -------
        pandas.Timestamp

        Raises
        ------
        ValueError
            If the task is unpinned.
        """
        _, eval_end = self.eval_window
        if self.holdout_start is not None:
            return self.holdout_start
        return eval_end + pd.Timedelta(days=1)

    @property
    def value_col(self) -> str:
        """History value column, e.g. ``price_jpy_kwh``."""
        return self.history_cls.value_col

    @property
    def forecast_col(self) -> str:
        """Forecast column shared by the forecast, result and records frames."""
        return self.forecast_cls.forecast_col

    @property
    def actual_col(self) -> str:
        """Actual-value column of the backtest result."""
        return self.result_cls.actual_col

    @property
    def contribution_table(self) -> str:
        """Warehouse table the run's forecast contributions are published to.

        ``forecast_table`` with a ``_contribution`` suffix, e.g.
        ``pma_ml.demand_forecast_contribution``.
        """
        return f"{self.forecast_table}_contribution"

    @property
    def contribution_col(self) -> str:
        """Warehouse column of a component's contribution to the forecast.

        ``forecast_col`` with its ``forecast_`` prefix swapped for
        ``contribution_``, e.g. ``contribution_demand_kwh`` — same unit as the
        forecast.
        """
        return "contribution_" + self.forecast_col.removeprefix("forecast_")

    @property
    def importance_table(self) -> str:
        """Warehouse table the run's permutation feature importance is published to.

        ``forecast_table`` with an ``_importance`` suffix, e.g.
        ``pma_ml.demand_forecast_importance``.
        """
        return f"{self.forecast_table}_importance"

    @property
    def mae_col(self) -> str:
        """Warehouse column of the importance rows' baseline MAE.

        ``forecast_col`` with its ``forecast_`` prefix swapped for ``mae_``,
        e.g. ``mae_demand_kwh`` — the forecast unit.
        """
        return "mae_" + self.forecast_col.removeprefix("forecast_")

    @property
    def permuted_mae_col(self) -> str:
        """Warehouse column of the MAE after shuffling the feature, e.g.
        ``permuted_mae_demand_kwh``."""
        return "permuted_" + self.mae_col

    def history_cutoff(self, target_date: pd.Timestamp) -> pd.Timestamp:
        """Newest delivery day a strategy may see when forecasting ``target_date``.

        Parameters
        ----------
        target_date : pandas.Timestamp
            Delivery day D.

        Returns
        -------
        pandas.Timestamp
            ``D - history_lead_days`` days; history rows must satisfy
            ``trade_date <= cutoff``.
        """
        return pd.Timestamp(target_date) - pd.Timedelta(days=self.history_lead_days)
