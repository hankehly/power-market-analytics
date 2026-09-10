"""The demand task's presets: the feature sets its LightGBM strategy runs on.

Each preset lists its features in the order the strategy class it replaces
used, so the models and their SHAP components read the same. The similar-day
strategies build on ``LIGHTGBM_MSM_POPW_DAYTYPE`` until the similar day is a
mart column of its own.
"""

from __future__ import annotations

from power_market_analytics.features.presets import Preset

TASK_NAME = "demand"

#: Calendar, the recency-weighted same-hour temperature over D-8..D-2 at the
#: representative station and the D-7 demand lag.
LIGHTGBM = Preset(
    task=TASK_NAME,
    name="lightgbm",
    features=(
        "ftr_day_calendar:month",
        "ftr_day_calendar:day_of_week",
        "ftr_hour_jma_obs:wavg_temperature_c",
        "ftr_period_actuals:lag_7d_demand_kwh",
    ),
)
#: Plus the MSM forecast temperature at the representative station (demand/R-001).
LIGHTGBM_MSM = LIGHTGBM.with_changes(
    name="lightgbm_msm", add=("ftr_hour_msm:forecast_temperature_c",)
)
#: Plus the same forecast population-weighted over the area's stations instead
#: (demand/R-002).
LIGHTGBM_MSM_POPW = LIGHTGBM.with_changes(
    name="lightgbm_msm_popw", add=("ftr_hour_msm:popw_forecast_temperature_c",)
)
#: Plus the delivery day's type, categorical by the mart's tag (demand/R-003;
#: the script default and the Kansai baseline).
LIGHTGBM_MSM_POPW_DAYTYPE = LIGHTGBM_MSM_POPW.with_changes(
    name="lightgbm_msm_popw_daytype", add=("ftr_day_calendar:day_type",)
)

#: Every preset by name: the registry keys before the similar-day strategies.
PRESETS: dict[str, Preset] = {
    preset.name: preset
    for preset in (LIGHTGBM, LIGHTGBM_MSM, LIGHTGBM_MSM_POPW, LIGHTGBM_MSM_POPW_DAYTYPE)
}
