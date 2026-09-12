"""Shared でんき予報 parser and loader (power_market_analytics/power_usage.py)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from power_market_analytics.ingestion.loader import SOURCE_FILE_COL, CsvTableSchema
from power_market_analytics.ingestion.tso.area_actuals import AreaActualsSource
from power_market_analytics.ingestion.tso.power_usage import (
    CORRECTION_MARKER,
    HourlyFile,
    HourlyRow,
    PowerUsageCsvLoader,
    PowerUsageSource,
    parse_hourly,
)

# --- a demo source and fixture builders -------------------------------------

HEADER_5 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"
HEADER_6 = "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%),供給力(万kW)"
MULTI_DAY_HEADER = "DATE,TIME,実績(万kW)"

DEMO = PowerUsageSource(
    code="demo_power_usage",
    url_template="https://example.test/{year:04d}{month:02d}.zip",
    earliest_month=(2016, 4),
    member_re=re.compile(r"\d{8}_demo\.csv$"),
    accepted_headers=frozenset({HEADER_5, HEADER_6, MULTI_DAY_HEADER}),
    default_data_dir="data/demo/power_usage",
    multi_day_headers=frozenset({MULTI_DAY_HEADER}),
)


def hourly_rows(date: str, header: str = HEADER_5, hours: int = 24, base: int = 1200) -> list[str]:
    """One day's hourly rows: demand base+h, forecast base-10+h, 使用率 60+h%5, supply 2000+h."""
    width = header.count(",") + 1
    return [
        ",".join(
            [
                date,
                f"{hour}:00",
                str(base + hour),
                str(base - 10 + hour),
                str(60 + hour % 5),
                str(2000 + hour),
            ][:width]
        )
        for hour in range(hours)
    ]


def daily_lines(
    date: str = "2016/4/1",
    updated: str = "2016/4/2 1:10",
    header: str = HEADER_5,
    rows: list[str] | None = None,
) -> list[str]:
    """A daily でんき予報 file in the multi-section layout, as a list of lines."""
    return [
        f"{updated} UPDATE",
        "ピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,ピーク時使用率(%)",
        "2060,9:00～10:00,4/1,9:30,88",
        "",
        "予想最大電力(万kW),時間帯,予想最大電力情報更新日,予想最大電力情報更新時刻",
        "1830,9:00～10:00,4/1,9:30",
        "",
        header,
        *(hourly_rows(date, header) if rows is None else rows),
        "",
        "翌日のピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,ピーク時使用率(%)",
        "1968,19:00～20:00,4/1,20:20,78",
        "",
        "DATE,TIME,当日実績(５分間隔値)(万kW)",
        f"{date},0:00,1310",
        f"{date},0:05,1300",
        "",
    ]


def padded(lines: list[str]) -> list[str]:
    """Every line padded with commas to the widest line, as Excel re-saves a CSV."""
    width = max(line.count(",") + 1 for line in lines)
    return [line + "," * (width - line.count(",") - 1) for line in lines]


