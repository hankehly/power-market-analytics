"""Tests for the e-Stat census population-mesh vintages, mesh decoding and downloader.

HTTP is never real: the downloader takes an injectable ``session`` and the
listing / archive responses are built from small fixtures shaped like the
portal's ``search_detail`` JSON (HTML fragments inside JSON) and its
one-member zip archives.
"""

from __future__ import annotations

import datetime

import pytest

from power_market_analytics.estat import (
    VINTAGES,
    CensusVintage,
    MeshBounds,
    decode_mesh_code,
    vintage_for_stats_id,
    vintage_for_year,
)

# --------------------------------------------------------------------------- vintages


class TestVintages:
    def test_two_initial_vintages_in_census_order(self):
        assert [v.census_year for v in VINTAGES] == [2015, 2020]

    def test_2015_configuration(self):
        v = vintage_for_year(2015)
        assert v == CensusVintage(
            census_year=2015,
            census_date=datetime.date(2015, 10, 1),
            geodetic_datum="JGD2000",
            stats_id="T000847",
            population_source_column="T000847001",
            listing_url=(
                "https://www.e-stat.go.jp/gis/statmap-search?page=1&type=1"
                "&toukeiCode=00200521&toukeiYear=2015&aggregateUnit=H"
                "&serveyId=H002005112015&statsId=T000847"
            ),
            expected_file_count=151,
        )

    def test_2020_configuration_is_the_jgd2000_product(self):
        v = vintage_for_year(2020)
        assert (v.census_date, v.geodetic_datum, v.stats_id, v.population_source_column) == (
            datetime.date(2020, 10, 1),
            "JGD2000",
            "T001101",
            "T001101001",
        )
        assert v.listing_url == (
            "https://www.e-stat.go.jp/gis/statmap-search?page=1&type=1"
            "&toukeiCode=00200521&toukeiYear=2020&aggregateUnit=H"
            "&serveyId=H002005112020&statsId=T001101"
        )
        assert v.expected_file_count == 151

    def test_lookup_by_stats_id(self):
        assert vintage_for_stats_id("T001101").census_year == 2020

    def test_unknown_year_and_stats_id_raise(self):
        with pytest.raises(KeyError, match="2010"):
            vintage_for_year(2010)
        with pytest.raises(KeyError, match="T999999"):
            vintage_for_stats_id("T999999")

    def test_vintage_is_immutable(self):
        with pytest.raises(AttributeError):
            VINTAGES[0].stats_id = "x"  # type: ignore[misc]

    def test_download_url_and_file_names(self):
        v = vintage_for_year(2015)
        assert v.download_url("5339") == (
            "https://www.e-stat.go.jp/gis/statmap-search/data"
            "?statsId=T000847&code=5339&downloadType=2"
        )
        assert v.zip_name("5339") == "tblT000847H5339.zip"
        assert v.member_name("5339") == "tblT000847H5339.txt"

    def test_listing_detail_url_swaps_path_and_adds_the_ajax_flags(self):
        # The HTML listing is a JavaScript shell; its rows come from the
        # search_detail JSON endpoint with the same query plus two flags.
        v = vintage_for_year(2020)
        assert v.listing_detail_url(3) == (
            "https://www.e-stat.go.jp/gis/statmap-search/search_detail?page=3&type=1"
            "&toukeiCode=00200521&toukeiYear=2020&aggregateUnit=H"
            "&serveyId=H002005112020&statsId=T001101&mesh_data_flg=1&download_disp_flg=1"
        )


# --------------------------------------------------------------------------- mesh decoding


