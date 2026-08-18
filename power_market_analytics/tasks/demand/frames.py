"""Domain frames for the area demand (load) forecasting task."""

from __future__ import annotations

import pandas as pd

from power_market_analytics.common.frames import DomainFrame
from power_market_analytics.forecasting.frames import (
    BacktestResult,
    DayAheadForecast,
    ForecastRecords,
    HalfHourlySeries,
)


class AreaDemand(HalfHourlySeries):
    """Half-hourly area demand history for one area, in kWh per 30-minute period.

    Rows whose actual is unpublished (the TSO holes, e.g. Tokyo 2025-06-14
    time codes 11-48) are absent — the loader drops them — so the value is
    non-null and the grain may be sparse on those days.

    Grain: (trade_date, time_code).
    """

    value_col = "demand_kwh"


class DemandForecast(DayAheadForecast):
    """Forecast for one delivery day: exactly 48 half-hour demand values (kWh).

    Grain: (trade_date, time_code); trade_date is the target delivery day.
    """

    forecast_col = "forecast_demand_kwh"


class DemandBacktestResult(BacktestResult):
    """Demand forecasts joined to actuals over a backtest window.

    Grain: (trade_date, time_code).
    """

    actual_col = "actual_demand_kwh"
    forecast_col = "forecast_demand_kwh"


class DemandForecastRecords(ForecastRecords):
    """One backtest run's demand forecasts shaped for ``pma_ml.demand_forecast``.

    Grain: (run_id, area_code, trade_date, time_code).
    """

    forecast_col = "forecast_demand_kwh"


class AreaTemperature(DomainFrame):
    """Hourly temperature at an area's representative JMA station.

    ``hour_ending`` is JMA's observation hour 1..24 (24 = the reading at
    24:00, which the weather fact stores as next-day 00:00 but keys to the
    observation day). ``temperature_c`` is null where JMA published no usable
    value (quality flag 2/1/0), so it is not a non-null column.

    Grain: (obs_date, hour_ending).
    """

    schema = {
        "obs_date": "datetime64[ns]",
        "hour_ending": "int64",
        "temperature_c": "float64",
    }
    keys = ["obs_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        bad = df.loc[~df["hour_ending"].between(1, 24), "hour_ending"]
        if not bad.empty:
            raise ValueError(f"{cls.__name__}: hour_ending outside 1..24: {sorted(bad.unique())}")
