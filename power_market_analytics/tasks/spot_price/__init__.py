"""Day-ahead JEPX spot price forecasting.

Task definition: at 9:55 JST on day D-1 (just before the 10:00 gate closure
of the day-ahead auction), forecast all 48 half-hour prices for delivery day
D in a given area. At that moment the newest published spot results are for
delivery day D-1 (published ~noon on D-2), so a strategy's usable history is
delivery days <= D-1.
"""

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks.spot_price.frames import (
    SpotPriceBacktestResult,
    SpotPriceForecast,
    SpotPriceForecastRecords,
    SpotPrices,
)

TASK = TaskSpec(
    name="spot_price",
    unit="JPY/kWh",
    history_lead_days=1,
    # Forecasts for delivery day D are issued at 9:55 JST on D-1.
    issue_offset=pd.Timedelta(days=-1, hours=9, minutes=55),
    forecast_table="pma_ml.spot_price_forecast",
    history_cls=SpotPrices,
    forecast_cls=SpotPriceForecast,
    result_cls=SpotPriceBacktestResult,
    records_cls=SpotPriceForecastRecords,
)

MLFLOW_EXPERIMENT = TASK.name
