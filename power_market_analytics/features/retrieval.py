"""Build the prediction rows of a task and retrieve their features as of the issue time.

Time zones. Warehouse timestamps are naive wall-clock JST values written under a
UTC Spark session, and Feast's Spark offline store renders the entity
timestamps as UTC string literals in the SQL it generates (``WHERE
available_at <= '2025-03-09T00:30:00'``) while comparing rows as instants, so it
is only consistent when the session runs in UTC. The entity frame therefore
stamps the naive JST issue time as UTC, and retrieval refuses any other session
zone rather than silently shifting the cutoff by nine hours.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import numpy as np
import pandas as pd
from feast import FeatureStore
from pyspark.sql import SparkSession

from power_market_analytics.features.store import session_time_zone
from power_market_analytics.forecasting.frames import N_PERIODS

EVENT_TIMESTAMP_COL = "event_timestamp"
#: The zone the entity frame is stamped in: the warehouse's wall-clock convention.
ENTITY_TIME_ZONE = "UTC"
#: The columns Feast reads off an entity frame: every join key plus the timestamp.
ENTITY_COLS: list[str] = [
    "area_code",
    "trade_date_key",
    "hour_ending",
    "time_code",
    EVENT_TIMESTAMP_COL,
]


def is_utc(time_zone: str) -> bool:
    """Whether a zone name never departs from UTC (``UTC``, ``Etc/UTC``, ``GMT``, ...).

    Parameters
    ----------
    time_zone : str

    Returns
    -------
    bool
    """
    return all(
        pd.Timestamp(day, tz=time_zone).utcoffset() == timedelta(0)
        for day in ("2000-01-01", "2000-07-01")
    )


def entity_frame(
    area_code: str, days: pd.DatetimeIndex, issue_offset: pd.Timedelta
) -> pd.DataFrame:
    """One row per delivery period of the given days, stamped with its issue time.

    Parameters
    ----------
    area_code : str
        dim_area.area_code of the area being forecast.
    days : pandas.DatetimeIndex
        Delivery days D (midnight timestamps).
    issue_offset : pandas.Timedelta
        The task's issue time relative to D 00:00, e.g. ``TaskSpec.issue_offset``.

    Returns
    -------
    pandas.DataFrame
        ``area_code``, ``trade_date``, ``time_code`` (1..48), ``trade_date_key``
        (int yyyymmdd), ``hour_ending`` and ``event_timestamp``: the naive JST
        issue time stamped as UTC (``ENTITY_TIME_ZONE``).
    """
    days = pd.DatetimeIndex(days)
    trade_date = pd.DatetimeIndex(np.repeat(days.to_numpy(), N_PERIODS))
    time_code = np.tile(np.arange(1, N_PERIODS + 1, dtype="int64"), len(days))
    return pd.DataFrame(
        {
            "area_code": area_code,
            "trade_date": trade_date,
            "time_code": time_code,
            "trade_date_key": np.asarray(
                trade_date.year * 10000 + trade_date.month * 100 + trade_date.day, dtype="int64"
            ),
            # The observation hour containing the period's start (fct_jma_weather_hourly's alignment).
            "hour_ending": (time_code + 1) // 2,
            EVENT_TIMESTAMP_COL: (trade_date + issue_offset).tz_localize(ENTITY_TIME_ZONE),
        }
    )


def historical_features(
    store: FeatureStore, entity_df: pd.DataFrame, features: Sequence[str]
) -> pd.DataFrame:
    """The features of every entity row as of its ``event_timestamp``.

    Parameters
    ----------
    store : feast.FeatureStore
        An open store (:func:`~power_market_analytics.features.store.open_store`).
    entity_df : pandas.DataFrame
        Rows with ``ENTITY_COLS`` (other columns pass through), unique on them,
        ``event_timestamp`` stamped as UTC like :func:`entity_frame` does.
    features : sequence of str
        Feature references, ``<view>:<column>``.

    Returns
    -------
    pandas.DataFrame
        ``entity_df`` plus one column per feature, in ``entity_df``'s row
        order; ``event_timestamp`` naive again.

    Raises
    ------
    RuntimeError
        If no SparkSession is active (the Spark offline store would start a
        bare one without the warehouse), or the active session's zone is not
        UTC.
    ValueError
        If ``entity_df`` has duplicate rows on ``ENTITY_COLS``, or its
        ``event_timestamp`` is not stamped as UTC.
    """
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("no active SparkSession: start the project's session before retrieving")
    zone = session_time_zone(spark)
    if not is_utc(zone):
        raise RuntimeError(
            f"spark.sql.session.timeZone is {zone!r}: Feast's Spark store needs a UTC session"
        )
    stamped = entity_df[EVENT_TIMESTAMP_COL].dt.tz
    if stamped is None or not is_utc(str(stamped)):
        raise ValueError(f"event_timestamp must be stamped as UTC, got {stamped!r}")
    if entity_df.duplicated(subset=ENTITY_COLS).any():
        raise ValueError("entity_df must be unique on " + ", ".join(ENTITY_COLS))
    got = store.get_historical_features(
        entity_df=entity_df[ENTITY_COLS], features=list(features)
    ).to_df()
    if got[EVENT_TIMESTAMP_COL].dt.tz is not None:
        got[EVENT_TIMESTAMP_COL] = got[EVENT_TIMESTAMP_COL].dt.tz_convert(ENTITY_TIME_ZONE)
    got[EVENT_TIMESTAMP_COL] = got[EVENT_TIMESTAMP_COL].dt.tz_localize(None)
    naive = entity_df.assign(
        **{EVENT_TIMESTAMP_COL: entity_df[EVENT_TIMESTAMP_COL].dt.tz_localize(None)}
    )
    return naive.merge(got, how="left", on=ENTITY_COLS, validate="one_to_one")
