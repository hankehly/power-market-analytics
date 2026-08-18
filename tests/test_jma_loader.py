"""Tests for the positional JMA hourly loader (``power_market_analytics.jma_loader``).

Files are small but realistic cp932 CSVs shaped like docs/JMA-Weather-Data-Retrieval.md
§7 (download-timestamp line, blank line, four header rows, then data rows), named the
way ``scripts/download_jma_hourly.py`` names them, and loaded through the *real*
``conf/schemas/jma_hourly_*.yaml`` contracts.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.jma_loader import JmaHourlyCsvLoader
from tests.support import REPO_ROOT

AMEDAS_CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jma_hourly_amedas.yaml")
STAFFED_CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jma_hourly_staffed.yaml")

#: 15-column AMeDAS layout (府中): precip(3) temp(3) wind(5) sunshine(3) after the timestamp.
AMEDAS_HEADER = [
    "ダウンロードした時刻：2026/07/20 12:49:53",
    "",
    "," + ",".join(["府中"] * 14),
    "年月日時,降水量(mm),降水量(mm),降水量(mm),気温(℃),気温(℃),気温(℃),"
    "風速(m/s),風速(m/s),風速(m/s),風速(m/s),風速(m/s),日照時間(時間),日照時間(時間),日照時間(時間)",
    ",,,,,,,,,風向,風向,,,,",
    ",,品質情報,均質番号,,品質情報,均質番号,,品質情報,,品質情報,均質番号,,品質情報,均質番号",
]
AMEDAS_ROWS = [
    "2016/1/1 1:00:00,0,8,1,5.2,8,1,2.4,8,北西,8,1,,8,1",
    "2016/1/1 2:00:00,0.5,8,1,4.9,8,1,3.1,8,静穏,8,1,0.2,8,1",
    # Missing precip/temp (flag 1), wind direction unobserved with an EMPTY flag cell.
    "2016/1/2 0:00:00,,1,1,,1,1,1.0,8,西,,1,,0,2",
]

#: 17-column staffed layout (東京): precip and sunshine gain a 現象なし情報 column.
STAFFED_HEADER = [
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
STAFFED_ROWS = [
    "2016/1/1 1:00:00,0,1,8,1,5.2,8,1,2.4,8,北西,8,1,0,1,8,1",
    "2016/1/1 2:00:00,1.5,0,8,1,5.0,8,1,2.0,8,北,8,1,0.1,0,8,1",
]


def write_cp932(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


def amedas_file(directory: Path, name: str = "a44132_101-201-301-401_2016.csv") -> Path:
    return write_cp932(directory / name, AMEDAS_HEADER + AMEDAS_ROWS)


class TestExpectedColumnCount:
    def test_real_contracts(self, spark, tmp_path):
        assert (
            JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "t", spark=spark)._expected_column_count()
            == 15
        )
        assert (
            JmaHourlyCsvLoader(
                STAFFED_CONTRACT, tmp_path, "t", spark=spark
            )._expected_column_count()
            == 17
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
        assert JmaHourlyCsvLoader._sniff_column_count(str(amedas_file(tmp_path))) == 15
        staffed = write_cp932(tmp_path / "s.csv", STAFFED_HEADER + STAFFED_ROWS)
        assert JmaHourlyCsvLoader._sniff_column_count(str(staffed)) == 17

    def test_headers_only_file_raises(self, tmp_path):
        f = write_cp932(tmp_path / "a44132_101-201-301-401_2016.csv", AMEDAS_HEADER)
        with pytest.raises(ValueError, match="no data rows found"):
            JmaHourlyCsvLoader._sniff_column_count(str(f))


class TestJmaHourlyCsvLoaderLoad:
    def test_amedas_file_loads_positionally_with_station_from_file_name(self, spark, tmp_path):
        amedas_file(tmp_path)
        loader = JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "test_jma.amedas", spark=spark)

        assert loader.load() == 3

        table = spark.table("test_jma.amedas")
        assert [(f.name, f.dataType.simpleString()) for f in table.schema][:5] == [
            ("station_id", "string"),
            ("observed_at", "timestamp"),
            ("precipitation_mm", "double"),
            ("precipitation_quality_flag", "int"),
            ("precipitation_homogeneity_no", "int"),
        ]
        rows = {r.observed_at: r for r in table.collect()}
        assert set(rows) == {
            datetime.datetime(2016, 1, 1, 1, 0),
            datetime.datetime(2016, 1, 1, 2, 0),
            datetime.datetime(2016, 1, 2, 0, 0),
        }
        r1 = rows[datetime.datetime(2016, 1, 1, 1, 0)]
        assert tuple(r1) == (
            "a44132",
            datetime.datetime(2016, 1, 1, 1, 0),
            0.0,
            8,
            1,
            5.2,
            8,
            1,
            2.4,
            8,
            "北西",
            8,
            1,
            None,  # AMeDAS: no-sunshine hour is an empty cell (flag 8)
            8,
            1,
        )
        r2 = rows[datetime.datetime(2016, 1, 1, 2, 0)]
        assert (r2.precipitation_mm, r2.wind_direction, r2.sunshine_duration_h) == (
            0.5,
            "静穏",
            0.2,
        )
        r3 = rows[datetime.datetime(2016, 1, 2, 0, 0)]
        assert (r3.precipitation_mm, r3.precipitation_quality_flag) == (None, 1)
        assert (r3.temperature_c, r3.temperature_quality_flag) == (None, 1)
        assert (r3.wind_direction, r3.wind_direction_quality_flag) == ("西", None)
        assert (r3.sunshine_duration_h, r3.sunshine_quality_flag, r3.sunshine_homogeneity_no) == (
            None,
            0,
            2,
        )

    def test_staffed_file_loads_through_the_staffed_contract(self, spark, tmp_path):
        write_cp932(tmp_path / "s47662_101-201-301-401_2016.csv", STAFFED_HEADER + STAFFED_ROWS)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.staffed", spark=spark)

        assert loader.load() == 2

        rows = {r.observed_at: r for r in spark.table("test_jma.staffed").collect()}
        r1 = rows[datetime.datetime(2016, 1, 1, 1, 0)]
        assert r1.station_id == "s47662"
        assert (r1.precipitation_mm, r1.precipitation_phenomenon_absent) == (0.0, 1)
        assert (r1.sunshine_duration_h, r1.sunshine_phenomenon_absent) == (0.0, 1)
        assert (r1.wind_speed_ms, r1.wind_direction, r1.wind_direction_quality_flag) == (
            2.4,
            "北西",
            8,
        )
        assert r1.sunshine_homogeneity_no == 1
        r2 = rows[datetime.datetime(2016, 1, 1, 2, 0)]
        assert (r2.precipitation_mm, r2.precipitation_phenomenon_absent) == (1.5, 0)
        assert (r2.sunshine_duration_h, r2.sunshine_phenomenon_absent) == (0.1, 0)

    def test_two_stations_share_a_table_and_the_grain_holds(self, spark, tmp_path):
        amedas_file(tmp_path, "a44132_101-201-301-401_2016.csv")
        amedas_file(tmp_path, "a44116_101-201-301-401_2016.csv")
        loader = JmaHourlyCsvLoader(
            AMEDAS_CONTRACT, tmp_path / "a*_101-201-301-401_*.csv", "test_jma.two", spark=spark
        )

        assert loader.load() == 6

        rows = spark.table("test_jma.two").collect()
        assert sorted({r.station_id for r in rows}) == ["a44116", "a44132"]
        assert len({(r.station_id, r.observed_at) for r in rows}) == 6

    def test_overlapping_year_files_of_one_station_violate_the_grain(self, spark, tmp_path):
        amedas_file(tmp_path, "a44132_101-201-301-401_2016.csv")
        amedas_file(tmp_path, "a44132_101-201-301-401_2017.csv")  # same hours again
        loader = JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "test_jma.overlap", spark=spark)
        with pytest.raises(
            ValueError,
            match=re.escape("Grain ['station_id', 'observed_at'] is not unique: 6 rows but 3"),
        ):
            loader.load()

    def test_staffed_layout_against_amedas_contract_fails_loudly(self, spark, tmp_path):
        write_cp932(tmp_path / "s47662_101-201-301-401_2016.csv", STAFFED_HEADER + STAFFED_ROWS)
        loader = JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "test_jma.wrong", spark=spark)
        with pytest.raises(ValueError, match="first data row has 17 columns, contract expects 15"):
            loader.load()
        assert not spark.catalog.tableExists("test_jma.wrong")

    def test_amedas_layout_against_staffed_contract_fails_loudly(self, spark, tmp_path):
        amedas_file(tmp_path)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.wrong2", spark=spark)
        with pytest.raises(ValueError, match="first data row has 15 columns, contract expects 17"):
            loader.load()

    def test_unparseable_file_name_raises(self, spark, tmp_path):
        amedas_file(tmp_path, "fuchu_2016.csv")
        loader = JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "test_jma.name", spark=spark)
        with pytest.raises(ValueError, match="cannot parse a station id"):
            loader.load()

    def test_headers_only_file_raises(self, spark, tmp_path):
        write_cp932(tmp_path / "a44132_101-201-301-401_2016.csv", AMEDAS_HEADER)
        loader = JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "test_jma.empty", spark=spark)
        with pytest.raises(ValueError, match="no data rows found"):
            loader.load()

    def test_missing_non_nullable_flag_fails_the_load(self, spark, tmp_path):
        rows = list(AMEDAS_ROWS)
        rows[0] = "2016/1/1 1:00:00,0,,1,5.2,8,1,2.4,8,北西,8,1,,8,1"  # precip flag blank
        write_cp932(tmp_path / "a44132_101-201-301-401_2016.csv", AMEDAS_HEADER + rows)
        loader = JmaHourlyCsvLoader(AMEDAS_CONTRACT, tmp_path, "test_jma.flag", spark=spark)
        with pytest.raises(ValueError, match=re.escape("{'precipitation_quality_flag': 1}")):
            loader.load()