class TestDecodeMeshCode:
    def test_known_tokyo_station_mesh(self):
        bounds = decode_mesh_code("533946114")
        assert isinstance(bounds, MeshBounds)
        assert bounds.centroid_latitude == pytest.approx(35.681250, abs=1e-9)
        assert bounds.centroid_longitude == pytest.approx(139.771875, abs=1e-9)
        assert bounds.south_latitude == pytest.approx(35.679166667, abs=1e-9)
        assert bounds.north_latitude == pytest.approx(35.683333333, abs=1e-9)
        assert bounds.west_longitude == pytest.approx(139.76875, abs=1e-9)
        assert bounds.east_longitude == pytest.approx(139.775, abs=1e-9)

    @pytest.mark.parametrize(
        "quadrant, south, west",
        [
            ("1", 35.675, 139.7625),  # southwest: the third-level mesh's own corner
            ("2", 35.675, 139.7625 + 1 / 160),  # southeast
            ("3", 35.675 + 1 / 240, 139.7625),  # northwest
            ("4", 35.675 + 1 / 240, 139.7625 + 1 / 160),  # northeast
        ],
    )
    def test_all_four_quadrants(self, quadrant, south, west):
        bounds = decode_mesh_code("53394611" + quadrant)
        assert bounds.south_latitude == pytest.approx(south, abs=1e-9)
        assert bounds.west_longitude == pytest.approx(west, abs=1e-9)
        assert bounds.north_latitude - bounds.south_latitude == pytest.approx(1 / 240, abs=1e-12)
        assert bounds.east_longitude - bounds.west_longitude == pytest.approx(1 / 160, abs=1e-12)
        assert bounds.centroid_latitude == pytest.approx(south + 1 / 480, abs=1e-9)
        assert bounds.centroid_longitude == pytest.approx(west + 1 / 320, abs=1e-9)

    def test_fixture_mesh_lies_in_primary_mesh_5339(self):
        # 5339 = lat band 53 (35°20'..36°00'N), lon band 39 (139°..140°E).
        bounds = decode_mesh_code("533900054")
        assert 35 + 20 / 60 <= bounds.south_latitude < 36
        assert 139 <= bounds.west_longitude < 140

    @pytest.mark.parametrize(
        "bad",
        [
            "53394611",  # too short
            "5339461140",  # too long
            "533946110",  # quadrant 0
            "533946115",  # quadrant 5
            "533986114",  # second-level column 8 (only 0-7 exist)
            "5339x6114",  # not digits
            "",
        ],
    )
    def test_invalid_codes_raise(self, bad):
        with pytest.raises(ValueError, match="mesh code"):
            decode_mesh_code(bad)


# --------------------------------------------------------------------------- downloader
import dataclasses  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

import requests  # noqa: E402

from power_market_analytics import estat  # noqa: E402
from power_market_analytics.estat import (  # noqa: E402
    EstatCensusMeshDownloader,
    EstatDownloadError,
)

V2015 = vintage_for_year(2015)
V2020 = vintage_for_year(2020)

#: A tiny vintage: two listing pages of two rows, three primary meshes in total.
DEMO = CensusVintage(
    census_year=1999,
    census_date=datetime.date(1999, 10, 1),
    geodetic_datum="JGD2000",
    stats_id="T000001",
    population_source_column="T000001001",
    listing_url=(
        "https://www.e-stat.go.jp/gis/statmap-search?page=1&type=1&toukeiCode=00200521"
        "&toukeiYear=1999&aggregateUnit=H&serveyId=H002005111999&statsId=T000001"
    ),
    expected_file_count=3,
)
DEMO_CODES = ["3622", "3623", "5339"]


def listing_row(stats_id: str, code: str) -> str:
    """One result row exactly as the portal renders it inside the JSON ``detail``."""
    return (
        '<article class="stat-resorce_list-item">\n<div class="stat-resorce_list-main">\n'
        '<ul class="stat-resorce_list-detail">\n'
        '<li class="stat-resorce_list-detail-item">その１　人口等基本集計に関する事項</li>\n'
        f'<li class="stat-resorce_list-detail-item">M{code}</li>\n'
        '<li class="stat-resorce_list-detail-item align-center-data">2017-06-27</li>\n'
        '<li class="stat-resorce_list-detail-item">\n'
        '<a class="stat-dl_icon stat-statistics-table_icon"\n'
        f'href="/gis/statmap-search/data?statsId={stats_id}&code={code}&downloadType=2"'
        ' tabindex="40">\n<span class="stat-dl_text">CSV</span>\n</a>\n</li>\n</ul>\n'
        "</div>\n</article>\n"
    )


