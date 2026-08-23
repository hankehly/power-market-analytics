with
  standardized as (
  select * from {{ ref('std_jma__msm_surface_forecast') }}
  ),

  final as (
  select
    -- grain: one row per station, forecast run and forecast-valid hour
    station_id,
    forecast_reference_at,
    forecast_valid_at,
    forecast_hour_start_at,
    forecast_lead_hours,
    forecast_date as date_key,
    grid_latitude,
    grid_longitude,
    grid_distance_km,
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
    standardized
  )

select * from final
