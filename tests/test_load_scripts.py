"""CLI wiring tests for the ``scripts/load_*.py`` entry points.

Each script is exercised through ``main(argv)`` with the loader class swapped
for a fake that records its constructor arguments, so the tests pin the
default contract / data path / table literals and the CLI overrides without
touching Spark.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from power_market_analytics.csv_loader import CsvTableSchema
from tests.support import REPO_ROOT, import_script


class RecordingLoader:
    """Fake loader class: records constructor kwargs and ``load()`` calls."""

    built: list[dict] = []

    def __init__(self, schema, filepath, table):
        self.record = {"schema": schema, "filepath": filepath, "table": table, "loaded": False}
        type(self).built.append(self.record)

    def load(self) -> int:
        self.record["loaded"] = True
        return 7


@pytest.fixture(autouse=True)
def reset_recording_loader():
    RecordingLoader.built = []
    yield
    RecordingLoader.built = []


#: (script stem, loader attribute in the script namespace, default contract,
#:  default data path, default table)
GENERIC_SCRIPTS = [
    (
        "load_jepx_spot",
        "CsvLoader",
        "conf/schemas/jepx_spot.yaml",
        "data/jepx/spot",
        "pma_raw.jepx_spot",
    ),
    (
        "load_occto_area_reserve_rate",
        "CsvLoader",
        "conf/schemas/occto_area_reserve_rate_dad.yaml",
        "data/occto/area_reserve_rate_dad",
        "pma_raw.occto_area_reserve_rate_dad",
    ),
    (
        "load_occto_demand_forecast",
        "CsvLoader",
        "conf/schemas/occto_demand_forecast_dad.yaml",
        "data/occto/demand_forecast_dad",
        "pma_raw.occto_demand_forecast_dad",
    ),
    (
        "load_tepco_area_demand_generation",
        "TepcoAreaCsvLoader",
        "conf/schemas/tepco_area_demand_generation_actual.yaml",
        "data/tepco/area_demand_generation/csv",
        "pma_raw.tepco_area_demand_generation_actual",
    ),
    (
        "load_estat_census_population_mesh",
        "EstatCensusMeshCsvLoader",
        "conf/schemas/estat_census_population_mesh.yaml",
        "data/estat/census_population_mesh",
        "pma_raw.estat_census_population_mesh",
    ),
]

#: The grain of each default contract, proving the script read the right file.
CONTRACT_GRAINS = {
    "conf/schemas/jepx_spot.yaml": ["trade_date", "time_code"],
    "conf/schemas/occto_area_reserve_rate_dad.yaml": [
        "target_date",
        "period_end_time",
        "area_name_ja",
    ],
    "conf/schemas/occto_demand_forecast_dad.yaml": ["target_date", "area_name_ja"],
    "conf/schemas/tepco_area_demand_generation_actual.yaml": ["target_date", "time_code"],
    "conf/schemas/estat_census_population_mesh.yaml": ["census_year", "mesh_code"],
}


@pytest.mark.parametrize("stem, loader_attr, contract, data, table", GENERIC_SCRIPTS)
class TestSingleContractScripts:
    def test_defaults(self, monkeypatch, stem, loader_attr, contract, data, table):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)

        script.main([])

        assert len(RecordingLoader.built) == 1
        built = RecordingLoader.built[0]
        assert isinstance(built["schema"], CsvTableSchema)
        assert built["schema"].grain == CONTRACT_GRAINS[contract]
        assert built["filepath"] == REPO_ROOT / data
        assert built["table"] == table
        assert built["loaded"] is True

    def test_overrides(self, tmp_path, monkeypatch, stem, loader_attr, contract, data, table):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)
        # A minimal contract file so --schema is proven to be honoured.
        schema_file = tmp_path / "alt.yaml"
        schema_file.write_text("grain: [k]\ncolumns:\n  - {name: k, type: int}\n", encoding="utf-8")

        script.main(
            [
                "--schema",
                str(schema_file),
                "--data",
                str(tmp_path / "x.csv"),
                "--table",
                "db.t",
            ]
        )

        built = RecordingLoader.built[0]
        assert built["schema"].grain == ["k"]
        assert [c.name for c in built["schema"].columns] == ["k"]
        assert built["filepath"] == tmp_path / "x.csv"
        assert built["table"] == "db.t"
        assert built["loaded"] is True

    def test_missing_schema_file_fails_before_loading(
        self, tmp_path, monkeypatch, stem, loader_attr, contract, data, table
    ):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)
        with pytest.raises(FileNotFoundError):
            script.main(["--schema", str(tmp_path / "nope.yaml")])
        assert RecordingLoader.built == []


class TestLoadJmaHourly:
    def test_loads_both_layouts_with_defaults(self, monkeypatch):
        script = import_script("load_jma_hourly")
        monkeypatch.setattr(script, "JmaHourlyCsvLoader", RecordingLoader)

        script.main([])

        assert [(b["filepath"], b["table"], b["loaded"]) for b in RecordingLoader.built] == [
            (
                REPO_ROOT / "data/jma/hourly" / "a*_101-201-301-401_*.csv",
                "pma_raw.jma_hourly_amedas",
                True,
            ),
            (
                REPO_ROOT / "data/jma/hourly" / "s*_101-201-301-401_*.csv",
                "pma_raw.jma_hourly_staffed",
                True,
            ),
        ]
        amedas, staffed = (b["schema"] for b in RecordingLoader.built)
        assert isinstance(amedas, CsvTableSchema) and isinstance(staffed, CsvTableSchema)
        assert amedas.grain == ["station_id", "observed_at"]
        assert staffed.grain == ["station_id", "observed_at"]
        # The two contracts are distinct layouts (15 vs 17 physical columns).
        assert amedas.columns[-1].source == "_c14"
        assert staffed.columns[-1].source == "_c16"

    def test_data_dir_and_schema_dir_overrides(self, tmp_path, monkeypatch):
        script = import_script("load_jma_hourly")
        monkeypatch.setattr(script, "JmaHourlyCsvLoader", RecordingLoader)
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        for stem, grain in (("jma_hourly_amedas", "a"), ("jma_hourly_staffed", "s")):
            (schema_dir / f"{stem}.yaml").write_text(
                f"grain: [{grain}]\ncolumns:\n  - {{name: {grain}, type: string}}\n",
                encoding="utf-8",
            )
        data_dir = tmp_path / "hourly"

        script.main(["--data-dir", str(data_dir), "--schema-dir", str(schema_dir)])

        assert [b["schema"].grain for b in RecordingLoader.built] == [["a"], ["s"]]
        assert [b["filepath"] for b in RecordingLoader.built] == [
            data_dir / "a*_101-201-301-401_*.csv",
            data_dir / "s*_101-201-301-401_*.csv",
        ]

    def test_formats_table_is_the_source_of_truth(self):
        script = import_script("load_jma_hourly")
        assert script.FORMATS == [
            ("jma_hourly_amedas", "a*_101-201-301-401_*.csv", "pma_raw.jma_hourly_amedas"),
            ("jma_hourly_staffed", "s*_101-201-301-401_*.csv", "pma_raw.jma_hourly_staffed"),
        ]


def test_repo_root_constant_points_at_the_checkout():
    for stem in ("load_jepx_spot", "load_jma_hourly"):
        assert import_script(stem).REPO_ROOT == REPO_ROOT
        assert isinstance(import_script(stem).REPO_ROOT, Path)
