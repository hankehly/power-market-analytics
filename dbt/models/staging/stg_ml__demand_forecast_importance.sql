-- Written by a separate Spark application (the backtest script). The table
-- exists only once a run has published to it, so the pre-hook refreshes the
-- thriftserver's cached file listing when it exists, and until then the model
-- is an empty frame of the contract's types: the graph stays buildable.
{{ config(pre_hook="{{ refresh_ml_source_if_exists(source('ml', 'demand_forecast_importance')) }}") }}

{%- set importance = source('ml', 'demand_forecast_importance') -%}

{% if load_relation(importance) is not none %}
with
  source as (
  select
    run_id,
    strategy,
    area_code,
    feature,
    feature_order,
    repeat_index,
    n_periods,
    mae_demand_kwh,
    permuted_mae_demand_kwh,
    published_at
  from
    {{ importance }}
  )

select * from source
{% else %}
select
  cast(null as string) as run_id,
  cast(null as string) as strategy,
  cast(null as string) as area_code,
  cast(null as string) as feature,
  cast(null as int) as feature_order,
  cast(null as int) as repeat_index,
  cast(null as int) as n_periods,
  cast(null as double) as mae_demand_kwh,
  cast(null as double) as permuted_mae_demand_kwh,
  cast(null as timestamp) as published_at
where
  false
{% endif %}
