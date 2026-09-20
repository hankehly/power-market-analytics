{% macro discomfort_index(temperature_c, relative_humidity_pct) -%}
{#- The 不快指数 (discomfort index, temperature-humidity index) from a
    temperature in C and a relative humidity in %:
    0.81 T + 0.01 H (0.99 T - 14.3) + 46.3, as printed in 木内 2001 (天気 48(9),
    p. 669, eq. A1; docs/research/literature-review.md). It is the U.S.
    Weather Bureau's relative-humidity form,
    T - (0.55 - 0.0055 H)(T - 58) with T in F, after
    T in F = 1.8 T in C + 32: the two are equal term by term. Not a JMA
    statistic. Below 14.4 C a higher humidity lowers the index.
    Every literal is cast to double: a Spark literal with a decimal point is a
    decimal. Null when either input is null. -#}
cast(0.81 as double) * {{ temperature_c }}
+ cast(0.01 as double) * {{ relative_humidity_pct }}
  * (cast(0.99 as double) * {{ temperature_c }} - cast(14.3 as double))
+ cast(46.3 as double)
{%- endmacro %}
