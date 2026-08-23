# tests/test_demand_datasets.py
"""Tests for the warehouse readers feeding the demand task.

Read against the synthetic ``pma_curated`` star from ``curated_warehouse``:
tokyo has demand actuals for ``DEMAND_DAYS`` (with a partial-day hole),
hourly temperature at its representative station and an MSM forecast
temperature for every day but ``FORECAST_MISSING_DAY``; kansai has an area
row and a station id but no facts.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    AREA_CODES,
    PopulationWeightedTemperatureForecast,
    load_area_demand,
    load_area_temperature,
    load_area_temperature_forecast,
    load_area_temperature_forecast_population_weighted,
)
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaTemperature,
    AreaTemperatureForecast,
)
from tests.conftest import (
    DEMAND_DAYS,
    DEMAND_HOLE_DAY,
    DEMAND_HOLE_TIME_CODES,
    FORECAST_MISSING_DAY,
    SECOND_STATION_FORECAST_OFFSET_C,
    SECOND_STATION_MISSING_HOUR,
    STATION_POPULATION_WEIGHTS,
    TEMPERATURE_MISSING_HOURS,
    TOKYO_SECOND_STATION_ID,
    TOKYO_STATION_ID,
    CuratedWarehouse,
)


def test_area_codes_are_the_areas_with_a_tso_feed():
    assert AREA_CODES == ("tokyo", "kansai")


def expected_demand(warehouse: CuratedWarehouse) -> pd.DataFrame:
    return (
        warehouse.demand.dropna(subset=["demand_kwh"])
        .assign(trade_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]"))[
            ["trade_date", "time_code", "demand_kwh"]
        ]
        .astype({"time_code": "int64", "demand_kwh": "float64"})
        .sort_values(["trade_date", "time_code"], ignore_index=True)
    )


class TestLoadAreaDemand:
    def test_tokyo_history_without_the_null_hole(self, spark, curated_warehouse):
        demand = load_area_demand("tokyo", spark=spark)
        assert isinstance(demand, AreaDemand)
        pd.testing.assert_frame_equal(demand.df, expected_demand(curated_warehouse))
        assert len(demand) == len(DEMAND_DAYS) * 48 - len(DEMAND_HOLE_TIME_CODES)
        hole = demand.df[demand.df["trade_date"] == DEMAND_HOLE_DAY]
        assert hole["time_code"].tolist() == list(range(1, 11))

    def test_area_without_actuals_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No demand actuals found for area_code='kansai'"):
            load_area_demand("kansai", spark=spark)

    def test_defaults_to_tokyo_and_the_active_session(self, spark, curated_warehouse):
        assert len(load_area_demand()) == len(DEMAND_DAYS) * 48 - len(DEMAND_HOLE_TIME_CODES)


def expected_temperature(warehouse: CuratedWarehouse) -> pd.DataFrame:
    return (
        warehouse.weather.assign(
            obs_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]")
        )[["obs_date", "hour_ending", "temperature_c"]]
        .astype({"hour_ending": "int64", "temperature_c": "float64"})
        .sort_values(["obs_date", "hour_ending"], ignore_index=True)
    )


class TestLoadAreaTemperature:
    def test_tokyo_temperature_by_observation_day_and_hour_ending(self, spark, curated_warehouse):
        temperature = load_area_temperature("tokyo", spark=spark)
        assert isinstance(temperature, AreaTemperature)
        pd.testing.assert_frame_equal(temperature.df, expected_temperature(curated_warehouse))
        # The 24:00 reading is hour_ending 24 of the observation day, not hour 0 of the next.
        assert set(temperature.df["hour_ending"]) == set(range(1, 25))
        assert len(temperature) == len(DEMAND_DAYS) * 24
        # Missing hours are kept as NaN, not dropped.
        assert temperature.df["temperature_c"].isna().sum() == len(TEMPERATURE_MISSING_HOURS)

    def test_area_whose_station_has_no_observations_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No temperature observations found for area_code='kansai'"
        ):
            load_area_temperature("kansai", spark=spark)


def expected_temperature_forecast(warehouse: CuratedWarehouse) -> pd.DataFrame:
    station = warehouse.weather_forecast[
        warehouse.weather_forecast["station_id"] == TOKYO_STATION_ID
    ]
    return (
        station.assign(trade_date=lambda d: pd.to_datetime(d["date_key"]).astype("datetime64[ns]"))[
            ["trade_date", "hour_ending", "forecast_temperature_c"]
        ]
        .astype({"hour_ending": "int64", "forecast_temperature_c": "float64"})
        .sort_values(["trade_date", "hour_ending"], ignore_index=True)
    )


class TestLoadAreaTemperatureForecast:
    def test_tokyo_forecast_by_delivery_day_and_hour_ending(self, spark, curated_warehouse):
        forecast = load_area_temperature_forecast("tokyo", spark=spark)
        assert isinstance(forecast, AreaTemperatureForecast)
        pd.testing.assert_frame_equal(forecast.df, expected_temperature_forecast(curated_warehouse))
        # Hour 24 (valid at next-day 00:00) stays on its delivery day as hour_ending 24.
        assert set(forecast.df["hour_ending"]) == set(range(1, 25))
        assert len(forecast) == (len(DEMAND_DAYS) - 1) * 24
        # A day without forecast rows is simply absent, not filled.
        assert FORECAST_MISSING_DAY not in set(forecast.df["trade_date"])

    def test_area_whose_station_has_no_forecasts_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No temperature forecasts found for area_code='kansai'"
        ):
            load_area_temperature_forecast("kansai", spark=spark)

    def test_defaults_to_tokyo_and_the_active_session(self, spark, curated_warehouse):
        assert len(load_area_temperature_forecast()) == (len(DEMAND_DAYS) - 1) * 24


def expected_population_weighted_forecast(
    warehouse: CuratedWarehouse, census_year: int
) -> pd.DataFrame:
    """Hand-derived weighted mean: w1 * T1 + w2 * (T1 + offset), renormalised where
    the second station's row is missing."""
    weights = STATION_POPULATION_WEIGHTS[census_year]
    first = expected_temperature_forecast(warehouse).rename(
        columns={"forecast_temperature_c": "t1"}
    )
    w1, w2 = weights[TOKYO_STATION_ID], weights[TOKYO_SECOND_STATION_ID]
    missing_day, missing_hour = SECOND_STATION_MISSING_HOUR
    second_present = ~(
        (first["trade_date"] == missing_day) & (first["hour_ending"] == missing_hour)
    )
    value = first["t1"] + second_present * (w2 * SECOND_STATION_FORECAST_OFFSET_C) / (w1 + w2)
    return first.assign(forecast_temperature_c=value.astype("float64")).drop(columns="t1")


