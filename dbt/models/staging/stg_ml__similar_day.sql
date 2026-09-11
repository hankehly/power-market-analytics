-- Written by a separate Spark application (scripts/fit_similar_day.py). The
-- table exists only once a run has published to it, so the pre-hook refreshes
-- the thriftserver's cached file listing when it exists, and until then the
-- model is an empty frame of the contract's types: the graph stays buildable.
{{ config(pre_hook="{{ refresh_ml_source_if_exists(source('ml', 'similar_day')) }}") }}

{%- set similar_day = source('ml', 'similar_day') -%}

{% if load_relation(similar_day) is not none %}
with
  source as (
  select
    run_id,
    area_code,
    trade_date,
    time_code,
    similar_day_demand_kwh,
    similar_day_reference_date,
    similar_day_reference_lag_days,
    similar_day_distance,
    similar_day_n_candidates,
    similar_day_fit_cutoff,
    available_at,
    published_at
  from
    {{ similar_day }}
  )

select * from source
{% else %}
select
  cast(null as string) as run_id,
  cast(null as string) as area_code,
  cast(null as date) as trade_date,
  cast(null as int) as time_code,
  cast(null as double) as similar_day_demand_kwh,
  cast(null as date) as similar_day_reference_date,
  cast(null as int) as similar_day_reference_lag_days,
  cast(null as double) as similar_day_distance,
  cast(null as int) as similar_day_n_candidates,
  cast(null as timestamp) as similar_day_fit_cutoff,
  cast(null as timestamp) as available_at,
  cast(null as timestamp) as published_at
where
  false
{% endif %}
