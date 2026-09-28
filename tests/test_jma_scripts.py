"""CLI wiring of ``scripts/jma.py``: the ``download`` subcommands (hourly, normals,
msm_surface_forecast, stations), the parser tree, and the one import that must stay lazy.

The downloader classes are swapped for recording fakes in the script's namespace, so what
is asserted is the argument plumbing, not the HTTP work. The ``load`` subcommands are in
``test_load_scripts.py`` with every other load.
"""

from __future__ import annotations

import csv
import datetime
import importlib
import os
import re
import sys
import time
from collections.abc import Collection
from pathlib import Path

import pytest
from loguru import logger

from power_market_analytics.ingestion.jma.hourly import SCRAPE_ELEMENTS, JmaHourlyDownloader
from power_market_analytics.ingestion.jma.stations import JmaStationMasterDownloader
from power_market_analytics.ingestion.msm import download as msm_download
from power_market_analytics.ingestion.msm import stations as msm_stations
from power_market_analytics.ingestion.msm import vintage as msm_vintage
from tests.support import REPO_ROOT, import_script

TODAY = datetime.date.today()


def write_stations(path: Path, rows: list[dict]) -> Path:
    """Write a station master CSV with the real seed header.

    ``rows`` only need ``station_id``, ``prefecture_code`` and (optionally)
    ``observation_ended_on``; the other columns are left blank.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=JmaStationMasterDownloader.FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({"observation_ended_on": "", **row})
    return path


def scrape_path(data_dir: Path, station_id: str, year: int) -> Path:
    """Where the orchestrator expects a stitched file (hand-derived name)."""
    return data_dir / f"{station_id}_101-201-301-401-501-605-610_{year}.csv"


# --------------------------------------------------------------------------- download hourly


def make_hourly_fake(
    record: dict, failing: Collection[str] = frozenset(), write_root: Path | None = None
):
    """A ``JmaHourlyDownloader`` whose ``download`` records calls instead of fetching.

    Everything else (``EARLIEST_YEAR``, ``path_for``, the constructor) is the
    real class, so file names and defaults are the production ones. A stub
    file is written at ``path_for(...)`` only when ``write_root`` is given and
    the destination lies inside it — the fake must never touch the real
    ``data/jma/hourly`` when a test exercises the script's default arguments.
    """
    record.setdefault("calls", [])

    class FakeHourly(JmaHourlyDownloader):
        def __init__(self, data_dir, request_interval=5.0):
            super().__init__(data_dir=data_dir, request_interval=request_interval)
            record["data_dir"] = data_dir
            record["request_interval"] = request_interval

        def download(self, station_id, elements, year, force=False):
            record["calls"].append((station_id, list(elements), year, force))
            if station_id in failing:
                raise RuntimeError(f"503 for {station_id}")
            dest = self.path_for(station_id, elements, year)
            if write_root is not None and dest.resolve().is_relative_to(write_root.resolve()):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"csv")
            return dest

    return FakeHourly


class TestBuildPlan:
    def test_station_major_years(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "a0368", "prefecture_code": "44"},
            ],
        )

        plan = script.build_plan(stations, 2016, 2018, None)

        assert plan == [
            ("s47662", 2016),
            ("s47662", 2017),
            ("s47662", 2018),
            ("a0368", 2016),
            ("a0368", 2017),
            ("a0368", 2018),
        ]

    def test_station_ended_before_the_window_is_skipped(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "a1207",
                    "prefecture_code": "11",
                    "observation_ended_on": "2015-12-31",
                },
                {"station_id": "a0002", "prefecture_code": "11"},
            ],
        )
        assert script.build_plan(stations, 2016, 2016, None) == [("a0002", 2016)]

    def test_discontinued_station_stops_at_its_end_year(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "a0370",
                    "prefecture_code": "44",
                    "observation_ended_on": "2017-03-31",
                }
            ],
        )
        assert script.build_plan(stations, 2016, 2019, None) == [("a0370", 2016), ("a0370", 2017)]

    def test_station_ended_during_the_start_year_still_gets_that_year(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "a0370",
                    "prefecture_code": "44",
                    "observation_ended_on": "2016-01-05",
                }
            ],
        )
        assert script.build_plan(stations, 2016, 2019, None) == [("a0370", 2016)]

    def test_end_date_after_the_window_does_not_extend_it(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "a0370",
                    "prefecture_code": "44",
                    "observation_ended_on": "2030-01-01",
                }
            ],
        )
        assert script.build_plan(stations, 2017, 2018, None) == [("a0370", 2017), ("a0370", 2018)]

    def test_prefecture_filter_keeps_only_those_codes(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "a0999", "prefecture_code": "45"},
                {"station_id": "a1207", "prefecture_code": "11"},
            ],
        )
        assert script.build_plan(stations, 2016, 2016, None, prefectures=[44]) == [("s47662", 2016)]
        assert script.build_plan(stations, 2016, 2016, None, prefectures=[11, 45]) == [
            ("a0999", 2016),
            ("a1207", 2016),
        ]

    def test_unmatched_prefecture_filter_raises(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv", [{"station_id": "s47662", "prefecture_code": "44"}]
        )
        with pytest.raises(ValueError, match=r"No stations in prefecture codes \[99\]"):
            script.build_plan(stations, 2016, 2016, None, prefectures=[99])

    def test_limit_truncates_after_the_prefecture_filter(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "a0001", "prefecture_code": "44"},
                {"station_id": "b0001", "prefecture_code": "45"},
                {"station_id": "a0002", "prefecture_code": "44"},
                {"station_id": "a0003", "prefecture_code": "44"},
            ],
        )
        assert script.build_plan(stations, 2016, 2016, 2, prefectures=[44]) == [
            ("a0001", 2016),
            ("a0002", 2016),
        ]
        assert script.build_plan(stations, 2016, 2016, 2) == [("a0001", 2016), ("b0001", 2016)]

    def test_limit_counts_skipped_stations(self, tmp_path):
        # limit is applied to the master rows before end-date filtering, so a
        # discontinued station inside the limit still consumes a slot.
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "a1207",
                    "prefecture_code": "11",
                    "observation_ended_on": "2003-10-16",
                },
                {"station_id": "a0002", "prefecture_code": "11"},
            ],
        )
        assert script.build_plan(stations, 2016, 2016, 1) == []

    def test_station_filter_keeps_only_those_ids(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "s47772", "prefecture_code": "62"},
                {"station_id": "s47401", "prefecture_code": "11"},
            ],
        )
        assert script.build_plan(stations, 2016, 2016, None, stations=["s47772"]) == [
            ("s47772", 2016)
        ]
        assert script.build_plan(stations, 2016, 2016, None, stations=["s47401", "s47662"]) == [
            ("s47662", 2016),
            ("s47401", 2016),
        ]

    def test_unmatched_station_filter_raises(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv", [{"station_id": "s47662", "prefecture_code": "44"}]
        )
        with pytest.raises(ValueError, match=r"No stations with ids \['nope'\]"):
            script.build_plan(stations, 2016, 2016, None, stations=["nope"])

    def test_station_filter_runs_after_the_prefecture_filter(self, tmp_path):
        # 大阪 (s47772, pd 62) is not among the 東京 stations, so asking for both is empty
        # and stops here, rather than planning nothing and reporting 0/0 done.
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "s47772", "prefecture_code": "62"},
            ],
        )
        with pytest.raises(ValueError, match=r"No stations with ids \['s47772'\]"):
            script.build_plan(stations, 2016, 2016, None, prefectures=[44], stations=["s47772"])

    def test_a_discontinued_station_makes_an_empty_plan(self, tmp_path):
        # A valid id whose observations ended before the window is not a typo: no error,
        # an empty plan, and the "ended before" line in the log.
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "s47401",
                    "prefecture_code": "11",
                    "observation_ended_on": "2003-10-16",
                }
            ],
        )
        assert script.build_plan(stations, 2016, 2016, None, stations=["s47401"]) == []


def make_station_master_fake(record: dict, rows: list[dict] | None = None):
    """A ``JmaStationMasterDownloader`` stand-in that writes ``rows`` to ``dest`` if absent."""

    class FakeStationMaster:
        def __init__(self, dest, staffed_only=False, jepx_areas_only=False):
            record["dest"] = Path(dest)
            record["staffed_only"] = staffed_only
            record["jepx_areas_only"] = jepx_areas_only

        def download(self, force=False):
            record["download"] = {"force": force}
            if rows is not None and not record["dest"].exists():
                write_stations(record["dest"], rows)
            return record["dest"]

    return FakeStationMaster


def capture_logs(level: str = "INFO") -> tuple[list[str], int]:
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(m.record["message"]), level=level)
    return messages, sink


class TestDownloadHourly:
    TWO_STATIONS = [
        {"station_id": "s47662", "prefecture_code": "44"},
        {"station_id": "a0368", "prefecture_code": "44"},
    ]

    def test_dry_run_plans_but_downloads_nothing(self, tmp_path, monkeypatch):
        script = import_script("jma")
        master: dict = {}
        hourly: dict = {}
        monkeypatch.setattr(
            script,
            "JmaStationMasterDownloader",
            make_station_master_fake(master, self.TWO_STATIONS),
        )
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )
        stations_csv = tmp_path / "seed" / "jma_stations.csv"  # absent → fake writes it
        data_dir = tmp_path / "hourly"
        scrape_path(data_dir, "s47662", 2016).parent.mkdir()
        scrape_path(data_dir, "s47662", 2016).write_bytes(b"old")
        messages, sink = capture_logs()
        try:
            result = script.main(
                [
                    "download",
                    "hourly",
                    "--stations-csv",
                    str(stations_csv),
                    "--data-dir",
                    str(data_dir),
                    "--start-year",
                    "2016",
                    "--end-year",
                    "2017",
                    "--dry-run",
                ]
            )
        finally:
            logger.remove(sink)

        assert result is None
        assert master == {
            "dest": stations_csv,
            "staffed_only": True,
            "jepx_areas_only": True,
            "download": {"force": False},
        }
        assert stations_csv.exists()
        assert hourly["calls"] == []
        assert "Dry run: would download 3 of 4 station-years" in messages

    def test_full_run_downloads_every_station_year(self, tmp_path, monkeypatch):
        script = import_script("jma")
        master: dict = {}
        hourly: dict = {}
        stations_csv = write_stations(tmp_path / "stations.csv", self.TWO_STATIONS)
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake(master))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )
        data_dir = tmp_path / "hourly"

        script.main(
            [
                "download",
                "hourly",
                "--stations-csv",
                str(stations_csv),
                "--data-dir",
                str(data_dir),
                "--start-year",
                "2016",
                "--end-year",
                "2017",
                "--request-interval",
                "0.5",
            ]
        )

        assert master == {
            "dest": stations_csv,
            "staffed_only": True,
            "jepx_areas_only": True,
            "download": {"force": False},
        }
        assert master["staffed_only"] is True
        assert hourly["data_dir"] == data_dir
        assert hourly["request_interval"] == 0.5
        assert hourly["calls"] == [
            ("s47662", SCRAPE_ELEMENTS, 2016, False),
            ("s47662", SCRAPE_ELEMENTS, 2017, False),
            ("a0368", SCRAPE_ELEMENTS, 2016, False),
            ("a0368", SCRAPE_ELEMENTS, 2017, False),
        ]
        assert sorted(p.name for p in data_dir.iterdir()) == [
            "a0368_101-201-301-401-501-605-610_2016.csv",
            "a0368_101-201-301-401-501-605-610_2017.csv",
            "s47662_101-201-301-401-501-605-610_2016.csv",
            "s47662_101-201-301-401-501-605-610_2017.csv",
        ]

    def test_prefecture_and_limit_flow_into_the_plan(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "a1207", "prefecture_code": "11"},
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "a0368", "prefecture_code": "44"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        script.main(
            [
                "download",
                "hourly",
                "--stations-csv",
                str(stations_csv),
                "--data-dir",
                str(tmp_path / "hourly"),
                "--start-year",
                "2016",
                "--end-year",
                "2016",
                "--prefecture",
                "44",
                "--limit",
                "1",
            ]
        )

        assert hourly["calls"] == [("s47662", SCRAPE_ELEMENTS, 2016, False)]

    def test_unmatched_prefecture_aborts_before_downloading(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(tmp_path / "stations.csv", self.TWO_STATIONS)
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        with pytest.raises(ValueError, match="No stations in prefecture codes"):
            script.main(
                ["download", "hourly", "--stations-csv", str(stations_csv), "--prefecture", "99"]
            )

        assert hourly.get("calls", []) == []

    def test_failing_station_is_skipped_and_reported_with_exit_1(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "bad", "prefecture_code": "44"},
                {"station_id": "a0368", "prefecture_code": "44"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script,
            "JmaHourlyDownloader",
            make_hourly_fake(hourly, failing={"bad"}, write_root=tmp_path),
        )
        data_dir = tmp_path / "hourly"
        messages, sink = capture_logs("ERROR")
        try:
            with pytest.raises(SystemExit) as exc:
                script.main(
                    [
                        "download",
                        "hourly",
                        "--stations-csv",
                        str(stations_csv),
                        "--data-dir",
                        str(data_dir),
                        "--start-year",
                        "2016",
                        "--end-year",
                        "2017",
                    ]
                )
        finally:
            logger.remove(sink)

        assert exc.value.code == 1
        # Every station-year was attempted: two failures in a row is well
        # under the circuit breaker.
        assert [(c[0], c[2]) for c in hourly["calls"]] == [
            ("s47662", 2016),
            ("s47662", 2017),
            ("bad", 2016),
            ("bad", 2017),
            ("a0368", 2016),
            ("a0368", 2017),
        ]
        assert sorted(p.name for p in data_dir.iterdir()) == [
            "a0368_101-201-301-401-501-605-610_2016.csv",
            "a0368_101-201-301-401-501-605-610_2017.csv",
            "s47662_101-201-301-401-501-605-610_2016.csv",
            "s47662_101-201-301-401-501-605-610_2017.csv",
        ]
        assert messages == [
            "FAILED bad 2016: 503 for bad",
            "FAILED bad 2017: 503 for bad",
            "2 failures (re-run to retry):",
            "  bad 2016: 503 for bad",
            "  bad 2017: 503 for bad",
        ]

    def test_ten_consecutive_failures_abort_the_run(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "bad1", "prefecture_code": "44"},
                {"station_id": "bad2", "prefecture_code": "44"},
                {"station_id": "s47662", "prefecture_code": "44"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script,
            "JmaHourlyDownloader",
            make_hourly_fake(hourly, failing={"bad1", "bad2"}, write_root=tmp_path),
        )
        data_dir = tmp_path / "hourly"

        with pytest.raises(SystemExit) as exc:
            # 3 stations x 6 years = 18 planned; the breaker trips at the 10th.
            script.main(
                [
                    "download",
                    "hourly",
                    "--stations-csv",
                    str(stations_csv),
                    "--data-dir",
                    str(data_dir),
                    "--start-year",
                    "2016",
                    "--end-year",
                    "2021",
                ]
            )

        assert exc.value.code == 1
        assert len(hourly["calls"]) == 10
        assert [c[0] for c in hourly["calls"]] == ["bad1"] * 6 + ["bad2"] * 4
        assert not data_dir.exists()  # the good station was never reached

    def test_a_success_resets_the_consecutive_failure_count(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        # 6 failures, 6 successes, 6 failures: 12 failures in total but never
        # 10 in a row — a counter that did not reset on success would trip
        # the breaker on bad2's 4th year (16 calls instead of 18).
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "bad1", "prefecture_code": "44"},
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "bad2", "prefecture_code": "44"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script,
            "JmaHourlyDownloader",
            make_hourly_fake(hourly, failing={"bad1", "bad2"}, write_root=tmp_path),
        )

        with pytest.raises(SystemExit) as exc:
            script.main(
                [
                    "download",
                    "hourly",
                    "--stations-csv",
                    str(stations_csv),
                    "--data-dir",
                    str(tmp_path / "hourly"),
                    "--start-year",
                    "2016",
                    "--end-year",
                    "2021",
                ]
            )

        assert exc.value.code == 1
        assert [c[0] for c in hourly["calls"]] == ["bad1"] * 6 + ["s47662"] * 6 + ["bad2"] * 6

    def test_current_year_file_is_forced_only_when_it_predates_today(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "stale", "prefecture_code": "44"},
                {"station_id": "fresh", "prefecture_code": "44"},
                {"station_id": "new", "prefecture_code": "44"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )
        data_dir = tmp_path / "hourly"
        data_dir.mkdir()
        # A last-year file and a this-year file written two days ago …
        for year in (TODAY.year - 1, TODAY.year):
            path = scrape_path(data_dir, "stale", year)
            path.write_bytes(b"old")
            two_days_ago = time.time() - 2 * 86400
            os.utime(path, (two_days_ago, two_days_ago))
        # … and a this-year file written just now.
        scrape_path(data_dir, "fresh", TODAY.year).write_bytes(b"today")

        script.main(
            [
                "download",
                "hourly",
                "--stations-csv",
                str(stations_csv),
                "--data-dir",
                str(data_dir),
                "--start-year",
                str(TODAY.year - 1),
                "--end-year",
                str(TODAY.year),
            ]
        )

        assert [(c[0], c[2], c[3]) for c in hourly["calls"]] == [
            ("stale", TODAY.year - 1, False),  # past year: cache, even if old
            ("stale", TODAY.year, True),  # current year, predates today
            ("fresh", TODAY.year - 1, False),
            ("fresh", TODAY.year, False),  # current year, written today
            ("new", TODAY.year - 1, False),
            ("new", TODAY.year, False),  # no file yet → plain download
        ]

    def test_progress_is_logged_every_100_station_years(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [{"station_id": f"a{i:04d}", "prefecture_code": "44"} for i in range(10)],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )
        messages, sink = capture_logs()
        try:
            script.main(
                [
                    "download",
                    "hourly",
                    "--stations-csv",
                    str(stations_csv),
                    "--data-dir",
                    str(tmp_path / "hourly"),
                    "--start-year",
                    "2016",
                    "--end-year",
                    "2025",  # 10 stations x 10 years
                ]
            )
        finally:
            logger.remove(sink)

        assert len(hourly["calls"]) == 100
        assert [m for m in messages if m.startswith("Progress")] == [
            "Progress: 100/100 station-years"
        ]
        assert "Done: 100/100 station-years ok" in messages

    def test_station_filter_flows_into_the_plan(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47401", "prefecture_code": "11"},
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "s47772", "prefecture_code": "62"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        script.main(
            [
                "download",
                "hourly",
                "--stations-csv",
                str(stations_csv),
                "--data-dir",
                str(tmp_path / "hourly"),
                "--start-year",
                "2016",
                "--end-year",
                "2017",
                "--station",
                "s47662",
            ]
        )

        assert hourly["calls"] == [
            ("s47662", SCRAPE_ELEMENTS, 2016, False),
            ("s47662", SCRAPE_ELEMENTS, 2017, False),
        ]

    def test_unmatched_station_aborts_before_downloading(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(tmp_path / "stations.csv", self.TWO_STATIONS)
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        with pytest.raises(ValueError, match="No stations with ids"):
            script.main(
                ["download", "hourly", "--stations-csv", str(stations_csv), "--station", "nope"]
            )

        assert hourly.get("calls", []) == []


# --------------------------------------------------------------------------- download stations


class TestDownloadStations:
    def test_refreshes_the_dbt_seed_by_default(self, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake(record))

        script.main(["download", "stations"])

        assert script.SEED_PATH == REPO_ROOT / "dbt/seeds/jma_stations.csv"
        # force=True is the contract: the seed must always be regenerated.
        assert record == {
            "dest": script.SEED_PATH,
            "staffed_only": True,
            "jepx_areas_only": True,
            "download": {"force": True},
        }

    def test_dest_override(self, tmp_path, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake(record))

        script.main(["download", "stations", "--dest", str(tmp_path / "stations.csv")])

        assert record == {
            "dest": tmp_path / "stations.csv",
            "staffed_only": True,
            "jepx_areas_only": True,
            "download": {"force": True},
        }


# --------------------------------------------------------------------------- download normals


def make_normals_fake(record: dict):
    class FakeNormals:
        def __init__(self, data_dir, timeout=60.0):
            record["data_dir"] = data_dir
            record["timeout"] = timeout

        def download_all(self, years=None):
            record["years"] = years
            return [Path(record["data_dir"]) / "2020/csv/daily/nml_sfc_d_47662.csv"]

    return FakeNormals


class TestDownloadNormals:
    def test_defaults(self, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_normals_fake(record))

        script.main(["download", "normals"])

        assert record == {"data_dir": Path("data/jma/normals"), "timeout": 60.0, "years": [2020]}

    def test_overrides(self, tmp_path, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_normals_fake(record))

        script.main(
            [
                "download",
                "normals",
                "--data-dir",
                str(tmp_path),
                "--timeout",
                "5",
                "--years",
                "2020",
            ]
        )

        assert record == {"data_dir": tmp_path, "timeout": 5.0, "years": [2020]}

    def test_an_unconfigured_year_is_rejected_by_the_parser(self, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_normals_fake(record))

        with pytest.raises(SystemExit) as exc:
            script.main(["download", "normals", "--years", "2030"])

        assert exc.value.code == 2
        assert record == {}


# --------------------------------------------------------------------------- download msm_surface_forecast


class TestDownloadMsmSurfaceForecast:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("jma")
        seen: dict = {}
        stations = [
            msm_stations.MsmStation(station_id="s47662", latitude=35.6, longitude=139.7),
            msm_stations.MsmStation(station_id="s47772", latitude=34.6, longitude=135.5),
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
        # The handler imports MsmDownloader when it runs (spec decision 8), so the fake
        # goes on the msm.download module, not on the script.
        monkeypatch.setattr(msm_download, "MsmDownloader", FakeDownloader)
        monkeypatch.setattr(module, "default_end_date", lambda: datetime.date(2026, 8, 22))
        return module, seen, stations

    def test_defaults(self, fake):
        module, seen, stations = fake

        module.main(["download", "msm_surface_forecast"])

        assert seen["stations_csv"] == REPO_ROOT / "dbt/seeds/jma_stations.csv"
        assert seen["station_areas_csv"] == REPO_ROOT / "dbt/seeds/jma_station_areas.csv"
        assert seen["data_dir"] == Path("data/jma/msm_surface_forecast")
        assert seen["start_date"] == msm_vintage.DEFAULT_BACKFILL_START
        assert seen["end_date"] == datetime.date(2026, 8, 22)
        assert seen["stations"] == stations
        assert seen["force"] is False
        assert seen["keep_grib"] is False

    def test_start_end_data_dir_overrides(self, fake, tmp_path):
        module, seen, _stations = fake

        module.main(
            [
                "download",
                "msm_surface_forecast",
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

        module.main(["download", "msm_surface_forecast", "--force", "--keep-grib"])

        assert seen["force"] is True
        assert seen["keep_grib"] is True


# --------------------------------------------------------------------------- the parser tree


def choices_in(error: str) -> list[str]:
    """The names argparse lists in an invalid-choice message, in its order."""
    listed = re.search(r"\(choose from (.*)\)", error)
    assert listed is not None, error
    return [name.strip("'") for name in listed.group(1).split(", ")]


CLASSES = (
    "JmaHourlyDownloader",
    "JmaNormalsDownloader",
    "JmaStationMasterDownloader",
    "JmaHourlyCsvLoader",
    "JmaNormalsCsvLoader",
    "MsmForecastCsvLoader",
)


class Untouched:
    """A stand-in for a downloader or loader class the run must never reach.

    Building the parser reads one constant off a class — ``--start-year`` defaults to
    ``JmaHourlyDownloader.EARLIEST_YEAR`` — and that is allowed; building or calling any of
    the classes before argparse has exited is not.
    """

    EARLIEST_YEAR = JmaHourlyDownloader.EARLIEST_YEAR

    def __init__(self, *args, **kwargs):
        raise AssertionError("a downloader or loader was built before the parser exited")


class TestParserTree:
    def test_download_lists_exactly_the_four_datasets(self, capsys):
        script = import_script("jma")

        with pytest.raises(SystemExit) as exc:
            script.main(["download", "nope"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == [
            "hourly",
            "normals",
            "msm_surface_forecast",
            "stations",
        ]

    def test_load_lists_exactly_the_three_datasets(self, capsys):
        script = import_script("jma")

        with pytest.raises(SystemExit) as exc:
            script.main(["load", "stations"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == ["hourly", "normals", "msm_surface_forecast"]

    @pytest.mark.parametrize("argv", [[], ["download"], ["load"], ["refresh", "hourly"]])
    def test_a_missing_or_unknown_verb_or_dataset_exits_2(self, argv, monkeypatch):
        script = import_script("jma")
        for name in CLASSES:
            monkeypatch.setattr(script, name, Untouched)

        with pytest.raises(SystemExit) as exc:
            script.main(argv)

        assert exc.value.code == 2

    @pytest.mark.parametrize(
        "argv, phrase",
        [
            (["-h"], "{download,load}"),
            (["download", "-h"], "{hourly,normals,msm_surface_forecast,stations}"),
            (["load", "-h"], "{hourly,normals,msm_surface_forecast}"),
            (["download", "hourly", "-h"], "The scrape is resumable"),
            # One word: argparse wraps a flag's help at the column, a description never.
            (["load", "normals", "-h"], "manifest"),
        ],
    )
    def test_help_at_every_level_exits_0(self, argv, phrase, capsys):
        script = import_script("jma")

        with pytest.raises(SystemExit) as exc:
            script.main(argv)

        out = capsys.readouterr().out
        assert exc.value.code == 0
        assert out.startswith("usage: jma")
        assert phrase in out


# --------------------------------------------------------------------------- the lazy import

GRIB = "power_market_analytics.ingestion.msm.grib"
DOWNLOAD = "power_market_analytics.ingestion.msm.download"


def block_eccodes(monkeypatch):
    """Make ``import eccodes`` fail and forget the two msm modules that reach it.

    ``None`` in ``sys.modules`` makes the import raise ``ImportError``; the two entries are
    removed so a fresh import runs their module code under the block. monkeypatch restores
    all three afterwards.
    """
    monkeypatch.setitem(sys.modules, "eccodes", None)
    for name in (GRIB, DOWNLOAD):
        monkeypatch.delitem(sys.modules, name, raising=False)


class TestMsmDownloaderImportIsLazy:
    def test_the_script_imports_without_eccodes(self, monkeypatch):
        block_eccodes(monkeypatch)
        with pytest.raises(ImportError):  # the control: the block is live
            importlib.import_module(GRIB)

        script = import_script("jma")  # must not raise: msm.download is not imported here

        assert not hasattr(script, "MsmDownloader")

    def test_only_the_msm_download_handler_needs_eccodes(self, monkeypatch, capsys):
        block_eccodes(monkeypatch)
        script = import_script("jma")

        # Another subcommand runs to completion under the block — through build_parser and
        # a load handler's help — so an import argparse reaches before dispatch would fail here.
        with pytest.raises(SystemExit) as exc:
            script.main(["load", "normals", "-h"])
        assert exc.value.code == 0
        assert "usage: jma load normals" in capsys.readouterr().out

        with pytest.raises(ImportError):
            script.main(["download", "msm_surface_forecast"])
