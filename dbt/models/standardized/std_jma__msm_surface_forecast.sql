with
  source as (
  select * from {{ ref('stg_jma__msm_surface_forecast') }}
  ),

  final as (
  select
    station_id,
    timestampadd(hour, 9, to_timestamp(forecast_reference_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
      as forecast_reference_at,
    timestampadd(hour, 9, to_timestamp(forecast_valid_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
      as forecast_valid_at,
    timestampadd(hour, 8, to_timestamp(forecast_valid_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
      as forecast_hour_start_at,
    cast(timestampadd(hour, 8, to_timestamp(forecast_valid_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'")) as date)
      as forecast_date,
    forecast_lead_hours,
    station_latitude,
    station_longitude,
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
    source
  )

select * from final
