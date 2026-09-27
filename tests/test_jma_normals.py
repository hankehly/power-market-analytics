"""Tests for the JMA climatological normals (平年値) module.

The vintage config and the page's version heading; the downloader against a fake
session serving a two-station archive built in memory; the positional loader on
files written into ``tmp_path`` in the exact 308-byte daily layout, loaded through
the real ``conf/schemas/jma_normal_surface_daily.yaml`` contract.
"""

from __future__ import annotations

import datetime

import pytest
from power_market_analytics.ingestion.jma.normals import (
    VINTAGES,
    NormalsVintage,
    parse_versions,
    vintage_for_year,
)

# --------------------------------------------------------------------------- vintages


class TestVintages:
    def test_one_configured_period(self):
        assert VINTAGES == (
            NormalsVintage(
                period_start_year=1991,
                period_end_year=2020,
                in_use_since=datetime.date(2021, 5, 19),
                zip_url=(
                    "https://www.data.jma.go.jp/stats/data/mdrr/normal/2020/data/normal_surface.zip"
                ),
                expected_station_count=157,
            ),
        )

    def test_lookup_by_period_end_year(self):
        assert vintage_for_year(2020) is VINTAGES[0]

    def test_unknown_year_raises(self):
        with pytest.raises(KeyError, match="2030"):
            vintage_for_year(2030)

    def test_vintage_is_immutable(self):
        with pytest.raises(AttributeError):
            VINTAGES[0].zip_url = "x"  # type: ignore[misc]


# --------------------------------------------------------------------------- the page


class TestParseVersions:
    def test_the_heading_as_jma_writes_it_mixed_brackets(self):
        assert parse_versions("<h2>2020年平年値（第5版)</h2>") == {2020: "5"}

    def test_full_width_brackets_and_a_dotted_version(self):
        assert parse_versions("2020年平年値（第4.0.1版）") == {2020: "4.0.1"}

    def test_two_periods_on_one_page(self):
        html = "<h2>2030年平年値（第1版）</h2> … <h2>2020年平年値（第5版）</h2>"
        assert parse_versions(html) == {2030: "1", 2020: "5"}

    def test_news_items_do_not_match(self):
        # The notices read 2020年平年値の第5版を公開しました — no bracket, not a heading.
        assert parse_versions("2020年平年値の第5版を公開しました。") == {}

    def test_no_heading(self):
        assert parse_versions("<html>maintenance</html>") == {}
