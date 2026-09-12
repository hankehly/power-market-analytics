"""fct_feature_value: the checked-in generated model run on the fixture marts.

The model's ``ref()`` calls are pointed at the local session's tables and the
SQL runs as dbt would compile it, so what is checked is the file in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

MODEL_PATH = (
    Path(__file__).resolve().parents[1] / "dbt" / "models" / "curated" / "fct_feature_value.sql"
)
REF = re.compile(r"\{\{\s*ref\('(\w+)'\)\s*\}\}")
CTE = re.compile(r"^  (ftr_\w+) as \(\n(.*?)\n  \),\n", re.S | re.M)
#: A stacked column: its literal name, the mart column it casts and the categorical literal.
STACK_ROW = re.compile(
    r"^\s+'(\w+)', cast\((?:cast\()?m\.(\w+) as (?:int\) as )?double\), (true|false)", re.M
)
KEY = ["area_code", "trade_date", "time_code", "feature_ref", "available_at"]
SIMILAR_DAY_MART = "ftr_period_similar_day"
COLUMNS = [
    "area_code",
    "trade_date",
    "time_code",
    "feature_view",
    "feature_name",
    "feature_ref",
    "feature_value",
    "is_categorical",
    "available_at",
    "published_at",
]


def model_sql(tables: dict[str, str] | None = None) -> str:
    """The model with every ``ref`` replaced by a table (``pma_features.<mart>`` unless given)."""
    text = MODEL_PATH.read_text()
    tables = {"dim_delivery_period": "pma_curated.dim_delivery_period", **(tables or {})}
    return REF.sub(lambda m: tables.get(m.group(1), f"pma_features.{m.group(1)}"), text)


def stacked_columns(text: str) -> dict[str, tuple[int, list[tuple[str, bool]]]]:
    """Per mart CTE: its period multiplier and ``[(column, categorical)]`` in stack order."""
    out: dict[str, tuple[int, list[tuple[str, bool]]]] = {}
    for match in CTE.finditer(text):
        name, body = match.group(1), match.group(2)
        multiplier = 48 if "cross join periods" in body else 2 if "join periods p on" in body else 1
        columns = [(col, flag == "true") for literal, col, flag in STACK_ROW.findall(body)]
        assert [literal for literal, _, _ in STACK_ROW.findall(body)] == [c for c, _ in columns]
        out[name] = (multiplier, columns)
    return out


@pytest.fixture
def fact(spark: SparkSession, feature_marts) -> DataFrame:
    """The model over the fixture marts, left unevaluated.

    It is 650k rows, 203 MB collected. Pulling it whole into the driver needs a
    task-result block the test session's 1 GB heap has no room for once the rest
    of the suite is resident, which is how it ran a CI runner out of heap. The
    tests below count and filter in Spark and collect only what they assert on.
    """
    return spark.sql(model_sql())


class TestFeatureValueFact:
    def test_every_tagged_cell_appears_once_at_its_periods(self, spark, fact):
        marts = stacked_columns(MODEL_PATH.read_text())
        counts = fact.groupBy("feature_view", "feature_name").count().toPandas()
        assert (
            set(counts["feature_view"])
            == set(marts)
            == {
                "ftr_day_calendar",
                "ftr_day_occto",
                "ftr_hour_jma_obs",
                "ftr_hour_msm",
                "ftr_period_actuals",
                "ftr_period_jepx",
                SIMILAR_DAY_MART,
            }
        )
        for mart, (multiplier, columns) in marts.items():
            rows = spark.table(f"pma_features.{mart}").toPandas()
            per_column = counts[counts["feature_view"] == mart].set_index("feature_name")["count"]
            assert per_column.to_dict() == {col: len(rows) * multiplier for col, _ in columns}, mart
            # The mart's first row, at every period it covers.
            row = rows.iloc[0]
            if multiplier == 48:
                periods = list(range(1, 49))
            elif multiplier == 2:
                periods = [2 * int(row["hour_ending"]) - 1, 2 * int(row["hour_ending"])]
            else:
                periods = [int(row["time_code"])]
            day = fact.filter(
                (F.col("feature_view") == mart)
                & (F.col("trade_date") == str(pd.Timestamp(row["trade_date"]).date()))
                & F.col("time_code").isin(periods)
            ).toPandas()
            for col, categorical in columns:
                got = day[
                    (day["area_code"] == row["area_code"])
                    & (day["feature_name"] == col)
                    & (day["available_at"] == row["available_at"])
                ].set_index("time_code")
                assert sorted(got.index) == periods, (mart, col)
                expected = None if pd.isna(row[col]) else float(row[col])
                for time_code in periods:
                    value = got.loc[time_code, "feature_value"]
                    assert (expected is None and pd.isna(value)) or value == expected, (mart, col)
                    assert got.loc[time_code, "feature_ref"] == f"{mart}:{col}"
                    assert bool(got.loc[time_code, "is_categorical"]) is categorical

    def test_the_key_is_unique_and_the_flags_come_from_the_tags(self, fact):
        assert fact.groupBy(*KEY).count().where("count > 1").count() == 0
        categorical = fact.where("is_categorical").select("feature_ref").distinct().collect()
        assert {r["feature_ref"] for r in categorical} == {"ftr_day_calendar:day_type"}
        assert fact.columns == COLUMNS
        views = (
            fact.select("feature_view", F.col("published_at").isNotNull().alias("published"))
            .distinct()
            .toPandas()
        )
        assert set(views.loc[views["published"], "feature_view"]) == {SIMILAR_DAY_MART}
        assert (views.loc[~views["published"], "feature_view"] != SIMILAR_DAY_MART).all()

    def test_a_mart_with_two_vintages_keeps_the_newest_published(self, spark, feature_marts):
        # A second, older run of the similar day with doubled values: the fact keeps
        # one row per key and available_at, the newest published one.
        mart = spark.table(f"pma_features.{SIMILAR_DAY_MART}")
        older = (
            mart.withColumn("similar_day_run_id", F.lit("older-run"))
            .withColumn("similar_day_demand_kwh", F.col("similar_day_demand_kwh") * 2)
            .withColumn("published_at", F.col("published_at") - F.expr("interval 1 day"))
        )
        mart.unionByName(older).createOrReplaceTempView("similar_day_two_runs")
        fact = spark.sql(model_sql({SIMILAR_DAY_MART: "similar_day_two_runs"}))
        subset = fact.where(F.col("feature_view") == SIMILAR_DAY_MART).toPandas()
        assert len(subset) == mart.count()
        expected = mart.toPandas()
        merged = subset.merge(
            expected,
            on=["area_code", "trade_date", "time_code", "available_at", "published_at"],
            how="inner",
            validate="one_to_one",
        )
        assert len(merged) == len(subset)
        assert (merged["feature_value"] == merged["similar_day_demand_kwh"]).all()
