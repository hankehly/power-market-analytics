"""Tests for scripts/update_holidays_seed.py (Cabinet Office holidays → dbt seed)."""

from __future__ import annotations

import types

import pytest
import requests

from tests.support import REPO_ROOT, import_script

SOURCE_HEADER = "国民の祝日・休日月日,国民の祝日・休日名称"

#: The Cabinet Office file: Shift_JIS, CRLF, unpadded month/day.
SOURCE_CONTENT = (
    f"{SOURCE_HEADER}\r\n2025/1/13,成人の日\r\n2025/1/1,元日\r\n2025/11/3,文化の日\r\n"
).encode("shift_jis")

#: What the seed must look like for SOURCE_CONTENT: UTF-8, CRLF (csv.writer's
#: default line terminator), sorted ISO dates.
EXPECTED_SEED = (
    "holiday_date,holiday_name_ja\r\n2025-01-01,元日\r\n2025-01-13,成人の日\r\n2025-11-03,文化の日\r\n"
).encode("utf-8")


@pytest.fixture
def script():
    return import_script("update_holidays_seed")


class TestParseHolidays:
    def test_converts_unpadded_slashed_dates_to_iso_in_source_order(self, script):
        assert script.parse_holidays(SOURCE_CONTENT) == [
            ("2025-01-13", "成人の日"),
            ("2025-01-01", "元日"),
            ("2025-11-03", "文化の日"),
        ]

    def test_rejects_an_unexpected_header(self, script):
        content = "date,name\r\n2025/1/1,元日\r\n".encode("shift_jis")
        with pytest.raises(ValueError, match=r"Unexpected source header: \['date', 'name'\]"):
            script.parse_holidays(content)

    def test_rejects_empty_content(self, script):
        with pytest.raises(ValueError, match="Unexpected source header: None"):
            script.parse_holidays(b"")

    def test_rejects_a_header_only_file(self, script):
        with pytest.raises(ValueError, match="no holiday rows"):
            script.parse_holidays(f"{SOURCE_HEADER}\r\n".encode("shift_jis"))

    def test_rejects_an_unparsable_date(self, script):
        content = f"{SOURCE_HEADER}\r\n2025/13/1,元日\r\n".encode("shift_jis")
        with pytest.raises(ValueError, match="does not match format"):
            script.parse_holidays(content)


class TestWriteSeed:
    def test_writes_utf8_crlf_sorted_by_date(self, script, tmp_path):
        dest = tmp_path / "seed.csv"
        script.write_seed(
            [("2025-01-13", "成人の日"), ("2025-01-01", "元日"), ("2025-11-03", "文化の日")], dest
        )
        assert dest.read_bytes() == EXPECTED_SEED

    def test_overwrites_an_existing_seed(self, script, tmp_path):
        dest = tmp_path / "seed.csv"
        dest.write_bytes(b"holiday_date,holiday_name_ja\r\n1955-01-01,old\r\n")
        script.write_seed([("2025-01-01", "元日")], dest)
        assert dest.read_bytes() == "holiday_date,holiday_name_ja\r\n2025-01-01,元日\r\n".encode()


# --------------------------------------------------------------------------- main()


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


@pytest.fixture
def http(script, monkeypatch):
    """Replace the script's ``requests`` module with a recording ``get``."""
    state = {"calls": [], "response": FakeResponse(SOURCE_CONTENT)}

    def get(url, timeout):
        state["calls"].append((url, timeout))
        return state["response"]

    monkeypatch.setattr(script, "requests", types.SimpleNamespace(get=get))
    return state


class TestMain:
    def test_default_paths(self, script):
        assert script.SEED_PATH == REPO_ROOT / "dbt/seeds/jpn_national_holidays.csv"
        assert script.SOURCE_URL == "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"

    def test_fetches_the_cabinet_office_csv_and_writes_dest(self, script, http, tmp_path):
        dest = tmp_path / "holidays.csv"
        script.main(["--dest", str(dest)])
        assert http["calls"] == [
            ("https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv", 60),
        ]
        assert dest.read_bytes() == EXPECTED_SEED

    def test_source_url_override(self, script, http, tmp_path):
        dest = tmp_path / "holidays.csv"
        script.main(["--dest", str(dest), "--source-url", "https://mirror.test/h.csv"])
        assert http["calls"] == [("https://mirror.test/h.csv", 60)]
        assert dest.exists()

    def test_default_dest_is_the_dbt_seed(self, script, http, tmp_path, monkeypatch):
        # The parser binds SEED_PATH at main() time; point it at a temp file so
        # the real seed is never touched.
        seed = tmp_path / "jpn_national_holidays.csv"
        monkeypatch.setattr(script, "SEED_PATH", seed)
        script.main([])
        assert seed.read_bytes() == EXPECTED_SEED

    def test_http_error_propagates_and_writes_nothing(self, script, http, tmp_path):
        http["response"] = FakeResponse(b"", status=503)
        dest = tmp_path / "holidays.csv"
        with pytest.raises(requests.HTTPError):
            script.main(["--dest", str(dest)])
        assert not dest.exists()

    def test_bad_source_writes_nothing(self, script, http, tmp_path):
        http["response"] = FakeResponse(b"<html>moved</html>")
        dest = tmp_path / "holidays.csv"
        with pytest.raises(ValueError, match="Unexpected source header"):
            script.main(["--dest", str(dest)])
        assert not dest.exists()
