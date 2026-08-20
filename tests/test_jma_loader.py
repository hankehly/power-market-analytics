"""Tests for the positional JMA hourly loader (``power_market_analytics.jma_loader``).

Files are small but realistic cp932 CSVs shaped like docs/JMA-Weather-Data-Retrieval.md
§7 (download-timestamp line, blank line, four header rows, then data rows), named the
way ``scripts/download_jma_hourly.py`` names them, and loaded through the *real*
``conf/schemas/jma_hourly_staffed.yaml`` contract.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.jma_loader import JmaHourlyCsvLoader
from tests.support import REPO_ROOT

STAFFED_CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jma_hourly_staffed.yaml")

#: 27-column staffed layout: precip(4) temp(3) wind(5) sunshine(4) snow(4)
#: humidity(3) solar(3) after the timestamp. Verbatim from the 2026-08-20
#: spike response for 東京 (積雪の深さ carries 現象なし情報 at staffed
#: stations, unlike what doc §7.2 suggested).
STAFFED_HEADER = [
    "ダウンロードした時刻：2026/08/20 10:20:49",
    "",
    "," + ",".join(["東京"] * 26),
    "年月日時,降水量(mm),降水量(mm),降水量(mm),降水量(mm),気温(℃),気温(℃),気温(℃),"
    "風速(m/s),風速(m/s),風速(m/s),風速(m/s),風速(m/s),"
    "日照時間(時間),日照時間(時間),日照時間(時間),日照時間(時間),"
    "積雪(cm),積雪(cm),積雪(cm),積雪(cm),相対湿度(％),相対湿度(％),相対湿度(％),"
    "日射量(MJ/㎡),日射量(MJ/㎡),日射量(MJ/㎡)",
    ",,,,,,,,,,風向,風向,,,,,,,,,,,,,,,",
    ",,現象なし情報,品質情報,均質番号,,品質情報,均質番号,,品質情報,,品質情報,均質番号,"
    ",現象なし情報,品質情報,均質番号,,現象なし情報,品質情報,均質番号,,品質情報,均質番号,"
    ",品質情報,均質番号",
]
STAFFED_ROWS = [
    # Snow event in progress: 積雪 3 cm (現象なし=0), winter evening hour.
    "2024/2/5 19:00:00,3.0,0,8,1,0.6,8,1,3.9,8,北北西,8,1,0,1,8,1,3,0,8,1,98,8,1,0,8,1",
    # Summer daytime: snow untracked (blank value + blank 現象なし, quality 1),
    # solar 1.56 MJ/m2; note precip prints 0.0 here but bare 0 elsewhere.
    "2024/7/1 13:00:00,0.0,0,8,1,27.7,8,1,5.2,8,南南西,8,1,0.0,0,8,1,,,1,1,85,8,1,1.56,8,1",
    # Missing 気温 and 相対湿度: blank value cells with quality 1 (flags never blank).
    "2024/11/7 16:00:00,0,1,8,1,,1,1,4.8,8,北西,8,1,1.0,0,8,1,,,1,1,,1,1,0.61,8,1",
]

#: Pre-rescope 17-column core layout (no snow/humidity/solar) — kept only to
#: prove the loader's column-count guard still catches JMA layout drift.
OLD_CORE_HEADER = [
    "ダウンロードした時刻：2026/07/20 12:49:53",
    "",
    "," + ",".join(["東京"] * 16),
    "年月日時,降水量(mm),降水量(mm),降水量(mm),降水量(mm),気温(℃),気温(℃),気温(℃),"
    "風速(m/s),風速(m/s),風速(m/s),風速(m/s),風速(m/s),"
    "日照時間(時間),日照時間(時間),日照時間(時間),日照時間(時間)",
    ",,,,,,,,,,風向,風向,,,,,",
    ",,現象なし情報,品質情報,均質番号,,品質情報,均質番号,,品質情報,,品質情報,均質番号,"
    ",現象なし情報,品質情報,均質番号",
]
OLD_CORE_ROWS = [
    "2016/1/1 1:00:00,0,1,8,1,5.2,8,1,2.4,8,北西,8,1,0,1,8,1",
]


def write_cp932(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


def staffed_file(
    directory: Path, name: str = "s47662_101-201-301-401-501-605-610_2024.csv"
) -> Path:
    return write_cp932(directory / name, STAFFED_HEADER + STAFFED_ROWS)


class TestExpectedColumnCount:
    def test_real_contracts(self, spark, tmp_path):
        assert (
            JmaHourlyCsvLoader(
                STAFFED_CONTRACT, tmp_path, "t", spark=spark
            )._expected_column_count()
            == 27
        )

    def test_is_max_position_plus_one_ignoring_injected_sources(self, spark, tmp_path):
        contract = CsvTableSchema.model_validate(
            {
                "columns": [
                    {"name": "station_id", "source": "__station_id", "type": "string"},
                    {"name": "x", "source": "_c3", "type": "string"},
                    {"name": "y", "source": "_c1", "type": "string"},
                ]
            }
        )
        loader = JmaHourlyCsvLoader(contract, tmp_path, "t", spark=spark)
        assert loader._expected_column_count() == 4


class TestSniffColumnCount:
    def test_counts_first_data_row_not_header_rows(self, tmp_path):
        staffed = staffed_file(tmp_path)
        assert JmaHourlyCsvLoader._sniff_column_count(str(staffed)) == 27

    def test_headers_only_file_raises(self, tmp_path):
        f = write_cp932(tmp_path / "s47662_101-201-301-401-501-605-610_2024.csv", STAFFED_HEADER)
        with pytest.raises(ValueError, match="no data rows found"):
            JmaHourlyCsvLoader._sniff_column_count(str(f))


class TestJmaHourlyCsvLoaderLoad:
    def test_staffed_file_loads_through_the_contract(self, spark, tmp_path):
        write_cp932(
            tmp_path / "s47662_101-201-301-401-501-605-610_2024.csv",
            STAFFED_HEADER + STAFFED_ROWS,
        )
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.staffed", spark=spark)

        assert loader.load() == 3

        rows = {r.observed_at: r for r in spark.table("test_jma.staffed").collect()}
        r1 = rows[datetime.datetime(2024, 2, 5, 19, 0)]
        assert r1.station_id == "s47662"
        assert (r1.snow_depth_cm, r1.snow_depth_phenomenon_absent) == (3, 0)
        assert (r1.humidity_pct, r1.humidity_quality_flag) == (98, 8)
        assert (r1.solar_radiation_mjm2, r1.solar_radiation_quality_flag) == (0.0, 8)
        r2 = rows[datetime.datetime(2024, 7, 1, 13, 0)]
        # Snow untracked off-season: blank value AND blank 現象なし, quality 1.
        assert (r2.snow_depth_cm, r2.snow_depth_phenomenon_absent) == (None, None)
        assert r2.snow_depth_quality_flag == 1
        assert r2.solar_radiation_mjm2 == 1.56
        r3 = rows[datetime.datetime(2024, 11, 7, 16, 0)]
        assert (r3.temperature_c, r3.temperature_quality_flag) == (None, 1)
        assert (r3.humidity_pct, r3.humidity_quality_flag) == (None, 1)
        assert r3.solar_radiation_mjm2 == 0.61

    def test_two_stations_share_a_table_and_the_grain_holds(self, spark, tmp_path):
        staffed_file(tmp_path, "s47662_101-201-301-401-501-605-610_2024.csv")
        staffed_file(tmp_path, "s47772_101-201-301-401-501-605-610_2024.csv")
        loader = JmaHourlyCsvLoader(
            STAFFED_CONTRACT,
            tmp_path / "s*_101-201-301-401-501-605-610_*.csv",
            "test_jma.two",
            spark=spark,
        )

        assert loader.load() == 6

        rows = spark.table("test_jma.two").collect()
        assert sorted({r.station_id for r in rows}) == ["s47662", "s47772"]
        assert len({(r.station_id, r.observed_at) for r in rows}) == 6

    def test_overlapping_year_files_of_one_station_violate_the_grain(self, spark, tmp_path):
        staffed_file(tmp_path, "s47662_101-201-301-401-501-605-610_2024.csv")
        staffed_file(tmp_path, "s47662_101-201-301-401-501-605-610_2025.csv")  # same hours again
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.overlap", spark=spark)
        with pytest.raises(
            ValueError,
            match=re.escape("Grain ['station_id', 'observed_at'] is not unique: 6 rows but 3"),
        ):
            loader.load()

    def test_wrong_layout_fails_loudly(self, spark, tmp_path):
        write_cp932(tmp_path / "s47662_101-201-301-401_2016.csv", OLD_CORE_HEADER + OLD_CORE_ROWS)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.wrong", spark=spark)
        with pytest.raises(ValueError, match="first data row has 17 columns, contract expects 27"):
            loader.load()
        assert not spark.catalog.tableExists("test_jma.wrong")

    def test_unparseable_file_name_raises(self, spark, tmp_path):
        staffed_file(tmp_path, "fuchu_2024.csv")
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.name", spark=spark)
        with pytest.raises(ValueError, match="cannot parse a station id"):
            loader.load()

    def test_headers_only_file_raises(self, spark, tmp_path):
        write_cp932(tmp_path / "s47662_101-201-301-401-501-605-610_2024.csv", STAFFED_HEADER)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.empty", spark=spark)
        with pytest.raises(ValueError, match="no data rows found"):
            loader.load()

    def test_missing_non_nullable_flag_fails_the_load(self, spark, tmp_path):
        rows = list(STAFFED_ROWS)
        # precip flag blank (position _c3)
        rows[0] = "2024/2/5 19:00:00,3.0,0,,1,0.6,8,1,3.9,8,北北西,8,1,0,1,8,1,3,0,8,1,98,8,1,0,8,1"
        write_cp932(tmp_path / "s47662_101-201-301-401-501-605-610_2024.csv", STAFFED_HEADER + rows)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.flag", spark=spark)
        with pytest.raises(ValueError, match=re.escape("{'precipitation_quality_flag': 1}")):
            loader.load()
