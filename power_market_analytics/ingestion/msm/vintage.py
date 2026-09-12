"""Which MSM run covers a delivery day, and where its hours land.

The pipeline ingests a single vintage per delivery day D: the 12 UTC run of D-2,
the latest run whose forecast horizon (FH51) still reaches every hour of D and
which is safely published before the demand model's 09:30 JST D-1 cutoff. Using
a later run would leak information the model could not have seen, so this
arithmetic is the leakage boundary.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

#: Root of the RISH mirror: one directory per issue date.
BASE_URL = "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original"

#: 12 UTC runs only reach forecast hour 51 (needed for the full delivery day)
#: from this date; earlier archive members are incomplete for this pipeline.
EARLIEST_DELIVERY_DATE = datetime.date(2019, 4, 1)
#: Default start of a full historical backfill (matches the other refresh tasks).
DEFAULT_BACKFILL_START = datetime.date(2022, 4, 1)
#: Japan Standard Time: a fixed UTC+9 offset, no daylight saving.
JST = datetime.timezone(datetime.timedelta(hours=9))


def _now() -> datetime.datetime:
    """Return the current instant in JST.

    A seam so :func:`default_end_date` is testable: tests monkeypatch this
    function to freeze "now" rather than depending on the real clock.

    Returns
    -------
    datetime.datetime
        Timezone-aware JST.
    """
    return datetime.datetime.now(JST)


def default_end_date() -> datetime.date:
    """Return the default upper bound of an MSM download range.

    Returns
    -------
    datetime.date
        JST "today" (:func:`_now`) plus one day — the default
        ``--end-date`` of ``scripts/download_jma_msm_surface_forecast.py``.
    """
    return _now().date() + datetime.timedelta(days=1)


def reference_at_for(delivery_date: datetime.date) -> datetime.datetime:
    """Return the ingested forecast run's issue time for a delivery day.

    Parameters
    ----------
    delivery_date : datetime.date
        Day D whose 24 hourly forecasts are wanted.

    Returns
    -------
    datetime.datetime
        Timezone-aware UTC: 12:00 UTC on D-2 (the 21:00 JST D-2 run).
    """
    reference_date = delivery_date - datetime.timedelta(days=2)
    return datetime.datetime(
        reference_date.year,
        reference_date.month,
        reference_date.day,
        12,
        0,
        tzinfo=datetime.timezone.utc,
    )


def issue_cutoff_for(delivery_date: datetime.date) -> datetime.datetime:
    """Return the demand-forecast model's leakage cutoff for a delivery day.

    Parameters
    ----------
    delivery_date : datetime.date
        Day D being forecast.

    Returns
    -------
    datetime.datetime
        Timezone-aware JST: 09:30 JST on D-1 — the same cutoff the demand
        task issues its forecast at, so any MSM run used as a feature must be
        available (:func:`reference_at_for`) strictly before this instant.
    """
    cutoff_date = delivery_date - datetime.timedelta(days=1)
    return datetime.datetime(
        cutoff_date.year, cutoff_date.month, cutoff_date.day, 9, 30, tzinfo=JST
    )


@dataclass(frozen=True)
class MsmSourceFile:
    """One RISH GRIB2 archive member covering a band of forecast hours.

    Attributes
    ----------
    file_name : str
        Archive member name, e.g.
        ``"Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin"``.
    url : str
        Full download URL (``BASE_URL`` + the reference date's ``YYYY/MM/DD``
        directory + ``file_name``).
    leads_used : range
        Forecast-hour leads this pipeline reads from the file (a subset of
        the leads the file physically contains — the FH16-33 file's leads
        16-27 are never used).
    """

    file_name: str
    url: str
    leads_used: range


#: (forecast-hour band label, leads actually used from that file), in the
#: order the files must be processed.
_SOURCE_FILE_BANDS: tuple[tuple[str, range], ...] = (
    ("FH16-33", range(28, 34)),
    ("FH34-39", range(34, 40)),
    ("FH40-51", range(40, 52)),
)


def source_files_for(
    delivery_date: datetime.date,
) -> tuple[MsmSourceFile, MsmSourceFile, MsmSourceFile]:
    """Return the three GRIB2 files that cover a delivery day's 24 hours.

    Parameters
    ----------
    delivery_date : datetime.date
        Day D whose forecasts are wanted.

    Returns
    -------
    tuple of MsmSourceFile
        Exactly three, in processing order: FH16-33 (leads 28-33), FH34-39
        (leads 34-39), FH40-51 (leads 40-51).
    """
    reference_at = reference_at_for(delivery_date)
    reference_stamp = reference_at.strftime("%Y%m%d")
    directory = reference_at.strftime("%Y/%m/%d")

    def _file(band: str, leads_used: range) -> MsmSourceFile:
        file_name = f"Z__C_RJTD_{reference_stamp}120000_MSM_GPV_Rjp_Lsurf_{band}_grib2.bin"
        return MsmSourceFile(
            file_name=file_name, url=f"{BASE_URL}/{directory}/{file_name}", leads_used=leads_used
        )

    fh16_33, fh34_39, fh40_51 = _SOURCE_FILE_BANDS
    return _file(*fh16_33), _file(*fh34_39), _file(*fh40_51)


def valid_at_for(delivery_date: datetime.date, lead_hours: int) -> datetime.datetime:
    """Return the UTC instant a forecast lead represents.

    Parameters
    ----------
    delivery_date : datetime.date
        Delivery day D the lead belongs to.
    lead_hours : int
        Forecast lead in hours from the run's reference time (28-51 for the
        leads this pipeline uses).

    Returns
    -------
    datetime.datetime
        Timezone-aware UTC: ``reference_at_for(delivery_date) + lead_hours``.
    """
    return reference_at_for(delivery_date) + datetime.timedelta(hours=lead_hours)


def hour_ending_for(lead_hours: int) -> int:
    """Return the JST hour-ending (1-24) a forecast lead represents.

    Parameters
    ----------
    lead_hours : int
        Forecast lead in hours (28-51).

    Returns
    -------
    int
        1-24 (lead 28 -> 1, lead 51 -> 24).

    Raises
    ------
    ValueError
        If ``lead_hours`` is outside 28-51.
    """
    if not 28 <= lead_hours <= 51:
        raise ValueError(f"lead_hours must be 28..51 (got {lead_hours})")
    return lead_hours - 27


def time_codes_for(hour_ending: int) -> tuple[int, int]:
    """Return the pair of JEPX 30-minute time codes an hour-ending covers.

    Parameters
    ----------
    hour_ending : int
        JST hour-ending, 1-24.

    Returns
    -------
    tuple of int
        ``(2 * hour_ending - 1, 2 * hour_ending)``; downstream reverses this
        with ``hour_ending = (time_code + 1) // 2``.

    Raises
    ------
    ValueError
        If ``hour_ending`` is outside 1-24.
    """
    if not 1 <= hour_ending <= 24:
        raise ValueError(f"hour_ending must be 1..24 (got {hour_ending})")
    return (2 * hour_ending - 1, 2 * hour_ending)
