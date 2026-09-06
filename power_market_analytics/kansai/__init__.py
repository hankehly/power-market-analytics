"""関西電力送配電 (Kansai Transmission and Distribution) public datasets, one module per dataset.

* :mod:`~power_market_analytics.kansai.area_demand_generation` — エリア需給・発電（実績）
  (30-minute A-1 / B-1 / B-4 actuals, 2022-04 →).
* :mod:`~power_market_analytics.kansai.power_usage` — でんき予報 過去の電力使用実績
  (hourly 電力使用状況, 2016-04 →).

The names of the first dataset are re-exported here so
``from power_market_analytics.kansai import KANSAI, KansaiAreaDownloader`` keeps
working; the shared download/load machinery lives in
:mod:`power_market_analytics.area_actuals` and :mod:`power_market_analytics.power_usage`.
"""

from power_market_analytics.kansai.area_demand_generation import (
    KANSAI,
    KansaiAreaCsvLoader,
    KansaiAreaDownloader,
)

__all__ = ["KANSAI", "KansaiAreaCsvLoader", "KansaiAreaDownloader"]
