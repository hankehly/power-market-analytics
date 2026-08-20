"""Tests for the JMA obsdl downloaders (``power_market_analytics/jma.py``).

Only the HTTP boundary is faked (a recording ``requests.Session`` stand-in);
payloads, HTML fragments, cp932 bytes and files are all real.
"""

from __future__ import annotations

import datetime
import time
from pathlib import Path

import pytest
import requests
from loguru import logger

import power_market_analytics.jma as jma_module
from power_market_analytics.jma import (
    ELEMENT_VALUE_COLUMNS,
    HOURLY_ELEMENTS,
    KANSOKU_DIGITS,
    JmaHourlyDownloader,
    JmaStationMasterDownloader,
    _JmaDownloader,
)

TODAY = datetime.date(2026, 8, 18)


class FakeResponse:
    def __init__(self, content: bytes = b"", status: int = 200):
        self.content = content
        self.status_code = status

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Stand-in for requests.Session answering ``post`` from a queue or a router.

    ``responses`` is either a list consumed in order (the last one is repeated
    once exhausted) or a callable ``payload -> FakeResponse``.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[dict] = []

    def post(self, url: str, data: dict, headers: dict, timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        if callable(self.responses):
            return self.responses(data)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record ``time.sleep`` calls made by the jma module instead of sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr(jma_module.time, "sleep", recorded.append)
    return recorded


# --------------------------------------------------------------------------- HTTP core


class TestPostWithRetry:
    def test_success_returns_after_one_call_with_headers_and_timeout(self, sleeps):
        session = FakeSession([FakeResponse(b"ok")])
        dl = _JmaDownloader(timeout=12.5, request_interval=0.0, session=session)

        response = dl._post_with_retry("https://example.test/x", {"pd": "44"})

        assert response.content == b"ok"
        assert session.calls == [
            {
                "url": "https://example.test/x",
                "data": {"pd": "44"},
                "headers": _JmaDownloader._HEADERS,
                "timeout": 12.5,
            }
        ]
        assert sleeps == []

    def test_headers_identify_the_browser_and_referer(self):
        assert (
            _JmaDownloader._HEADERS["Referer"] == "https://www.data.jma.go.jp/risk/obsdl/index.php"
        )
        assert _JmaDownloader._HEADERS["User-Agent"].startswith("Mozilla/5.0")

    def test_429_then_200_retries_once_with_base_backoff(self, sleeps):
        session = FakeSession([FakeResponse(b"", status=429), FakeResponse(b"ok")])
        dl = _JmaDownloader(request_interval=0.0, session=session)  # backoff_base=30 default

        response = dl._post_with_retry("https://example.test/x", {})

        assert response.content == b"ok"
        assert len(session.calls) == 2
        assert sleeps == [30.0]

    def test_backoff_doubles_per_attempt(self, sleeps):
        session = FakeSession(
            [FakeResponse(status=429), FakeResponse(status=503), FakeResponse(b"ok")]
        )
        dl = _JmaDownloader(request_interval=0.0, backoff_base=7.0, session=session)

        dl._post_with_retry("https://example.test/x", {})

        assert len(session.calls) == 3
        assert sleeps == [7.0, 14.0]

    def test_5xx_exhausting_retries_raises_after_max_retries_plus_one_calls(self, sleeps):
        session = FakeSession([FakeResponse(b"boom", status=502)])
        dl = _JmaDownloader(request_interval=0.0, max_retries=2, backoff_base=1.0, session=session)

        with pytest.raises(requests.HTTPError, match="502"):
            dl._post_with_retry("https://example.test/x", {})

        assert len(session.calls) == 3
        assert sleeps == [1.0, 2.0]  # no sleep after the final attempt

    def test_404_is_not_retried(self, sleeps):
        session = FakeSession([FakeResponse(status=404), FakeResponse(b"never")])
        dl = _JmaDownloader(request_interval=0.0, session=session)

        with pytest.raises(requests.HTTPError, match="404"):
            dl._post_with_retry("https://example.test/x", {})

        assert len(session.calls) == 1
        assert sleeps == []

    def test_default_session_is_a_requests_session(self):
        assert isinstance(_JmaDownloader().session, requests.Session)


class TestThrottle:
    def test_first_request_never_sleeps(self, sleeps):
        dl = _JmaDownloader(request_interval=5.0)
        assert dl._last_request_at == 0.0
        dl._throttle()
        assert sleeps == []
        assert dl._last_request_at > 0.0

    def test_zero_interval_never_sleeps(self, sleeps):
        dl = _JmaDownloader(request_interval=0.0)
        dl._throttle()
        dl._throttle()
        assert sleeps == []

    def test_back_to_back_requests_sleep_the_remaining_interval(self, sleeps):
        dl = _JmaDownloader(request_interval=10.0)
        dl._last_request_at = time.monotonic()  # a request just went out
        dl._throttle()
        assert len(sleeps) == 1
        assert 9.0 < sleeps[0] <= 10.0

    def test_no_sleep_once_the_interval_has_elapsed(self, sleeps):
        dl = _JmaDownloader(request_interval=10.0)
        dl._last_request_at = time.monotonic() - 20.0
        before = dl._last_request_at
        dl._throttle()
        assert sleeps == []
        assert dl._last_request_at > before


# --------------------------------------------------------------------------- hourly


CSV_HEAD = "ダウンロードした時刻：2026/08/18 10:00:00\r\n\r\n,東京,東京\r\n"
CSV_BYTES = (CSV_HEAD + "2016/1/1 1:00,5.1,8,1,0.0,8,1\r\n").encode("cp932")
HTML_ERROR = "<html><body>エラーが発生しました</body></html>".encode("cp932")

PAST_YEAR_PAYLOAD = {
    "stationNumList": '["s47662"]',
    "aggrgPeriod": "9",
    "elementNumList": '[["101",""],["201",""]]',
    "interAnnualType": "1",
    "ymdList": '["2016","2016","1","12","1","31"]',
    "optionNumList": "[]",
    "downloadFlag": "true",
    "rmkFlag": "1",
    "disconnectFlag": "1",
    "youbiFlag": "0",
    "fukenFlag": "0",
    "kijiFlag": "0",
    "csvFlag": "1",
    "jikantaiFlag": "0",
    "jikantaiList": "[1,24]",
    "ymdLiteral": "1",
}


class TestElementTables:
    def test_wind_is_the_only_two_column_element(self):
        assert ELEMENT_VALUE_COLUMNS["wind"] == 2
        assert {n for n, c in ELEMENT_VALUE_COLUMNS.items() if c != 1} == {"wind"}
        assert set(ELEMENT_VALUE_COLUMNS) == set(HOURLY_ELEMENTS)

    def test_kansoku_digit_order_matches_the_site_js(self):
        assert KANSOKU_DIGITS == [
            "precipitation",
            "wind",
            "temperature",
            "sunshine",
            "snow",
            "other",
        ]


class TestHourlyPaths:
    def test_default_data_dir(self):
        assert JmaHourlyDownloader().data_dir == Path("data/jma/hourly")

    def test_path_encodes_sorted_codes_regardless_of_argument_order(self, tmp_path):
        dl = JmaHourlyDownloader(data_dir=tmp_path)
        assert dl.path_for("s47662", ["wind", "temperature"], 2016) == (
            tmp_path / "s47662_201-301_2016.csv"
        )
        assert dl.path_for("s47662", ["temperature", "wind"], 2016) == (
            tmp_path / "s47662_201-301_2016.csv"
        )

    def test_element_codes_sorted_numerically(self):
        dl = JmaHourlyDownloader()
        assert dl._element_codes(["humidity", "wind", "precipitation"]) == ["101", "301", "605"]


class TestValidateElements:
    def test_empty(self):
        with pytest.raises(ValueError, match="At least one element"):
            JmaHourlyDownloader()._validate_elements([])

    def test_unknown(self):
        with pytest.raises(ValueError, match=r"Unknown elements \['rainbow', 'temp'\]"):
            JmaHourlyDownloader()._validate_elements(["temp", "temperature", "rainbow"])

    def test_duplicate(self):
        with pytest.raises(ValueError, match="Duplicate"):
            JmaHourlyDownloader()._validate_elements(["temperature", "temperature"])

    def test_over_the_value_column_cap(self):
        elements = ["wind", "temperature", "precipitation", "sunshine", "humidity"]  # 6 cols
        with pytest.raises(ValueError, match="needs 6 value columns"):
            JmaHourlyDownloader()._validate_elements(elements)

    def test_exactly_at_the_cap_is_allowed(self):
        JmaHourlyDownloader()._validate_elements(
            ["wind", "temperature", "precipitation", "sunshine"]
        )


class TestHourlyPayload:
    def test_past_year_covers_january_through_december(self):
        dl = JmaHourlyDownloader()
        payload = dl._payload("s47662", ["temperature", "precipitation"], 2016, today=TODAY)
        assert payload == PAST_YEAR_PAYLOAD

    def test_current_year_is_clamped_to_yesterday(self):
        dl = JmaHourlyDownloader()
        payload = dl._payload("a0368", ["wind"], 2026, today=TODAY)
        assert payload["ymdList"] == '["2026","2026","1","8","1","17"]'
        assert payload["stationNumList"] == '["a0368"]'
        assert payload["elementNumList"] == '[["301",""]]'

    def test_last_year_on_new_years_day_still_ends_december_31(self):
        dl = JmaHourlyDownloader()
        payload = dl._payload("s47662", ["temperature"], 2025, today=datetime.date(2026, 1, 1))
        assert payload["ymdList"] == '["2025","2025","1","12","1","31"]'

    def test_today_defaults_to_the_real_date(self):
        payload = JmaHourlyDownloader()._payload("s47662", ["temperature"], 2016)
        assert payload["ymdList"] == '["2016","2016","1","12","1","31"]'


class TestHourlyDownload:
    def test_year_before_earliest_is_rejected_before_any_http(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        with pytest.raises(ValueError, match=r"Year 2015 outside supported range 2016\.\.2026"):
            dl.download("s47662", ["temperature"], 2015, today=TODAY)
        assert session.calls == []

    def test_year_after_current_is_rejected(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        with pytest.raises(ValueError, match="Year 2027 outside"):
            dl.download("s47662", ["temperature"], 2027, today=TODAY)
        assert session.calls == []

    def test_invalid_elements_are_rejected_before_any_http(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        with pytest.raises(ValueError, match="Unknown elements"):
            dl.download("s47662", ["temp"], 2016, today=TODAY)
        assert session.calls == []

    def test_success_writes_cp932_bytes_and_posts_the_payload(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        data_dir = tmp_path / "nested" / "hourly"  # does not exist yet
        dl = JmaHourlyDownloader(
            data_dir=data_dir, request_interval=0.0, timeout=9.0, session=session
        )

        path = dl.download("s47662", ["temperature", "precipitation"], 2016, today=TODAY)

        assert path == data_dir / "s47662_101-201_2016.csv"
        assert path.read_bytes() == CSV_BYTES
        assert path.read_bytes().startswith("ダウンロードした時刻".encode("cp932"))
        assert sorted(p.name for p in data_dir.iterdir()) == ["s47662_101-201_2016.csv"]
        assert session.calls == [
            {
                "url": "https://www.data.jma.go.jp/risk/obsdl/show/table",
                "data": PAST_YEAR_PAYLOAD,
                "headers": _JmaDownloader._HEADERS,
                "timeout": 9.0,
            }
        ]

    def test_cached_file_is_returned_without_http(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        cached = tmp_path / "s47662_201_2016.csv"
        cached.write_bytes(b"old")

        assert dl.download("s47662", ["temperature"], 2016, today=TODAY) == cached
        assert cached.read_bytes() == b"old"
        assert session.calls == []

    def test_force_redownloads_over_a_cached_file(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        cached = tmp_path / "s47662_201_2016.csv"
        cached.write_bytes(b"old")

        dl.download("s47662", ["temperature"], 2016, force=True, today=TODAY)

        assert cached.read_bytes() == CSV_BYTES
        assert len(session.calls) == 1

    def test_current_year_request_ends_yesterday(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)

        dl.download("s47662", ["temperature"], 2026, today=TODAY)

        assert session.calls[0]["data"]["ymdList"] == '["2026","2026","1","8","1","17"]'

    def test_html_response_raises_and_writes_nothing(self, tmp_path):
        session = FakeSession([FakeResponse(HTML_ERROR)])
        data_dir = tmp_path / "hourly"
        dl = JmaHourlyDownloader(data_dir=data_dir, request_interval=0.0, session=session)

        with pytest.raises(ValueError, match=r"s47662/\['temperature'\]/2016 \(not a JMA CSV\)"):
            dl.download("s47662", ["temperature"], 2016, today=TODAY)

        assert not data_dir.exists()

    def test_http_error_propagates(self, tmp_path):
        session = FakeSession([FakeResponse(status=404)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        with pytest.raises(requests.HTTPError):
            dl.download("s47662", ["temperature"], 2016, today=TODAY)
        assert list(tmp_path.iterdir()) == []

    def test_today_defaults_to_the_real_date(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        # A far-future year is out of range under the real clock too.
        with pytest.raises(ValueError, match="Year 9999 outside"):
            dl.download("s47662", ["temperature"], 9999)
        # A past year downloads under the real clock.
        assert dl.download("s47662", ["temperature"], 2016).read_bytes() == CSV_BYTES


# --------------------------------------------------------------------------- station master


def station_block(
    stid: str,
    stname: str,
    prid: str,
    kansoku: str,
    title: str,
    extra_class: str = "",
) -> str:
    """One station div exactly as the ``top/station`` page renders it."""
    return (
        f'<div class="station{extra_class}" style="left:10px;top:20px;" title="{title}">'
        f'<input type="hidden" name="stid" value="{stid}">'
        f'<input type="hidden" name="stname" value="{stname}">'
        f'<input type="hidden" name="prid" value="{prid}">'
        f'<input type="hidden" name="kansoku" value="{kansoku}">'
        "</div>"
    )


TOKYO_TITLE = "地点名：東京\nカナ:トウキヨウ\n北緯：35度41.5分\n東経：139度45.0分\n標高：25.2m"
FUCHU_TITLE = "地点名：府中\nカナ:フチユウ\n北緯：35度41.0分\n東経：139度29.1分\n標高：58.0m"
SHINKIBA_TITLE = (
    "地点名：新木場\nカナ:シンキバ\n北緯：35度38.4分\n東経：139度49.9分\n標高：2.0m\n"
    "2019年3月31日に観測終了"
)
NAVIGATION_BLOCK = (
    '<div class="movepr" style="left:0px;top:0px;">'
    '<input type="hidden" name="stid" value="h45">'
    '<input type="hidden" name="stname" value="千葉">'
    '<input type="hidden" name="prid" value="45">'
    '<input type="hidden" name="kansoku" value="000000">'
    "</div>"
)

TOKYO = station_block("s47662", "東京", "44", "111111", TOKYO_TITLE)
FUCHU = station_block("a1133", "府中", "44", "111201", FUCHU_TITLE, extra_class=" st_a")
SHINKIBA = station_block("a0370", "新木場", "44", "111100", SHINKIBA_TITLE)

TOKYO_ROW = {
    "station_id": "s47662",
    "prefecture_code": 44,
    "station_name": "東京",
    "station_kana": "トウキヨウ",
    "latitude": 35.6917,
    "longitude": 139.75,
    "elevation_m": 25.2,
    "kansoku": "111111",
    "obs_precipitation": 1,
    "obs_wind": 1,
    "obs_temperature": 1,
    "obs_sunshine": 1,
    "obs_snow": 1,
    "obs_other": 1,
    "observation_ended_on": None,
}
FUCHU_ROW = {
    "station_id": "a1133",
    "prefecture_code": 44,
    "station_name": "府中",
    "station_kana": "フチユウ",
    "latitude": 35.6833,
    "longitude": 139.485,
    "elevation_m": 58.0,
    "kansoku": "111201",
    "obs_precipitation": 1,
    "obs_wind": 1,
    "obs_temperature": 1,
    "obs_sunshine": 2,
    "obs_snow": 0,
    "obs_other": 1,
    "observation_ended_on": None,
}
SHINKIBA_ROW = {
    "station_id": "a0370",
    "prefecture_code": 44,
    "station_name": "新木場",
    "station_kana": "シンキバ",
    "latitude": 35.64,
    "longitude": 139.8317,
    "elevation_m": 2.0,
    "kansoku": "111100",
    "obs_precipitation": 1,
    "obs_wind": 1,
    "obs_temperature": 1,
    "obs_sunshine": 1,
    "obs_snow": 0,
    "obs_other": 0,
    "observation_ended_on": "2019-03-31",
}


class TestParseTitle:
    parse = staticmethod(JmaStationMasterDownloader._parse_title)

    def test_tokyo_tooltip(self):
        assert self.parse(TOKYO_TITLE) == {
            "station_kana": "トウキヨウ",
            "latitude": 35.6917,
            "longitude": 139.75,
            "elevation_m": 25.2,
            "observation_ended_on": None,
        }

    def test_southern_and_western_hemispheres_are_negative(self):
        title = "地点名：昭和\nカナ:シヨウワ\n南緯：69度0.3分\n西経：39度35.0分\n標高：18.4m"
        parsed = self.parse(title)
        assert parsed["latitude"] == -69.005
        assert parsed["longitude"] == -39.5833

    def test_negative_elevation(self):
        assert self.parse("標高：-3.0m")["elevation_m"] == -3.0

    def test_end_of_observation_date_is_iso(self):
        assert self.parse(SHINKIBA_TITLE)["observation_ended_on"] == "2019-03-31"
        assert self.parse("2003年10月16日に観測終了")["observation_ended_on"] == "2003-10-16"

    def test_kana_is_stripped_of_trailing_whitespace(self):
        assert self.parse("カナ:フナドマリ \n北緯：45度26.2分")["station_kana"] == "フナドマリ"

    def test_missing_fields_are_none(self):
        assert self.parse("地点名：謎") == {
            "station_kana": None,
            "latitude": None,
            "longitude": None,
            "elevation_m": None,
            "observation_ended_on": None,
        }


class TestParseStations:
    def test_parses_blocks_and_collapses_duplicates(self):
        # The page renders each station twice (map marker + name label).
        html = f"<div>{TOKYO}{FUCHU}{TOKYO}{SHINKIBA}</div>"
        rows = JmaStationMasterDownloader()._parse_stations(html, 44)
        assert rows == [TOKYO_ROW, FUCHU_ROW, SHINKIBA_ROW]

    def test_prefecture_code_comes_from_the_page_not_the_argument(self):
        rows = JmaStationMasterDownloader()._parse_stations(TOKYO, 99)
        assert rows[0]["prefecture_code"] == 44

    def test_short_kansoku_leaves_missing_digits_none(self):
        html = station_block("a9001", "短い", "44", "1112", TOKYO_TITLE)
        [row] = JmaStationMasterDownloader()._parse_stations(html, 44)
        assert row["kansoku"] == "1112"
        assert (row["obs_precipitation"], row["obs_wind"], row["obs_temperature"]) == (1, 1, 1)
        assert row["obs_sunshine"] == 2
        assert row["obs_snow"] is None
        assert row["obs_other"] is None

    def test_navigation_ids_are_ignored(self):
        html = NAVIGATION_BLOCK + TOKYO + NAVIGATION_BLOCK.replace("h45", "h46")
        rows = JmaStationMasterDownloader()._parse_stations(html, 44)
        assert [r["station_id"] for r in rows] == ["s47662"]

    def test_uncaptured_station_id_signals_markup_drift(self):
        # Whitespace between the hidden inputs breaks the block regex, but the
        # id is still on the page — the parser must not silently drop it.
        broken = station_block("a9999", "壊", "7", "111111", TOKYO_TITLE).replace(
            '"><input type="hidden" name="stname"', '">\n<input type="hidden" name="stname"'
        )
        html = TOKYO + broken
        with pytest.raises(
            ValueError,
            match=r"Area 07: parsed 1 stations but the page contains 2 station ids "
            r"\(missed: \['a9999'\]\) — markup may have changed",
        ):
            JmaStationMasterDownloader()._parse_stations(html, 7)

    def test_empty_page_yields_no_rows(self):
        assert JmaStationMasterDownloader()._parse_stations("<div></div>", 44) == []


PREFECTURE_MAP = (
    '<div id="prmap"><div class="prefecture" id="pr45" style="x"></div>'
    '<div class="prefecture" id="pr44"></div><div class="prefecture" id="pr45"></div></div>'
)


class TestPrefectureCodes:
    def test_posts_pd_00_and_returns_sorted_unique_codes(self):
        session = FakeSession([FakeResponse(PREFECTURE_MAP.encode("utf-8"))])
        dl = JmaStationMasterDownloader(request_interval=0.0, session=session)

        assert dl._prefecture_codes() == [44, 45]
        assert session.calls[0]["url"] == "https://www.data.jma.go.jp/risk/obsdl/top/station"
        assert session.calls[0]["data"] == {"pd": "00"}

    def test_no_codes_raises(self):
        session = FakeSession([FakeResponse(b"<div>nothing here</div>")])
        dl = JmaStationMasterDownloader(request_interval=0.0, session=session)
        with pytest.raises(ValueError, match="No prefecture codes"):
            dl._prefecture_codes()


class TestFetchArea:
    @pytest.mark.parametrize("code, pd", [(0, "00"), (5, "05"), (44, "44"), ("7", "07")])
    def test_pd_is_zero_padded(self, code, pd):
        session = FakeSession([FakeResponse("<div>ページ</div>".encode("utf-8"))])
        dl = JmaStationMasterDownloader(request_interval=0.0, session=session)

        assert dl._fetch_area(code) == "<div>ページ</div>"
        assert session.calls == [
            {
                "url": "https://www.data.jma.go.jp/risk/obsdl/top/station",
                "data": {"pd": pd},
                "headers": _JmaDownloader._HEADERS,
                "timeout": 60.0,
            }
        ]


HEADER = (
    "station_id,prefecture_code,station_name,station_kana,latitude,longitude,elevation_m,"
    "kansoku,obs_precipitation,obs_wind,obs_temperature,obs_sunshine,obs_snow,obs_other,"
    "observation_ended_on"
)


def route_areas(pages: dict[str, str]):
    """Answer ``pd=NN`` posts from ``pages`` (utf-8 HTML strings)."""

    def respond(payload: dict) -> FakeResponse:
        return FakeResponse(pages[payload["pd"]].encode("utf-8"))

    return respond


class TestStationMasterDownload:
    def test_default_dest(self):
        assert JmaStationMasterDownloader().dest == Path("data/jma/stations.csv")

    def test_fieldnames_match_the_seed_header(self):
        assert ",".join(JmaStationMasterDownloader.FIELDNAMES) == HEADER

    def test_cached_dest_is_returned_without_http(self, tmp_path):
        dest = tmp_path / "stations.csv"
        dest.write_text("cached")
        session = FakeSession([FakeResponse(PREFECTURE_MAP.encode())])
        dl = JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session)

        assert dl.download() == dest
        assert dest.read_text() == "cached"
        assert session.calls == []

    def test_full_run_writes_sorted_utf8_csv(self, tmp_path):
        chiba = station_block(
            "a0999",
            "千葉",
            "45",
            "111000",
            "地点名：千葉\nカナ:チバ\n北緯：35度36.0分\n東経：140度6.0分\n標高：4.0m",
        )
        pages = {
            "00": PREFECTURE_MAP,
            # Tokyo's page renders each station twice, plus a navigation cell.
            "44": TOKYO + FUCHU + SHINKIBA + NAVIGATION_BLOCK + TOKYO,
            # A border station (東京) shows up on the neighbouring page too, with
            # identical metadata → collapsed into one row.
            "45": chiba + TOKYO,
        }
        session = FakeSession(route_areas(pages))
        dest = tmp_path / "seeds" / "jma_stations.csv"  # parent does not exist yet
        dl = JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session)

        assert dl.download() == dest

        assert [c["data"] for c in session.calls] == [{"pd": "00"}, {"pd": "44"}, {"pd": "45"}]
        assert dest.read_bytes().decode("utf-8") == (
            HEADER + "\r\n"
            "a0370,44,新木場,シンキバ,35.64,139.8317,2.0,111100,1,1,1,1,0,0,2019-03-31\r\n"
            "a1133,44,府中,フチユウ,35.6833,139.485,58.0,111201,1,1,1,2,0,1,\r\n"
            "s47662,44,東京,トウキヨウ,35.6917,139.75,25.2,111111,1,1,1,1,1,1,\r\n"
            "a0999,45,千葉,チバ,35.6,140.1,4.0,111000,1,1,1,0,0,0,\r\n"
        )
        assert sorted(p.name for p in dest.parent.iterdir()) == ["jma_stations.csv"]

    def test_force_overwrites_an_existing_dest(self, tmp_path):
        dest = tmp_path / "stations.csv"
        dest.write_text("stale")
        session = FakeSession(route_areas({"00": PREFECTURE_MAP, "44": TOKYO, "45": ""}))
        dl = JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session)

        dl.download(force=True)

        assert dest.read_bytes().decode("utf-8") == (
            HEADER + "\r\ns47662,44,東京,トウキヨウ,35.6917,139.75,25.2,111111,1,1,1,1,1,1,\r\n"
        )

    def test_staffed_only_writes_only_s_stations(self, tmp_path):
        pages = {"00": PREFECTURE_MAP, "44": TOKYO + FUCHU + SHINKIBA, "45": ""}
        session = FakeSession(route_areas(pages))
        dest = tmp_path / "stations.csv"
        dl = JmaStationMasterDownloader(
            dest=dest, staffed_only=True, request_interval=0.0, session=session
        )

        dl.download()

        assert dest.read_bytes().decode("utf-8") == (
            HEADER + "\r\ns47662,44,東京,トウキヨウ,35.6917,139.75,25.2,111111,1,1,1,1,1,1,\r\n"
        )

    def test_staffed_only_defaults_off(self, tmp_path):
        pages = {"00": PREFECTURE_MAP, "44": TOKYO + FUCHU, "45": ""}
        session = FakeSession(route_areas(pages))
        dest = tmp_path / "stations.csv"
        JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session).download()
        station_ids = [line.split(",")[0] for line in dest.read_text().splitlines()[1:]]
        assert station_ids == ["a1133", "s47662"]

    def test_conflicting_duplicate_keeps_the_first_and_warns(self, tmp_path):
        # Same station id on two pages but with different metadata (JMA moved
        # it, or the pages disagree): the first-seen row wins.
        tokyo_moved = station_block("s47662", "東京", "45", "111000", TOKYO_TITLE)
        session = FakeSession(route_areas({"00": PREFECTURE_MAP, "44": TOKYO, "45": tokyo_moved}))
        dest = tmp_path / "stations.csv"
        dl = JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session)
        warnings: list[str] = []
        sink = logger.add(lambda m: warnings.append(m.record["message"]), level="WARNING")
        try:
            dl.download()
        finally:
            logger.remove(sink)

        assert dest.read_bytes().decode("utf-8").splitlines()[1:] == [
            "s47662,44,東京,トウキヨウ,35.6917,139.75,25.2,111111,1,1,1,1,1,1,"
        ]
        assert warnings == [
            "Station s47662 appears with conflicting metadata (prefectures 44 and 45); "
            "keeping the first"
        ]

    def test_markup_drift_on_an_area_page_aborts_without_writing(self, tmp_path):
        broken = TOKYO.replace(
            '"><input type="hidden" name="stname"', '">\n<input type="hidden" name="stname"'
        )
        session = FakeSession(route_areas({"00": PREFECTURE_MAP, "44": broken, "45": ""}))
        dest = tmp_path / "stations.csv"
        dl = JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session)

        with pytest.raises(ValueError, match="markup may have changed"):
            dl.download()

        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []
