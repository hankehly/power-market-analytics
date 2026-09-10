"""Presets: named feature lists, their dtypes and categoricals off the views, and their services."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from feast import Field
from feast.types import Int64, String

from power_market_analytics.features import presets as presets_module
from power_market_analytics.features import views
from power_market_analytics.features.catalogue import feature_services
from power_market_analytics.features.entities import ENTITIES
from power_market_analytics.features.presets import (
    Preset,
    categorical_columns,
    feature_column,
    feature_dtypes,
    feature_service,
)
from power_market_analytics.features.store import open_store
from power_market_analytics.tasks.spot_price.presets import LIGHTGBM
from tests.support import write_feature_store_yaml

CALENDAR = "ftr_day_calendar:month"
LAG = "ftr_period_jepx:lag_1d_price"
DAY_TYPE = "ftr_day_calendar:day_type"
#: Every registered preset's service, both tasks, sorted.
REGISTERED_SERVICES = [
    "demand__lightgbm",
    "demand__lightgbm_msm",
    "demand__lightgbm_msm_popw",
    "demand__lightgbm_msm_popw_daytype",
    "spot_price__lightgbm",
    "spot_price__lightgbm_occto",
]


def preset(**overrides) -> Preset:
    fields = {"task": "spot_price", "name": "p", "features": (CALENDAR, LAG)}
    return Preset(**{**fields, **overrides})


class TestFeatureColumn:
    def test_column_of_a_reference(self):
        assert feature_column(LAG) == "lag_1d_price"

    @pytest.mark.parametrize("ref", ["lag_1d_price", ":x", "view:", ""])
    def test_malformed_reference(self, ref):
        with pytest.raises(ValueError, match="is not '<view>:<column>'"):
            feature_column(ref)


class TestPreset:
    def test_columns_and_feature_cols(self):
        p = preset()
        assert p.columns == ("month", "lag_1d_price")
        assert p.feature_cols == ("time_code", "month", "lag_1d_price")

    def test_rejects_a_duplicate_column(self):
        with pytest.raises(ValueError, match=r"duplicate feature columns \['month'\]"):
            preset(features=(CALENDAR, "ftr_other:month"))

    def test_rejects_time_code_as_a_feature(self):
        with pytest.raises(ValueError, match="'time_code' is every preset's first feature"):
            preset(features=(CALENDAR, "ftr_x:time_code"))

    def test_with_changes_drops_adds_and_renames(self):
        p = preset(features=(CALENDAR, DAY_TYPE, LAG))
        changed = p.with_changes(add=("ftr_day_occto:max_demand_mw",), drop=(DAY_TYPE,), name="q")
        assert changed.name == "q"
        assert changed.features == (CALENDAR, LAG, "ftr_day_occto:max_demand_mw")
        assert p.features == (CALENDAR, DAY_TYPE, LAG)  # unchanged

    def test_with_changes_rejects_an_absent_drop_and_a_present_add(self):
        with pytest.raises(ValueError, match=r"cannot drop \['ftr_x:y'\]"):
            preset().with_changes(drop=("ftr_x:y",), name="q")
        with pytest.raises(ValueError, match=r"cannot add \['ftr_period_jepx:lag_1d_price'\]"):
            preset().with_changes(add=(LAG,), name="q")


class TestFeatureDtypes:
    def test_reads_the_views_types_in_feature_order(self):
        p = preset(features=(LAG, CALENDAR, "ftr_day_occto:max_demand_mw"))
        assert feature_dtypes(p) == {
            "lag_1d_price": "float64",
            "month": "int64",
            "max_demand_mw": "int64",
        }

    def test_unknown_view(self):
        with pytest.raises(ValueError, match="unknown feature view 'ftr_x'"):
            feature_dtypes(preset(features=("ftr_x:y",)))

    def test_unknown_column_and_a_join_key(self):
        with pytest.raises(ValueError, match="'ftr_day_calendar:nope' is not a feature"):
            feature_dtypes(preset(features=("ftr_day_calendar:nope",)))
        with pytest.raises(ValueError, match="'ftr_day_calendar:area_code' is not a feature"):
            feature_dtypes(preset(features=("ftr_day_calendar:area_code",)))

    def test_a_non_numeric_feature_is_rejected(self, monkeypatch):
        fake_view = SimpleNamespace(schema=[Field(name="note", dtype=String)], join_keys=[])
        monkeypatch.setattr(presets_module, "_views_by_name", lambda: {"ftr_x": fake_view})
        with pytest.raises(ValueError, match="'ftr_x:note' has Feast type"):
            feature_dtypes(preset(features=("ftr_x:note",)))


class TestCategoricalColumns:
    def test_reads_the_views_tag_in_feature_order(self):
        assert categorical_columns(preset()) == ()
        assert categorical_columns(preset(features=(LAG, DAY_TYPE, CALENDAR))) == ("day_type",)

    def test_an_added_categorical_is_categorical(self):
        # The registered preset has none; adding the day type (tagged in the
        # mart) marks it without the preset saying so, and dropping it unmarks.
        assert categorical_columns(LIGHTGBM) == ()
        added = LIGHTGBM.with_changes(add=(DAY_TYPE,), name="lightgbm_daytype")
        assert categorical_columns(added) == ("day_type",)
        assert categorical_columns(added.with_changes(drop=(DAY_TYPE,), name="back")) == ()

    def test_unknown_reference(self):
        with pytest.raises(ValueError, match="unknown feature view 'ftr_x'"):
            categorical_columns(preset(features=("ftr_x:y",)))

    def test_a_field_without_tags_is_numeric(self, monkeypatch):
        fake_view = SimpleNamespace(schema=[Field(name="n", dtype=Int64, tags=None)], join_keys=[])
        monkeypatch.setattr(presets_module, "_views_by_name", lambda: {"ftr_x": fake_view})
        assert categorical_columns(preset(features=("ftr_x:n",))) == ()


class TestFeatureService:
    def test_one_projection_per_view_with_the_presets_columns(self):
        p = preset(features=(CALENDAR, DAY_TYPE, LAG))
        service = feature_service(p)
        assert service.name == "spot_price__p"
        projections = {
            proj.name: [f.name for f in proj.features] for proj in service.feature_view_projections
        }
        assert projections == {
            "ftr_day_calendar": ["month", "day_type"],
            "ftr_period_jepx": ["lag_1d_price"],
        }
        assert service.tags == {"task": "spot_price", "preset": "p", "categorical": "day_type"}

    def test_the_catalogue_lists_every_tasks_presets(self):
        assert sorted(s.name for s in feature_services()) == REGISTERED_SERVICES


class TestStoreServices:
    def test_open_store_applies_the_services_and_drops_a_stale_one(self, tmp_path):
        repo = write_feature_store_yaml(tmp_path)
        stale = feature_service(preset(name="stale"))
        open_store(repo, definitions=[*ENTITIES, *views.VIEWS, stale])
        assert [
            s.name
            for s in open_store(
                repo, definitions=[*ENTITIES, *views.VIEWS, stale]
            ).list_feature_services()
        ] == ["spot_price__stale"]
        store = open_store(repo)
        assert sorted(s.name for s in store.list_feature_services()) == REGISTERED_SERVICES
