"""関西電力送配電 でんき予報 過去の電力使用実績 (Kansai-area hourly 電力使用状況) archive spec.

Kansai Transmission and Distribution publishes its でんき予報 demand history on
https://www.kansai-td.co.jp/denkiyoho/download/ as one zip per month,
``https://www.kansai-td.co.jp/yamasou/YYYYMM_jisseki.zip`` (2016-04 → the
current month, 57–78 KB each, ~8.5 MB in all, listed in
``…/yamasou/jisseki.json``), of daily multi-section CSVs —
``YYYYMMDD_juyo1_kansai.csv`` through 2025-11, ``juyo_06_YYYYMMDD.csv`` from
2025-12 — whose hourly table carries ``当日実績(万kW)``, ``予想値(万kW)``,
``使用率(%)`` and, from 2019-09-12, ``供給力想定値(万kW)`` (``供給力(万kW)``
from 2025-12-25). The archive holds finished days only (day D appears on D+1
at 01:10); 2024-03-31 was never published (site maintenance). Format, quirks
and the comparison against the A-1 series: docs/Kansai-Power-Usage-Retrieval.md.

The parser and loader are the shared :mod:`power_market_analytics.ingestion.tso.power_usage`;
this module only supplies the Kansai
:class:`~power_market_analytics.ingestion.tso.power_usage.PowerUsageSource` and convenience
subclasses bound to it.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import requests

from power_market_analytics.ingestion.tso.area_actuals import AreaActualsDownloader
from power_market_analytics.ingestion.tso.power_usage import PowerUsageCsvLoader, PowerUsageSource

__all__ = [
    "HOURLY_HEADER_2016",
    "HOURLY_HEADER_2019",
    "HOURLY_HEADER_2025",
    "KANSAI_POWER_USAGE",
    "MISSING_DAY",
    "SUPPLY_CAPACITY_FROM",
    "KansaiPowerUsageCsvLoader",
    "KansaiPowerUsageDownloader",
]

#: Hourly-table header 2016-04-01 → 2019-09-11: no supply-capacity column.
HOURLY_HEADER_2016 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"
#: 2019-09-12 → 2025-12-24: + 供給力想定値.
HOURLY_HEADER_2019 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力想定値(万kW)"
#: 2025-12-25 onward: 供給力 instead (the month the A-1 feed also renamed its members).
HOURLY_HEADER_2025 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力(万kW)"
#: First delivery day with a supply-capacity column (std pins the null pattern to it).
SUPPLY_CAPACITY_FROM = datetime.date(2019, 9, 12)
#: The one day Kansai never published (site maintenance that day, per the page).
MISSING_DAY = datetime.date(2024, 3, 31)

KANSAI_POWER_USAGE = PowerUsageSource(
    code="kansai_power_usage",
    url_template="https://www.kansai-td.co.jp/yamasou/{year:04d}{month:02d}_jisseki.zip",
    earliest_month=(2016, 4),
    #: Daily members in either naming generation; members are flat.
    member_re=re.compile(r"(^|/)(\d{8}_juyo1_kansai|juyo_06_\d{8})\.csv$"),
    accepted_headers=frozenset({HOURLY_HEADER_2016, HOURLY_HEADER_2019, HOURLY_HEADER_2025}),
    default_data_dir="data/kansai/power_usage",
    known_missing_days=frozenset({MISSING_DAY}),
)


class KansaiPowerUsageDownloader(AreaActualsDownloader):
    """Download the monthly Kansai でんき予報 archives and extract the daily members.

    Every call re-downloads the requested month: Kansai revises past days
    without notice (需要実績は、過去にさかのぼり修正させていただく場合があります)
    and the current month's zip grows daily; the whole history is ~8.5 MB.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/kansai/power_usage"``
        Root directory: ``zip/`` for the monthly archives, ``csv/`` for the
        extracted daily files.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to use; defaults to a fresh one.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(KANSAI_POWER_USAGE, data_dir=data_dir, timeout=timeout, session=session)


class KansaiPowerUsageCsvLoader(PowerUsageCsvLoader):
    """Full reload of the Kansai でんき予報 hourly tables into a warehouse table.

    The shared :class:`~power_market_analytics.ingestion.tso.power_usage.PowerUsageCsvLoader`
    bound to :data:`KANSAI_POWER_USAGE`: every file holds one date and all
    rows are kept (contract ``conf/schemas/kansai_power_usage_hourly.yaml``).

    Same constructor as :class:`~power_market_analytics.ingestion.loader.CsvLoader`
    (``schema``, ``filepath``, ``table``, optional ``spark``).
    """

    source = KANSAI_POWER_USAGE
