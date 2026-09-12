"""JMA MSM GPV surface forecasts from the Kyoto University RISH archive.

The MSM (メソ数値予報モデル) GPV is JMA's mesoscale numerical weather prediction
product, published on a Japan-region surface grid several times a day at
different forecast horizons. RISH mirrors the raw GRIB2 files at a stable,
publicly reachable URL. This package ingests one vintage per delivery day — the
12 UTC run of D-2, forecast hours 28-51 — and lands it as one extract per day.

* :mod:`~power_market_analytics.ingestion.msm.errors` — the error hierarchy.
* :mod:`~power_market_analytics.ingestion.msm.vintage` — which run covers a
  delivery day, which files hold it, and where each lead hour lands.
* :mod:`~power_market_analytics.ingestion.msm.stations` — the stations sampled.
* :mod:`~power_market_analytics.ingestion.msm.grid` — the surface grid and the
  point nearest a station.
* :mod:`~power_market_analytics.ingestion.msm.elements` — the GRIB2 parameters
  extracted, their unit conversions and the extract's columns.
* :mod:`~power_market_analytics.ingestion.msm.grib` — the ecCodes decoder.
* :mod:`~power_market_analytics.ingestion.msm.download` — the RISH client.
* :mod:`~power_market_analytics.ingestion.msm.load` — the extracts into
  ``pma_raw.jma_msm_surface_forecast``.

Protocol, file format and the GRIB2 element/grid metadata this package trusts:
``docs/JMA-MSM-GPV-Retrieval.md``.
"""
