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
    load_occto_demand_forecast,
)
from power_market_analytics.tasks.spot_price.frames import OcctoDemandForecast, SpotPrices
from tests.conftest import OCCTO_DAYS, PRICE_DAYS, CuratedWarehouse, synthetic_price


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


def expected_occto(warehouse: CuratedWarehouse) -> pd.DataFrame:
    """The fixture's tokyo OCCTO rows in the ``OcctoDemandForecast`` layout, sorted."""
    return (
        warehouse.occto.assign(
            trade_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]")
        )[["trade_date", "max_demand_hour_ending", "max_demand_mw", "max_supply_capacity_mw"]]
        .astype(
            {
                "max_demand_hour_ending": "int64",
                "max_demand_mw": "int64",
                "max_supply_capacity_mw": "int64",
            }
        )
        .sort_values("trade_date", ignore_index=True)
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


class TestLoadOcctoDemandForecast:
    def test_returns_the_tokyo_forecasts_sorted(self, spark, curated_warehouse):
        occto = load_occto_demand_forecast("tokyo", spark=spark)
        assert isinstance(occto, OcctoDemandForecast)
        assert len(occto) == len(OCCTO_DAYS) == 61
        assert occto.df.dtypes.astype(str).to_dict() == {
            "trade_date": "datetime64[ns]",
            "max_demand_hour_ending": "int64",
            "max_demand_mw": "int64",
            "max_supply_capacity_mw": "int64",
        }
        # Day 0 of the fixture (2024-04-01): hour 17, 40000 MW, 46000 MW;
        # day 4 (2024-04-05): hour 18, 40040 MW, 46040 MW.
        assert occto.df.iloc[0].tolist() == [pd.Timestamp("2024-04-01"), 17, 40_000, 46_000]
        assert occto.df.iloc[4].tolist() == [pd.Timestamp("2024-04-05"), 18, 40_040, 46_040]
        assert occto.df.iloc[-1]["trade_date"] == pd.Timestamp("2024-05-31")
        pd.testing.assert_frame_equal(occto.df, expected_occto(curated_warehouse))

    def test_defaults_to_the_active_session_and_tokyo(self, spark, curated_warehouse):
        occto = load_occto_demand_forecast()
        assert len(occto) == 61
        assert occto.df.iloc[0]["max_demand_mw"] == 40_000

    def test_area_without_forecasts_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No OCCTO demand forecasts found for area_code='kansai'"
        ):
            load_occto_demand_forecast("kansai", spark=spark)
