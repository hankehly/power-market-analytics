"""Tests for the pure MSM GPV core (run mapping, grid selection, conversions).

No HTTP, no GRIB here — only date/time arithmetic, unit conversions,
nearest-grid-point selection, the station-seed loader and the raw CSV loader.
The GRIB2 decoder and downloader in the same module are covered by
``test_msm_grib.py`` and ``test_msm_downloader.py``.
"""

from __future__ import annotations

import csv
import datetime
from pathlib import Path

import pytest

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.msm import (
    BASE_URL,
    DEFAULT_BACKFILL_START,
    EARLIEST_DELIVERY_DATE,
    JST,
    MSM_SURFACE_ELEMENTS,
    RAW_CSV_COLUMNS,
    MsmElement,
    MsmError,
    MsmForecastCsvLoader,
    MsmGrid,
    MsmSourceFile,
    MsmStation,
    element_for,
    haversine_km,
    hour_ending_for,
    issue_cutoff_for,
    kelvin_to_celsius,
    load_stations,
    pa_to_hpa,
    reference_at_for,
    select_grid_point,
    source_files_for,
    time_codes_for,
    valid_at_for,
    wind_speed,
    wm2_to_mjm2,
)
from tests.support import REPO_ROOT

# --------------------------------------------------------------------------- constants


class TestModuleConstants:
    def test_earliest_delivery_date(self):
        assert EARLIEST_DELIVERY_DATE == datetime.date(2019, 4, 1)

    def test_default_backfill_start(self):
        assert DEFAULT_BACKFILL_START == datetime.date(2022, 4, 1)

    def test_jst_is_a_fixed_utc_plus_9_offset(self):
        assert JST == datetime.timezone(datetime.timedelta(hours=9))

    def test_base_url(self):
        assert BASE_URL == "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original"


# --------------------------------------------------------------------------- reference / cutoff


class TestReferenceAtFor:
    def test_reference_at_is_12_utc_two_days_before_delivery(self):
        assert reference_at_for(datetime.date(2026, 8, 19)) == datetime.datetime(
            2026, 8, 17, 12, 0, tzinfo=datetime.timezone.utc
        )

    def test_reference_at_renders_as_21_00_jst_on_d_minus_2(self):
        reference_at = reference_at_for(datetime.date(2026, 8, 19))
        jst_rendering = reference_at.astimezone(JST)
        assert jst_rendering == datetime.datetime(2026, 8, 17, 21, 0, tzinfo=JST)

    def test_result_is_timezone_aware_utc(self):
        reference_at = reference_at_for(datetime.date(2026, 8, 19))
        assert reference_at.tzinfo == datetime.timezone.utc

    @pytest.mark.parametrize(
        "delivery_date",
        [
            datetime.date(2019, 4, 3),
            datetime.date(2022, 4, 1),
            datetime.date(2024, 2, 29),
            datetime.date(2026, 12, 31),
        ],
    )
    def test_month_and_year_boundaries(self, delivery_date):
        expected_day = delivery_date - datetime.timedelta(days=2)
        reference_at = reference_at_for(delivery_date)
        assert reference_at.date() == expected_day
        assert (reference_at.hour, reference_at.minute) == (12, 0)


class TestIssueCutoffFor:
    def test_cutoff_is_09_30_jst_on_d_minus_1(self):
        assert issue_cutoff_for(datetime.date(2026, 8, 19)) == datetime.datetime(
            2026, 8, 18, 9, 30, tzinfo=JST
        )

    def test_result_is_timezone_aware_jst(self):
        cutoff = issue_cutoff_for(datetime.date(2026, 8, 19))
        assert cutoff.tzinfo == JST

    @pytest.mark.parametrize(
        "delivery_date",
        [
            datetime.date(2019, 4, 3),
            datetime.date(2022, 4, 1),
            datetime.date(2024, 2, 29),
            datetime.date(2026, 12, 31),
            datetime.date(2026, 1, 1),
        ],
    )
    def test_reference_at_always_precedes_the_leakage_gate(self, delivery_date):
        # The forecast run (D-2 21:00 JST) must be available strictly before
        # the demand-model cutoff (D-1 09:30 JST) that would consume it.
        assert reference_at_for(delivery_date) < issue_cutoff_for(delivery_date)


# --------------------------------------------------------------------------- source files


