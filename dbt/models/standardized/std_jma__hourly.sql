with
  source as (
  select * from {{ ref('stg_jma__hourly_staffed') }}
  ),

  final as (
  select
    station_id,
    observed_at,
    timestampadd(hour, -1, observed_at) as observed_hour_start_at,
    cast(timestampadd(hour, -1, observed_at) as date) as observed_date,
    case
      when month(timestampadd(hour, -1, observed_at)) >= 4
      then year(timestampadd(hour, -1, observed_at))
      else year(timestampadd(hour, -1, observed_at)) - 1
    end as fiscal_year,
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
    source
  )

select * from final
