"""Tests for the demand strategy registry and factory."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.demand.strategies.lgbm import (
    LightGbmMsmPopWeightedDayTypeLag1yStrategy,
    LightGbmMsmPopWeightedDayTypeStrategy,
    LightGbmMsmPopWeightedStrategy,
    LightGbmMsmStrategy,
    LightGbmStrategy,
)
from tests.conftest import TOKYO_STATION_ID, CuratedWarehouse


class TestRegistry:
    def test_registered_names(self):
        assert list(STRATEGIES) == [
            "lightgbm",
            "lightgbm_msm",
            "lightgbm_msm_popw",
            "lightgbm_msm_popw_daytype",
            "lightgbm_msm_popw_daytype_lag1y",
        ]
        assert STRATEGIES["lightgbm"] is LightGbmStrategy
        assert STRATEGIES["lightgbm_msm"] is LightGbmMsmStrategy
        assert STRATEGIES["lightgbm_msm_popw"] is LightGbmMsmPopWeightedStrategy
        assert STRATEGIES["lightgbm_msm_popw_daytype"] is LightGbmMsmPopWeightedDayTypeStrategy
        assert (
            STRATEGIES["lightgbm_msm_popw_daytype_lag1y"]
            is LightGbmMsmPopWeightedDayTypeLag1yStrategy
        )


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
        # The representative station's rows only, not the second station's.
        forecast_rows = curated_warehouse.weather_forecast
        assert (
            len(strategy.temperature_forecast)
            == (forecast_rows["station_id"] == TOKYO_STATION_ID).sum()
        )

    def test_lightgbm_msm_popw_loads_the_population_weighted_forecast(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy("lightgbm_msm_popw", area_code="tokyo", spark=spark)
        assert type(strategy) is LightGbmMsmPopWeightedStrategy
        assert strategy.census_year == 2020
        assert len(strategy.temperature) == len(curated_warehouse.weather)
        # One weighted value per (delivery day, hour): the two stations' rows collapse,
        # and the value lies between the two stations' forecasts (equal to the single
        # present one at the hour the second station lacks).
        by_hour = curated_warehouse.weather_forecast.groupby(["date_key", "hour_ending"])[
            "forecast_temperature_c"
        ]
        weighted = strategy.temperature_forecast.df["forecast_temperature_c"].to_numpy()
        assert len(weighted) == by_hour.ngroups
        assert (weighted >= by_hour.min().to_numpy() - 1e-9).all()
        assert (weighted <= by_hour.max().to_numpy() + 1e-9).all()
        assert (weighted > by_hour.min().to_numpy()).sum() == by_hour.ngroups - 1

    def test_lightgbm_msm_popw_daytype_loads_the_day_type_calendar_too(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype",
            area_code="tokyo",
            train_start_date=pd.Timestamp("2024-04-01"),
            spark=spark,
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert strategy.census_year == 2020
        assert len(strategy.temperature) == len(curated_warehouse.weather)
        by_hour = curated_warehouse.weather_forecast.groupby(["date_key", "hour_ending"])
        assert len(strategy.temperature_forecast) == by_hour.ngroups
        # One coded row per dim_date day.
        assert len(strategy.day_types) == len(curated_warehouse.dates)
        assert set(strategy.day_types.df["day_type"]) == {0, 1, 2}

    def test_lightgbm_msm_popw_does_not_carry_a_day_type_calendar(self, spark, curated_warehouse):
        assert not hasattr(
            build_strategy("lightgbm_msm_popw", area_code="tokyo", spark=spark), "day_types"
        )

    def test_lightgbm_msm_popw_daytype_lag1y_loads_the_prior_year_calendar_and_hourly_load_too(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_lag1y",
            area_code="tokyo",
            train_start_date=pd.Timestamp("2024-04-01"),
            spark=spark,
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeLag1yStrategy
        assert strategy.train_start_date == pd.Timestamp("2024-04-01")
        assert strategy.census_year == 2020
        assert len(strategy.temperature) == len(curated_warehouse.weather)
        by_hour = curated_warehouse.weather_forecast.groupby(["date_key", "hour_ending"])
        assert len(strategy.temperature_forecast) == by_hour.ngroups
        assert len(strategy.day_types) == len(curated_warehouse.dates)
        # One reference per dim_date day; one load per hour of the hourly fact.
        assert len(strategy.prior_year_calendar) == len(curated_warehouse.dates)
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)

    def test_lightgbm_msm_popw_daytype_does_not_carry_the_year_ago_inputs(
        self, spark, curated_warehouse
    ):
        strategy = build_strategy("lightgbm_msm_popw_daytype", area_code="tokyo", spark=spark)
        assert not hasattr(strategy, "prior_year_calendar")
        assert not hasattr(strategy, "hourly_load")

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
