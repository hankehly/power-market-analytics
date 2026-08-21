"""Tests for the MSM GPV downloader / per-day extraction pipeline (``MsmDownloader``).

HTTP is never real: the downloader takes an injectable ``session`` whose
``get(url, timeout, stream=...)`` returns canned responses shaped like
``requests.Response`` (``status_code``, ``raise_for_status``, a streamed
``iter_content``). GRIB2 payloads are real bytes built by
:mod:`tests.msm_grib_support` (the same fixture builder the decode-layer
tests use), so the happy path exercises the real decode layer end to end
rather than a stand-in.
"""

from __future__ import annotations

import csv
import datetime
import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest
import requests

from power_market_analytics import msm_grib
from power_market_analytics.msm import (
    EARLIEST_DELIVERY_DATE,
    MSM_SURFACE_ELEMENTS,
    RAW_CSV_COLUMNS,
    MsmGrid,
    MsmStation,
    reference_at_for,
    source_files_for,
)
from power_market_analytics.msm_grib import MsmDownloader, MsmDownloadError, MsmExtractError
from tests.msm_grib_support import build_message, day_messages

DELIVERY_DATE = datetime.date(2026, 8, 19)
REFERENCE_AT = reference_at_for(DELIVERY_DATE)
SOURCE_FILES = source_files_for(DELIVERY_DATE)

#: Tiny grid: latitudes 36.0/35.5, longitudes 139.0/139.5.
GRID = MsmGrid(
    ni=2, nj=2, first_latitude=36.0, first_longitude=139.0, latitude_step=-0.5, longitude_step=0.5
)
#: Both stations sit exactly on a grid point (grid_distance_km == 0).
STATIONS = (
    MsmStation("s00001", 36.0, 139.0),
    MsmStation("s00002", 35.5, 139.5),
)

SAMPLE_ELEMENT = MSM_SURFACE_ELEMENTS[0]


def sample_message_bytes() -> bytes:
    """One minimal, real GRIB2 message — enough to satisfy download_file's checks."""
    return build_message(
        SAMPLE_ELEMENT,
        lead_hours=28,
        reference_at=REFERENCE_AT,
        grid=GRID,
        values=[1013.25] * (GRID.ni * GRID.nj),
    )


def constant_value(element_key: str, lead_hours: int, flat_index: int) -> float:
    from tests.msm_grib_support import ELEMENT_BASE_VALUES

    return ELEMENT_BASE_VALUES[element_key]


def file_content(source_file, missing=None) -> bytes:
    """A complete, real archive member for one of DELIVERY_DATE's three files."""
    return b"".join(day_messages(source_file, REFERENCE_AT, GRID, constant_value, missing=missing))


def complete_session(missing_for_file0=None) -> "FakeSession":
    """A session serving all three of DELIVERY_DATE's source files."""
    responses = {}
    for i, sf in enumerate(SOURCE_FILES):
        missing = missing_for_file0 if i == 0 else None
        responses[sf.url] = FakeResponse(file_content(sf, missing=missing))
    return FakeSession(responses)


class FakeResponse:
    """Stand-in for requests.Response: status, raise_for_status, streamed content."""

    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400 and self.status_code != 404:
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_content(self, chunk_size: int):
        content = self.content
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]


class FakeSession:
    """Stand-in for requests.Session serving canned responses by URL (404 if absent)."""

    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = dict(responses)
        self.calls: list[str] = []

    def get(self, url: str, timeout: float, stream: bool = False) -> FakeResponse:
        self.calls.append(url)
        if url not in self.responses:
            return FakeResponse(b"not found", status=404)
        return self.responses[url]


