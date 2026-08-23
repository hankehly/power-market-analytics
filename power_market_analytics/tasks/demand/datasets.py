# power_market_analytics/tasks/demand/datasets.py
"""Load demand history, observed and forecast temperature for the demand task."""

from __future__ import annotations

import pandas as pd
from loguru import logger
from pyspark.sql import SparkSession

from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.forecasting.frames import GRAIN_COLS
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaTemperature,
    AreaTemperatureForecast,
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
