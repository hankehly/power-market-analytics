"""The demand presets: the eleven files of conf/presets/demand, pinned to the tuples of 2026-09-19."""

from __future__ import annotations

from power_market_analytics.features.presets import (
    categorical_columns,
    feature_dtypes,
    load_presets,
)
from tests.conftest import RECENT_LOAD_COLUMNS

PRESETS = load_presets("demand")

#: The base four, then the MSM temperature, the day type and the similar day.
SIMDAY = (
    "ftr_day_calendar:month",
    "ftr_day_calendar:day_of_week",
    "ftr_hour_jma_obs:wavg_temperature_c",
    "ftr_period_actuals:lag_7d_demand_kwh",
    "ftr_hour_msm:popw_forecast_temperature_c",
    "ftr_day_calendar:day_type",
    "ftr_period_similar_day:similar_day_rank1_demand_kwh",
)
CALENDAR_TEN = tuple(
    f"ftr_day_calendar:{c}"
    for c in (
        "half",
        "quarter",
        "day_of_month",
        "day_of_quarter",
        "day_of_year",
        "holiday_degree",
        "is_business_day",
        "fiscal_quarter",
        "days_since_holiday",
        "days_until_holiday",
    )
)
CALENDAR_COUNTS = tuple(
    f"ftr_day_calendar:{c}"
    for c in ("half", "quarter", "day_of_month", "day_of_quarter", "day_of_year", "fiscal_quarter")
)
RECENT_LOAD = (
    "ftr_period_actuals:lag_2d_demand_kwh",
    "ftr_period_actuals:lag_3d_demand_kwh",
    "ftr_period_actuals:lag_14d_demand_kwh",
    "ftr_period_actuals:lag_21d_demand_kwh",
    "ftr_period_actuals:lag_28d_demand_kwh",
    "ftr_period_actuals:mean_weekly_lags_demand_kwh",
    "ftr_period_actuals:ewm_weekly_lags_demand_kwh",
    "ftr_period_actuals:change_2d_9d_demand_kwh",
    "ftr_period_actuals:mean_daytype_4d_demand_kwh",
    "ftr_period_actuals:ewm_daytype_4d_demand_kwh",
    "ftr_day_actuals:lag_2d_mean_demand_kwh",
    "ftr_day_actuals:lag_2d_max_demand_kwh",
    "ftr_day_actuals:lag_2d_range_demand_kwh",
)
MSM_ELEMENTS = (
    "ftr_hour_msm:popw_forecast_relative_humidity_pct",
    "ftr_hour_msm:popw_forecast_precipitation_mm",
    "ftr_hour_msm:popw_forecast_solar_radiation_mjm2",
)
SIMDAY_NAME = "lightgbm_msm_popw_daytype_simday"
#: Every preset as tasks/demand/presets.py registered it on 2026-09-19: base, then features.
EXPECTED = {
    "lightgbm": (None, SIMDAY[:4]),
    "lightgbm_msm": ("lightgbm", (*SIMDAY[:4], "ftr_hour_msm:forecast_temperature_c")),
    "lightgbm_msm_popw": ("lightgbm", SIMDAY[:5]),
    "lightgbm_msm_popw_daytype": ("lightgbm_msm_popw", SIMDAY[:6]),
    SIMDAY_NAME: ("lightgbm_msm_popw_daytype", SIMDAY),
    f"{SIMDAY_NAME}_calendar": (SIMDAY_NAME, (*SIMDAY, *CALENDAR_TEN)),
    f"{SIMDAY_NAME}_calendarcounts": (SIMDAY_NAME, (*SIMDAY, *CALENDAR_COUNTS)),
    f"{SIMDAY_NAME}_holidaydegree": (SIMDAY_NAME, (*SIMDAY, "ftr_day_calendar:holiday_degree")),
    f"{SIMDAY_NAME}_holidaydistance": (
        SIMDAY_NAME,
        (*SIMDAY, "ftr_day_calendar:days_since_holiday", "ftr_day_calendar:days_until_holiday"),
    ),
    f"{SIMDAY_NAME}_lags": (SIMDAY_NAME, (*SIMDAY, *RECENT_LOAD)),
    f"{SIMDAY_NAME}_lags_weather": (f"{SIMDAY_NAME}_lags", (*SIMDAY, *RECENT_LOAD, *MSM_ELEMENTS)),
}


