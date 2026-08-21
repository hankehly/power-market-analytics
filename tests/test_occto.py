"""Tests for the OCCTO 情報ダウンロード bulk downloader (power_market_analytics.occto)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import cast

import pytest
import requests

from power_market_analytics.occto import (
    BASE_URL,
    DATASETS,
    JST,
    MAX_ROWS_PER_DOWNLOAD,
    OcctoBulkDownloader,
    OcctoDataset,
    OcctoDownloadError,
)

SCREEN_URL = f"{BASE_URL}/CF01S010C"
LOGIN_URL = f"{BASE_URL}/LOGIN_login"

DEMAND_HEADER = (
    "策定日,対象日付,対象エリア,最小総需要予想時刻,最小総需要予想（MW）,"
    "最大総需要予想時刻,最大総需要予想（MW）,最大供給力予想（MW）,予想使用率,予想予備率"
)
RESERVE_HEADER = (
    "対象年月日,区分,時刻,エリア,広域予備率(%),広域使用率(%),ブロックNo.,"
    "広域ブロック需要(MW),広域ブロック供給力(MW),広域ブロック予備力(MW),"
    "エリア需要(MW),エリア供給力(MW),エリア予備力(MW)"
)

#: A small chunked spec so window boundaries are easy to derive by hand.
WEEKLY = OcctoDataset(
    key="weekly",
    area_data_knd="99",
    header="h",
    history_start=datetime.date(2025, 4, 1),
    max_days_per_download=7,
)
ALL_TERM = OcctoDataset(key="allterm", area_data_knd="98", header="h")

#: Every field the portal's screen framework expects on a reference/<sub_type> POST.
FRAMEWORK_OK = {
    "fwExtention.actionType": "reference",
    "fwExtention.actionSubType": "ok",
    "fwExtention.pagingTargetTable": "",
    "fwExtention.pathInfo": "CF01S010C",
    "fwExtention.prgbrh": "0",
    "fwExtention.formId": "CF01S010P",
    "fwExtention.jsonString": "",
    "ajaxToken": "",
    "requestTokenBk": "",
    "transitionContextKey": "DEFAULT",
}
FRAMEWORK_DOWNLOAD = {**FRAMEWORK_OK, "fwExtention.actionSubType": "download"}

#: The エリア・広域ブロック情報 tab with every area (and the エリア計 sum) ticked.
AREA_SELECTION = {
    "tabSntk": "1",
    "allAreaSectDwld": "11",
    "hkd": "01",
    "thk": "02",
    "tko": "03",
    "chb": "04",
    "hkr": "05",
    "kns": "06",
    "cgk": "07",
    "skk": "08",
    "kys": "09",
    "oki": "10",
    "areaSum": "11",
}


def cp932(text: str) -> bytes:
    return text.encode("cp932")


# --------------------------------------------------------------------------- fakes


class FakeResponse:
    def __init__(self, content: bytes = b"", status: int = 200, headers: dict | None = None):
        self.content = content
        self.status_code = status
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def json_response(payload) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def ok_response(download_key: str, request_token: str) -> FakeResponse:
    """The reference/ok JSON that hands out a downloadKey / requestToken pair."""
    return json_response(
        {
            "root": {
                "bizRoot": {
                    "header": {
                        "downloadKey": {"value": download_key},
                        "requestToken": {"value": request_token},
                    }
                }
            }
        }
    )


def csv_response(content: bytes) -> FakeResponse:
    return FakeResponse(
        content,
        headers={
            "Content-Type": "text/csv;charset=Shift_JIS",
            "Content-Disposition": 'attachment; filename="data.csv"',
        },
    )


class FakeSession:
    """Stand-in for requests.Session: replays canned responses, records every call.

    ``cookies`` supports the ``"JSESSIONID" in session.cookies`` membership test
    the downloader performs after LOGIN_login.
    """

    def __init__(self, responses: list[FakeResponse], cookies: tuple[str, ...] = ("JSESSIONID",)):
        self.responses = list(responses)
        self.cookies = dict.fromkeys(cookies, "x")
        self.calls: list[tuple] = []
        self.closed = False

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc) -> None:
        self.closed = True

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append(("get", url, {"timeout": timeout}))
        return self._next()

    def post(
        self, url: str, data: dict, timeout: float, headers: dict | None = None
    ) -> FakeResponse:
        self.calls.append(("post", url, {"data": data, "headers": headers, "timeout": timeout}))
        return self._next()

    def _next(self) -> FakeResponse:
        if not self.responses:
            raise AssertionError("downloader issued more requests than the test scripted")
        return self.responses.pop(0)


class FakeSessionFactory:
    """Hands out one scripted FakeSession per call and remembers them."""

    def __init__(self, *sessions: FakeSession):
        self.pending = list(sessions)
        self.created: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = self.pending.pop(0)
        self.created.append(session)
        return session


# --------------------------------------------------------------------------- dataset catalog


class TestOcctoDataset:
    def test_rejects_non_positive_window_size(self):
        with pytest.raises(ValueError, match="x: max_days_per_download must be >= 1"):
            OcctoDataset(
                key="x",
                area_data_knd="1",
                header="h",
                history_start=datetime.date(2025, 1, 1),
                max_days_per_download=0,
            )

    def test_chunked_dataset_requires_history_start(self):
        with pytest.raises(ValueError, match="x: history_start is required when chunking"):
            OcctoDataset(key="x", area_data_knd="1", header="h", max_days_per_download=10)

    def test_all_term_dataset_needs_no_history_start(self):
        spec = OcctoDataset(key="x", area_data_knd="1", header="h")
        assert (spec.history_start, spec.max_days_per_download) == (None, None)


class TestDatasets:
    def test_catalog_keys_and_radio_values(self):
        assert {k: d.area_data_knd for k, d in DATASETS.items()} == {
            "demand_forecast_dad": "32",
            "area_reserve_rate_dad": "31",
        }

    def test_demand_forecast_is_a_single_all_term_download(self):
        assert DATASETS["demand_forecast_dad"].max_days_per_download is None
        assert DATASETS["demand_forecast_dad"].header == DEMAND_HEADER

    def test_reserve_rate_windows_stay_under_the_row_cap(self):
        spec = DATASETS["area_reserve_rate_dad"]
        assert spec.history_start == datetime.date(2025, 4, 1)
        assert spec.max_days_per_download == 300
        # 48 half-hours × 10 areas = 480 rows/day; a window must fit in one download.
        assert spec.max_days_per_download * 480 <= MAX_ROWS_PER_DOWNLOAD
        assert spec.header == RESERVE_HEADER


# --------------------------------------------------------------------------- pure helpers


class TestPathFor:
    def test_defaults(self):
        dl = OcctoBulkDownloader()
        assert dl.data_dir == Path("data/occto")
        assert dl.timeout == 120.0
        assert dl.session_factory is requests.Session

    def test_one_subdirectory_per_dataset(self, tmp_path):
        dl = OcctoBulkDownloader(data_dir=tmp_path)
        assert dl.path_for("demand_forecast_dad") == (
            tmp_path / "demand_forecast_dad" / "demand_forecast_dad.csv"
        )


class TestWindows:
    windows = staticmethod(OcctoBulkDownloader._windows)

    def test_all_term_dataset_is_one_unbounded_window(self):
        assert self.windows(ALL_TERM, None, None) == [(None, None)]

    def test_all_term_dataset_keeps_an_explicit_range_as_one_window(self):
        d1, d2 = datetime.date(2025, 4, 1), datetime.date(2030, 12, 31)
        assert self.windows(ALL_TERM, d1, d2) == [(d1, d2)]

    def test_chunked_range_splits_into_closed_windows_with_a_short_tail(self):
        assert self.windows(WEEKLY, datetime.date(2025, 4, 1), datetime.date(2025, 4, 20)) == [
            (datetime.date(2025, 4, 1), datetime.date(2025, 4, 7)),
            (datetime.date(2025, 4, 8), datetime.date(2025, 4, 14)),
            (datetime.date(2025, 4, 15), datetime.date(2025, 4, 20)),
        ]

    def test_exact_multiple_leaves_no_empty_trailing_window(self):
        assert self.windows(WEEKLY, datetime.date(2025, 4, 1), datetime.date(2025, 4, 14)) == [
            (datetime.date(2025, 4, 1), datetime.date(2025, 4, 7)),
            (datetime.date(2025, 4, 8), datetime.date(2025, 4, 14)),
        ]

    def test_single_day_range(self):
        d = datetime.date(2025, 4, 5)
        assert self.windows(WEEKLY, d, d) == [(d, d)]

    def test_windows_cross_month_boundaries(self):
        assert self.windows(WEEKLY, datetime.date(2025, 4, 27), datetime.date(2025, 5, 6)) == [
            (datetime.date(2025, 4, 27), datetime.date(2025, 5, 3)),
            (datetime.date(2025, 5, 4), datetime.date(2025, 5, 6)),
        ]

    def test_reserve_rate_300_day_windows(self):
        spec = DATASETS["area_reserve_rate_dad"]
        # 2025-04-01 + 299 days = 2026-01-25, so 2026-01-26 starts window two.
        assert self.windows(spec, datetime.date(2025, 4, 1), datetime.date(2026, 1, 25)) == [
            (datetime.date(2025, 4, 1), datetime.date(2026, 1, 25)),
        ]
        assert self.windows(spec, datetime.date(2025, 4, 1), datetime.date(2026, 1, 26)) == [
            (datetime.date(2025, 4, 1), datetime.date(2026, 1, 25)),
            (datetime.date(2026, 1, 26), datetime.date(2026, 1, 26)),
        ]

    def test_chunked_dataset_without_a_range_spans_history_start_to_today_plus_two(self):
        # A window size larger than any plausible history collapses the default
        # range into exactly one window, exposing both bounds.
        spec = OcctoDataset(
            key="big",
            area_data_knd="1",
            header="h",
            history_start=datetime.date(2025, 4, 1),
            max_days_per_download=100_000,
        )
        today_jst = datetime.datetime.now(JST).date()
        assert self.windows(spec, None, None) == [
            (datetime.date(2025, 4, 1), today_jst + datetime.timedelta(days=2))
        ]

    def test_default_reserve_rate_windows_are_contiguous_and_capped(self):
        spec = DATASETS["area_reserve_rate_dad"]
        windows = self.windows(spec, None, None)
        today_jst = datetime.datetime.now(JST).date()
        assert windows[0] == (datetime.date(2025, 4, 1), datetime.date(2026, 1, 25))
        assert windows[-1][1] == today_jst + datetime.timedelta(days=2)
        for (_, prev_to), (next_from, _) in zip(windows, windows[1:]):
            assert next_from == prev_to + datetime.timedelta(days=1)
        assert all((w_to - w_from).days + 1 <= 300 for w_from, w_to in windows)


class TestConcatenate:
    concatenate = staticmethod(OcctoBulkDownloader._concatenate)

    def test_keeps_first_header_and_drops_the_rest(self):
        assert self.concatenate([b"h\nA\n", b"h\nB\n", b"h\nC\n"]) == b"h\nA\nB\nC\n"

    def test_adds_a_missing_trailing_newline_before_the_next_chunk(self):
        assert self.concatenate([b"h\nA", b"h\nB"]) == b"h\nA\nB\n"

    def test_header_only_chunk_contributes_nothing(self):
        # A fully-future window comes back header-only from the portal.
        assert self.concatenate([b"h\nA\n", b"h\n"]) == b"h\nA\n"

    def test_header_only_chunk_without_newline_contributes_nothing(self):
        # _verify_csv accepts a bare header line; it must not blow up here.
        assert self.concatenate([b"h\nA\n", b"h"]) == b"h\nA\n"

    def test_single_chunk_is_returned_as_is(self):
        assert self.concatenate([b"h\nA\n"]) == b"h\nA\n"

    def test_no_chunks(self):
        assert self.concatenate([]) == b""


class TestSelection:
    selection = staticmethod(OcctoBulkDownloader._selection)

    def test_all_term(self):
        assert self.selection(DATASETS["demand_forecast_dad"], None, None) == {
            **AREA_SELECTION,
            "areaDataKnd": "32",
            "areaAllTermDwld": "Y",
        }

    def test_dated_range_uses_slashed_dates(self):
        assert self.selection(
            DATASETS["area_reserve_rate_dad"],
            datetime.date(2025, 4, 1),
            datetime.date(2026, 1, 25),
        ) == {
            **AREA_SELECTION,
            "areaDataKnd": "31",
            "areaNngpFrom": "2025/04/01",
            "areaNngpTo": "2026/01/25",
        }


class TestFrameworkFields:
    def test_ok(self):
        assert OcctoBulkDownloader._framework_fields("ok") == FRAMEWORK_OK

    def test_download(self):
        assert OcctoBulkDownloader._framework_fields("download") == FRAMEWORK_DOWNLOAD


class TestVerifyCsv:
    verify = OcctoBulkDownloader()._verify_csv
    spec = DATASETS["demand_forecast_dad"]

    def test_accepts_the_dataset_header(self):
        self.verify(self.spec, cp932(DEMAND_HEADER + "\n2025/04/01,x\n"))
        # A CRLF-terminated file passes too (splitlines handles it).
        self.verify(self.spec, cp932(DEMAND_HEADER + "\r\n"))

    def test_rejects_a_different_header(self):
        with pytest.raises(OcctoDownloadError, match="unexpected header row"):
            self.verify(self.spec, cp932(RESERVE_HEADER + "\n"))

    def test_rejects_an_html_error_page(self):
        with pytest.raises(OcctoDownloadError, match=r"unexpected header row: '<html>'"):
            self.verify(self.spec, b"<html>\n<body>error</body>")

    def test_rejects_empty_content(self):
        with pytest.raises(OcctoDownloadError, match="demand_forecast_dad is empty"):
            self.verify(self.spec, b"")

    def test_rejects_non_cp932_bytes(self):
        with pytest.raises(OcctoDownloadError, match="demand_forecast_dad is not cp932 text"):
            self.verify(self.spec, b"\x81\x39")


# --------------------------------------------------------------------------- protocol steps


class TestOpenSession:
    def test_gets_login_and_requires_a_session_cookie(self):
        session = FakeSession([FakeResponse(b"<html>")])
        OcctoBulkDownloader(timeout=7.0)._open_session(session)
        assert session.calls == [("get", LOGIN_URL, {"timeout": 7.0})]

    def test_missing_jsessionid_is_an_error(self):
        session = FakeSession([FakeResponse(b"<html>")], cookies=())
        with pytest.raises(OcctoDownloadError, match="did not issue a session cookie"):
            OcctoBulkDownloader()._open_session(session)

    def test_http_error_propagates(self):
        session = FakeSession([FakeResponse(b"", status=503)])
        with pytest.raises(requests.HTTPError):
            OcctoBulkDownloader()._open_session(session)


class TestIssueDownloadKey:
    selection = {"tabSntk": "1", "areaDataKnd": "32", "areaAllTermDwld": "Y"}

    def issue(self, response: FakeResponse) -> tuple[FakeSession, tuple[str, str] | None]:
        session = FakeSession([response])
        result = OcctoBulkDownloader(timeout=9.0)._issue_download_key(
            cast(requests.Session, session), self.selection
        )
        return session, result

    def test_posts_the_ajax_ok_request_and_returns_the_key_pair(self):
        session, result = self.issue(ok_response("KEY-1", "TOKEN-1"))
        assert result == ("KEY-1", "TOKEN-1")
        assert session.calls == [
            (
                "post",
                SCREEN_URL,
                {
                    "data": {
                        **FRAMEWORK_OK,
                        "requestToken": "",
                        "downloadKey": "",
                        "tabSntk": "1",
                        "areaDataKnd": "32",
                        "areaAllTermDwld": "Y",
                    },
                    "headers": {"sdReqType": "AJAX"},
                    "timeout": 9.0,
                },
            )
        ]

    def test_non_json_body_is_an_error(self):
        with pytest.raises(OcctoDownloadError, match=r"Unexpected reference/ok response: '<html>"):
            self.issue(FakeResponse(b"<html>session expired</html>"))

    def test_json_without_root_is_an_error(self):
        with pytest.raises(OcctoDownloadError, match="Unexpected reference/ok response"):
            self.issue(json_response({"status": "ok"}))

    def test_interceptor_error(self):
        with pytest.raises(OcctoDownloadError, match="OCCTO session error: timeout CF000001"):
            self.issue(json_response({"root": {"interceptorErr": "timeout CF000001"}}))

    def test_validation_error_message(self):
        with pytest.raises(OcctoDownloadError, match="OCCTO rejected the selection: too many rows"):
            self.issue(json_response({"root": {"errMessage": "too many rows"}}))

    @pytest.mark.parametrize(
        "root",
        [
            {},
            {"bizRoot": None},
            {"bizRoot": {"header": None}},
            {"bizRoot": {"header": {"downloadKey": {"value": "K"}}}},
        ],
    )
    def test_missing_key_or_token_is_an_error(self, root):
        with pytest.raises(OcctoDownloadError, match="lacks downloadKey/requestToken"):
            self.issue(json_response({"root": root}))

    def test_http_error_propagates(self):
        with pytest.raises(requests.HTTPError):
            self.issue(FakeResponse(b"", status=500))


class TestFetchCsv:
    selection = {"tabSntk": "1", "areaDataKnd": "32", "areaAllTermDwld": "Y"}

    def fetch(self, response: FakeResponse) -> tuple[FakeSession, bytes]:
        session = FakeSession([response])
        dl = OcctoBulkDownloader(timeout=11.0)
        return session, dl._fetch_csv(
            cast(requests.Session, session), self.selection, "KEY-1", "TOKEN-1"
        )

    def test_posts_the_key_pair_and_returns_the_attachment_bytes(self):
        payload = cp932(DEMAND_HEADER + "\nrow\n")
        session, content = self.fetch(csv_response(payload))
        assert content == payload
        assert session.calls == [
            (
                "post",
                SCREEN_URL,
                {
                    "data": {
                        **FRAMEWORK_DOWNLOAD,
                        "requestToken": "TOKEN-1",
                        "downloadKey": "KEY-1",
                        "tabSntk": "1",
                        "areaDataKnd": "32",
                        "areaAllTermDwld": "Y",
                    },
                    "headers": None,
                    "timeout": 11.0,
                },
            )
        ]

    def test_non_attachment_response_is_an_error(self):
        html = FakeResponse(b"<html>expired</html>", headers={"Content-Type": "text/html"})
        with pytest.raises(OcctoDownloadError, match=r"Content-Type='text/html'.*<html>expired"):
            self.fetch(html)

    def test_http_error_propagates(self):
        with pytest.raises(requests.HTTPError):
            self.fetch(FakeResponse(b"", status=502))


# --------------------------------------------------------------------------- download()


class TestDownload:
    def test_unknown_dataset(self, tmp_path):
        factory = FakeSessionFactory()
        dl = OcctoBulkDownloader(data_dir=tmp_path, session_factory=factory)
        with pytest.raises(
            ValueError,
            match=r"Unknown dataset 'nope'; expected one of "
            r"\['area_reserve_rate_dad', 'demand_forecast_dad'\]",
        ):
            dl.download("nope")
        assert factory.created == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"target_date_from": datetime.date(2025, 4, 1)},
            {"target_date_to": datetime.date(2025, 4, 1)},
        ],
    )
    def test_date_bounds_must_come_together(self, tmp_path, kwargs):
        dl = OcctoBulkDownloader(data_dir=tmp_path, session_factory=FakeSessionFactory())
        with pytest.raises(ValueError, match="must be given together"):
            dl.download("demand_forecast_dad", **kwargs)

    def test_inverted_range(self, tmp_path):
        dl = OcctoBulkDownloader(data_dir=tmp_path, session_factory=FakeSessionFactory())
        with pytest.raises(ValueError, match="target_date_from must not be after target_date_to"):
            dl.download(
                "demand_forecast_dad",
                target_date_from=datetime.date(2025, 4, 2),
                target_date_to=datetime.date(2025, 4, 1),
            )

    def test_equal_bounds_are_a_valid_single_day_range(self, tmp_path):
        payload = cp932(RESERVE_HEADER + "\n2025/04/01,A\n")
        session = FakeSession(
            [FakeResponse(b"<html>"), ok_response("KEY-1", "TOKEN-1"), csv_response(payload)]
        )
        dl = OcctoBulkDownloader(data_dir=tmp_path, session_factory=FakeSessionFactory(session))

        path = dl.download(
            "area_reserve_rate_dad",
            target_date_from=datetime.date(2025, 4, 1),
            target_date_to=datetime.date(2025, 4, 1),
        )

        assert path.read_bytes() == payload
        posts = [kw["data"] for kind, _, kw in session.calls if kind == "post"]
        assert [(d["areaNngpFrom"], d["areaNngpTo"]) for d in posts] == [
            ("2025/04/01", "2025/04/01"),
            ("2025/04/01", "2025/04/01"),
        ]

    def test_all_term_download_writes_the_csv_atomically(self, tmp_path):
        payload = cp932(DEMAND_HEADER + "\n2025/03/30,2025/04/01,東京,04:00,20000,x\n")
        session = FakeSession(
            [FakeResponse(b"<html>"), ok_response("KEY-1", "TOKEN-1"), csv_response(payload)]
        )
        factory = FakeSessionFactory(session)
        dl = OcctoBulkDownloader(data_dir=tmp_path, timeout=5.0, session_factory=factory)
        # A previous download is always overwritten.
        dest = tmp_path / "demand_forecast_dad" / "demand_forecast_dad.csv"
        dest.parent.mkdir()
        dest.write_bytes(b"stale")

        path = dl.download("demand_forecast_dad")

        assert path == dest
        assert path.read_bytes() == payload
        assert sorted(p.name for p in dest.parent.iterdir()) == ["demand_forecast_dad.csv"]
        assert session.closed
        selection = {**AREA_SELECTION, "areaDataKnd": "32", "areaAllTermDwld": "Y"}
        assert session.calls == [
            ("get", LOGIN_URL, {"timeout": 5.0}),
            (
                "post",
                SCREEN_URL,
                {
                    "data": {**FRAMEWORK_OK, "requestToken": "", "downloadKey": "", **selection},
                    "headers": {"sdReqType": "AJAX"},
                    "timeout": 5.0,
                },
            ),
            (
                "post",
                SCREEN_URL,
                {
                    "data": {
                        **FRAMEWORK_DOWNLOAD,
                        "requestToken": "TOKEN-1",
                        "downloadKey": "KEY-1",
                        **selection,
                    },
                    "headers": None,
                    "timeout": 5.0,
                },
            ),
        ]

    def test_chunked_download_concatenates_the_windows(self, tmp_path):
        chunk1 = cp932(RESERVE_HEADER + "\n2025/04/01,A\n2026/01/25,B\n")
        chunk2 = cp932(RESERVE_HEADER + "\n2026/01/26,C")  # no trailing newline
        session = FakeSession(
            [
                FakeResponse(b"<html>"),
                ok_response("KEY-1", "TOKEN-1"),
                csv_response(chunk1),
                ok_response("KEY-2", "TOKEN-2"),
                csv_response(chunk2),
            ]
        )
        dl = OcctoBulkDownloader(
            data_dir=tmp_path, timeout=5.0, session_factory=FakeSessionFactory(session)
        )

        path = dl.download(
            "area_reserve_rate_dad",
            target_date_from=datetime.date(2025, 4, 1),
            target_date_to=datetime.date(2026, 1, 26),
        )

        assert path == tmp_path / "area_reserve_rate_dad" / "area_reserve_rate_dad.csv"
        assert path.read_bytes() == cp932(
            RESERVE_HEADER + "\n2025/04/01,A\n2026/01/25,B\n2026/01/26,C\n"
        )
        assert sorted(p.name for p in path.parent.iterdir()) == ["area_reserve_rate_dad.csv"]
        # One LOGIN_login, then an ok/download pair per window with that window's dates.
        assert [(kind, url) for kind, url, _ in session.calls] == [
            ("get", LOGIN_URL),
            ("post", SCREEN_URL),
            ("post", SCREEN_URL),
            ("post", SCREEN_URL),
            ("post", SCREEN_URL),
        ]
        posts = [kw["data"] for kind, _, kw in session.calls if kind == "post"]
        assert [(d["areaNngpFrom"], d["areaNngpTo"]) for d in posts] == [
            ("2025/04/01", "2026/01/25"),
            ("2025/04/01", "2026/01/25"),
            ("2026/01/26", "2026/01/26"),
            ("2026/01/26", "2026/01/26"),
        ]
        assert [(d["downloadKey"], d["requestToken"]) for d in posts] == [
            ("", ""),
            ("KEY-1", "TOKEN-1"),
            ("", ""),
            ("KEY-2", "TOKEN-2"),
        ]
        assert [d["fwExtention.actionSubType"] for d in posts] == ["ok", "download"] * 2
        assert all(d["areaDataKnd"] == "31" and "areaAllTermDwld" not in d for d in posts)

    def test_bad_window_aborts_before_anything_is_written(self, tmp_path):
        session = FakeSession(
            [
                FakeResponse(b"<html>"),
                ok_response("KEY-1", "TOKEN-1"),
                csv_response(b"<html>maintenance</html>"),
            ]
        )
        dl = OcctoBulkDownloader(data_dir=tmp_path, session_factory=FakeSessionFactory(session))
        with pytest.raises(OcctoDownloadError, match="unexpected header row"):
            dl.download("demand_forecast_dad")
        assert not (tmp_path / "demand_forecast_dad").exists()
        assert session.closed

    def test_each_download_opens_a_fresh_session(self, tmp_path):
        payload = cp932(DEMAND_HEADER + "\nrow\n")
        first = FakeSession(
            [FakeResponse(b"<html>"), ok_response("K1", "T1"), csv_response(payload)]
        )
        second = FakeSession(
            [FakeResponse(b"<html>"), ok_response("K2", "T2"), csv_response(payload)]
        )
        factory = FakeSessionFactory(first, second)
        dl = OcctoBulkDownloader(data_dir=tmp_path, session_factory=factory)

        dl.download("demand_forecast_dad")
        dl.download("demand_forecast_dad")

        assert factory.created == [first, second]
        assert first.closed and second.closed
        assert [c[0] for c in first.calls] == ["get", "post", "post"]
        assert [c[0] for c in second.calls] == ["get", "post", "post"]
