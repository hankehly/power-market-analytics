# tests/test_demand_datasets.py
"""Tests for the warehouse readers feeding the demand task.

Read against the synthetic ``pma_curated`` star from ``curated_warehouse``:
tokyo has demand actuals for ``DEMAND_DAYS`` (with a partial-day hole), an
hourly load history, observed weather at its stations and an MSM forecast for
every day but ``FORECAST_MISSING_DAY``; kansai has an area row and a station id
but no facts. ``dim_date`` covers ``CALENDAR_DAYS`` with its weekend / holiday
flags and calendar attributes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    AREA_CODES,
    PopulationWeightedObservedWeather,
    PopulationWeightedWeatherForecast,
    load_area_demand,
    load_area_hourly_load,
    load_area_observed_weather_population_weighted,
    load_area_weather_forecast_population_weighted,
    load_day_calendar,
)
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    AreaHourlyLoad,
    AreaObservedWeather,
    AreaWeatherForecast,
    DayCalendar,
)
from tests.conftest import (
    DEMAND_DAYS,
    DEMAND_HOLE_DAY,
    DEMAND_HOLE_TIME_CODES,
    HOLIDAYS_2024_SPRING,
    HOURLY_LOAD_DAYS,
    SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT,
    SECOND_STATION_FORECAST_OFFSET_C,
    SECOND_STATION_FORECAST_RAIN_OFFSET_MM,
    SECOND_STATION_MISSING_HOUR,
    STATION_POPULATION_WEIGHTS,
    TEMPERATURE_MISSING_HOURS,
    TOKYO_SECOND_STATION_ID,
    TOKYO_STATION_ID,
    CuratedWarehouse,
    synthetic_forecast_humidity,
    synthetic_forecast_precipitation,
    synthetic_forecast_temperature,
    synthetic_holiday_degree,
    synthetic_hourly_load,
    synthetic_humidity,
    synthetic_precipitation,
    synthetic_temperature,
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


class TestLoadAreaHourlyLoad:
    def test_loads_every_hour_as_hour_ending(self, spark, curated_warehouse: CuratedWarehouse):
        frame = load_area_hourly_load("tokyo", spark=spark)
        assert type(frame) is AreaHourlyLoad
        assert len(frame) == len(HOURLY_LOAD_DAYS) * 24
        first = frame.df.iloc[0]
        assert first["load_date"] == HOURLY_LOAD_DAYS[0]
        assert first["hour_ending"] == 1
        assert first["demand_kwh"] == float(synthetic_hourly_load(HOURLY_LOAD_DAYS[0], 0))
        assert frame.df["hour_ending"].max() == 24

    def test_unknown_area_raises(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No hourly load history found for area_code='kansai'"):
            load_area_hourly_load("kansai", spark=spark)


def expected_holiday_distances(day: pd.Timestamp) -> tuple[int, int]:
    holidays = sorted(HOLIDAYS_2024_SPRING)
    if day in holidays:
        return 0, 0
    before = [h for h in holidays if h < day]
    after = [h for h in holidays if h > day]
    return (day - before[-1]).days, (after[0] - day).days


class TestLoadDayCalendar:
    def test_attributes_per_day(self, spark, curated_warehouse: CuratedWarehouse):
        frame = load_day_calendar(spark=spark)
        assert type(frame) is DayCalendar
        # Days before the first holiday (03-20) and after the last (05-06) have no distance.
        first, last = min(HOLIDAYS_2024_SPRING), max(HOLIDAYS_2024_SPRING)
        assert frame.df["trade_date"].min() == first
        assert frame.df["trade_date"].max() == last
        assert len(frame) == (last - first).days + 1
        by_day = frame.df.set_index("trade_date")
        for day in (pd.Timestamp("2024-04-10"), pd.Timestamp("2024-04-30"), first, last):
            since, until = expected_holiday_distances(day)
            assert by_day.loc[day, "days_since_holiday"] == since
            assert by_day.loc[day, "days_until_holiday"] == until
            assert by_day.loc[day, "holiday_degree"] == synthetic_holiday_degree(day)
        assert list(by_day.columns) == [
            "days_since_holiday",
            "days_until_holiday",
            "holiday_degree",
        ]
        assert by_day["days_since_holiday"].dtype == "int64"

    def test_empty_dim_date_raises(self, spark, monkeypatch):
        monkeypatch.setattr(
            "power_market_analytics.tasks.demand.datasets.query_pandas",
            lambda *a, **k: pd.DataFrame(),
        )
        with pytest.raises(ValueError, match="No calendar days found in dim_date"):
            load_day_calendar(spark=spark)

    def test_dim_date_without_a_holiday_raises(self, spark, monkeypatch):
        days = pd.date_range("2024-04-01", "2024-04-05")
        monkeypatch.setattr(
            "power_market_analytics.tasks.demand.datasets.query_pandas",
            lambda *a, **k: pd.DataFrame(
                {
                    "trade_date": [d.date() for d in days],
                    "is_holiday": [False] * 5,
                    "holiday_degree": [0.0] * 5,
                }
            ),
        )
        with pytest.raises(ValueError, match="dim_date has no named holiday"):
            load_day_calendar(spark=spark)


def weighted(first: float, second: float | None, w1: float, w2: float) -> float:
    if second is None:
        return first
    return (w1 * first + w2 * second) / (w1 + w2)


class TestLoadAreaWeatherForecastPopulationWeighted:
    def test_three_measures_weighted_and_renormalised(
        self, spark, curated_warehouse: CuratedWarehouse
    ):
        loaded = load_area_weather_forecast_population_weighted("tokyo", spark=spark)
        assert type(loaded) is PopulationWeightedWeatherForecast
        assert type(loaded.forecast) is AreaWeatherForecast
        assert loaded.census_year == 2020
        assert loaded.n_stations == 2
        w1, w2 = (
            STATION_POPULATION_WEIGHTS[2020][s] for s in (TOKYO_STATION_ID, TOKYO_SECOND_STATION_ID)
        )
        by_hour = loaded.forecast.df.set_index(["trade_date", "hour_ending"])
        day, hour = pd.Timestamp("2024-04-10"), 7
        t = synthetic_forecast_temperature(day, hour)
        h = synthetic_forecast_humidity(day, hour)
        r = synthetic_forecast_precipitation(day, hour)
        row = by_hour.loc[(day, hour)]
        assert isinstance(row, pd.Series)
        assert row["forecast_temperature_c"] == pytest.approx(
            weighted(t, t + SECOND_STATION_FORECAST_OFFSET_C, w1, w2)
        )
        assert row["forecast_relative_humidity_pct"] == pytest.approx(
            weighted(h, h + SECOND_STATION_FORECAST_HUMIDITY_OFFSET_PCT, w1, w2)
        )
        assert row["forecast_precipitation_mm"] == pytest.approx(
            weighted(r, r + SECOND_STATION_FORECAST_RAIN_OFFSET_MM, w1, w2)
        )
        # The hour the second station lacks falls back to the first station alone.
        day, hour = SECOND_STATION_MISSING_HOUR
        assert by_hour.loc[(day, hour), "forecast_temperature_c"] == pytest.approx(
            synthetic_forecast_temperature(day, hour)
        )

    def test_explicit_census_year(self, spark, curated_warehouse):
        assert (
            load_area_weather_forecast_population_weighted(
                "tokyo", census_year=2015, spark=spark
            ).census_year
            == 2015
        )

    def test_two_vintages_for_one_hour_raise(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="forecast vintages"):
            load_area_weather_forecast_population_weighted("chubu", spark=spark)

    def test_no_weights_raise(self, spark, curated_warehouse):
        with pytest.raises(ValueError, match="No station population weights found"):
            load_area_weather_forecast_population_weighted("tokyo", census_year=1999, spark=spark)

    def test_no_forecast_rows_raise(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No weather forecasts found for the weighted stations"
        ):
            load_area_weather_forecast_population_weighted("kansai", spark=spark)


class TestLoadAreaObservedWeatherPopulationWeighted:
    def test_only_the_observing_station_counts(self, spark, curated_warehouse: CuratedWarehouse):
        loaded = load_area_observed_weather_population_weighted("tokyo", spark=spark)
        assert type(loaded) is PopulationWeightedObservedWeather
        assert type(loaded.weather) is AreaObservedWeather
        assert loaded.census_year == 2020
        assert loaded.n_stations == 2
        # The second station has no observations, so the weighted value is s47662's.
        by_hour = loaded.weather.df.set_index(["obs_date", "hour_ending"])
        day, hour = pd.Timestamp("2024-04-10"), 7
        row = by_hour.loc[(day, hour)]
        assert isinstance(row, pd.Series)
        assert row["temperature_c"] == pytest.approx(synthetic_temperature(day, hour))
        assert row["humidity_pct"] == pytest.approx(synthetic_humidity(day, hour))
        assert row["precipitation_mm"] == pytest.approx(synthetic_precipitation(day, hour))
        # A missing temperature hour is null for temperature and present for the others.
        day, hour = next(iter(sorted(TEMPERATURE_MISSING_HOURS)))
        assert pd.isna(by_hour.loc[(day, hour), "temperature_c"])
        assert by_hour.loc[(day, hour), "humidity_pct"] == pytest.approx(
            synthetic_humidity(day, hour)
        )
        assert len(loaded.weather) == len(curated_warehouse.weather)

    def test_explicit_census_year(self, spark, curated_warehouse):
        assert (
            load_area_observed_weather_population_weighted(
                "tokyo", census_year=2015, spark=spark
            ).census_year
            == 2015
        )

    def test_no_observation_rows_raise(self, spark, curated_warehouse):
        with pytest.raises(
            ValueError, match="No weather observations found for the weighted stations"
        ):
            load_area_observed_weather_population_weighted("kansai", spark=spark)
