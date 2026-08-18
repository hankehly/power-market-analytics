"""Tests for the JEPX spot-price CSV downloader (power_market_analytics.jepx)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import requests

from power_market_analytics import jepx
from power_market_analytics.jepx import JepxSpotDownloader, current_fiscal_year


class TestCurrentFiscalYear:
    def test_april_first_starts_the_new_fiscal_year(self):
        assert current_fiscal_year(datetime.date(2024, 4, 1)) == 2024

    def test_march_31_still_belongs_to_the_previous_fiscal_year(self):
        assert current_fiscal_year(datetime.date(2024, 3, 31)) == 2023

    def test_january_belongs_to_the_previous_calendar_year_fy(self):
        assert current_fiscal_year(datetime.date(2025, 1, 15)) == 2024

    def test_december_belongs_to_the_same_calendar_year_fy(self):
        assert current_fiscal_year(datetime.date(2024, 12, 31)) == 2024

    def test_defaults_to_today(self):
        today = datetime.date.today()
        expected = today.year if today.month >= 4 else today.year - 1
        assert current_fiscal_year() == expected


# --------------------------------------------------------------------------- downloader


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

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


CSV_BYTES = "受渡日,時刻コード,売り入札量(kWh)\r\n2024/04/01,1,100\r\n".encode("cp932")


class TestJepxSpotDownloader:
    def test_defaults(self):
        dl = JepxSpotDownloader()
        assert dl.data_dir == Path("data/jepx/spot")
        assert dl.timeout == 60.0
        assert isinstance(dl.session, requests.Session)

    def test_path_for(self, tmp_path):
        dl = JepxSpotDownloader(data_dir=tmp_path)
        assert dl.path_for(2024) == tmp_path / "spot_2024.csv"

    def test_download_writes_the_file_and_creates_data_dir(self, tmp_path):
        data_dir = tmp_path / "nested" / "spot"
        session = FakeSession(FakeResponse(CSV_BYTES))
        dl = JepxSpotDownloader(data_dir=data_dir, timeout=12.5, session=session)

        path = dl.download(2024)

        assert path == data_dir / "spot_2024.csv"
        assert path.read_bytes() == CSV_BYTES
        assert session.calls == [("https://www.jepx.jp/market/excel/spot_2024.csv", 12.5)]
        # Atomic write: the temp file is renamed away, never left behind.
        assert sorted(p.name for p in data_dir.iterdir()) == ["spot_2024.csv"]

    def test_download_uses_the_cache_without_an_http_call(self, tmp_path):
        cached = tmp_path / "spot_2020.csv"
        cached.write_bytes(b"cached")
        session = FakeSession(FakeResponse(CSV_BYTES))
        dl = JepxSpotDownloader(data_dir=tmp_path, session=session)

        assert dl.download(2020) == cached
        assert cached.read_bytes() == b"cached"
        assert session.calls == []

    def test_force_redownloads_over_the_cache(self, tmp_path):
        cached = tmp_path / "spot_2020.csv"
        cached.write_bytes(b"stale")
        session = FakeSession(FakeResponse(CSV_BYTES))
        dl = JepxSpotDownloader(data_dir=tmp_path, session=session)

        assert dl.download(2020, force=True) == cached
        assert cached.read_bytes() == CSV_BYTES
        assert session.calls == [("https://www.jepx.jp/market/excel/spot_2020.csv", 60.0)]

    def test_http_error_propagates_and_writes_nothing(self, tmp_path):
        session = FakeSession(FakeResponse(b"", status=404))
        dl = JepxSpotDownloader(data_dir=tmp_path / "spot", session=session)
        with pytest.raises(requests.HTTPError):
            dl.download(2020)
        assert not (tmp_path / "spot").exists()

    @pytest.mark.parametrize("fiscal_year", [2015, 9999])
    def test_rejects_fiscal_years_outside_the_published_range(self, tmp_path, fiscal_year):
        session = FakeSession(FakeResponse(CSV_BYTES))
        dl = JepxSpotDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(ValueError, match="fiscal_year must be between"):
            dl.download(fiscal_year)
        assert session.calls == []

    def test_bounds_are_inclusive_of_earliest_and_current_fiscal_year(self, tmp_path, monkeypatch):
        # Pin the clock so the upper boundary and the message are exact.
        monkeypatch.setattr(jepx, "current_fiscal_year", lambda today=None: 2024)
        session = FakeSession(FakeResponse(CSV_BYTES))
        dl = JepxSpotDownloader(data_dir=tmp_path, session=session)

        assert dl.download(2016) == tmp_path / "spot_2016.csv"
        assert dl.download(2024) == tmp_path / "spot_2024.csv"
        with pytest.raises(ValueError, match=r"between 2016 and 2024 \(got 2025\)"):
            dl.download(2025)
        with pytest.raises(ValueError, match=r"between 2016 and 2024 \(got 2015\)"):
            dl.download(2015)
        assert session.calls == [
            ("https://www.jepx.jp/market/excel/spot_2016.csv", 60.0),
            ("https://www.jepx.jp/market/excel/spot_2024.csv", 60.0),
        ]
