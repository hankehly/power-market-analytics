"""CLI wiring of the ``download`` subcommands of ``scripts/jepx.py``, ``occto.py``,
``tepco.py``, ``kansai.py`` (power usage; the area actuals are in ``test_kansai_scripts.py``)
and ``estat.py``, and the five parser trees.

Each handler builds one downloader and drives it; the downloader class is swapped for a
recording fake in the script's namespace, so what is asserted is the argument plumbing
(data dir, dataset key, force policy), not the HTTP work. The ``load`` subcommands are in
``test_load_scripts.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support import import_script


class TestDownloadJepxSpot:
    @pytest.fixture
    def script(self, monkeypatch):
        module = import_script("jepx")
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
        script.main(["download", "spot", "--data-dir", str(tmp_path)])
        assert fake["data_dir"] == tmp_path
        assert fake["downloads"] == [
            (2016, False),
            (2017, False),
            (2018, False),
            (2019, True),
            (2020, True),
        ]

    def test_force_all_forces_every_fiscal_year(self, script, fake, tmp_path):
        script.main(["download", "spot", "--data-dir", str(tmp_path), "--force-all"])
        assert fake["downloads"] == [
            (2016, True),
            (2017, True),
            (2018, True),
            (2019, True),
            (2020, True),
        ]

    def test_default_data_dir(self, script, fake):
        script.main(["download", "spot"])
        assert fake["data_dir"] == Path("data/jepx/spot")

    def test_earliest_year_comes_from_the_downloader(self, script, fake, monkeypatch):
        monkeypatch.setattr(script.JepxSpotDownloader, "EARLIEST_FISCAL_YEAR", 2019)
        script.main(["download", "spot"])
        assert fake["downloads"] == [(2019, True), (2020, True)]


class _OcctoScriptCase:
    """Shared assertions for the two OCCTO datasets (they differ only in dataset)."""

    dataset: str

    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("occto")
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
        module.main(["download", self.dataset, "--data-dir", str(tmp_path)])
        assert seen == {"data_dir": tmp_path, "dataset": self.dataset}

    def test_default_data_dir(self, fake):
        module, seen = fake
        module.main(["download", self.dataset])
        assert seen == {"data_dir": Path("data/occto"), "dataset": self.dataset}


class TestDownloadOcctoDemandForecast(_OcctoScriptCase):
    dataset = "demand_forecast_dad"


class TestDownloadOcctoAreaReserveRate(_OcctoScriptCase):
    dataset = "area_reserve_rate_dad"


class TestDownloadTepcoAreaDemandGeneration:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("tepco")
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
        module.main(["download", "area_demand_generation", "--data-dir", str(tmp_path)])
        assert seen == {"data_dir": tmp_path, "download_all": True}

    def test_default_data_dir(self, fake):
        module, seen = fake
        module.main(["download", "area_demand_generation"])
        assert seen == {"data_dir": Path("data/tepco/area_demand_generation"), "download_all": True}


class TestDownloadTepcoPowerUsage:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("tepco")
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir
                self.csv_dir = Path(data_dir) / "csv"

            def download_all(self, force_yearly=False):
                seen["force_yearly"] = force_yearly
                return [self.csv_dir / "juyo-2016.csv", self.csv_dir / "20220401_power_usage.csv"]

        monkeypatch.setattr(module, "TepcoPowerUsageDownloader", FakeDownloader)
        return module, seen

    def test_default_data_dir_and_cached_yearly_files(self, fake):
        module, seen = fake
        module.main(["download", "power_usage"])
        assert seen == {"data_dir": Path("data/tepco/power_usage"), "force_yearly": False}

    def test_data_dir_override_and_force_yearly(self, fake, tmp_path):
        module, seen = fake
        module.main(["download", "power_usage", "--data-dir", str(tmp_path), "--force-yearly"])
        assert seen == {"data_dir": tmp_path, "force_yearly": True}


class TestDownloadKansaiPowerUsage:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("kansai")
        seen: dict = {}

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir
                self.csv_dir = Path(data_dir) / "csv"

            def download_all(self):
                seen["download_all"] = True
                return [self.csv_dir / "20160401_juyo1_kansai.csv"]

        monkeypatch.setattr(module, "KansaiPowerUsageDownloader", FakeDownloader)
        return module, seen

    def test_default_data_dir(self, fake):
        module, seen = fake
        module.main(["download", "power_usage"])
        assert seen == {"data_dir": Path("data/kansai/power_usage"), "download_all": True}

    def test_data_dir_override(self, fake, tmp_path):
        module, seen = fake
        module.main(["download", "power_usage", "--data-dir", str(tmp_path)])
        assert seen == {"data_dir": tmp_path, "download_all": True}


class TestDownloadEstatCensusPopulationMesh:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("estat")
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
        module.main(["download", "census_population_mesh"])
        assert seen == {
            "data_dir": Path("data/estat/census_population_mesh"),
            "years": [2015, 2020],
            "force": False,
        }

    def test_years_data_dir_and_force_overrides(self, fake, tmp_path):
        module, seen = fake
        module.main(
            [
                "download",
                "census_population_mesh",
                "--years",
                "2020",
                "--data-dir",
                str(tmp_path),
                "--force",
            ]
        )
        assert seen == {"data_dir": tmp_path, "years": [2020], "force": True}

    def test_several_years(self, fake):
        module, seen = fake
        module.main(["download", "census_population_mesh", "--years", "2020", "2015"])
        assert seen["years"] == [2020, 2015]

    def test_unconfigured_year_is_rejected_by_the_parser(self, fake):
        module, seen = fake
        with pytest.raises(SystemExit):
            module.main(["download", "census_population_mesh", "--years", "2010"])
        assert seen == {}


# --------------------------------------------------------------------------- the parser trees


def choices_in(error: str) -> list[str]:
    """The names argparse lists in an invalid-choice message, in its order."""
    listed = re.search(r"\(choose from (.*)\)", error)
    assert listed is not None, error
    return [name.strip("'") for name in listed.group(1).split(", ")]


#: script → (its download datasets, its load datasets), in --help order.
TREES = {
    "jepx": (["spot"], ["spot"]),
    "occto": (
        ["demand_forecast_dad", "area_reserve_rate_dad"],
        ["demand_forecast_dad", "area_reserve_rate_dad"],
    ),
    "tepco": (["area_demand_generation", "power_usage"], ["area_demand_generation", "power_usage"]),
    "kansai": (
        ["area_demand_generation", "power_usage"],
        ["area_demand_generation", "power_usage"],
    ),
    "estat": (["census_population_mesh"], ["census_population_mesh"]),
}


@pytest.mark.parametrize("stem", sorted(TREES))
class TestParserTrees:
    def test_download_lists_exactly_the_datasets(self, stem, capsys):
        script = import_script(stem)

        with pytest.raises(SystemExit) as exc:
            script.main(["download", "nope"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == TREES[stem][0]

    def test_load_lists_exactly_the_datasets(self, stem, capsys):
        script = import_script(stem)

        with pytest.raises(SystemExit) as exc:
            script.main(["load", "nope"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == TREES[stem][1]

    @pytest.mark.parametrize("argv", [[], ["download"], ["load"]])
    def test_a_missing_verb_or_dataset_exits_2(self, stem, argv):
        script = import_script(stem)

        with pytest.raises(SystemExit) as exc:
            script.main(argv)

        assert exc.value.code == 2

    def test_help_at_every_level_exits_0(self, stem, capsys):
        script = import_script(stem)
        downloads, loads = TREES[stem]
        for argv in ([], ["download"], ["load"], ["download", downloads[0]], ["load", loads[0]]):
            with pytest.raises(SystemExit) as exc:
                script.main([*argv, "-h"])
            assert exc.value.code == 0, argv
            assert capsys.readouterr().out.startswith(f"usage: {stem}"), argv


def test_a_flag_of_another_dataset_is_refused(capsys):
    # --force-yearly belongs to tepco download power_usage alone: the area actuals have no
    # yearly files, so the flag is not shared across the source's datasets.
    script = import_script("tepco")

    with pytest.raises(SystemExit) as exc:
        script.main(["download", "area_demand_generation", "--force-yearly"])

    assert exc.value.code == 2
    assert "unrecognized arguments: --force-yearly" in capsys.readouterr().err
