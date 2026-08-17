with
  source as (
  select
    target_date,
    time_code,
    period_start_time,
    period_end_time,
    demand_kwh,
    generation_kwh,
    wind_solar_generation_kwh,
    file_updated_at
  from
    {{ source('kansai', 'kansai_area_demand_generation_actual') }}
  )

select * from source
