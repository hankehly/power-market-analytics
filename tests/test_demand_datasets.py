# tests/test_demand_datasets.py
"""Tests for the warehouse readers feeding the demand task.

Read against the synthetic ``pma_curated`` star from ``curated_warehouse``:
tokyo has demand actuals for ``DEMAND_DAYS`` (with a partial-day hole) and
hourly temperature at its representative station; kansai has an area row
and a station id but no facts.
"""

from __future__ import annotations

import pandas as pd
import pytest

from power_market_analytics.tasks.demand.datasets import (
    AREA_CODES,
    load_area_demand,
    load_area_temperature,
)
from power_market_analytics.tasks.demand.frames import AreaDemand, AreaTemperature
from tests.conftest import (
    DEMAND_DAYS,
    DEMAND_HOLE_DAY,
    DEMAND_HOLE_TIME_CODES,
    TEMPERATURE_MISSING_HOURS,
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
