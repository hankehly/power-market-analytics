"""Tests for the generic TSO area-actuals downloader/loader building blocks."""

from __future__ import annotations

import re

import pytest

from power_market_analytics.area_actuals import AreaActualsSource, month_range

SOURCE = AreaActualsSource(
    code="demo",
    url_template="https://example.test/archives/{year:04d}{month:02d}.zip",
    earliest_month=(2022, 4),
    member_re=re.compile(r"(^|/)DEMO_JISEKI_\d{8}\.csv$"),
    accepted_headers=frozenset({"h1", "h2"}),
    default_data_dir="data/demo/area_demand_generation",
)


class TestMonthRange:
    def test_single_month(self):
        assert month_range((2024, 2), (2024, 2)) == [(2024, 2)]

    def test_crosses_year_boundary(self):
        assert month_range((2023, 11), (2024, 2)) == [
            (2023, 11),
            (2023, 12),
            (2024, 1),
            (2024, 2),
        ]

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="after"):
            month_range((2024, 3), (2024, 2))


class TestAreaActualsSource:
    def test_zip_url_formats_year_and_month(self):
        assert SOURCE.zip_url(2025, 7) == "https://example.test/archives/202507.zip"

    def test_zip_name_is_last_url_segment(self):
        assert SOURCE.zip_name(2025, 7) == "202507.zip"

    def test_is_actuals_member_matches_only_daily_actuals(self):
        assert SOURCE.is_actuals_member("DEMO_JISEKI_20250701.csv")
        assert SOURCE.is_actuals_member("nested/DEMO_JISEKI_20250701.csv")
        assert not SOURCE.is_actuals_member("DEMO_YOSOKU_20250701.csv")

    def test_source_is_immutable(self):
        with pytest.raises(AttributeError):
            SOURCE.code = "other"  # type: ignore[misc]


# --------------------------------------------------------------------------- downloader
import datetime  # noqa: E402
import io  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

import requests  # noqa: E402

from power_market_analytics.area_actuals import (  # noqa: E402
    AreaActualsDownloader,
    AreaActualsDownloadError,
)


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "application/zip"):
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Minimal stand-in for requests.Session recording the calls it receives."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        return self.response


class TestAreaActualsDownloader:
    def test_default_data_dir_comes_from_source(self):
        dl = AreaActualsDownloader(SOURCE)
        assert dl.data_dir == Path("data/demo/area_demand_generation")
        assert dl.zip_dir == dl.data_dir / "zip"
        assert dl.csv_dir == dl.data_dir / "csv"

    def test_zip_path_for_uses_source_zip_name(self, tmp_path):
        dl = AreaActualsDownloader(SOURCE, data_dir=tmp_path)
        assert dl.zip_path_for(2025, 7) == tmp_path / "zip" / "202507.zip"

    def test_download_extracts_only_actuals_members_flattened(self, tmp_path):
        payload = make_zip(
            {
                "DEMO_JISEKI_20250702.csv": b"b",
                "202507/DEMO_JISEKI_20250701.csv": b"a",  # nested member is flattened
                "DEMO_YOSOKU_20250701.csv": b"skip",
            }
        )
        session = FakeSession(FakeResponse(payload))
        dl = AreaActualsDownloader(SOURCE, data_dir=tmp_path, timeout=12.5, session=session)

        extracted = dl.download(2025, 7)

        assert session.calls == [("https://example.test/archives/202507.zip", 12.5)]
        assert extracted == [
            tmp_path / "csv" / "DEMO_JISEKI_20250701.csv",
            tmp_path / "csv" / "DEMO_JISEKI_20250702.csv",
        ]
        assert (tmp_path / "csv" / "DEMO_JISEKI_20250701.csv").read_bytes() == b"a"
        assert not (tmp_path / "csv" / "DEMO_YOSOKU_20250701.csv").exists()
        assert (tmp_path / "zip" / "202507.zip").read_bytes() == payload
        assert list((tmp_path / "zip").glob("*.part")) == []

    def test_download_rejects_non_zip_response(self, tmp_path):
        session = FakeSession(FakeResponse(b"<html>maintenance</html>", content_type="text/html"))
        dl = AreaActualsDownloader(SOURCE, data_dir=tmp_path, session=session)
        with pytest.raises(AreaActualsDownloadError, match="zip"):
            dl.download(2025, 7)
        assert not (tmp_path / "zip").exists()

    def test_download_rejects_zip_without_actuals_members(self, tmp_path):
        session = FakeSession(FakeResponse(make_zip({"DEMO_YOSOKU_20250701.csv": b"x"})))
        dl = AreaActualsDownloader(SOURCE, data_dir=tmp_path, session=session)
        with pytest.raises(AreaActualsDownloadError, match="no members matching"):
            dl.download(2025, 7)

    def test_download_propagates_http_errors(self, tmp_path):
        session = FakeSession(FakeResponse(b"", status=404))
        dl = AreaActualsDownloader(SOURCE, data_dir=tmp_path, session=session)
        with pytest.raises(requests.HTTPError):
            dl.download(2025, 7)

    def test_download_all_covers_earliest_month_through_today(self, tmp_path):
        dl = AreaActualsDownloader(SOURCE, data_dir=tmp_path)
        calls: list[tuple[int, int]] = []

        def fake_download(year: int, month: int) -> list[Path]:
            calls.append((year, month))
            return [tmp_path / f"{year}{month:02d}.csv"]

        dl.download = fake_download  # type: ignore[method-assign]
        result = dl.download_all(today=datetime.date(2022, 6, 15))

        assert calls == [(2022, 4), (2022, 5), (2022, 6)]
        assert result == [tmp_path / "202204.csv", tmp_path / "202205.csv", tmp_path / "202206.csv"]


