"""Tests for the demand strategy registry and factory."""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.strategies import STRATEGIES, build_strategy
from power_market_analytics.tasks.demand.strategies.lgbm import (
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
    LightGbmMsmPopWeightedDayTypeSimilarDayStrategy,
    LightGbmMsmPopWeightedDayTypeStrategy,
    LightGbmMsmPopWeightedStrategy,
    LightGbmMsmStrategy,
    LightGbmStrategy,
)
from tests.conftest import (
    CALENDAR_DAYS,
    HOLIDAYS_2024_SPRING,
    HOURLY_LOAD_DAYS,
    TOKYO_STATION_ID,
    CuratedWarehouse,
)
from tests.test_demand_datasets import expected_day_type


class TestRegistry:
    def test_registered_names(self):
        assert list(STRATEGIES) == [
            "lightgbm",
            "lightgbm_msm",
            "lightgbm_msm_popw",
            "lightgbm_msm_popw_daytype",
            "lightgbm_msm_popw_daytype_simday",
            "lightgbm_msm_popw_daytype_simday_calendar",
            "lightgbm_msm_popw_daytype_simday_calendarcounts",
            "lightgbm_msm_popw_daytype_simday_holidaydegree",
            "lightgbm_msm_popw_daytype_simday_holidaydistance",
        ]
        assert STRATEGIES["lightgbm"] is LightGbmStrategy
        assert (
            STRATEGIES["lightgbm_msm_popw_daytype_simday"]
            is LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
        )
        assert (
            STRATEGIES["lightgbm_msm_popw_daytype_simday_calendar"]
            is LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy
        )
        assert (
            STRATEGIES["lightgbm_msm_popw_daytype_simday_calendarcounts"]
            is LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy
        )
        assert (
            STRATEGIES["lightgbm_msm_popw_daytype_simday_holidaydegree"]
            is LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy
        )
        assert (
            STRATEGIES["lightgbm_msm_popw_daytype_simday_holidaydistance"]
            is LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy
        )
        assert STRATEGIES["lightgbm_msm"] is LightGbmMsmStrategy
        assert STRATEGIES["lightgbm_msm_popw"] is LightGbmMsmPopWeightedStrategy
        assert STRATEGIES["lightgbm_msm_popw_daytype"] is LightGbmMsmPopWeightedDayTypeStrategy


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

    def test_similar_day_strategy_loads_its_five_inputs(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_simday", area_code="tokyo", spark=spark
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeSimilarDayStrategy
        assert strategy.census_year == 2020
        assert len(strategy.temperature) == len(curated_warehouse.weather)
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        assert strategy.selector.first_candidate_day == min(HOLIDAYS_2024_SPRING)
        assert strategy.selector.hourly_load_span == (HOURLY_LOAD_DAYS[0], HOURLY_LOAD_DAYS[-1])
        assert strategy.day_types.df["day_type"].tolist() == [
            expected_day_type(d) for d in strategy.day_types.df["trade_date"]
        ]

    def test_calendar_strategy_loads_the_similar_day_inputs_and_keeps_the_calendar(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_simday_calendar", area_code="tokyo", spark=spark
        )
        assert type(strategy) is LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy
        assert strategy.census_year == 2020
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        # The whole dim_date spine between its first and last holiday, with the counts.
        first, last = min(HOLIDAYS_2024_SPRING), max(HOLIDAYS_2024_SPRING)
        assert len(strategy.day_calendar) == (last - first).days + 1
        assert len(strategy.day_calendar) < len(CALENDAR_DAYS)
        assert "day_of_quarter" in strategy.day_calendar.df.columns

    @pytest.mark.parametrize(
        ("name", "cls"),
        [
            (
                "lightgbm_msm_popw_daytype_simday_holidaydegree",
                LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDegreeStrategy,
            ),
            (
                "lightgbm_msm_popw_daytype_simday_holidaydistance",
                LightGbmMsmPopWeightedDayTypeSimilarDayHolidayDistanceStrategy,
            ),
            (
                "lightgbm_msm_popw_daytype_simday_calendarcounts",
                LightGbmMsmPopWeightedDayTypeSimilarDayCalendarCountStrategy,
            ),
        ],
    )
    def test_calendar_subset_strategies_load_the_similar_day_inputs(
        self, spark, curated_warehouse: CuratedWarehouse, name, cls
    ):
        strategy = build_strategy(name, area_code="tokyo", spark=spark)
        assert type(strategy) is cls
        assert strategy.census_year == 2020
        assert len(strategy.hourly_load) == len(curated_warehouse.hourly_load)
        assert set(strategy.calendar_feature_cols) <= set(strategy.day_calendar.df.columns)
