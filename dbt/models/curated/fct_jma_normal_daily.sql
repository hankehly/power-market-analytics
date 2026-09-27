-- fct_jma_normal_daily: the daily climatological normals of a period, one row
-- per station and calendar day (month, day_of_month; 366 per station), wide
-- over the daily elements — the normal and, for the three temperatures, its
-- standard deviation, with one quality flag per element. The hourly
-- temperatures are fct_jma_normal_hourly; the class thresholds and n_years
-- stay in std_jma__normal_daily. A normal has no date: a dated row finds its
-- normal through the month and day of its date. The inner join to
-- dim_jma_station drops the ten stations outside a JEPX area.
with
  normals as (
  select
    *
  from
    {{ ref('std_jma__normal_daily') }}
  where
    element <> 'hourly_temperature'
    and statistic in ('normal', 'std')
  ),

  stations as (
  select station_id from {{ ref('dim_jma_station') }}
  ),

  final as (
  select
    normals.normals_period_end_year,
    normals.station_id,
    normals.month,
    normals.day_of_month,
    -- one row per key and element in `normals`, so each max() sees one value
    max(case when normals.element = 'mean_temperature' and normals.statistic = 'normal' then normals.value end) as mean_temperature_c,
    max(case when normals.element = 'mean_temperature' and normals.statistic = 'std' then normals.value end) as mean_temperature_std_c,
    max(case when normals.element = 'mean_temperature' and normals.statistic = 'normal' then normals.quality_flag end) as mean_temperature_quality_flag,
    max(case when normals.element = 'max_temperature' and normals.statistic = 'normal' then normals.value end) as max_temperature_c,
    max(case when normals.element = 'max_temperature' and normals.statistic = 'std' then normals.value end) as max_temperature_std_c,
    max(case when normals.element = 'max_temperature' and normals.statistic = 'normal' then normals.quality_flag end) as max_temperature_quality_flag,
    max(case when normals.element = 'min_temperature' and normals.statistic = 'normal' then normals.value end) as min_temperature_c,
    max(case when normals.element = 'min_temperature' and normals.statistic = 'std' then normals.value end) as min_temperature_std_c,
    max(case when normals.element = 'min_temperature' and normals.statistic = 'normal' then normals.quality_flag end) as min_temperature_quality_flag,
    max(case when normals.element = 'cloud_cover' then normals.value end) as cloud_cover_tenths,
    max(case when normals.element = 'cloud_cover' then normals.quality_flag end) as cloud_cover_quality_flag,
    max(case when normals.element = 'sunshine_duration' then normals.value end) as sunshine_duration_h,
    max(case when normals.element = 'sunshine_duration' then normals.quality_flag end) as sunshine_duration_quality_flag,
    max(case when normals.element = 'sunshine_rate_ge_40pct' then normals.value end) as prob_sunshine_rate_ge_40pct_pct,
    max(case when normals.element = 'sunshine_rate_ge_40pct' then normals.quality_flag end) as sunshine_rate_ge_40pct_quality_flag,
    max(case when normals.element = 'solar_radiation' then normals.value end) as solar_radiation_mjm2,
    max(case when normals.element = 'solar_radiation' then normals.quality_flag end) as solar_radiation_quality_flag,
    max(case when normals.element = 'precipitation' then normals.value end) as precipitation_mm,
    max(case when normals.element = 'precipitation' then normals.quality_flag end) as precipitation_quality_flag,
    max(case when normals.element = 'precipitation_ge_1mm' then normals.value end) as prob_precipitation_ge_1mm_pct,
    max(case when normals.element = 'precipitation_ge_1mm' then normals.quality_flag end) as precipitation_ge_1mm_quality_flag,
    max(case when normals.element = 'precipitation_ge_10mm' then normals.value end) as prob_precipitation_ge_10mm_pct,
    max(case when normals.element = 'precipitation_ge_10mm' then normals.quality_flag end) as precipitation_ge_10mm_quality_flag,
    max(case when normals.element = 'snowfall' then normals.value end) as snowfall_cm,
    max(case when normals.element = 'snowfall' then normals.quality_flag end) as snowfall_quality_flag,
    max(case when normals.element = 'max_snow_depth' then normals.value end) as max_snow_depth_cm,
    max(case when normals.element = 'max_snow_depth' then normals.quality_flag end) as max_snow_depth_quality_flag,
    max(normals.normals_period_start_year) as normals_period_start_year,
    max(normals.normals_version) as normals_version,
    max(normals.available_at) as available_at
  from
    normals
    inner join stations
      on stations.station_id = normals.station_id
  group by
    normals.normals_period_end_year,
    normals.station_id,
    normals.month,
    normals.day_of_month
  )

select * from final
