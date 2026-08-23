"""Tests for the ecCodes GRIB2 decode layer (:mod:`power_market_analytics.msm_grib`).

Every fixture is a real GRIB2 file encoded by ecCodes itself
(:mod:`tests.msm_grib_support`), so the decoder is exercised against real key
names, a real scan order and a real bitmap rather than a stand-in. The grid is
a 4x3 toy grid; the source file is the real FH16-33 member spec (leads 28-33),
so a complete fixture holds 12 elements x 6 leads = 72 messages.

No fixture variant had to be dropped: the sample template accepts every key
the decoder reads, including ``jScansPositively``, ``iScansNegatively`` and
``jPointsAreConsecutive`` (see the support module's docstring).
"""

from __future__ import annotations

import datetime

import pytest

from power_market_analytics.msm import (
    MSM_SURFACE_ELEMENTS,
    RAW_CSV_COLUMNS,
    MsmElement,
    MsmError,
    MsmGrid,
    MsmSourceFile,
    MsmStation,
    haversine_km,
    reference_at_for,
    source_files_for,
)
from power_market_analytics.msm_grib import (
    VALUE_COLUMNS,
    MsmExtractError,
    StationHourRecord,
    extract_station_records,
)
from tests.msm_grib_support import (
    MINUTE_STEP_UNIT,
    build_day_file,
    build_edition1_message,
    build_file,
    build_message,
    build_multi_field_file,
    constant_value,
    day_messages,
    read_message_keys,
    varying_value,
)

DELIVERY_DATE = datetime.date(2026, 8, 19)
REFERENCE_AT = reference_at_for(DELIVERY_DATE)  # 2026-08-17 12:00 UTC
SOURCE_FILE = source_files_for(DELIVERY_DATE)[0]  # FH16-33, leads 28..33

#: Toy grid scanning north to south (negative latitude step), 4 x 3 points:
#: latitudes 36.0 / 35.5 / 35.0, longitudes 139.0 .. 140.5.
GRID = MsmGrid(
    ni=4,
    nj=3,
    first_latitude=36.0,
    first_longitude=139.0,
    latitude_step=-0.5,
    longitude_step=0.5,
)

#: Deliberately unsorted, so the sort order of the result is a real assertion.
#: s00001/s00003 sit exactly on a grid point, s00002 slightly off one.
STATIONS = (
    MsmStation("s00003", 35.0, 140.5),  # flat index 11
    MsmStation("s00001", 36.0, 139.0),  # flat index 0
    MsmStation("s00002", 35.49, 139.98),  # nearest point (35.5, 140.0) = flat index 6
)
FLAT_INDEX = {"s00001": 0, "s00002": 6, "s00003": 11}


def extract(path, *, source_file=SOURCE_FILE, reference_at=REFERENCE_AT, stations=STATIONS):
    """Run the decoder over ``path`` with this module's defaults."""
    return extract_station_records(path, source_file, reference_at, list(stations))


def records_by_key(records):
    """Index records by ``(station_id, forecast_lead_hours)``."""
    return {(r.station_id, r.forecast_lead_hours): r for r in records}


@pytest.fixture
def constant_file(tmp_path):
    """A complete FH16-33 member whose every point holds the element's base value."""
    return build_day_file(
        tmp_path / SOURCE_FILE.file_name, SOURCE_FILE, REFERENCE_AT, GRID, constant_value
    )


@pytest.fixture
def varying_file(tmp_path):
    """A complete FH16-33 member whose value differs per (element, lead, grid point)."""
    return build_day_file(
        tmp_path / SOURCE_FILE.file_name, SOURCE_FILE, REFERENCE_AT, GRID, varying_value
    )


# --------------------------------------------------------------------------- value columns


class TestValueColumns:
    def test_value_columns_are_the_value_slice_of_the_raw_csv_header(self):
        # Task 3 writes record.values by name into these csv columns.
        assert VALUE_COLUMNS == RAW_CSV_COLUMNS[9:-1]
        assert VALUE_COLUMNS[0] == "temperature_c"
        assert VALUE_COLUMNS[-1] == "low_cloud_cover_pct"
        assert len(VALUE_COLUMNS) == 14


# --------------------------------------------------------------------------- happy path