class TestLoadAreaTemperatureForecastPopulationWeighted:
    def test_latest_vintage_weights_by_default(self, spark, curated_warehouse):
        loaded = load_area_temperature_forecast_population_weighted("tokyo", spark=spark)
        assert isinstance(loaded, PopulationWeightedTemperatureForecast)
        assert loaded.census_year == 2020
        assert loaded.n_stations == 2
        forecast = loaded.forecast
        assert isinstance(forecast, AreaTemperatureForecast)
        pd.testing.assert_frame_equal(
            forecast.df, expected_population_weighted_forecast(curated_warehouse, 2020)
        )
        assert len(forecast) == (len(DEMAND_DAYS) - 1) * 24
        assert FORECAST_MISSING_DAY not in set(forecast.df["trade_date"])

    def test_explicit_census_year(self, spark, curated_warehouse):
        loaded = load_area_temperature_forecast_population_weighted(
            "tokyo", census_year=2015, spark=spark
        )
        assert loaded.census_year == 2015
        pd.testing.assert_frame_equal(
            loaded.forecast.df, expected_population_weighted_forecast(curated_warehouse, 2015)
        )

    def test_missing_station_hour_is_renormalised_over_the_present_stations(
        self, spark, curated_warehouse
    ):
        forecast = load_area_temperature_forecast_population_weighted("tokyo", spark=spark).forecast
        day, hour = SECOND_STATION_MISSING_HOUR
        row = forecast.df[(forecast.df["trade_date"] == day) & (forecast.df["hour_ending"] == hour)]
        only_first = expected_temperature_forecast(curated_warehouse)
        t1 = only_first[(only_first["trade_date"] == day) & (only_first["hour_ending"] == hour)]
        assert row["forecast_temperature_c"].iloc[0] == pytest.approx(
            t1["forecast_temperature_c"].iloc[0]
        )

    def test_area_without_weights_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No station population weights found for area_code='hokkaido'$"
        ):
            load_area_temperature_forecast_population_weighted("hokkaido", spark=spark)

    def test_weighted_stations_without_forecast_rows_raise(self, spark, curated_warehouse):
        # kansai has a weighted station (s47772) but no MSM rows for it.
        with pytest.raises(
            ValueError,
            match="No temperature forecasts found for the weighted stations of "
            r"area_code='kansai' \(census_year=2020\)",
        ):
            load_area_temperature_forecast_population_weighted("kansai", spark=spark)

    def test_unknown_census_year_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError,
            match="No station population weights found for area_code='tokyo', census_year=1999",
        ):
            load_area_temperature_forecast_population_weighted(
                "tokyo", census_year=1999, spark=spark
            )
