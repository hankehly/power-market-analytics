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
    JmaNormalsDownloader,
    JmaNormalsDownloadError,
    NormalsVintage,
    parse_versions,
    vintage_for_year,
)

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
