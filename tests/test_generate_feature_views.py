"""scripts/generate_feature_views.py: the dbt manifest → the Feast views module."""

from __future__ import annotations

import json

import pytest

from tests.support import import_script

generate = import_script("generate_feature_views")


def column(
    data_type: str, description: str = "", meta: dict | None = None, top_level: bool = False
) -> dict:
    col: dict[str, object] = {"data_type": data_type, "description": description}
    if meta is not None:
        if top_level:
            col["meta"] = meta
        else:
            col["config"] = {"meta": meta}
    return col


def node(name: str, path: str, columns: dict, description: str = "") -> dict:
    return {
        "resource_type": "model",
        "name": name,
        "path": path,
        "schema": "pma_features",
        "description": description,
        "columns": {col_name: {"name": col_name, **col} for col_name, col in columns.items()},
    }


def manifest() -> dict:
    keys = {
        "area_code": column("string"),
        "trade_date": column("date"),
        "available_at": column("timestamp"),
    }
    return {
        "nodes": {
            "model.pma.ftr_period_x": node(
                "ftr_period_x",
                "features/ftr_period_x.sql",
                {
                    **keys,
                    "time_code": column("int"),
                    "x": column("double", "X.", {"feature": True, "categorical": False}),
                },
                "Period mart.",
            ),
            "model.pma.ftr_day_y": node(
                "ftr_day_y",
                "features/ftr_day_y.sql",
                {
                    **keys,
                    "y": column(
                        "int", "Y.", {"feature": True, "categorical": True}, top_level=True
                    ),
                },
                "Day mart.",
            ),
            "model.pma.ftr_hour_z": node(
                "ftr_hour_z",
                "features/ftr_hour_z.sql",
                {
                    **keys,
                    "hour_ending": column("int"),
                    "z": column("bigint", "Z.", {"feature": True}),
                    "note": column("string", "Untagged."),
                    "published_at": column("timestamp", "When the row was written."),
                },
            ),
            "model.pma.fct_other": node(
                "fct_other", "curated/fct_other.sql", {"v": column("double", "", {"feature": True})}
            ),
            "test.pma.some_test": {
                "resource_type": "test",
                "path": "features/x.sql",
                "name": "t",
                "columns": {},
            },
        }
    }


class TestRender:
    def test_one_view_per_feature_mart_by_name_with_the_grain_entities(self):
        text = generate.render(manifest())
        assert (
            text.index("FTR_DAY_Y_SOURCE")
            < text.index("FTR_HOUR_Z_SOURCE")
            < text.index("FTR_PERIOD_X_SOURCE")
        )
        assert 'entities=list(GRAIN_ENTITIES["day"])' in text
        assert 'entities=list(GRAIN_ENTITIES["hour"])' in text
        assert 'entities=list(GRAIN_ENTITIES["period"])' in text
        assert "fct_other" not in text
        assert text.endswith("VIEWS = (\n    FTR_DAY_Y,\n    FTR_HOUR_Z,\n    FTR_PERIOD_X,\n)\n")

    def test_queries_select_keys_tagged_features_and_available_at(self):
        text = generate.render(manifest())
        assert (
            "query=\"select area_code, cast(date_format(trade_date, 'yyyyMMdd') as int) as trade_date_key, "
            'time_code, x, available_at from pma_features.ftr_period_x"'
        ) in text
        assert "hour_ending, z, available_at, published_at from pma_features.ftr_hour_z" in text
        assert "note" not in text.split("FTR_HOUR_Z = FeatureView")[1].split("VIEWS")[0].replace(
            "Untagged", ""
        )

    def test_a_mart_with_published_at_breaks_ties_on_it(self):
        text = generate.render(manifest())
        z_source = text.split("FTR_HOUR_Z_SOURCE = SparkSource(")[1].split(")\n")[0]
        assert (
            'timestamp_field="available_at",\n    created_timestamp_column="published_at",'
            in z_source
        )
        x_source = text.split("FTR_PERIOD_X_SOURCE = SparkSource(")[1].split(")\n")[0]
        assert "created_timestamp_column" not in x_source

    def test_fields_carry_types_descriptions_and_categorical_tags(self):
        text = generate.render(manifest())
        assert (
            'name="y",\n            dtype=Int64,\n            description="Y.",\n            tags={"categorical": "true"},'
            in text
        )
        assert (
            'name="x",\n            dtype=Float64,\n            description="X.",\n            tags={"categorical": "false"},'
            in text
        )
        assert 'name="z",\n            dtype=Int64,' in text
        assert "from feast.types import Float64, Int64\n" in text

    def test_rejects_a_feature_column_without_a_feast_type(self):
        bad = manifest()
        bad["nodes"]["model.pma.ftr_day_y"]["columns"]["y"]["data_type"] = "date"
        with pytest.raises(ValueError, match="ftr_day_y.y: data type 'date'"):
            generate.render(bad)


class TestMain:
    def test_writes_the_module_then_check_passes_and_detects_staleness(self, tmp_path, capsys):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest()))
        output = tmp_path / "views.py"
        args = ["--manifest", str(manifest_path), "--output", str(output)]
        assert generate.main(args) == 0
        assert output.read_text() == generate.render(manifest())
        assert generate.main([*args, "--check"]) == 0
        assert "is current" in capsys.readouterr().out
        output.write_text(output.read_text() + "# edited\n")
        assert generate.main([*args, "--check"]) == 1
        assert "is stale" in capsys.readouterr().err

    def test_check_fails_when_the_output_is_missing(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest()))
        assert (
            generate.main(
                ["--manifest", str(manifest_path), "--output", str(tmp_path / "none.py"), "--check"]
            )
            == 1
        )

    def test_the_checked_in_module_is_current(self):
        if not generate.DEFAULT_MANIFEST.exists():
            pytest.skip("no dbt manifest: run dbt parse first")
        assert generate.main(["--check"]) == 0