def listing_json(
    stats_id: str,
    codes: list[str],
    page: int,
    pages: int,
    total: int | None = None,
    paginate: str | None = None,
) -> bytes:
    """A ``search_detail`` response: rows in ``detail``, page index in ``paginate``."""
    detail = (
        '<div class="stat-resorce_list">\n<div class="stat-resorce_list-body">\n'
        + "".join(listing_row(stats_id, c) for c in codes)
        + "</div>\n</div>\n"
    )
    if paginate is None:
        paginate = (
            '\n<div class="stat-paginate js-paginate fix" style="width:100%">\n'
            f'<div class="stat-paginate-index rig js-paginate-index">{page}/{pages}ページ</div>\n'
            "</div>\n"
        )
    side_mega = (
        '<div class="stat-hit">\n    <span class="stat-hit-number js-total_resource">'
        f"{len(codes) if total is None else total}</span>件のデータ\n</div>"
    )
    return json.dumps(
        {"side_mega": side_mega, "paginate": paginate, "detail": detail, "error_msg": ""},
        ensure_ascii=False,
    ).encode("utf-8")


def demo_listing_pages(codes: list[str] = DEMO_CODES, stats_id: str = DEMO.stats_id) -> dict:
    """Two-page listing responses for ``DEMO`` keyed by URL."""
    return {
        DEMO.listing_detail_url(1): FakeResponse(listing_json(stats_id, codes[:2], 1, 2, 3)),
        DEMO.listing_detail_url(2): FakeResponse(listing_json(stats_id, codes[2:], 2, 2, 3)),
    }


def member_text(stats_id: str, code: str) -> bytes:
    """A minimal CP932 mesh table for one primary mesh (two header rows + one row)."""
    return (
        f"KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,{stats_id}001,{stats_id}002\r\n"
        ",,,,　人口総数,　人口総数　男\r\n"
        f"{code}00054,0,,,64,33\r\n"
    ).encode("cp932")


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def demo_zip(code: str, vintage: CensusVintage = DEMO) -> bytes:
    return make_zip({vintage.member_name(code): member_text(vintage.stats_id, code)})


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


def demo_session(codes: list[str] = DEMO_CODES) -> FakeSession:
    responses = demo_listing_pages()
    for code in codes:
        responses[DEMO.download_url(code)] = FakeResponse(demo_zip(code))
    return FakeSession(responses)


class TestDownloaderPaths:
    def test_defaults(self):
        dl = EstatCensusMeshDownloader()
        assert dl.data_dir == Path("data/estat/census_population_mesh")
        assert dl.timeout == 60.0
        assert isinstance(dl.session, requests.Session)
        assert dl.zip_dir(V2015) == Path("data/estat/census_population_mesh/2015/zip")
        assert dl.txt_dir(V2020) == Path("data/estat/census_population_mesh/2020/txt")

    def test_paths_for_a_primary_mesh(self, tmp_path):
        dl = EstatCensusMeshDownloader(data_dir=tmp_path)
        assert dl.zip_path_for(V2015, "5339") == tmp_path / "2015/zip/tblT000847H5339.zip"
        assert dl.txt_path_for(V2015, "5339") == tmp_path / "2015/txt/tblT000847H5339.txt"


