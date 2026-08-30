with
  periods as (
  select
    *
  from
    {{ ref('dim_delivery_period') }}
  ),

  -- Shrunken rollup of dim_delivery_period at the hour grain, derived from
  -- the base dimension so the two can never disagree: grouping by the
  -- attributes as well as the hour yields more than 24 rows — and fails the
  -- unique test on hour_of_day — should a day-part boundary ever stop
  -- falling on a full hour.
  final as (
  select
    hour_of_day,
    hour_of_day + 1 as hour_ending,
    lpad(cast(hour_of_day as string), 2, '0') || ':00' as period_start_time,
    lpad(cast(hour_of_day + 1 as string), 2, '0') || ':00' as period_end_time,
    min(time_code) as first_time_code,
    max(time_code) as last_time_code,
    is_daytime,
    day_part
  from
    periods
  group by
    hour_of_day,
    is_daytime,
    day_part
  )

select * from final
