"""Shared pytest fixtures.

The Spark fixture is a plain local session (no Hive metastore) with its
warehouse and catalog rooted in a temporary directory, so loader tests can
``saveAsTable`` without touching the devcontainer warehouse.

``curated_warehouse`` populates a small synthetic ``pma_curated`` star (the
tables the spot-price and demand tasks read) in that same temp warehouse,
and ``mlflow_store`` points MLflow at a temp file store so nothing lands in
``./mlruns``.
"""

from __future__ import annotations

import dataclasses
import math
import os
import time
from collections.abc import Iterator

# The Spark fixture pins spark.sql.session.timeZone to Asia/Tokyo, but PySpark's
# collect() renders TimestampType as a naive datetime in the *process's* local
# time zone, so the tests' JST wall-clock literals only match when that is JST
# too. Pin it here, before the JVM starts (it inherits TZ), so the suite is
# host-independent (CI runners are UTC).
os.environ["TZ"] = "Asia/Tokyo"
time.tzset()

# Headless matplotlib for the SHAP plots MLflow evaluation renders.
os.environ.setdefault("MPLBACKEND", "Agg")
# MLflow 3.x refuses the local filesystem tracking backend ("maintenance
# mode") unless explicitly allowed; it is exactly what an isolated test store
# wants (artifacts stay under the temp root, no sqlite/mlruns in the repo).
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402


