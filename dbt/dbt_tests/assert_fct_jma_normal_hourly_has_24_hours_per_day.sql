-- Every station-day of the hourly fact has its 24 hour endings exactly once.
select
  normals_period_end_year,
  station_id,
  month,
  day_of_month,
  count(*) as n_rows,
  count(distinct hour_ending) as n_hours
from
  {{ ref('fct_jma_normal_hourly') }}
group by
  normals_period_end_year,
  station_id,
  month,
  day_of_month
having
  count(*) <> 24
  or count(distinct hour_ending) <> 24
