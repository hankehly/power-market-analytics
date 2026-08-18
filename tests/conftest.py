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
def spark(tmp_path_factory: pytest.TempPathFactory) -> SparkSession:
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

AREAS = pd.DataFrame(
    {
        "area_key": [1, 2],
        "area_code": ["tokyo", "kansai"],
        "representative_jma_station_id": [TOKYO_STATION_ID, KANSAI_STATION_ID],
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
#: One partial-day hole like Tokyo 2025-06-14: time codes 11..48 have null demand.
DEMAND_HOLE_DAY = pd.Timestamp("2024-04-20")
DEMAND_HOLE_TIME_CODES = range(11, 49)
#: (observation day, hour_ending) pairs whose temperature is null.
TEMPERATURE_MISSING_HOURS = {(pd.Timestamp("2024-04-25"), 13), (pd.Timestamp("2024-04-25"), 14)}


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
    """

    areas: pd.DataFrame
    prices: pd.DataFrame
    occto: pd.DataFrame
    delivery_periods: pd.DataFrame
    accuracy: pd.DataFrame
    demand: pd.DataFrame
    weather: pd.DataFrame


@pytest.fixture(scope="session")
def curated_warehouse(spark: SparkSession) -> CuratedWarehouse:
    """Create the ``pma_curated`` tables the spot-price task reads.

    Tokyo has prices for every day of ``PRICE_DAYS`` × 48 time codes and
    OCCTO forecasts for ``OCCTO_DAYS``; Kansai has an area row but no facts.
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
    return CuratedWarehouse(
        areas=AREAS,
        prices=prices,
        occto=occto,
        delivery_periods=delivery_periods,
        accuracy=accuracy,
        demand=demand,
        weather=weather,
    )
