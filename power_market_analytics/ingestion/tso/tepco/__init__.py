"""TEPCO Power Grid (東京電力パワーグリッド) public datasets, one module per dataset.

* :mod:`~power_market_analytics.ingestion.tso.tepco.area_demand_generation` —
  エリア需要・発電情報 実績 (30-minute A-1 / B-1 / B-4 actuals, 2022-04 →).
* :mod:`~power_market_analytics.ingestion.tso.tepco.power_usage` — でんき予報
  過去の電力使用実績 (hourly 電力使用状況, 2016-04 →).

The shared download and load machinery is in
:mod:`power_market_analytics.ingestion.tso`; import every name from the module
that defines it.
"""
