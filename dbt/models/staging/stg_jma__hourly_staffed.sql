with
  source as (
  select
    station_id,
    observed_at,
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
    sunshine_homogeneity_no,
    snow_depth_cm,
    snow_depth_phenomenon_absent,
    snow_depth_quality_flag,
    snow_depth_homogeneity_no,
    humidity_pct,
    humidity_quality_flag,
    humidity_homogeneity_no,
    solar_radiation_mjm2,
    solar_radiation_quality_flag,
    solar_radiation_homogeneity_no
  from
    {{ source('jma', 'jma_hourly_staffed') }}
  )

select * from source
