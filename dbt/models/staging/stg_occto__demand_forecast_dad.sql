with
  source as (
  select
    formulated_date,
    target_date,
    area_name_ja,
    min_demand_time,
    min_demand_mw,
    max_demand_time,
    max_demand_mw,
    max_supply_capacity_mw,
    usage_rate_pct,
    reserve_rate_pct
  from
    {{ source('occto', 'occto_demand_forecast_dad') }}
  )

select * from source