class TestSourceFilesFor:
    def test_exact_file_names_urls_and_lead_ranges_in_order(self):
        files = source_files_for(datetime.date(2026, 8, 19))

        assert len(files) == 3
        fh16_33, fh34_39, fh40_51 = files

        assert fh16_33 == MsmSourceFile(
            file_name="Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin",
            url=f"{BASE_URL}/2026/08/17/Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin",
            leads_used=range(28, 34),
        )
        assert fh34_39 == MsmSourceFile(
            file_name="Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH34-39_grib2.bin",
            url=f"{BASE_URL}/2026/08/17/Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH34-39_grib2.bin",
            leads_used=range(34, 40),
        )
        assert fh40_51 == MsmSourceFile(
            file_name="Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH40-51_grib2.bin",
            url=f"{BASE_URL}/2026/08/17/Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH40-51_grib2.bin",
            leads_used=range(40, 52),
        )

    def test_directory_uses_the_reference_date_not_the_delivery_date(self):
        # Delivery 2026-01-01 -> reference date 2025-12-30 (year/month boundary).
        files = source_files_for(datetime.date(2026, 1, 1))
        assert all("/2025/12/30/" in f.url for f in files)
        assert all("20251230120000" in f.file_name for f in files)

    def test_source_file_is_immutable(self):
        files = source_files_for(datetime.date(2026, 8, 19))
        with pytest.raises(AttributeError):
            files[0].file_name = "x"  # type: ignore[misc]


# --------------------------------------------------------------------------- valid_at_for


class TestValidAtFor:
    def test_lead_28_is_01_00_jst_on_delivery_day(self):
        valid_at = valid_at_for(datetime.date(2026, 8, 19), 28)
        assert valid_at == datetime.datetime(2026, 8, 18, 16, 0, tzinfo=datetime.timezone.utc)
        assert valid_at.astimezone(JST) == datetime.datetime(2026, 8, 19, 1, 0, tzinfo=JST)

    def test_lead_51_is_24_00_jst_stored_as_next_day_00_00(self):
        valid_at = valid_at_for(datetime.date(2026, 8, 19), 51)
        assert valid_at == datetime.datetime(2026, 8, 19, 15, 0, tzinfo=datetime.timezone.utc)
        assert valid_at.astimezone(JST) == datetime.datetime(2026, 8, 20, 0, 0, tzinfo=JST)

    def test_equals_reference_at_plus_lead_hours(self):
        delivery_date = datetime.date(2026, 8, 19)
        for lead in (28, 33, 34, 39, 40, 51):
            assert valid_at_for(delivery_date, lead) == reference_at_for(
                delivery_date
            ) + datetime.timedelta(hours=lead)


# --------------------------------------------------------------------------- hour ending / time codes


class TestHourEndingFor:
    @pytest.mark.parametrize(
        "lead_hours, expected",
        [(28, 1), (33, 6), (34, 7), (39, 12), (40, 13), (51, 24)],
    )
    def test_lead_to_hour_ending(self, lead_hours, expected):
        assert hour_ending_for(lead_hours) == expected

    @pytest.mark.parametrize("bad_lead", [0, 27, 52, 100, -1])
    def test_out_of_range_leads_raise(self, bad_lead):
        with pytest.raises(ValueError, match=str(bad_lead)):
            hour_ending_for(bad_lead)


class TestTimeCodesFor:
    @pytest.mark.parametrize(
        "hour_ending, expected",
        [(1, (1, 2)), (24, (47, 48)), (12, (23, 24)), (13, (25, 26))],
    )
    def test_hour_ending_to_time_codes(self, hour_ending, expected):
        assert time_codes_for(hour_ending) == expected

    @pytest.mark.parametrize("hour_ending", range(1, 25))
    def test_round_trips_via_the_downstream_formula(self, hour_ending):
        _, end_code = time_codes_for(hour_ending)
        assert (end_code + 1) // 2 == hour_ending

    @pytest.mark.parametrize("bad_hour", [0, 25, -1, 100])
    def test_out_of_range_hours_raise(self, bad_hour):
        with pytest.raises(ValueError, match=str(bad_hour)):
            time_codes_for(bad_hour)


# --------------------------------------------------------------------------- conversions


class TestConversions:
    def test_kelvin_to_celsius(self):
        assert kelvin_to_celsius(273.15) == 0.0

    def test_pa_to_hpa(self):
        assert pa_to_hpa(101325.0) == 1013.25

    def test_wind_speed(self):
        assert wind_speed(3.0, 4.0) == 5.0

    def test_wm2_to_mjm2(self):
        assert wm2_to_mjm2(250.0) == pytest.approx(0.9)


