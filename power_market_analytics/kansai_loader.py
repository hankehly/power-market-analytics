"""Loader for 関西電力送配電 エリア需給・発電（実績） CSVs into a raw warehouse table.

The daily files (format in docs/Kansai-Area-Demand-Generation-Retrieval.md)
open with metadata lines before the real column header — three lines until
2025-12-24 (a title line plus the two ``ファイル更新日`` lines), two from
2025-12-25 — so they are read positionally by the shared
:class:`~power_market_analytics.area_actuals.AreaActualsCsvLoader` (contract
``source: _c0`` .. ``_c6`` plus ``__file_updated_at``), which also normalises
the newer ``yyyy/mm/dd`` dates to ``yyyymmdd``. This module only binds that
loader to the Kansai source spec.
"""

from __future__ import annotations

from power_market_analytics.area_actuals import AreaActualsCsvLoader
from power_market_analytics.kansai import KANSAI

__all__ = ["KansaiAreaCsvLoader"]


class KansaiAreaCsvLoader(AreaActualsCsvLoader):
    """Positional full reload of Kansai area actuals CSVs into a warehouse table.

    Same constructor as :class:`~power_market_analytics.csv_loader.CsvLoader`
    (``schema``, ``filepath``, ``table``, optional ``spark``); the source spec
    (accepted column-header lines) is fixed to
    :data:`~power_market_analytics.kansai.KANSAI`.
    """

    source = KANSAI