# --------------------------------------------------------------------------- loader
from power_market_analytics.area_actuals import (  # noqa: E402
    AreaActualsCsvLoader,
    sniff_metadata,
)
from power_market_analytics.csv_loader import CsvTableSchema  # noqa: E402

TEPCO_HEADER = "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量"
KANSAI_OLD_HEADER = "日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光"
KANSAI_NEW_HEADER = (
    "DATE,時間コマ,時間帯_自,時間帯_至,エリア総需要量(kWh),エリア総発電量(kWh),エリア風力・太陽光発電量(kWh)"
)
HEADERS = frozenset({TEPCO_HEADER, KANSAI_OLD_HEADER, KANSAI_NEW_HEADER})

TEPCO_FILE = [
    "ファイル更新日,ファイル更新時間,対象年月日",
    "20250716,00:05:04,20250715",
    TEPCO_HEADER,
    "20250715,1,0:00,0:30,15059000,12421000,141000",
    "20250715,2,0:30,1:00,14800000,12300000,120000",
]
KANSAI_OLD_FILE = [
    "実績値（Ａ－１・Ｂ－１・Ｂ－４）",
    "ファイル更新日,ファイル更新時間,対象年月日",
    "20250702,00:13:11,20250701",
    KANSAI_OLD_HEADER,
    "20250701,1,00:00,00:30,7649810,8018589,2717",
    "20250701,2,00:30,01:00,7328856,7904639,2546",
]
KANSAI_NEW_FILE = [
    "ファイル更新日,ファイル更新時間,対象年月日",
    "2025/12/26,00:13:12,2025/12/25",
    KANSAI_NEW_HEADER,
    "2025/12/25,1,00:00,00:30,6786128,7066384,13060",
    "2025/12/25,2,00:30,01:00,6565396,6955317,14754",
]


def write_cp932(path: Path, lines: list[str]) -> Path:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp932"))
    return path


class TestSniffMetadata:
    def test_tepco_layout(self, tmp_path):
        f = write_cp932(tmp_path / "AREA_JISEKI_20250715.csv", TEPCO_FILE)
        assert sniff_metadata(f, HEADERS) == "20250716 00:05:04"

    def test_kansai_old_layout_with_title_line(self, tmp_path):
        f = write_cp932(tmp_path / "20250701_jisseki.csv", KANSAI_OLD_FILE)
        assert sniff_metadata(f, HEADERS) == "20250702 00:13:11"

    def test_kansai_new_layout_with_slashed_dates(self, tmp_path):
        f = write_cp932(tmp_path / "jukyu_jisseki_20251225_06.csv", KANSAI_NEW_FILE)
        assert sniff_metadata(f, HEADERS) == "20251226 00:13:12"

    def test_unpadded_hour_and_trailing_commas_are_normalised(self, tmp_path):
        lines = list(KANSAI_OLD_FILE)
        lines[0] = "実績値（Ａ－１・Ｂ－１・Ｂ－４）,,,,,,"
        lines[2] = "20220328,0:13:11,20220327,,,,"
        f = write_cp932(tmp_path / "20220327_jisseki.csv", lines)
        assert sniff_metadata(f, HEADERS) == "20220328 00:13:11"

    def test_unknown_header_raises(self, tmp_path):
        lines = list(TEPCO_FILE)
        lines[2] = "日付,時間コマ,something,else"
        f = write_cp932(tmp_path / "bad.csv", lines)
        with pytest.raises(ValueError, match="column header"):
            sniff_metadata(f, HEADERS)

    def test_unparsable_update_timestamp_raises(self, tmp_path):
        lines = list(TEPCO_FILE)
        lines[1] = "yesterday,noon,20250715"
        f = write_cp932(tmp_path / "bad.csv", lines)
        with pytest.raises(ValueError, match="update timestamp"):
            sniff_metadata(f, HEADERS)


