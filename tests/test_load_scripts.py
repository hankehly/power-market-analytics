"""CLI wiring tests for every ``load`` subcommand of the six source scripts.

Each is exercised through ``main(argv)`` with the loader class swapped for a fake that
records its constructor arguments, so the tests pin the default contract / data path /
table literals and the CLI overrides without touching Spark.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from power_market_analytics.ingestion.loader import CsvTableSchema
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


#: (script stem, the argv before the load flags, loader attribute in the script namespace,
#:  default contract, default data path, default table)
LOAD_COMMANDS = [
    (
        "jepx",
        ["load", "spot"],
        "CsvLoader",
        "conf/schemas/jepx_spot.yaml",
        "data/jepx/spot",
        "pma_raw.jepx_spot",
    ),
    (
        "occto",
        ["load", "area_reserve_rate_dad"],
        "CsvLoader",
        "conf/schemas/occto_area_reserve_rate_dad.yaml",
        "data/occto/area_reserve_rate_dad",
        "pma_raw.occto_area_reserve_rate_dad",
    ),
    (
        "occto",
        ["load", "demand_forecast_dad"],
        "CsvLoader",
        "conf/schemas/occto_demand_forecast_dad.yaml",
        "data/occto/demand_forecast_dad",
        "pma_raw.occto_demand_forecast_dad",
    ),
    (
        "tepco",
        ["load", "area_demand_generation"],
        "TepcoAreaCsvLoader",
        "conf/schemas/tepco_area_demand_generation_actual.yaml",
        "data/tepco/area_demand_generation/csv",
        "pma_raw.tepco_area_demand_generation_actual",
    ),
    (
        "tepco",
        ["load", "power_usage"],
        "TepcoPowerUsageCsvLoader",
        "conf/schemas/tepco_power_usage_hourly.yaml",
        "data/tepco/power_usage/csv",
        "pma_raw.tepco_power_usage_hourly",
    ),
    (
        "kansai",
        ["load", "area_demand_generation"],
        "KansaiAreaCsvLoader",
        "conf/schemas/kansai_area_demand_generation_actual.yaml",
        "data/kansai/area_demand_generation/csv",
        "pma_raw.kansai_area_demand_generation_actual",
    ),
    (
        "kansai",
        ["load", "power_usage"],
        "KansaiPowerUsageCsvLoader",
        "conf/schemas/kansai_power_usage_hourly.yaml",
        "data/kansai/power_usage/csv",
        "pma_raw.kansai_power_usage_hourly",
    ),
    (
        "estat",
        ["load", "census_population_mesh"],
        "EstatCensusMeshCsvLoader",
        "conf/schemas/estat_census_population_mesh.yaml",
        "data/estat/census_population_mesh",
        "pma_raw.estat_census_population_mesh",
    ),
    (
        "jma",
        ["load", "hourly"],
        "JmaHourlyCsvLoader",
        "conf/schemas/jma_hourly_staffed.yaml",
        "data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv",
        "pma_raw.jma_hourly_staffed",
    ),
    (
        "jma",
        ["load", "normals"],
        "JmaNormalsCsvLoader",
        "conf/schemas/jma_normal_surface_daily.yaml",
        "data/jma/normals",
        "pma_raw.jma_normal_surface_daily",
    ),
    (
        "jma",
        ["load", "msm_surface_forecast"],
        "MsmForecastCsvLoader",
        "conf/schemas/jma_msm_surface_forecast.yaml",
        "data/jma/msm_surface_forecast/csv",
        "pma_raw.jma_msm_surface_forecast",
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
    "conf/schemas/kansai_area_demand_generation_actual.yaml": ["target_date", "time_code"],
    "conf/schemas/estat_census_population_mesh.yaml": ["census_year", "mesh_code"],
    "conf/schemas/tepco_power_usage_hourly.yaml": ["target_date", "hour_start"],
    "conf/schemas/kansai_power_usage_hourly.yaml": ["target_date", "hour_start"],
    "conf/schemas/jma_hourly_staffed.yaml": ["station_id", "observed_at"],
    "conf/schemas/jma_normal_surface_daily.yaml": [
        "normals_period_end_year",
        "station_number",
        "element_code",
        "month",
    ],
    "conf/schemas/jma_msm_surface_forecast.yaml": [
        "station_id",
        "forecast_reference_at_utc",
        "forecast_valid_at_utc",
    ],
}


@pytest.mark.parametrize(
    "stem, command, loader_attr, contract, data, table",
    LOAD_COMMANDS,
    ids=[" ".join([stem, *command]) for stem, command, *_ in LOAD_COMMANDS],
)
class TestLoadCommands:
    def test_defaults(self, monkeypatch, stem, command, loader_attr, contract, data, table):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)

        script.main([*command])

        assert len(RecordingLoader.built) == 1
        built = RecordingLoader.built[0]
        assert isinstance(built["schema"], CsvTableSchema)
        assert built["schema"].grain == CONTRACT_GRAINS[contract]
        assert built["filepath"] == REPO_ROOT / data
        assert built["table"] == table
        assert built["loaded"] is True

    def test_overrides(
        self, tmp_path, monkeypatch, stem, command, loader_attr, contract, data, table
    ):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)
        # A minimal contract file so --schema is proven to be honoured.
        schema_file = tmp_path / "alt.yaml"
        schema_file.write_text("grain: [k]\ncolumns:\n  - {name: k, type: int}\n", encoding="utf-8")

        script.main(
            [
                *command,
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
        self, tmp_path, monkeypatch, stem, command, loader_attr, contract, data, table
    ):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)
        with pytest.raises(FileNotFoundError):
            script.main([*command, "--schema", str(tmp_path / "nope.yaml")])
        assert RecordingLoader.built == []


def test_repo_root_constant_points_at_the_checkout():
    for stem in ("jepx", "occto", "tepco", "kansai", "estat", "jma"):
        assert import_script(stem).REPO_ROOT == REPO_ROOT
        assert isinstance(import_script(stem).REPO_ROOT, Path)
