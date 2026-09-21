"""Area demand (load) forecasting.

Task definition: at 09:30 JST on day D-1 — before the 10:00 gate closure of
the JEPX day-ahead auction — forecast all 48 half-hourly area demand values
(``demand_kwh``, 30分kWh as the TSOs publish them) for delivery day D in one
area. At that moment the newest finalized TSO 実績 file is D-2's: a day's file
is finalized shortly after midnight of the following day, so D-1 is still in
progress. A strategy's usable demand history is therefore delivery days
<= D-2 (``history_lead_days = 2``). JMA hourly observations exist through
09:00 on D-1, but features use complete observation days only (<= D-2), so
every one of the 48 periods is built from the same window. Each area's
temperature comes from one representative JMA station
(``dim_area.representative_jma_station_id``).
"""

import pandas as pd

from power_market_analytics.forecasting.task import TaskSpec
from power_market_analytics.tasks.demand.frames import (
    AreaDemand,
    DemandBacktestResult,
    DemandForecast,
    DemandForecastRecords,
)

TASK = TaskSpec(
    name="demand",
    unit="kWh",
    history_lead_days=2,
    # Forecasts for delivery day D are issued at 09:30 JST on D-1.
    issue_offset=pd.Timedelta(days=-1, hours=9, minutes=30),
    forecast_table="pma_ml.demand_forecast",
    history_cls=AreaDemand,
    forecast_cls=DemandForecast,
    result_cls=DemandBacktestResult,
    records_cls=DemandForecastRecords,
    # Two fiscal years, FY2024 and FY2025. It opens on the first day that can be
    # scored at all: the target starts 2022-04-01 and the sliding 730-day
    # training window eats the two years before 2024-04-01. Everything after it
    # is the holdout, which no experiment reads until a confirmation run.
    eval_start=pd.Timestamp("2024-04-01"),
    eval_end=pd.Timestamp("2026-03-31"),
    # Not the day after eval_end: 49 runs scored past it before the window was
    # pinned - 43 through 2026-08-17 and 3 through 2026-09-05 - and the split of
    # those runs' per-day errors was read while deciding to pin at all. Nothing
    # before 2026-09-06 is unseen, whatever the window says.
    holdout_start=pd.Timestamp("2026-09-06"),
)

MLFLOW_EXPERIMENT = TASK.name
