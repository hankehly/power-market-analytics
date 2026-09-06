"""関西電力送配電 でんき予報 hourly 電力使用実績: spec, downloader, contract and loader."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from power_market_analytics.kansai.power_usage import (
    HOURLY_HEADER_2016,
    HOURLY_HEADER_2019,
    HOURLY_HEADER_2025,
    KANSAI_POWER_USAGE,
    MISSING_DAY,
    SUPPLY_CAPACITY_FROM,
    KansaiPowerUsageCsvLoader,
    KansaiPowerUsageDownloader,
)

from power_market_analytics.area_actuals import AreaActualsDownloader, AreaActualsDownloadError
from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.power_usage import (
    CORRECTION_MARKER,
    PowerUsageCsvLoader,
    PowerUsageSource,
)
from tests.support import REPO_ROOT
from tests.test_area_actuals import FakeResponse, FakeSession, make_zip
from tests.test_power_usage import daily_lines, hourly_rows, padded, write_lines_cp932

CONTRACT_PATH = REPO_ROOT / "conf/schemas/kansai_power_usage_hourly.yaml"


def member(date: str, updated: str, header: str = HOURLY_HEADER_2019) -> bytes:
    return ("\r\n".join(daily_lines(date, updated, header)) + "\r\n").encode("cp932")


class TestKansaiPowerUsageSource:
    def test_is_a_power_usage_source_for_the_yamasou_archive(self):
        assert isinstance(KANSAI_POWER_USAGE, PowerUsageSource)
        assert KANSAI_POWER_USAGE.code == "kansai_power_usage"
        assert (
            KANSAI_POWER_USAGE.zip_url(2016, 4)
            == "https://www.kansai-td.co.jp/yamasou/201604_jisseki.zip"
        )
        assert KANSAI_POWER_USAGE.zip_name(2026, 9) == "202609_jisseki.zip"
        assert KANSAI_POWER_USAGE.earliest_month == (2016, 4)
        assert KANSAI_POWER_USAGE.default_data_dir == "data/kansai/power_usage"

    def test_matches_both_member_naming_generations(self):
        assert KANSAI_POWER_USAGE.is_actuals_member("20160401_juyo1_kansai.csv")  # through 2025-11
        assert KANSAI_POWER_USAGE.is_actuals_member("nested/20251130_juyo1_kansai.csv")
        assert KANSAI_POWER_USAGE.is_actuals_member("juyo_06_20251201.csv")  # from 2025-12
        # The A-1 feed's members (its archives share the name YYYYMM_jisseki.zip).
        assert not KANSAI_POWER_USAGE.is_actuals_member("20250701_jisseki.csv")
        assert not KANSAI_POWER_USAGE.is_actuals_member("jukyu_jisseki_20251225_06.csv")

    def test_accepts_the_three_hourly_layouts(self):
        assert HOURLY_HEADER_2016 == "DATE,TIME,当日実績(万kW),予想値(万kW),使用率(%)"
        assert HOURLY_HEADER_2019 == HOURLY_HEADER_2016 + ",供給力想定値(万kW)"
        assert HOURLY_HEADER_2025 == HOURLY_HEADER_2016 + ",供給力(万kW)"
        assert KANSAI_POWER_USAGE.accepted_headers == frozenset(
            {HOURLY_HEADER_2016, HOURLY_HEADER_2019, HOURLY_HEADER_2025}
        )
        assert SUPPLY_CAPACITY_FROM == datetime.date(2019, 9, 12)

    def test_every_file_holds_one_date_and_archives_hold_finished_days_only(self):
        assert KANSAI_POWER_USAGE.multi_day_headers == frozenset()
        assert KANSAI_POWER_USAGE.archive_includes_current_day is False

    def test_the_one_known_missing_day(self):
        assert MISSING_DAY == datetime.date(2024, 3, 31)
        assert KANSAI_POWER_USAGE.known_missing_days == frozenset({MISSING_DAY})


class TestKansaiPowerUsageDownloader:
    def test_is_bound_to_the_source_and_its_data_dir(self, tmp_path):
        dl = KansaiPowerUsageDownloader()
        assert isinstance(dl, AreaActualsDownloader)
        assert dl.source is KANSAI_POWER_USAGE
        assert dl.data_dir == Path("data/kansai/power_usage")
        assert KansaiPowerUsageDownloader(data_dir=tmp_path).csv_dir == tmp_path / "csv"

    def test_download_extracts_members_of_either_generation(self, tmp_path):
        archive = make_zip(
            {
                "juyo_06_20251201.csv": member("2025/12/1", "2025/12/2 1:10"),
                "20251202_juyo1_kansai.csv": member("2025/12/2", "2025/12/3 1:10"),
                "readme.txt": b"skip",
            }
        )
        dl = KansaiPowerUsageDownloader(
            data_dir=tmp_path, timeout=9.0, session=FakeSession(FakeResponse(archive))
        )

        extracted = dl.download(2025, 12, today=datetime.date(2025, 12, 3))

        assert extracted == [
            tmp_path / "csv" / "20251202_juyo1_kansai.csv",
            tmp_path / "csv" / "juyo_06_20251201.csv",
        ]
        assert not (tmp_path / "csv" / "readme.txt").exists()

    def test_settled_march_2024_passes_without_the_31st(self, tmp_path):
        members = {
            f"202403{d:02d}_juyo1_kansai.csv": member(f"2024/3/{d}", f"2024/3/{d + 1} 1:10")
            for d in range(1, 31)
        }
        dl = KansaiPowerUsageDownloader(
            data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )

        assert len(dl.download(2024, 3, today=datetime.date(2024, 5, 1))) == 30

    def test_any_other_missing_day_in_a_settled_month_fails(self, tmp_path):
        members = {
            f"202403{d:02d}_juyo1_kansai.csv": member(f"2024/3/{d}", f"2024/3/{d + 1} 1:10")
            for d in range(1, 30)
        }
        dl = KansaiPowerUsageDownloader(
            data_dir=tmp_path, session=FakeSession(FakeResponse(make_zip(members)))
        )

        with pytest.raises(AreaActualsDownloadError, match="20240330"):
            dl.download(2024, 3, today=datetime.date(2024, 5, 1))

    def test_download_all_starts_at_2016_04(self, tmp_path):
        dl = KansaiPowerUsageDownloader(data_dir=tmp_path)
        calls: list[tuple[int, int]] = []
        dl.download = lambda year, month, today: calls.append((year, month)) or []  # type: ignore[method-assign]

        assert dl.download_all(today=datetime.date(2016, 6, 15)) == []
        assert calls == [(2016, 4), (2016, 5), (2016, 6)]


class TestKansaiContract:
    def test_grain_columns_and_nullability(self):
        schema = CsvTableSchema.from_yaml(CONTRACT_PATH)

        assert schema.grain == ["target_date", "hour_start"]
        assert [(c.name, c.source) for c in schema.columns] == [
            ("target_date", "__target_date"),
            ("hour_start", "__hour_start"),
            ("demand_mankw", "__demand_mankw"),
            ("forecast_mankw", "__forecast_mankw"),
            ("usage_rate_pct", "__usage_rate_pct"),
            ("supply_capacity_mankw", "__supply_capacity_mankw"),
            ("file_updated_at", "__file_updated_at"),
            ("source_file", "__source_file"),
        ]
        assert {c.name for c in schema.columns if not c.nullable} == {
            "target_date",
            "hour_start",
            "demand_mankw",
            "file_updated_at",
            "source_file",
        }

    def test_matches_the_tepco_contract_column_for_column(self):
        tepco = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/tepco_power_usage_hourly.yaml")
        kansai = CsvTableSchema.from_yaml(CONTRACT_PATH)
        assert kansai.grain == tepco.grain
        assert [c.model_dump() for c in kansai.columns] == [c.model_dump() for c in tepco.columns]


class TestKansaiPowerUsageCsvLoader:
    def test_is_bound_to_the_source(self):
        assert issubclass(KansaiPowerUsageCsvLoader, PowerUsageCsvLoader)
        assert KansaiPowerUsageCsvLoader.source is KANSAI_POWER_USAGE

    def test_loads_every_layout_and_the_padded_corrected_file(self, spark, tmp_path):
        write_lines_cp932(
            tmp_path / "20160401_juyo1_kansai.csv",
            daily_lines("2016/4/1", "2016/4/2 1:10", HOURLY_HEADER_2016),
        )
        corrected = hourly_rows("2016/4/24", HOURLY_HEADER_2016, base=1267)
        corrected.insert(
            1, f"{CORRECTION_MARKER},,1212,,65,4/25 システム不具合による数値誤りのため修正"
        )
        write_lines_cp932(
            tmp_path / "20160424_juyo1_kansai.csv",
            padded(daily_lines("2016/4/24", "2016/4/25 1:10", HOURLY_HEADER_2016, corrected)),
        )
        write_lines_cp932(
            tmp_path / "20190912_juyo1_kansai.csv",
            daily_lines("2019/9/12", "2019/9/13 1:10", HOURLY_HEADER_2019),
        )
        write_lines_cp932(
            tmp_path / "juyo_06_20251225.csv",
            daily_lines("2025/12/25", "2025/12/26 1:10", HOURLY_HEADER_2025),
        )
        loader = KansaiPowerUsageCsvLoader(
            CsvTableSchema.from_yaml(CONTRACT_PATH),
            tmp_path,
            "test_kansai.power_usage",
            spark=spark,
        )

        assert loader.load() == 96

        table = spark.table("test_kansai.power_usage")
        assert dict(table.dtypes) == {
            "target_date": "date",
            "hour_start": "int",
            "demand_mankw": "double",
            "forecast_mankw": "double",
            "usage_rate_pct": "double",
            "supply_capacity_mankw": "double",
            "file_updated_at": "timestamp",
            "source_file": "string",
        }
        rows = {(r.target_date.isoformat(), r.hour_start): r for r in table.collect()}
        first = rows[("2016-04-01", 0)]
        assert (
            first.demand_mankw,
            first.forecast_mankw,
            first.usage_rate_pct,
            first.supply_capacity_mankw,
        ) == (1200.0, 1190.0, 60.0, None)
        assert first.file_updated_at.isoformat() == "2016-04-02T01:10:00"
        assert first.source_file == "20160401_juyo1_kansai.csv"
        fixed = rows[("2016-04-24", 0)]
        assert (fixed.demand_mankw, fixed.forecast_mankw, fixed.usage_rate_pct) == (
            1212.0,
            1257.0,
            65.0,
        )
        assert rows[("2016-04-24", 1)].demand_mankw == 1268.0
        assert rows[("2019-09-12", 0)].supply_capacity_mankw == 2000.0
        latest = rows[("2025-12-25", 23)]
        assert latest.supply_capacity_mankw == 2023.0
        assert latest.source_file == "juyo_06_20251225.csv"
