"""Tests for the vintage-aware census population-mesh loader (``estat``).

Files are small CP932 extracts of the real ``tblT000847H5339.txt`` /
``tblT001101H5339.txt`` tables (two header rows, ``*``-suppressed detail
columns, HTKSAKI / GASSAN privacy metadata) laid out the way the downloader
stores them (``{year}/txt/``) and loaded through the *real*
``conf/schemas/estat_census_population_mesh.yaml`` contract.
"""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

import pytest

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.estat import (
    VINTAGES,
    CensusVintage,
    EstatCensusMeshCsvLoader,
    vintage_for_year,
)
from tests.support import REPO_ROOT

CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/estat_census_population_mesh.yaml")
V2015 = vintage_for_year(2015)
V2020 = vintage_for_year(2020)

#: 2015 (T000847): population is the 5th column, as published.
LINES_2015 = [
    "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847001,T000847002,T000847003",
    ",,,,　人口総数,　人口総数　男,　人口総数　女",
    "533900054,0,,,64,33,31",
    "533900064,2,533900073,,3,1,2",
    "533900073,1,,533900064,57,27,30",
    "533900341,1,,533900342;533900343,24,12,12",
    "533900342,2,533900341,,2,*,*",
]
#: 2020 (T001101): the population column deliberately moved to the LAST
#: position so the loader is proven to select it by name, not by position.
LINES_2020 = [
    "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T001101002,T001101003,T001101001",
    ",,,,　人口（総数）　男,　人口（総数）　女,　人口（総数）",
    "533900054,0,,,29,23,52",
    "533900064,2,533900073,,2,1,3",
    "533900073,1,,533900064,14,20,34",
]


