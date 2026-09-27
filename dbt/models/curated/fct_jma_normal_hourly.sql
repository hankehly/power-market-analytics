-- fct_jma_normal_hourly: the climatological normal of the temperature at each
-- hour 01–24 of each calendar day, one row per station, day and hour ending —
-- the same hour axis as fct_jma_weather_hourly (hour 24 belongs to the day it
-- ends) and the MSM forecast fact. The normal and the std rows of std share
-- the flag and the years, so each max() sees one value.
with
  normals as (
  select
    *
  from
    {{ ref('std_jma__normal_daily') }}
  where
    element = 'hourly_temperature'
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
    normals.hour_ending,
    max(case when normals.statistic = 'normal' then normals.value end) as temperature_c,
    max(case when normals.statistic = 'std' then normals.value end) as temperature_std_c,
    max(case when normals.statistic = 'normal' then normals.quality_flag end) as temperature_quality_flag,
    max(normals.n_years) as n_years,
    max(normals.statistic_start_year) as statistic_start_year,
    max(normals.statistic_end_year) as statistic_end_year,
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
    normals.day_of_month,
    normals.hour_ending
  )

select * from final
