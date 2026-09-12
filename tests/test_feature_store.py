"""The Feast store: opening it applies the catalogue's definitions."""

from __future__ import annotations

from feast import FeatureView, Field
from feast.infra.offline_stores.contrib.spark_offline_store.spark_source import SparkSource
from feast.types import Float64

from power_market_analytics.features.entities import ENTITIES, GRAIN_ENTITIES
from power_market_analytics.features.store import open_store, session_time_zone
from tests.support import write_feature_store_yaml

MART_NAMES = [
    "ftr_day_actuals",
    "ftr_day_calendar",
    "ftr_day_occto",
    "ftr_hour_jma_obs",
    "ftr_hour_msm",
    "ftr_period_actuals",
    "ftr_period_jepx",
    "ftr_period_similar_day",
]


def day_view(name: str = "ftr_test_day") -> FeatureView:
    return FeatureView(
        name=name,
        entities=list(GRAIN_ENTITIES["day"]),
        schema=[Field(name="x", dtype=Float64)],
        source=SparkSource(name=name, table=name, timestamp_field="available_at"),
        online=False,
    )


class TestOpenStore:
    def test_applies_the_given_definitions_and_creates_the_registry_directory(self, tmp_path):
        repo = write_feature_store_yaml(tmp_path, registry="nested/registry.db")
        store = open_store(repo, definitions=[*ENTITIES, day_view()])
        assert [view.name for view in store.list_feature_views()] == ["ftr_test_day"]
        assert {entity.name for entity in store.list_entities()} >= {e.name for e in ENTITIES}
        assert (tmp_path / "nested" / "registry.db").exists()

    def test_applies_the_package_views_by_default(self, tmp_path):
        store = open_store(write_feature_store_yaml(tmp_path))
        assert sorted(view.name for view in store.list_feature_views()) == MART_NAMES

    def test_a_definition_dropped_from_the_package_leaves_the_registry(self, tmp_path):
        repo = write_feature_store_yaml(tmp_path)
        open_store(repo, definitions=[*ENTITIES, day_view("ftr_old"), day_view("ftr_kept")])
        store = open_store(repo, definitions=[*ENTITIES, day_view("ftr_kept")])
        assert [view.name for view in store.list_feature_views()] == ["ftr_kept"]
        assert [source.name for source in store.list_data_sources()] == ["ftr_kept"]

    def test_an_entity_dropped_from_the_package_leaves_the_registry(self, tmp_path):
        repo = write_feature_store_yaml(tmp_path)
        open_store(repo, definitions=[*ENTITIES, day_view()])
        store = open_store(repo, definitions=[*GRAIN_ENTITIES["day"], day_view()])
        assert {entity.name for entity in store.list_entities()} == {"area_code", "trade_date_key"}

    def test_applying_twice_is_idempotent(self, tmp_path):
        repo = write_feature_store_yaml(tmp_path)
        open_store(repo, definitions=[*ENTITIES, day_view()])
        store = open_store(repo, definitions=[*ENTITIES, day_view()])
        assert [view.name for view in store.list_feature_views()] == ["ftr_test_day"]


def test_session_time_zone_is_the_sessions(spark):
    assert session_time_zone(spark) == "Asia/Tokyo"
