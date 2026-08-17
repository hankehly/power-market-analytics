"""TEPCO エリア需要・発電情報 (Tokyo-area demand & generation) archive spec.

TEPCO Power Grid publishes 30-minute Tokyo-area total demand, total
generation and wind+solar generation on
https://www.tepco.co.jp/forecast/html/area-download-j.html. The history is
served as one zip per month
(``https://www4.tepco.co.jp/forecast/html/images/AREA_YYYYMM.zip``, 2022-04
onward, ~100 KB each) holding three CP932 CSVs per day: 実績 actuals
(``AREA_JISEKI_YYYYMMDD.csv``), 予測 forecasts (``AREA_YOSOKU_``) and
BG計画総計 balancing-group plans (``AREA_BGKEI_``). Only the actuals files are
extracted. The archive layout, CSV format and data quirks are documented in
docs/TEPCO-Area-Demand-Generation-Retrieval.md.

The download/extract logic itself is the shared
:class:`~power_market_analytics.area_actuals.AreaActualsDownloader`; this
module only supplies the TEPCO :class:`~power_market_analytics.area_actuals.AreaActualsSource`
and a convenience subclass bound to it.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

from power_market_analytics.area_actuals import (
    AreaActualsDownloader,
    AreaActualsDownloadError,
    AreaActualsSource,
    month_range,
)

__all__ = [
    "TEPCO",
    "TepcoAreaDownloader",
    "TepcoDownloadError",
    "month_range",
]

#: Backwards-compatible alias; the shared downloader raises this type.
TepcoDownloadError = AreaActualsDownloadError

TEPCO = AreaActualsSource(
    code="tepco",
    url_template="https://www4.tepco.co.jp/forecast/html/images/AREA_{year:04d}{month:02d}.zip",
    #: First month published on the download page.
    earliest_month=(2022, 4),
    #: Daily actuals members; AREA_YOSOKU_* / AREA_BGKEI_* are skipped. Members
    #: are flat in every archive except AREA_202403.zip, which nests them under
    #: AREA_202403/ (the downloader flattens on extraction).
    member_re=re.compile(r"AREA_JISEKI_\d{8}\.csv$"),
    #: Exact column-header line (line 3) of every actuals file since 2022-04.
    #: Note the full-width underscores in 時間帯＿自 / 時間帯＿至.
    accepted_headers=frozenset(
        {
            "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量"
        }
    ),
    default_data_dir="data/tepco/area_demand_generation",
)


class TepcoAreaDownloader(AreaActualsDownloader):
    """Download the monthly TEPCO area archives and extract the daily actuals CSVs.

    Every call re-downloads the requested month: TEPCO occasionally revises
    past days (e.g. 2022-12-01/02 were re-issued on 2022-12-14, 2024-03-11 on
    2024-04-19) and the current month's zip grows daily, and the whole history
    is only ~5 MB, so no caching is attempted.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/tepco/area_demand_generation"``
        Root directory. Zips are kept under ``zip/`` and the extracted
        ``AREA_JISEKI_YYYYMMDD.csv`` files under ``csv/``.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        HTTP session to use; defaults to a fresh one.

    Examples
    --------
    >>> downloader = TepcoAreaDownloader()
    >>> downloader.download(2025, 7)[:2]
    [PosixPath('data/tepco/area_demand_generation/csv/AREA_JISEKI_20250701.csv'),
     PosixPath('data/tepco/area_demand_generation/csv/AREA_JISEKI_20250702.csv')]
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(TEPCO, data_dir=data_dir, timeout=timeout, session=session)
