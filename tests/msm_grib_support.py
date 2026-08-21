"""Deterministic GRIB2 fixtures for the MSM decode tests (plain helpers, not fixtures).

Every message is encoded by ecCodes itself from the ``regular_ll_sfc_grib2``
sample, so the decode tests exercise the real key names and the real GRIB2
byte layout rather than a hand-rolled stand-in. Three encoding choices make
the fixtures deterministic and cheap:

* ``packingType = "grid_ieee"`` with ``precision = 2`` (64-bit floats) —
  values round-trip bit-exactly, so a test can assert ``15.0`` and not an
  approximation of it (the sample's default ``grid_simple`` packing is lossy).
* The grid is tiny (a handful of points); its geometry comes from an
  :class:`~power_market_analytics.msm.MsmGrid` whose *signed* steps decide the
  scan flags, so a north-to-south or east-to-west fixture is just a grid with
  a negative step.
* A GRIB file is a plain concatenation of messages, so :func:`build_file`
  writes ``b"".join(messages)`` — message order in the file is whatever the
  caller passes.

No variant the decode layer must handle had to be dropped: the sample template
accepts ``jScansPositively``, ``iScansNegatively``, ``jPointsAreConsecutive``,
a non-operational ``productionStatusOfProcessedData`` and the statistical
product-definition template 8 (set *before* ``startStep``/``endStep``, which
the template-0 sample does not carry). GRIB1 (for the ``editionNumber`` check)
comes from the matching ``regular_ll_sfc_grib1`` sample instead of an in-place
edition conversion.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import eccodes

from power_market_analytics.msm import MSM_SURFACE_ELEMENTS, MsmElement, MsmGrid, MsmSourceFile

#: ecCodes sample the GRIB2 fixtures are built from.
GRIB2_SAMPLE = "regular_ll_sfc_grib2"
#: ecCodes sample used for the ``editionNumber`` rejection fixture.
GRIB1_SAMPLE = "regular_ll_sfc_grib1"
#: Step unit code for hours (``stepUnits``), the unit MSM forecast hours use.
HOUR_STEP_UNIT = 1
#: Sentinel written at ``missing_indices``; also ecCodes' default decode-side
#: ``missingValue``, which is what the decoder compares against.
MISSING_VALUE = 9999.0

#: One representative value per element, in the element's own GRIB unit.
ELEMENT_BASE_VALUES: dict[str, float] = {
    "surface_pressure_pa": 101325.0,
    "sea_level_pressure_pa": 101800.0,
    "u_wind_ms": 3.0,
    "v_wind_ms": 4.0,
    "temperature_k": 288.15,
    "relative_humidity_pct": 60.0,
    "precipitation_mm": 1.5,
    "shortwave_radiation_wm2": 1000.0,
    "total_cloud_cover_pct": 80.0,
    "low_cloud_cover_pct": 10.0,
    "middle_cloud_cover_pct": 20.0,
    "high_cloud_cover_pct": 30.0,
}


def constant_value(element_key: str, lead_hours: int, flat_index: int) -> float:
    """Return the element's base value everywhere (a ``value_for`` callable)."""
    return ELEMENT_BASE_VALUES[element_key]


def varying_value(element_key: str, lead_hours: int, flat_index: int) -> float:
    """Return a value unique per (element, lead, grid point) (a ``value_for`` callable).

    The offsets (whole hours, eighths of a unit) are exactly representable in
    binary floating point, so a test recomputing this expression gets the very
    same double the fixture encoded.
    """
    return ELEMENT_BASE_VALUES[element_key] + lead_hours + flat_index / 8


