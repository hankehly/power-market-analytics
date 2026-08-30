with
  staging as (
  select
    *
  from
    {{ ref('stg_tepco__power_usage_hourly') }}
  ),

  final as (
  select
    target_date as delivery_date,
    hour_start,
    hour_start + 1 as hour_ending,
    timestampadd(hour, hour_start, cast(target_date as timestamp)) as delivery_datetime,
    case when month(target_date) >= 4 then year(target_date) else year(target_date) - 1 end as fiscal_year,
    -- Published as integer 万kW (1 万kW = 10 MW); raw stores double only
    -- defensively — every loaded value is integral.
    cast(round(demand_mankw) as int) as demand_mankw,
    -- Daily-file columns: null before 2022-04-01, where the yearly files
    -- carry the actual only.
    cast(round(forecast_mankw) as int) as forecast_mankw,
    cast(round(usage_rate_pct) as int) as usage_rate_pct,
    cast(round(supply_capacity_mankw) as int) as supply_capacity_mankw,
    file_updated_at,
    source_file
  from
    staging
  )

select * from final
