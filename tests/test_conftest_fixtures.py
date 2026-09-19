"""The fixture marts hold, under each column name, the value their row builders gave it."""

from __future__ import annotations

import pandas as pd

from tests.conftest import synthetic_calendar_counts, synthetic_is_business_day


def test_the_calendar_mart_holds_each_columns_own_values(spark, feature_marts):
    # Both columns are ints, so a swap between them passes every type check: until
    # 2026-09-19 ``is_business_day`` held the fiscal quarter and the reverse.
    rows = spark.table("pma_features.ftr_day_calendar").where("area_code = 'tokyo'").toPandas()
    assert len(rows) > 0
    for row in rows.to_dict("records"):
        day = pd.Timestamp(row["trade_date"])
        assert row["is_business_day"] == int(synthetic_is_business_day(day)), day
        assert row["fiscal_quarter"] == synthetic_calendar_counts(day)["fiscal_quarter"], day