class ScriptedSession:
    """Replays a fixed sequence of responses/exceptions for one URL, repeating the last."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[str] = []

    def get(self, url: str, timeout: float, stream: bool = False):
        self.calls.append(url)
        effect = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(effect, Exception):
            raise effect
        return effect


# --------------------------------------------------------------------------- paths / defaults


class TestDownloaderDefaults:
    def test_defaults(self):
        dl = MsmDownloader()
        assert dl.data_dir == Path("data/jma/msm_surface_forecast")
        assert dl.grib_dir == Path("data/jma/msm_surface_forecast/grib")
        assert dl.csv_dir == Path("data/jma/msm_surface_forecast/csv")
        assert dl.timeout == 60.0
        assert isinstance(dl.session, requests.Session)
        assert dl.request_interval == 1.0
        assert dl.max_attempts == 3

    def test_path_helpers(self, tmp_path):
        dl = MsmDownloader(data_dir=tmp_path)
        assert dl.csv_path_for(DELIVERY_DATE) == tmp_path / "csv" / "msm_surface_20260819.csv.gz"
        assert dl.manifest_path_for(DELIVERY_DATE) == tmp_path / "csv" / "msm_surface_20260819.json"
        assert dl.grib_path_for(SOURCE_FILES[0]) == tmp_path / "grib" / SOURCE_FILES[0].file_name


# --------------------------------------------------------------------------- download_file


class TestDownloadFile:
    def test_downloads_streams_and_hashes(self, tmp_path):
        content = sample_message_bytes()
        sf = SOURCE_FILES[0]
        session = FakeSession({sf.url: FakeResponse(content)})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        path, sha256_hex = dl.download_file(sf)

        assert path == tmp_path / "grib" / sf.file_name
        assert path.read_bytes() == content
        assert sha256_hex == hashlib.sha256(content).hexdigest()
        assert session.calls == [sf.url]
        assert list((tmp_path / "grib").glob("*.part")) == []

    def test_streams_content_spanning_multiple_chunks(self, tmp_path):
        # Real archive members are far larger than one 1 MiB chunk; pad past
        # the magic-bytes header so streaming actually spans several reads.
        content = sample_message_bytes() + b"\x00" * (2 * msm_grib.DOWNLOAD_CHUNK_BYTES)
        sf = SOURCE_FILES[0]
        session = FakeSession({sf.url: FakeResponse(content)})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        path, sha256_hex = dl.download_file(sf)

        assert path.read_bytes() == content
        assert sha256_hex == hashlib.sha256(content).hexdigest()

    def test_cached_grib_is_reused_without_http(self, tmp_path):
        sf = SOURCE_FILES[0]
        session = FakeSession({})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dest = dl.grib_path_for(sf)
        dest.parent.mkdir(parents=True)
        content = sample_message_bytes()
        dest.write_bytes(content)

        path, sha256_hex = dl.download_file(sf)

        assert path == dest
        assert sha256_hex == hashlib.sha256(content).hexdigest()
        assert session.calls == []

    def test_force_redownloads_even_if_cached(self, tmp_path):
        sf = SOURCE_FILES[0]
        content = sample_message_bytes()
        session = FakeSession({sf.url: FakeResponse(content)})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dest = dl.grib_path_for(sf)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"stale-content")

        path, sha256_hex = dl.download_file(sf, force=True)

        assert path.read_bytes() == content
        assert sha256_hex == hashlib.sha256(content).hexdigest()
        assert session.calls == [sf.url]

    def test_404_raises_naming_the_url_and_leaves_nothing(self, tmp_path):
        sf = SOURCE_FILES[0]
        session = FakeSession({sf.url: FakeResponse(b"missing", status=404)})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        with pytest.raises(MsmDownloadError, match=re.escape(sf.url)):
            dl.download_file(sf)

        assert not dl.grib_path_for(sf).exists()
        assert list(tmp_path.rglob("*.part")) == []

    def test_non_grib_content_is_rejected(self, tmp_path):
        sf = SOURCE_FILES[0]
        session = FakeSession({sf.url: FakeResponse(b"<html>not grib</html>")})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        with pytest.raises(MsmDownloadError, match="not a GRIB2"):
            dl.download_file(sf)

        assert not dl.grib_path_for(sf).exists()
        assert list(tmp_path.rglob("*.part")) == []

    def test_empty_body_is_rejected(self, tmp_path):
        sf = SOURCE_FILES[0]
        session = FakeSession({sf.url: FakeResponse(b"")})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        with pytest.raises(MsmDownloadError, match="not a GRIB2"):
            dl.download_file(sf)

        assert not dl.grib_path_for(sf).exists()

    def test_one_connection_error_then_success(self, tmp_path):
        sf = SOURCE_FILES[0]
        content = sample_message_bytes()
        session = ScriptedSession([requests.ConnectionError("boom"), FakeResponse(content)])
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0, max_attempts=3)

        path, sha256_hex = dl.download_file(sf)

        assert path.read_bytes() == content
        assert sha256_hex == hashlib.sha256(content).hexdigest()
        assert len(session.calls) == 2

    def test_server_error_status_is_retried_then_succeeds(self, tmp_path):
        sf = SOURCE_FILES[0]
        content = sample_message_bytes()
        session = ScriptedSession([FakeResponse(b"", status=500), FakeResponse(content)])
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0, max_attempts=3)

        path, sha256_hex = dl.download_file(sf)

        assert path.read_bytes() == content
        assert len(session.calls) == 2

    def test_retries_exhausted_reraises_the_underlying_exception(self, tmp_path):
        sf = SOURCE_FILES[0]
        session = ScriptedSession([requests.ConnectionError("boom")])
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0, max_attempts=3)

        with pytest.raises(requests.ConnectionError):
            dl.download_file(sf)

        assert len(session.calls) == 3
        assert not dl.grib_path_for(sf).exists()


class TestThrottle:
    def test_consecutive_requests_are_spaced_by_the_interval(self, tmp_path, monkeypatch):
        clock = {"now": 100.0}
        sleeps: list[float] = []
        monkeypatch.setattr(msm_grib.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(msm_grib.time, "sleep", lambda s: sleeps.append(s))
        sf0, sf1, sf2 = SOURCE_FILES
        session = FakeSession(
            {
                sf0.url: FakeResponse(sample_message_bytes()),
                sf1.url: FakeResponse(sample_message_bytes()),
                sf2.url: FakeResponse(sample_message_bytes()),
            }
        )
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0.5)

        dl.download_file(sf0)  # first request: no wait
        clock["now"] += 0.2
        dl.download_file(sf1)  # 0.2 s later: waits the remaining 0.3 s
        clock["now"] += 1.0
        dl.download_file(sf2)  # long after: no wait

        assert sleeps == pytest.approx([0.3])
        assert dl.request_interval == 0.5

    def test_default_interval_is_polite_but_short(self):
        assert MsmDownloader().request_interval == 1.0


# --------------------------------------------------------------------------- extract_day


class TestExtractDay:
    def test_happy_path_writes_csv_and_manifest_and_deletes_gribs(self, tmp_path):
        session = complete_session(missing_for_file0={"total_cloud_cover_pct": [0]})
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        csv_path = dl.extract_day(DELIVERY_DATE, STATIONS)

        assert csv_path == dl.csv_path_for(DELIVERY_DATE)
        assert csv_path.exists()
        with gzip.open(csv_path, "rt", newline="") as f:
            rows = list(csv.reader(f))
        header, data_rows = rows[0], rows[1:]
        assert header == list(RAW_CSV_COLUMNS)
        assert len(data_rows) == len(STATIONS) * 24

        manifest = json.loads(dl.manifest_path_for(DELIVERY_DATE).read_text())
        assert manifest["delivery_date"] == DELIVERY_DATE.isoformat()
        assert manifest["reference_at_utc"] == REFERENCE_AT.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert [f["file_name"] for f in manifest["files"]] == [sf.file_name for sf in SOURCE_FILES]
        for f, sf in zip(manifest["files"], SOURCE_FILES, strict=True):
            content = session.responses[sf.url].content
            assert f["url"] == sf.url
            assert f["sha256"] == hashlib.sha256(content).hexdigest()
            assert f["size_bytes"] == len(content)

        # GRIBs deleted after a successful extract.
        assert list((tmp_path / "grib").glob("*")) == []

        idx = {name: i for i, name in enumerate(header)}
        # Bitmap-missing value -> empty CSV cell.
        null_cells = [
            r
            for r in data_rows
            if r[idx["station_id"]] == "s00001" and r[idx["total_cloud_cover_pct"]] == ""
        ]
        assert len(null_cells) > 0
        # A non-missing double is a plain rounded float string.
        assert data_rows[0][idx["surface_pressure_hpa"]] != ""

        # Timestamps end with Z and parse back to the expected UTC instants.
        sample = data_rows[0]
        assert sample[idx["forecast_reference_at_utc"]].endswith("Z")
        parsed_reference = datetime.datetime.strptime(
            sample[idx["forecast_reference_at_utc"]], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc)
        assert parsed_reference == REFERENCE_AT

    def test_keep_grib_retains_the_downloaded_files(self, tmp_path):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        dl.extract_day(DELIVERY_DATE, STATIONS, keep_grib=True)

        for sf in SOURCE_FILES:
            assert dl.grib_path_for(sf).exists()

    def test_cached_csv_short_circuits_with_no_http(self, tmp_path):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.extract_day(DELIVERY_DATE, STATIONS)
        session.calls.clear()

        path = dl.extract_day(DELIVERY_DATE, STATIONS)

        assert path == dl.csv_path_for(DELIVERY_DATE)
        assert session.calls == []

    def test_force_redownloads_and_rewrites(self, tmp_path):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.extract_day(DELIVERY_DATE, STATIONS)
        session.calls.clear()

        dl.extract_day(DELIVERY_DATE, STATIONS, force=True)

        assert session.calls == [sf.url for sf in SOURCE_FILES]

    def test_cached_grib_is_reused_when_csv_is_absent(self, tmp_path):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)
        dl.extract_day(DELIVERY_DATE, STATIONS, keep_grib=True)
        dl.csv_path_for(DELIVERY_DATE).unlink()
        dl.manifest_path_for(DELIVERY_DATE).unlink()
        session.calls.clear()

        path = dl.extract_day(DELIVERY_DATE, STATIONS)

        assert path.exists()
        assert session.calls == []

    def test_download_failure_propagates_and_leaves_earlier_gribs(self, tmp_path):
        session = complete_session()
        session.responses[SOURCE_FILES[1].url] = FakeResponse(b"missing", status=404)
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        with pytest.raises(MsmDownloadError):
            dl.extract_day(DELIVERY_DATE, STATIONS)

        assert not dl.csv_path_for(DELIVERY_DATE).exists()
        assert dl.grib_path_for(SOURCE_FILES[0]).exists()
        assert not dl.grib_path_for(SOURCE_FILES[1]).exists()

    def test_incomplete_source_file_raises_and_keeps_gribs(self, tmp_path):
        session = complete_session()
        incomplete = b"".join(
            day_messages(
                SOURCE_FILES[2], REFERENCE_AT, GRID, constant_value, omit=[("temperature_k", 40)]
            )
        )
        session.responses[SOURCE_FILES[2].url] = FakeResponse(incomplete)
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        with pytest.raises(MsmExtractError):
            dl.extract_day(DELIVERY_DATE, STATIONS)

        assert not dl.csv_path_for(DELIVERY_DATE).exists()
        for sf in SOURCE_FILES:
            assert dl.grib_path_for(sf).exists()

    def test_record_count_mismatch_raises(self, tmp_path, monkeypatch):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)
        monkeypatch.setattr(msm_grib, "extract_station_records", lambda *a, **k: [])

        with pytest.raises(MsmExtractError, match="expected 48"):
            dl.extract_day(DELIVERY_DATE, STATIONS)

    def _assert_retry_actually_rewrites(self, dl):
        """Nothing at the csv path short-circuits the cache check, so a later
        non-force call must re-run the full pipeline and produce a correct,
        complete extract — not silently report the day as already done."""
        assert not dl.csv_path_for(DELIVERY_DATE).exists()
        # GRIBs from the failed attempt are kept for inspection, per contract.
        for sf in SOURCE_FILES:
            assert dl.grib_path_for(sf).exists()

        path = dl.extract_day(DELIVERY_DATE, STATIONS)

        assert path.exists()
        with gzip.open(path, "rt", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == list(RAW_CSV_COLUMNS)
        assert len(rows) - 1 == len(STATIONS) * 24
        assert dl.manifest_path_for(DELIVERY_DATE).exists()
        # The retry succeeded end to end, so the usual GRIB cleanup ran too.
        for sf in SOURCE_FILES:
            assert not dl.grib_path_for(sf).exists()

    def test_csv_write_failure_cleans_up_and_a_later_call_retries(self, tmp_path, monkeypatch):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        with monkeypatch.context() as m:
            m.setattr(msm_grib.gzip, "open", boom)
            with pytest.raises(OSError):
                dl.extract_day(DELIVERY_DATE, STATIONS)

        assert list(dl.csv_dir.rglob("*.part")) == []
        self._assert_retry_actually_rewrites(dl)

    def test_manifest_write_failure_leaves_no_csv_and_a_later_call_retries(
        self, tmp_path, monkeypatch
    ):
        session = complete_session()
        dl = MsmDownloader(data_dir=tmp_path, session=session, request_interval=0)

        def boom(*args, **kwargs):
            raise TypeError("not serializable")

        with monkeypatch.context() as m:
            m.setattr(msm_grib.json, "dumps", boom)
            with pytest.raises(TypeError):
                dl.extract_day(DELIVERY_DATE, STATIONS)

        assert list(dl.csv_dir.rglob("*.part")) == []
        # The manifest is written before the csv (csv commit is the sole
        # "day done" signal the cache check trusts), so a manifest-write
        # failure must leave no file at the final csv path either — the bug
        # this test pins: previously the csv was written first and survived
        # a manifest-write failure, so a later non-force call would
        # short-circuit on the stale "cached" csv.gz without ever noticing
        # the manifest was never written.
        self._assert_retry_actually_rewrites(dl)


# --------------------------------------------------------------------------- download_range


class TestDownloadRange:
    def test_inclusive_walk_returns_paths_in_date_order(self, tmp_path, monkeypatch):
        dl = MsmDownloader(data_dir=tmp_path, session=FakeSession({}), request_interval=0)
        calls: list[tuple[datetime.date, bool, bool]] = []

        def fake_extract_day(delivery_date, stations, force=False, keep_grib=False):
            calls.append((delivery_date, force, keep_grib))
            return dl.csv_path_for(delivery_date)

        monkeypatch.setattr(dl, "extract_day", fake_extract_day)

        start, end = datetime.date(2026, 8, 19), datetime.date(2026, 8, 21)
        paths = dl.download_range(start, end, STATIONS, force=True, keep_grib=True)

        assert calls == [
            (datetime.date(2026, 8, 19), True, True),
            (datetime.date(2026, 8, 20), True, True),
            (datetime.date(2026, 8, 21), True, True),
        ]
        assert paths == [dl.csv_path_for(d) for d, _, _ in calls]

    def test_reversed_range_raises(self, tmp_path):
        dl = MsmDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(ValueError, match="after"):
            dl.download_range(datetime.date(2026, 8, 21), datetime.date(2026, 8, 19), STATIONS)

    def test_start_before_earliest_delivery_date_raises(self, tmp_path):
        dl = MsmDownloader(data_dir=tmp_path, session=FakeSession({}))
        before = EARLIEST_DELIVERY_DATE - datetime.timedelta(days=1)
        with pytest.raises(ValueError, match="EARLIEST_DELIVERY_DATE"):
            dl.download_range(before, before, STATIONS)