def write_cp932(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


def write_vintage(root: Path, vintage: CensusVintage, code: str, lines: list[str]) -> Path:
    return write_cp932(root / str(vintage.census_year) / "txt" / vintage.member_name(code), lines)


def rows_of(spark, table: str) -> dict[tuple[int, str], dict]:
    return {(r["census_year"], r["mesh_code"]): r.asDict() for r in spark.table(table).collect()}


class TestContract:
    def test_grain_and_encoding(self):
        assert CONTRACT.grain == ["census_year", "mesh_code"]
        assert CONTRACT.read_options == {"encoding": "windows-31j"}

    def test_destination_columns(self):
        assert [(c.name, c.type, c.nullable) for c in CONTRACT.columns] == [
            ("census_year", "int", False),
            ("census_date", "date", False),
            ("geodetic_datum", "string", False),
            ("stats_id", "string", False),
            ("primary_mesh_code", "string", False),
            ("mesh_code", "string", False),
            ("privacy_processing_code", "int", False),
            ("aggregation_destination_mesh_code", "string", True),
            ("aggregation_source_mesh_codes", "string", True),
            ("population_total", "bigint", False),
            ("source_file", "string", False),
        ]

    def test_physical_source_columns_are_the_shared_privacy_headers(self):
        physical = [c.source_name for c in CONTRACT.columns if not c.source_name.startswith("__")]
        assert physical == ["KEY_CODE", "HTKSYORI", "HTKSAKI", "GASSAN"]


class TestLoad:
    @pytest.fixture
    def loaded(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_vintage(tmp_path, V2020, "5339", LINES_2020)
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.both", spark=spark)
        n_rows = loader.load()
        return n_rows, rows_of(spark, "test_estat_loader.both")

    def test_row_count_and_grain(self, loaded):
        n_rows, rows = loaded
        assert n_rows == 8
        assert sorted(rows) == [
            (2015, "533900054"),
            (2015, "533900064"),
            (2015, "533900073"),
            (2015, "533900341"),
            (2015, "533900342"),
            (2020, "533900054"),
            (2020, "533900064"),
            (2020, "533900073"),
        ]

    def test_population_column_is_selected_per_vintage(self, loaded):
        _, rows = loaded
        assert rows[(2015, "533900054")]["population_total"] == 64
        assert rows[(2020, "533900054")]["population_total"] == 52
        assert rows[(2020, "533900073")]["population_total"] == 34

    def test_vintage_attributes_are_injected(self, loaded):
        _, rows = loaded
        r = rows[(2015, "533900054")]
        assert (
            r["census_date"],
            r["geodetic_datum"],
            r["stats_id"],
            r["primary_mesh_code"],
            r["source_file"],
        ) == (datetime.date(2015, 10, 1), "JGD2000", "T000847", "5339", "tblT000847H5339.txt")
        r = rows[(2020, "533900054")]
        assert (r["census_date"], r["stats_id"], r["source_file"]) == (
            datetime.date(2020, 10, 1),
            "T001101",
            "tblT001101H5339.txt",
        )

    def test_privacy_metadata_is_kept_verbatim(self, loaded):
        _, rows = loaded
        pick = lambda y, m: (  # noqa: E731
            rows[(y, m)]["privacy_processing_code"],
            rows[(y, m)]["aggregation_destination_mesh_code"],
            rows[(y, m)]["aggregation_source_mesh_codes"],
        )
        assert pick(2015, "533900054") == (0, None, None)
        assert pick(2015, "533900064") == (2, "533900073", None)
        assert pick(2015, "533900073") == (1, None, "533900064")
        # Semicolon-delimited GASSAN is retained as the source string.
        assert pick(2015, "533900341") == (1, None, "533900342;533900343")

    def test_population_is_kept_for_privacy_processed_meshes(self, loaded):
        _, rows = loaded
        # HTKSYORI 2 (aggregated away) still reports its own population; the
        # detail columns are '*' but total population never is.
        assert rows[(2015, "533900064")]["population_total"] == 3
        assert rows[(2015, "533900342")]["population_total"] == 2
        assert rows[(2015, "533900073")]["population_total"] == 57

    def test_table_types_follow_the_contract(self, spark, loaded):
        schema = {
            f.name: f.dataType.simpleString() for f in spark.table("test_estat_loader.both").schema
        }
        assert schema == {
            "census_year": "int",
            "census_date": "date",
            "geodetic_datum": "string",
            "stats_id": "string",
            "primary_mesh_code": "string",
            "mesh_code": "string",
            "privacy_processing_code": "int",
            "aggregation_destination_mesh_code": "string",
            "aggregation_source_mesh_codes": "string",
            "population_total": "bigint",
            "source_file": "string",
        }


class TestFileResolution:
    def test_directory_root_finds_every_vintage_txt(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_vintage(tmp_path, V2020, "5339", LINES_2020)
        (tmp_path / "2015" / "zip").mkdir()
        (tmp_path / "2015" / "zip" / "tblT000847H5339.zip").write_bytes(b"not a txt")
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "t", spark=spark)
        assert loader._resolve_files() == [
            str(tmp_path / "2015/txt/tblT000847H5339.txt"),
            str(tmp_path / "2020/txt/tblT001101H5339.txt"),
        ]

    def test_glob_pattern_selects_one_vintage(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_vintage(tmp_path, V2020, "5339", LINES_2020)
        loader = EstatCensusMeshCsvLoader(
            CONTRACT, tmp_path / "2015/txt/*.txt", "test_estat_loader.only2015", spark=spark
        )
        assert loader.load() == 5
        assert {r["census_year"] for r in spark.table("test_estat_loader.only2015").collect()} == {
            2015
        }

    def test_single_file_path(self, spark, tmp_path):
        path = write_vintage(tmp_path, V2020, "5339", LINES_2020)
        loader = EstatCensusMeshCsvLoader(CONTRACT, path, "t", spark=spark)
        assert loader._resolve_files() == [str(path)]

    def test_no_files_raises(self, spark, tmp_path):
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "t", spark=spark)
        with pytest.raises(FileNotFoundError, match="No census mesh text files"):
            loader.load()


class TestVintageConfiguration:
    def test_defaults_to_the_configured_vintages(self, spark, tmp_path):
        assert EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "t", spark=spark).vintages == VINTAGES

    def test_custom_vintages_drive_the_population_column(self, spark, tmp_path):
        demo = dataclasses.replace(
            V2015, census_year=1999, stats_id="T000001", population_source_column="T000001009"
        )
        write_vintage(
            tmp_path,
            demo,
            "5339",
            [
                "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000001001,T000001009",
                ",,,,　人口総数,　something",
                "533900054,0,,,1,999",
            ],
        )
        loader = EstatCensusMeshCsvLoader(
            CONTRACT, tmp_path, "test_estat_loader.demo", spark=spark, vintages=(demo,)
        )
        loader.load()
        rows = rows_of(spark, "test_estat_loader.demo")
        assert rows[(1999, "533900054")]["population_total"] == 999
        assert rows[(1999, "533900054")]["stats_id"] == "T000001"

    def test_unknown_stats_id_in_file_name_fails(self, spark, tmp_path):
        path = write_cp932(tmp_path / "2015/txt/tblT999999H5339.txt", LINES_2015)
        loader = EstatCensusMeshCsvLoader(CONTRACT, path, "t", spark=spark)
        with pytest.raises(ValueError, match="T999999"):
            loader.load()

    def test_unparseable_file_name_fails(self, spark, tmp_path):
        path = write_cp932(tmp_path / "2015/txt/population.txt", LINES_2015)
        loader = EstatCensusMeshCsvLoader(CONTRACT, path, "t", spark=spark)
        with pytest.raises(ValueError, match="cannot parse"):
            loader.load()


def _with_row(lines: list[str], row: str) -> list[str]:
    return lines + [row]


class TestValidationFailsBeforeWriting:
    @pytest.mark.parametrize(
        "lines, message",
        [
            # population: '*', negative, non-integer, empty
            (_with_row(LINES_2015, "533900999,0,,,*,1,1"), r"population.*\*"),
            (_with_row(LINES_2015, "533900999,0,,,-1,1,1"), "population.*-1"),
            (_with_row(LINES_2015, "533900999,0,,,12.5,1,1"), r"population.*12\.5"),
            (_with_row(LINES_2015, "533900999,0,,,,1,1"), "population"),
            # mesh code malformed: 8 digits, quadrant 5, second-level column 8
            (_with_row(LINES_2015, "53390099,0,,,1,1,1"), "mesh code.*53390099"),
            (_with_row(LINES_2015, "533900995,0,,,1,1,1"), "mesh code.*533900995"),
            (_with_row(LINES_2015, "533980991,0,,,1,1,1"), "mesh code.*533980991"),
            # mesh code from another primary mesh
            (_with_row(LINES_2015, "533800054,0,,,1,1,1"), "primary mesh 5339.*533800054"),
            # privacy code outside 0/1/2
            (_with_row(LINES_2015, "533900999,3,,,1,1,1"), "HTKSYORI.*3"),
            (_with_row(LINES_2015, "533900999,*,,,1,1,1"), r"HTKSYORI.*\*"),
            # a data row with an empty KEY_CODE would be silently dropped otherwise
            (_with_row(LINES_2015, ",0,,,1,1,1"), "label row"),
        ],
    )
    def test_bad_rows(self, spark, tmp_path, lines, message):
        write_vintage(tmp_path, V2015, "5339", lines)
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.bad", spark=spark)
        with pytest.raises(ValueError, match=message) as excinfo:
            loader.load()
        assert "tblT000847H5339.txt" in str(excinfo.value)
        assert not spark.catalog.tableExists("test_estat_loader.bad")

    @pytest.mark.parametrize(
        "lines, message",
        [
            # KEY_CODE header renamed
            (
                ["MESH,HTKSYORI,HTKSAKI,GASSAN,T000847001"] + LINES_2015[1:],
                "missing required columns.*KEY_CODE",
            ),
            # GASSAN header absent
            (
                ["KEY_CODE,HTKSYORI,HTKSAKI,T000847001", ",,,　人口総数", "533900054,0,,64"],
                "missing required columns.*GASSAN",
            ),
            # configured population column absent (a 2020-shaped header in a 2015 file)
            (
                [
                    "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T001101001",
                    ",,,,　人口（総数）",
                    "533900054,0,,,52",
                ],
                "population column T000847001",
            ),
            # second line is data, not the label row
            ([LINES_2015[0]] + LINES_2015[2:], "label row"),
            # a file with only the header rows
            (LINES_2015[:2], "no data rows"),
        ],
    )
    def test_bad_headers(self, spark, tmp_path, lines, message):
        write_vintage(tmp_path, V2015, "5339", lines)
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.bad", spark=spark)
        with pytest.raises(ValueError, match=message) as excinfo:
            loader.load()
        assert "tblT000847H5339.txt" in str(excinfo.value)

    def test_duplicate_mesh_within_a_vintage_fails_the_grain(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", _with_row(LINES_2015, "533900054,0,,,64,33,31"))
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.dup", spark=spark)
        with pytest.raises(ValueError, match="not unique"):
            loader.load()
        assert not spark.catalog.tableExists("test_estat_loader.dup")

    def test_same_mesh_in_two_vintages_is_fine(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015[:3])
        write_vintage(tmp_path, V2020, "5339", LINES_2020[:3])
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.two", spark=spark)
        assert loader.load() == 2


class TestSingleScan:
    def test_files_of_one_vintage_with_reordered_headers_load_correctly(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        # Same vintage, population column moved to the end: must not be read
        # through 5339's header by position.
        reordered = [
            "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847002,T000847003,T000847001",
            ",,,,　人口総数　男,　人口総数　女,　人口総数",
            "534000054,0,,,10,20,30",
        ]
        write_vintage(tmp_path, V2015, "5340", reordered)
        loader = EstatCensusMeshCsvLoader(
            CONTRACT, tmp_path, "test_estat_loader.reorder", spark=spark
        )

        assert loader.load() == 6

        rows = rows_of(spark, "test_estat_loader.reorder")
        assert rows[(2015, "534000054")]["population_total"] == 30
        assert rows[(2015, "534000054")]["primary_mesh_code"] == "5340"
        assert rows[(2015, "533900054")]["population_total"] == 64
        assert rows[(2015, "533900054")]["primary_mesh_code"] == "5339"

    def test_two_files_with_the_same_name_are_rejected(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_cp932(tmp_path / "1999" / "txt" / V2015.member_name("5339"), LINES_2015)
        loader = EstatCensusMeshCsvLoader(
            CONTRACT, tmp_path, "test_estat_loader.dupnames", spark=spark
        )

        with pytest.raises(ValueError, match="share the file name tblT000847H5339.txt"):
            loader.load()
        assert not spark.catalog.tableExists("test_estat_loader.dupnames")

    def test_row_check_failure_names_only_the_bad_file(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_vintage(
            tmp_path,
            V2015,
            "5340",
            ["KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847001", ",,,,　人口総数", "534000054,3,,,1"],
        )
        loader = EstatCensusMeshCsvLoader(
            CONTRACT, tmp_path, "test_estat_loader.onebad", spark=spark
        )

        with pytest.raises(
            ValueError, match=r"tblT000847H5340\.txt: 1 row\(s\) with HTKSYORI"
        ) as excinfo:
            loader.load()
        assert "tblT000847H5339.txt" not in str(excinfo.value)

    def test_loader_has_no_per_file_read(self):
        assert "_read_file" not in EstatCensusMeshCsvLoader.__dict__
