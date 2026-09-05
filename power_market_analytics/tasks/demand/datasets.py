# power_market_analytics/tasks/demand/datasets.py
"""Load demand history, observed and forecast temperature, the day-type
calendar and, for the similar-day selector, the hourly load, the holiday
calendar and the population-weighted weather profiles of the demand task."""

from __future__ import annotations

from typing import NamedTuple

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.features import day_type_code
from power_market_analytics.tasks.demand.frames import (
    DAY_TYPE_LEVELS,
    AreaDemand,
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaTemperature,
    AreaTemperatureForecast,
    AreaWeatherForecast,
    DayCalendar,
    DayTypeCalendar,
)

# The areas whose TSO actuals feed fct_area_demand_generation_actual (one
# union branch per TSO). Extend together with that model.
AREA_CODES = (
    "tokyo",
    "kansai",
)


def load_area_demand(area_code: str = "tokyo", spark: SparkSession | None = None) -> AreaDemand:
    """Load the full half-hourly demand history for one area.

    Rows whose ``demand_kwh`` is null (a TSO hole such as Tokyo 2025-06-14
    time codes 11-48) are dropped, so the returned grain can be sparse on
    those days; the count is logged.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    AreaDemand

    Raises
    ------
    ValueError
        If the area returns no rows or the result violates the AreaDemand
        contract.
    """
    pdf = query_pandas(
        f"""
        select
          f.date_key as trade_date,
          f.time_code,
          f.demand_kwh
        from pma_curated.fct_area_demand_generation_actual f
        join pma_curated.dim_area a on f.area_key = a.area_key
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(f"No demand actuals found for area_code={area_code!r}")
    # Logged unconditionally (a zero is informative too, and it keeps the
    # function branch-free for the coverage gate).
    logger.info(
        "load_area_demand: {} of {} rows have null demand_kwh and are dropped ({})",
        int(pdf["demand_kwh"].isna().sum()),
        len(pdf),
        area_code,
    )
    pdf = (
        pdf.dropna(subset=["demand_kwh"])
        .assign(trade_date=lambda d: pd.to_datetime(d["trade_date"]))
        .astype({"time_code": "int64", "demand_kwh": "float64"})
        .sort_values(GRAIN_COLS, ignore_index=True)
    )
    return AreaDemand.from_df(pdf)


def load_area_temperature(
    area_code: str = "tokyo", spark: SparkSession | None = None
) -> AreaTemperature:
    """Load hourly temperature at the area's representative JMA station.

    Reads ``fct_jma_weather_hourly`` for the station named by
    ``dim_area.representative_jma_station_id``; ``hour_ending`` is derived
    from ``observed_hour_start_at`` so the 24:00 reading stays on its
    observation day as hour 24.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    AreaTemperature

    Raises
    ------
    ValueError
        If the area has no representative station, the station has no
        observations, or the result violates the AreaTemperature contract.
    """
    pdf = query_pandas(
        f"""
        select
          w.date_key as obs_date,
          hour(w.observed_hour_start_at) + 1 as hour_ending,
          w.temperature_c
        from pma_curated.fct_jma_weather_hourly w
        join pma_curated.dim_area a on w.station_id = a.representative_jma_station_id
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No temperature observations found for area_code={area_code!r} "
            "(no representative station, or no weather rows for it)"
        )
    pdf = (
        pdf.assign(obs_date=lambda d: pd.to_datetime(d["obs_date"]))
        .astype({"hour_ending": "int64", "temperature_c": "float64"})
        .sort_values(["obs_date", "hour_ending"], ignore_index=True)
    )
    return AreaTemperature.from_df(pdf)