class TestExtractedRecords:
    def test_one_record_per_station_and_lead(self, constant_file):
        records = extract(constant_file)
        assert len(records) == len(STATIONS) * len(SOURCE_FILE.leads_used)
        assert {(r.station_id, r.forecast_lead_hours) for r in records} == {
            (station.station_id, lead) for station in STATIONS for lead in SOURCE_FILE.leads_used
        }

    def test_records_are_sorted_by_station_then_lead(self, constant_file):
        records = extract(constant_file)
        assert [(r.station_id, r.forecast_lead_hours) for r in records] == sorted(
            (r.station_id, r.forecast_lead_hours) for r in records
        )
        assert records[0].station_id == "s00001"
        assert records[0].forecast_lead_hours == 28

    def test_record_is_a_frozen_dataclass(self, constant_file):
        record = extract(constant_file)[0]
        assert isinstance(record, StationHourRecord)
        with pytest.raises(AttributeError):
            record.station_id = "x"  # type: ignore[misc]

    def test_reference_and_valid_times_are_utc_and_one_lead_apart(self, constant_file):
        for record in extract(constant_file):
            assert record.forecast_reference_at == REFERENCE_AT
            assert record.forecast_reference_at.tzinfo == datetime.timezone.utc
            assert record.forecast_valid_at.tzinfo == datetime.timezone.utc
            assert record.forecast_valid_at == REFERENCE_AT + datetime.timedelta(
                hours=record.forecast_lead_hours
            )

    def test_first_and_last_lead_cover_the_delivery_day_band(self, constant_file):
        leads = sorted({r.forecast_lead_hours for r in extract(constant_file)})
        assert leads == list(range(28, 34))

    def test_station_coordinates_are_carried_through(self, constant_file):
        by_key = records_by_key(extract(constant_file))
        record = by_key[("s00002", 31)]
        assert (record.station_latitude, record.station_longitude) == (35.49, 139.98)

    def test_grid_geometry_comes_from_the_nearest_grid_point(self, constant_file):
        by_key = records_by_key(extract(constant_file))
        exact = by_key[("s00001", 28)]
        assert (exact.grid_latitude, exact.grid_longitude) == (36.0, 139.0)
        assert exact.grid_distance_km == 0.0
        offset = by_key[("s00002", 28)]
        assert (offset.grid_latitude, offset.grid_longitude) == (35.5, 140.0)
        assert offset.grid_distance_km == round(haversine_km(35.49, 139.98, 35.5, 140.0), 3)
        assert offset.grid_distance_km > 0

    def test_source_file_name_is_recorded(self, constant_file):
        assert {r.source_file_name for r in extract(constant_file)} == {SOURCE_FILE.file_name}

    def test_values_hold_exactly_the_value_columns(self, constant_file):
        for record in extract(constant_file):
            assert tuple(record.values) == VALUE_COLUMNS


class TestConversions:
    @pytest.fixture
    def values(self, constant_file):
        return records_by_key(extract(constant_file))[("s00001", 28)].values

    def test_temperature_kelvin_becomes_celsius(self, values):
        assert values["temperature_c"] == 15.0  # 288.15 K

    def test_pressures_become_hectopascals(self, values):
        assert values["surface_pressure_hpa"] == 1013.25  # 101325 Pa
        assert values["sea_level_pressure_hpa"] == 1018.0  # 101800 Pa

    def test_wind_speed_is_derived_from_the_components(self, values):
        assert (values["u_wind_ms"], values["v_wind_ms"]) == (3.0, 4.0)
        assert values["wind_speed_ms"] == 5.0

    def test_shortwave_radiation_becomes_megajoules_per_hour(self, values):
        assert values["shortwave_radiation_wm2"] == 1000.0
        assert values["solar_radiation_mjm2"] == 3.6  # 1000 W/m2 over one hour

    def test_unconverted_elements_pass_through(self, values):
        assert values["relative_humidity_pct"] == 60.0
        assert values["precipitation_mm"] == 1.5
        assert values["total_cloud_cover_pct"] == 80.0
        assert values["high_cloud_cover_pct"] == 30.0
        assert values["middle_cloud_cover_pct"] == 20.0
        assert values["low_cloud_cover_pct"] == 10.0

    def test_values_are_rounded_to_six_decimals(self, tmp_path):
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name,
            SOURCE_FILE,
            REFERENCE_AT,
            GRID,
            lambda key, lead, index: 288.1512344999 if key == "temperature_k" else 1.0,
        )
        values = records_by_key(extract(path))[("s00001", 28)].values
        assert values["temperature_c"] == 15.001234  # 15.0012344999 C
        assert values["wind_speed_ms"] == 1.414214  # sqrt(2), from u = v = 1.0


