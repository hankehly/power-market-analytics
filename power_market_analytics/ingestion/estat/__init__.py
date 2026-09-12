"""e-Stat census 500 m population mesh (国勢調査 4次メッシュ).

e-Stat publishes each census on the 500 m mesh as one CP932 text file per
第１次地域区画, zipped, one archive per (statsId, primary mesh code).

* :mod:`~power_market_analytics.ingestion.estat.vintages` — the configured
  censuses and the URLs each is served at.
* :mod:`~power_market_analytics.ingestion.estat.mesh` — mesh-code validation and
  the bounding box / centroid each code encodes.
* :mod:`~power_market_analytics.ingestion.estat.download` — the archives.
* :mod:`~power_market_analytics.ingestion.estat.load` — the extracted text files
  into ``pma_raw.estat_census_population_mesh``.

Protocol and format: ``docs/eStat-Census-Population-Mesh-Retrieval.md``.
"""
