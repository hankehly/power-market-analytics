-- Every model under models/features/ declares available_at in its enforced
-- contract: the as-of join of the feature layer reads it, so a mart without
-- the column would silently join nothing. One row per offending model.
{% set missing = [] %}
{% for node in graph.nodes.values() %}
  {% if node.resource_type == 'model' and node.path.startswith('features/') and 'available_at' not in node.columns %}
    {% do missing.append(node.name) %}
  {% endif %}
{% endfor %}
{% if missing %}
select model from values {% for name in missing %}('{{ name }}'){% if not loop.last %}, {% endif %}{% endfor %} as t(model)
{% else %}
select cast(null as string) as model where false
{% endif %}