def write_lines_cp932(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


# --- source --------------------------------------------------------------------


class TestPowerUsageSource:
    def test_is_an_area_actuals_source(self):
        assert isinstance(DEMO, AreaActualsSource)
        assert DEMO.zip_url(2016, 4) == "https://example.test/201604.zip"
        assert DEMO.is_actuals_member("20160401_demo.csv")

    def test_multi_day_headers_default_to_none(self):
        plain = PowerUsageSource(
            code="plain",
            url_template="https://example.test/{year}{month}.zip",
            earliest_month=(2016, 4),
            member_re=re.compile(r"\.csv$"),
            accepted_headers=frozenset({HEADER_5}),
            default_data_dir="data/plain",
        )
        assert plain.multi_day_headers == frozenset()
        assert plain.known_missing_days == frozenset()

    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            DEMO.multi_day_headers = frozenset()  # type: ignore[misc]


# --- parser -------------------------------------------------------------------


class TestParseHourly:
    def test_plain_file_yields_the_hourly_rows_only(self, tmp_path):
        file = write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines())

        parsed = parse_hourly(file, DEMO)

        assert isinstance(parsed, HourlyFile)
        assert parsed.file_updated_at == "20160402 01:10:00"
        assert parsed.header == HEADER_5
        assert len(parsed.rows) == 24
        assert parsed.rows[0] == HourlyRow("20160401", 0, "1200", "1190", "60", None)
        assert parsed.rows[23] == HourlyRow("20160401", 23, "1223", "1213", "63", None)

    def test_six_field_header_fills_the_supply_capacity(self, tmp_path):
        lines = daily_lines("2025/9/1", "2025/9/2 1:10", HEADER_6)
        file = write_lines_cp932(tmp_path / "20250901_demo.csv", lines)

        rows = parse_hourly(file, DEMO).rows

        assert rows[5] == HourlyRow("20250901", 5, "1205", "1195", "60", "2005")

    def test_padded_file_parses_like_the_plain_one(self, tmp_path):
        # 60 Kansai files were re-saved from Excel: every line padded to the
        # widest row, ",,,,," where a blank line belongs, the header ending in
        # a comma. Without the trailing-comma rule the table never ends.
        lines = daily_lines()
        lines[1] = (
            "ピーク時供給力(万kW),時間帯,供給力情報更新日,供給力情報更新時刻,"
            "ピーク時予備率(%),ピーク時使用率(%)"
        )
        lines[2] = "2039,9:00～10:00,3月15日,0:13,15,86"
        padded_lines = padded(lines)
        assert padded_lines[0].endswith(" UPDATE,,,,,")
        assert padded_lines[3] == ",,,,,"
        assert padded_lines[7] == HEADER_5 + ","
        plain = write_lines_cp932(tmp_path / "plain.csv", daily_lines())
        excel = write_lines_cp932(tmp_path / "excel.csv", padded_lines)

        assert parse_hourly(excel, DEMO) == parse_hourly(plain, DEMO)

    def test_correction_row_replaces_the_non_blank_measures_of_the_row_above(self, tmp_path):
        # Kansai 2016-04-24: 修正後,,1212,,65,<reason> under the hour-0 row
        # (1267 万kW, 68 %) — demand and 使用率 change, the forecast stays.
        rows = hourly_rows("2016/4/24")
        rows.insert(1, f"{CORRECTION_MARKER},,1212,,65,4/25 システム不具合による数値誤りのため修正")
        lines = padded(daily_lines("2016/4/24", "2016/4/25 1:10", rows=rows))
        file = write_lines_cp932(tmp_path / "20160424_demo.csv", lines)

        parsed = parse_hourly(file, DEMO).rows

        assert len(parsed) == 24
        assert parsed[0] == HourlyRow("20160424", 0, "1212", "1190", "65", None)
        assert parsed[1] == HourlyRow("20160424", 1, "1201", "1191", "61", None)

    def test_correction_row_shorter_than_the_header_keeps_the_other_measures(self, tmp_path):
        rows = hourly_rows("2016/4/24")
        rows.insert(1, f"{CORRECTION_MARKER},,1212")
        file = write_lines_cp932(
            tmp_path / "20160424_demo.csv", daily_lines("2016/4/24", "2016/4/25 1:10", rows=rows)
        )

        assert parse_hourly(file, DEMO).rows[0] == HourlyRow(
            "20160424", 0, "1212", "1190", "60", None
        )

    def test_correction_row_under_the_header_raises(self, tmp_path):
        rows = [f"{CORRECTION_MARKER},,1212,,65", *hourly_rows("2016/4/24")]
        file = write_lines_cp932(
            tmp_path / "20160424_demo.csv", daily_lines("2016/4/24", "2016/4/25 1:10", rows=rows)
        )

        with pytest.raises(ValueError, match="no hourly row above"):
            parse_hourly(file, DEMO)

    def test_row_with_a_blank_last_measure_fails_the_field_count(self, tmp_path):
        # A trailing blank cell is indistinguishable from padding once the
        # trailing commas are stripped: fail loudly rather than load a shifted row.
        rows = hourly_rows("2016/4/1")
        rows[3] = "2016/4/1,3:00,1203,1193,"
        file = write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines(rows=rows))

        with pytest.raises(ValueError, match="4 fields, expected 5"):
            parse_hourly(file, DEMO)

    def test_daily_file_with_two_dates_raises(self, tmp_path):
        rows = [*hourly_rows("2016/4/1")[:23], *hourly_rows("2016/4/2")[23:]]
        file = write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines(rows=rows))

        with pytest.raises(ValueError, match="one target date"):
            parse_hourly(file, DEMO)

    def test_multi_day_header_may_hold_many_dates(self, tmp_path):
        rows = [
            *hourly_rows("2016/4/1", MULTI_DAY_HEADER),
            *hourly_rows("2016/4/2", MULTI_DAY_HEADER),
        ]
        lines = ["2018/1/1 18:10 UPDATE", "", MULTI_DAY_HEADER, *rows]
        file = write_lines_cp932(tmp_path / "juyo-2016.csv", lines)

        parsed = parse_hourly(file, DEMO)

        assert len(parsed.rows) == 48
        assert parsed.rows[24] == HourlyRow("20160402", 0, "1200", None, None, None)


