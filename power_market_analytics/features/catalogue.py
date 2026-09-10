"""Every task's presets as Feast feature services, for the registry and the UI."""

from __future__ import annotations

from feast import FeatureService

from power_market_analytics.features.presets import feature_service


def feature_services() -> list[FeatureService]:
    """The feature services of every registered preset, by task.

    Returns
    -------
    list of feast.FeatureService
    """
    # Imported here, not at module level: the tasks import the features
    # package, so the package must not import them back when it loads.
    from power_market_analytics.tasks.demand.presets import PRESETS as DEMAND_PRESETS
    from power_market_analytics.tasks.spot_price.presets import PRESETS as SPOT_PRESETS

    return [
        feature_service(preset)
        for presets in (DEMAND_PRESETS, SPOT_PRESETS)
        for preset in presets.values()
    ]
