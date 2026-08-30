# tests/test_demand_datasets.py
"""Tests for the warehouse readers feeding the demand task.

Read against the synthetic ``pma_curated`` star from ``curated_warehouse``:
tokyo has demand actuals for ``DEMAND_DAYS`` (with a partial-day hole),
hourly temperature at its representative station and an MSM forecast
temperature for every day but ``FORECAST_MISSING_DAY``, and an hourly load
history over ``HOURLY_LOAD_DAYS``; kansai has an area row and a station id but
no facts. ``dim_date`` covers ``DEMAND_DAYS`` with its weekend / holiday flags
and prior-year reference.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    AREA_CODES,
    PopulationWeightedTemperatureForecast,
    load_area_demand,
    load_area_hourly_load,
    load_area_temperature,
    load_area_temperature_forecast,
    load_area_temperature_forecast_population_weighted,
    load_day_types,
    load_prior_year_calendar,
)
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaHourlyLoad,
    AreaTemperature,
    AreaTemperatureForecast,
    DayTypeCalendar,
    PriorYearCalendar,
)
from tests.conftest import (
    DEMAND_DAYS,
    DEMAND_HOLE_DAY,
    DEMAND_HOLE_TIME_CODES,
    FORECAST_MISSING_DAY,
    HOLIDAYS_2024_SPRING,
    HOURLY_LOAD_DAYS,
    SECOND_STATION_FORECAST_OFFSET_C,
    SECOND_STATION_MISSING_HOUR,
    STATION_POPULATION_WEIGHTS,
    TEMPERATURE_MISSING_HOURS,
    TOKYO_SECOND_STATION_ID,
    TOKYO_STATION_ID,
    CuratedWarehouse,
    synthetic_hourly_load,
    synthetic_prior_year_reference,
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

    def test_two_vintages_for_one_hour_fail_fast(self, spark, curated_warehouse):
        # chubu's station has TWO_VINTAGE_HOUR forecast by two MSM runs: the frame's
        # unique grain refuses to pick one silently.
        with pytest.raises(ValueError, match=r"grain .* not unique \(1 duplicate rows\)"):
            load_area_temperature_forecast("chubu", spark=spark)

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

    def test_two_vintages_for_one_hour_fail_fast_instead_of_blending(
        self, spark, curated_warehouse
    ):
        with pytest.raises(
            ValueError,
            match=r"2 forecast vintages for 1 delivery-day hour\(s\) of area_code='chubu' "
            r"\(e\.g\. 2024-04-01 hour 9: 2024-03-30 21:00, 2024-03-31 09:00\)",
        ):
            load_area_temperature_forecast_population_weighted("chubu", spark=spark)

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


def expected_day_type(day: pd.Timestamp) -> int:
    if day in HOLIDAYS_2024_SPRING:
        return 2
    return 1 if day.dayofweek >= 5 else 0


class TestLoadDayTypes:
    def test_codes_every_day_of_dim_date(self, spark, curated_warehouse):
        calendar = load_day_types(spark=spark)
        assert isinstance(calendar, DayTypeCalendar)
        assert calendar.df["trade_date"].tolist() == list(DEMAND_DAYS)
        assert calendar.df["day_type"].tolist() == [expected_day_type(day) for day in DEMAND_DAYS]
        assert set(calendar.df["day_type"]) == {0, 1, 2}

    def test_a_holiday_on_a_weekend_is_a_holiday(self, spark, curated_warehouse):
        # 2024-05-04 みどりの日 (Saturday) and 2024-05-05 こどもの日 (Sunday) are holidays,
        # 2024-05-11 is a plain Saturday.
        by_day = load_day_types(spark=spark).df.set_index("trade_date")["day_type"]
        assert by_day[pd.Timestamp("2024-05-04")] == 2
        assert by_day[pd.Timestamp("2024-05-05")] == 2
        assert by_day[pd.Timestamp("2024-05-11")] == 1
        assert by_day[pd.Timestamp("2024-05-10")] == 0

    def test_empty_calendar_raises(self, monkeypatch):
        monkeypatch.setattr(
            "power_market_analytics.tasks.demand.datasets.query_pandas",
            lambda sql, spark=None: pd.DataFrame(
                columns=["trade_date", "is_weekend", "is_holiday"]
            ),
        )
        with pytest.raises(ValueError, match="No calendar days found in dim_date"):
            load_day_types()


class TestLoadAreaHourlyLoad:
    def test_reads_every_hour_of_the_areas_hourly_fact(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        load = load_area_hourly_load("tokyo", spark=spark)
        assert isinstance(load, AreaHourlyLoad)
        assert len(load) == len(curated_warehouse.hourly_load) == len(HOURLY_LOAD_DAYS) * 24
        assert load.df["load_date"].drop_duplicates().tolist() == list(HOURLY_LOAD_DAYS)
        assert load.df["demand_kwh"].dtype == "float64"
        first_day = load.df[load.df["load_date"] == HOURLY_LOAD_DAYS[0]]
        assert first_day["demand_kwh"].tolist() == [
            float(synthetic_hourly_load(HOURLY_LOAD_DAYS[0], hour)) for hour in range(24)
        ]

    def test_hour_ending_is_the_facts_hour_of_day_plus_one(self, spark, curated_warehouse):
        load = load_area_hourly_load("tokyo", spark=spark)
        by_hour = load.df[load.df["load_date"] == HOURLY_LOAD_DAYS[-1]]
        assert by_hour["hour_ending"].tolist() == list(range(1, 25))
        # hour_of_day 0 (00:00-01:00) is hour_ending 1.
        assert by_hour["demand_kwh"].iloc[0] == synthetic_hourly_load(HOURLY_LOAD_DAYS[-1], 0)

    def test_area_without_an_hourly_fact_raises(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No hourly load history found for area_code='kansai'"
        ):
            load_area_hourly_load("kansai", spark=spark)


class TestLoadPriorYearCalendar:
    def test_reads_every_day_of_dim_date(self, spark, curated_warehouse: CuratedWarehouse):
        calendar = load_prior_year_calendar(spark=spark)
        assert isinstance(calendar, PriorYearCalendar)
        assert calendar.df["trade_date"].tolist() == list(DEMAND_DAYS)
        expected = [synthetic_prior_year_reference(day) for day in DEMAND_DAYS]
        assert calendar.df["prior_year_reference_date"].tolist() == [
            reference for reference, _ in expected
        ]
        assert calendar.df["prior_year_reference_rule"].tolist() == [rule for _, rule in expected]
        assert set(calendar.df["prior_year_reference_rule"]) == {"same_weekday", "same_holiday"}

    def test_empty_calendar_raises(self, monkeypatch):
        monkeypatch.setattr(
            "power_market_analytics.tasks.demand.datasets.query_pandas",
            lambda sql, spark=None: pd.DataFrame(
                columns=["trade_date", "prior_year_reference_date", "prior_year_reference_rule"]
            ),
        )
        with pytest.raises(ValueError, match="No calendar days found in dim_date"):
            load_prior_year_calendar()
