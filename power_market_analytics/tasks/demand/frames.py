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


#: ``dim_date.prior_year_reference_rule`` values — how the reference day was
#: chosen: the same weekday 52 weeks back; that weekday one week nearer or
#: farther because D-364 is a holiday; the same-named holiday a year earlier;
#: the nearest non-working day for a holiday without a same-named twin.
PRIOR_YEAR_REFERENCE_RULES: tuple[str, ...] = (
    "same_weekday",
    "same_weekday_shifted",
    "same_holiday",
    "nearest_non_working_day",
)
#: How far back a reference can lie under those rules (dim_date's tests): the
#: weekday rules give 357 / 364 / 371 days, the holiday rules up to 14 days
#: either side of the same calendar date a year earlier (365 or 366 days).
PRIOR_YEAR_REFERENCE_MIN_DAYS = 351
PRIOR_YEAR_REFERENCE_MAX_DAYS = 380
#: Exact lags of the weekday rules: 52 weeks, or 51 / 53 weeks when D-364 is a holiday.
SAME_WEEKDAY_LAG_DAYS = 364
SHIFTED_WEEKDAY_LAG_DAYS: tuple[int, ...] = (357, 371)


class PriorYearCalendar(DomainFrame):
    """``dim_date``'s prior-year reference per delivery day: the day one year
    earlier that stands for it in a year-over-year comparison, and the rule
    that chose it (:data:`PRIOR_YEAR_REFERENCE_RULES`; the rules themselves
    are the dimension's). The frame re-checks the dimension's contract at
    this boundary so a mis-read calendar cannot silently feed the model a
    lag that is not a year old: every reference lies
    :data:`PRIOR_YEAR_REFERENCE_MIN_DAYS`-:data:`PRIOR_YEAR_REFERENCE_MAX_DAYS`
    days back, a ``same_weekday`` one exactly :data:`SAME_WEEKDAY_LAG_DAYS`
    and a ``same_weekday_shifted`` one :data:`SHIFTED_WEEKDAY_LAG_DAYS`.

    Grain: (trade_date).
    """

    schema = {
        "trade_date": "datetime64[ns]",
        "prior_year_reference_date": "datetime64[ns]",
        "prior_year_reference_rule": "object",
    }
    keys = ["trade_date"]
    non_null_cols = ["prior_year_reference_date", "prior_year_reference_rule"]

    @classmethod
    def _validate_extra(cls, df: pd.DataFrame) -> None:
        unknown = sorted(set(df["prior_year_reference_rule"]) - set(PRIOR_YEAR_REFERENCE_RULES))
        if unknown:
            raise ValueError(f"{cls.__name__}: unknown prior_year_reference_rule: {unknown}")
        late = df[df["prior_year_reference_date"] >= df["trade_date"]]
        if not late.empty:
            raise ValueError(
                f"{cls.__name__}: prior_year_reference_date is not before trade_date on "
                f"{len(late)} day(s) (e.g. {late.iloc[0]['trade_date'].date()})"
            )
        lag_days = (df["trade_date"] - df["prior_year_reference_date"]).dt.days
        outside = df[
            (lag_days < PRIOR_YEAR_REFERENCE_MIN_DAYS) | (lag_days > PRIOR_YEAR_REFERENCE_MAX_DAYS)
        ]
        if not outside.empty:
            first = outside.index[0]
            raise ValueError(
                f"{cls.__name__}: prior_year_reference_date must lie "
                f"{PRIOR_YEAR_REFERENCE_MIN_DAYS}-{PRIOR_YEAR_REFERENCE_MAX_DAYS} days before "
                f"trade_date; {len(outside)} day(s) do not "
                f"(e.g. {df.loc[first, 'trade_date'].date()}: {int(lag_days.loc[first])} days)"
            )
        for rule, allowed, label in (
            ("same_weekday", (SAME_WEEKDAY_LAG_DAYS,), f"{SAME_WEEKDAY_LAG_DAYS}"),
            (
                "same_weekday_shifted",
                SHIFTED_WEEKDAY_LAG_DAYS,
                " or ".join(str(d) for d in SHIFTED_WEEKDAY_LAG_DAYS),
            ),
        ):
            bad = df[(df["prior_year_reference_rule"] == rule) & ~lag_days.isin(allowed)]
            if not bad.empty:
                first = bad.index[0]
                raise ValueError(
                    f"{cls.__name__}: a {rule} reference must be {label} days back; "
                    f"{len(bad)} day(s) are not "
                    f"(e.g. {df.loc[first, 'trade_date'].date()}: {int(lag_days.loc[first])} days)"
                )