class TestDiscoverPrimaryMeshCodes:
    def test_walks_every_listing_page_and_returns_codes_in_order(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)

        codes = dl.discover_primary_mesh_codes(DEMO)

        assert codes == DEMO_CODES
        assert [url for url, _ in session.calls] == [
            DEMO.listing_detail_url(1),
            DEMO.listing_detail_url(2),
        ]

    def test_accepts_html_escaped_ampersands(self, tmp_path):
        page = listing_json(DEMO.stats_id, ["3622"], 1, 1).replace(b"&code=", b"&amp;code=")
        page = page.replace(b"&downloadType", b"&amp;downloadType")
        session = FakeSession({DEMO.listing_detail_url(1): FakeResponse(page)})
        vintage = dataclasses.replace(DEMO, expected_file_count=1)
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        assert dl.discover_primary_mesh_codes(vintage) == ["3622"]

    def test_deduplicates_repeated_codes(self, tmp_path):
        session = FakeSession(demo_listing_pages(codes=["3622", "3622", "3623", "5339"][:4]))
        # Page 1 lists 3622 twice, page 2 lists 3623 and 5339 -> still three codes.
        session.responses[DEMO.listing_detail_url(1)] = FakeResponse(
            listing_json(DEMO.stats_id, ["3622", "3622"], 1, 2, 3)
        )
        session.responses[DEMO.listing_detail_url(2)] = FakeResponse(
            listing_json(DEMO.stats_id, ["3623", "5339"], 2, 2, 3)
        )
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        assert dl.discover_primary_mesh_codes(DEMO) == DEMO_CODES

    def test_rejects_unexpected_file_count(self, tmp_path):
        session = demo_session()
        vintage = dataclasses.replace(DEMO, expected_file_count=151)
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(EstatDownloadError, match="expected 151 .* found 3"):
            dl.discover_primary_mesh_codes(vintage)

    def test_rejects_links_of_another_stats_id(self, tmp_path):
        session = FakeSession(demo_listing_pages(stats_id="T999999"))
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(EstatDownloadError, match="statsId T999999"):
            dl.discover_primary_mesh_codes(DEMO)

    def test_rejects_malformed_primary_mesh_codes(self, tmp_path):
        session = FakeSession(
            {
                DEMO.listing_detail_url(1): FakeResponse(
                    listing_json(DEMO.stats_id, ["3622", "36", "5339"], 1, 1)
                )
            }
        )
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(EstatDownloadError, match="primary mesh code.*'36'"):
            dl.discover_primary_mesh_codes(DEMO)

    def test_rejects_a_page_without_a_page_index(self, tmp_path):
        session = FakeSession(
            {
                DEMO.listing_detail_url(1): FakeResponse(
                    listing_json(DEMO.stats_id, ["3622"], 1, 1, paginate="<div>?</div>")
                )
            }
        )
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(EstatDownloadError, match="page index"):
            dl.discover_primary_mesh_codes(DEMO)

    def test_rejects_a_non_json_listing_response(self, tmp_path):
        session = FakeSession(
            {DEMO.listing_detail_url(1): FakeResponse(b"<html>maintenance</html>")}
        )
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(EstatDownloadError, match="not JSON"):
            dl.discover_primary_mesh_codes(DEMO)

    def test_http_errors_propagate(self, tmp_path):
        session = FakeSession({})
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(requests.HTTPError):
            dl.discover_primary_mesh_codes(DEMO)


