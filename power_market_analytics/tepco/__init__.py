"""TEPCO Power Grid (東京電力パワーグリッド) public datasets, one module per dataset.

* :mod:`~power_market_analytics.tepco.area_demand_generation` — エリア需要・発電情報
  実績 (30-minute A-1 / B-1 / B-4 actuals, 2022-04 →).
* :mod:`~power_market_analytics.tepco.power_usage` — でんき予報 過去の電力使用実績
  (hourly 電力使用状況, 2016-04 →).

The names of the first dataset are re-exported here so
``from power_market_analytics.tepco import TEPCO, TepcoAreaDownloader`` keeps
working; the shared download/load machinery lives in
:mod:`power_market_analytics.area_actuals`.
"""

from power_market_analytics.tepco.area_demand_generation import (
    TEPCO,
    TepcoAreaCsvLoader,
    TepcoAreaDownloader,
    TepcoDownloadError,
    month_range,
)

__all__ = [
    "TEPCO",
    "TepcoAreaCsvLoader",
    "TepcoAreaDownloader",
    "TepcoDownloadError",
    "month_range",
]