@pytest.fixture(scope="session")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SparkSession]:
    """Session-scoped local SparkSession writing to a temp warehouse."""
    warehouse = tmp_path_factory.mktemp("warehouse")
    session = (
        SparkSession.builder.master("local[1]")
        .appName("pma-tests")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.sql.session.timeZone", "Asia/Tokyo")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.fixture(scope="session", autouse=True)
def single_threaded_lightgbm() -> None:
    """Cap LightGBM at one thread for the whole session.

    ``LGBM_PARAMS`` leaves ``num_threads`` at LightGBM's default (every core)
    and the wheel ignores ``OMP_NUM_THREADS``; the OpenMP fan-out makes a
    500-tree fit on ~1,500 rows take ~2.3 s instead of ~0.15 s, and the
    strategy tests fit dozens of times. ``LGBM_SetMaxThreads`` is LightGBM's
    public C API, reached through the Python package's library handle.
    """
    import ctypes

    from lightgbm.basic import _LIB

    _LIB.LGBM_SetMaxThreads(ctypes.c_int(1))


@pytest.fixture(scope="session", autouse=True)
def mlflow_store(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Point MLflow at a session-wide temp file store.

    Autouse so no test can write to ``./mlruns`` by accident; request it
    explicitly for the URI.
    """
    # A not-yet-existing root: FileStore creates it together with the default
    # experiment "0", so a bare ``mlflow.start_run()`` works too.
    uri = (tmp_path_factory.mktemp("mlflow") / "mlruns").as_uri()
    os.environ["MLFLOW_TRACKING_URI"] = uri
    mlflow.set_tracking_uri(uri)
    return uri


# --------------------------------------------------------------------------- curated star
# Synthetic stand-in for the dbt curated layer, shaped like the real models
# (same column names and Spark types) but tiny and deterministic.

#: Representative JMA stations written to dim_area; only tokyo's has weather rows.
TOKYO_STATION_ID = "s47662"
KANSAI_STATION_ID = "s47772"
#: A second tokyo-area station: MSM forecast rows only (no observations), weighted
#: together with TOKYO_STATION_ID in fct_census_population_jma_station.
TOKYO_SECOND_STATION_ID = "s47670"
#: Forecast offset of the second station over the first (a constant, so the
#: population-weighted value is hand-derivable).
SECOND_STATION_FORECAST_OFFSET_C = 2.0
#: (delivery day, hour_ending) whose forecast row is missing for the second station only.
SECOND_STATION_MISSING_HOUR = (pd.Timestamp("2024-05-20"), 12)
#: Station population weights per census vintage (tokyo area): the latest vintage
#: is the default; 2015 differs so an explicit census_year is observable. Kansai
#: has a weight row too (KANSAI_STATION_ID, weight 1) but no forecast rows.
STATION_POPULATION_WEIGHTS = {
    2020: {TOKYO_STATION_ID: 0.6, TOKYO_SECOND_STATION_ID: 0.4},
    2015: {TOKYO_STATION_ID: 0.5, TOKYO_SECOND_STATION_ID: 0.5},
}
KANSAI_AREA_KEY = 2
CHUBU_AREA_KEY = 3

#: A third area whose station carries TWO forecast vintages for one hour, so the
#: loaders' fail-fast on blended vintages can be exercised.
CHUBU_STATION_ID = "s47636"
TWO_VINTAGE_HOUR = (pd.Timestamp("2024-04-01"), 9)

AREAS = pd.DataFrame(
    {
        "area_key": [1, 2, 3],
        "area_code": ["tokyo", "kansai", "chubu"],
        "representative_jma_station_id": [TOKYO_STATION_ID, KANSAI_STATION_ID, CHUBU_STATION_ID],
    }
)
TOKYO_AREA_KEY = 1

#: Delivery days with spot prices (tokyo only).
PRICE_DAYS = pd.date_range("2024-03-01", "2024-05-31", freq="D")
#: Delivery days with an OCCTO 翌々日 forecast (tokyo only) — starts a month
#: into the price history, like the real fact (2024-04-01).
OCCTO_DAYS = pd.date_range("2024-04-01", "2024-05-31", freq="D")
#: Window scored by the two matched accuracy runs (tokyo).
ACCURACY_DAYS = pd.date_range("2024-04-10", "2024-04-30", freq="D")
BASELINE_RUN_ID = "run-baseline"
CANDIDATE_RUN_ID = "run-candidate"
#: A third run scored on a different window (for "not matched" errors).
UNMATCHED_RUN_ID = "run-unmatched"
UNMATCHED_DAYS = pd.date_range("2024-04-15", "2024-04-20", freq="D")
#: Delivery days with demand actuals (tokyo only) — the same span as the prices.
DEMAND_DAYS = PRICE_DAYS
#: Two matched demand accuracy runs over ACCURACY_DAYS and one over UNMATCHED_DAYS.
DEMAND_BASELINE_RUN_ID = "demand-run-baseline"
DEMAND_CANDIDATE_RUN_ID = "demand-run-candidate"
DEMAND_UNMATCHED_RUN_ID = "demand-run-unmatched"
#: Signed error of the demand baseline on odd time codes (kWh); even codes carry
#: minus half of it, so MAE and bias differ. The candidate halves it, the
#: unmatched run scales it by 0.8.
DEMAND_BASELINE_ERROR_KWH = 100_000
#: National holidays inside DEMAND_DAYS (the jpn_national_holidays seed, 2024).
HOLIDAYS_2024_SPRING = (
    pd.Timestamp("2024-03-20"),
    pd.Timestamp("2024-04-29"),
    pd.Timestamp("2024-05-03"),
    pd.Timestamp("2024-05-04"),
    pd.Timestamp("2024-05-05"),
    pd.Timestamp("2024-05-06"),
)
#: One partial-day hole like Tokyo 2025-06-14: time codes 11..48 have null demand.
DEMAND_HOLE_DAY = pd.Timestamp("2024-04-20")
DEMAND_HOLE_TIME_CODES = range(11, 49)
#: (observation day, hour_ending) pairs whose temperature is null.
TEMPERATURE_MISSING_HOURS = {(pd.Timestamp("2024-04-25"), 13), (pd.Timestamp("2024-04-25"), 14)}
#: Delivery day with no MSM forecast rows at all (a day whose GRIB files were never fetched).
FORECAST_MISSING_DAY = pd.Timestamp("2024-05-15")
#: Days of hourly load history (fct_area_power_usage_hourly, tokyo): from the
#: earliest prior-year reference a DEMAND_DAYS day can have (371 days before the
#: first) through the last demand day, so every reference day has a load.
HOURLY_LOAD_DAYS = pd.date_range(
    DEMAND_DAYS[0] - pd.Timedelta(days=371), DEMAND_DAYS[-1], freq="D"
)


def synthetic_price(day: pd.Timestamp, time_code: int) -> float:
    """Deterministic, strictly positive JPY/kWh price with a daily shape."""
    day_index = (day - PRICE_DAYS[0]).days
    shape = 12.0 + 6.0 * math.sin(2 * math.pi * (time_code - 1) / 48)
    weekend = -1.5 if day.dayofweek >= 5 else 0.0
    wobble = ((day_index * 7 + time_code * 13) % 11) / 10.0
    return round(shape + weekend + wobble, 2)


def day_part(time_code: int) -> str:
    """dim_delivery_period.day_part for a time code."""
    if time_code <= 12:
        return "Overnight"
    if time_code <= 16:
        return "Morning"
    if time_code <= 36:
        return "Daytime"
    return "Evening"


def synthetic_demand(day: pd.Timestamp, time_code: int) -> int:
    """Deterministic 30-minute demand in kWh: daily shape, weekend dip, slow drift.

    Multiples of 1,000 like TEPCO's published values.
    """
    day_index = (day - PRICE_DAYS[0]).days
    shape = 15_000_000 - 4_000_000 * math.cos(2 * math.pi * (time_code - 1) / 48)
    weekend = -1_000_000 if day.dayofweek >= 5 else 0
    return int(round((shape + weekend + 5_000 * day_index) / 1000) * 1000)


def synthetic_temperature(day: pd.Timestamp, hour_ending: int) -> float:
    """Deterministic hourly temperature in °C: diurnal cycle plus slow warming."""
    day_index = (day - PRICE_DAYS[0]).days
    return round(8.0 + 0.15 * day_index + 5.0 * math.sin(2 * math.pi * (hour_ending - 9) / 24), 1)


def synthetic_forecast_temperature(day: pd.Timestamp, hour_ending: int) -> float:
    """Deterministic MSM point-forecast temperature in °C for a delivery-day hour.

    The observed temperature plus a small hour-dependent forecast error, so the
    two series are close but never identical.
    """
    return round(synthetic_temperature(day, hour_ending) + 0.3 * math.cos(hour_ending / 3.0), 2)


def synthetic_hourly_load(day: pd.Timestamp, hour_of_day: int) -> int:
    """Deterministic hourly energy in kWh: daily shape, weekend dip, slow drift.

    Multiples of 10,000 like the でんき予報 fact (integer 万kW × 10,000).
    """
    day_index = (day - HOURLY_LOAD_DAYS[0]).days
    shape = 30_000_000 - 8_000_000 * math.cos(2 * math.pi * hour_of_day / 24)
    weekend = -2_000_000 if day.dayofweek >= 5 else 0
    return int(round((shape + weekend + 10_000 * day_index) / 10_000) * 10_000)


def synthetic_prior_year_reference(day: pd.Timestamp) -> tuple[pd.Timestamp, str]:
    """dim_date's prior-year reference in the fixture: a holiday takes the same
    calendar date a year earlier (``same_holiday``), every other day the same
    weekday 52 weeks back (``same_weekday``)."""
    if day in HOLIDAYS_2024_SPRING:
        return day - pd.DateOffset(years=1), "same_holiday"
    return day - pd.Timedelta(days=364), "same_weekday"


@dataclasses.dataclass(frozen=True)
class CuratedWarehouse:
    """What ``curated_warehouse`` created, as the pandas frames it wrote.

    Attributes
    ----------
    areas, prices, occto, delivery_periods, accuracy : pandas.DataFrame
        Contents of ``dim_area``, ``fct_jepx_spot_area_price``,
        ``fct_occto_demand_supply_forecast_daily``, ``dim_delivery_period``
        and ``fct_spot_price_forecast_accuracy`` (tokyo rows only, except the
        area dimension).
    demand : pandas.DataFrame
        Contents of ``fct_area_demand_generation_actual`` (tokyo,
        ``demand_kwh`` NaN in the hole).
    weather : pandas.DataFrame
        Contents of ``fct_jma_weather_hourly`` (s47662, hourly,
        ``temperature_c`` NaN for the missing hours).
    weather_forecast : pandas.DataFrame
        The tokyo rows of ``fct_jma_msm_weather_forecast_hourly`` as
        (station_id, date_key, hour_ending, forecast_temperature_c) — s47662 and
        ``TOKYO_SECOND_STATION_ID`` (offset by ``SECOND_STATION_FORECAST_OFFSET_C``,
        minus ``SECOND_STATION_MISSING_HOUR``), every ``DEMAND_DAYS`` day except
        ``FORECAST_MISSING_DAY``. The table also holds chubu's ``CHUBU_STATION_ID``
        at ``TWO_VINTAGE_HOUR`` under two ``forecast_reference_at`` values.
    demand_accuracy : pandas.DataFrame
        Contents of ``fct_demand_forecast_accuracy`` (tokyo; the two matched
        demand runs over ``ACCURACY_DAYS`` and the unmatched one).
    dates : pandas.DataFrame
        Contents of ``dim_date`` over ``DEMAND_DAYS`` (weekend / holiday flags,
        prior-year reference date and rule per ``synthetic_prior_year_reference``).
    hourly_load : pandas.DataFrame
        Contents of ``fct_area_power_usage_hourly`` (tokyo, ``HOURLY_LOAD_DAYS``
        × hours 0-23, ``synthetic_hourly_load``).
    station_weights : pandas.DataFrame
        Contents of ``fct_census_population_jma_station`` (tokyo area, the
        ``STATION_POPULATION_WEIGHTS`` vintages; plus kansai's and chubu's single
        stations at weight 1 for 2020 — kansai's has no forecast rows).
    """

    areas: pd.DataFrame
    prices: pd.DataFrame
    occto: pd.DataFrame
    delivery_periods: pd.DataFrame
    accuracy: pd.DataFrame
    demand: pd.DataFrame
    weather: pd.DataFrame
    weather_forecast: pd.DataFrame
    station_weights: pd.DataFrame
    demand_accuracy: pd.DataFrame
    dates: pd.DataFrame
    hourly_load: pd.DataFrame


@pytest.fixture(scope="session")
def curated_warehouse(spark: SparkSession) -> CuratedWarehouse:
    """Create the ``pma_curated`` tables the spot-price task reads.

    Tokyo has prices for every day of ``PRICE_DAYS`` × 48 time codes, OCCTO
    forecasts for ``OCCTO_DAYS``, demand actuals and hourly observed
    temperature for ``DEMAND_DAYS`` and an MSM forecast temperature (at two
    stations, with census population weights) for every one of those days but
    ``FORECAST_MISSING_DAY``; Kansai has an area row but no facts.
    Two matched accuracy runs (``BASELINE_RUN_ID``, ``CANDIDATE_RUN_ID``)
    score ``ACCURACY_DAYS``; ``UNMATCHED_RUN_ID`` scores ``UNMATCHED_DAYS``.
    """
    prices = pd.DataFrame(
        [
            {
                "date_key": day.date(),
                "time_code": tc,
                "area_key": TOKYO_AREA_KEY,
                "trade_datetime": day + pd.Timedelta(minutes=30 * (tc - 1)),
                "area_price_jpy_kwh": synthetic_price(day, tc),
            }
            for day in PRICE_DAYS
            for tc in range(1, 49)
        ]
    )
    occto = pd.DataFrame(
        [
            {
                "date_key": day.date(),
                "area_key": TOKYO_AREA_KEY,
                "max_demand_hour_ending": 17 + i % 3,
                "max_demand_mw": 40_000 + 10 * i,
                "max_supply_capacity_mw": 46_000 + 10 * i,
            }
            for i, day in enumerate(OCCTO_DAYS)
        ]
    )
    delivery_periods = pd.DataFrame(
        {"time_code": range(1, 49), "day_part": [day_part(tc) for tc in range(1, 49)]}
    )
    accuracy_rows = []
    for run_id, days, bias in (
        (BASELINE_RUN_ID, ACCURACY_DAYS, 1.0),
        (CANDIDATE_RUN_ID, ACCURACY_DAYS, 0.5),
        (UNMATCHED_RUN_ID, UNMATCHED_DAYS, 0.8),
    ):
        for day in days:
            for tc in range(1, 49):
                actual = synthetic_price(day, tc)
                # Signed error alternates by time code so bias and MAE differ.
                error = bias * (1 if tc % 2 else -0.5)
                accuracy_rows.append(
                    {
                        "date_key": day.date(),
                        "time_code": tc,
                        "area_key": TOKYO_AREA_KEY,
                        "run_id": run_id,
                        "actual_price_jpy_kwh": actual,
                        "forecast_price_jpy_kwh": round(actual + error, 2),
                    }
                )
    accuracy = pd.DataFrame(accuracy_rows)

    demand_accuracy_rows = []
    for run_id, days, scale in (
        (DEMAND_BASELINE_RUN_ID, ACCURACY_DAYS, 1.0),
        (DEMAND_CANDIDATE_RUN_ID, ACCURACY_DAYS, 0.5),
        (DEMAND_UNMATCHED_RUN_ID, UNMATCHED_DAYS, 0.8),
    ):
        for day in days:
            for tc in range(1, 49):
                actual = float(synthetic_demand(day, tc))
                error = scale * DEMAND_BASELINE_ERROR_KWH * (1 if tc % 2 else -0.5)
                demand_accuracy_rows.append(
                    {
                        "date_key": day.date(),
                        "time_code": tc,
                        "area_key": TOKYO_AREA_KEY,
                        "run_id": run_id,
                        "actual_demand_kwh": actual,
                        "forecast_demand_kwh": actual + error,
                    }
                )
    demand_accuracy = pd.DataFrame(demand_accuracy_rows)
    references = [synthetic_prior_year_reference(day) for day in DEMAND_DAYS]
    dates = pd.DataFrame(
        {
            "date_key": [day.date() for day in DEMAND_DAYS],
            "is_weekend": [day.dayofweek >= 5 for day in DEMAND_DAYS],
            "is_holiday": [day in HOLIDAYS_2024_SPRING for day in DEMAND_DAYS],
            "prior_year_reference_date": [reference.date() for reference, _ in references],
            "prior_year_reference_rule": [rule for _, rule in references],
        }
    )
    hourly_load_rows: list[tuple] = []
    hourly_load_records: list[dict] = []
    for day in HOURLY_LOAD_DAYS:
        for hour in range(24):
            load_kwh = synthetic_hourly_load(day, hour)
            hourly_load_rows.append(
                (
                    day.date(),
                    hour,
                    TOKYO_AREA_KEY,
                    (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                    load_kwh,
                )
            )
            hourly_load_records.append(
                {"date_key": day.date(), "hour_of_day": hour, "demand_kwh": load_kwh}
            )
    hourly_load = pd.DataFrame(hourly_load_records)

    demand_rows: list[tuple] = []
    demand_records: list[dict] = []
    for day in DEMAND_DAYS:
        for tc in range(1, 49):
            in_hole = day == DEMAND_HOLE_DAY and tc in DEMAND_HOLE_TIME_CODES
            demand_kwh = None if in_hole else synthetic_demand(day, tc)
            demand_rows.append(
                (
                    day.date(),
                    tc,
                    TOKYO_AREA_KEY,
                    (day + pd.Timedelta(minutes=30 * (tc - 1))).to_pydatetime(),
                    demand_kwh,
                    synthetic_demand(day, tc) + 500_000,
                    1_000_000,
                )
            )
            demand_records.append(
                {
                    "date_key": day.date(),
                    "time_code": tc,
                    "area_key": TOKYO_AREA_KEY,
                    "demand_kwh": demand_kwh,
                }
            )
    demand = pd.DataFrame(demand_records).astype({"demand_kwh": "float64"})
    weather_rows: list[tuple] = []
    weather_records: list[dict] = []
    for day in DEMAND_DAYS:
        for hour in range(1, 25):
            temperature = (
                None
                if (day, hour) in TEMPERATURE_MISSING_HOURS
                else synthetic_temperature(day, hour)
            )
            weather_rows.append(
                (
                    TOKYO_STATION_ID,
                    (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                    (day + pd.Timedelta(hours=hour - 1)).to_pydatetime(),
                    day.date(),
                    temperature,
                )
            )
            weather_records.append(
                {
                    "station_id": TOKYO_STATION_ID,
                    "date_key": day.date(),
                    "hour_ending": hour,
                    "temperature_c": temperature,
                }
            )
    weather = pd.DataFrame(weather_records).astype({"temperature_c": "float64"})
    forecast_rows: list[tuple] = []
    forecast_records: list[dict] = []
    for day in DEMAND_DAYS:
        if day == FORECAST_MISSING_DAY:
            continue
        # The single ingested vintage: the 12 UTC run of D-2 (21:00 JST).
        reference_at = (day - pd.Timedelta(days=2) + pd.Timedelta(hours=21)).to_pydatetime()
        for hour in range(1, 25):
            for station_id, offset in (
                (TOKYO_STATION_ID, 0.0),
                (TOKYO_SECOND_STATION_ID, SECOND_STATION_FORECAST_OFFSET_C),
            ):
                if station_id == TOKYO_SECOND_STATION_ID and (day, hour) == (
                    SECOND_STATION_MISSING_HOUR
                ):
                    continue
                forecast = synthetic_forecast_temperature(day, hour) + offset
                forecast_rows.append(
                    (
                        station_id,
                        reference_at,
                        (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                        (day + pd.Timedelta(hours=hour - 1)).to_pydatetime(),
                        day.date(),
                        forecast,
                    )
                )
                forecast_records.append(
                    {
                        "station_id": station_id,
                        "date_key": day.date(),
                        "hour_ending": hour,
                        "forecast_temperature_c": forecast,
                    }
                )
    # chubu: one hour forecast by two MSM runs (the D-2 12 UTC one and a later one).
    day, hour = TWO_VINTAGE_HOUR
    for reference_at in (
        day - pd.Timedelta(days=2) + pd.Timedelta(hours=21),
        day - pd.Timedelta(days=1) + pd.Timedelta(hours=9),
    ):
        forecast_rows.append(
            (
                CHUBU_STATION_ID,
                reference_at.to_pydatetime(),
                (day + pd.Timedelta(hours=hour)).to_pydatetime(),
                (day + pd.Timedelta(hours=hour - 1)).to_pydatetime(),
                day.date(),
                12.0,
            )
        )
    weather_forecast = pd.DataFrame(forecast_records)
    station_weights = pd.DataFrame(
        [
            {
                "census_year": year,
                "station_id": station_id,
                "area_key": TOKYO_AREA_KEY,
                "n_meshes": 10,
                "population_total": int(weight * 1000),
                "area_population_total": 1000,
                "area_population_weight": weight,
            }
            for year, weights in STATION_POPULATION_WEIGHTS.items()
            for station_id, weight in weights.items()
        ]
        + [
            {
                "census_year": 2020,
                "station_id": station_id,
                "area_key": area_key,
                "n_meshes": 10,
                "population_total": 1000,
                "area_population_total": 1000,
                "area_population_weight": 1.0,
            }
            for station_id, area_key in (
                (KANSAI_STATION_ID, KANSAI_AREA_KEY),
                (CHUBU_STATION_ID, CHUBU_AREA_KEY),
            )
        ]
    )

    spark.sql("CREATE DATABASE IF NOT EXISTS pma_curated")
    spark.createDataFrame(
        AREAS, "area_key int, area_code string, representative_jma_station_id string"
    ).write.mode("overwrite").saveAsTable("pma_curated.dim_area")
    spark.createDataFrame(
        prices,
        "date_key date, time_code int, area_key int, trade_datetime timestamp, "
        "area_price_jpy_kwh double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_jepx_spot_area_price")
    spark.createDataFrame(
        occto,
        "date_key date, area_key int, max_demand_hour_ending int, max_demand_mw int, "
        "max_supply_capacity_mw int",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_occto_demand_supply_forecast_daily")
    spark.createDataFrame(delivery_periods, "time_code int, day_part string").write.mode(
        "overwrite"
    ).saveAsTable("pma_curated.dim_delivery_period")
    spark.createDataFrame(
        accuracy,
        "date_key date, time_code int, area_key int, run_id string, "
        "actual_price_jpy_kwh double, forecast_price_jpy_kwh double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_spot_price_forecast_accuracy")
    spark.createDataFrame(
        demand_rows,
        "date_key date, time_code int, area_key int, delivery_datetime timestamp, "
        "demand_kwh bigint, generation_kwh bigint, wind_solar_generation_kwh bigint",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_area_demand_generation_actual")
    spark.createDataFrame(
        weather_rows,
        "station_id string, observed_at timestamp, observed_hour_start_at timestamp, "
        "date_key date, temperature_c double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_jma_weather_hourly")
    spark.createDataFrame(
        forecast_rows,
        "station_id string, forecast_reference_at timestamp, forecast_valid_at timestamp, "
        "forecast_hour_start_at timestamp, date_key date, temperature_c double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_jma_msm_weather_forecast_hourly")
    spark.createDataFrame(
        station_weights,
        "census_year int, station_id string, area_key int, n_meshes int, "
        "population_total bigint, area_population_total bigint, area_population_weight double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_census_population_jma_station")
    spark.createDataFrame(
        demand_accuracy,
        "date_key date, time_code int, area_key int, run_id string, "
        "actual_demand_kwh double, forecast_demand_kwh double",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_demand_forecast_accuracy")
    spark.createDataFrame(
        dates,
        "date_key date, is_weekend boolean, is_holiday boolean, "
        "prior_year_reference_date date, prior_year_reference_rule string",
    ).write.mode("overwrite").saveAsTable("pma_curated.dim_date")
    spark.createDataFrame(
        hourly_load_rows,
        "date_key date, hour_of_day int, area_key int, delivery_datetime timestamp, "
        "demand_kwh bigint",
    ).write.mode("overwrite").saveAsTable("pma_curated.fct_area_power_usage_hourly")
    return CuratedWarehouse(
        areas=AREAS,
        prices=prices,
        occto=occto,
        delivery_periods=delivery_periods,
        accuracy=accuracy,
        demand=demand,
        weather=weather,
        weather_forecast=weather_forecast,
        station_weights=station_weights,
        demand_accuracy=demand_accuracy,
        dates=dates,
        hourly_load=hourly_load,
    )
