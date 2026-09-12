-- fct_feature_value is generated from the manifest (scripts/generate_feature_views.py),
-- so a column tagged after the last `just feature-views` is missing from it. This test
-- reads the model's own SQL from the graph: every tagged column of every model under
-- models/features/ must be stacked inside its mart's CTE. One row per missing column,
-- whatever the warehouse holds, so an empty mart cannot fail it. The ref below is
-- the dependency that selects this test with the model and orders it after it.
-- depends_on: {{ ref('fct_feature_value') }}
{% set model_sql = graph.nodes['model.pma.fct_feature_value'].raw_code %}
{% set missing = [] %}
{% for node in graph.nodes.values() %}
  {% if node.resource_type == 'model' and node.path.startswith('features/') %}
    {% set parts = model_sql.split('  ' ~ node.name ~ ' as (\n') %}
    {% set body = parts[1].split('\n  ),\n')[0] if parts | length > 1 else '' %}
    {% for column in node.columns.values() %}
      {% set meta = column.config.meta if (column.config and column.config.meta) else column.meta %}
      {% if meta and meta.feature and ("'" ~ column.name ~ "', cast(") not in body %}
        {% do missing.append(node.name ~ ':' ~ column.name) %}
      {% endif %}
    {% endfor %}
  {% endif %}
{% endfor %}
{% if missing %}
select feature_ref from values {% for ref in missing %}('{{ ref }}'){% if not loop.last %}, {% endif %}{% endfor %} as t(feature_ref)
{% else %}
select cast(null as string) as feature_ref where false
{% endif %}