def load_area_temperature_forecast(
    area_code: str = "tokyo", spark: SparkSession | None = None
) -> AreaTemperatureForecast:
    """Load the hourly MSM forecast temperature at the area's representative station.

    Reads ``fct_jma_msm_weather_forecast_hourly`` for the station named by
    ``dim_area.representative_jma_station_id``. The pipeline ingests one
    vintage per delivery day — the 12 UTC run of D-2 (``forecast_reference_at``
    = 21:00 JST D-2, leads 28-51), which JMA disseminates a few hours after
    its reference time and is therefore available well before the task's
    09:30 JST D-1 issue time; the frame's unique grain fails fast should a
    second vintage ever be loaded. ``hour_ending`` is derived from
    ``forecast_hour_start_at`` so the hour valid at next-day 00:00 stays on
    its delivery day as hour 24, exactly like the observed series.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    AreaTemperatureForecast

    Raises
    ------
    ValueError
        If the area has no representative station, the station has no
        forecast rows, or the result violates the AreaTemperatureForecast
        contract (e.g. two vintages for one hour).
    """
    pdf = query_pandas(
        f"""
        select
          m.date_key as trade_date,
          hour(m.forecast_hour_start_at) + 1 as hour_ending,
          m.temperature_c as forecast_temperature_c
        from pma_curated.fct_jma_msm_weather_forecast_hourly m
        join pma_curated.dim_area a on m.station_id = a.representative_jma_station_id
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No temperature forecasts found for area_code={area_code!r} "
            "(no representative station, or no MSM forecast rows for it)"
        )
    pdf = (
        pdf.assign(trade_date=lambda d: pd.to_datetime(d["trade_date"]))
        .astype({"hour_ending": "int64", "forecast_temperature_c": "float64"})
        .sort_values(["trade_date", "hour_ending"], ignore_index=True)
    )
    return AreaTemperatureForecast.from_df(pdf)


def _load_station_weights(
    area_code: str, census_year: int | None, spark: SparkSession | None, caller: str
) -> tuple[int, pd.DataFrame]:
    """Census population weights of an area's stations for one vintage.

    Parameters
    ----------
    area_code : str
        dim_area.area_code value.
    census_year : int or None
        Vintage to use; None = the latest loaded.
    spark : pyspark.sql.SparkSession or None
        Existing session to reuse.
    caller : str
        Loader name for the log line.

    Returns
    -------
    tuple of int and pandas.DataFrame
        The vintage used and its weights (``station_id``,
        ``area_population_weight``), largest first.

    Raises
    ------
    ValueError
        If the area has no weights (for that vintage).
    """
    year_clause = "" if census_year is None else f"and w.census_year = {int(census_year)}"
    weights = query_pandas(
        f"""
        select
          w.census_year,
          w.station_id,
          w.area_population_weight
        from pma_curated.fct_census_population_jma_station w
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}' {year_clause}
        """,
        spark=spark,
    )
    if weights.empty:
        detail = "" if census_year is None else f", census_year={int(census_year)}"
        raise ValueError(f"No station population weights found for area_code={area_code!r}{detail}")
    year = int(weights["census_year"].max())
    used = weights[weights["census_year"] == year].sort_values(
        "area_population_weight", ascending=False
    )
    logger.info(
        "{}: {} census weights over {} stations for {} (largest: {})",
        caller,
        year,
        len(used),
        area_code,
        ", ".join(
            f"{r.station_id}={r.area_population_weight:.3f}" for r in used.head(5).itertuples()
        ),
    )
    return year, used


def _reject_multiple_vintages(pdf: pd.DataFrame, area_code: str) -> None:
    """Fail when a weighted forecast frame holds two vintages for one delivery-day hour.

    Parameters
    ----------
    pdf : pandas.DataFrame
        Rows with ``trade_date``, ``hour_ending`` and ``forecast_reference_at``.
    area_code : str
        For the error message.

    Raises
    ------
    ValueError
        If any delivery-day hour appears under more than one reference time.
    """
    hour_keys = ["trade_date", "hour_ending"]
    duplicated = pdf[pdf.duplicated(subset=hour_keys, keep=False)]
    if not duplicated.empty:
        first_day, first_hour = duplicated.iloc[0][hour_keys]
        first = duplicated[
            (duplicated["trade_date"] == first_day) & (duplicated["hour_ending"] == first_hour)
        ]
        raise ValueError(
            f"{len(duplicated)} forecast vintages for "
            f"{duplicated.groupby(hour_keys).ngroups} delivery-day hour(s) of "
            f"area_code={area_code!r} (e.g. {first_day.date()} hour {first_hour}: "
            + ", ".join(
                pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") for t in first["forecast_reference_at"]
            )
            + "); the population-weighted average is defined within one vintage"
        )


def _weighted_mean_sql(measure: str, alias: str) -> str:
    """The SQL for a population-weighted mean renormalised over stations with a value.

    Parameters
    ----------
    measure : str
        Column of the fact aliased ``m``.
    alias : str
        Output column name.

    Returns
    -------
    str
    """
    return (
        f"sum(w.area_population_weight * m.{measure}) "
        f"/ sum(case when m.{measure} is not null then w.area_population_weight end) as {alias}"
    )


class PopulationWeightedTemperatureForecast(NamedTuple):
    """A population-weighted area forecast temperature plus its weighting provenance.

    Attributes
    ----------
    forecast : AreaTemperatureForecast
        The weighted hourly forecast temperature by delivery day.
    census_year : int
        Census vintage of the station weights that were applied.
    n_stations : int
        Number of weighted stations in the area for that vintage.
    """

    forecast: AreaTemperatureForecast
    census_year: int
    n_stations: int


def load_area_temperature_forecast_population_weighted(
    area_code: str = "tokyo",
    census_year: int | None = None,
    spark: SparkSession | None = None,
) -> PopulationWeightedTemperatureForecast:
    """Load the population-weighted hourly MSM forecast temperature for an area.

    Averages ``fct_jma_msm_weather_forecast_hourly`` over the area's staffed
    stations with the census population weights of
    ``fct_census_population_jma_station`` (each station weighted by the share
    of the area's population living in the 500 m meshes nearest to it), per
    delivery day and hour-ending. Hours at which some stations have no
    forecast value are averaged over the stations that do (weights
    renormalised), so an hour is absent only when no weighted station has a
    value. One census vintage's weights are applied to the whole history: the
    latest loaded vintage by default. The same forecast vintage and hour
    convention as :func:`load_area_temperature_forecast` apply, and the
    average is taken within a forecast vintage (``forecast_reference_at``):
    should the fact ever hold two vintages for one delivery-day hour, this
    raises rather than blending runs into a temperature no single forecast
    ever gave.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    census_year : int, optional
        Census vintage whose weights to use; default: the latest loaded.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    PopulationWeightedTemperatureForecast
        The weighted forecast with the census year and station count used.

    Raises
    ------
    ValueError
        If the area has no station population weights (for that census
        year), none of its weighted stations has forecast rows, more than one
        forecast vintage covers a delivery-day hour, or the result violates
        the AreaTemperatureForecast contract.
    """
    year, used = _load_station_weights(
        area_code, census_year, spark, "load_area_temperature_forecast_population_weighted"
    )
    pdf = query_pandas(
        f"""
        select
          m.date_key as trade_date,
          hour(m.forecast_hour_start_at) + 1 as hour_ending,
          m.forecast_reference_at,
          sum(w.area_population_weight * m.temperature_c)
            / sum(case when m.temperature_c is not null then w.area_population_weight end)
            as forecast_temperature_c
        from pma_curated.fct_jma_msm_weather_forecast_hourly m
        join pma_curated.fct_census_population_jma_station w
          on w.station_id = m.station_id and w.census_year = {year}
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}'
        group by 1, 2, 3
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No temperature forecasts found for the weighted stations of "
            f"area_code={area_code!r} (census_year={year})"
        )
    pdf = (
        pdf.assign(trade_date=lambda d: pd.to_datetime(d["trade_date"]))
        .astype({"hour_ending": "int64", "forecast_temperature_c": "float64"})
        .sort_values(["trade_date", "hour_ending", "forecast_reference_at"], ignore_index=True)
    )
    _reject_multiple_vintages(pdf, area_code)
    pdf = pdf.drop(columns="forecast_reference_at")
    return PopulationWeightedTemperatureForecast(
        forecast=AreaTemperatureForecast.from_df(pdf), census_year=year, n_stations=len(used)
    )


