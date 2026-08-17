"""関西電力送配電 エリア需給・発電（実績） (Kansai-area demand & generation) archive spec.

Kansai Transmission and Distribution publishes the same インバランス料金関連
「系統の需給に関する情報」 series as TEPCO — 30-minute area total demand, total
generation and wind+solar generation in 30分kWh — on
https://www.kansai-td.co.jp/denkiyoho/imbalance/ (past months via
``past.html``). The history is one zip per month,
``https://www.kansai-td.co.jp/interchange/denkiyoho/imbalance/YYYYMM_jisseki.zip``
(~35 KB), holding one CP932 CSV per day. Members were named
``YYYYMMDD_jisseki.csv`` until 2025-11 and ``jukyu_jisseki_YYYYMMDD_06.csv``
(``06`` = the Kansai area code) from 2025-12; the CSV layout also changed on
2025-12-25 (see :data:`KANSAI.accepted_headers`). Real data starts 2022-03-16
(the 2022-01/02 archives are test stubs); the download starts at 2022-04, the
first full month and the start of the new imbalance regime, matching TEPCO.
Format and quirks: docs/Kansai-Area-Demand-Generation-Retrieval.md.

The download/extract logic itself is the shared
:class:`~power_market_analytics.area_actuals.AreaActualsDownloader`; this
module only supplies the Kansai :class:`~power_market_analytics.area_actuals.AreaActualsSource`
and a convenience subclass bound to it.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

from power_market_analytics.area_actuals import AreaActualsDownloader, AreaActualsSource

__all__ = ["KANSAI", "KansaiAreaDownloader"]

KANSAI = AreaActualsSource(
    code="kansai",
    url_template=(
        "https://www.kansai-td.co.jp/interchange/denkiyoho/imbalance/{year:04d}{month:02d}_jisseki.zip"
    ),
    earliest_month=(2022, 4),
    #: Daily actuals members in either naming generation; the sibling
    #: *_yosoku / *_bgkeikaku archives are separate zips and never match.
    member_re=re.compile(r"(^|/)(\d{8}_jisseki|jukyu_jisseki_\d{8}_06)\.csv$"),
    accepted_headers=frozenset(
        {
            # 2022-03-16 .. 2025-12-24: a title line 「実績値（Ａ－１・Ｂ－１・Ｂ－４）」
            # precedes the two metadata lines; dates yyyymmdd; full-width ＿.
            "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光",
            # 2025-12-25 onward: no title line; dates yyyy/mm/dd; half-width _;
            # unit suffixes on the measures.
            "DATE,時間コマ,時間帯_自,時間帯_至,エリア総需要量(kWh),エリア総発電量(kWh),エリア風力・太陽光発電量(kWh)",
        }
    ),
    default_data_dir="data/kansai/area_demand_generation",
    #: The current month's zip also carries today's file, refreshed intraday
    #: with blank cells for periods not yet observed.
    archive_includes_current_day=True,
)


class KansaiAreaDownloader(AreaActualsDownloader):
    """Download the monthly Kansai archives and extract the daily actuals CSVs.

    Every call re-downloads the requested month (Kansai re-issued the April
    2022 files in 2023-09, and the current month's zip grows daily).

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/kansai/area_demand_generation"``
        Root directory. Zips are kept under ``zip/`` and the extracted daily
        CSVs under ``csv/``.
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
        super().__init__(KANSAI, data_dir=data_dir, timeout=timeout, session=session)