class TestGridPointSelection:
    def test_each_station_reads_its_own_grid_point(self, varying_file):
        by_key = records_by_key(extract(varying_file))
        for station_id, flat_index in FLAT_INDEX.items():
            assert by_key[(station_id, 29)].values["temperature_c"] == pytest.approx(
                varying_value("temperature_k", 29, flat_index) - 273.15, abs=1e-9
            )

    def test_each_lead_reads_its_own_message(self, varying_file):
        by_key = records_by_key(extract(varying_file))
        celsius = [by_key[("s00001", lead)].values["temperature_c"] for lead in range(28, 34)]
        assert celsius == sorted(celsius)
        assert len(set(celsius)) == 6

    def test_south_to_north_scan_is_read_with_a_positive_latitude_step(self, tmp_path):
        # jScansPositively = 1: the same physical points, first row at 35.0.
        grid = MsmGrid(
            ni=4,
            nj=3,
            first_latitude=35.0,
            first_longitude=139.0,
            latitude_step=0.5,
            longitude_step=0.5,
        )
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name, SOURCE_FILE, REFERENCE_AT, grid, varying_value
        )
        by_key = records_by_key(extract(path))
        north_west = by_key[("s00001", 28)]
        assert (north_west.grid_latitude, north_west.grid_longitude) == (36.0, 139.0)
        # (36.0, 139.0) is now the *last* row: j = 2, i = 0 -> flat index 8.
        assert north_west.values["temperature_c"] == pytest.approx(
            varying_value("temperature_k", 28, 8) - 273.15, abs=1e-9
        )

    def test_east_to_west_scan_is_read_with_a_negative_longitude_step(self, tmp_path):
        # iScansNegatively = 1: rows run 140.5 -> 139.0.
        grid = MsmGrid(
            ni=4,
            nj=3,
            first_latitude=36.0,
            first_longitude=140.5,
            latitude_step=-0.5,
            longitude_step=-0.5,
        )
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name, SOURCE_FILE, REFERENCE_AT, grid, varying_value
        )
        by_key = records_by_key(extract(path))
        north_west = by_key[("s00001", 28)]
        assert (north_west.grid_latitude, north_west.grid_longitude) == (36.0, 139.0)
        # (36.0, 139.0) is now the last column of the first row -> flat index 3.
        assert north_west.values["temperature_c"] == pytest.approx(
            varying_value("temperature_k", 28, 3) - 273.15, abs=1e-9
        )

    def test_station_outside_the_grid_raises(self, tmp_path, constant_file):
        far_away = MsmStation("s09999", 43.06, 141.33)  # Sapporo, off the toy grid
        with pytest.raises(MsmError, match="outside the MSM domain"):
            extract(constant_file, stations=(*STATIONS, far_away))


class TestMessageOrder:
    def test_shuffled_message_order_yields_identical_records(self, tmp_path, varying_file):
        shuffled = build_file(
            tmp_path / "shuffled.bin",
            reversed(day_messages(SOURCE_FILE, REFERENCE_AT, GRID, varying_value)),
        )
        # The file's message order is arbitrary; identification is by metadata.
        assert extract(shuffled) == extract(varying_file)


class TestMultiFieldEnvelopes:
    """JMA packs a whole archive member into one multi-field GRIB2 envelope.

    The real FH16-33 file is a single ``GRIB`` envelope holding 216 fields
    (12 elements x leads 16-33); a reader without ecCodes' multi-field support
    sees only the first field of an envelope and then skips to the next one.
    """

    @pytest.fixture
    def multi_field_file(self, tmp_path):
        # A distinct name: `varying_file` shares this tmp_path and would
        # otherwise overwrite the envelope with a plain concatenation.
        return build_multi_field_file(
            tmp_path / f"multi_{SOURCE_FILE.file_name}",
            day_messages(SOURCE_FILE, REFERENCE_AT, GRID, varying_value),
        )

    def test_the_fixture_really_is_a_single_envelope(self, multi_field_file):
        # Guards the test below from silently degrading into the concatenated case.
        assert multi_field_file.read_bytes().count(b"GRIB") == 1

    def test_every_field_of_an_envelope_is_decoded(self, multi_field_file, varying_file):
        # Same 72 fields as the concatenated fixture, same records.
        assert extract(multi_field_file) == extract(varying_file)

    def test_several_envelopes_in_one_file_are_all_walked(self, tmp_path, varying_file):
        messages = day_messages(SOURCE_FILE, REFERENCE_AT, GRID, varying_value)
        half = len(messages) // 2
        first = build_multi_field_file(tmp_path / "first.bin", messages[:half])
        second = build_multi_field_file(tmp_path / "second.bin", messages[half:])
        combined = build_file(tmp_path / "combined.bin", [first.read_bytes(), second.read_bytes()])
        assert combined.read_bytes().count(b"GRIB") == 2
        assert extract(combined) == extract(varying_file)


