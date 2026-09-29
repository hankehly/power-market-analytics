{% macro inverse_distance_mean(values, distances) -%}
{#- The similar-day job's inverse-distance weighted mean over the ranks whose value
    is present (tasks/demand/similar_day.inverse_distance_weights and
    similar_day_feature._weighted_mean): inv_r = 1 / d_r; when a present rank has
    d_r = 0, those ranks take inv 1 and the others 0; total = inv_1 + inv_2 + inv_3
    in rank order; w_r = inv_r / total; the mean adds w_r x_r in rank order, an
    absent rank contributing nothing; null when no rank is present. `values` and
    `distances` are lists of SQL expressions in rank order. -#}
{%- set present = [] -%}
{%- for i in range(values | length) -%}
  {%- do present.append('(' ~ values[i] ~ ' is not null and ' ~ distances[i] ~ ' is not null)') -%}
{%- endfor -%}
{%- set zero_terms = [] -%}
{%- for i in range(values | length) -%}
  {%- do zero_terms.append('(' ~ present[i] ~ ' and ' ~ distances[i] ~ ' = 0)') -%}
{%- endfor -%}
{%- set any_zero = '(' ~ zero_terms | join(' or ') ~ ')' -%}
{%- set inverse = [] -%}
{%- for i in range(values | length) -%}
  {%- do inverse.append(
    '(case when not ' ~ present[i] ~ ' then cast(0 as double)'
    ~ ' when ' ~ any_zero ~ ' then (case when ' ~ distances[i] ~ ' = 0 then cast(1 as double) else cast(0 as double) end)'
    ~ ' else cast(1 as double) / ' ~ distances[i] ~ ' end)'
  ) -%}
{%- endfor -%}
{%- set total = '(' ~ inverse | join(' + ') ~ ')' -%}
{%- set terms = [] -%}
{%- for i in range(values | length) -%}
  {%- do terms.append('coalesce((' ~ inverse[i] ~ ' / ' ~ total ~ ') * ' ~ values[i] ~ ', cast(0 as double))') -%}
{%- endfor -%}
case when {{ total }} = 0 then cast(null as double) else ({{ terms | join(' + ') }}) end
{%- endmacro %}