class TestDownload:
    def test_downloads_validates_and_extracts_one_primary_mesh(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(
            data_dir=tmp_path, session=session, timeout=12.5, request_interval=0
        )

        zip_path = dl.download(DEMO, "5339")
        txt_path = dl.extract(DEMO, "5339")

        assert session.calls == [(DEMO.download_url("5339"), 12.5)]
        assert zip_path == tmp_path / "1999/zip/tblT000001H5339.zip"
        assert zip_path.read_bytes() == demo_zip("5339")
        assert txt_path == tmp_path / "1999/txt/tblT000001H5339.txt"
        # Extraction is byte-exact: CP932 and CRLF preserved.
        assert txt_path.read_bytes() == member_text(DEMO.stats_id, "5339")
        assert list((tmp_path / "1999").rglob("*.part")) == []

    def test_cached_archive_is_not_downloaded_again(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.download(DEMO, "5339")

        again = dl.download(DEMO, "5339")

        assert again == tmp_path / "1999/zip/tblT000001H5339.zip"
        assert len(session.calls) == 1

    def test_force_redownloads(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.download(DEMO, "5339")

        dl.download(DEMO, "5339", force=True)

        assert len(session.calls) == 2

    def test_http_error_leaves_nothing_behind(self, tmp_path):
        session = FakeSession({})
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(requests.HTTPError):
            dl.download(DEMO, "5339")
        assert not (tmp_path / "1999").exists()

    @pytest.mark.parametrize(
        "content, message",
        [
            (b"<html>maintenance</html>", "not a zip archive"),
            (make_zip({}), "exactly one member"),
            (
                make_zip({"tblT000001H5339.txt": b"a", "extra.txt": b"b"}),
                "exactly one member",
            ),
            (make_zip({"tblT000001H5340.txt": b"a"}), "tblT000001H5339.txt"),
            (make_zip({"nested/tblT000001H5339.txt": b"a"}), "tblT000001H5339.txt"),
        ],
    )
    def test_malformed_archives_are_rejected_and_not_cached(self, tmp_path, content, message):
        session = FakeSession({DEMO.download_url("5339"): FakeResponse(content)})
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        with pytest.raises(EstatDownloadError, match=message):
            dl.download(DEMO, "5339")
        assert (
            list(tmp_path.rglob("*")) == []
            or not (tmp_path / "1999/zip/tblT000001H5339.zip").exists()
        )

    def test_extract_without_archive_raises(self, tmp_path):
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(FileNotFoundError):
            dl.extract(DEMO, "5339")

    def test_extract_rejects_a_tampered_cached_archive(self, tmp_path):
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=FakeSession({}))
        bad = dl.zip_path_for(DEMO, "5339")
        bad.parent.mkdir(parents=True)
        bad.write_bytes(make_zip({"other.txt": b"x"}))
        with pytest.raises(EstatDownloadError, match="tblT000001H5339.txt"):
            dl.extract(DEMO, "5339")


class TestDownloadVintageAndAll:
    def test_download_vintage_discovers_then_fetches_every_primary_mesh(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)

        paths = dl.download_vintage(DEMO)

        assert paths == [tmp_path / f"1999/txt/tblT000001H{c}.txt" for c in DEMO_CODES]
        assert [url for url, _ in session.calls] == [
            DEMO.listing_detail_url(1),
            DEMO.listing_detail_url(2),
            *(DEMO.download_url(c) for c in DEMO_CODES),
        ]

    def test_second_run_uses_the_cache_but_still_checks_the_listing(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.download_vintage(DEMO)
        session.calls.clear()

        paths = dl.download_vintage(DEMO)

        assert len(paths) == 3
        assert [url for url, _ in session.calls] == [
            DEMO.listing_detail_url(1),
            DEMO.listing_detail_url(2),
        ]

    def test_force_refetches_every_archive(self, tmp_path):
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.download_vintage(DEMO)
        session.calls.clear()

        dl.download_vintage(DEMO, force=True)

        assert [url for url, _ in session.calls][2:] == [DEMO.download_url(c) for c in DEMO_CODES]

    def test_download_all_defaults_to_every_configured_vintage(self, tmp_path, monkeypatch):
        seen: list[tuple[int, bool]] = []
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=FakeSession({}))
        monkeypatch.setattr(
            dl,
            "download_vintage",
            lambda vintage, force=False: (
                seen.append((vintage.census_year, force)) or [tmp_path / str(vintage.census_year)]
            ),
        )

        paths = dl.download_all()

        assert seen == [(2015, False), (2020, False)]
        assert paths == [tmp_path / "2015", tmp_path / "2020"]

    def test_download_all_filters_years_and_passes_force(self, tmp_path, monkeypatch):
        seen: list[tuple[int, bool]] = []
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=FakeSession({}))
        monkeypatch.setattr(
            dl,
            "download_vintage",
            lambda vintage, force=False: seen.append((vintage.census_year, force)) or [],
        )

        dl.download_all(years=[2020], force=True)

        assert seen == [(2020, True)]

    def test_download_all_rejects_unknown_years(self, tmp_path):
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(KeyError, match="2010"):
            dl.download_all(years=[2015, 2010])


class TestThrottle:
    def test_consecutive_requests_are_spaced_by_the_interval(self, tmp_path, monkeypatch):
        clock = {"now": 100.0}
        sleeps: list[float] = []
        monkeypatch.setattr(estat.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(estat.time, "sleep", lambda s: sleeps.append(s))
        session = demo_session()
        dl = EstatCensusMeshDownloader(data_dir=tmp_path, session=session, request_interval=0.5)

        dl.download(DEMO, "3622")  # first request: no wait
        clock["now"] += 0.2
        dl.download(DEMO, "3623")  # 0.2 s later: waits the remaining 0.3 s
        clock["now"] += 1.0
        dl.download(DEMO, "5339")  # long after: no wait

        assert sleeps == pytest.approx([0.3])
        assert dl.request_interval == 0.5

    def test_default_interval_is_polite_but_short(self):
        assert EstatCensusMeshDownloader().request_interval == 0.5
