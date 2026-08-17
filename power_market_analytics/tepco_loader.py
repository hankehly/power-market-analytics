"""Loader for TEPCO エリア需要・発電情報 actuals CSVs into a raw warehouse table.

Each ``AREA_JISEKI_YYYYMMDD.csv`` (format in
docs/TEPCO-Area-Demand-Generation-Retrieval.md) opens with two metadata lines
— the header ``ファイル更新日,ファイル更新時間,対象年月日`` and its values — before
the real column header, so the files are read positionally by the shared
:class:`~power_market_analytics.area_actuals.AreaActualsCsvLoader` (contract
``source: _c0`` .. ``_c6`` plus ``__file_updated_at``). This module only
binds that loader to the TEPCO source spec.
"""

from __future__ import annotations

from power_market_analytics.area_actuals import AreaActualsCsvLoader
from power_market_analytics.tepco import TEPCO

__all__ = ["TepcoAreaCsvLoader"]


class TepcoAreaCsvLoader(AreaActualsCsvLoader):
    """Positional full reload of TEPCO area actuals CSVs into a warehouse table.

    Same constructor as :class:`~power_market_analytics.csv_loader.CsvLoader`
    (``schema``, ``filepath``, ``table``, optional ``spark``); the source spec
    (accepted column-header line) is fixed to
    :data:`~power_market_analytics.tepco.TEPCO`.
    """

    source = TEPCO
