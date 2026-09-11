"""The demand task's presets: the nine feature sets in the old classes' order."""

from __future__ import annotations

from power_market_analytics.features.presets import categorical_columns, feature_dtypes
from power_market_analytics.tasks.demand.presets import (
    CALENDAR_COUNT_FEATURES,
    DAY_CALENDAR_FEATURES,
    HOLIDAY_DEGREE_FEATURES,
    HOLIDAY_DISTANCE_FEATURES,
    LIGHTGBM,
    LIGHTGBM_MSM,
    LIGHTGBM_MSM_POPW,
    LIGHTGBM_MSM_POPW_DAYTYPE,
    LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY,
    LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDAR,
    LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDARCOUNTS,
    LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDEGREE,
    LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDISTANCE,
    PRESETS,
    SIMILAR_DAY_FEATURE,
)

SIMDAY_COLUMNS = (
    "month",
    "day_of_week",
    "wavg_temperature_c",
    "lag_7d_demand_kwh",
    "popw_forecast_temperature_c",
    "day_type",
    "similar_day_demand_kwh",
)
DAY_CALENDAR_COLUMNS = (
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


def test_the_nine_presets_keep_the_old_feature_order():
    assert list(PRESETS) == [
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
    assert PRESETS["lightgbm"] is LIGHTGBM
    assert LIGHTGBM.feature_cols == (
        "time_code",
        "month",
        "day_of_week",
        "wavg_temperature_c",
        "lag_7d_demand_kwh",
    )
    assert LIGHTGBM_MSM.columns == (*LIGHTGBM.columns, "forecast_temperature_c")
    assert LIGHTGBM_MSM_POPW.columns == (*LIGHTGBM.columns, "popw_forecast_temperature_c")
    assert LIGHTGBM_MSM_POPW_DAYTYPE.columns == (*LIGHTGBM_MSM_POPW.columns, "day_type")
    assert all(preset.task == "demand" for preset in PRESETS.values())


def test_the_similar_day_presets_add_the_mart_column_then_the_calendar_columns():
    assert SIMILAR_DAY_FEATURE == "ftr_period_similar_day:similar_day_demand_kwh"
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.features[-1] == SIMILAR_DAY_FEATURE
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.columns == SIMDAY_COLUMNS
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDAR.columns == (
        *SIMDAY_COLUMNS,
        *DAY_CALENDAR_COLUMNS,
    )
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDARCOUNTS.columns == (
        *SIMDAY_COLUMNS,
        "half",
        "quarter",
        "day_of_month",
        "day_of_quarter",
        "day_of_year",
        "fiscal_quarter",
    )
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDEGREE.columns == (
        *SIMDAY_COLUMNS,
        "holiday_degree",
    )
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDISTANCE.columns == (
        *SIMDAY_COLUMNS,
        "days_since_holiday",
        "days_until_holiday",
    )
    assert DAY_CALENDAR_FEATURES == tuple(f"ftr_day_calendar:{c}" for c in DAY_CALENDAR_COLUMNS)
    # The three subsets partition the ten calendar columns but the working-day flag.
    subsets = (HOLIDAY_DEGREE_FEATURES, HOLIDAY_DISTANCE_FEATURES, CALENDAR_COUNT_FEATURES)
    assert sum(len(subset) for subset in subsets) == len(DAY_CALENDAR_FEATURES) - 1
    assert set().union(*subsets) == set(DAY_CALENDAR_FEATURES) - {
        "ftr_day_calendar:is_business_day"
    }


def test_the_presets_record_what_they_were_changed_from():
    assert LIGHTGBM.base is None
    assert LIGHTGBM_MSM.base == "lightgbm"
    assert LIGHTGBM_MSM_POPW.base == "lightgbm"
    assert LIGHTGBM_MSM_POPW_DAYTYPE.base == "lightgbm_msm_popw"
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.base == "lightgbm_msm_popw_daytype"
    assert all(
        PRESETS[name].base == "lightgbm_msm_popw_daytype_simday"
        for name in (
            "lightgbm_msm_popw_daytype_simday_calendar",
            "lightgbm_msm_popw_daytype_simday_calendarcounts",
            "lightgbm_msm_popw_daytype_simday_holidaydegree",
            "lightgbm_msm_popw_daytype_simday_holidaydistance",
        )
    )


def test_types_and_categoricals_come_from_the_views():
    assert feature_dtypes(LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY) == {
        "month": "int64",
        "day_of_week": "int64",
        "wavg_temperature_c": "float64",
        "lag_7d_demand_kwh": "int64",
        "popw_forecast_temperature_c": "float64",
        "day_type": "int64",
        "similar_day_demand_kwh": "float64",
    }
    assert feature_dtypes(LIGHTGBM_MSM)["forecast_temperature_c"] == "float64"
    calendar = feature_dtypes(LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_CALENDAR)
    assert calendar["holiday_degree"] == "float64"
    assert all(calendar[c] == "int64" for c in DAY_CALENDAR_COLUMNS if c != "holiday_degree")
    assert all(
        categorical_columns(preset) == (("day_type",) if "daytype" in name else ())
        for name, preset in PRESETS.items()
    )
