with
  staging as (
  select
    *
  from
    {{ ref('stg_kansai__area_demand_generation_actual') }}
  ),

  final as (
  select
    target_date as delivery_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(target_date as timestamp)) as delivery_datetime,
    case when month(target_date) >= 4 then year(target_date) else year(target_date) - 1 end as fiscal_year,
    -- Kansai publishes full-precision integer kWh and leaves cells it could
    -- not observe blank (null in raw), so the measures pass through as-is;
    -- no TEPCO-style all-zero sentinel exists in this feed.
    demand_kwh,
    generation_kwh,
    wind_solar_generation_kwh,
    file_updated_at
  from
    staging
  )

select * from final
