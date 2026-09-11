{% macro profile_rmse(target, candidate) -%}
{#- The root mean square of the hour-by-hour gaps between two 24-value profiles
    (arrays in hour order): the similar-day weather parts of
    tasks/demand/similar_day.py, added in hour order so a rebuild gives the same
    value to the bit. -#}
sqrt(
  aggregate(
    zip_with({{ target }}, {{ candidate }}, (t, c) -> (t - c) * (t - c)),
    cast(0 as double),
    (acc, x) -> acc + x
  ) / size({{ target }})
)
{%- endmacro %}
