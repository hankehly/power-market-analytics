"""The demand task's presets: the four feature sets in the old classes' order."""

from __future__ import annotations

from power_market_analytics.features.presets import categorical_columns, feature_dtypes
from power_market_analytics.tasks.demand.presets import (
    LIGHTGBM,
    LIGHTGBM_MSM,
    LIGHTGBM_MSM_POPW,
    LIGHTGBM_MSM_POPW_DAYTYPE,
    PRESETS,
)


def test_the_four_presets_keep_the_old_feature_order():
    assert list(PRESETS) == [
        "lightgbm",
        "lightgbm_msm",
        "lightgbm_msm_popw",
        "lightgbm_msm_popw_daytype",
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


def test_the_presets_record_what_they_were_changed_from():
    assert LIGHTGBM.base is None
    assert LIGHTGBM_MSM.base == "lightgbm"
    assert LIGHTGBM_MSM_POPW.base == "lightgbm"
    assert LIGHTGBM_MSM_POPW_DAYTYPE.base == "lightgbm_msm_popw"


def test_types_and_categoricals_come_from_the_views():
    assert feature_dtypes(LIGHTGBM_MSM_POPW_DAYTYPE) == {
        "month": "int64",
        "day_of_week": "int64",
        "wavg_temperature_c": "float64",
        "lag_7d_demand_kwh": "int64",
        "popw_forecast_temperature_c": "float64",
        "day_type": "int64",
    }
    assert feature_dtypes(LIGHTGBM_MSM)["forecast_temperature_c"] == "float64"
    assert categorical_columns(LIGHTGBM_MSM_POPW_DAYTYPE) == ("day_type",)
    assert all(
        categorical_columns(preset) == ()
        for name, preset in PRESETS.items()
        if name != "lightgbm_msm_popw_daytype"
    )
