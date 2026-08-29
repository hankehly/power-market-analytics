"""Tests for the MSM forecast raw load contract.

Files are written the way :meth:`MsmDownloader.extract_day` writes them
(``power_market_analytics.msm``): gzip CSV, header ``RAW_CSV_COLUMNS``,
floats as ``str(round(v, 6))``, timestamps as ``"...Z"`` strings, ``None`` as
an empty cell — loaded through the real
``conf/schemas/jma_msm_surface_forecast.yaml`` contract and
``MsmForecastCsvLoader``.
"""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.msm import RAW_CSV_COLUMNS, MsmForecastCsvLoader
from tests.support import REPO_ROOT

CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jma_msm_surface_forecast.yaml")

REFERENCE_AT = "2026-08-17T12:00:00Z"
VALID_AT = "2026-08-19T01:00:00Z"
SOURCE_FILE_NAME = "Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin"


def row(
    station_id: str = "s00001",
    station_latitude: str = "35.681",
    station_longitude: str = "139.767",
    grid_latitude: str = "35.7",
    grid_longitude: str = "139.75",
    grid_distance_km: str = "2.1",
    forecast_reference_at_utc: str = REFERENCE_AT,
    forecast_valid_at_utc: str = VALID_AT,
    forecast_lead_hours: str = "28",
    temperature_c: str = "25.5",
    relative_humidity_pct: str = "60.0",
    u_wind_ms: str = "3.0",
    v_wind_ms: str = "4.0",
    wind_speed_ms: str = "5.0",
    precipitation_mm: str = "0.0",
    surface_pressure_hpa: str = "1013.25",
    sea_level_pressure_hpa: str = "1018.0",
    shortwave_radiation_wm2: str = "120.0",
    solar_radiation_mjm2: str = "0.432",
    total_cloud_cover_pct: str = "80.0",
    high_cloud_cover_pct: str = "30.0",
    middle_cloud_cover_pct: str = "20.0",
    low_cloud_cover_pct: str = "10.0",
    source_file_name: str = SOURCE_FILE_NAME,
) -> list[str]:
    """One raw csv row (in RAW_CSV_COLUMNS order), keyword-overridable per cell."""
    values = dict(locals())
    return [values[c] for c in RAW_CSV_COLUMNS]


