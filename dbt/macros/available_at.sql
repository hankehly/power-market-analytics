{% macro available_at(columns) -%}
{#- The instant a feature row became public: the greatest of its inputs'
    available_at values. Spark's greatest() needs two arguments, so a single
    input passes through. -#}
{%- if columns | length == 1 -%}
{{ columns[0] }}
{%- else -%}
greatest({{ columns | join(', ') }})
{%- endif -%}
{%- endmacro %}
