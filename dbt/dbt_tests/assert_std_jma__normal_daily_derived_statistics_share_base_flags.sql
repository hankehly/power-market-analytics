-- A standard-deviation or class-threshold row carries its base element's
-- flag, year count and statistic years on every cell of every file (measured
-- on 2026-09-27: 0 mismatches over 4.7 M cells), which is why the facts carry
-- one flag per measure. A derived row without a base row, or one whose flag
-- or years differ, fails here.
with
  normals as (
  select * from {{ ref('std_jma__normal_daily') }}
  ),

  derived as (
  select * from normals where statistic <> 'normal'
  ),

  base as (
  select * from normals where statistic = 'normal'
  )

select
  derived.normals_period_end_year,
  derived.station_id,
  derived.element_code,
  derived.month,
  derived.day_of_month
from
  derived
  left join base
    on base.normals_period_end_year = derived.normals_period_end_year
    and base.station_id = derived.station_id
    and base.element = derived.element
    and base.hour_ending <=> derived.hour_ending
    and base.month = derived.month
    and base.day_of_month = derived.day_of_month
where
  base.element_code is null
  or base.quality_flag <> derived.quality_flag
  or base.n_years <> derived.n_years
  or not (base.statistic_start_year <=> derived.statistic_start_year)
  or not (base.statistic_end_year <=> derived.statistic_end_year)
