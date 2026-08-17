"""関西電力送配電 エリア需給・発電（実績） spec and its downloader/loader subclasses."""

from __future__ import annotations

from pathlib import Path

from power_market_analytics.area_actuals import AreaActualsCsvLoader, AreaActualsDownloader
from power_market_analytics.kansai import KANSAI, KansaiAreaDownloader
from power_market_analytics.kansai_loader import KansaiAreaCsvLoader


class TestKansaiSource:
    def test_zip_url(self):
        assert (
            KANSAI.zip_url(2025, 7)
            == "https://www.kansai-td.co.jp/interchange/denkiyoho/imbalance/202507_jisseki.zip"
        )

    def test_zip_name(self):
        assert KANSAI.zip_name(2022, 4) == "202204_jisseki.zip"

    def test_earliest_month_is_the_imbalance_regime_start(self):
        assert KANSAI.earliest_month == (2022, 4)

    def test_matches_both_member_naming_generations(self):
        assert KANSAI.is_actuals_member("20250701_jisseki.csv")  # until 2025-11
        assert KANSAI.is_actuals_member("jukyu_jisseki_20251225_06.csv")  # from 2025-12
        assert not KANSAI.is_actuals_member("20250701_yosoku.csv")
        assert not KANSAI.is_actuals_member("20250701_bgkeikaku.csv")
        assert not KANSAI.is_actuals_member("jukyu_yosoku_20251225_06.csv")

    def test_archive_carries_the_running_day(self):
        # The current month's zip includes today's file, refreshed intraday with
        # blank cells for future periods; the loader must skip it.
        assert KANSAI.archive_includes_current_day is True

    def test_accepts_both_header_layouts(self):
        assert KANSAI.accepted_headers == frozenset(
            {
                # until 2025-12-24 (files also carry a leading title line)
                "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光",
                # from 2025-12-25
                "DATE,時間コマ,時間帯_自,時間帯_至,エリア総需要量(kWh),エリア総発電量(kWh),エリア風力・太陽光発電量(kWh)",
            }
        )


class TestKansaiSubclasses:
    def test_downloader_defaults_to_kansai_source_and_dir(self):
        dl = KansaiAreaDownloader()
        assert isinstance(dl, AreaActualsDownloader)
        assert dl.source is KANSAI
        assert dl.data_dir == Path("data/kansai/area_demand_generation")

    def test_downloader_accepts_data_dir(self, tmp_path):
        assert KansaiAreaDownloader(data_dir=tmp_path).csv_dir == tmp_path / "csv"

    def test_loader_is_bound_to_kansai_source(self):
        assert issubclass(KansaiAreaCsvLoader, AreaActualsCsvLoader)
        assert KansaiAreaCsvLoader.source is KANSAI
