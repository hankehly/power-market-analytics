"""Presets: named feature lists, their dtypes, categoricals and expressions off the views, and their services."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from feast import Field
from feast.types import Int64, String

from power_market_analytics.features import presets as presets_module
from power_market_analytics.features import views
from power_market_analytics.features.catalogue import feature_services
from power_market_analytics.features.entities import ENTITIES
from power_market_analytics.features.presets import (
    PRESETS_DIR,
    Preset,
    categorical_columns,
    feature_column,
    feature_dtypes,
    feature_expressions,
    feature_service,
    load_presets,
    preset_tasks,
)
from power_market_analytics.features.store import open_store
from tests.support import write_feature_store_yaml

CALENDAR = "ftr_day_calendar:month"
LAG = "ftr_period_jepx:lag_1d_price"
DAY_TYPE = "ftr_day_calendar:day_type"
#: Every registered preset's service, both tasks, sorted.
REGISTERED_SERVICES = [
    "demand__e212",
    "demand__e219",
    "demand__lightgbm",
    "demand__lightgbm_msm",
    "demand__lightgbm_msm_popw",
    "demand__lightgbm_msm_popw_daytype",
    "demand__lightgbm_msm_popw_daytype_simday",
    "demand__lightgbm_msm_popw_daytype_simday_calendar",
    "demand__lightgbm_msm_popw_daytype_simday_calendarcounts",
    "demand__lightgbm_msm_popw_daytype_simday_holidaydegree",
    "demand__lightgbm_msm_popw_daytype_simday_holidaydistance",
    "demand__lightgbm_msm_popw_daytype_simday_lags",
    "demand__lightgbm_msm_popw_daytype_simday_lags_weather",
    "spot_price__lightgbm",
    "spot_price__lightgbm_occto",
]
LIGHTGBM = load_presets("spot_price")["lightgbm"]


def preset(**overrides) -> Preset:
    fields = {"task": "spot_price", "name": "p", "features": (CALENDAR, LAG)}
    return Preset(**{**fields, **overrides})


def write_preset(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(textwrap.dedent(text))
    return path


ONE = "description: The first.\nfeatures: [ftr_day_calendar:month, ftr_period_jepx:lag_1d_price]\n"


class TestLoadPresets:
    def test_a_full_list_and_a_base_chain_resolve_in_order(self, tmp_path):
        task = tmp_path / "demand"
        write_preset(task, "one", ONE)
        write_preset(
            task,
            "two",
            "description: Plus the day type.\nbase: one\nadd: [ftr_day_calendar:day_type]\n",
        )
        write_preset(
            task,
            "three",
            "description: Minus the lag.\nbase: two\ndrop: [ftr_period_jepx:lag_1d_price]\n",
        )
        presets = load_presets("demand", tmp_path)
        assert list(presets) == ["one", "three", "two"]
        assert presets["one"] == Preset("demand", "one", (CALENDAR, LAG), description="The first.")
        assert presets["two"].features == (CALENDAR, LAG, DAY_TYPE)
        assert presets["two"].base == "one"
        assert presets["three"] == Preset(
            "demand", "three", (CALENDAR, DAY_TYPE), base="two", description="Minus the lag."
        )

    def test_a_base_may_add_and_drop_in_one_file(self, tmp_path):
        task = tmp_path / "demand"
        write_preset(task, "one", ONE)
        write_preset(
            task,
            "two",
            "description: Swap.\nbase: one\nadd: [ftr_day_calendar:day_type]\ndrop: [ftr_day_calendar:month]\n",
        )
        assert load_presets("demand", tmp_path)["two"].features == (LAG, DAY_TYPE)

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("- a\n", "is not a mapping"),
            ("description: x\nfeatures: [ftr_a:b]\nextra: 1\n", "unknown keys ['extra']"),
            ("features: [ftr_a:b]\n", "description is required"),
            ("description: ' '\nfeatures: [ftr_a:b]\n", "description is required"),
            ("description: x\n", "exactly one of features and base"),
            (
                "description: x\nfeatures: [ftr_a:b]\nbase: one\n",
                "exactly one of features and base",
            ),
            ("description: x\nfeatures: [ftr_a:b]\nadd: [ftr_a:c]\n", "add and drop need a base"),
            ("description: x\nbase: one\n", "a base needs add or drop"),
            ("description: x\nbase: one\nadd: []\ndrop: []\n", "a base needs add or drop"),
            ("description: x\nbase: 3\nadd: [ftr_a:c]\n", "base must be a preset name"),
            (
                "description: x\nbase: nope\nadd: [ftr_a:c]\n",
                "base 'nope' is not a preset of demand",
            ),
            (
                "description: x\nfeatures: ftr_a:b\n",
                "features must be a list of 'view:column' strings",
            ),
            ("description: x\nfeatures: [lag]\n", "is not '<view>:<column>'"),
            ("description: x\nfeatures: [ftr_a:b, ftr_c:b]\n", "duplicate feature columns ['b']"),
            ("description: x\nfeatures: [ftr_a:time_code]\n", "every preset's first feature"),
            ("description: x\nbase: one\ndrop: [ftr_x:y]\n", "cannot drop ['ftr_x:y']"),
            ("description: x\nbase: one\nadd: [ftr_day_calendar:month]\n", "cannot add"),
        ],
    )
    def test_rejects_a_bad_file_naming_it(self, tmp_path, text, message):
        task = tmp_path / "demand"
        write_preset(task, "one", ONE)
        bad = write_preset(task, "bad", text)
        with pytest.raises(ValueError, match=re.escape(str(bad))) as excinfo:
            load_presets("demand", tmp_path)
        assert message in str(excinfo.value)

    def test_rejects_a_looping_chain(self, tmp_path):
        task = tmp_path / "demand"
        write_preset(task, "a", "description: x\nbase: b\nadd: [ftr_a:b]\n")
        write_preset(task, "b", "description: x\nbase: a\nadd: [ftr_a:c]\n")
        with pytest.raises(ValueError, match=r"a\.yaml: base chain loops: a -> b -> a"):
            load_presets("demand", tmp_path)

    def test_rejects_a_name_outside_lowercase_snake_case(self, tmp_path):
        write_preset(tmp_path / "demand", "Bad-Name", ONE)
        with pytest.raises(ValueError, match=r"Bad-Name\.yaml: a preset name is lowercase a-z0-9_"):
            load_presets("demand", tmp_path)

    def test_rejects_a_task_without_files(self, tmp_path):
        (tmp_path / "demand").mkdir()
        with pytest.raises(ValueError, match="no preset files"):
            load_presets("demand", tmp_path)

    def test_the_task_directories_are_listed_sorted(self, tmp_path):
        write_preset(tmp_path / "spot_price", "one", ONE)
        write_preset(tmp_path / "demand", "one", ONE)
        (tmp_path / "notes.txt").write_text("")
        assert preset_tasks(tmp_path) == ("demand", "spot_price")

    def test_the_repository_directories_are_the_tasks(self):
        assert PRESETS_DIR.name == "presets" and PRESETS_DIR.parent.name == "conf"
        assert preset_tasks() == ("demand", "spot_price")


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

    def test_with_changes_records_the_preset_it_started_from(self):
        first = preset().with_changes(add=(DAY_TYPE,), name="q")
        assert first.base == "p"
        second = first.with_changes(drop=(DAY_TYPE,), name="r")
        assert second.base == "q"

    def test_with_changes_rejects_an_absent_drop_and_a_present_add(self):
        with pytest.raises(ValueError, match=r"cannot drop \['ftr_x:y'\]"):
            preset().with_changes(drop=("ftr_x:y",), name="q")
        with pytest.raises(ValueError, match=r"cannot add \['ftr_period_jepx:lag_1d_price'\]"):
            preset().with_changes(add=(LAG,), name="q")

    def test_description_is_empty_unless_given_and_a_change_clears_it(self):
        assert preset().description == ""
        described = preset(description="Calendar and the lag.")
        assert described.description == "Calendar and the lag."
        assert described.with_changes(add=(DAY_TYPE,), name="q").description == ""


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


class TestFeatureExpressions:
    def test_reads_the_views_tag_in_feature_order(self):
        assert feature_expressions(preset(features=(LAG, DAY_TYPE, CALENDAR))) == {
            "lag_1d_price": "LAG(area_price_jpy_kwh, 1d)",
            "day_type": "day_type",
            "month": "month",
        }
        assert list(feature_expressions(preset(features=(CALENDAR, LAG)))) == [
            "month",
            "lag_1d_price",
        ]

    def test_every_registered_feature_has_an_expression(self):
        for view in views.VIEWS:
            for field in view.schema:
                if field.name not in view.join_keys:
                    assert field.tags.get("expression"), f"{view.name}:{field.name}"

    def test_a_field_without_the_tag_is_left_out(self, monkeypatch):
        fake_view = SimpleNamespace(
            schema=[
                Field(name="n", dtype=Int64, tags=None),
                Field(name="m", dtype=Int64, tags={"categorical": "false"}),
                Field(name="e", dtype=Int64, tags={"expression": "LAG(e, 1d)"}),
            ],
            join_keys=[],
        )
        monkeypatch.setattr(presets_module, "_views_by_name", lambda: {"ftr_x": fake_view})
        assert feature_expressions(preset(features=("ftr_x:n", "ftr_x:m", "ftr_x:e"))) == {
            "e": "LAG(e, 1d)"
        }

    def test_unknown_reference(self):
        with pytest.raises(ValueError, match="unknown feature view 'ftr_x'"):
            feature_expressions(preset(features=("ftr_x:y",)))


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

    def test_the_description_is_the_presets_or_a_summary(self):
        assert feature_service(preset(description="The lag.")).description == "The lag."
        assert feature_service(preset()).description == (
            "Preset 'p' of the spot_price task: ftr_day_calendar:month, ftr_period_jepx:lag_1d_price."
        )

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
