"""CLI wiring of ``scripts/kansai.py download area_demand_generation``, and the Kansai
area-actuals contract. The ``load`` subcommands are in ``test_load_scripts.py`` with every
other load; ``download power_usage`` is in ``test_download_scripts.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from power_market_analytics.ingestion.loader import CsvTableSchema
from tests.support import REPO_ROOT, import_script


class TestDownloadScript:
    def test_downloads_everything_into_the_given_dir(self, tmp_path, monkeypatch):
        script = import_script("kansai")
        built: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                built["data_dir"] = data_dir
                self.csv_dir = Path(data_dir) / "csv"

            def download_all(self):
                built["download_all"] = True
                return [self.csv_dir / "a.csv", self.csv_dir / "b.csv"]

        monkeypatch.setattr(script, "KansaiAreaDownloader", FakeDownloader)
        script.main(["download", "area_demand_generation", "--data-dir", str(tmp_path)])
        assert built == {"data_dir": tmp_path, "download_all": True}

    def test_default_data_dir(self, monkeypatch):
        script = import_script("kansai")
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir
                self.csv_dir = Path(data_dir) / "csv"

            def download_all(self):
                return []

        monkeypatch.setattr(script, "KansaiAreaDownloader", FakeDownloader)
        script.main(["download", "area_demand_generation"])
        assert seen["data_dir"] == Path("data/kansai/area_demand_generation")


class TestKansaiContract:
    @pytest.fixture
    def contract(self) -> CsvTableSchema:
        return CsvTableSchema.from_yaml(
            REPO_ROOT / "conf/schemas/kansai_area_demand_generation_actual.yaml"
        )

    def test_positional_sources_and_grain(self, contract):
        assert [c.source for c in contract.columns] == [
            "_c0",
            "_c1",
            "_c2",
            "_c3",
            "_c4",
            "_c5",
            "_c6",
            "__file_updated_at",
        ]
        assert contract.grain == ["target_date", "time_code"]
        assert contract.read_options == {"encoding": "windows-31j"}

    def test_types_and_formats(self, contract):
        by_name = {c.name: c for c in contract.columns}
        assert (by_name["target_date"].type, by_name["target_date"].format) == ("date", "yyyyMMdd")
        assert by_name["time_code"].type == "int"
        # Kansai publishes full-precision integer kWh with no scientific notation,
        # so the measures are bigint (TEPCO's need double).
        for m in ("demand_kwh", "generation_kwh", "wind_solar_generation_kwh"):
            assert by_name[m].type == "bigint"
        assert (by_name["file_updated_at"].type, by_name["file_updated_at"].format) == (
            "timestamp",
            "yyyyMMdd HH:mm:ss",
        )
        # Kansai leaves cells blank for periods it could not observe (e.g. 22
        # periods on 2025-10-12), so only the keys are non-nullable.
        assert {c.name for c in contract.columns if not c.nullable} == {
            "target_date",
            "time_code",
            "period_start_time",
            "period_end_time",
            "file_updated_at",
        }