class TestHaversineKm:
    def test_zero_at_identical_points(self):
        assert haversine_km(35.0, 139.0, 35.0, 139.0) == 0.0

    def test_known_pair(self):
        assert haversine_km(35.0, 139.0, 35.05, 139.0) == pytest.approx(5.56, abs=0.01)

    def test_symmetric(self):
        a = haversine_km(35.0, 139.0, 36.0, 140.0)
        b = haversine_km(36.0, 140.0, 35.0, 139.0)
        assert a == pytest.approx(b)


# --------------------------------------------------------------------------- grid selection

NS_GRID = MsmGrid(
    ni=5,
    nj=5,
    first_latitude=36.0,
    first_longitude=139.0,
    latitude_step=-0.05,
    longitude_step=0.0625,
)
#: Same geographic domain, rows scanned south -> north instead.
SN_GRID = MsmGrid(
    ni=5,
    nj=5,
    first_latitude=35.80,
    first_longitude=139.0,
    latitude_step=0.05,
    longitude_step=0.0625,
)


class TestSelectGridPoint:
    def test_interior_nearest_point(self):
        point = select_grid_point(NS_GRID, 35.83, 139.14)
        # j_exact = (35.83-36.0)/-0.05 = 3.4 -> 3; i_exact = (139.14-139.0)/0.0625 = 2.24 -> 2
        assert point.latitude == pytest.approx(35.85)
        assert point.longitude == pytest.approx(139.125)
        assert point.flat_index == 3 * NS_GRID.ni + 2
        expected_distance = round(haversine_km(35.83, 139.14, 35.85, 139.125), 3)
        assert point.distance_km == pytest.approx(expected_distance)

    def test_flat_index_formula(self):
        point = select_grid_point(NS_GRID, 35.83, 139.14)
        assert point.flat_index == 3 * NS_GRID.ni + 2

    def test_exact_tie_picks_the_lower_index_on_each_axis(self):
        # Latitude midpoint between j=2 (35.90) and j=3 (35.85): j_exact == 2.5 exactly
        # (chosen so the -0.05 step's binary rounding still lands on an exact half).
        # Longitude midpoint between i=1 (139.0625) and i=2 (139.125): i_exact == 1.5 exactly.
        query_lat = NS_GRID.first_latitude + 2.5 * NS_GRID.latitude_step
        query_lon = NS_GRID.first_longitude + 1.5 * NS_GRID.longitude_step
        assert (query_lat - NS_GRID.first_latitude) / NS_GRID.latitude_step == 2.5
        assert (query_lon - NS_GRID.first_longitude) / NS_GRID.longitude_step == 1.5

        point = select_grid_point(NS_GRID, query_lat, query_lon)

        assert point.flat_index == 2 * NS_GRID.ni + 1

    def test_south_north_grid_selects_the_same_geographic_point(self):
        # Node shared by both grids: latitude 35.95 (NS j=1, SN j=3), longitude 139.125 (i=2).
        ns_point = select_grid_point(NS_GRID, 35.95, 139.125)
        sn_point = select_grid_point(SN_GRID, 35.95, 139.125)

        assert ns_point.latitude == pytest.approx(sn_point.latitude)
        assert ns_point.longitude == pytest.approx(sn_point.longitude)
        # Reversed row scan -> different flat index for the same geography.
        assert ns_point.flat_index != sn_point.flat_index

    @pytest.mark.parametrize(
        "latitude, longitude",
        [
            (36.0, 139.0),  # NW
            (36.0, 139.25),  # NE
            (35.80, 139.0),  # SW
            (35.80, 139.25),  # SE
        ],
    )
    def test_corner_points_are_in_domain(self, latitude, longitude):
        point = select_grid_point(NS_GRID, latitude, longitude)
        assert 0 <= point.flat_index < NS_GRID.ni * NS_GRID.nj

    @pytest.mark.parametrize(
        "latitude, longitude",
        [
            (36.001, 139.10),  # just north of the domain
            (35.799, 139.10),  # just south of the domain
            (35.90, 138.999),  # just west of the domain
            (35.90, 139.251),  # just east of the domain
        ],
    )
    def test_just_outside_each_edge_raises(self, latitude, longitude):
        with pytest.raises(MsmError, match="outside the MSM domain"):
            select_grid_point(NS_GRID, latitude, longitude)


# --------------------------------------------------------------------------- elements


