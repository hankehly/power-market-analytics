with
  standardized as (
  select
    *
  from
    {{ ref('std_jma__hourly') }}
  ),

  final as (
  select
    -- grain: one row per station per observation hour
    station_id,
    observed_at,
    observed_hour_start_at,
    -- date_key references dim_date on the observation day (the hour-start
    -- date), so hour 24:00 (stored as next-day 00:00) stays on the day it
    -- measured; fiscal_year and calendar attributes come from dim_date.
    observed_date as date_key,
    precipitation_mm,
    precipitation_phenomenon_absent,
    precipitation_quality_flag,
    precipitation_homogeneity_no,
    temperature_c,
    temperature_quality_flag,
    temperature_homogeneity_no,
    wind_speed_ms,
    wind_speed_quality_flag,
    wind_direction,
    wind_direction_quality_flag,
    wind_homogeneity_no,
    sunshine_duration_h,
    sunshine_phenomenon_absent,
    sunshine_quality_flag,
    sunshine_homogeneity_no
  from
    standardized
  )

select * from final
