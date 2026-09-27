"""JMA climatological normals (平年値): the vintage config, the downloader and the raw loader.

JMA publishes the normals of its staffed stations as one archive,
``normal_surface.zip``, on the 平年値ダウンロード page. Each station's daily file
holds 81 elements × 12 months, one row each, with 31 (value, flag) cells per row
and no header. The page, the archive, the record layout, the element codes, the
versions and the warehouse models are documented in
``docs/JMA-Climatological-Normals-Retrieval.md``.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

#: The 平年値ダウンロード page; its ``<year>年平年値（第<n>版）`` heading names the version.
NORMALS_PAGE_URL = "https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html"

#: The station list inside the archive, extracted for reference and never loaded.
STATION_INDEX_MEMBER = "normal_surface/surface_station_index.csv"

#: One daily file per station inside the archive.
DAILY_MEMBER_RE = re.compile(r"^normal_surface/daily/nml_sfc_d_(?P<station>\d{5})\.csv$")

#: ``2020年平年値（第5版)`` — JMA mixes full-width and ASCII brackets. The notices
#: (``2020年平年値の第5版を公開しました``) have no bracket and do not match.
_HEADING_RE = re.compile(r"(?P<year>\d{4})年平年値[（(]第(?P<version>\d+(?:\.\d+)*)版[)）]")


@dataclass(frozen=True)
class NormalsVintage:
    """One normals period as JMA publishes it.

    Attributes
    ----------
    period_start_year, period_end_year : int
        The 30 years the normals average, e.g. 1991 and 2020.
    in_use_since : datetime.date
        The day JMA put the period's normals into use (2021-05-19 for
        1991–2020); the ``available_at`` of every row of the period.
    zip_url : str
        The surface-observation archive of the period.
    expected_station_count : int
        Daily files the archive must hold; another count fails the download.
    """

    period_start_year: int
    period_end_year: int
    in_use_since: datetime.date
    zip_url: str
    expected_station_count: int


#: Configured periods, oldest first. The 2030 normals become a second entry.
VINTAGES: tuple[NormalsVintage, ...] = (
    NormalsVintage(
        period_start_year=1991,
        period_end_year=2020,
        in_use_since=datetime.date(2021, 5, 19),
        zip_url="https://www.data.jma.go.jp/stats/data/mdrr/normal/2020/data/normal_surface.zip",
        expected_station_count=157,
    ),
)


def vintage_for_year(period_end_year: int) -> NormalsVintage:
    """Return the configured vintage whose period ends in ``period_end_year``.

    Parameters
    ----------
    period_end_year : int
        The last year of the period, e.g. ``2020``.

    Returns
    -------
    NormalsVintage

    Raises
    ------
    KeyError
        If no vintage is configured for the year.
    """
    for vintage in VINTAGES:
        if vintage.period_end_year == period_end_year:
            return vintage
    raise KeyError(f"no normals vintage configured for the period ending {period_end_year}")


def parse_versions(html: str) -> dict[int, str]:
    """Every ``<year>年平年値（第<version>版）`` heading of the page, by period end year.

    Parameters
    ----------
    html : str
        The decoded page.

    Returns
    -------
    dict of int to str
        Period end year → version as written (``"5"``, ``"4.0.1"``); the last
        heading wins should a year repeat.
    """
    return {int(m["year"]): m["version"] for m in _HEADING_RE.finditer(html)}
