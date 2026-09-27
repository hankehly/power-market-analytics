"""Tests for the JMA climatological normals (平年値) module.

The vintage config and the page's version heading; the downloader against a fake
session serving a two-station archive built in memory; the positional loader on
files written into ``tmp_path`` in the exact 308-byte daily layout, loaded through
the real ``conf/schemas/jma_normal_surface_daily.yaml`` contract.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
import requests

from power_market_analytics.ingestion.jma.normals import (
    NORMALS_PAGE_URL,
    STATION_INDEX_MEMBER,
    VINTAGES,
    JmaNormalsCsvLoader,
    JmaNormalsDownloader,
    JmaNormalsDownloadError,
    NormalsVintage,
    parse_versions,
    vintage_for_year,
)
from power_market_analytics.ingestion.loader import CsvTableSchema
from tests.support import REPO_ROOT

# --------------------------------------------------------------------------- vintages


class TestVintages:
    def test_one_configured_period(self):
        assert VINTAGES == (
            NormalsVintage(
                period_start_year=1991,
                period_end_year=2020,
                in_use_since=datetime.date(2021, 5, 19),
                zip_url=(
                    "https://www.data.jma.go.jp/stats/data/mdrr/normal/2020/data/normal_surface.zip"
                ),
                expected_station_count=157,
            ),
        )

    def test_lookup_by_period_end_year(self):
        assert vintage_for_year(2020) is VINTAGES[0]

    def test_unknown_year_raises(self):
        with pytest.raises(KeyError, match="2030"):
            vintage_for_year(2030)

    def test_vintage_is_immutable(self):
        with pytest.raises(AttributeError):
            VINTAGES[0].zip_url = "x"  # type: ignore[misc]


# --------------------------------------------------------------------------- the page


class TestParseVersions:
    def test_the_heading_as_jma_writes_it_mixed_brackets(self):
        assert parse_versions("<h2>2020年平年値（第5版)</h2>") == {2020: "5"}

    def test_full_width_brackets_and_a_dotted_version(self):
        assert parse_versions("2020年平年値（第4.0.1版）") == {2020: "4.0.1"}

    def test_two_periods_on_one_page(self):
        html = "<h2>2030年平年値（第1版）</h2> … <h2>2020年平年値（第5版）</h2>"
        assert parse_versions(html) == {2030: "1", 2020: "5"}

    def test_news_items_do_not_match(self):
        # The notices read 2020年平年値の第5版を公開しました — no bracket, not a heading.
        assert parse_versions("2020年平年値の第5版を公開しました。") == {}

    def test_no_heading(self):
        assert parse_versions("<html>maintenance</html>") == {}


# --------------------------------------------------------------------------- fixtures

V2020 = vintage_for_year(2020)
#: The real vintage with the station count of the two-station test archive.
DEMO = dataclasses.replace(V2020, expected_station_count=2)
PAGE_HTML = "<h2>2020年平年値（第5版)</h2>".encode("utf-8")
STATION_INDEX_BYTES = (
    "Station Number,Station Name,Station Name,Station Name,Latitude,Latitude,Longitude,"
    "Longitude,Altitude\r\n,(Kanji),(Kana),,(degree),(minutes),(degree),(minutes),(m)\r\n"
    "47662,東京　　　　　　　　,ﾄｳｷｮｳ          ,TOKYO                         ,35,41.5,139,45.0, 0025.2\r\n"
).encode("cp932")


def daily_line(
    station: str,
    element: str,
    month: int,
    cells: list[tuple[int, int]],
    n_years: int = 30,
    start: int = 1991,
    end: int = 2020,
    kind: int = 15,
) -> str:
    """One line as JMA writes it: 7 key fields, then 31 (value, flag) pairs, values in 6."""
    assert len(cells) == 31
    head = f"{kind:>2},{station},{element},{n_years:>2},{start:>4},{end:>4},{month:>2}"
    return head + "," + ",".join(f"{value:>6},{flag}" for value, flag in cells)


def month_cells(month: int, values: list[int], flag: int = 8) -> list[tuple[int, int]]:
    """``values`` for the month's days, padded with 0,0 to 31 cells as JMA pads."""
    days = calendar.monthrange(2020, month)[1]
    assert len(values) == days
    return [(v, flag) for v in values] + [(0, 0)] * (31 - days)


