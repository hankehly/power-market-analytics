"""Tests for the generic header-based CSV loader (``power_market_analytics.csv_loader``).

All loads run against the shared local Spark fixture and real CSV files in
``tmp_path``; tables land in the ``test_csv_loader`` database of the temp
warehouse.
"""

from __future__ import annotations

import datetime
import re
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
            match=re.escape("nulls after casting (null count per column): {'id': 1, 'd': 2}") + "$",
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
            match=re.escape("Grain ['id'] is not unique: 3 rows but 2 distinct keys") + "$",
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
