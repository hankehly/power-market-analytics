"""Forecast strategy registry for the spot price task."""

from power_market_analytics.tasks.spot_price.strategies.base import ForecastStrategy
from power_market_analytics.tasks.spot_price.strategies.naive import PreviousDayStrategy

STRATEGIES: dict[str, type[ForecastStrategy]] = {
    PreviousDayStrategy.name: PreviousDayStrategy,
}