def test_the_eleven_files_resolve_to_the_tuples_registered_on_2026_09_19():
    # The migration pin: a rerun of any preset sees the same columns in the same
    # order as before the move to files, so no published run's feature set moved.
    assert {name: (p.base, p.features) for name, p in PRESETS.items()} == EXPECTED
    assert all(p.task == "demand" for p in PRESETS.values())
    assert PRESETS["lightgbm"].feature_cols == (
        "time_code",
        "month",
        "day_of_week",
        "wavg_temperature_c",
        "lag_7d_demand_kwh",
    )


def test_every_file_has_a_description():
    assert all(p.description for p in PRESETS.values())
    assert PRESETS[f"{SIMDAY_NAME}_lags_weather"].description.startswith("Plus the three other MSM")


def test_the_calendar_subsets_partition_the_ten_but_the_working_day_flag():
    subsets = (
        ("ftr_day_calendar:holiday_degree",),
        ("ftr_day_calendar:days_since_holiday", "ftr_day_calendar:days_until_holiday"),
        CALENDAR_COUNTS,
    )
    assert sum(len(s) for s in subsets) == len(CALENDAR_TEN) - 1
    assert set().union(*subsets) == set(CALENDAR_TEN) - {"ftr_day_calendar:is_business_day"}


def test_types_and_categoricals_come_from_the_views():
    assert feature_dtypes(PRESETS[SIMDAY_NAME]) == {
        "month": "int64",
        "day_of_week": "int64",
        "wavg_temperature_c": "float64",
        "lag_7d_demand_kwh": "int64",
        "popw_forecast_temperature_c": "float64",
        "day_type": "int64",
        "similar_day_rank1_demand_kwh": "float64",
    }
    assert feature_dtypes(PRESETS["lightgbm_msm"])["forecast_temperature_c"] == "float64"
    calendar = feature_dtypes(PRESETS[f"{SIMDAY_NAME}_calendar"])
    assert calendar["holiday_degree"] == "float64"
    assert all(
        calendar[c.split(":")[1]] == "int64" for c in CALENDAR_TEN if "holiday_degree" not in c
    )
    assert all(
        categorical_columns(preset) == (("day_type",) if "daytype" in name else ())
        for name, preset in PRESETS.items()
    )


def test_the_lags_preset_appends_the_thirteen_recent_load_features():
    lags = PRESETS[f"{SIMDAY_NAME}_lags"]
    assert lags.columns == (*(r.split(":")[1] for r in SIMDAY), *RECENT_LOAD_COLUMNS)
    # The D-9 lag is a mart column for the change, not a feature of the preset.
    assert "ftr_period_actuals:lag_9d_demand_kwh" not in lags.features
    dtypes = feature_dtypes(lags)
    assert {c: dtypes[c] for c in RECENT_LOAD_COLUMNS} == {
        "lag_2d_demand_kwh": "int64",
        "lag_3d_demand_kwh": "int64",
        "lag_14d_demand_kwh": "int64",
        "lag_21d_demand_kwh": "int64",
        "lag_28d_demand_kwh": "int64",
        "mean_weekly_lags_demand_kwh": "float64",
        "ewm_weekly_lags_demand_kwh": "float64",
        "change_2d_9d_demand_kwh": "int64",
        "mean_daytype_4d_demand_kwh": "float64",
        "ewm_daytype_4d_demand_kwh": "float64",
        "lag_2d_mean_demand_kwh": "float64",
        "lag_2d_max_demand_kwh": "int64",
        "lag_2d_range_demand_kwh": "int64",
    }
    weather = PRESETS[f"{SIMDAY_NAME}_lags_weather"]
    assert len(weather.features) == len(lags.features) + 3
    assert all(feature_dtypes(weather)[c.split(":")[1]] == "float64" for c in MSM_ELEMENTS)