# --- loader -------------------------------------------------------------------

CONTRACT = CsvTableSchema.model_validate(
    {
        "grain": ["target_date", "hour_start"],
        "columns": [
            {
                "name": "target_date",
                "source": "__target_date",
                "type": "date",
                "format": "yyyyMMdd",
                "nullable": False,
            },
            {"name": "hour_start", "source": "__hour_start", "type": "int", "nullable": False},
            {
                "name": "demand_mankw",
                "source": "__demand_mankw",
                "type": "double",
                "nullable": False,
            },
            {"name": "forecast_mankw", "source": "__forecast_mankw", "type": "double"},
            {"name": "usage_rate_pct", "source": "__usage_rate_pct", "type": "double"},
            {
                "name": "supply_capacity_mankw",
                "source": "__supply_capacity_mankw",
                "type": "double",
            },
            {
                "name": "file_updated_at",
                "source": "__file_updated_at",
                "type": "timestamp",
                "format": "yyyyMMdd HH:mm:ss",
                "nullable": False,
            },
            {"name": "source_file", "source": "__source_file", "type": "string", "nullable": False},
        ],
    }
)


class TestPowerUsageCsvLoader:
    def test_requires_a_source(self, spark, tmp_path):
        with pytest.raises(ValueError, match="source"):
            PowerUsageCsvLoader(CONTRACT, tmp_path, "test_pu.none", spark=spark)

    def test_loads_every_file_into_one_relation(self, spark, tmp_path):
        write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines())
        write_lines_cp932(
            tmp_path / "20160402_demo.csv", daily_lines("2016/4/2", "2016/4/3 1:10", HEADER_6)
        )
        loader = PowerUsageCsvLoader(
            CONTRACT, tmp_path, "test_pu.two_days", spark=spark, source=DEMO
        )

        df = loader._read_all(loader._resolve_files())
        assert df.columns == [c.name for c in CONTRACT.columns] + [SOURCE_FILE_COL]
        assert "Union" not in df._jdf.queryExecution().analyzed().toString()

        assert loader.load() == 48
        rows = {
            (r.target_date.isoformat(), r.hour_start): r
            for r in spark.table("test_pu.two_days").collect()
        }
        first = rows[("2016-04-01", 0)]
        assert (
            first.demand_mankw,
            first.forecast_mankw,
            first.usage_rate_pct,
            first.supply_capacity_mankw,
        ) == (1200.0, 1190.0, 60.0, None)
        assert first.file_updated_at.isoformat() == "2016-04-02T01:10:00"
        assert first.source_file == "20160401_demo.csv"
        assert rows[("2016-04-02", 23)].supply_capacity_mankw == 2023.0

    def test_file_rows_hook_selects_the_rows_per_file(self, spark, tmp_path):
        class OddHoursOnly(PowerUsageCsvLoader):
            source = DEMO

            def _file_rows(self, file: str, parsed: HourlyFile) -> list[HourlyRow]:
                return [row for row in parsed.rows if row.hour_start % 2]

        write_lines_cp932(tmp_path / "20160401_demo.csv", daily_lines())

        assert OddHoursOnly(CONTRACT, tmp_path, "test_pu.hook", spark=spark).load() == 12
