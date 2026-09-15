"""scripts/generate_feature_views.py: the dbt manifest → the Feast views module, the
feature-value fact and the feature dimension."""

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
                    "x": column(
                        "double",
                        "X.",
                        {"feature": True, "categorical": False, "expression": "LAG(x, 1d)"},
                    ),
                },
                "Period mart.",
            ),
            "model.pma.ftr_day_y": node(
                "ftr_day_y",
                "features/ftr_day_y.sql",
                {
                    **keys,
                    "y": column(
                        "int",
                        "Y.",
                        {"feature": True, "categorical": True, "expression": "y"},
                        top_level=True,
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
                    "z": column(
                        "bigint", "Z.", {"feature": True, "expression": "SIMILAR_DAY(z) / 2"}
                    ),
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

    def test_fields_carry_types_descriptions_and_categorical_and_expression_tags(self):
        text = generate.render(manifest())
        assert (
            'name="y",\n            dtype=Int64,\n            description="Y.",\n'
            '            tags={"categorical": "true", "expression": "y"},'
        ) in text
        assert (
            'name="x",\n            dtype=Float64,\n            description="X.",\n'
            '            tags={"categorical": "false", "expression": "LAG(x, 1d)"},'
        ) in text
        assert (
            'name="z",\n            dtype=Int64,\n            description="Z.",\n'
            '            tags={"categorical": "false", "expression": "SIMILAR_DAY(z) / 2"},'
        ) in text
        assert "from feast.types import Float64, Int64\n" in text

    def test_rejects_a_feature_column_without_an_expression(self):
        bad = manifest()
        del bad["nodes"]["model.pma.ftr_period_x"]["columns"]["x"]["config"]["meta"]["expression"]
        with pytest.raises(ValueError, match="ftr_period_x.x: no meta.expression"):
            generate.render(bad)

    def test_rejects_a_feature_column_without_a_feast_type(self):
        bad = manifest()
        bad["nodes"]["model.pma.ftr_day_y"]["columns"]["y"]["data_type"] = "date"
        with pytest.raises(ValueError, match="ftr_day_y.y: data type 'date'"):
            generate.render(bad)


class TestCatalogue:
    def test_one_entry_per_tagged_column_by_mart_then_contract_order(self):
        entries = generate.catalogue(manifest())
        assert [(e["feature_view"], e["feature_name"]) for e in entries] == [
            ("ftr_day_y", "y"),
            ("ftr_hour_z", "z"),
            ("ftr_period_x", "x"),
        ]
        assert entries[1] == {
            "feature_name": "z",
            "feature_expression": "SIMILAR_DAY(z) / 2",
            "feature_view": "ftr_hour_z",
            "grain": "hour",
            "data_type": "bigint",
            "is_categorical": False,
            "feature_description": "Z.",
        }
        assert entries[0]["is_categorical"] is True

    def test_trims_the_expression_and_folds_the_description_whitespace(self):
        m = manifest()
        x = m["nodes"]["model.pma.ftr_period_x"]["columns"]["x"]
        x["config"]["meta"]["expression"] = "  LAG(x, 1d)\n"
        x["description"] = "The x\n  of D-1's\tperiod. "
        entry = generate.catalogue(m)[2]
        assert entry["feature_expression"] == "LAG(x, 1d)"
        assert entry["feature_description"] == "The x of D-1's period."

    @pytest.mark.parametrize("expression", [None, "", "   ", 3])
    def test_rejects_a_missing_blank_or_non_string_expression(self, expression):
        m = manifest()
        meta = m["nodes"]["model.pma.ftr_hour_z"]["columns"]["z"]["config"]["meta"]
        meta["expression"] = expression
        with pytest.raises(ValueError, match="ftr_hour_z.z: no meta.expression"):
            generate.catalogue(m)

    def test_rejects_a_column_name_two_marts_share(self):
        m = manifest()
        columns = m["nodes"]["model.pma.ftr_period_x"]["columns"]
        columns["y"] = {"name": "y", **column("int", "", {"feature": True, "expression": "Y2"})}
        with pytest.raises(
            ValueError, match="ftr_period_x.y and ftr_day_y.y share the feature_name 'y'"
        ):
            generate.catalogue(m)

    def test_rejects_an_expression_two_columns_share(self):
        m = manifest()
        meta = m["nodes"]["model.pma.ftr_period_x"]["columns"]["x"]["config"]["meta"]
        meta["expression"] = "SIMILAR_DAY(z) / 2"
        with pytest.raises(
            ValueError,
            match=r"ftr_period_x.x and ftr_hour_z.z share the feature_expression 'SIMILAR_DAY\(z\) / 2'",
        ):
            generate.catalogue(m)


class TestSqlString:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("LAG(x, 1d)", "'LAG(x, 1d)'"),
            ("D-2's", "'D-2\\'s'"),
            ("a\\b", "'a\\\\b'"),
            ("\\'", "'\\\\\\''"),
        ],
    )
    def test_quotes_and_escapes_with_backslashes(self, text, expected):
        assert generate.sql_string(text) == expected


class TestRenderDim:
    def test_catalogue_rows_in_a_raw_block_then_the_retired_seed(self):
        m = manifest()
        m["nodes"]["model.pma.ftr_day_y"]["columns"]["y"]["description"] = "Y's {{ day }}."
        text = generate.render_dim(m)
        assert text.startswith("-- dim_feature:")
        rows = text.split("{% raw %}\n")[1].split("\n{% endraw %}")[0]
        assert rows.split(",\n") == [
            "    ('y', 'y', 'ftr_day_y', 'day', 'int', true, 'Y\\'s {{ day }}.')",
            "    ('z', 'SIMILAR_DAY(z) / 2', 'ftr_hour_z', 'hour', 'bigint', false, 'Z.')",
            "    ('x', 'LAG(x, 1d)', 'ftr_period_x', 'period', 'double', false, 'X.')",
        ]
        assert (
            "    as t(feature_name, feature_expression, feature_view, grain, data_type, "
            "is_categorical, feature_description)\n"
        ) in text
        assert "{{ ref('retired_features') }}" in text.split("{% endraw %}")[1]
        assert "select *, true as is_retired from retired" in text
        assert (
            "case when feature_view is not null then concat(feature_view, ':', feature_name) end "
            "as feature_ref"
        ) in text
        assert text.endswith("select * from final\n")

    def test_rejects_a_manifest_without_a_tagged_column(self):
        m = manifest()
        for node in m["nodes"].values():
            node["columns"] = {
                name: col
                for name, col in node["columns"].items()
                if not generate.column_meta(col).get("feature")
            }
        with pytest.raises(ValueError, match="no feature mart has a tagged column"):
            generate.render_dim(m)


def cte(text: str, name: str) -> str:
    """The body of the named CTE of a rendered fact."""
    return text.split(f"  {name} as (\n")[1].split("\n  ),\n")[0]


class TestRenderFact:
    def test_day_marts_broadcast_hour_marts_join_and_period_marts_pass(self):
        text = generate.render_fact(manifest())
        assert text.startswith("-- fct_feature_value:")
        assert (
            "  periods as (\n  select\n    time_code,\n    hour_of_day + 1 as hour_ending\n"
            "  from\n    {{ ref('dim_half_hour') }}\n  ),\n"
        ) in text
        day = cte(text, "ftr_day_y")
        assert "    p.time_code,\n" in day
        assert "{{ ref('ftr_day_y') }} m\n    cross join periods p" in day
        assert (
            "    stack(\n      1,\n      'y', cast(m.y as double), true\n"
            "    ) as (feature_name, feature_value, is_categorical)\n"
        ) in day
        assert "'ftr_day_y' as feature_view," in day
        hour = cte(text, "ftr_hour_z")
        assert "    join periods p on p.hour_ending = m.hour_ending" in hour
        assert "'z', cast(m.z as double), false" in hour
        assert "note" not in hour
        period = cte(text, "ftr_period_x")
        assert "    m.time_code,\n" in period
        assert "periods" not in period
        assert "'x', cast(m.x as double), false" in period
        assert (
            text.index("ftr_day_y as (")
            < text.index("ftr_hour_z as (")
            < text.index("ftr_period_x as (")
        )
        assert "fct_other" not in text
        assert (
            "  unioned as (\n  select * from ftr_day_y\n  union all\n  select * from ftr_hour_z\n"
            "  union all\n  select * from ftr_period_x\n  ),\n"
        ) in text
        assert "concat(feature_view, ':', feature_name) as feature_ref" in text
        assert text.endswith("select * from final\n")

    def test_a_mart_with_published_at_keeps_the_newest_published_vintage(self):
        text = generate.render_fact(manifest())
        hour = cte(text, "ftr_hour_z")
        assert (
            "      row_number() over (\n"
            "        partition by area_code, trade_date, hour_ending, available_at\n"
            "        order by published_at desc\n      ) as vintage_rank"
        ) in hour
        assert "    m.published_at,\n" in hour
        assert "  where\n    m.vintage_rank = 1" in hour
        assert "cast(null as timestamp) as published_at" in cte(text, "ftr_day_y")
        assert "vintage_rank" not in cte(text, "ftr_period_x")

    def test_booleans_cast_through_int_and_other_types_are_rejected(self):
        bad = manifest()
        columns = bad["nodes"]["model.pma.ftr_day_y"]["columns"]
        columns["b"] = {"name": "b", **column("boolean", "B.", {"feature": True})}
        text = generate.render_fact(bad)
        assert "    stack(\n      2,\n" in text
        assert "'b', cast(cast(m.b as int) as double), false" in text
        columns["b"]["data_type"] = "string"
        with pytest.raises(ValueError, match="ftr_day_y.b: data type 'string' has no numeric"):
            generate.render_fact(bad)

    def test_a_mart_without_a_tagged_column_is_left_out(self):
        thin = manifest()
        del thin["nodes"]["model.pma.ftr_hour_z"]["columns"]["z"]
        text = generate.render_fact(thin)
        assert "ftr_hour_z" not in text
        assert (
            "  unioned as (\n  select * from ftr_day_y\n  union all\n  select * from ftr_period_x"
            in text
        )
        for node in thin["nodes"].values():
            node["columns"] = {
                name: col
                for name, col in node["columns"].items()
                if not generate.column_meta(col).get("feature")
            }
        with pytest.raises(ValueError, match="no feature mart has a tagged column"):
            generate.render_fact(thin)


class TestMain:
    def test_writes_the_three_outputs_then_check_passes_and_detects_staleness(
        self, tmp_path, capsys
    ):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest()))
        output = tmp_path / "views.py"
        fact = tmp_path / "fct_feature_value.sql"
        dim = tmp_path / "dim_feature.sql"
        args = [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output),
            "--fact-output",
            str(fact),
            "--dim-output",
            str(dim),
        ]
        assert generate.main(args) == 0
        assert output.read_text() == generate.render(manifest())
        assert fact.read_text() == generate.render_fact(manifest())
        assert dim.read_text() == generate.render_dim(manifest())
        assert generate.main([*args, "--check"]) == 0
        out = capsys.readouterr().out
        assert "views.py is current" in out and "fct_feature_value.sql is current" in out
        assert "dim_feature.sql is current" in out
        # Any output stale fails the check and is named.
        dim.write_text(dim.read_text() + "-- edited\n")
        assert generate.main([*args, "--check"]) == 1
        err = capsys.readouterr().err
        assert "dim_feature.sql is stale" in err and "views.py" not in err
        fact.write_text(fact.read_text() + "-- edited\n")
        assert generate.main([*args, "--check"]) == 1
        err = capsys.readouterr().err
        assert "fct_feature_value.sql is stale" in err and "views.py" not in err
        output.write_text(output.read_text() + "# edited\n")
        assert generate.main([*args, "--check"]) == 1
        err = capsys.readouterr().err
        assert "views.py is stale" in err and "fct_feature_value.sql is stale" in err

    def test_check_fails_when_the_output_is_missing(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest()))
        args = [
            "--manifest",
            str(manifest_path),
            "--output",
            str(tmp_path / "none.py"),
            "--fact-output",
            str(tmp_path / "none.sql"),
            "--dim-output",
            str(tmp_path / "none_dim.sql"),
        ]
        assert generate.main([*args, "--check"]) == 1

    def test_the_checked_in_outputs_are_current(self):
        if not generate.DEFAULT_MANIFEST.exists():
            pytest.skip("no dbt manifest: run dbt parse first")
        assert generate.main(["--check"]) == 0
