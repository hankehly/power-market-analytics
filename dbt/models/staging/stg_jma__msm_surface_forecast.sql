with
  source as (
  select
    station_id,
    station_latitude,
    station_longitude,
    grid_latitude,
    grid_longitude,
    grid_distance_km,
    forecast_reference_at_utc,
    forecast_valid_at_utc,
    forecast_lead_hours,
    temperature_c,
    relative_humidity_pct,
    u_wind_ms,
    v_wind_ms,
    wind_speed_ms,
    precipitation_mm,
    surface_pressure_hpa,
    sea_level_pressure_hpa,
    shortwave_radiation_wm2,
    solar_radiation_mjm2,
    total_cloud_cover_pct,
    high_cloud_cover_pct,
    middle_cloud_cover_pct,
    low_cloud_cover_pct,
    source_file_name
  from
    {{ source('jma', 'jma_msm_surface_forecast') }}
  )

select * from source