class TestMsmSurfaceElements:
    def test_exactly_twelve_elements_in_the_documented_order(self):
        assert MSM_SURFACE_ELEMENTS == (
            MsmElement("surface_pressure_pa", 0, 3, 0, 1, False),
            MsmElement("sea_level_pressure_pa", 0, 3, 1, 101, False),
            MsmElement("u_wind_ms", 0, 2, 2, 103, False),
            MsmElement("v_wind_ms", 0, 2, 3, 103, False),
            MsmElement("temperature_k", 0, 0, 0, 103, False),
            MsmElement("relative_humidity_pct", 0, 1, 1, 103, False),
            MsmElement("precipitation_mm", 0, 1, 8, 1, True),
            MsmElement("shortwave_radiation_wm2", 0, 4, 7, 1, True),
            MsmElement("total_cloud_cover_pct", 0, 6, 1, 1, False),
            MsmElement("low_cloud_cover_pct", 0, 6, 3, 1, False),
            MsmElement("middle_cloud_cover_pct", 0, 6, 4, 1, False),
            MsmElement("high_cloud_cover_pct", 0, 6, 5, 1, False),
        )

    def test_element_is_immutable(self):
        with pytest.raises(AttributeError):
            MSM_SURFACE_ELEMENTS[0].key = "x"  # type: ignore[misc]


class TestElementFor:
    def test_known_triple_resolves(self):
        element = element_for(0, 3, 0)
        assert element is not None
        assert element.key == "surface_pressure_pa"

    def test_statistical_flag_distinguishes_accumulations(self):
        precipitation = element_for(0, 1, 8)
        assert precipitation is not None
        assert precipitation.statistical is True

        humidity = element_for(0, 1, 1)
        assert humidity is not None
        assert humidity.statistical is False

    def test_unmatched_triple_returns_none(self):
        assert element_for(0, 19, 0) is None


# --------------------------------------------------------------------------- load_stations

STATIONS_HEADER = [
    "station_id",
    "prefecture_code",
    "station_name",
    "station_kana",
    "latitude",
    "longitude",
    "elevation_m",
    "kansoku",
    "obs_precipitation",
    "obs_wind",
    "obs_temperature",
    "obs_sunshine",
    "obs_snow",
    "obs_other",
    "observation_ended_on",
]
AREAS_HEADER = ["station_id", "area_code", "assignment_basis"]


def station_row(
    station_id: str, latitude: str = "35.0", longitude: str = "139.0", **overrides
) -> dict:
    row = {
        "station_id": station_id,
        "prefecture_code": "13",
        "station_name": "テスト",
        "station_kana": "テスト",
        "latitude": latitude,
        "longitude": longitude,
        "elevation_m": "5.0",
        "kansoku": "111111",
        "obs_precipitation": "1",
        "obs_wind": "1",
        "obs_temperature": "1",
        "obs_sunshine": "1",
        "obs_snow": "1",
        "obs_other": "1",
        "observation_ended_on": "",
    }
    row.update(overrides)
    return row


def write_stations_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STATIONS_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_areas_csv(path: Path, station_ids: list[str]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=AREAS_HEADER)
        writer.writeheader()
        for station_id in station_ids:
            writer.writerow(
                {
                    "station_id": station_id,
                    "area_code": "tokyo",
                    "assignment_basis": "prefecture in the TSO supply area",
                }
            )
    return path


class TestLoadStations:
    def test_happy_path_sorted_by_station_id(self, tmp_path):
        stations_csv = write_stations_csv(
            tmp_path / "stations.csv",
            [
                station_row("s3", latitude="35.3", longitude="139.3"),
                station_row("s1", latitude="35.1", longitude="139.1"),
                station_row("s2", latitude="35.2", longitude="139.2"),
            ],
        )
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1", "s2", "s3"])

        stations = load_stations(stations_csv, areas_csv)

        assert stations == [
            MsmStation(station_id="s1", latitude=35.1, longitude=139.1),
            MsmStation(station_id="s2", latitude=35.2, longitude=139.2),
            MsmStation(station_id="s3", latitude=35.3, longitude=139.3),
        ]

    def test_accepts_str_paths(self, tmp_path):
        stations_csv = write_stations_csv(tmp_path / "stations.csv", [station_row("s1")])
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1"])

        stations = load_stations(str(stations_csv), str(areas_csv))

        assert [s.station_id for s in stations] == ["s1"]

    def test_discontinued_station_is_kept(self, tmp_path):
        stations_csv = write_stations_csv(
            tmp_path / "stations.csv",
            [
                station_row("s1"),
                station_row("s2", observation_ended_on="2020-01-01"),
            ],
        )
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1", "s2"])

        stations = load_stations(stations_csv, areas_csv)

        assert [s.station_id for s in stations] == ["s1", "s2"]

    def test_missing_mapping_row_raises_naming_the_station(self, tmp_path):
        stations_csv = write_stations_csv(
            tmp_path / "stations.csv", [station_row("s1"), station_row("s2")]
        )
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1"])  # s2 has no mapping row

        with pytest.raises(MsmError, match="s2"):
            load_stations(stations_csv, areas_csv)

    def test_empty_latitude_raises(self, tmp_path):
        stations_csv = write_stations_csv(
            tmp_path / "stations.csv", [station_row("s1", latitude="")]
        )
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1"])

        with pytest.raises(MsmError, match="s1"):
            load_stations(stations_csv, areas_csv)

    def test_empty_longitude_raises(self, tmp_path):
        stations_csv = write_stations_csv(
            tmp_path / "stations.csv", [station_row("s1", longitude="")]
        )
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1"])

        with pytest.raises(MsmError, match="s1"):
            load_stations(stations_csv, areas_csv)

    def test_extra_mapping_only_row_is_ignored(self, tmp_path):
        stations_csv = write_stations_csv(tmp_path / "stations.csv", [station_row("s1")])
        areas_csv = write_areas_csv(tmp_path / "areas.csv", ["s1", "s_not_a_station"])

        stations = load_stations(stations_csv, areas_csv)

        assert [s.station_id for s in stations] == ["s1"]


