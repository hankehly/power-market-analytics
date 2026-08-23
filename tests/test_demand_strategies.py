"""Tests for the demand strategy registry and factory."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.demand.strategies.lgbm import (
    LightGbmMsmStrategy,
    LightGbmStrategy,
)
from tests.conftest import CuratedWarehouse


class TestRegistry:
    def test_registered_names(self):
        assert list(STRATEGIES) == ["lightgbm", "lightgbm_msm"]
        assert STRATEGIES["lightgbm"] is LightGbmStrategy
        assert STRATEGIES["lightgbm_msm"] is LightGbmMsmStrategy


class TestBuildStrategy:
    def test_lightgbm_loads_the_areas_temperature(self, spark, curated_warehouse: CuratedWarehouse):
        strategy = build_strategy(
            "lightgbm", area_code="tokyo", train_start_date=pd.Timestamp("2024-04-01"), spark=spark
        )
        assert type(strategy) is LightGbmStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert len(strategy.temperature) == len(curated_warehouse.weather)

    def test_without_train_start_date(self, spark, curated_warehouse):
        assert build_strategy("lightgbm", area_code="tokyo", spark=spark).train_start_date is None

    def test_lightgbm_does_not_carry_a_temperature_forecast(self, spark, curated_warehouse):
        assert not hasattr(
            build_strategy("lightgbm", area_code="tokyo", spark=spark), "temperature_forecast"
        )

    def test_lightgbm_msm_loads_the_areas_temperature_and_its_forecast(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy(
            "lightgbm_msm",
            area_code="tokyo",
            train_start_date=pd.Timestamp("2024-04-01"),
            spark=spark,
        )
        assert type(strategy) is LightGbmMsmStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert len(strategy.temperature) == len(curated_warehouse.weather)
        assert len(strategy.temperature_forecast) == len(curated_warehouse.weather_forecast)

    def test_lightgbm_msm_area_without_weather_fails_on_the_observations_first(
        self, spark, curated_warehouse
    ):
        # kansai has neither observations nor forecasts; the observations are loaded first.
        with pytest.raises(
            ValueError, match="No temperature observations found for area_code='kansai'"
        ):
            build_strategy("lightgbm_msm", area_code="kansai", spark=spark)

    def test_area_without_temperature_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No temperature observations found for area_code='kansai'"
        ):
            build_strategy("lightgbm", area_code="kansai", spark=spark)

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError, match="arima"):
            build_strategy("arima", area_code="tokyo")
