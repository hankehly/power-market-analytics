"""Tests for the generic header-based CSV loader (``power_market_analytics.csv_loader``).

All loads run against the shared local Spark fixture and real CSV files in
``tmp_path``; tables land in the ``test_csv_loader`` database of the temp
warehouse.
"""

from __future__ import annotations

import bz2
import datetime
import gzip
import re
import zlib
from pathlib import Path

import pytest
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from power_market_analytics.csv_loader import (
    REPORT_LIMIT,
    SOURCE_FILE_COL,
    CsvColumn,
    CsvLoader,
    CsvTableSchema,
    python_codec,
)
from tests.support import REPO_ROOT

# --------------------------------------------------------------------------- schema objects


class TestCsvColumn:
    def test_source_name_defaults_to_name(self):
        assert CsvColumn(name="trade_date", type="date").source_name == "trade_date"

    def test_source_name_prefers_explicit_source(self):
        col = CsvColumn(name="trade_date", type="date", source="年月日")
        assert col.source_name == "年月日"

    def test_defaults_are_nullable_and_required(self):
        col = CsvColumn(name="x", type="int")
        assert (col.nullable, col.required, col.format, col.source) == (True, True, None, None)


class TestCsvTableSchema:
    def test_from_yaml_parses_every_section(self, tmp_path):
        path = tmp_path / "s.yaml"
        path.write_text(
            "description: demo\n"
            "read_options: {encoding: windows-31j, sep: ';'}\n"
            "grain: [a, b]\n"
            "columns:\n"
            "  - {name: a, source: 'A(x)', type: int, nullable: false}\n"
            "  - {name: b, type: date, format: yyyy/MM/dd}\n"
            "  - {name: c, type: string, required: false}\n",
            encoding="utf-8",
        )
        schema = CsvTableSchema.from_yaml(path)
        assert schema.description == "demo"
        assert schema.read_options == {"encoding": "windows-31j", "sep": ";"}
        assert schema.grain == ["a", "b"]
        assert [
            (c.name, c.source_name, c.type, c.format, c.nullable, c.required)
            for c in schema.columns
        ] == [
            ("a", "A(x)", "int", None, False, True),
            ("b", "b", "date", "yyyy/MM/dd", True, True),
            ("c", "c", "string", None, True, False),
        ]

    def test_from_yaml_accepts_str_path_and_defaults(self, tmp_path):
        path = tmp_path / "min.yaml"
        path.write_text("columns:\n  - {name: k, type: int}\n", encoding="utf-8")
        schema = CsvTableSchema.from_yaml(str(path))
        assert (schema.description, schema.read_options, schema.grain) == (None, {}, [])

    @pytest.mark.parametrize("name", ["_source_file", "_SOURCE_FILE", "_Source_File"])
    def test_the_file_name_column_name_is_reserved_in_any_case(self, name):
        # Spark resolves names case-insensitively by default, so a variant
        # would collide with the hidden column just the same.
        with pytest.raises(ValueError, match=re.escape(f"'{name}' is reserved")):
            CsvTableSchema.model_validate(
                {"columns": [{"name": name, "source": "origin", "type": "string"}]}
            )
        # Sourcing it under another name is how a contract exposes the file name.
        schema = CsvTableSchema.model_validate(
            {"columns": [{"name": "origin", "source": "_source_file", "type": "string"}]}
        )
        assert schema.columns[0].source_name == "_source_file"

    def test_columns_are_mandatory(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("grain: [a]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="columns"):
            CsvTableSchema.from_yaml(path)

    def test_real_jepx_contract(self):
        schema = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jepx_spot.yaml")
        assert schema.grain == ["trade_date", "time_code"]
        assert schema.read_options == {"encoding": "windows-31j"}
        first = schema.columns[0]
        assert (first.name, first.source, first.type, first.format, first.nullable) == (
            "trade_date",
            "年月日",
            "date",
            "yyyy/MM/dd",
            False,
        )
        # Block/FIP columns were appended in later fiscal years → optional.
        assert any(not c.required for c in schema.columns)


# --------------------------------------------------------------------------- loader

SCHEMA = CsvTableSchema.model_validate(
    {
        "grain": ["id"],
        "columns": [
            {"name": "id", "type": "int", "nullable": False},
            {"name": "big", "type": "bigint"},
            {"name": "val", "type": "double"},
            {"name": "label", "type": "string"},
            {"name": "d", "type": "date", "format": "yyyy/MM/dd"},
            {"name": "iso_date", "type": "date"},
            {"name": "ts", "type": "timestamp", "format": "yyyy/MM/dd HH:mm"},
            {"name": "block_no", "source": "ブロックNo.", "type": "int"},
            {"name": "value_kwh", "source": "value(kWh)", "type": "bigint"},
            {"name": "extra", "type": "string", "required": False},
        ],
    }
)

FILE_A = [
    "id,big,val,label,d,iso_date,ts,ブロックNo.,value(kWh),extra,ignored",
    "1,3000000000,1.5,alpha,2024/01/05,2024-01-05,2024/01/05 13:30,7,100,x,zzz",
    "2,-1,0.25,beta,2024/02/29,2024-02-29,2024/02/29 00:00,8,200,y,zzz",
]
#: Same source, later vintage: columns reordered and ``extra`` not yet present.
FILE_B = [
    "value(kWh),ブロックNo.,ts,iso_date,d,label,val,big,id",
    "300,9,2024/03/01 23:59,2024-03-01,2024/03/01,gamma,-2.0,0,3",
]


def write_utf8(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_cp932(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


def table_rows(spark, table: str) -> dict:
    return {r.id: r for r in spark.table(table).collect()}


POSITIONAL_SCHEMA = CsvTableSchema.model_validate(
    {
        "grain": ["id"],
        "columns": [
            {"name": "id", "source": "_c0", "type": "int", "nullable": False},
            {"name": "label", "source": "_c1", "type": "string", "nullable": False},
        ],
    }
)


class TestScanPositional:
    def test_one_scan_reads_every_file_headerless_with_its_file_name(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha", "2,beta"])
        write_utf8(tmp_path / "b.csv", ["id,label", "3,gamma"])
        loader = CsvLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.scan", spark=spark)

        df = loader._scan_positional([str(tmp_path / "a.csv"), str(tmp_path / "b.csv")], 2)

        assert df.columns == ["_c0", "_c1", SOURCE_FILE_COL]
        assert {f.dataType.simpleString() for f in df.schema} == {"string"}
        # Header lines are ordinary rows; every row names the file it came from.
        assert sorted((r._c0, r._c1, r[SOURCE_FILE_COL]) for r in df.collect()) == [
            ("1", "alpha", "a.csv"),
            ("2", "beta", "a.csv"),
            ("3", "gamma", "b.csv"),
            ("id", "label", "a.csv"),
            ("id", "label", "b.csv"),
        ]


class PositionalLoader(CsvLoader):
    """Test double: ``id,label`` files with a one-line header, read positionally."""

    def _read_all(self, files: list[str]) -> DataFrame:
        raw = self._scan_positional(files, 2).filter(F.col("_c0") != "id")
        return self._project(raw)


class TestProject:
    def test_casts_the_contract_in_order_and_keeps_the_file_name(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha"])
        loader = CsvLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.project", spark=spark)
        raw = loader._scan_positional([str(tmp_path / "a.csv")], 2).filter(F.col("_c0") != "id")

        df = loader._project(raw)

        assert [(f.name, f.dataType.simpleString()) for f in df.schema] == [
            ("id", "int"),
            ("label", "string"),
            (SOURCE_FILE_COL, "string"),
        ]
        assert [tuple(r) for r in df.collect()] == [(1, "alpha", "a.csv")]


class TestPositionalLoad:
    def test_loads_through_the_contract_without_the_file_column(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha", "2,beta"])
        write_utf8(tmp_path / "b.csv", ["id,label", "3,gamma"])
        loader = PositionalLoader(
            POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.positional", spark=spark
        )

        assert loader.load() == 3

        table = spark.table("test_csv_loader.positional")
        assert table.columns == ["id", "label"]
        assert sorted(tuple(r) for r in table.collect()) == [
            (1, "alpha"),
            (2, "beta"),
            (3, "gamma"),
        ]

    def test_null_report_names_the_offending_files_only(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha"])
        write_utf8(tmp_path / "b.csv", ["id,label", "2,", "3,"])
        loader = PositionalLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.nulls", spark=spark)

        with pytest.raises(ValueError) as exc:
            loader.load()

        assert str(exc.value) == (
            "Non-nullable columns contain nulls after casting (null count per column): "
            f"{{'label': 2}}; by file (first {REPORT_LIMIT}): {{'b.csv': {{'label': 2}}}}"
        )
        assert not spark.catalog.tableExists("test_csv_loader.nulls")

    def test_reports_are_empty_for_frames_without_a_source_file_column(self, spark, tmp_path):
        loader = CsvLoader(POSITIONAL_SCHEMA, tmp_path, "t", spark=spark)
        df = spark.createDataFrame([(1, "a"), (1, "b")], "id int, label string")
        assert loader._nulls_by_file(df, ["label"]) == ""
        assert loader._duplicates_by_file(df) == ""

    def test_duplicate_report_lists_keys_with_their_files(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha", "2,beta"])
        write_utf8(tmp_path / "b.csv", ["id,label", "2,beta-again", "3,gamma"])
        loader = PositionalLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.dups", spark=spark)

        with pytest.raises(ValueError) as exc:
            loader.load()

        assert str(exc.value) == (
            "Grain ['id'] is not unique: 4 rows but 3 distinct keys; "
            f"first {REPORT_LIMIT} duplicated keys (key, rows, files): "
            "[((2,), 2, ['a.csv', 'b.csv'])]"
        )


class TestCsvLoaderLoad:
    def test_directory_of_files_is_unioned_by_name_and_cast(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "b.csv", FILE_B)
        (tmp_path / "notes.txt").write_text("not a csv", encoding="utf-8")
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.happy", spark=spark)

        n_rows = loader.load()

        assert n_rows == 3
        table = spark.table("test_csv_loader.happy")
        # Destination column order and types follow the contract, not the files.
        assert [(f.name, f.dataType.simpleString()) for f in table.schema] == [
            ("id", "int"),
            ("big", "bigint"),
            ("val", "double"),
            ("label", "string"),
            ("d", "date"),
            ("iso_date", "date"),
            ("ts", "timestamp"),
            ("block_no", "int"),
            ("value_kwh", "bigint"),
            ("extra", "string"),
        ]
        rows = table_rows(spark, "test_csv_loader.happy")
        assert set(rows) == {1, 2, 3}
        assert tuple(rows[1]) == (
            1,
            3000000000,
            1.5,
            "alpha",
            datetime.date(2024, 1, 5),
            datetime.date(2024, 1, 5),
            datetime.datetime(2024, 1, 5, 13, 30),
            7,
            100,
            "x",
        )
        assert tuple(rows[2]) == (
            2,
            -1,
            0.25,
            "beta",
            datetime.date(2024, 2, 29),
            datetime.date(2024, 2, 29),
            datetime.datetime(2024, 2, 29, 0, 0),
            8,
            200,
            "y",
        )
        # File B: reordered columns are matched by header, the optional column
        # is filled with null and the unlisted ``ignored`` column is dropped.
        assert tuple(rows[3]) == (
            3,
            0,
            -2.0,
            "gamma",
            datetime.date(2024, 3, 1),
            datetime.date(2024, 3, 1),
            datetime.datetime(2024, 3, 1, 23, 59),
            9,
            300,
            None,
        )
        assert "ignored" not in table.columns

    def test_single_file_path(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "b.csv", FILE_B)
        loader = CsvLoader(SCHEMA, tmp_path / "b.csv", "test_csv_loader.single", spark=spark)
        assert loader.load() == 1
        assert set(table_rows(spark, "test_csv_loader.single")) == {3}

    def test_glob_pattern_selects_matching_files_only(self, spark, tmp_path):
        write_utf8(tmp_path / "spot_2023.csv", FILE_A)
        write_utf8(tmp_path / "spot_2024.csv", FILE_B)
        write_utf8(tmp_path / "other.csv", FILE_B)
        loader = CsvLoader(SCHEMA, tmp_path / "spot_*.csv", "test_csv_loader.glob", spark=spark)
        assert loader.load() == 3
        assert set(table_rows(spark, "test_csv_loader.glob")) == {1, 2, 3}

    def test_no_matching_files_raises(self, spark, tmp_path):
        (tmp_path / "empty").mkdir()
        loader = CsvLoader(SCHEMA, tmp_path / "empty", "test_csv_loader.none", spark=spark)
        with pytest.raises(FileNotFoundError, match="No CSV files found"):
            loader.load()
        loader = CsvLoader(SCHEMA, tmp_path / "nope_*.csv", "test_csv_loader.none", spark=spark)
        with pytest.raises(FileNotFoundError, match="nope_"):
            loader.load()

    def test_reload_replaces_previous_contents(self, spark, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        write_utf8(first / "a.csv", FILE_A)
        write_utf8(second / "b.csv", FILE_B)
        assert CsvLoader(SCHEMA, first, "test_csv_loader.reload", spark=spark).load() == 2
        assert CsvLoader(SCHEMA, second, "test_csv_loader.reload", spark=spark).load() == 1
        assert set(table_rows(spark, "test_csv_loader.reload")) == {3}

    def test_table_without_database_prefix_lands_in_default_db(self, spark, tmp_path):
        write_utf8(tmp_path / "b.csv", FILE_B)
        loader = CsvLoader(SCHEMA, tmp_path, "csv_loader_nodb", spark=spark)
        assert loader.load() == 1
        assert spark.catalog.tableExists("csv_loader_nodb")
        assert set(table_rows(spark, "csv_loader_nodb")) == {3}

    def test_missing_required_column_raises_with_source_names(self, spark, tmp_path):
        lines = list(FILE_A)
        # Drop the value(kWh) header + cell from every line.
        lines = [",".join(v for i, v in enumerate(ln.split(",")) if i != 8) for ln in lines]
        write_utf8(tmp_path / "a.csv", lines)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.missing", spark=spark)
        with pytest.raises(
            ValueError, match=re.escape("is missing required columns: ['value(kWh)']")
        ):
            loader.load()
        assert not spark.catalog.tableExists("test_csv_loader.missing")

    def test_non_nullable_column_with_nulls_after_cast_raises(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "columns": [
                    {"name": "id", "type": "int", "nullable": False},
                    {"name": "d", "type": "date", "format": "yyyy/MM/dd", "nullable": False},
                    {"name": "note", "type": "string", "nullable": False},
                ],
            }
        )
        write_utf8(
            tmp_path / "a.csv",
            ["id,d,note", ",2024/01/05,ok", "2,,ok", "3,2024/01/07,ok", "4,,ok"],
        )
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.nulls", spark=spark)
        with pytest.raises(
            ValueError,
            match=re.escape(
                "nulls after casting (null count per column): {'id': 1, 'd': 2}; by file"
            ),
        ):
            loader.load()
        assert not spark.catalog.tableExists("test_csv_loader.nulls")

    def test_nullable_columns_may_be_null(self, spark, tmp_path):
        write_utf8(
            tmp_path / "a.csv",
            ["id,big,val,label,d,iso_date,ts,ブロックNo.,value(kWh)", "5,,,,,,,,"],
        )
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.nullable", spark=spark)
        assert loader.load() == 1
        row = table_rows(spark, "test_csv_loader.nullable")[5]
        assert tuple(row) == (5, None, None, None, None, None, None, None, None, None)

    def test_duplicate_grain_raises(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "dup.csv", FILE_A[:2])  # id 1 again
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.dup", spark=spark)
        with pytest.raises(
            ValueError,
            match=re.escape("Grain ['id'] is not unique: 3 rows but 2 distinct keys; first 10"),
        ):
            loader.load()
        assert not spark.catalog.tableExists("test_csv_loader.dup")

    def test_empty_grain_skips_uniqueness_check(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "dup.csv", FILE_A[:2])
        schema = SCHEMA.model_copy(update={"grain": []})
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.nograin", spark=spark)
        assert loader.load() == 3
        assert [r.id for r in spark.table("test_csv_loader.nograin").collect()].count(1) == 2

    def test_read_options_encoding_decodes_cp932_headers_and_values(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"encoding": "windows-31j"},
                "columns": [
                    {"name": "d", "source": "年月日", "type": "date", "format": "yyyy/MM/dd"},
                    {"name": "area", "source": "エリア", "type": "string"},
                ],
            }
        )
        write_cp932(tmp_path / "jp.csv", ["年月日,エリア", "2024/01/05,東京", "2024/01/06,関西"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.cp932", spark=spark)
        assert loader.load() == 2
        rows = sorted((r.d, r.area) for r in spark.table("test_csv_loader.cp932").collect())
        assert rows == [
            (datetime.date(2024, 1, 5), "東京"),
            (datetime.date(2024, 1, 6), "関西"),
        ]

    def test_missing_encoding_option_makes_cp932_headers_unresolvable(self, spark, tmp_path):
        # Without read_options the header decodes as mojibake, so the load
        # fails loudly instead of loading garbage.
        schema = CsvTableSchema.model_validate(
            {"columns": [{"name": "d", "source": "年月日", "type": "string"}]}
        )
        write_cp932(tmp_path / "jp.csv", ["年月日,エリア", "2024/01/05,東京"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.badenc", spark=spark)
        with pytest.raises(ValueError, match="missing required columns"):
            loader.load()


class TestCsvLoaderConstruction:
    def test_enables_java_charsets_on_the_session(self, spark, tmp_path):
        spark.conf.set("spark.sql.legacy.javaCharsets", "false")
        CsvLoader(SCHEMA, tmp_path, "test_csv_loader.ctor", spark=spark)
        assert spark.conf.get("spark.sql.legacy.javaCharsets") == "true"

    def test_filepath_is_normalised_to_path(self, spark, tmp_path):
        loader = CsvLoader(SCHEMA, str(tmp_path / "x.csv"), "t", spark=spark)
        assert loader.filepath == tmp_path / "x.csv"
        assert (loader.schema, loader.table, loader.spark) == (SCHEMA, "t", spark)

    def test_default_session_is_the_active_one_and_loads(self, spark, tmp_path):
        write_utf8(tmp_path / "b.csv", FILE_B)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.default_session")
        assert loader.spark is spark
        assert loader.load() == 1
        assert set(table_rows(spark, "test_csv_loader.default_session")) == {3}


class TestHeaderGroupedRead:
    def test_files_with_one_header_share_a_scan_and_layouts_are_unioned(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "a2.csv", FILE_A)
        write_utf8(tmp_path / "b.csv", FILE_B)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.groups", spark=spark)

        df = loader._read_all(loader._resolve_files())

        plan = df._jdf.queryExecution().analyzed().toString()
        # Two layouts → exactly one Union of two scans, not a union per file.
        assert plan.count("Union") == 1
        assert sorted((r.id, r[SOURCE_FILE_COL]) for r in df.collect()) == [
            (1, "a.csv"),
            (1, "a2.csv"),
            (2, "a.csv"),
            (2, "a2.csv"),
            (3, "b.csv"),
        ]

    def test_single_layout_is_one_scan_without_a_union(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "a2.csv", FILE_A)
        loader = CsvLoader(SCHEMA, tmp_path, "t", spark=spark)
        plan = loader._read_all(loader._resolve_files())._jdf.queryExecution().analyzed()
        assert "Union" not in plan.toString()

    def test_missing_required_column_is_reported_for_its_own_file(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        lines = [",".join(v for i, v in enumerate(ln.split(",")) if i != 8) for ln in FILE_A]
        write_utf8(tmp_path / "bad.csv", lines)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.badfile", spark=spark)
        with pytest.raises(
            ValueError, match=re.escape("bad.csv is missing required columns: ['value(kWh)']")
        ):
            loader.load()

    def test_gzip_files_are_sniffed_and_read(self, spark, tmp_path):
        with gzip.open(tmp_path / "a.csv.gz", "wt", encoding="utf-8", newline="") as f:
            f.write("\n".join(FILE_A) + "\n")
        loader = CsvLoader(SCHEMA, tmp_path / "*.csv.gz", "test_csv_loader.gz", spark=spark)
        assert loader.load() == 2

    def test_header_sniff_honours_encoding_and_separator(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"encoding": "windows-31j", "sep": ";"},
                "columns": [
                    {"name": "v", "source": "値", "type": "int"},
                    {"name": "k", "source": "キー", "type": "string"},
                ],
            }
        )
        write_cp932(tmp_path / "j.csv", ["キー;値", "a;1"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.sep", spark=spark)
        assert loader._read_header(str(tmp_path / "j.csv")) == ["キー", "値"]
        assert loader.load() == 1

    def test_header_sniff_honours_the_quote_character(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"sep": ";", "quote": "'"},
                "columns": [
                    {"name": "id_part", "source": "id;part", "type": "string"},
                    {"name": "value", "type": "int"},
                ],
            }
        )
        write_utf8(tmp_path / "q.csv", ["'id;part';value", "'a;b';1"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.quote", spark=spark)
        assert loader._read_header(str(tmp_path / "q.csv")) == ["id;part", "value"]
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.quote").collect()] == [("a;b", 1)]

    def test_header_sniff_with_quoting_disabled_keeps_quotes_literally(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"quote": "\u0000"},
                "columns": [
                    {"name": "a", "source": '"a"', "type": "string"},
                    {"name": "b", "type": "int"},
                ],
            }
        )
        write_utf8(tmp_path / "nq.csv", ['"a",b', '"x",1'])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.noquote", spark=spark)
        assert loader._read_header(str(tmp_path / "nq.csv")) == ['"a"', "b"]
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.noquote").collect()] == [('"x"', 1)]

    def test_header_sniff_accepts_the_delimiter_alias(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"delimiter": ";"},
                "columns": [{"name": "id", "type": "int"}, {"name": "value", "type": "int"}],
            }
        )
        write_utf8(tmp_path / "d.csv", ["id;value", "1;2"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.delim", spark=spark)
        assert loader._read_header(str(tmp_path / "d.csv")) == ["id", "value"]
        assert loader.load() == 1

    def test_duplicated_contract_header_is_rejected(self, spark, tmp_path):
        write_utf8(tmp_path / "dup.csv", ["id,id,big", "1,1,2"])
        schema = CsvTableSchema.model_validate(
            {"columns": [{"name": "id", "type": "int"}, {"name": "big", "type": "bigint"}]}
        )
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.dup", spark=spark)
        with pytest.raises(
            ValueError, match=re.escape("dup.csv has duplicated header columns: ['id']")
        ):
            loader.load()
        assert not spark.catalog.tableExists("test_csv_loader.dup")

    @pytest.mark.parametrize(
        "suffix, compress",
        [
            (".csv.gz", lambda b: gzip.compress(b)),
            (".csv.bz2", lambda b: bz2.compress(b)),
            # Hadoop's DefaultCodec (zlib format): no Python opener, header via Spark.
            (".csv.deflate", lambda b: zlib.compress(b)),
        ],
    )
    def test_compressed_files_are_sniffed_and_read(self, spark, tmp_path, suffix, compress):
        (tmp_path / f"a{suffix}").write_bytes(compress(("\n".join(FILE_A) + "\n").encode("utf-8")))
        loader = CsvLoader(
            SCHEMA, tmp_path / f"*{suffix}", f"test_csv_loader.c{suffix.count('.')}", spark=spark
        )
        assert loader._read_header(str(tmp_path / f"a{suffix}"))[:3] == ["id", "big", "val"]
        assert loader.load() == 2

    def test_header_sniff_honours_the_escape_character(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "columns": [
                    {"name": "dn", "source": 'display"name', "type": "string"},
                    {"name": "id", "type": "int"},
                ]
            }
        )
        write_utf8(tmp_path / "e.csv", ['"display\\"name",id', '"x\\"y",1'])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.escape", spark=spark)
        # An escape anywhere in the line is Spark's to apply.
        assert loader._parse_header('"display\\"name",id') is None
        assert loader._read_header(str(tmp_path / "e.csv")) == ['display"name', "id"]
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.escape").collect()] == [('x"y', 1)]

    def test_multi_character_separator_falls_back_to_spark(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"sep": "||"},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}],
            }
        )
        write_utf8(tmp_path / "m.csv", ["id||v", "1||2"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.multisep", spark=spark)
        assert loader._read_header(str(tmp_path / "m.csv")) == ["id", "v"]
        assert loader.load() == 1

    def test_leading_blank_lines_are_skipped_like_spark(self, spark, tmp_path):
        write_utf8(tmp_path / "blank.csv", ["", ""] + FILE_A)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.blank", spark=spark)
        assert loader._read_header(str(tmp_path / "blank.csv"))[:2] == ["id", "big"]
        assert loader.load() == 2

    def test_charset_alias_is_honoured(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"charset": "windows-31j"},
                "columns": [{"name": "k", "source": "キー", "type": "string"}],
            }
        )
        write_cp932(tmp_path / "c.csv", ["キー", "a"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.charset", spark=spark)
        assert loader._read_header(str(tmp_path / "c.csv")) == ["キー"]
        assert loader.load() == 1

    def test_python_misparse_is_verified_by_spark_before_failing(self, spark, tmp_path):
        # A name the Python dialect cannot reproduce but Spark can is not an error;
        # a column Spark cannot find either still is.
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"sep": "||"},
                "columns": [{"name": "id", "type": "int"}, {"name": "gone", "type": "int"}],
            }
        )
        write_utf8(tmp_path / "m.csv", ["id||v", "1||2"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.verify", spark=spark)
        with pytest.raises(
            ValueError, match=re.escape("m.csv is missing required columns: ['gone']")
        ):
            loader.load()

    def test_empty_file_is_reported_as_missing_columns(self, spark, tmp_path):
        (tmp_path / "empty.csv").write_bytes(b"")
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.empty", spark=spark)
        assert loader._header_line(str(tmp_path / "empty.csv")) == ""
        with pytest.raises(ValueError, match="empty.csv is missing required columns"):
            loader.load()

    def test_comment_lines_are_skipped_and_layouts_still_grouped_by_header(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"comment": "#"},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}],
            }
        )
        # Same comment line, opposite column order: must not share a scan.
        write_utf8(tmp_path / "a.csv", ["# generated", "id,v", "1,10"])
        write_utf8(tmp_path / "b.csv", ["# generated", "v,id", "20,2"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.comment", spark=spark)
        assert loader._read_header(str(tmp_path / "b.csv")) == ["v", "id"]
        assert loader.load() == 2
        assert sorted(tuple(r) for r in spark.table("test_csv_loader.comment").collect()) == [
            (1, 10),
            (2, 20),
        ]

    def test_custom_line_separator_defers_to_spark_and_still_groups(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"lineSep": ";"},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}],
            }
        )
        (tmp_path / "a.csv").write_text("id,v;1,10;2,20;", encoding="utf-8")
        (tmp_path / "b.csv").write_text("id,v;3,30;", encoding="utf-8")
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.linesep", spark=spark)
        assert loader._header_line(str(tmp_path / "a.csv")) is None
        df = loader._read_all(loader._resolve_files())
        # Identical Spark-parsed headers → one scan, no per-file union.
        assert "Union" not in df._jdf.queryExecution().analyzed().toString()
        assert loader.load() == 3

    def test_multiline_option_defers_the_header_to_spark(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"multiLine": "true"},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}],
            }
        )
        write_utf8(tmp_path / "a.csv", ["id,v", "1,10"])
        write_utf8(tmp_path / "b.csv", ["id,v", "2,20"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.multiline", spark=spark)
        assert loader._header_line(str(tmp_path / "a.csv")) is None
        df = loader._read_all(loader._resolve_files())
        assert "Union" not in df._jdf.queryExecution().analyzed().toString()
        assert loader.load() == 2

    def test_a_source_column_named_metadata_does_not_shadow_the_file_name(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "meta", "source": "_metadata", "type": "string"},
                ]
            }
        )
        write_utf8(tmp_path / "m.csv", ["id,_metadata", "1,x"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.metadata", spark=spark)
        df = loader._read_all(loader._resolve_files())
        assert [tuple(r) for r in df.collect()] == [(1, "x", "m.csv")]
        assert loader.load() == 1

    def test_java_only_charset_defers_the_header_to_spark(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {
                # A Java charset Python has no codec for (ASCII-compatible, so
                # Spark's byte-level line splitting still works).
                "read_options": {"encoding": "x-IBM942C"},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}],
            }
        )
        (tmp_path / "u.csv").write_bytes(b"id,v\n1,10\n")
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.javacharset", spark=spark)
        assert loader._header_line(str(tmp_path / "u.csv")) is None
        assert loader._read_header(str(tmp_path / "u.csv")) == ["id", "v"]
        assert loader.load() == 1

    def test_case_only_duplicate_header_follows_the_session_case_sensitivity(self, spark, tmp_path):
        # Spark's default resolver is case-insensitive: it names id/ID id0/ID1,
        # and the contract's `id` would silently read as null.
        write_utf8(tmp_path / "dup.csv", ["id,ID,big", "1,7,2"])
        schema = CsvTableSchema.model_validate(
            {"columns": [{"name": "id", "type": "int"}, {"name": "big", "type": "bigint"}]}
        )
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.casedup", spark=spark)
        assert loader._safe_header(["id", "ID", "big"]) == ["id0", "ID1", "big"]
        with pytest.raises(
            ValueError, match=re.escape("dup.csv has duplicated header columns: ['id']")
        ):
            loader.load()
        spark.conf.set("spark.sql.caseSensitive", "true")
        try:
            assert loader._safe_header(["id", "ID", "big"]) == ["id", "ID", "big"]
            assert loader.load() == 1
            assert [tuple(r) for r in spark.table("test_csv_loader.casedup").collect()] == [(1, 2)]
        finally:
            spark.conf.set("spark.sql.caseSensitive", "false")

    def test_case_only_duplicate_is_rejected_on_the_spark_path_too(self, spark, tmp_path):
        (tmp_path / "dup.csv.deflate").write_bytes(zlib.compress(b"id,ID,big\n1,7,2\n"))
        schema = CsvTableSchema.model_validate(
            {"columns": [{"name": "id", "type": "int"}, {"name": "big", "type": "bigint"}]}
        )
        loader = CsvLoader(schema, tmp_path / "*.deflate", "test_csv_loader.sdup", spark=spark)
        assert loader._header_line(str(tmp_path / "dup.csv.deflate")) is None
        with pytest.raises(
            ValueError, match=re.escape("dup.csv.deflate has duplicated header columns: ['id']")
        ):
            loader.load()

    def test_header_cell_equal_to_null_value_is_not_a_column_on_either_path(self, spark, tmp_path):
        # Spark names a header cell equal to nullValue `_c<i>` (makeSafeHeader),
        # so a contract sourcing that name can never be served.
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"nullValue": "NA"},
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "na", "source": "NA", "type": "int"},
                ],
            }
        )
        write_utf8(tmp_path / "n.csv", ["id,NA", "1,2"])
        (tmp_path / "n.csv.deflate").write_bytes(zlib.compress(b"id,NA\n1,2\n"))
        assert CsvLoader(schema, tmp_path, "t", spark=spark)._safe_header(["id", "NA"]) == [
            "id",
            "_c1",
        ]
        for pattern in ("*.csv", "*.deflate"):
            loader = CsvLoader(schema, tmp_path / pattern, "test_csv_loader.nullvalue", spark=spark)
            with pytest.raises(ValueError, match=re.escape("is missing required columns: ['NA']")):
                loader.load()
        # Spark reads the cell as null on the fallback path; it is named like the header scan.
        assert loader._spark_header(str(tmp_path / "n.csv.deflate")) == ["id", ""]
        assert loader._safe_header(loader._spark_header(str(tmp_path / "n.csv.deflate"))) == [
            "id",
            "_c1",
        ]

    @pytest.mark.parametrize("case_sensitive", ["false", "true"])
    def test_header_names_match_sparks_on_both_paths(self, spark, tmp_path, case_sensitive):
        # Neither the preflight nor the fallback may accept what Spark's
        # header=true scan would rename: both must reproduce its names.
        headers = ["id,ID,big", "id,id,big", "a,,b", 'a,"",b', "a,NA,b", "a,b,A,B,", "x,y,z"]
        schema = CsvTableSchema.model_validate(
            {"read_options": {"nullValue": "NA"}, "columns": [{"name": "x", "type": "int"}]}
        )
        loader = CsvLoader(schema, tmp_path, "t", spark=spark)
        spark.conf.set("spark.sql.caseSensitive", case_sensitive)
        try:
            for i, header in enumerate(headers):
                file = tmp_path / f"h{i}.csv"
                write_utf8(file, [header, "1,2,3,4,5"])
                spark_names = (
                    spark.read.options(header="true", nullValue="NA").csv(str(file)).columns
                )
                cells = loader._parse_header(loader._header_line(str(file)))
                assert loader._safe_header(cells) == spark_names, header
                assert loader._safe_header(loader._spark_header(str(file))) == spark_names, header
                assert loader._safe_header(spark_names) == spark_names, header
        finally:
            spark.conf.set("spark.sql.caseSensitive", "false")

    def test_file_name_keeps_a_literal_plus_and_decodes_escapes(self, spark, tmp_path):
        # Hadoop paths keep "+" literal and escape a space as %20 (a percent as
        # %25); form decoding would have turned the plus into a space.
        write_utf8(tmp_path / "a+b c%.csv", FILE_A)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.plus", spark=spark)
        files = loader._resolve_files()
        assert {r[SOURCE_FILE_COL] for r in loader._read_all(files).collect()} == {"a+b c%.csv"}
        assert {r[SOURCE_FILE_COL] for r in loader._scan_positional(files, 1).collect()} == {
            "a+b c%.csv"
        }

    def test_option_keys_are_matched_case_insensitively_like_sparks(self, spark, tmp_path):
        # Spark keeps reader options in a case-insensitive map: the preflight
        # must apply the same separator and nullValue the scan will.
        columns = [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}]
        schema = CsvTableSchema.model_validate({"read_options": {"SEP": ";"}, "columns": columns})
        write_utf8(tmp_path / "s.csv", ["id;v", "1;2"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.sepcase", spark=spark)
        assert loader._option("sep", ",") == ";"
        assert loader._parse_header("id;v") == ["id", "v"]
        assert loader.load() == 1

        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"nullvalue": "NA"},
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "na", "source": "NA", "type": "int"},
                ],
            }
        )
        write_utf8(tmp_path / "n.csv", ["id,NA", "1,2"])
        (tmp_path / "n.csv.deflate").write_bytes(zlib.compress(b"id,NA\n1,2\n"))
        for pattern in ("n.csv", "*.deflate"):
            loader = CsvLoader(schema, tmp_path / pattern, "test_csv_loader.nvcase", spark=spark)
            assert loader._safe_header(["id", "NA"]) == ["id", "_c1"]
            with pytest.raises(ValueError, match=re.escape("is missing required columns: ['NA']")):
                loader.load()

    def test_a_physical_source_file_header_is_rejected_as_reserved(self, spark, tmp_path):
        # `_source_file` is the loader's hidden file-name column: a provider
        # column of that name would be overwritten, so the file is refused —
        # in any case the session resolver would match.
        schema = CsvTableSchema.model_validate(
            {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "origin", "source": "_source_file", "type": "string"},
                ]
            }
        )
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.reserved", spark=spark)
        for header in ("_source_file", "_SOURCE_FILE"):
            write_utf8(tmp_path / "s.csv", [f"id,{header}", "1,provider"])
            with pytest.raises(
                ValueError, match=re.escape(f"s.csv has the reserved header column ['{header}']")
            ):
                loader.load()
        # Without such a header the sourced `_source_file` is the file name.
        write_utf8(tmp_path / "s.csv", ["id", "1"])
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.reserved").collect()] == [
            (1, "s.csv")
        ]

    @pytest.mark.parametrize("option", ["ignoreLeadingWhiteSpace", "ignoreTrailingWhiteSpace"])
    def test_whitespace_trimming_options_defer_the_header_to_spark(self, spark, tmp_path, option):
        # Spark trims header cells before naming them, so " id,id" is a
        # duplicate to it and "id ,v" is the column `id`.
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {option: "true"},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}],
            }
        )
        pad = (" ", "") if option == "ignoreLeadingWhiteSpace" else ("", " ")
        write_utf8(tmp_path / "dup.csv", [f"{pad[0]}id{pad[1]},id,v", "1,7,2"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.trim", spark=spark)
        assert loader._header_line(str(tmp_path / "dup.csv")) is None
        with pytest.raises(
            ValueError, match=re.escape("dup.csv has duplicated header columns: ['id']")
        ):
            loader.load()
        write_utf8(tmp_path / "dup.csv", [f"{pad[0]}id{pad[1]},v", "1,2"])
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.trim").collect()] == [(1, 2)]

    @pytest.mark.parametrize("escape", ["", "\u0000"])
    def test_disabled_escape_defers_the_header_to_spark(self, spark, tmp_path, escape):
        # Spark reads an empty (or NUL) escape as "no escape character";
        # Python's csv module has no such dialect, so Spark parses the header.
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"escape": escape},
                "columns": [{"name": "id", "type": "int"}, {"name": "v", "type": "string"}],
            }
        )
        write_utf8(tmp_path / "e.csv", ["id,v", '1,"a\\b"'])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.noescape", spark=spark)
        assert loader._parse_header("id,v") is None
        assert loader._read_header(str(tmp_path / "e.csv")) == ["id", "v"]
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.noescape").collect()] == [
            (1, "a\\b")
        ]

    def test_malformed_quoting_defers_the_header_to_spark(self, spark, tmp_path):
        # A quote the Python parser cannot place strictly is left to Spark,
        # whose unescapedQuoteHandling decides what the cell becomes.
        columns = [{"name": "id", "type": "int"}, {"name": "v", "type": "int"}]
        loader = CsvLoader(
            CsvTableSchema.model_validate({"columns": columns}), tmp_path, "t", spark=spark
        )
        assert loader._parse_header('"id"x,v') is None
        write_utf8(tmp_path / "q.csv", ['"id"x,v', "1,2"])
        file = str(tmp_path / "q.csv")
        assert loader._read_header(file) == loader._spark_header(file)
        assert loader._safe_header(loader._read_header(file)) == (
            spark.read.options(header="true").csv(file).columns
        )

        schema = CsvTableSchema.model_validate(
            {"read_options": {"unescapedQuoteHandling": "SKIP_VALUE"}, "columns": columns}
        )
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.skipvalue", spark=spark)
        assert loader._parse_header("id,v") is None
        assert loader._safe_header(loader._read_header(file)) == (
            spark.read.options(header="true", unescapedQuoteHandling="SKIP_VALUE").csv(file).columns
        )
        with pytest.raises(
            ValueError, match=re.escape("q.csv is missing required columns: ['id']")
        ):
            loader.load()

    def test_non_default_empty_value_defers_the_header_to_spark(self, spark, tmp_path):
        # A quoted-empty header cell is named emptyValue by Spark; Python's csv
        # module cannot tell it from an unquoted empty cell, so Spark decides.
        schema = CsvTableSchema.model_validate(
            {
                "read_options": {"emptyValue": "EMPTY"},
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "e", "source": "EMPTY", "type": "int"},
                ],
            }
        )
        write_utf8(tmp_path / "e.csv", ['id,"",', "1,2,3"])
        file = str(tmp_path / "e.csv")
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.emptyvalue", spark=spark)
        assert loader._header_line(file) is None
        assert loader._read_header(file) == ["id", "EMPTY", ""]
        assert loader._safe_header(loader._read_header(file)) == (
            spark.read.options(header="true", emptyValue="EMPTY").csv(file).columns
        )
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.emptyvalue").collect()] == [(1, 2)]

    def test_an_unquoted_escape_character_defers_the_header_to_spark(self, spark, tmp_path):
        # Python's csv module would apply the escape in an unquoted cell too
        # (foo\bar -> foobar); Spark keeps it literal, so Spark decides.
        write_utf8(tmp_path / "u.csv", ["foo\\bar,id", "x,1"])
        file = str(tmp_path / "u.csv")
        columns = [
            {"name": "fb", "source": "foobar", "type": "string"},
            {"name": "id", "type": "int"},
        ]
        loader = CsvLoader(
            CsvTableSchema.model_validate({"columns": columns}),
            tmp_path,
            "test_csv_loader.unqesc",
            spark=spark,
        )
        assert loader._parse_header("foo\\bar,id") is None
        assert loader._read_header(file) == ["foo\\bar", "id"]
        with pytest.raises(
            ValueError, match=re.escape("u.csv is missing required columns: ['foobar']")
        ):
            loader.load()
        columns[0]["source"] = "foo\\bar"
        loader = CsvLoader(
            CsvTableSchema.model_validate({"columns": columns}),
            tmp_path,
            "test_csv_loader.unqesc",
            spark=spark,
        )
        assert loader.load() == 1
        assert [tuple(r) for r in spark.table("test_csv_loader.unqesc").collect()] == [("x", 1)]

    @pytest.mark.parametrize("header", ['"a""b",id', 'a"b,id', '"a"b,id'])
    def test_a_quote_inside_a_cell_defers_the_header_to_spark(self, spark, tmp_path, header):
        # Doubled, unquoted-cell or unescaped quotes are read by Spark's own
        # rules (its escape character, unescapedQuoteHandling), not Python's.
        loader = CsvLoader(SCHEMA, tmp_path, "t", spark=spark)
        assert loader._parse_header(header) is None
        assert loader._parse_header('"a","b",id') == ["a", "b", "id"]
        write_utf8(tmp_path / "q.csv", [header, "1,2"])
        file = str(tmp_path / "q.csv")
        assert loader._safe_header(loader._read_header(file)) == (
            spark.read.options(header="true").csv(file).columns
        )

    def test_loader_has_no_per_file_read(self):
        assert "_read_file" not in CsvLoader.__dict__


class TestPythonCodec:
    @pytest.mark.parametrize(
        "java, python",
        [
            ("windows-31j", "cp932"),
            ("MS932", "cp932"),
            ("UTF-8", "utf-8-sig"),
            ("utf8", "utf-8-sig"),
            ("Shift_JIS", "shift_jis"),
            ("EUC-JP", "euc-jp"),
        ],
    )
    def test_maps_java_charset_names(self, java, python):
        assert python_codec(java) == python
