{% macro ordered_weighted_mean(terms) -%}
{#- The weighted mean of an array of structs (weight, value), added in the
    array's order — the caller sorts it — so the value is the same on every
    build whatever order Spark reads the rows in. A plain sum() adds in read
    order and can move a value by ~1e-14 between builds, enough to move
    LightGBM's histogram bins. Null when the array is empty. -#}
case when size({{ terms }}) > 0 then
  aggregate({{ terms }}, cast(0 as double), (acc, x) -> acc + x.weight * x.value)
  / aggregate({{ terms }}, cast(0 as double), (acc, x) -> acc + x.weight)
end
{%- endmacro %}