def build_message(
    element: MsmElement,
    *,
    lead_hours: int,
    reference_at: datetime.datetime,
    grid: MsmGrid,
    values: Sequence[float],
    production_status: int = 0,
    missing_indices: Sequence[int] = (),
    surface_type: int | None = None,
    j_points_are_consecutive: int = 0,
) -> bytes:
    """Encode one GRIB2 message the decode layer should recognise.

    Parameters
    ----------
    element : MsmElement
        Supplies the parameter triple, the surface type and whether the
        message is a statistical (interval) product. A made-up element is how
        a test writes a message the decoder must ignore.
    lead_hours : int
        Forecast hour the message is valid at (its ``endStep``). Statistical
        elements are encoded over the interval ``(lead_hours - 1, lead_hours]``.
    reference_at : datetime.datetime
        Run reference time; encoded as ``dataDate``/``dataTime``.
    grid : MsmGrid
        Grid geometry. The *signs* of ``latitude_step``/``longitude_step``
        become ``jScansPositively``/``iScansNegatively``.
    values : sequence of float
        Exactly ``grid.ni * grid.nj`` values in scan order (i fastest).
    production_status : int, optional
        ``productionStatusOfProcessedData`` (0 = operational).
    missing_indices : sequence of int, optional
        Flat indices encoded as bitmap holes (``bitmapPresent = 1``).
    surface_type : int, optional
        Overrides ``element.surface_type`` (to build a mismatching message).
    j_points_are_consecutive : int, optional
        ``jPointsAreConsecutive`` (1 builds a j-fastest message).

    Returns
    -------
    bytes
        The encoded message.

    Raises
    ------
    ValueError
        If ``values`` does not hold exactly ``grid.ni * grid.nj`` entries.
    """
    if len(values) != grid.ni * grid.nj:
        raise ValueError(f"expected {grid.ni * grid.nj} values, got {len(values)}")
    msg = eccodes.codes_grib_new_from_samples(GRIB2_SAMPLE)
    try:
        eccodes.codes_set(msg, "discipline", element.discipline)
        eccodes.codes_set(msg, "parameterCategory", element.parameter_category)
        eccodes.codes_set(msg, "parameterNumber", element.parameter_number)
        if element.statistical:
            # Template 8 (statistical over an interval) must be selected before
            # the step keys exist on the message.
            eccodes.codes_set(msg, "productDefinitionTemplateNumber", 8)
        eccodes.codes_set(
            msg,
            "typeOfFirstFixedSurface",
            element.surface_type if surface_type is None else surface_type,
        )
        eccodes.codes_set(msg, "productionStatusOfProcessedData", production_status)
        eccodes.codes_set(msg, "dataDate", int(reference_at.strftime("%Y%m%d")))
        eccodes.codes_set(msg, "dataTime", reference_at.hour * 100 + reference_at.minute)
        eccodes.codes_set(msg, "stepUnits", HOUR_STEP_UNIT)
        if element.statistical:
            eccodes.codes_set(msg, "startStep", lead_hours - 1)
            eccodes.codes_set(msg, "endStep", lead_hours)
        else:
            eccodes.codes_set(msg, "forecastTime", lead_hours)
        _set_grid(msg, grid, j_points_are_consecutive)
        eccodes.codes_set(msg, "packingType", "grid_ieee")
        eccodes.codes_set(msg, "precision", 2)
        encoded = list(values)
        if missing_indices:
            eccodes.codes_set(msg, "missingValue", MISSING_VALUE)
            eccodes.codes_set(msg, "bitmapPresent", 1)
            for index in missing_indices:
                encoded[index] = MISSING_VALUE
        eccodes.codes_set_values(msg, encoded)
        return bytes(eccodes.codes_get_message(msg))
    finally:
        eccodes.codes_release(msg)


def build_edition1_message(*, reference_at: datetime.datetime, grid: MsmGrid) -> bytes:
    """Encode a minimal GRIB **1** message (for the ``editionNumber`` check).

    Parameters
    ----------
    reference_at : datetime.datetime
        Run reference time (``dataDate``/``dataTime``).
    grid : MsmGrid
        Grid geometry; only its extent is encoded.

    Returns
    -------
    bytes
        The encoded GRIB1 message.
    """
    msg = eccodes.codes_grib_new_from_samples(GRIB1_SAMPLE)
    try:
        eccodes.codes_set(msg, "dataDate", int(reference_at.strftime("%Y%m%d")))
        eccodes.codes_set(msg, "dataTime", reference_at.hour * 100 + reference_at.minute)
        _set_grid(msg, grid, 0)
        eccodes.codes_set_values(msg, [0.0] * (grid.ni * grid.nj))
        return bytes(eccodes.codes_get_message(msg))
    finally:
        eccodes.codes_release(msg)


