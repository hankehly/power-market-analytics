"""CLI entry points of the JEPX / OCCTO / TEPCO / e-Stat download scripts (scripts/).

Each script builds one downloader and drives it; the downloader class is swapped
for a recording fake in the script's namespace, so what is asserted is the
argument plumbing (data dir, dataset key, force policy), not the HTTP work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import import_script


class TestDownloadJepxSpot:
    @pytest.fixture
    def script(self, monkeypatch):
        module = import_script("download_jepx_spot")
        # Pin "now" to FY2020 so the year list and the force policy are exact.
        monkeypatch.setattr(module, "current_fiscal_year", lambda today=None: 2020)
        return module

    @pytest.fixture
    def fake(self, script, monkeypatch):
        seen: dict = {"downloads": []}

        class FakeDownloader:
            EARLIEST_FISCAL_YEAR = 2016

            def __init__(self, data_dir):
                seen["data_dir"] = data_dir

            def download(self, fiscal_year, force=False):
                seen["downloads"].append((fiscal_year, force))
                return Path(seen["data_dir"]) / f"spot_{fiscal_year}.csv"

        monkeypatch.setattr(script, "JepxSpotDownloader", FakeDownloader)
        return seen

    def test_forces_only_the_two_most_recent_fiscal_years(self, script, fake, tmp_path):
        script.main(["--data-dir", str(tmp_path)])
        assert fake["data_dir"] == tmp_path
        assert fake["downloads"] == [
            (2016, False),
            (2017, False),
            (2018, False),
            (2019, True),
            (2020, True),
        ]

    def test_force_all_forces_every_fiscal_year(self, script, fake, tmp_path):
        script.main(["--data-dir", str(tmp_path), "--force-all"])
        assert fake["downloads"] == [
            (2016, True),
            (2017, True),
            (2018, True),
            (2019, True),
            (2020, True),
        ]

    def test_default_data_dir(self, script, fake):
        script.main([])
        assert fake["data_dir"] == Path("data/jepx/spot")

    def test_earliest_year_comes_from_the_downloader(self, script, fake, monkeypatch):
        monkeypatch.setattr(script.JepxSpotDownloader, "EARLIEST_FISCAL_YEAR", 2019)
        script.main([])
        assert fake["downloads"] == [(2019, True), (2020, True)]


class _OcctoScriptCase:
    """Shared assertions for the two OCCTO scripts (they differ only in dataset)."""

    script_name: str
    dataset: str

    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script(self.script_name)
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir

            def download(self, dataset):
                seen["dataset"] = dataset
                return Path(seen["data_dir"]) / dataset / f"{dataset}.csv"

        monkeypatch.setattr(module, "OcctoBulkDownloader", FakeDownloader)
        return module, seen

    def test_downloads_the_dataset_into_the_given_dir(self, fake, tmp_path):
        module, seen = fake
        module.main(["--data-dir", str(tmp_path)])
        assert seen == {"data_dir": tmp_path, "dataset": self.dataset}

    def test_default_data_dir(self, fake):
        module, seen = fake
        module.main([])
        assert seen == {"data_dir": Path("data/occto"), "dataset": self.dataset}


class TestDownloadOcctoDemandForecast(_OcctoScriptCase):
    script_name = "download_occto_demand_forecast"
    dataset = "demand_forecast_dad"


class TestDownloadOcctoAreaReserveRate(_OcctoScriptCase):
    script_name = "download_occto_area_reserve_rate"
    dataset = "area_reserve_rate_dad"


class TestDownloadTepcoAreaDemandGeneration:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("download_tepco_area_demand_generation")
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir
                self.csv_dir = Path(data_dir) / "csv"

            def download_all(self):
                seen["download_all"] = True
                return [self.csv_dir / "AREA_JISEKI_20250701.csv"]

        monkeypatch.setattr(module, "TepcoAreaDownloader", FakeDownloader)
        return module, seen

    def test_downloads_everything_into_the_given_dir(self, fake, tmp_path):
        module, seen = fake
        module.main(["--data-dir", str(tmp_path)])
        assert seen == {"data_dir": tmp_path, "download_all": True}

    def test_default_data_dir(self, fake):
        module, seen = fake
        module.main([])
        assert seen == {"data_dir": Path("data/tepco/area_demand_generation"), "download_all": True}


class TestDownloadEstatCensusPopulationMesh:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("download_estat_census_population_mesh")
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir

            def download_all(self, years=None, force=False):
                seen["years"] = years
                seen["force"] = force
                return [Path(seen["data_dir"]) / "2015/txt/tblT000847H5339.txt"]

        monkeypatch.setattr(module, "EstatCensusMeshDownloader", FakeDownloader)
        return module, seen

    def test_defaults_to_every_configured_vintage_without_force(self, fake):
        module, seen = fake
        module.main([])
        assert seen == {
            "data_dir": Path("data/estat/census_population_mesh"),
            "years": [2015, 2020],
            "force": False,
        }

    def test_years_data_dir_and_force_overrides(self, fake, tmp_path):
        module, seen = fake
        module.main(["--years", "2020", "--data-dir", str(tmp_path), "--force"])
        assert seen == {"data_dir": tmp_path, "years": [2020], "force": True}

    def test_several_years(self, fake):
        module, seen = fake
        module.main(["--years", "2020", "2015"])
        assert seen["years"] == [2020, 2015]

    def test_unconfigured_year_is_rejected_by_the_parser(self, fake):
        module, seen = fake
        with pytest.raises(SystemExit):
            module.main(["--years", "2010"])
        assert seen == {}
