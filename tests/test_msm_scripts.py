"""CLI wiring tests for the ``scripts/download_jma_msm_surface_forecast.py`` /
``scripts/load_jma_msm_surface_forecast.py`` entry points, plus
``power_market_analytics.msm.default_end_date``.

The downloader/loader classes are swapped for recording fakes in each
script's namespace, so what is asserted is the argument plumbing (station
seed paths, date range, data dir, force/keep-grib flags, schema/table
defaults) rather than any real HTTP/GRIB/Spark work.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from power_market_analytics import msm
from power_market_analytics.csv_loader import CsvTableSchema
from tests.support import REPO_ROOT, import_script


class TestDefaultEndDate:
    def test_is_jst_today_plus_one_day(self, monkeypatch):
        frozen = datetime.datetime(2026, 8, 21, 23, 59, tzinfo=msm.JST)
        monkeypatch.setattr(msm, "_now", lambda: frozen)

        assert msm.default_end_date() == datetime.date(2026, 8, 22)

    def test_uses_jst_not_the_naive_calendar_date(self, monkeypatch):
        # 2026-08-21 15:30 UTC == 2026-08-22 00:30 JST, so "today" is already
        # the 22nd in JST even though a naive UTC read would still say 21st.
        frozen = datetime.datetime(2026, 8, 22, 0, 30, tzinfo=msm.JST)
        monkeypatch.setattr(msm, "_now", lambda: frozen)

        assert msm.default_end_date() == datetime.date(2026, 8, 23)

    def test_real_now_returns_a_date_in_the_future(self):
        # No monkeypatch: exercises the real _now() seam. The reference date
        # is read once, strictly before default_end_date() makes its own
        # (possibly later) _now() call — so the result is always at least
        # one full day ahead of this reference, even if midnight JST falls
        # between the two reads; comparing against a *second*, later now()
        # read would be flaky right at that boundary.
        reference_date = datetime.datetime.now(msm.JST).date()

        assert msm.default_end_date() > reference_date


class TestDownloadJmaMsmSurfaceForecast:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("download_jma_msm_surface_forecast")
        seen: dict = {}
        stations = [
            msm.MsmStation(station_id="s47662", latitude=35.6, longitude=139.7),
            msm.MsmStation(station_id="s47772", latitude=34.6, longitude=135.5),
        ]

        def fake_load_stations(stations_csv, station_areas_csv):
            seen["stations_csv"] = stations_csv
            seen["station_areas_csv"] = station_areas_csv
            return stations

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir

            def download_range(self, start_date, end_date, stations, force=False, keep_grib=False):
                seen["start_date"] = start_date
                seen["end_date"] = end_date
                seen["stations"] = stations
                seen["force"] = force
                seen["keep_grib"] = keep_grib
                return [Path("data/jma/msm_surface_forecast/csv/msm_surface_20260821.csv.gz")]

        monkeypatch.setattr(module, "load_stations", fake_load_stations)
        monkeypatch.setattr(module, "MsmDownloader", FakeDownloader)
        monkeypatch.setattr(module, "default_end_date", lambda: datetime.date(2026, 8, 22))
        return module, seen, stations

    def test_defaults(self, fake):
        module, seen, stations = fake

        module.main([])

        assert seen["stations_csv"] == REPO_ROOT / "dbt/seeds/jma_stations.csv"
        assert seen["station_areas_csv"] == REPO_ROOT / "dbt/seeds/jma_station_areas.csv"
        assert seen["data_dir"] == Path("data/jma/msm_surface_forecast")
        assert seen["start_date"] == msm.DEFAULT_BACKFILL_START
        assert seen["end_date"] == datetime.date(2026, 8, 22)
        assert seen["stations"] == stations
        assert seen["force"] is False
        assert seen["keep_grib"] is False

    def test_start_end_data_dir_overrides(self, fake, tmp_path):
        module, seen, _stations = fake

        module.main(
            [
                "--start-date",
                "2026-08-01",
                "--end-date",
                "2026-08-03",
                "--data-dir",
                str(tmp_path),
            ]
        )

        assert seen["start_date"] == datetime.date(2026, 8, 1)
        assert seen["end_date"] == datetime.date(2026, 8, 3)
        assert seen["data_dir"] == tmp_path
        assert seen["force"] is False
        assert seen["keep_grib"] is False

    def test_force_and_keep_grib_flags_forwarded(self, fake):
        module, seen, _stations = fake

        module.main(["--force", "--keep-grib"])

        assert seen["force"] is True
        assert seen["keep_grib"] is True

    def test_module_repo_root_matches_the_real_repo_root(self, fake):
        module, _seen, _stations = fake
        assert module.REPO_ROOT == REPO_ROOT


class TestLoadJmaMsmSurfaceForecast:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("load_jma_msm_surface_forecast")
        seen: dict = {}

        class FakeLoader:
            def __init__(self, schema, filepath, table):
                seen["schema"] = schema
                seen["filepath"] = filepath
                seen["table"] = table

            def load(self) -> int:
                seen["loaded"] = True
                return 42

        monkeypatch.setattr(module, "MsmForecastCsvLoader", FakeLoader)
        return module, seen

    def test_defaults(self, fake):
        module, seen = fake

        module.main([])

        assert isinstance(seen["schema"], CsvTableSchema)
        assert seen["schema"].grain == [
            "station_id",
            "forecast_reference_at_utc",
            "forecast_valid_at_utc",
        ]
        assert seen["filepath"] == REPO_ROOT / "data/jma/msm_surface_forecast/csv"
        assert seen["table"] == "pma_raw.jma_msm_surface_forecast"
        assert seen["loaded"] is True

    def test_schema_data_table_overrides(self, fake, tmp_path):
        module, seen = fake
        schema_file = tmp_path / "alt.yaml"
        schema_file.write_text("grain: [k]\ncolumns:\n  - {name: k, type: int}\n", encoding="utf-8")

        module.main(
            [
                "--schema",
                str(schema_file),
                "--data",
                str(tmp_path / "x.csv"),
                "--table",
                "db.t",
            ]
        )

        assert seen["schema"].grain == ["k"]
        assert [c.name for c in seen["schema"].columns] == ["k"]
        assert seen["filepath"] == tmp_path / "x.csv"
        assert seen["table"] == "db.t"
        assert seen["loaded"] is True

    def test_missing_schema_file_fails_before_loading(self, fake, tmp_path):
        module, seen = fake

        with pytest.raises(FileNotFoundError):
            module.main(["--schema", str(tmp_path / "nope.yaml")])

        assert seen == {}

    def test_module_repo_root_matches_the_real_repo_root(self, fake):
        module, _seen = fake
        assert module.REPO_ROOT == REPO_ROOT
