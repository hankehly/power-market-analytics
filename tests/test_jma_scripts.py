"""CLI entry points for the JMA weather pipeline (scripts/)."""

from __future__ import annotations

import csv
import datetime
import os
import time
from pathlib import Path

import pytest
from loguru import logger

from power_market_analytics.jma import JmaHourlyDownloader, JmaStationMasterDownloader
from tests.support import REPO_ROOT, import_script

TODAY = datetime.date.today()
CORE_ELEMENTS = ["temperature", "precipitation", "sunshine", "wind"]


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


def core_path(data_dir: Path, station_id: str, year: int) -> Path:
    """Where the orchestrator expects a core-element file (hand-derived name)."""
    return data_dir / f"{station_id}_101-201-301-401_{year}.csv"


# --------------------------------------------------------------------------- download_jma_hourly


def make_hourly_fake(record: dict, failing: set[str] = frozenset(), write_root: Path | None = None):
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


class TestDownloadJmaHourly:
    def test_defaults_cover_tokyo_core_set_from_2016_forcing_only_this_year(self, monkeypatch):
        script = import_script("download_jma_hourly")
        record: dict = {}
        monkeypatch.setattr(script, "JmaHourlyDownloader", make_hourly_fake(record))

        script.main([])

        assert record["data_dir"] == Path("data/jma/hourly")
        elements = ["temperature", "precipitation", "sunshine", "wind"]
        assert record["calls"] == [
            ("s47662", elements, year, year == TODAY.year) for year in range(2016, TODAY.year + 1)
        ]

    def test_explicit_past_range_is_served_from_cache(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly")
        record: dict = {}
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(record, write_root=tmp_path)
        )

        script.main(
            [
                "--station",
                "a0368",
                "--elements",
                "wind",
                "temperature",
                "--start-year",
                "2016",
                "--end-year",
                "2017",
                "--data-dir",
                str(tmp_path),
            ]
        )

        assert record["data_dir"] == tmp_path
        assert record["calls"] == [
            ("a0368", ["wind", "temperature"], 2016, False),
            ("a0368", ["wind", "temperature"], 2017, False),
        ]
        assert (tmp_path / "a0368_201-301_2016.csv").exists()

    def test_force_all_ignores_the_cache_for_every_year(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly")
        record: dict = {}
        monkeypatch.setattr(script, "JmaHourlyDownloader", make_hourly_fake(record))

        script.main(["--start-year", "2016", "--end-year", "2017", "--force-all"])

        assert [c[3] for c in record["calls"]] == [True, True]

    def test_current_year_is_forced_even_without_force_all(self, monkeypatch):
        script = import_script("download_jma_hourly")
        record: dict = {}
        monkeypatch.setattr(script, "JmaHourlyDownloader", make_hourly_fake(record))

        script.main(["--start-year", str(TODAY.year - 1), "--end-year", str(TODAY.year)])

        assert [(c[2], c[3]) for c in record["calls"]] == [
            (TODAY.year - 1, False),
            (TODAY.year, True),
        ]

    def test_unknown_element_is_rejected_by_the_parser(self, monkeypatch):
        script = import_script("download_jma_hourly")
        record: dict = {}
        monkeypatch.setattr(script, "JmaHourlyDownloader", make_hourly_fake(record))

        with pytest.raises(SystemExit) as exc:
            script.main(["--elements", "rainbow"])

        assert exc.value.code == 2
        assert record.get("calls", []) == []


# --------------------------------------------------------------------------- download_jma_hourly_all


class TestBuildPlan:
    def test_station_major_years(self, tmp_path):
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
        stations = write_stations(
            tmp_path / "stations.csv", [{"station_id": "s47662", "prefecture_code": "44"}]
        )
        with pytest.raises(ValueError, match=r"No stations in prefecture codes \[99\]"):
            script.build_plan(stations, 2016, 2016, None, prefectures=[99])

    def test_limit_truncates_after_the_prefecture_filter(self, tmp_path):
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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


def make_station_master_fake(record: dict, rows: list[dict] | None = None):
    """A ``JmaStationMasterDownloader`` stand-in that writes ``rows`` to ``dest`` if absent."""

    class FakeStationMaster:
        def __init__(self, dest, staffed_only=False):
            record["dest"] = Path(dest)
            record["staffed_only"] = staffed_only

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


class TestDownloadJmaHourlyAll:
    TWO_STATIONS = [
        {"station_id": "s47662", "prefecture_code": "44"},
        {"station_id": "a0368", "prefecture_code": "44"},
    ]

    def test_dry_run_plans_but_downloads_nothing(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly_all")
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
        core_path(data_dir, "s47662", 2016).parent.mkdir()
        core_path(data_dir, "s47662", 2016).write_bytes(b"old")
        messages, sink = capture_logs()
        try:
            result = script.main(
                [
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
            "staffed_only": False,
            "download": {"force": False},
        }
        assert stations_csv.exists()
        assert hourly["calls"] == []
        assert "Dry run: would download 3 of 4 station-years" in messages

    def test_full_run_downloads_every_station_year(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly_all")
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
            "staffed_only": False,
            "download": {"force": False},
        }
        assert hourly["data_dir"] == data_dir
        assert hourly["request_interval"] == 0.5
        assert hourly["calls"] == [
            ("s47662", CORE_ELEMENTS, 2016, False),
            ("s47662", CORE_ELEMENTS, 2017, False),
            ("a0368", CORE_ELEMENTS, 2016, False),
            ("a0368", CORE_ELEMENTS, 2017, False),
        ]
        assert sorted(p.name for p in data_dir.iterdir()) == [
            "a0368_101-201-301-401_2016.csv",
            "a0368_101-201-301-401_2017.csv",
            "s47662_101-201-301-401_2016.csv",
            "s47662_101-201-301-401_2017.csv",
        ]

    def test_prefecture_and_limit_flow_into_the_plan(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly_all")
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

        assert hourly["calls"] == [("s47662", CORE_ELEMENTS, 2016, False)]

    def test_unmatched_prefecture_aborts_before_downloading(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly_all")
        hourly: dict = {}
        stations_csv = write_stations(tmp_path / "stations.csv", self.TWO_STATIONS)
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        with pytest.raises(ValueError, match="No stations in prefecture codes"):
            script.main(["--stations-csv", str(stations_csv), "--prefecture", "99"])

        assert hourly.get("calls", []) == []

    def test_failing_station_is_skipped_and_reported_with_exit_1(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly_all")
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
            "a0368_101-201-301-401_2016.csv",
            "a0368_101-201-301-401_2017.csv",
            "s47662_101-201-301-401_2016.csv",
            "s47662_101-201-301-401_2017.csv",
        ]
        assert messages == [
            "FAILED bad 2016: 503 for bad",
            "FAILED bad 2017: 503 for bad",
            "2 failures (re-run to retry):",
            "  bad 2016: 503 for bad",
            "  bad 2017: 503 for bad",
        ]

    def test_ten_consecutive_failures_abort_the_run(self, tmp_path, monkeypatch):
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
        script = import_script("download_jma_hourly_all")
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
            path = core_path(data_dir, "stale", year)
            path.write_bytes(b"old")
            two_days_ago = time.time() - 2 * 86400
            os.utime(path, (two_days_ago, two_days_ago))
        # … and a this-year file written just now.
        core_path(data_dir, "fresh", TODAY.year).write_bytes(b"today")

        script.main(
            [
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
        script = import_script("download_jma_hourly_all")
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


# --------------------------------------------------------------------------- update_jma_stations_seed


class TestUpdateJmaStationsSeed:
    def test_refreshes_the_dbt_seed_by_default(self, monkeypatch):
        script = import_script("update_jma_stations_seed")
        record: dict = {}
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake(record))

        script.main([])

        assert script.SEED_PATH == REPO_ROOT / "dbt/seeds/jma_stations.csv"
        # force=True is the contract: the seed must always be regenerated.
        assert record == {
            "dest": script.SEED_PATH,
            "staffed_only": True,
            "download": {"force": True},
        }

    def test_dest_override(self, tmp_path, monkeypatch):
        script = import_script("update_jma_stations_seed")
        record: dict = {}
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake(record))

        script.main(["--dest", str(tmp_path / "stations.csv")])

        assert record == {
            "dest": tmp_path / "stations.csv",
            "staffed_only": True,
            "download": {"force": True},
        }