class TestLoadStationsRealSeedSanity:
    def test_real_seeds_load_and_lie_inside_the_jepx_area_bounding_box(self):
        stations = load_stations(
            REPO_ROOT / "dbt/seeds/jma_stations.csv",
            REPO_ROOT / "dbt/seeds/jma_station_areas.csv",
        )

        assert len(stations) > 0
        for station in stations:
            assert 22.4 <= station.latitude <= 47.6
            assert 120 <= station.longitude <= 150


# --------------------------------------------------------------------------- RAW_CSV_COLUMNS


class TestRawCsvColumns:
    def test_exact_column_order(self):
        assert RAW_CSV_COLUMNS == (
            "station_id",
            "station_latitude",
            "station_longitude",
            "grid_latitude",
            "grid_longitude",
            "grid_distance_km",
            "forecast_reference_at_utc",
            "forecast_valid_at_utc",
            "forecast_lead_hours",
            "temperature_c",
            "relative_humidity_pct",
            "u_wind_ms",
            "v_wind_ms",
            "wind_speed_ms",
            "precipitation_mm",
            "surface_pressure_hpa",
            "sea_level_pressure_hpa",
            "shortwave_radiation_wm2",
            "solar_radiation_mjm2",
            "total_cloud_cover_pct",
            "high_cloud_cover_pct",
            "middle_cloud_cover_pct",
            "low_cloud_cover_pct",
            "source_file_name",
        )


# --------------------------------------------------------------------------- loader

EMPTY_SCHEMA = CsvTableSchema(columns=[])


class TestMsmForecastCsvLoader:
    def test_directory_globs_csv_gz_only(self, spark, tmp_path):
        (tmp_path / "msm_surface_20260817.csv.gz").write_bytes(b"")
        (tmp_path / "msm_surface_20260818.csv.gz").write_bytes(b"")
        (tmp_path / "manifest.json").write_bytes(b"{}")
        loader = MsmForecastCsvLoader(EMPTY_SCHEMA, tmp_path, "t", spark=spark)

        assert loader._resolve_files() == [
            str(tmp_path / "msm_surface_20260817.csv.gz"),
            str(tmp_path / "msm_surface_20260818.csv.gz"),
        ]

    def test_glob_pattern_is_honored_for_non_directory_paths(self, spark, tmp_path):
        (tmp_path / "msm_surface_20260817.csv.gz").write_bytes(b"")
        (tmp_path / "msm_surface_20260818.csv.gz").write_bytes(b"")
        loader = MsmForecastCsvLoader(
            EMPTY_SCHEMA, tmp_path / "*20260817*.csv.gz", "t", spark=spark
        )

        assert loader._resolve_files() == [str(tmp_path / "msm_surface_20260817.csv.gz")]

    def test_single_file_path(self, spark, tmp_path):
        path = tmp_path / "msm_surface_20260817.csv.gz"
        path.write_bytes(b"")
        loader = MsmForecastCsvLoader(EMPTY_SCHEMA, path, "t", spark=spark)

        assert loader._resolve_files() == [str(path)]

    def test_no_files_raises_with_the_msm_specific_message(self, spark, tmp_path):
        loader = MsmForecastCsvLoader(EMPTY_SCHEMA, tmp_path, "t", spark=spark)

        with pytest.raises(FileNotFoundError, match="No MSM forecast csv.gz files found"):
            loader._resolve_files()