CONTRACT = CsvTableSchema.model_validate(
    {
        "read_options": {"encoding": "windows-31j"},
        "grain": ["target_date", "time_code"],
        "columns": [
            {"name": "target_date", "source": "_c0", "type": "date", "format": "yyyyMMdd", "nullable": False},
            {"name": "time_code", "source": "_c1", "type": "int", "nullable": False},
            {"name": "period_start_time", "source": "_c2", "type": "string", "nullable": False},
            {"name": "period_end_time", "source": "_c3", "type": "string", "nullable": False},
            {"name": "demand_kwh", "source": "_c4", "type": "bigint", "nullable": False},
            {"name": "generation_kwh", "source": "_c5", "type": "bigint", "nullable": False},
            {"name": "wind_solar_generation_kwh", "source": "_c6", "type": "bigint", "nullable": False},
            {
                "name": "file_updated_at",
                "source": "__file_updated_at",
                "type": "timestamp",
                "format": "yyyyMMdd HH:mm:ss",
                "nullable": False,
            },
        ],
    }
)

LOADER_SOURCE = AreaActualsSource(
    code="demo",
    url_template="https://example.test/{year}{month:02d}.zip",
    earliest_month=(2022, 4),
    member_re=re.compile(r"\.csv$"),
    accepted_headers=HEADERS,
    default_data_dir="data/demo",
)


class TestAreaActualsCsvLoader:
    def test_requires_a_source(self, spark, tmp_path):
        with pytest.raises(ValueError, match="source"):
            AreaActualsCsvLoader(CONTRACT, tmp_path, "t", spark=spark)

    def test_loads_all_layouts_positionally_into_one_table(self, spark, tmp_path):
        write_cp932(tmp_path / "AREA_JISEKI_20250715.csv", TEPCO_FILE)
        write_cp932(tmp_path / "20250701_jisseki.csv", KANSAI_OLD_FILE)
        write_cp932(tmp_path / "jukyu_jisseki_20251225_06.csv", KANSAI_NEW_FILE)
        loader = AreaActualsCsvLoader(
            CONTRACT, tmp_path, "test_area.actuals", spark=spark, source=LOADER_SOURCE
        )

        n_rows = loader.load()

        assert n_rows == 6
        rows = {
            (r.target_date.isoformat(), r.time_code): r
            for r in spark.table("test_area.actuals").collect()
        }
        assert set(rows) == {
            ("2025-07-15", 1),
            ("2025-07-15", 2),
            ("2025-07-01", 1),
            ("2025-07-01", 2),
            ("2025-12-25", 1),
            ("2025-12-25", 2),
        }
        old = rows[("2025-07-01", 1)]
        assert (old.demand_kwh, old.generation_kwh, old.wind_solar_generation_kwh) == (
            7649810,
            8018589,
            2717,
        )
        assert old.period_start_time == "00:00" and old.period_end_time == "00:30"
        assert old.file_updated_at.isoformat() == "2025-07-02T00:13:11"
        new = rows[("2025-12-25", 2)]
        assert new.demand_kwh == 6565396
        assert new.file_updated_at.isoformat() == "2025-12-26T00:13:12"
        assert rows[("2025-07-15", 1)].period_start_time == "0:00"

    def test_unknown_header_fails_the_load(self, spark, tmp_path):
        lines = list(TEPCO_FILE)
        lines[2] = "日付,時間コマ,something,else"
        write_cp932(tmp_path / "bad.csv", lines)
        loader = AreaActualsCsvLoader(
            CONTRACT, tmp_path, "test_area.bad", spark=spark, source=LOADER_SOURCE
        )
        with pytest.raises(ValueError, match="column header"):
            loader.load()