def write_csv_gz(path: Path, rows: list[list[str]], header: list[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header if header is not None else list(RAW_CSV_COLUMNS))
        writer.writerows(rows)
    return path


class TestContract:
    def test_grain(self):
        assert CONTRACT.grain == [
            "station_id",
            "forecast_reference_at_utc",
            "forecast_valid_at_utc",
        ]

    def test_destination_columns(self):
        assert [(c.name, c.type, c.nullable) for c in CONTRACT.columns] == [
            ("station_id", "string", False),
            ("station_latitude", "double", False),
            ("station_longitude", "double", False),
            ("grid_latitude", "double", False),
            ("grid_longitude", "double", False),
            ("grid_distance_km", "double", False),
            ("forecast_reference_at_utc", "string", False),
            ("forecast_valid_at_utc", "string", False),
            ("forecast_lead_hours", "int", False),
            ("temperature_c", "double", True),
            ("relative_humidity_pct", "double", True),
            ("u_wind_ms", "double", True),
            ("v_wind_ms", "double", True),
            ("wind_speed_ms", "double", True),
            ("precipitation_mm", "double", True),
            ("surface_pressure_hpa", "double", True),
            ("sea_level_pressure_hpa", "double", True),
            ("shortwave_radiation_wm2", "double", True),
            ("solar_radiation_mjm2", "double", True),
            ("total_cloud_cover_pct", "double", True),
            ("high_cloud_cover_pct", "double", True),
            ("middle_cloud_cover_pct", "double", True),
            ("low_cloud_cover_pct", "double", True),
            ("source_file_name", "string", False),
        ]

    def test_columns_match_raw_csv_columns_order(self):
        assert [c.name for c in CONTRACT.columns] == list(RAW_CSV_COLUMNS)

    def test_no_read_options_needed(self):
        assert CONTRACT.read_options == {}


class TestLoad:
    def test_types_and_values(self, spark, tmp_path):
        write_csv_gz(tmp_path / "msm_surface_20260819.csv.gz", [row()])
        loader = MsmForecastCsvLoader(CONTRACT, tmp_path, "test_msm_loader.basic", spark=spark)

        n = loader.load()

        assert n == 1
        table = spark.table("test_msm_loader.basic")
        schema = {f.name: f.dataType.simpleString() for f in table.schema}
        assert schema["forecast_lead_hours"] == "int"
        assert schema["temperature_c"] == "double"
        assert schema["station_latitude"] == "double"
        assert schema["forecast_reference_at_utc"] == "string"
        assert schema["forecast_valid_at_utc"] == "string"
        assert schema["source_file_name"] == "string"

        r = table.collect()[0]
        assert r["station_id"] == "s00001"
        assert r["forecast_lead_hours"] == 28
        assert r["temperature_c"] == pytest.approx(25.5)
        assert r["surface_pressure_hpa"] == pytest.approx(1013.25)
        assert r["forecast_reference_at_utc"] == REFERENCE_AT
        assert r["forecast_valid_at_utc"] == VALID_AT
        assert r["source_file_name"] == SOURCE_FILE_NAME

    def test_empty_cells_become_null_doubles(self, spark, tmp_path):
        write_csv_gz(
            tmp_path / "msm_surface_20260819.csv.gz",
            [row(temperature_c="", wind_speed_ms="", solar_radiation_mjm2="")],
        )
        loader = MsmForecastCsvLoader(CONTRACT, tmp_path, "test_msm_loader.nulls", spark=spark)

        loader.load()

        r = spark.table("test_msm_loader.nulls").collect()[0]
        assert r["temperature_c"] is None
        assert r["wind_speed_ms"] is None
        assert r["solar_radiation_mjm2"] is None
        # A non-blanked double on the same row still casts normally.
        assert r["surface_pressure_hpa"] == pytest.approx(1013.25)

    def test_duplicate_grain_across_two_files_fails(self, spark, tmp_path):
        write_csv_gz(tmp_path / "a.csv.gz", [row()])
        write_csv_gz(tmp_path / "b.csv.gz", [row()])
        loader = MsmForecastCsvLoader(CONTRACT, tmp_path, "test_msm_loader.dup", spark=spark)

        with pytest.raises(ValueError, match="not unique"):
            loader.load()

    def test_distinct_grain_across_two_files_succeeds(self, spark, tmp_path):
        write_csv_gz(tmp_path / "a.csv.gz", [row(station_id="s00001")])
        write_csv_gz(tmp_path / "b.csv.gz", [row(station_id="s00002")])
        loader = MsmForecastCsvLoader(CONTRACT, tmp_path, "test_msm_loader.two", spark=spark)

        n = loader.load()

        assert n == 2
        ids = {r["station_id"] for r in spark.table("test_msm_loader.two").collect()}
        assert ids == {"s00001", "s00002"}

    def test_missing_required_column_fails(self, spark, tmp_path):
        header = [c for c in RAW_CSV_COLUMNS if c != "station_latitude"]
        full_row = row()
        bad_row = [
            v for c, v in zip(RAW_CSV_COLUMNS, full_row, strict=True) if c != "station_latitude"
        ]
        write_csv_gz(tmp_path / "bad.csv.gz", [bad_row], header=header)
        loader = MsmForecastCsvLoader(CONTRACT, tmp_path, "test_msm_loader.bad", spark=spark)

        with pytest.raises(ValueError, match="missing required columns.*station_latitude"):
            loader.load()


class TestFileResolution:
    def test_directory_globs_csv_gz_only(self, spark, tmp_path):
        write_csv_gz(tmp_path / "msm_surface_20260817.csv.gz", [row(station_id="s00001")])
        write_csv_gz(tmp_path / "msm_surface_20260818.csv.gz", [row(station_id="s00002")])
        (tmp_path / "msm_surface_20260817.json").write_text("{}")

        loader = MsmForecastCsvLoader(CONTRACT, tmp_path, "test_msm_loader.glob", spark=spark)

        assert loader._resolve_files() == [
            str(tmp_path / "msm_surface_20260817.csv.gz"),
            str(tmp_path / "msm_surface_20260818.csv.gz"),
        ]
        assert loader.load() == 2
