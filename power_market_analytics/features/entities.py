"""Feast entities: the join keys of the feature marts, one per key column.

A feature view joins on the entities of its mart's grain: day = area and day,
hour = those plus ``hour_ending``, period = those plus ``time_code``. The day
is an int ``yyyymmdd`` because Feast has no date value type; the marts' source
queries derive it from ``trade_date``.
"""

from __future__ import annotations

from feast import Entity, ValueType

AREA_CODE = Entity(
    name="area_code",
    join_keys=["area_code"],
    value_type=ValueType.STRING,
    description="dim_area.area_code of the bidding zone.",
)
TRADE_DATE_KEY = Entity(
    name="trade_date_key",
    join_keys=["trade_date_key"],
    value_type=ValueType.INT64,
    description="The delivery day as an int yyyymmdd.",
)
HOUR_ENDING = Entity(
    name="hour_ending",
    join_keys=["hour_ending"],
    value_type=ValueType.INT64,
    description="Hour ending 1-24 of an hour-grain feature; a period's hour is (time_code + 1) div 2.",
)
TIME_CODE = Entity(
    name="time_code",
    join_keys=["time_code"],
    value_type=ValueType.INT64,
    description="JEPX time code 1-48 of a period-grain feature.",
)

ENTITIES: tuple[Entity, ...] = (AREA_CODE, TRADE_DATE_KEY, HOUR_ENDING, TIME_CODE)

#: The entities of each mart grain, in join-key order.
GRAIN_ENTITIES: dict[str, tuple[Entity, ...]] = {
    "day": (AREA_CODE, TRADE_DATE_KEY),
    "hour": (AREA_CODE, TRADE_DATE_KEY, HOUR_ENDING),
    "period": (AREA_CODE, TRADE_DATE_KEY, TIME_CODE),
}