class TestStatisticalProducts:
    @pytest.fixture
    def encoded_steps(self, varying_file):
        return read_message_keys(
            varying_file,
            (
                "parameterCategory",
                "parameterNumber",
                "productDefinitionTemplateNumber",
                "startStep",
                "endStep",
            ),
        )

    @staticmethod
    def messages_of(encoded_steps, element):
        return [
            message
            for message in encoded_steps
            if (message["parameterCategory"], message["parameterNumber"])
            == (element.parameter_category, element.parameter_number)
        ]

    def test_precipitation_is_encoded_over_the_hour_ending_at_the_lead(self, encoded_steps):
        precipitation = next(e for e in MSM_SURFACE_ELEMENTS if e.key == "precipitation_mm")
        assert precipitation.statistical
        messages = self.messages_of(encoded_steps, precipitation)
        assert len(messages) == len(SOURCE_FILE.leads_used)
        assert {m["productDefinitionTemplateNumber"] for m in messages} == {8}
        assert {(m["startStep"], m["endStep"]) for m in messages} == {
            (lead - 1, lead) for lead in SOURCE_FILE.leads_used
        }

    def test_temperature_is_encoded_as_an_instantaneous_product(self, encoded_steps):
        temperature = next(e for e in MSM_SURFACE_ELEMENTS if e.key == "temperature_k")
        assert not temperature.statistical
        messages = self.messages_of(encoded_steps, temperature)
        assert {m["productDefinitionTemplateNumber"] for m in messages} == {0}
        assert {(m["startStep"], m["endStep"]) for m in messages} == {
            (lead, lead) for lead in SOURCE_FILE.leads_used
        }

    def test_interval_products_land_on_the_record_of_their_end_step(self, varying_file):
        # The hour (lead - 1, lead] belongs to the same record an instantaneous
        # value at `lead` does — both are read as the hour ending at that lead.
        by_key = records_by_key(extract(varying_file))
        for lead in SOURCE_FILE.leads_used:
            assert by_key[("s00001", lead)].values["precipitation_mm"] == pytest.approx(
                varying_value("precipitation_mm", lead, 0), abs=1e-9
            )
            assert by_key[("s00001", lead)].values["temperature_c"] == pytest.approx(
                varying_value("temperature_k", lead, 0) - 273.15, abs=1e-9
            )


class TestStepEncoding:
    """Parameter + endStep alone is not enough: template and interval are asserted."""

    def _file_with(self, tmp_path, element_key, **overrides):
        """A complete member where one (element, first lead) message is re-encoded."""
        element = next(e for e in MSM_SURFACE_ELEMENTS if e.key == element_key)
        lead = SOURCE_FILE.leads_used[0]
        messages = day_messages(
            SOURCE_FILE, REFERENCE_AT, GRID, constant_value, omit=[(element_key, lead)]
        )
        replaced = build_message(
            element,
            lead_hours=lead,
            reference_at=REFERENCE_AT,
            grid=GRID,
            values=[constant_value(element_key, lead, i) for i in range(GRID.ni * GRID.nj)],
            **overrides,
        )
        return build_file(tmp_path / "re_encoded.bin", [replaced, *messages])

    def test_cumulative_interval_on_a_statistical_element_is_rejected(self, tmp_path):
        # (0, 28] passes a parameter + endStep match but is not the hour (27, 28].
        path = self._file_with(tmp_path, "precipitation_mm", start_step=0)
        with pytest.raises(
            MsmExtractError,
            match=r"precipitation_mm \(lead 28\).*over steps 0-28.*expected template 8 over steps 27-28",
        ):
            extract(path)

    def test_statistical_element_encoded_instantaneously_is_rejected(self, tmp_path):
        path = self._file_with(tmp_path, "shortwave_radiation_wm2", statistical=False)
        with pytest.raises(
            MsmExtractError,
            match=r"shortwave_radiation_wm2 \(lead 28\).*productDefinitionTemplateNumber=0",
        ):
            extract(path)

    def test_instantaneous_element_encoded_as_an_interval_is_rejected(self, tmp_path):
        path = self._file_with(tmp_path, "temperature_k", statistical=True)
        with pytest.raises(
            MsmExtractError,
            match=r"temperature_k \(lead 28\).*productDefinitionTemplateNumber=8.*expected template 0 over steps 28-28",
        ):
            extract(path)

    def test_steps_coded_in_minutes_are_read_in_hours(self, tmp_path, constant_file):
        # The decoder pins stepUnits to hours before reading any step key, so a
        # message coding lead 28 as 1680 minutes decodes exactly like the hourly one.
        path = self._file_with(tmp_path, "temperature_k", step_unit=MINUTE_STEP_UNIT)
        assert extract(path) == extract(constant_file)