def load_day_types(spark: SparkSession | None = None) -> DayTypeCalendar:
    """Load the day type of every calendar day in ``dim_date``.

    Codes each day with :func:`day_type_code` from the dimension's
    ``is_weekend`` / ``is_holiday`` flags, so ``is_holiday``'s definition (the
    国民の祝日 plus the customary 年末年始 / ゴールデンウィーク / お盆 days) is
    the feature's. The whole spine is returned (2016 through the end of the
    holiday seed's last year); a delivery day beyond it has no day type and is
    unforecastable for a strategy that needs one.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    DayTypeCalendar

    Raises
    ------
    ValueError
        If ``dim_date`` returns no rows or the result violates the
        DayTypeCalendar contract.
    """
    pdf = query_pandas(
        """
        select
          d.date_key as trade_date,
          d.is_weekend,
          d.is_holiday
        from pma_curated.dim_date d
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError("No calendar days found in dim_date")
    pdf = pdf.assign(
        trade_date=lambda d: pd.to_datetime(d["trade_date"]),
        day_type=lambda d: day_type_code(d["is_weekend"], d["is_holiday"]),
    ).sort_values("trade_date", ignore_index=True)
    counts = pdf["day_type"].value_counts()
    logger.info(
        "load_day_types: {} days ({})",
        len(pdf),
        ", ".join(
            f"{level}={int(counts.get(code, 0))}" for code, level in enumerate(DAY_TYPE_LEVELS)
        ),
    )
    return DayTypeCalendar.from_df(pdf)


def load_area_hourly_load(
    area_code: str = "tokyo", spark: SparkSession | None = None
) -> AreaHourlyLoad:
    """Load the full hourly load history of one area.

    Reads ``fct_area_power_usage_hourly`` — the TSO でんき予報 hourly
    電力使用状況 series (Tokyo: 2016-04-01 onward, gapless), energy over the
    hour in kWh — the source of the demand task's similar-day load feature. It
    is the only public area demand before the A-1 series begins (2022-04) and,
    by research decision (demand/R-004), the single source for the whole
    history rather than a stitch with the A-1 fact. ``hour_ending`` is the
    fact's ``hour_of_day`` + 1, the hour convention of the temperature frames.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    AreaHourlyLoad

    Raises
    ------
    ValueError
        If the area has no hourly load rows or the result violates the
        AreaHourlyLoad contract.
    """
    pdf = query_pandas(
        f"""
        select
          f.date_key as load_date,
          f.hour_of_day + 1 as hour_ending,
          f.demand_kwh
        from pma_curated.fct_area_power_usage_hourly f
        join pma_curated.dim_area a on f.area_key = a.area_key
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(f"No hourly load history found for area_code={area_code!r}")
    pdf = (
        pdf.assign(load_date=lambda d: pd.to_datetime(d["load_date"]))
        .astype({"hour_ending": "int64", "demand_kwh": "float64"})
        .sort_values(["load_date", "hour_ending"], ignore_index=True)
    )
    logger.info(
        "load_area_hourly_load: {} hours over {} days ({}..{}) for {}",
        len(pdf),
        pdf["load_date"].nunique(),
        pdf["load_date"].iloc[0].date(),
        pdf["load_date"].iloc[-1].date(),
        area_code,
    )
    return AreaHourlyLoad.from_df(pdf)


def load_day_calendar(spark: SparkSession | None = None) -> DayCalendar:
    """Load the calendar attributes the similar-day selector reads from ``dim_date``.

    ``day_type`` is :func:`day_type_code` of the weekend / holiday flags; the
    two holiday distances count calendar days to the nearest named holiday
    (``is_holiday``: the 国民の祝日 plus the customary 年末年始 / ゴールデン
    ウィーク / お盆 days; 0 on a holiday itself), computed over the gapless
    spine with a forward and a backward fill; days before the spine's first
    holiday or after its last have no distance and are dropped.
    ``holiday_degree`` is the dimension's column.

    Parameters
    ----------
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    DayCalendar

    Raises
    ------
    ValueError
        If ``dim_date`` returns no rows or the result violates the
        DayCalendar contract.
    """
    pdf = query_pandas(
        """
        select
          d.date_key as trade_date,
          d.is_weekend,
          d.is_holiday,
          d.holiday_degree
        from pma_curated.dim_date d
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError("No calendar days found in dim_date")
    pdf = pdf.assign(trade_date=lambda d: pd.to_datetime(d["trade_date"])).sort_values(
        "trade_date", ignore_index=True
    )
    holiday_dates = pdf["trade_date"].where(pdf["is_holiday"].astype(bool))
    last_holiday = holiday_dates.ffill()
    next_holiday = holiday_dates.bfill()
    pdf = (
        pdf.assign(
            day_type=lambda d: day_type_code(d["is_weekend"], d["is_holiday"]),
            days_since_holiday=(pdf["trade_date"] - last_holiday).dt.days,
            days_until_holiday=(next_holiday - pdf["trade_date"]).dt.days,
        )
        .dropna(subset=["days_since_holiday", "days_until_holiday"])
        .astype(
            {
                "days_since_holiday": "int64",
                "days_until_holiday": "int64",
                "holiday_degree": "float64",
            }
        )
        .reset_index(drop=True)
    )
    logger.info(
        "load_day_calendar: {} days ({}..{}), {} holidays, holiday_degree > 0 on {} days",
        len(pdf),
        pdf["trade_date"].iloc[0].date(),
        pdf["trade_date"].iloc[-1].date(),
        int(pdf["is_holiday"].astype(bool).sum()),
        int((pdf["holiday_degree"] > 0).sum()),
    )
    return DayCalendar.from_df(pdf)


class PopulationWeightedWeatherForecast(NamedTuple):
    """A population-weighted area weather forecast plus its weighting provenance.

    Attributes
    ----------
    forecast : AreaWeatherForecast
        The weighted hourly forecast of temperature, humidity and rain by delivery day.
    census_year : int
        Census vintage of the station weights that were applied.
    n_stations : int
        Number of weighted stations in the area for that vintage.
    """

    forecast: AreaWeatherForecast
    census_year: int
    n_stations: int


def load_area_weather_forecast_population_weighted(
    area_code: str = "tokyo",
    census_year: int | None = None,
    spark: SparkSession | None = None,
) -> PopulationWeightedWeatherForecast:
    """Load the population-weighted hourly MSM forecast of temperature, humidity and rain.

    The temperature is exactly
    :func:`load_area_temperature_forecast_population_weighted`'s;
    ``relative_humidity_pct`` and ``precipitation_mm`` are weighted the same
    way, each renormalised over the stations that have a value for the hour.
    One vintage per delivery-day hour, as there.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value.
    census_year : int, optional
        Census vintage whose weights to use; default: the latest loaded.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    PopulationWeightedWeatherForecast

    Raises
    ------
    ValueError
        If the area has no weights, none of its weighted stations has forecast
        rows, more than one forecast vintage covers a delivery-day hour, or the
        result violates the AreaWeatherForecast contract.
    """
    year, used = _load_station_weights(
        area_code, census_year, spark, "load_area_weather_forecast_population_weighted"
    )
    measures = ", ".join(
        _weighted_mean_sql(measure, alias)
        for measure, alias in (
            ("temperature_c", "forecast_temperature_c"),
            ("relative_humidity_pct", "forecast_relative_humidity_pct"),
            ("precipitation_mm", "forecast_precipitation_mm"),
        )
    )
    pdf = query_pandas(
        f"""
        select
          m.date_key as trade_date,
          hour(m.forecast_hour_start_at) + 1 as hour_ending,
          m.forecast_reference_at,
          {measures}
        from pma_curated.fct_jma_msm_weather_forecast_hourly m
        join pma_curated.fct_census_population_jma_station w
          on w.station_id = m.station_id and w.census_year = {year}
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}'
        group by 1, 2, 3
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No weather forecasts found for the weighted stations of "
            f"area_code={area_code!r} (census_year={year})"
        )
    pdf = (
        pdf.assign(trade_date=lambda d: pd.to_datetime(d["trade_date"]))
        .astype(
            {
                "hour_ending": "int64",
                "forecast_temperature_c": "float64",
                "forecast_relative_humidity_pct": "float64",
                "forecast_precipitation_mm": "float64",
            }
        )
        .sort_values(["trade_date", "hour_ending", "forecast_reference_at"], ignore_index=True)
    )
    _reject_multiple_vintages(pdf, area_code)
    return PopulationWeightedWeatherForecast(
        forecast=AreaWeatherForecast.from_df(pdf.drop(columns="forecast_reference_at")),
        census_year=year,
        n_stations=len(used),
    )


