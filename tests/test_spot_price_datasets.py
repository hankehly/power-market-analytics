"""Tests for the warehouse readers feeding the spot-price task.

Read against the synthetic ``pma_curated`` star from ``curated_warehouse``:
tokyo has prices for ``PRICE_DAYS`` and OCCTO forecasts for ``OCCTO_DAYS``,
kansai has an area row but no facts.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.spot_price.datasets import (
    AREA_CODES,
    load_area_spot_prices,
)
from power_market_analytics.tasks.spot_price.frames import SpotPrices
from tests.conftest import PRICE_DAYS, CuratedWarehouse, synthetic_price


def test_area_codes_are_the_nine_bidding_zones_in_dim_area_order():
    assert AREA_CODES == (
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


def expected_prices(warehouse: CuratedWarehouse) -> pd.DataFrame:
    """The fixture's tokyo price rows in the ``SpotPrices`` layout, sorted."""
    return (
        warehouse.prices.assign(
            trade_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]")
        )
        .rename(columns={"area_price_jpy_kwh": "price_jpy_kwh"})[
            ["trade_date", "time_code", "price_jpy_kwh"]
        ]
        .astype({"time_code": "int64", "price_jpy_kwh": "float64"})
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )


class TestLoadAreaSpotPrices:
    def test_returns_the_full_tokyo_history_sorted(self, spark, curated_warehouse):
        prices = load_area_spot_prices("tokyo", spark=spark)
        assert isinstance(prices, SpotPrices)
        assert len(prices) == len(PRICE_DAYS) * 48 == 4416
        assert prices.df.dtypes.astype(str).to_dict() == {
            "trade_date": "datetime64[ns]",
            "time_code": "int64",
            "price_jpy_kwh": "float64",
        }
        # Sorted by (trade_date, time_code): first and last rows are the
        # first day's period 1 and the last day's period 48.
        first, last = prices.df.iloc[0], prices.df.iloc[-1]
        assert (first["trade_date"], first["time_code"]) == (pd.Timestamp("2024-03-01"), 1)
        assert (last["trade_date"], last["time_code"]) == (pd.Timestamp("2024-05-31"), 48)
        assert first["price_jpy_kwh"] == synthetic_price(pd.Timestamp("2024-03-01"), 1)
        # A cell from the middle of the history, and every cell against the
        # frame the fixture wrote.
        cell = prices.df.set_index(["trade_date", "time_code"]).loc[
            (pd.Timestamp("2024-04-15"), 30), "price_jpy_kwh"
        ]
        assert cell == synthetic_price(pd.Timestamp("2024-04-15"), 30)
        pd.testing.assert_frame_equal(prices.df, expected_prices(curated_warehouse))

    def test_defaults_to_the_active_session_and_tokyo(self, spark, curated_warehouse):
        prices = load_area_spot_prices()
        assert len(prices) == 4416
        assert prices.df.iloc[-1]["price_jpy_kwh"] == synthetic_price(
            pd.Timestamp("2024-05-31"), 48
        )

    def test_area_without_prices_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No spot prices found for area_code='kansai'"):
            load_area_spot_prices("kansai", spark=spark)
