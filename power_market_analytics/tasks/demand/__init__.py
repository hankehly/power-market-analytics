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
    # Two fiscal years, FY2024 and FY2025. It opens where the configured 730-day
    # training window is first full: the target starts 2022-04-01, and a model
    # trains on as much history as exists, so earlier days are scorable but on a
    # shorter window. Everything after it is the holdout.
    eval_start=pd.Timestamp("2024-04-01"),
    eval_end=pd.Timestamp("2026-03-31"),
    # Reserved, not derived. It opens after the last day any run had scored when
    # the window was pinned - 49 of them reached past eval_end, 3 as far as
    # 2026-09-05 - and runs to the end of FY2026. Only 14 of its days hold data
    # today; the rest fill as the data arrives, which is the point of reserving
    # rather than waiting. Every run of one confirmation batch scores the same
    # days; once a batch has spent it, move both dates in a pull request.
    holdout_start=pd.Timestamp("2026-09-06"),
    holdout_end=pd.Timestamp("2027-03-31"),
)

MLFLOW_EXPERIMENT = TASK.name