class PopulationWeightedObservedWeather(NamedTuple):
    """A population-weighted observed area weather series plus its weighting provenance.

    Attributes
    ----------
    weather : AreaObservedWeather
        The weighted hourly observed temperature, humidity and rain.
    census_year : int
        Census vintage of the station weights that were applied.
    n_stations : int
        Number of weighted stations in the area for that vintage.
    """

    weather: AreaObservedWeather
    census_year: int
    n_stations: int


def load_area_observed_weather_population_weighted(
    area_code: str = "tokyo",
    census_year: int | None = None,
    spark: SparkSession | None = None,
) -> PopulationWeightedObservedWeather:
    """Load the population-weighted hourly observed temperature, humidity and rain.

    Averages ``fct_jma_weather_hourly`` over the area's weighted stations with
    the census weights, each measure renormalised over the stations reporting
    it for the hour; a station without observations simply carries no weight.
    ``hour_ending`` follows :func:`load_area_temperature` (the 24:00 reading
    stays on its observation day as hour 24).

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value.
    census_year : int, optional
        Census vintage whose weights to use; default: the latest loaded.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    PopulationWeightedObservedWeather

    Raises
    ------
    ValueError
        If the area has no weights, none of its weighted stations has
        observations, or the result violates the AreaObservedWeather contract.
    """
    year, used = _load_station_weights(
        area_code, census_year, spark, "load_area_observed_weather_population_weighted"
    )
    measures = ", ".join(
        _weighted_mean_sql(measure, measure)
        for measure in ("temperature_c", "humidity_pct", "precipitation_mm")
    )
    pdf = query_pandas(
        f"""
        select
          m.date_key as obs_date,
          hour(m.observed_hour_start_at) + 1 as hour_ending,
          {measures}
        from pma_curated.fct_jma_weather_hourly m
        join pma_curated.fct_census_population_jma_station w
          on w.station_id = m.station_id and w.census_year = {year}
        join pma_curated.dim_area a on w.area_key = a.area_key
        where a.area_code = '{area_code}'
        group by 1, 2
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(
            f"No weather observations found for the weighted stations of "
            f"area_code={area_code!r} (census_year={year})"
        )
    pdf = (
        pdf.assign(obs_date=lambda d: pd.to_datetime(d["obs_date"]))
        .astype(
            {
                "hour_ending": "int64",
                "temperature_c": "float64",
                "humidity_pct": "float64",
                "precipitation_mm": "float64",
            }
        )
        .sort_values(["obs_date", "hour_ending"], ignore_index=True)
    )
    return PopulationWeightedObservedWeather(
        weather=AreaObservedWeather.from_df(pdf), census_year=year, n_stations=len(used)
    )
