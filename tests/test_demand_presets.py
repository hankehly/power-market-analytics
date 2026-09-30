"""The demand presets: the sixteen files of conf/presets/demand, pinned to their tuples.

The ten renamed on 2026-09-27 (``e169`` … ``e179``, chain names until then) keep
the tuples they were registered with on 2026-09-19; ``e212``, the joint test of
experiment #212, is pinned to the 104 references it was written with on
2026-09-20; ``e219`` and ``e221`` are counted, not pinned; the lag-window weather
batches ``e243``, ``e244`` and ``e245`` (2026-09-29) are counted and pinned to what
they add to ``e219``.
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
#: The similar-day preset: lightgbm_msm_popw_daytype_simday until 2026-09-27.
SIMDAY_NAME = "e173"
#: Every preset as tasks/demand/presets.py registered it on 2026-09-19 — base,
#: then features — under the name it carries since 2026-09-27, its experiment's.
#: The four-feature root lightgbm went that day, written out in e169 and e170,
#: so those two have no base.
EXPECTED = {
    "e169": (None, (*SIMDAY[:4], "ftr_hour_msm:forecast_temperature_c")),
    "e170": (None, SIMDAY[:5]),
    "e171": ("e170", SIMDAY[:6]),
    SIMDAY_NAME: ("e171", SIMDAY),
    "e174": (SIMDAY_NAME, (*SIMDAY, *CALENDAR_TEN)),
    "e175": (SIMDAY_NAME, (*SIMDAY, "ftr_day_calendar:holiday_degree")),
    "e176": (
        SIMDAY_NAME,
        (*SIMDAY, "ftr_day_calendar:days_since_holiday", "ftr_day_calendar:days_until_holiday"),
    ),
    "e177": (SIMDAY_NAME, (*SIMDAY, *CALENDAR_COUNTS)),
    "e178": (SIMDAY_NAME, (*SIMDAY, *RECENT_LOAD)),
    "e179": ("e178", (*SIMDAY, *RECENT_LOAD, *MSM_ELEMENTS)),
}
#: The presets that carry ftr_day_calendar:day_type, the one categorical outside e212's five.
WITH_DAY_TYPE = frozenset({"e171", SIMDAY_NAME, "e174", "e175", "e176", "e177", "e178", "e179"})

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


def test_the_ten_renamed_files_resolve_to_the_tuples_registered_on_2026_09_19():
    # The migration pin: a rerun of any preset sees the same columns in the same
    # order as before the move to files and before the rename, so no published
    # run's feature set moved.
    assert {
        name: (p.base, p.features) for name, p in PRESETS.items() if name in EXPECTED
    } == EXPECTED
    assert set(PRESETS) == set(EXPECTED) | {
        "e212",
        "e219",
        "e221",
        "e243",
        "e244",
        "e245",
        "e249",
    }
    assert all(p.task == "demand" for p in PRESETS.values())
    # The root's four, written out twice, still lead both of its former children.
    base_four = ("time_code", "month", "day_of_week", "wavg_temperature_c", "lag_7d_demand_kwh")
    assert PRESETS["e169"].feature_cols[:5] == base_four
    assert PRESETS["e170"].feature_cols[:5] == base_four


def test_the_lag_window_weather_batches_add_their_columns_to_e219():
    # The three experiment presets of 2026-09-29: e219 plus the 21 temperature
    # columns, the 19 radiation columns, and both; a batch adds and drops nothing else.
    baseline = PRESETS["e219"]
    added = {
        name: tuple(f for f in PRESETS[name].features if f not in baseline.features)
        for name in ("e243", "e244", "e245")
    }
    assert all(PRESETS[name].base == "e219" for name in added)
    assert all(set(baseline.features) <= set(PRESETS[name].features) for name in added)
    assert {name: len(refs) for name, refs in added.items()} == {"e243": 21, "e244": 19, "e245": 40}
    assert added["e245"] == (*added["e243"], *added["e244"])
    # Every added column names its element: the observed siblings and deltas end in
    # popw_<element>, the two D-side forecast means in popw_forecast_<element>.
    assert all(ref.split(":")[1].endswith("temperature_c") for ref in added["e243"])
    assert all(ref.split(":")[1].endswith("solar_radiation_mjm2") for ref in added["e244"])
    assert all(
        categorical_columns(PRESETS[name]) == categorical_columns(baseline) for name in added
    )


def test_e249_adds_the_eleven_d1_forecast_columns_to_e245():
    # Experiment #249, 2026-09-30: e245 plus feature candidate #248's eleven columns,
    # three in ftr_hour_msm and eight in ftr_day_msm; nothing dropped.
    baseline = PRESETS["e245"]
    preset = PRESETS["e249"]
    added = tuple(f for f in preset.features if f not in baseline.features)
    assert preset.base == "e245"
    assert set(baseline.features) <= set(preset.features)
    assert added == (
        "ftr_hour_msm:lag_1d_popw_forecast_temperature_c",
        "ftr_hour_msm:lag_1d_popw_forecast_solar_radiation_mjm2",
        "ftr_hour_msm:delta_lag_1d_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_mean_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_min_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_evening_mean_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_mean_popw_forecast_solar_radiation_mjm2",
        "ftr_day_msm:lag_1d_mean_popw_forecast_precipitation_mm",
        "ftr_day_msm:delta_lag_1d_mean_popw_forecast_temperature_c",
        "ftr_day_msm:change_1d_2d_mean_popw_temperature_c",
        "ftr_day_msm:mean_3d_popw_temperature_c",
    )
    assert len(preset.features) == len(baseline.features) + 11
    assert categorical_columns(preset) == categorical_columns(baseline)


def test_every_file_has_a_description():
    assert all(p.description for p in PRESETS.values())
    assert PRESETS["e179"].description.startswith("Plus the three other MSM")


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
    assert feature_dtypes(PRESETS["e169"])["forecast_temperature_c"] == "float64"
    calendar = feature_dtypes(PRESETS["e174"])
    assert calendar["holiday_degree"] == "float64"
    assert all(
        calendar[c.split(":")[1]] == "int64" for c in CALENDAR_TEN if "holiday_degree" not in c
    )
    assert all(
        categorical_columns(preset) == (("day_type",) if name in WITH_DAY_TYPE else ())
        for name, preset in PRESETS.items()
        # e212's five are pinned below; e219, e221 and the e219-based batches share them.
        if name not in ("e212", "e219", "e221", "e243", "e244", "e245", "e249")
    )


def test_the_lags_preset_appends_the_thirteen_recent_load_features():
    lags = PRESETS["e178"]
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
    weather = PRESETS["e179"]
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
    assert not [r for r in preset.features if r.startswith(("ftr_day_occto:", "ftr_period_jepx:"))]
    # Every column resolves to a dtype LightGBM can take, and the categoricals
    # are the marts', not the preset's.
    assert set(feature_dtypes(preset)) == set(preset.columns)
    assert categorical_columns(preset) == E212_CATEGORICALS
