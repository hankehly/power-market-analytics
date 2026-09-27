-- Every station and period has one row per calendar day of a leap year, Feb 29
-- among them: a padding day that slipped through, or a day the unpivot lost,
-- shows here as a count other than 366.
select
  normals_period_end_year,
  station_id,
  count(*) as n_days,
  sum(case when month = 2 and day_of_month = 29 then 1 else 0 end) as n_feb_29
from
  {{ ref('fct_jma_normal_daily') }}
group by
  normals_period_end_year,
  station_id
having
  count(*) <> 366
  or sum(case when month = 2 and day_of_month = 29 then 1 else 0 end) <> 1
