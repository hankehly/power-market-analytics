{#
  Pre-hook for the staging models of the pma_ml sources: refresh the
  thriftserver's cached file listing of a table another Spark application
  (a backtest script) writes — but only once the table exists. Every pma_ml
  table is created by the first run that publishes to it, so before that a
  plain REFRESH TABLE would abort the build.
#}
{% macro refresh_ml_source_if_exists(relation) -%}
  {%- if load_relation(relation) is not none -%}
    REFRESH TABLE {{ relation }}
  {%- else -%}
    select 1
  {%- endif -%}
{%- endmacro %}
