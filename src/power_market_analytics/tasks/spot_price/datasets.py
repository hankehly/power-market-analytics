"""Load spot price history for the forecasting task from the warehouse."""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession

from power_market_analytics.common.warehouse import query_pandas
from power_market_analytics.tasks.spot_price.frames import SpotPrices


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
