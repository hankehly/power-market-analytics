"""Every task's presets as Feast feature services, for the registry and the UI."""

from __future__ import annotations

from feast import FeatureService

from power_market_analytics.features.presets import feature_service, load_presets, preset_tasks


def feature_services() -> list[FeatureService]:
    """The feature services of every preset file, by task then name.

    Returns
    -------
    list of feast.FeatureService
    """
    return [
        feature_service(preset) for task in preset_tasks() for preset in load_presets(task).values()
    ]
