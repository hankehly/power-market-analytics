# power_market_analytics/tasks/demand/datasets.py
"""Load demand history, observed and forecast temperature for the demand task."""

from __future__ import annotations

from typing import NamedTuple

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
        "load_area_temperature_forecast_population_weighted: {} census weights over {} "
        "stations for {} (largest: {})",
        year,
        len(used),
        area_code,
        ", ".join(
            f"{r.station_id}={r.area_population_weight:.3f}" for r in used.head(5).itertuples()
        ),
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
    pdf = pdf.drop(columns="forecast_reference_at")
    return PopulationWeightedTemperatureForecast(
        forecast=AreaTemperatureForecast.from_df(pdf), census_year=year, n_stations=len(used)
    )