def _set_grid(msg: int, grid: MsmGrid, j_points_are_consecutive: int) -> None:
    """Write ``grid`` (signed steps -> scan flags) onto an open ecCodes message."""
    eccodes.codes_set(msg, "Ni", grid.ni)
    eccodes.codes_set(msg, "Nj", grid.nj)
    eccodes.codes_set(msg, "iScansNegatively", 1 if grid.longitude_step < 0 else 0)
    eccodes.codes_set(msg, "jScansPositively", 1 if grid.latitude_step > 0 else 0)
    eccodes.codes_set(msg, "jPointsAreConsecutive", j_points_are_consecutive)
    eccodes.codes_set(msg, "latitudeOfFirstGridPointInDegrees", grid.first_latitude)
    eccodes.codes_set(msg, "longitudeOfFirstGridPointInDegrees", grid.first_longitude)
    eccodes.codes_set(
        msg,
        "latitudeOfLastGridPointInDegrees",
        grid.first_latitude + (grid.nj - 1) * grid.latitude_step,
    )
    eccodes.codes_set(
        msg,
        "longitudeOfLastGridPointInDegrees",
        grid.first_longitude + (grid.ni - 1) * grid.longitude_step,
    )
    eccodes.codes_set(msg, "iDirectionIncrementInDegrees", abs(grid.longitude_step))
    eccodes.codes_set(msg, "jDirectionIncrementInDegrees", abs(grid.latitude_step))


def build_file(path: Path, messages: Iterable[bytes]) -> Path:
    """Write messages to ``path`` as one GRIB file (a plain concatenation).

    Parameters
    ----------
    path : pathlib.Path
        Destination; parent directories are created.
    messages : iterable of bytes
        Encoded messages, written in iteration order.

    Returns
    -------
    pathlib.Path
        ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(messages))
    return path


def read_message_keys(path: Path, keys: Sequence[str]) -> list[dict[str, int]]:
    """Read integer keys from every message of a GRIB file (to assert a fixture's shape).

    Parameters
    ----------
    path : pathlib.Path
        GRIB file to read.
    keys : sequence of str
        ecCodes key names, read as integers.

    Returns
    -------
    list of dict
        One mapping per message, in file order.
    """
    read: list[dict[str, int]] = []
    with open(path, "rb") as f:
        while (msg := eccodes.codes_grib_new_from_file(f)) is not None:
            try:
                read.append({key: eccodes.codes_get(msg, key, int) for key in keys})
            finally:
                eccodes.codes_release(msg)
    return read


def day_messages(
    source_file: MsmSourceFile,
    reference_at: datetime.datetime,
    grid: MsmGrid,
    value_for: Callable[[str, int, int], float],
    *,
    omit: Sequence[tuple[str, int]] = (),
    missing: Mapping[str, Sequence[int]] | None = None,
) -> list[bytes]:
    """Encode every (element, lead) message a complete archive member holds.

    Parameters
    ----------
    source_file : MsmSourceFile
        Supplies ``leads_used``.
    reference_at : datetime.datetime
        Run reference time.
    grid : MsmGrid
        Grid geometry of every message.
    value_for : callable
        ``value_for(element_key, lead_hours, flat_index) -> float``.
    omit : sequence of (str, int), optional
        ``(element_key, lead_hours)`` pairs to leave out (an incomplete file).
    missing : mapping of str to sequence of int, optional
        Per element key, the flat indices encoded as bitmap holes.

    Returns
    -------
    list of bytes
        Messages ordered lead-major, then by :data:`MSM_SURFACE_ELEMENTS`.
    """
    omitted = set(omit)
    missing_indices = dict(missing or {})
    return [
        build_message(
            element,
            lead_hours=lead_hours,
            reference_at=reference_at,
            grid=grid,
            values=[value_for(element.key, lead_hours, i) for i in range(grid.ni * grid.nj)],
            missing_indices=missing_indices.get(element.key, ()),
        )
        for lead_hours in source_file.leads_used
        for element in MSM_SURFACE_ELEMENTS
        if (element.key, lead_hours) not in omitted
    ]


def build_day_file(
    path: Path,
    source_file: MsmSourceFile,
    reference_at: datetime.datetime,
    grid: MsmGrid,
    value_for: Callable[[str, int, int], float],
    *,
    omit: Sequence[tuple[str, int]] = (),
    missing: Mapping[str, Sequence[int]] | None = None,
) -> Path:
    """Write a complete archive member (:func:`day_messages` into :func:`build_file`)."""
    return build_file(
        path,
        day_messages(source_file, reference_at, grid, value_for, omit=omit, missing=missing),
    )
