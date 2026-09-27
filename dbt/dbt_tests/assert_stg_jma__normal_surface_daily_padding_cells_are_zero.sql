-- std_jma__normal_daily drops the days a month does not have (the 30th and
-- 31st of February, the 31st of April, June, September and November) by a
-- fixed leap-year calendar. That is safe only while JMA writes 0,0 in those
-- cells, as every file did on 2026-09-27; a value there would be lost with
-- nothing saying so.
with
  source as (
  select * from {{ ref('stg_jma__normal_surface_daily') }}
  )

select
  normals_period_end_year,
  station_number,
  element_code,
  month
from
  source
where
  (month in (4, 6, 9, 11) and (value_d31 <> 0 or flag_d31 <> 0))
  or (
    month = 2
    and (value_d30 <> 0 or flag_d30 <> 0 or value_d31 <> 0 or flag_d31 <> 0)
  )
