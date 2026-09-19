"""The spot-price presets: the two files of conf/presets/spot_price, pinned to the tuples of 2026-09-19."""

from __future__ import annotations

from power_market_analytics.features.presets import (
    PRESETS_DIR,
    feature_dtypes,
    load_presets,
    preset_tasks,
)

PRESETS = load_presets("spot_price")
BASE = ("ftr_day_calendar:month", "ftr_day_calendar:day_of_week", "ftr_period_jepx:lag_1d_price")
OCCTO = (
    "ftr_day_occto:max_demand_hour_ending",
    "ftr_day_occto:max_demand_mw",
    "ftr_day_occto:max_supply_capacity_mw",
)


def test_the_two_files_resolve_to_the_tuples_registered_on_2026_09_19():
    assert {name: (p.base, p.features) for name, p in PRESETS.items()} == {
        "lightgbm": (None, BASE),
        "lightgbm_occto": ("lightgbm", (*BASE, *OCCTO)),
    }
    assert all(p.task == "spot_price" and p.description for p in PRESETS.values())


def test_every_preset_file_of_every_task_resolves_against_the_views():
    # A typo in a file fails here, not at the first backtest.
    for task in preset_tasks():
        for name, preset in load_presets(task).items():
            dtypes = feature_dtypes(preset)
            assert list(dtypes) == list(preset.columns), f"{task}/{name}"
    assert sorted(p.name for p in PRESETS_DIR.iterdir()) == ["demand", "spot_price"]
