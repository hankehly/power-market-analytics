"""The demand presets: the twelve files of conf/presets/demand, pinned to their tuples.

The eleven of 2026-09-19 keep the tuples they were registered with; ``e212``, ``e219``,
the joint test of experiment #212, is pinned to the 104 references it was
written with on 2026-09-20.
"""

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

#: The other 81 tagged columns of the seven marts we build ourselves, in view
#: order after the baseline's 23: the feature set of experiment #212. A
#: published preset is never edited, so this is a literal pin and not a recount
#: of the views — a mart that gains a column does not belong to this preset.
E212_REST = (
    # ftr_day_actuals
    "ftr_day_actuals:lag_2d_min_demand_kwh",
    "ftr_day_actuals:lag_2d_load_factor_demand",
    "ftr_day_actuals:lag_2d_morning_mean_demand_kwh",
    "ftr_day_actuals:lag_2d_afternoon_mean_demand_kwh",
    "ftr_day_actuals:lag_2d_evening_mean_demand_kwh",
    "ftr_day_actuals:lag_2d_peak_time_code",
    "ftr_day_actuals:lag_2d_morning_ramp_demand_kwh",
    "ftr_day_actuals:lag_2d_evening_ramp_demand_kwh",
    "ftr_day_actuals:ewm_5d_daily_max_demand_kwh",
    "ftr_day_actuals:ewm_weekly_lags_daily_max_demand_kwh",
    "ftr_day_actuals:ewm_5d_minus_ewm_weekly_lags_daily_max_demand_kwh",
    "ftr_day_actuals:ewm_5d_daily_mean_demand_kwh",
    "ftr_day_actuals:ewm_weekly_lags_daily_mean_demand_kwh",
    "ftr_day_actuals:ewm_5d_minus_ewm_weekly_lags_daily_mean_demand_kwh",
    "ftr_day_actuals:ewm_5d_daily_min_demand_kwh",
    "ftr_day_actuals:ewm_weekly_lags_daily_min_demand_kwh",
    "ftr_day_actuals:ewm_5d_minus_ewm_weekly_lags_daily_min_demand_kwh",
    # ftr_day_calendar
    "ftr_day_calendar:special_period",
    "ftr_day_calendar:lag_2d_day_type",
    "ftr_day_calendar:lag_3d_day_type",
    "ftr_day_calendar:lag_7d_day_type",
    "ftr_day_calendar:holiday_degree",
    "ftr_day_calendar:half",
    "ftr_day_calendar:quarter",
    "ftr_day_calendar:day_of_month",
    "ftr_day_calendar:day_of_quarter",
    "ftr_day_calendar:day_of_year",
    "ftr_day_calendar:is_business_day",
    "ftr_day_calendar:fiscal_quarter",
    "ftr_day_calendar:days_since_holiday",
    "ftr_day_calendar:days_until_holiday",
    # ftr_day_msm
    "ftr_day_msm:max_popw_forecast_temperature_c",
    "ftr_day_msm:min_popw_forecast_temperature_c",
    "ftr_day_msm:mean_popw_forecast_temperature_c",
    "ftr_day_msm:max_popw_forecast_temperature_hour_ending",
    "ftr_day_msm:morning_trend_popw_forecast_temperature_c",
    # ftr_hour_jma_obs
    "ftr_hour_jma_obs:mean_24h_popw_temperature_c",
    "ftr_hour_jma_obs:mean_72h_popw_temperature_c",
    "ftr_hour_jma_obs:ewm_72h_popw_temperature_c",
    # ftr_hour_msm
    "ftr_hour_msm:forecast_temperature_c",
    "ftr_hour_msm:popw_forecast_total_cloud_cover_pct",
    "ftr_hour_msm:popw_forecast_high_cloud_cover_pct",
    "ftr_hour_msm:popw_forecast_middle_cloud_cover_pct",
    "ftr_hour_msm:popw_forecast_low_cloud_cover_pct",
    "ftr_hour_msm:popw_forecast_wind_speed_ms",
    "ftr_hour_msm:popw_forecast_u_wind_ms",
    "ftr_hour_msm:popw_forecast_v_wind_ms",
    "ftr_hour_msm:popw_forecast_surface_pressure_hpa",
    "ftr_hour_msm:popw_forecast_sea_level_pressure_hpa",
    "ftr_hour_msm:popw_forecast_discomfort_index",
    "ftr_hour_msm:cum_popw_forecast_solar_radiation_mjm2",
    # ftr_period_actuals
    "ftr_period_actuals:lag_9d_demand_kwh",
    "ftr_period_actuals:ewstd_weekly_lags_demand_kwh",
    "ftr_period_actuals:trend_weekly_lags_demand_kwh",
    "ftr_period_actuals:std_weekly_lags_demand_kwh",
    "ftr_period_actuals:median_weekly_lags_demand_kwh",
    "ftr_period_actuals:zscore_7d_vs_14d_28d_demand_kwh",
    "ftr_period_actuals:lag_7d_adjacent_mean_demand_kwh",
    "ftr_period_actuals:lag_7d_ramp_demand_kwh",
    "ftr_period_actuals:mean_weekly_lags_ramp_demand_kwh",
    "ftr_period_actuals:newest_daytype_4d_lag_days",
    "ftr_period_actuals:oldest_daytype_4d_lag_days",
    "ftr_period_actuals:mean_daytype_weekly_lags_demand_kwh",
    "ftr_period_actuals:ewm_daytype_weekly_lags_demand_kwh",
    "ftr_period_actuals:ewm_5d_demand_kwh",
    "ftr_period_actuals:std_5d_demand_kwh",
    "ftr_period_actuals:ewstd_5d_demand_kwh",
    "ftr_period_actuals:ewm_5d_minus_ewm_weekly_lags_demand_kwh",
    "ftr_period_actuals:lag_2d_over_daily_mean_demand",
    "ftr_period_actuals:lag_7d_over_daily_mean_demand",
    "ftr_period_actuals:lag_2d_position_28d_demand",
    "ftr_period_actuals:rel_ewm_5d_minus_ewm_weekly_lags_demand",
    "ftr_period_actuals:rel_change_2d_9d_demand",
    "ftr_period_actuals:lag_7d_minus_median_weekly_lags_demand_kwh",
    "ftr_period_actuals:lag_2d_wind_solar_generation_kwh",
    "ftr_period_actuals:lag_7d_wind_solar_generation_kwh",
    # ftr_period_similar_day
    "ftr_period_similar_day:similar_day_rank2_demand_kwh",
    "ftr_period_similar_day:similar_day_rank3_demand_kwh",
    "ftr_period_similar_day:wavg_similar_day_top3_demand_kwh",
    "ftr_period_similar_day:similar_day_rank1_distance",
    "ftr_period_similar_day:similar_day_rank1_lag_days",
)
E212 = (*SIMDAY, *RECENT_LOAD, *MSM_ELEMENTS, *E212_REST)
#: The five categoricals of e212, in feature order: the mart tags, not the preset.
E212_CATEGORICALS = (
    "day_type",
    "special_period",
    "lag_2d_day_type",
    "lag_3d_day_type",
    "lag_7d_day_type",
)


