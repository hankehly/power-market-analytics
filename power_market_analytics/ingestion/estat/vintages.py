"""The censuses this pipeline ingests, and the URLs each one is served at.

Adding a census is an entry in :data:`VINTAGES` plus its fixtures and the year
list of the singular dbt test.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

BASE_URL = "https://www.e-stat.go.jp"
#: JSON endpoint behind the listing page (rows + pager as HTML fragments).
LISTING_DETAIL_PATH = "/gis/statmap-search/search_detail"
#: One zip archive per (statsId, primary mesh code).
DOWNLOAD_PATH = "/gis/statmap-search/data"


@dataclass(frozen=True)
class CensusVintage:
    """One census on the 500 m mesh as published by e-Stat.

    Attributes
    ----------
    census_year : int
        Census year, e.g. ``2015``.
    census_date : datetime.date
        Reference date of the census (October 1 of ``census_year``).
    geodetic_datum : str
        Datum of the mesh product, e.g. ``"JGD2000"`` (e-Stat also publishes
        JGD2011 duplicates for recent censuses; one datum is used throughout
        so mesh geography is consistent across vintages).
    stats_id : str
        e-Stat statistics table id (``statsId``), e.g. ``"T000847"``.
    population_source_column : str
        Header of the total-population column in the text files, e.g.
        ``"T000847001"``.
    listing_url : str
        First page of the filtered 第１次地域区画 listing on e-Stat (the
        human-facing URL; :meth:`listing_detail_url` derives the JSON
        endpoint from its query string).
    expected_file_count : int
        Number of primary-mesh downloads the listing must contain.
    """

    census_year: int
    census_date: datetime.date
    geodetic_datum: str
    stats_id: str
    population_source_column: str
    listing_url: str
    expected_file_count: int

    def listing_detail_url(self, page: int) -> str:
        """Return the ``search_detail`` JSON URL for one listing page.

        Parameters
        ----------
        page : int
            1-based listing page.

        Returns
        -------
        str
            ``listing_url`` with the path swapped for the JSON endpoint,
            ``page`` replaced and the two flags the portal's JavaScript adds.
        """
        parts = urlsplit(self.listing_url)
        query = dict(parse_qsl(parts.query))
        query["page"] = str(page)
        query["mesh_data_flg"] = "1"
        query["download_disp_flg"] = "1"
        return urlunsplit((parts.scheme, parts.netloc, LISTING_DETAIL_PATH, urlencode(query), ""))

    def download_url(self, primary_mesh_code: str) -> str:
        """Return the zip download URL of one primary mesh."""
        return (
            f"{BASE_URL}{DOWNLOAD_PATH}"
            f"?statsId={self.stats_id}&code={primary_mesh_code}&downloadType=2"
        )

    def zip_name(self, primary_mesh_code: str) -> str:
        """Return the local archive name (as served in Content-Disposition)."""
        return f"tbl{self.stats_id}H{primary_mesh_code}.zip"

    def member_name(self, primary_mesh_code: str) -> str:
        """Return the single text member every archive must contain."""
        return f"tbl{self.stats_id}H{primary_mesh_code}.txt"


def _listing_url(year: int, stats_id: str) -> str:
    return (
        f"{BASE_URL}/gis/statmap-search?page=1&type=1&toukeiCode=00200521"
        f"&toukeiYear={year}&aggregateUnit=H&serveyId=H00200511{year}&statsId={stats_id}"
    )


#: Configured censuses, oldest first. Add an entry (plus fixtures) for a new census.
VINTAGES: tuple[CensusVintage, ...] = (
    CensusVintage(
        census_year=2015,
        census_date=datetime.date(2015, 10, 1),
        geodetic_datum="JGD2000",
        stats_id="T000847",
        population_source_column="T000847001",
        listing_url=_listing_url(2015, "T000847"),
        expected_file_count=151,
    ),
    CensusVintage(
        census_year=2020,
        census_date=datetime.date(2020, 10, 1),
        geodetic_datum="JGD2000",
        stats_id="T001101",
        population_source_column="T001101001",
        listing_url=_listing_url(2020, "T001101"),
        expected_file_count=151,
    ),
)


def vintage_for_year(census_year: int) -> CensusVintage:
    """Return the configured vintage of a census year.

    Raises
    ------
    KeyError
        If no vintage is configured for ``census_year``.
    """
    for vintage in VINTAGES:
        if vintage.census_year == census_year:
            return vintage
    raise KeyError(f"no census vintage configured for {census_year}")


def vintage_for_stats_id(stats_id: str) -> CensusVintage:
    """Return the configured vintage of an e-Stat statistics table id.

    Raises
    ------
    KeyError
        If no vintage is configured for ``stats_id``.
    """
    for vintage in VINTAGES:
        if vintage.stats_id == stats_id:
            return vintage
    raise KeyError(f"no census vintage configured for statsId {stats_id}")
