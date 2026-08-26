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
        minutes=55)`` for 09:55 on D-1.
    forecast_table : str
        Warehouse table the run's forecasts are published to.
    history_cls, forecast_cls, result_cls, records_cls : type
        The task's ``HalfHourlySeries``, ``DayAheadForecast``,
        ``BacktestResult`` and ``ForecastRecords`` subclasses.
    The contribution table and column are derived (``contribution_table``,
    ``contribution_col``) rather than stored.
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

    def __post_init__(self) -> None:
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
