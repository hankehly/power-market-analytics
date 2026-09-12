"""Ingestion: one module or package per publisher, plus the loader they all share.

Every source here does the same two things and nothing else — download a
publisher's files into ``data/<source>/``, and load them into a ``pma_raw``
table through a load contract in ``conf/schemas/``. Everything downstream of
``pma_raw`` is dbt's.

* :mod:`~power_market_analytics.ingestion.loader` — the contract-driven Spark
  CSV loader every source's loader extends.
* :mod:`~power_market_analytics.ingestion.jepx` — JEPX spot market CSVs.
* :mod:`~power_market_analytics.ingestion.jma` — JMA hourly observations and the
  station master.
* :mod:`~power_market_analytics.ingestion.msm` — JMA MSM GPV surface forecasts
  from the Kyoto University RISH archive.
* :mod:`~power_market_analytics.ingestion.occto` — OCCTO 情報ダウンロード datasets.
* :mod:`~power_market_analytics.ingestion.estat` — e-Stat census 500 m population mesh.
* :mod:`~power_market_analytics.ingestion.tso` — the 一般送配電事業者 feeds.

A source is a module until it needs more than one file; there is no other rule.
"""