def daily_file_text(station: str, elements: tuple[str, ...] = ("0500", "7100")) -> str:
    """A daily file: 12 months per element, value = 10 × month + day, flag 8."""
    lines = []
    for element in elements:
        for month in range(1, 13):
            days = calendar.monthrange(2020, month)[1]
            values = [10 * month + day for day in range(1, days + 1)]
            lines.append(daily_line(station, element, month, month_cells(month, values)))
    return "\n".join(lines) + "\n"


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def demo_zip(stations: tuple[str, ...] = ("47662", "47772"), index: bool = True) -> bytes:
    """A two-station archive with a monthly member the downloader must ignore."""
    members = {
        f"normal_surface/daily/nml_sfc_d_{s}.csv": daily_file_text(s).encode("ascii")
        for s in stations
    }
    members["normal_surface/monthly/nml_sfc_m_47662.csv"] = b"11,47662,0100,30,1991,2020\n"
    if index:
        members[STATION_INDEX_MEMBER] = STATION_INDEX_BYTES
    return make_zip(members)


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "application/zip"):
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Stand-in for requests.Session serving canned responses by URL."""

    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        if url not in self.responses:
            return FakeResponse(b"not found", status=404, content_type="text/html")
        return self.responses[url]


def demo_session(zip_bytes: bytes | None = None, page: bytes = PAGE_HTML) -> FakeSession:
    return FakeSession(
        {
            NORMALS_PAGE_URL: FakeResponse(page, content_type="text/html"),
            DEMO.zip_url: FakeResponse(demo_zip() if zip_bytes is None else zip_bytes),
        }
    )


def files_under(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- downloader


class TestDownloaderPaths:
    def test_defaults(self):
        dl = JmaNormalsDownloader()
        assert dl.data_dir == Path("data/jma/normals")
        assert dl.timeout == 60.0
        assert isinstance(dl.session, requests.Session)
        assert dl.page_url == NORMALS_PAGE_URL

    def test_paths_of_a_period(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path)
        assert dl.vintage_dir(V2020) == tmp_path / "2020"
        assert dl.zip_path_for(V2020) == tmp_path / "2020/zip/normal_surface.zip"
        assert dl.daily_dir(V2020) == tmp_path / "2020/csv/daily"
        assert dl.station_index_path_for(V2020) == tmp_path / "2020/csv/surface_station_index.csv"
        assert dl.manifest_path_for(V2020) == tmp_path / "2020/manifest.json"


class TestReadVersion:
    def test_returns_the_periods_version(self, tmp_path):
        session = demo_session()
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session, timeout=12.5)
        assert dl.read_version(DEMO) == "5"
        assert session.calls == [(NORMALS_PAGE_URL, 12.5)]

    def test_a_page_without_the_period_names_the_periods_it_has(self, tmp_path):
        session = demo_session(page="<h2>2030年平年値（第1版）</h2>".encode("utf-8"))
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(JmaNormalsDownloadError, match=r"1991–2020.*2030.*add a vintage"):
            dl.read_version(DEMO)

    def test_a_page_with_no_heading(self, tmp_path):
        session = demo_session(page=b"<html>maintenance</html>")
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(JmaNormalsDownloadError, match="periods on the page: none"):
            dl.read_version(DEMO)

    def test_http_errors_propagate(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(requests.HTTPError):
            dl.read_version(DEMO)


class TestDownloadVintage:
    def test_writes_the_zip_the_daily_files_the_index_and_the_manifest(self, tmp_path):
        session = demo_session()
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)

        paths = dl.download_vintage(DEMO)

        assert [url for url, _ in session.calls] == [NORMALS_PAGE_URL, DEMO.zip_url]
        assert paths == [
            tmp_path / "2020/csv/daily/nml_sfc_d_47662.csv",
            tmp_path / "2020/csv/daily/nml_sfc_d_47772.csv",
        ]
        assert (tmp_path / "2020/zip/normal_surface.zip").read_bytes() == demo_zip()
        assert paths[0].read_bytes() == daily_file_text("47662").encode("ascii")
        assert (tmp_path / "2020/csv/surface_station_index.csv").read_bytes() == STATION_INDEX_BYTES
        manifest = json.loads((tmp_path / "2020/manifest.json").read_text(encoding="utf-8"))
        assert manifest == {
            "period_start_year": 1991,
            "period_end_year": 2020,
            "version": "5",
            "in_use_since": "2021-05-19",
            "downloaded_at_utc": manifest["downloaded_at_utc"],
            "zip_url": DEMO.zip_url,
            "zip_sha256": hashlib.sha256(demo_zip()).hexdigest(),
            "zip_bytes": len(demo_zip()),
            "daily_file_count": 2,
        }
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest["downloaded_at_utc"])
        assert list(tmp_path.rglob("*.part")) == []
        assert sorted(p.name for p in (tmp_path / "2020/csv/daily").iterdir()) == [
            "nml_sfc_d_47662.csv",
            "nml_sfc_d_47772.csv",
        ]

    def test_every_call_downloads_again(self, tmp_path):
        session = demo_session()
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        dl.download_vintage(DEMO)
        dl.download_vintage(DEMO)
        assert [url for url, _ in session.calls] == [NORMALS_PAGE_URL, DEMO.zip_url] * 2

    def test_a_daily_file_the_archive_no_longer_holds_is_removed(self, tmp_path):
        stale = tmp_path / "2020/csv/daily/nml_sfc_d_99999.csv"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session())
        dl.download_vintage(DEMO)
        assert not stale.exists()

    @pytest.mark.parametrize(
        "content, message",
        [
            (b"<html>maintenance</html>", "not a zip archive"),
            (demo_zip(index=False), "surface_station_index"),
            (demo_zip(stations=("47662",)), "expected 2 daily files, found 1"),
            (demo_zip(stations=("47662", "47772", "47401")), "expected 2 daily files, found 3"),
            (
                make_zip(
                    {
                        "daily/nml_sfc_d_47662.csv": b"x",
                        "daily/nml_sfc_d_47772.csv": b"x",
                        STATION_INDEX_MEMBER: STATION_INDEX_BYTES,
                    }
                ),
                "expected 2 daily files, found 0",
            ),
        ],
    )
    def test_a_bad_archive_is_rejected_and_nothing_is_written(self, tmp_path, content, message):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session(zip_bytes=content))
        with pytest.raises(JmaNormalsDownloadError, match=message):
            dl.download_vintage(DEMO)
        assert not (tmp_path / "2020").exists()

    def test_a_bad_archive_leaves_the_previous_download_intact(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session())
        dl.download_vintage(DEMO)
        before = files_under(tmp_path / "2020")
        dl.session = demo_session(zip_bytes=b"<html>maintenance</html>")
        with pytest.raises(JmaNormalsDownloadError):
            dl.download_vintage(DEMO)
        assert files_under(tmp_path / "2020") == before

    def test_a_failure_while_writing_leaves_no_manifest(self, tmp_path, monkeypatch):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session())
        dl.download_vintage(DEMO)  # a complete first download, manifest present
        real = JmaNormalsDownloader._atomic_write

        def failing(path, write):
            if path.name == "surface_station_index.csv":
                raise OSError("disk full")
            real(path, write)

        monkeypatch.setattr(JmaNormalsDownloader, "_atomic_write", staticmethod(failing))
        with pytest.raises(OSError, match="disk full"):
            dl.download_vintage(DEMO)
        assert not (tmp_path / "2020/manifest.json").exists()
        assert (tmp_path / "2020/csv/daily/nml_sfc_d_47662.csv").exists()
        assert list(tmp_path.rglob("*.part")) == []

    def test_http_error_on_the_zip_writes_nothing(self, tmp_path):
        session = FakeSession({NORMALS_PAGE_URL: FakeResponse(PAGE_HTML, content_type="text/html")})
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(requests.HTTPError):
            dl.download_vintage(DEMO)
        assert not (tmp_path / "2020").exists()


class TestAtomicWrite:
    def test_a_failing_write_removes_the_partial_file_and_keeps_the_old_one(self, tmp_path):
        dest = tmp_path / "csv" / "manifest.json"
        dest.parent.mkdir()
        dest.write_bytes(b"old")

        def write(partial: Path) -> None:
            partial.write_bytes(b"half")
            raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            JmaNormalsDownloader._atomic_write(dest, write)

        assert dest.read_bytes() == b"old"
        assert not dest.with_name("manifest.json.part").exists()


class TestDownloadAll:
    def test_defaults_to_every_configured_vintage(self, tmp_path, monkeypatch):
        seen: list[int] = []
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        monkeypatch.setattr(
            dl,
            "download_vintage",
            lambda vintage: seen.append(vintage.period_end_year) or [tmp_path / "x.csv"],
        )
        assert dl.download_all() == [tmp_path / "x.csv"]
        assert seen == [2020]

    def test_years_select_the_vintages(self, tmp_path, monkeypatch):
        seen: list[int] = []
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        monkeypatch.setattr(
            dl, "download_vintage", lambda vintage: seen.append(vintage.period_end_year) or []
        )
        dl.download_all(years=[2020])
        assert seen == [2020]

    def test_unknown_year_raises(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(KeyError, match="2030"):
            dl.download_all(years=[2030])


# --------------------------------------------------------------------------- loader

CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml")

MANIFEST_2020 = {
    "period_start_year": 1991,
    "period_end_year": 2020,
    "version": "5",
    "in_use_since": "2021-05-19",
    "downloaded_at_utc": "2026-09-27T10:00:00Z",
    "zip_url": V2020.zip_url,
    "zip_sha256": "0" * 64,
    "zip_bytes": 1,
    "daily_file_count": 2,
}
MANIFEST_2030 = {
    **MANIFEST_2020,
    "period_start_year": 2001,
    "period_end_year": 2030,
    "version": "1",
    "in_use_since": "2031-05-20",
}


def write_period(root: Path, year: int, files: dict[str, str], manifest: dict | None) -> Path:
    """Write daily files under ``root/<year>/csv/daily/`` and the manifest next to them."""
    daily = root / str(year) / "csv" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (daily / name).write_bytes(text.encode("ascii"))
    if manifest is not None:
        (root / str(year) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return daily


def rows_of(spark, table: str) -> dict[tuple[int, str, str, int], dict]:
    return {
        (r["normals_period_end_year"], r["station_number"], r["element_code"], r["month"]): (
            r.asDict()
        )
        for r in spark.table(table).collect()
    }


class TestContract:
    def test_grain_and_read_options(self):
        assert CONTRACT.grain == [
            "normals_period_end_year",
            "station_number",
            "element_code",
            "month",
        ]
        assert CONTRACT.read_options == {
            "encoding": "windows-31j",
            "ignoreLeadingWhiteSpace": "true",
        }

    def test_columns_in_order(self):
        names = [c.name for c in CONTRACT.columns]
        assert names[:7] == [
            "normal_kind",
            "station_number",
            "element_code",
            "n_years",
            "statistic_start_year",
            "statistic_end_year",
            "month",
        ]
        assert names[7:69] == [
            f"{kind}_d{d:02d}" for d in range(1, 32) for kind in ("value", "flag")
        ]
        assert names[69:] == [
            "normals_period_start_year",
            "normals_period_end_year",
            "normals_version",
            "in_use_since",
            "source_file",
        ]
        assert [c.source_name for c in CONTRACT.columns[:69]] == [f"_c{i}" for i in range(69)]
        assert all(not c.nullable for c in CONTRACT.columns)
        types = {c.name: c.type for c in CONTRACT.columns}
        assert (types["station_number"], types["element_code"], types["in_use_since"]) == (
            "string",
            "string",
            "date",
        )


@pytest.fixture(scope="module")
def loaded(spark, tmp_path_factory):
    """One load of two stations × two elements for every test of ``TestLoad``."""
    root = tmp_path_factory.mktemp("normals")
    write_period(
        root,
        2020,
        {
            "nml_sfc_d_47662.csv": daily_file_text("47662"),
            "nml_sfc_d_47772.csv": daily_file_text("47772"),
        },
        MANIFEST_2020,
    )
    loader = JmaNormalsCsvLoader(CONTRACT, root, "test_jma_normals.loaded", spark=spark)
    return loader.load(), rows_of(spark, "test_jma_normals.loaded")


class TestLoad:
    def test_row_count_and_grain(self, loaded):
        n_rows, rows = loaded
        assert n_rows == 48  # 2 stations × 2 elements × 12 months
        assert len(rows) == 48

    def test_cells_are_read_by_position(self, loaded):
        _, rows = loaded
        feb = rows[(2020, "47662", "0500", 2)]
        assert (feb["normal_kind"], feb["n_years"]) == (15, 30)
        assert (feb["statistic_start_year"], feb["statistic_end_year"]) == (1991, 2020)
        assert (feb["value_d01"], feb["flag_d01"]) == (21, 8)
        assert (feb["value_d29"], feb["flag_d29"]) == (49, 8)
        assert (feb["value_d30"], feb["flag_d30"], feb["value_d31"], feb["flag_d31"]) == (
            0,
            0,
            0,
            0,
        )
        jan = rows[(2020, "47772", "7100", 1)]
        assert (jan["value_d31"], jan["flag_d31"]) == (41, 8)

    def test_manifest_values_are_injected(self, loaded):
        _, rows = loaded
        r = rows[(2020, "47662", "0500", 1)]
        assert (
            r["normals_period_start_year"],
            r["normals_period_end_year"],
            r["normals_version"],
            r["in_use_since"],
            r["source_file"],
        ) == (1991, 2020, "5", datetime.date(2021, 5, 19), "nml_sfc_d_47662.csv")

    def test_table_types_follow_the_contract(self, spark, loaded):
        schema = {
            f.name: f.dataType.simpleString() for f in spark.table("test_jma_normals.loaded").schema
        }
        assert len(schema) == 74
        assert (schema["element_code"], schema["in_use_since"], schema["value_d31"]) == (
            "string",
            "date",
            "int",
        )

    def test_negative_values_parse(self, spark, tmp_path):
        january = month_cells(1, [-40 + d for d in range(1, 32)])
        lines = []
        for m in range(1, 13):
            cells = january if m == 1 else month_cells(m, [0] * calendar.monthrange(2020, m)[1])
            lines.append(daily_line("47412", "0500", m, cells))
        write_period(
            tmp_path, 2020, {"nml_sfc_d_47412.csv": "\n".join(lines) + "\n"}, MANIFEST_2020
        )
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.negative", spark=spark)
        assert loader.load() == 12
        rows = rows_of(spark, "test_jma_normals.negative")
        assert rows[(2020, "47412", "0500", 1)]["value_d01"] == -39

    def test_two_periods_load_side_by_side(self, spark, tmp_path):
        files = {"nml_sfc_d_47662.csv": daily_file_text("47662")}
        write_period(tmp_path, 2020, files, MANIFEST_2020)
        write_period(tmp_path, 2030, files, MANIFEST_2030)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.two", spark=spark)
        assert loader.load() == 48
        rows = rows_of(spark, "test_jma_normals.two")
        assert rows[(2030, "47662", "0500", 1)]["normals_version"] == "1"
        assert rows[(2030, "47662", "0500", 1)]["in_use_since"] == datetime.date(2031, 5, 20)
        assert rows[(2020, "47662", "0500", 1)]["normals_period_start_year"] == 1991


class TestFileResolution:
    def test_directory_root_finds_every_periods_daily_files(self, spark, tmp_path):
        write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": "x"}, MANIFEST_2020)
        write_period(tmp_path, 2030, {"nml_sfc_d_47662.csv": "x"}, MANIFEST_2030)
        (tmp_path / "2020/zip").mkdir()
        (tmp_path / "2020/zip/normal_surface.zip").write_bytes(b"not a csv")
        (tmp_path / "2020/csv/surface_station_index.csv").write_bytes(b"not loaded")
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "t", spark=spark)
        assert loader._resolve_files() == [
            str(tmp_path / "2020/csv/daily/nml_sfc_d_47662.csv"),
            str(tmp_path / "2030/csv/daily/nml_sfc_d_47662.csv"),
        ]

    def test_glob_and_single_file(self, spark, tmp_path):
        daily = write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": "x"}, MANIFEST_2020)
        by_glob = JmaNormalsCsvLoader(CONTRACT, daily / "*.csv", "t", spark=spark)
        assert by_glob._resolve_files() == [str(daily / "nml_sfc_d_47662.csv")]
        one = JmaNormalsCsvLoader(CONTRACT, daily / "nml_sfc_d_47662.csv", "t", spark=spark)
        assert one._resolve_files() == [str(daily / "nml_sfc_d_47662.csv")]

    def test_manifest_path_is_two_levels_up(self):
        assert JmaNormalsCsvLoader.manifest_path_for(
            "/data/2020/csv/daily/nml_sfc_d_47662.csv"
        ) == Path("/data/2020/manifest.json")

    def test_no_files_raises(self, spark, tmp_path):
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "t", spark=spark)
        with pytest.raises(FileNotFoundError, match="No daily normals files"):
            loader.load()


def _replace_line(text: str, element: str, month: int, line: str) -> str:
    lines = text.splitlines()
    for i, existing in enumerate(lines):
        if existing.split(",")[2] == element and int(existing.split(",")[6]) == month:
            lines[i] = line
            return "\n".join(lines) + "\n"
    raise AssertionError("no such line")


GOOD = daily_file_text("47662")
JAN = month_cells(1, [10 + d for d in range(1, 32)])
DECEMBER_0500 = daily_line("47662", "0500", 12, month_cells(12, [120 + d for d in range(1, 32)]))


class TestValidationFailsBeforeWriting:
    @pytest.mark.parametrize(
        "text, message",
        [
            (
                _replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 1, JAN, kind=16)),
                "first field not 15",
            ),
            (
                _replace_line(GOOD, "0500", 1, daily_line("47663", "0500", 1, JAN)),
                "station number not the file's",
            ),
            (
                _replace_line(GOOD, "0500", 1, daily_line("47662", "05", 1, JAN)),
                "element code not four digits",
            ),
            (
                _replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 13, JAN)),
                "month not 1-12",
            ),
            (
                _replace_line(
                    GOOD,
                    "0500",
                    1,
                    daily_line("47662", "0500", 1, JAN).replace("    11,8", "     x,8"),
                ),
                "value cell not an integer",
            ),
            (
                _replace_line(
                    GOOD,
                    "0500",
                    1,
                    daily_line("47662", "0500", 1, JAN).replace("    11,8", "    11,9"),
                ),
                r"flag not in \['0', '5', '6', '7', '8'\]",
            ),
            # a short line: the last two (value, flag) pairs missing
            (
                _replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 1, JAN)[:-18]),
                "value cell not an integer",
            ),
            # December of 0500 dropped: 11 months
            (GOOD.replace(DECEMBER_0500 + "\n", ""), "element 0500 has 11 row"),
        ],
        ids=[
            "kind-16",
            "station-of-another-file",
            "element-two-digits",
            "month-13",
            "value-not-integer",
            "flag-9",
            "short-line",
            "eleven-months",
        ],
    )
    def test_bad_rows_name_the_file(self, spark, tmp_path, text, message):
        write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": text}, MANIFEST_2020)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.bad", spark=spark)
        with pytest.raises(ValueError, match=message) as excinfo:
            loader.load()
        assert "nml_sfc_d_47662.csv" in str(excinfo.value)
        assert not spark.catalog.tableExists("test_jma_normals.bad")

    def test_files_with_different_element_sets_are_rejected(self, spark, tmp_path):
        write_period(
            tmp_path,
            2020,
            {
                "nml_sfc_d_47662.csv": daily_file_text("47662"),
                "nml_sfc_d_47772.csv": daily_file_text("47772", elements=("0500", "7100", "0600")),
            },
            MANIFEST_2020,
        )
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.sets", spark=spark)
        with pytest.raises(
            ValueError, match=r"nml_sfc_d_47662\.csv: 2 element\(s\) where the other files have 3"
        ):
            loader.load()

    @pytest.mark.parametrize(
        "manifest, message",
        [
            (None, "no manifest"),
            ({**MANIFEST_2020, "period_end_year": 2030}, "is not the directory's"),
            (
                {k: v for k, v in MANIFEST_2020.items() if k != "version"},
                r"manifest lacks \['version'\]",
            ),
        ],
    )
    def test_manifest_problems(self, spark, tmp_path, manifest, message):
        write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": daily_file_text("47662")}, manifest)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.manifest", spark=spark)
        with pytest.raises(ValueError, match=message):
            loader.load()

    def test_a_file_not_named_like_a_daily_file_is_rejected(self, spark, tmp_path):
        daily = write_period(
            tmp_path, 2020, {"normals_47662.csv": daily_file_text("47662")}, MANIFEST_2020
        )
        loader = JmaNormalsCsvLoader(CONTRACT, daily / "normals_47662.csv", "t", spark=spark)
        with pytest.raises(ValueError, match="not a daily normals file"):
            loader.load()
