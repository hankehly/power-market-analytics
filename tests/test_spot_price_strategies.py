"""Tests for the spot-price strategy registry and ``build_strategy``."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.spot_price.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.spot_price.strategies.lgbm import (
    LightGbmOcctoStrategy,
    LightGbmStrategy,
)
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy
from tests.conftest import CuratedWarehouse


class TestRegistry:
    def test_registered_names(self):
        assert set(STRATEGIES) == {"previous_day", "lightgbm", "lightgbm_occto"}

    def test_keys_are_the_classes_own_names(self):
        for name, cls in STRATEGIES.items():
            assert cls.name == name


class TestBuildStrategy:
    def test_previous_day(self):
        strategy = build_strategy("previous_day", area_code="tokyo")
        assert type(strategy) is PreviousDayStrategy

    def test_previous_day_rejects_train_start_date(self):
        with pytest.raises(ValueError, match="'previous_day' has no training step"):
            build_strategy(
                "previous_day", area_code="tokyo", train_start_date=pd.Timestamp("2024-04-01")
            )

    def test_lightgbm_forwards_train_start_date(self):
        strategy = build_strategy(
            "lightgbm", area_code="tokyo", train_start_date=pd.Timestamp("2024-04-01")
        )
        assert type(strategy) is LightGbmStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")

    def test_lightgbm_without_train_start_date(self):
        strategy = build_strategy("lightgbm", area_code="tokyo")
        assert type(strategy) is LightGbmStrategy
        assert strategy.train_start_date is None

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError, match="arima"):
            build_strategy("arima", area_code="tokyo")

    def test_lightgbm_occto_loads_the_areas_occto_forecast(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy(
            "lightgbm_occto",
            area_code="tokyo",
            train_start_date=pd.Timestamp("2024-04-01"),
            spark=spark,
        )
        assert type(strategy) is LightGbmOcctoStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        expected = (
            curated_warehouse.occto.assign(
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
        pd.testing.assert_frame_equal(strategy.occto.df, expected)

    def test_lightgbm_occto_for_an_area_without_forecasts_raises(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        with pytest.raises(
            ValueError, match="No OCCTO demand forecasts found for area_code='kansai'"
        ):
            build_strategy("lightgbm_occto", area_code="kansai", spark=spark)
