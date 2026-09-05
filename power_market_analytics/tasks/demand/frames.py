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
        _check_hour_ending(cls.__name__, df)


class AreaTemperatureForecast(DomainFrame):
    """Hourly *forecast* temperature at an area's representative JMA station,
    keyed by the delivery day it is valid for.

    One row per delivery day and hour-ending 1..24 (the same hour convention
    as :class:`AreaTemperature`, so both map onto delivery periods through
    ``hour_ending = (time_code + 1) // 2``). Exactly one forecast vintage per
    hour: the grain is unique, so a loader that sees two vintages for the
    same hour fails fast instead of silently picking one. ``forecast_temperature_c``
    may be null where the forecast source published no value, so it is not a
    non-null column (a missing hour makes the target day unforecastable for a
    strategy that needs it).

    Grain: (trade_date, hour_ending).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "hour_ending": "int64",
        "forecast_temperature_c": "float64",
    }
    keys = ["trade_date", "hour_ending"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        _check_hour_ending(cls.__name__, df)


#: Day-type categories in code order (``day_type`` = the level's index): a
#: working weekday, a Saturday/Sunday, or a ``dim_date`` holiday — the same
#: labels as the demand compare script's day-type segment.
DAY_TYPE_LEVELS: tuple[str, ...] = ("Weekday", "Weekend", "Holiday")


class DayTypeCalendar(DomainFrame):
    """Day type of every calendar day, as the integer code LightGBM is given.

    ``day_type`` is the index into :data:`DAY_TYPE_LEVELS`: 0 = Weekday (a
    Monday-Friday that is not a holiday), 1 = Weekend (a Saturday/Sunday that
    is not a holiday), 2 = Holiday (``dim_date.is_holiday``: a 国民の祝日 or a
    customary non-working day — 年末年始, ゴールデンウィーク, お盆 — whatever
    weekday it falls on). Holiday takes precedence over Weekend, as in the
    compare script's ``day_type`` segment, so the model's categories line up
    with the research tables.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "day_type": "int64",
    }
    keys = ["trade_date"]
    non_null_cols = ["day_type"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        last = len(DAY_TYPE_LEVELS) - 1
        bad = df.loc[~df["day_type"].between(0, last), "day_type"]
        if not bad.empty:
            codes = sorted(int(code) for code in bad.unique())
            raise ValueError(f"{cls.__name__}: day_type outside 0..{last}: {codes}")


class AreaHourlyLoad(DomainFrame):
    """Hourly area load history: energy over each hour in kWh, as
    ``fct_area_power_usage_hourly`` publishes it (the でんき予報 1時間平均 over
    one hour). ``hour_ending`` is the hour label 1..24 shared with
    :class:`AreaTemperature` (the fact's ``hour_of_day`` + 1), so a delivery
    period maps to its hour through ``hour_ending = (time_code + 1) // 2``.
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

    Same grain and hour convention as :class:`AreaTemperatureForecast`; the
    three measures are nullable (an hour no weighted station forecast). The
    temperature column alone is what the parent strategies consume, exposed
    through :meth:`temperature_forecast`.

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

    def temperature_forecast(self) -> AreaTemperatureForecast:
        """The temperature column as the frame the parent strategies take.

        Returns
        -------
        AreaTemperatureForecast
        """
        return AreaTemperatureForecast.from_df(
            self.df[["trade_date", "hour_ending", "forecast_temperature_c"]]
        )


class AreaObservedWeather(DomainFrame):
    """Hourly population-weighted observed temperature, relative humidity and
    rain for an area (``fct_jma_weather_hourly`` over the weighted stations).

    Same grain and hour convention as :class:`AreaTemperature`; the measures
    are nullable (an hour at which no weighted station reported).

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


class DayCalendar(DomainFrame):
    """Calendar attributes of every ``dim_date`` day the similar-day selector reads.

    ``day_type`` is :class:`DayTypeCalendar`'s code (for the parent strategy);
    ``days_since_holiday`` / ``days_until_holiday`` count calendar days to the
    nearest named holiday (``dim_date.is_holiday``; 0 on a holiday itself);
    ``holiday_degree`` is ``dim_date.holiday_degree``.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "day_type": "int64",
        "days_since_holiday": "int64",
        "days_until_holiday": "int64",
        "holiday_degree": "float64",
    }
    keys = ["trade_date"]
    non_null_cols = ["day_type", "days_since_holiday", "days_until_holiday", "holiday_degree"]

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
        levels = np.asarray(HOLIDAY_DEGREE_LEVELS)
        off = ~np.isclose(df["holiday_degree"].to_numpy()[:, None], levels[None, :]).any(axis=1)
        if off.any():
            values = sorted(float(v) for v in df.loc[off, "holiday_degree"].unique())
            raise ValueError(
                f"{cls.__name__}: holiday_degree outside {HOLIDAY_DEGREE_LEVELS}: {values}"
            )

    def day_types(self) -> DayTypeCalendar:
        """The day-type column as the frame the parent strategy takes.

        Returns
        -------
        DayTypeCalendar
        """
        return DayTypeCalendar.from_df(self.df[["trade_date", "day_type"]])
