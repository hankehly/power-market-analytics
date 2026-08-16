with
  staging as (
  select
    *
  from
    {{ ref('stg_tepco__area_demand_generation_actual') }}
  ),

  flagged as (
  select
    *,
    -- TEPCO writes 0 for periods not yet observed. A row where all three
    -- measures are 0 is that sentinel (the archived 2025-06-14 file froze
    -- mid-day: time codes 11-48), not an observation — Tokyo demand is never
    -- 0 — so those measures become null below.
    demand_kwh = 0 and generation_kwh = 0 and wind_solar_generation_kwh = 0 as is_unpublished
  from
    staging
  ),

  final as (
  select
    target_date as delivery_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(target_date as timestamp)) as delivery_datetime,
    case when month(target_date) >= 4 then year(target_date) else year(target_date) - 1 end as fiscal_year,
    -- round(): 13 files in April 2022 carry scientific-notation floats
    -- (1.66919e+07), so raw stores double; every other value is an integer.
    case when not is_unpublished then cast(round(demand_kwh) as bigint) end as demand_kwh,
    case when not is_unpublished then cast(round(generation_kwh) as bigint) end as generation_kwh,
    case when not is_unpublished then cast(round(wind_solar_generation_kwh) as bigint) end as wind_solar_generation_kwh,
    file_updated_at
  from
    flagged
  )

select * from final