def test_the_eleven_files_resolve_to_the_tuples_registered_on_2026_09_19():
    # The migration pin: a rerun of any preset sees the same columns in the same
    # order as before the move to files, so no published run's feature set moved.
    assert {
        name: (p.base, p.features) for name, p in PRESETS.items() if name in EXPECTED
    } == EXPECTED
    assert set(PRESETS) == set(EXPECTED) | {"e212", "e219"}
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
        if name not in ("e212", "e219")  # e212's five are pinned below; e219 shares them
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


def test_e212_is_every_feature_of_the_seven_marts_we_build_ourselves():
    preset = PRESETS["e212"]
    # The full list, not a base and an add: the baseline's 23 first, then the 81.
    assert preset.base is None
    assert preset.features == E212
    assert len(preset.features) == 104
    assert preset.columns == tuple(r.split(":")[1] for r in E212)
    assert preset.feature_cols == ("time_code", *preset.columns)
    # The two exogenous views are out at the researcher's ruling (issue #212).
    assert not [
        r for r in preset.features if r.startswith(("ftr_day_occto:", "ftr_period_jepx:"))
    ]
    # Every column resolves to a dtype LightGBM can take, and the categoricals
    # are the marts', not the preset's.
    assert set(feature_dtypes(preset)) == set(preset.columns)
    assert categorical_columns(preset) == E212_CATEGORICALS
