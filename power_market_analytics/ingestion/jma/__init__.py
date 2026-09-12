"""JMA (Japan Meteorological Agency) historical weather observations.

JMA serves historical observation data through the interactive page at
``https://www.data.jma.go.jp/risk/obsdl/index.php``. There is no documented API;
the page drives a small set of form-POST endpoints that this package calls
directly. The full reverse-engineered protocol — endpoints, payloads, station
model, request caps and CSV format — is documented in
``docs/JMA-Weather-Data-Retrieval.md``.

* :mod:`~power_market_analytics.ingestion.jma.client` — the throttled, retrying
  POST base both downloaders extend.
* :mod:`~power_market_analytics.ingestion.jma.hourly` — hourly observation CSVs
  from ``show/table``, one file per station, element set and calendar year.
* :mod:`~power_market_analytics.ingestion.jma.stations` — the station master
  (id, name, kana, prefecture, coordinates, elevation, observed-element mask,
  end-of-observation date) scraped from the per-prefecture ``top/station`` pages.
* :mod:`~power_market_analytics.ingestion.jma.load` — the hourly CSVs into
  ``pma_raw.jma_hourly_staffed``.

Only staffed stations (気象官署, ``s``-prefixed ids) inside a JEPX area are in
scope since the 2026-08 re-scope.
"""
