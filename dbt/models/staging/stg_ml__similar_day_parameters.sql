-- Written by a separate Spark application (scripts/fit_similar_day.py). The
-- table exists only once a fit has published to it, so the pre-hook refreshes
-- the thriftserver's cached file listing when it exists, and until then the
-- model is an empty frame of the contract's types: the graph stays buildable.
{{ config(pre_hook="{{ refresh_ml_source_if_exists(source('ml', 'similar_day_parameters')) }}") }}

{%- set parameters = source('ml', 'similar_day_parameters') -%}
{%- set parts = ['calendar_days', 'temperature', 'humidity', 'rain', 'days_since_holiday', 'days_until_holiday', 'holiday_degree'] -%}

{% if load_relation(parameters) is not none %}
with
  source as (
  select
    run_id,
    area_code,
    fit_from,
    fit_through,
    center_lag_days,
    window_half_width_days,
    census_year,
    {% for part in parts %}
    weight_{{ part }},
    {% endfor %}
    {% for part in parts %}
    scale_{{ part }},
    {% endfor %}
    alpha,
    beta,
    n_pairs,
    n_targets,
    fit_rmse,
    available_at,
    published_at
  from
    {{ parameters }}
  )

select * from source
{% else %}
select
  cast(null as string) as run_id,
  cast(null as string) as area_code,
  cast(null as date) as fit_from,
  cast(null as date) as fit_through,
  cast(null as int) as center_lag_days,
  cast(null as int) as window_half_width_days,
  cast(null as int) as census_year,
  {% for part in parts %}
  cast(null as double) as weight_{{ part }},
  {% endfor %}
  {% for part in parts %}
  cast(null as double) as scale_{{ part }},
  {% endfor %}
  cast(null as double) as alpha,
  cast(null as double) as beta,
  cast(null as bigint) as n_pairs,
  cast(null as int) as n_targets,
  cast(null as double) as fit_rmse,
  cast(null as timestamp) as available_at,
  cast(null as timestamp) as published_at
where
  false
{% endif %}
