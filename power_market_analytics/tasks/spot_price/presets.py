"""The spot-price task's presets: the feature sets its LightGBM strategy runs on."""

from __future__ import annotations

from power_market_analytics.features.presets import Preset

TASK_NAME = "spot_price"

LIGHTGBM = Preset(
    task=TASK_NAME,
    name="lightgbm",
    features=(
        "ftr_day_calendar:month",
        "ftr_day_calendar:day_of_week",
        "ftr_period_jepx:lag_1d_price",
    ),
)
LIGHTGBM_OCCTO = LIGHTGBM.with_changes(
    name="lightgbm_occto",
    add=(
        "ftr_day_occto:max_demand_hour_ending",
        "ftr_day_occto:max_demand_mw",
        "ftr_day_occto:max_supply_capacity_mw",
    ),
)

#: Every preset by name: the registry keys next to ``previous_day``.
PRESETS: dict[str, Preset] = {preset.name: preset for preset in (LIGHTGBM, LIGHTGBM_OCCTO)}
