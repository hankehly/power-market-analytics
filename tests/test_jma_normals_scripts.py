"""CLI wiring of scripts/download_jma_normals.py.

The downloader class is swapped for a recording fake in the script's namespace,
so what is asserted is the argument plumbing, not the HTTP work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import import_script


def make_fake(record: dict):
    class FakeDownloader:
        def __init__(self, data_dir, timeout=60.0):
            record["data_dir"] = data_dir
            record["timeout"] = timeout

        def download_all(self, years=None):
            record["years"] = years
            return [Path(record["data_dir"]) / "2020/csv/daily/nml_sfc_d_47662.csv"]

    return FakeDownloader


class TestDownloadJmaNormals:
    def test_defaults(self, monkeypatch):
        script = import_script("download_jma_normals")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_fake(record))

        script.main([])

        assert record == {"data_dir": Path("data/jma/normals"), "timeout": 60.0, "years": [2020]}

    def test_overrides(self, tmp_path, monkeypatch):
        script = import_script("download_jma_normals")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_fake(record))

        script.main(["--data-dir", str(tmp_path), "--timeout", "5", "--years", "2020"])

        assert record == {"data_dir": tmp_path, "timeout": 5.0, "years": [2020]}

    def test_an_unconfigured_year_is_rejected_by_the_parser(self, monkeypatch):
        script = import_script("download_jma_normals")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_fake(record))

        with pytest.raises(SystemExit) as exc:
            script.main(["--years", "2030"])

        assert exc.value.code == 2
        assert record == {}
