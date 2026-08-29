with
  source as (
  select
    target_date,
    hour_start,
    demand_mankw,
    forecast_mankw,
    usage_rate_pct,
    supply_capacity_mankw,
    file_updated_at,
    source_file
  from
    {{ source('tepco', 'tepco_power_usage_hourly') }}
  )

select * from source
