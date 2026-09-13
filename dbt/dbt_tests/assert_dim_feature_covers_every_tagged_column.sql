-- dim_feature is generated from the manifest (scripts/generate_feature_views.py), so a
-- column tagged, or an expression changed, after the last `just feature-views` is
-- missing from it. This test reads the model's own SQL from the graph: every tagged
-- column of every model under models/features/ must declare meta.expression, and its
-- row (name, expression, mart) must be in the model. One row per offending column,
-- whatever the warehouse holds. The ref below is the dependency that selects this
-- test with the model and orders it after it.
-- depends_on: {{ ref('dim_feature') }}
{% set model_sql = graph.nodes['model.pma.dim_feature'].raw_code %}
{% set offending = [] %}
{% for node in graph.nodes.values() %}
  {% if node.resource_type == 'model' and node.path.startswith('features/') %}
    {% for column in node.columns.values() %}
      {% set meta = column.config.meta if (column.config and column.config.meta) else column.meta %}
      {% if meta and meta.feature %}
        {% set expression = (meta.expression or '') | trim %}
        {% set row = "('" ~ column.name ~ "', '" ~ (expression | replace("\\", "\\\\") | replace("'", "\\'")) ~ "', '" ~ node.name ~ "', " %}
        {% if not expression %}
          {% do offending.append((node.name ~ ':' ~ column.name, 'no meta.expression')) %}
        {% elif row not in model_sql %}
          {% do offending.append((node.name ~ ':' ~ column.name, 'not in dim_feature')) %}
        {% endif %}
      {% endif %}
    {% endfor %}
  {% endif %}
{% endfor %}
{% if offending %}
select feature_ref, problem from values {% for ref, problem in offending %}('{{ ref }}', '{{ problem }}'){% if not loop.last %}, {% endif %}{% endfor %} as t(feature_ref, problem)
{% else %}
select cast(null as string) as feature_ref, cast(null as string) as problem where false
{% endif %}
