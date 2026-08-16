"""Load spot price history and exogenous inputs for the forecasting task."""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.tasks.spot_price.frames import OcctoDemandForecast, SpotPrices

# The nine bidding zones unpivoted by fct_jepx_spot_area_price, in the order
# dim_area lists them. dim_area also carries 'system', which is the nationwide
# reference price rather than an area price and has no rows in the fact, so it
# is not forecastable here.
AREA_CODES = (
    "hokkaido",
    "tohoku",
    "tokyo",
    "chubu",
    "hokuriku",
    "kansai",
    "chugoku",
    "shikoku",
    "kyushu",
)


def load_area_spot_prices(
    area_code: str = "tokyo", spark: SparkSession | None = None
) -> SpotPrices:
    """Load the full half-hourly price history for one bidding zone.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    SpotPrices

    Raises
    ------
    ValueError
        If the area returns no rows or the result violates the SpotPrices
        contract.
    """
    pdf = query_pandas(
        f"""
        select
          f.date_key as trade_date,
          f.time_code,
          f.area_price_jpy_kwh as price_jpy_kwh
        from pma_curated.fct_jepx_spot_area_price f
        join pma_curated.dim_area a on f.area_key = a.area_key
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(f"No spot prices found for area_code={area_code!r}")
    pdf = (
        pdf.assign(trade_date=pd.to_datetime(pdf["trade_date"]))
        .astype({"time_code": "int64", "price_jpy_kwh": "float64"})
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )
    return SpotPrices.from_df(pdf)


def load_occto_demand_forecast(
    area_code: str = "tokyo", spark: SparkSession | None = None
) -> OcctoDemandForecast:
    """Load the OCCTO 翌々日 peak-demand/supply forecast for one bidding zone.

    Reads ``fct_occto_demand_forecast_dad``, which already excludes the
    pre-FY2024 trial rows, so the history starts on 2024-04-01.

    Parameters
    ----------
    area_code : str, default "tokyo"
        dim_area.area_code value, e.g. ``tokyo``.
    spark : pyspark.sql.SparkSession, optional
        Existing session to reuse.

    Returns
    -------
    OcctoDemandForecast

    Raises
    ------
    ValueError
        If the area returns no rows or the result violates the
        OcctoDemandForecast contract.
    """
    pdf = query_pandas(
        f"""
        select
          f.date_key as trade_date,
          f.max_demand_hour_ending,
          f.max_demand_mw,
          f.max_supply_capacity_mw
        from pma_curated.fct_occto_demand_forecast_dad f
        join pma_curated.dim_area a on f.area_key = a.area_key
        where a.area_code = '{area_code}'
        """,
        spark=spark,
    )
    if pdf.empty:
        raise ValueError(f"No OCCTO demand forecasts found for area_code={area_code!r}")
    pdf = (
        pdf.assign(trade_date=pd.to_datetime(pdf["trade_date"]))
        .astype(
            {
                "max_demand_hour_ending": "int64",
                "max_demand_mw": "int64",
                "max_supply_capacity_mw": "int64",
            }
        )
        .sort_values("trade_date", ignore_index=True)
    )
    return OcctoDemandForecast.from_df(pdf)
