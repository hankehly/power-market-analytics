"""TEPCO エリア需要・発電情報 spec and its thin downloader/loader subclasses."""

from __future__ import annotations

from pathlib import Path

from power_market_analytics.area_actuals import AreaActualsCsvLoader, AreaActualsDownloader
from power_market_analytics.tepco import TEPCO, TepcoAreaCsvLoader, TepcoAreaDownloader


class TestTepcoSource:
    def test_zip_url(self):
        assert (
            TEPCO.zip_url(2025, 7)
            == "https://www4.tepco.co.jp/forecast/html/images/AREA_202507.zip"
        )

    def test_zip_name(self):
        assert TEPCO.zip_name(2022, 4) == "AREA_202204.zip"

    def test_earliest_month(self):
        assert TEPCO.earliest_month == (2022, 4)

    def test_only_daily_actuals_members(self):
        assert TEPCO.is_actuals_member("AREA_JISEKI_20250701.csv")
        assert TEPCO.is_actuals_member("AREA_202403/AREA_JISEKI_20240301.csv")
        assert not TEPCO.is_actuals_member("AREA_YOSOKU_20250701.csv")
        assert not TEPCO.is_actuals_member("AREA_BGKEI_20250701.csv")

    def test_archive_holds_finalized_days_only(self):
        assert TEPCO.archive_includes_current_day is False

    def test_accepted_header_is_the_published_layout(self):
        assert TEPCO.accepted_headers == frozenset(
            {
                "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量"
            }
        )


class TestTepcoSubclasses:
    def test_downloader_defaults_to_tepco_source_and_dir(self):
        dl = TepcoAreaDownloader()
        assert isinstance(dl, AreaActualsDownloader)
        assert dl.source is TEPCO
        assert dl.data_dir == Path("data/tepco/area_demand_generation")

    def test_downloader_accepts_data_dir(self, tmp_path):
        assert TepcoAreaDownloader(data_dir=tmp_path).zip_dir == tmp_path / "zip"

    def test_loader_is_bound_to_tepco_source(self):
        assert issubclass(TepcoAreaCsvLoader, AreaActualsCsvLoader)
        assert TepcoAreaCsvLoader.source is TEPCO
