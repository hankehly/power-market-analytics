"""一般送配電事業者 (TSO) feeds: the two dataset families, and one package per TSO.

Every TSO publishes the same two families in the same shape — monthly zips of
daily CSVs, past days occasionally re-issued, so both are always re-downloaded
whole. The download and load machinery therefore lives here and each TSO package
supplies only its own spec (URL template, earliest month, member names, accepted
header lines, known missing days):

* :mod:`~power_market_analytics.ingestion.tso.area_actuals` — エリア需要・発電情報
  実績, the 30-minute A-1 / B-1 / B-4 actuals published from 2022-04.
* :mod:`~power_market_analytics.ingestion.tso.power_usage` — でんき予報 過去の
  電力使用実績, the hourly 電力使用状況 series published from 2016-04, and the only
  public area demand before 2022-04.

Adding a TSO is a package here with one module per dataset, a load contract in
``conf/schemas/``, its staging and standardized models, and one branch of the
curated fact's ``union all``.
"""