class TestSkippedMessages:
    def test_unmatched_parameter_triples_are_ignored(self, tmp_path, constant_file):
        unmatched = MsmElement("ozone_dummy", 0, 19, 0, 1, False)
        path = build_file(
            tmp_path / "with_extra.bin",
            [
                build_message(
                    unmatched,
                    lead_hours=28,
                    reference_at=REFERENCE_AT,
                    grid=GRID,
                    values=[1.0] * (GRID.ni * GRID.nj),
                ),
                *day_messages(SOURCE_FILE, REFERENCE_AT, GRID, constant_value),
            ],
        )
        assert extract(path) == extract(constant_file)

    def test_leads_outside_the_used_band_are_skipped(self, tmp_path):
        # The FH16-33 member physically holds leads 16-33; only 28-33 are used.
        unused_band = MsmSourceFile(SOURCE_FILE.file_name, SOURCE_FILE.url, range(16, 17))
        path = build_file(
            tmp_path / SOURCE_FILE.file_name,
            [
                *day_messages(unused_band, REFERENCE_AT, GRID, constant_value),
                *day_messages(SOURCE_FILE, REFERENCE_AT, GRID, constant_value),
            ],
        )
        records = extract(path)
        assert {r.forecast_lead_hours for r in records} == set(SOURCE_FILE.leads_used)
        assert 16 not in {r.forecast_lead_hours for r in records}
        assert len(records) == len(STATIONS) * len(SOURCE_FILE.leads_used)


# --------------------------------------------------------------------------- bitmap


class TestMissingValues:
    @pytest.fixture
    def bitmap_file(self, tmp_path):
        # s00001 (flat 0) loses temperature, its u component and radiation;
        # s00002 (flat 6) loses only its v component; s00003 (flat 11) is intact.
        return build_day_file(
            tmp_path / SOURCE_FILE.file_name,
            SOURCE_FILE,
            REFERENCE_AT,
            GRID,
            constant_value,
            missing={
                "temperature_k": [0],
                "u_wind_ms": [0],
                "shortwave_radiation_wm2": [0],
                "v_wind_ms": [6],
            },
        )

    def test_masked_grid_point_yields_none(self, bitmap_file):
        values = records_by_key(extract(bitmap_file))[("s00001", 30)].values
        assert values["temperature_c"] is None
        assert values["u_wind_ms"] is None
        assert values["shortwave_radiation_wm2"] is None

    def test_wind_speed_is_none_when_the_u_component_is_missing(self, bitmap_file):
        values = records_by_key(extract(bitmap_file))[("s00001", 30)].values
        assert values["v_wind_ms"] == 4.0
        assert values["wind_speed_ms"] is None

    def test_wind_speed_is_none_when_the_v_component_is_missing(self, bitmap_file):
        values = records_by_key(extract(bitmap_file))[("s00002", 30)].values
        assert values["u_wind_ms"] == 3.0
        assert values["wind_speed_ms"] is None

    def test_solar_radiation_is_none_when_the_flux_is_missing(self, bitmap_file):
        values = records_by_key(extract(bitmap_file))[("s00001", 30)].values
        assert values["solar_radiation_mjm2"] is None

    def test_unmasked_elements_of_a_masked_station_are_kept(self, bitmap_file):
        values = records_by_key(extract(bitmap_file))[("s00001", 30)].values
        assert values["relative_humidity_pct"] == 60.0
        assert values["surface_pressure_hpa"] == 1013.25

    def test_other_stations_are_unaffected(self, bitmap_file):
        values = records_by_key(extract(bitmap_file))[("s00003", 30)].values
        assert not [key for key, value in values.items() if value is None]
        assert values["temperature_c"] == 15.0
        assert values["wind_speed_ms"] == 5.0
        assert values["solar_radiation_mjm2"] == 3.6

    def test_every_record_still_carries_all_value_columns(self, bitmap_file):
        for record in extract(bitmap_file):
            assert tuple(record.values) == VALUE_COLUMNS


