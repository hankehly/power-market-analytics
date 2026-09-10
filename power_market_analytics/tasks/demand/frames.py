"""Domain frames for the area demand (load) forecasting task."""

from __future__ import annotations

import numpy as np
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


def _check_hour_ending(name: str, df: pd.DataFrame) -> None:
    """Reject ``hour_ending`` values outside JMA's 1..24 observation hours.

    Parameters
    ----------
    name : str
        Frame name for the error message.
    df : pandas.DataFrame
        Frame with an ``hour_ending`` column.

    Raises
    ------
    ValueError
        If any ``hour_ending`` lies outside 1..24.
    """
    bad = df.loc[~df["hour_ending"].between(1, 24), "hour_ending"]
    if not bad.empty:
        raise ValueError(f"{name}: hour_ending outside 1..24: {sorted(bad.unique())}")


#: Day-type categories in code order (``day_type`` = the level's index): a
#: working weekday, a Saturday/Sunday, or a ``dim_date`` holiday — the same
#: labels as the demand compare script's day-type segment.
DAY_TYPE_LEVELS: tuple[str, ...] = ("Weekday", "Weekend", "Holiday")


class AreaHourlyLoad(DomainFrame):
    """Hourly area load history: energy over each hour in kWh, as
    ``fct_area_power_usage_hourly`` publishes it (the でんき予報 1時間平均 over
    one hour). ``hour_ending`` is JMA's hour label 1..24 (the fact's
    ``hour_of_day`` + 1; 24 = the reading at 24:00), so a delivery period maps
    to its hour through ``hour_ending = (time_code + 1) // 2``.
    Loads are positive: the fact never carries TEPCO's not-yet-final zero, so
    a zero here would be a load error, not a reading.

    Grain: (load_date, hour_ending).
    """

    schema = {
        "load_date": "datetime64[ns]",
        "hour_ending": "int64",
        "demand_kwh": "float64",
    }
    keys = ["load_date", "hour_ending"]
    non_null_cols = ["demand_kwh"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)
        bad = df[df["demand_kwh"] <= 0]
        if not bad.empty:
            first = bad.iloc[0]
            raise ValueError(
                f"{cls.__name__}: demand_kwh must be positive; {len(bad)} row(s) are not "
                f"(e.g. {first['load_date'].date()} hour {int(first['hour_ending'])})"
            )


class AreaWeatherForecast(DomainFrame):
    """Hourly population-weighted MSM forecast of temperature, relative humidity
    and rain for an area, keyed by the delivery day it is valid for.

    One row per delivery day and hour-ending 1..24, the hour convention of
    :class:`AreaHourlyLoad`; exactly one forecast vintage per hour, so a
    loader that sees two fails fast. The three measures are nullable (an hour
    no weighted station forecast).

    Grain: (trade_date, hour_ending).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "hour_ending": "int64",
        "forecast_temperature_c": "float64",
        "forecast_relative_humidity_pct": "float64",
        "forecast_precipitation_mm": "float64",
    }
    keys = ["trade_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)


class AreaObservedWeather(DomainFrame):
    """Hourly population-weighted observed temperature, relative humidity and
    rain for an area (``fct_jma_weather_hourly`` over the weighted stations).

    One row per observation day and hour-ending 1..24 (24 = the reading at
    24:00, which the weather fact stores as next-day 00:00 but keys to the
    observation day); the measures are nullable (an hour at which no weighted
    station reported).

    Grain: (obs_date, hour_ending).
    """

    schema = {
        "obs_date": "datetime64[ns]",
        "hour_ending": "int64",
        "temperature_c": "float64",
        "humidity_pct": "float64",
        "precipitation_mm": "float64",
    }
    keys = ["obs_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)


#: The values ``dim_date.holiday_degree`` takes (the graded 休日度合い).
HOLIDAY_DEGREE_LEVELS: tuple[float, ...] = (0.0, 0.3, 0.5, 0.8, 1.0)


#: Closed ranges of the ``dim_date`` calendar counts a :class:`DayCalendar` carries.
CALENDAR_COUNT_RANGES: dict[str, tuple[int, int]] = {
    "half": (1, 2),
    "quarter": (1, 4),
    "day_of_month": (1, 31),
    "day_of_quarter": (1, 92),
    "day_of_year": (1, 366),
    "fiscal_quarter": (1, 4),
}


class DayCalendar(DomainFrame):
    """Calendar attributes of every ``dim_date`` day the similar-day selector and
    the calendar-feature strategies read.

    ``day_type`` is the index into :data:`DAY_TYPE_LEVELS` (a holiday whatever
    weekday it falls on, else a weekend day, else a weekday);
    ``days_since_holiday`` / ``days_until_holiday`` count calendar days to the
    nearest named holiday (``dim_date.is_holiday``; 0 on a holiday itself);
    ``holiday_degree`` is ``dim_date.holiday_degree``. The calendar counts —
    ``half`` (1 before July), ``quarter``, ``day_of_month``, ``day_of_quarter``,
    ``day_of_year``, ``fiscal_quarter`` (April = Q1) — and the working-day flag
    ``is_business_day`` are ``dim_date``'s columns as-is.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "day_type": "int64",
        "days_since_holiday": "int64",
        "days_until_holiday": "int64",
        "holiday_degree": "float64",
        "half": "int64",
        "quarter": "int64",
        "day_of_month": "int64",
        "day_of_quarter": "int64",
        "day_of_year": "int64",
        "is_business_day": "bool",
        "fiscal_quarter": "int64",
    }
    keys = ["trade_date"]
    non_null_cols = [col for col in schema if col != "trade_date"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        last = len(DAY_TYPE_LEVELS) - 1
        bad = df.loc[~df["day_type"].between(0, last), "day_type"]
        if not bad.empty:
            codes = sorted(int(code) for code in bad.unique())
            raise ValueError(f"{cls.__name__}: day_type outside 0..{last}: {codes}")
        for col in ("days_since_holiday", "days_until_holiday"):
            if (df[col] < 0).any():
                raise ValueError(f"{cls.__name__}: {col} must be >= 0")
        for col, (low, high) in CALENDAR_COUNT_RANGES.items():
            outside = df.loc[~df[col].between(low, high), col]
            if not outside.empty:
                counts = sorted(int(v) for v in outside.unique())
                raise ValueError(f"{cls.__name__}: {col} outside {low}..{high}: {counts}")
        levels = np.asarray(HOLIDAY_DEGREE_LEVELS)
        degrees = df["holiday_degree"].to_numpy(dtype="float64")
        off = ~np.isclose(degrees[:, None], levels[None, :]).any(axis=1)
        if off.any():
            values = sorted(set(float(v) for v in degrees[off]))
            raise ValueError(
                f"{cls.__name__}: holiday_degree outside {HOLIDAY_DEGREE_LEVELS}: {values}"
            )
