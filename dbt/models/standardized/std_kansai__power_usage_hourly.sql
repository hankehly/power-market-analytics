with
  staging as (
  select
    *
  from
    {{ ref('stg_kansai__power_usage_hourly') }}
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
    cast(round(forecast_mankw) as int) as forecast_mankw,
    cast(round(usage_rate_pct) as int) as usage_rate_pct,
    -- 供給力想定値 (2019-09-12 → 2025-12-24) / 供給力 (2025-12-25 →); the
    -- column does not exist before 2019-09-12.
    cast(round(supply_capacity_mankw) as int) as supply_capacity_mankw,
    file_updated_at,
    source_file,
    -- Public at the daily file's update time, never before the hour ends.
    greatest(file_updated_at, timestampadd(hour, hour_start + 1, cast(target_date as timestamp))) as available_at
  from
    staging
  )

select * from final