# --------------------------------------------------------------------------- rejections


class TestRejectedFiles:
    def test_non_operational_production_status_is_rejected(self, tmp_path):
        path = build_file(
            tmp_path / SOURCE_FILE.file_name,
            [
                build_message(
                    MSM_SURFACE_ELEMENTS[0],
                    lead_hours=28,
                    reference_at=REFERENCE_AT,
                    grid=GRID,
                    values=[1.0] * (GRID.ni * GRID.nj),
                    production_status=1,
                )
            ],
        )
        with pytest.raises(MsmExtractError, match="productionStatusOfProcessedData=1"):
            extract(path)

    def test_grib_edition_1_is_rejected(self, tmp_path):
        path = build_file(
            tmp_path / SOURCE_FILE.file_name,
            [build_edition1_message(reference_at=REFERENCE_AT, grid=GRID)],
        )
        with pytest.raises(MsmExtractError, match="editionNumber=1"):
            extract(path)

    def test_mislabeled_data_date_is_rejected(self, tmp_path):
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name,
            SOURCE_FILE,
            REFERENCE_AT - datetime.timedelta(days=1),
            GRID,
            constant_value,
        )
        with pytest.raises(MsmExtractError, match="20260816 1200"):
            extract(path)

    def test_mislabeled_data_time_is_rejected(self, tmp_path):
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name,
            SOURCE_FILE,
            REFERENCE_AT + datetime.timedelta(hours=6),
            GRID,
            constant_value,
        )
        with pytest.raises(MsmExtractError, match="20260817 1800"):
            extract(path)

    def test_unexpected_surface_type_is_rejected(self, tmp_path):
        temperature = next(e for e in MSM_SURFACE_ELEMENTS if e.key == "temperature_k")
        path = build_file(
            tmp_path / SOURCE_FILE.file_name,
            [
                build_message(
                    temperature,
                    lead_hours=28,
                    reference_at=REFERENCE_AT,
                    grid=GRID,
                    values=[288.15] * (GRID.ni * GRID.nj),
                    surface_type=1,
                )
            ],
        )
        with pytest.raises(MsmExtractError, match="temperature_k"):
            extract(path)

    def test_j_fastest_scan_order_is_rejected(self, tmp_path):
        path = build_file(
            tmp_path / SOURCE_FILE.file_name,
            [
                build_message(
                    MSM_SURFACE_ELEMENTS[0],
                    lead_hours=28,
                    reference_at=REFERENCE_AT,
                    grid=GRID,
                    values=[1.0] * (GRID.ni * GRID.nj),
                    j_points_are_consecutive=1,
                )
            ],
        )
        with pytest.raises(MsmExtractError, match="jPointsAreConsecutive"):
            extract(path)

    def test_a_missing_element_for_one_lead_is_a_completeness_failure(self, tmp_path):
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name,
            SOURCE_FILE,
            REFERENCE_AT,
            GRID,
            constant_value,
            omit=[("temperature_k", 30)],
        )
        with pytest.raises(MsmExtractError, match=r"\('temperature_k', 30\)"):
            extract(path)

    def test_a_file_without_messages_is_a_completeness_failure(self, tmp_path):
        path = build_file(tmp_path / SOURCE_FILE.file_name, [])
        with pytest.raises(MsmExtractError, match="72"):
            extract(path)

    def test_the_error_names_the_file(self, tmp_path):
        path = build_day_file(
            tmp_path / SOURCE_FILE.file_name,
            SOURCE_FILE,
            REFERENCE_AT,
            GRID,
            constant_value,
            omit=[("v_wind_ms", 33)],
        )
        with pytest.raises(MsmExtractError, match=SOURCE_FILE.file_name):
            extract(path)
