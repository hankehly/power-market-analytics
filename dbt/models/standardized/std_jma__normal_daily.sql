-- std_jma__normal_daily: the daily normals unpivoted to one row per station,
-- element, month and day, scaled to the element's unit through the seed
-- jma_normal_elements. The padding cells (days a month does not have) are
-- dropped by a fixed leap-year calendar, so Feb 29 stays whatever the period's
-- end year is; a flag of 0 (no statistic) makes the value null. available_at
-- is the period's first in-use date, the documented bound: only the current
-- version of the archive is served, and each version changed a few stations'
-- files (docs/JMA-Climatological-Normals-Retrieval.md §5).
with
  source as (
  select * from {{ ref('stg_jma__normal_surface_daily') }}
  ),

  elements as (
  select * from {{ ref('jma_normal_elements') }}
  ),

  unpivoted as (
  select
    normals_period_start_year,
    normals_period_end_year,
    normals_version,
    in_use_since,
    station_number,
    element_code,
    n_years,
    statistic_start_year,
    statistic_end_year,
    month,
    stack(
      31,
      {% for d in range(1, 32) -%}
      {{ d }}, value_d{{ '%02d' % d }}, flag_d{{ '%02d' % d }}{{ ',' if not loop.last }}
      {% endfor -%}
    ) as (day_of_month, published_value, quality_flag)
  from
    source
  ),

  calendar_days as (
  -- 2000 is a leap year: the normals carry a Feb 29.
  select
    *
  from
    unpivoted
  where
    day_of_month <= day(last_day(make_date(2000, month, 1)))
  ),

  final as (
  select
    days.normals_period_start_year,
    days.normals_period_end_year,
    days.normals_version,
    concat('s', days.station_number) as station_id,
    days.element_code,
    elements.element,
    elements.statistic,
    elements.hour_ending,
    elements.element_name_ja,
    elements.unit,
    days.month,
    days.day_of_month,
    case
      when days.quality_flag = 0 then null
      else cast(days.published_value as double) / cast(elements.scale_denominator as double)
    end as value,
    days.quality_flag,
    days.quality_flag in (5, 7) as is_reference_only,
    days.n_years,
    nullif(days.statistic_start_year, 0) as statistic_start_year,
    nullif(days.statistic_end_year, 0) as statistic_end_year,
    cast(days.in_use_since as timestamp) as available_at
  from
    calendar_days as days
    left join elements
      on elements.element_code = days.element_code
  )

select * from final
