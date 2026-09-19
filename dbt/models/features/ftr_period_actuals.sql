-- The area's own recent demand for every delivery period: the lags of 2, 3,
-- 7, 9, 14, 21 and 28 days, and of 2 and 7 days for the wind and solar
-- generation; two means, a weighted standard deviation, a
-- least-squares trend, a standard deviation, a median and the mean ramp over
-- the weekly lags; D-7's z-score against the three weeks before it; D-7's
-- mean with its neighbouring periods and its ramp from the period before; an
-- exponentially weighted mean, a standard deviation and a weighted standard
-- deviation over D-2 to D-6, and the weighted mean's difference from the
-- weekly one, plain and as a fraction of the weekly one; the two-day-old
-- weekly change, plain and as a fraction of D-9; D-7 minus the weekly median;
-- D-2's and D-7's load over their day's mean; D-2's position between the
-- lowest and highest of D-2 to D-29; and two means over the last four
-- complete days of D's day type, with how many days back the newest and the
-- oldest of those days lie; and two means over the newest four weekly lags,
-- D-7 to D-56, that have D's day type. The shifted actuals, grouped per
-- period, are the row spine: a row exists wherever any lag exists and a
-- column is null where its input is absent. available_at is the greatest
-- over the rows shifted onto the row, the neighbouring periods included.
with
  actuals as (
  select
    areas.area_code,
    actuals.date_key,
    actuals.time_code,
    -- The period's position on the timeline, so a shift of one period
    -- crosses midnight.
    unix_date(actuals.date_key) * 48 + actuals.time_code - 1 as period_index,
    actuals.demand_kwh,
    -- The wind and solar share of generation, for its two lags. Demand alone
    -- makes the rows: a period without demand has no generation either.
    actuals.wind_solar_generation_kwh,
    actuals.available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    -- A TSO hole has no value: the lag it feeds is null, not a row.
    actuals.demand_kwh is not null
  ),

  -- Each actual with the lowest and highest of the same period over the 28
  -- days ending at it, the values present, and the newest availability among
  -- them. Read at D-2 it is the window D-2 to D-29. A window, not more shifts:
  -- the shifts are the row spine, and these 26 extra days must make no row.
  ranges as (
  select
    area_code,
    date_key,
    time_code,
    min(demand_kwh) over last_28_days as min_28d_demand_kwh,
    max(demand_kwh) over last_28_days as max_28d_demand_kwh,
    max(available_at) over last_28_days as available_at
  from
    actuals
  window last_28_days as (
    partition by area_code, time_code
    order by unix_date(date_key)
    range between 27 preceding and current row
  )
  ),

  -- Where each actual lands: 'at' the same period lag_days later; 'before'
  -- the period after that, so it is the t-1 of D-lag_days; 'after' the
  -- period before, so it is the t+1 of D-7. 4, 5 and 6 feed the D-2 to D-6
  -- statistics only.
  shifts as (
  select * from values
    ('at', 2, 0), ('at', 3, 0), ('at', 4, 0), ('at', 5, 0), ('at', 6, 0),
    ('at', 7, 0), ('at', 9, 0), ('at', 14, 0), ('at', 21, 0), ('at', 28, 0),
    ('before', 7, 1), ('before', 14, 1), ('before', 21, 1), ('before', 28, 1),
    ('after', 7, -1)
    as shifts(kind, lag_days, period_shift)
  ),

  -- Every actual shifted to each delivery period it feeds.
  shifted as (
  select
    area_code,
    date_from_unix_date(cast(div(target_index, 48) as int)) as trade_date,
    cast(target_index % 48 + 1 as int) as time_code,
    kind,
    lag_days,
    demand_kwh,
    wind_solar_generation_kwh,
    available_at
  from (
    select
      actuals.area_code,
      actuals.period_index + shifts.lag_days * 48 + shifts.period_shift as target_index,
      shifts.kind,
      shifts.lag_days,
      actuals.demand_kwh,
      actuals.wind_solar_generation_kwh,
      actuals.available_at
    from
      actuals
      cross join shifts
  )
  ),

  lag_values as (
  select
    area_code,
    trade_date,
    time_code,
    max(case when kind = 'at' and lag_days = 2 then demand_kwh end) as lag_2d_demand_kwh,
    max(case when kind = 'at' and lag_days = 3 then demand_kwh end) as lag_3d_demand_kwh,
    max(case when kind = 'at' and lag_days = 4 then demand_kwh end) as lag_4d_demand_kwh,
    max(case when kind = 'at' and lag_days = 5 then demand_kwh end) as lag_5d_demand_kwh,
    max(case when kind = 'at' and lag_days = 6 then demand_kwh end) as lag_6d_demand_kwh,
    max(case when kind = 'at' and lag_days = 7 then demand_kwh end) as lag_7d_demand_kwh,
    max(case when kind = 'at' and lag_days = 9 then demand_kwh end) as lag_9d_demand_kwh,
    max(case when kind = 'at' and lag_days = 14 then demand_kwh end) as lag_14d_demand_kwh,
    max(case when kind = 'at' and lag_days = 21 then demand_kwh end) as lag_21d_demand_kwh,
    max(case when kind = 'at' and lag_days = 28 then demand_kwh end) as lag_28d_demand_kwh,
    max(case when kind = 'before' and lag_days = 7 then demand_kwh end) as before_7d_demand_kwh,
    max(case when kind = 'before' and lag_days = 14 then demand_kwh end) as before_14d_demand_kwh,
    max(case when kind = 'before' and lag_days = 21 then demand_kwh end) as before_21d_demand_kwh,
    max(case when kind = 'before' and lag_days = 28 then demand_kwh end) as before_28d_demand_kwh,
    max(case when kind = 'after' and lag_days = 7 then demand_kwh end) as after_7d_demand_kwh,
    max(case when kind = 'at' and lag_days = 2 then wind_solar_generation_kwh end) as lag_2d_wind_solar_generation_kwh,
    max(case when kind = 'at' and lag_days = 7 then wind_solar_generation_kwh end) as lag_7d_wind_solar_generation_kwh,
    max(available_at) as available_at
  from
    shifted
  group by
    area_code, trade_date, time_code
  having
    -- A neighbour alone makes no row.
    max(case when kind = 'at' then 1 end) = 1
  ),

  -- Integer sums over the weekly lags present, against time in weeks for the
  -- trend: D-7 at x = -1, D-14 at -2, D-21 at -3, D-28 at -4. The trend and
  -- the variance in final are each one exact division; the median reads the
  -- present lags sorted.
  by_period as (
  select
    lag_values.*,
    cast(lag_7d_demand_kwh is not null as int)
      + cast(lag_14d_demand_kwh is not null as int)
      + cast(lag_21d_demand_kwh is not null as int)
      + cast(lag_28d_demand_kwh is not null as int) as weekly_n,
    -(cast(lag_7d_demand_kwh is not null as int)
      + 2 * cast(lag_14d_demand_kwh is not null as int)
      + 3 * cast(lag_21d_demand_kwh is not null as int)
      + 4 * cast(lag_28d_demand_kwh is not null as int)) as weekly_sum_x,
    cast(lag_7d_demand_kwh is not null as int)
      + 4 * cast(lag_14d_demand_kwh is not null as int)
      + 9 * cast(lag_21d_demand_kwh is not null as int)
      + 16 * cast(lag_28d_demand_kwh is not null as int) as weekly_sum_xx,
    coalesce(lag_7d_demand_kwh, 0)
      + coalesce(lag_14d_demand_kwh, 0)
      + coalesce(lag_21d_demand_kwh, 0)
      + coalesce(lag_28d_demand_kwh, 0) as weekly_sum_y,
    -(coalesce(lag_7d_demand_kwh, 0)
      + coalesce(2 * lag_14d_demand_kwh, 0)
      + coalesce(3 * lag_21d_demand_kwh, 0)
      + coalesce(4 * lag_28d_demand_kwh, 0)) as weekly_sum_xy,
    coalesce(lag_7d_demand_kwh * lag_7d_demand_kwh, 0)
      + coalesce(lag_14d_demand_kwh * lag_14d_demand_kwh, 0)
      + coalesce(lag_21d_demand_kwh * lag_21d_demand_kwh, 0)
      + coalesce(lag_28d_demand_kwh * lag_28d_demand_kwh, 0) as weekly_sum_yy,
    array_sort(filter(
      array(lag_7d_demand_kwh, lag_14d_demand_kwh, lag_21d_demand_kwh, lag_28d_demand_kwh),
      v -> v is not null)) as weekly_sorted,
    -- The same sums over the three weeks before D-7 (D-14, D-21, D-28), the
    -- reference of the D-7 z-score.
    cast(lag_14d_demand_kwh is not null as int)
      + cast(lag_21d_demand_kwh is not null as int)
      + cast(lag_28d_demand_kwh is not null as int) as prior_weeks_n,
    coalesce(lag_14d_demand_kwh, 0)
      + coalesce(lag_21d_demand_kwh, 0)
      + coalesce(lag_28d_demand_kwh, 0) as prior_weeks_sum_y,
    coalesce(lag_14d_demand_kwh * lag_14d_demand_kwh, 0)
      + coalesce(lag_21d_demand_kwh * lag_21d_demand_kwh, 0)
      + coalesce(lag_28d_demand_kwh * lag_28d_demand_kwh, 0) as prior_weeks_sum_yy,
    -- The same sums over D-2 to D-6, and with the exponential weights (16, 8,
    -- 4, 2, 1 and 8, 4, 2, 1) for the weighted standard deviations: Sw, Sww,
    -- Swy and Swyy.
    cast(lag_2d_demand_kwh is not null as int)
      + cast(lag_3d_demand_kwh is not null as int)
      + cast(lag_4d_demand_kwh is not null as int)
      + cast(lag_5d_demand_kwh is not null as int)
      + cast(lag_6d_demand_kwh is not null as int) as recent_n,
    coalesce(lag_2d_demand_kwh, 0)
      + coalesce(lag_3d_demand_kwh, 0)
      + coalesce(lag_4d_demand_kwh, 0)
      + coalesce(lag_5d_demand_kwh, 0)
      + coalesce(lag_6d_demand_kwh, 0) as recent_sum_y,
    coalesce(lag_2d_demand_kwh * lag_2d_demand_kwh, 0)
      + coalesce(lag_3d_demand_kwh * lag_3d_demand_kwh, 0)
      + coalesce(lag_4d_demand_kwh * lag_4d_demand_kwh, 0)
      + coalesce(lag_5d_demand_kwh * lag_5d_demand_kwh, 0)
      + coalesce(lag_6d_demand_kwh * lag_6d_demand_kwh, 0) as recent_sum_yy,
    16 * cast(lag_2d_demand_kwh is not null as int)
      + 8 * cast(lag_3d_demand_kwh is not null as int)
      + 4 * cast(lag_4d_demand_kwh is not null as int)
      + 2 * cast(lag_5d_demand_kwh is not null as int)
      + cast(lag_6d_demand_kwh is not null as int) as recent_sum_w,
    256 * cast(lag_2d_demand_kwh is not null as int)
      + 64 * cast(lag_3d_demand_kwh is not null as int)
      + 16 * cast(lag_4d_demand_kwh is not null as int)
      + 4 * cast(lag_5d_demand_kwh is not null as int)
      + cast(lag_6d_demand_kwh is not null as int) as recent_sum_ww,
    coalesce(16 * lag_2d_demand_kwh, 0)
      + coalesce(8 * lag_3d_demand_kwh, 0)
      + coalesce(4 * lag_4d_demand_kwh, 0)
      + coalesce(2 * lag_5d_demand_kwh, 0)
      + coalesce(lag_6d_demand_kwh, 0) as recent_sum_wy,
    coalesce(16 * lag_2d_demand_kwh * lag_2d_demand_kwh, 0)
      + coalesce(8 * lag_3d_demand_kwh * lag_3d_demand_kwh, 0)
      + coalesce(4 * lag_4d_demand_kwh * lag_4d_demand_kwh, 0)
      + coalesce(2 * lag_5d_demand_kwh * lag_5d_demand_kwh, 0)
      + coalesce(lag_6d_demand_kwh * lag_6d_demand_kwh, 0) as recent_sum_wyy,
    8 * cast(lag_7d_demand_kwh is not null as int)
      + 4 * cast(lag_14d_demand_kwh is not null as int)
      + 2 * cast(lag_21d_demand_kwh is not null as int)
      + cast(lag_28d_demand_kwh is not null as int) as weekly_sum_w,
    64 * cast(lag_7d_demand_kwh is not null as int)
      + 16 * cast(lag_14d_demand_kwh is not null as int)
      + 4 * cast(lag_21d_demand_kwh is not null as int)
      + cast(lag_28d_demand_kwh is not null as int) as weekly_sum_ww,
    coalesce(8 * lag_7d_demand_kwh, 0)
      + coalesce(4 * lag_14d_demand_kwh, 0)
      + coalesce(2 * lag_21d_demand_kwh, 0)
      + coalesce(lag_28d_demand_kwh, 0) as weekly_sum_wy,
    coalesce(8 * lag_7d_demand_kwh * lag_7d_demand_kwh, 0)
      + coalesce(4 * lag_14d_demand_kwh * lag_14d_demand_kwh, 0)
      + coalesce(2 * lag_21d_demand_kwh * lag_21d_demand_kwh, 0)
      + coalesce(lag_28d_demand_kwh * lag_28d_demand_kwh, 0) as weekly_sum_wyy,
    -- The period-to-period ramps of the weekly lags: D-k at t minus D-k at
    -- t-1; null when either is absent.
    lag_7d_demand_kwh - before_7d_demand_kwh as ramp_7d_demand_kwh,
    lag_14d_demand_kwh - before_14d_demand_kwh as ramp_14d_demand_kwh,
    lag_21d_demand_kwh - before_21d_demand_kwh as ramp_21d_demand_kwh,
    lag_28d_demand_kwh - before_28d_demand_kwh as ramp_28d_demand_kwh
  from
    lag_values
  ),

  -- A complete day has all 48 periods; it is public when its newest row is.
  -- Its sum is exact: demand is a whole number of kWh.
  complete_days as (
  select
    area_code,
    date_key,
    sum(demand_kwh) as day_sum_demand_kwh,
    max(available_at) as available_at
  from
    actuals
  group by
    area_code, date_key
  having
    count(*) = 48
  ),

  -- The one definition of the day type (research demand/R-003).
  day_types as (
  select area_code, trade_date, day_type
  from {{ ref('ftr_day_calendar') }}
  ),

  -- Every period of every complete day with the same period of the three
  -- previous complete days of the same day type: the last four such days
  -- ending at the candidate, and the newest availability among them.
  candidate_periods as (
  select
    actuals.area_code,
    actuals.date_key,
    day_types.day_type,
    actuals.time_code,
    actuals.demand_kwh as value_0,
    lag(actuals.demand_kwh, 1) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as value_1,
    lag(actuals.demand_kwh, 2) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as value_2,
    lag(actuals.demand_kwh, 3) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as value_3,
    -- The oldest day of the four, or the oldest present when there are fewer: a
    -- predecessor's date is null exactly where its load is.
    coalesce(
      lag(actuals.date_key, 3) over (
        partition by actuals.area_code, day_types.day_type, actuals.time_code
        order by actuals.date_key
      ),
      lag(actuals.date_key, 2) over (
        partition by actuals.area_code, day_types.day_type, actuals.time_code
        order by actuals.date_key
      ),
      lag(actuals.date_key, 1) over (
        partition by actuals.area_code, day_types.day_type, actuals.time_code
        order by actuals.date_key
      ),
      actuals.date_key
    ) as oldest_date_key,
    max(complete_days.available_at) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
      rows between 3 preceding and current row
    ) as available_at
  from
    actuals
    inner join complete_days
      on complete_days.area_code = actuals.area_code
      and complete_days.date_key = actuals.date_key
    inner join day_types
      on day_types.area_code = actuals.area_code
      and day_types.trade_date = actuals.date_key
  ),

  -- The delivery days the mart has, with their day type, and every complete
  -- day at the first delivery day it can serve (two days later): one spine,
  -- so a window finds the newest candidate at or before D-2.
  spine as (
  select
    targets.area_code,
    day_types.day_type,
    targets.trade_date as at_date,
    cast(null as date) as candidate_date,
    1 as is_target
  from
    (select distinct area_code, trade_date from by_period) as targets
    inner join day_types
      on day_types.area_code = targets.area_code
      and day_types.trade_date = targets.trade_date
  union all
  select
    complete_days.area_code,
    day_types.day_type,
    date_add(complete_days.date_key, 2) as at_date,
    complete_days.date_key as candidate_date,
    0 as is_target
  from
    complete_days
    inner join day_types
      on day_types.area_code = complete_days.area_code
      and day_types.trade_date = complete_days.date_key
  ),

  lookup as (
  select
    area_code,
    at_date as trade_date,
    day_type,
    candidate_date
  from (
    select
      area_code,
      day_type,
      at_date,
      is_target,
      -- A candidate sorts before a target of the same date, so D-2 counts.
      last_value(candidate_date, true) over (
        partition by area_code, day_type
        order by at_date, is_target
        rows between unbounded preceding and current row
      ) as candidate_date
    from
      spine
  )
  where
    is_target = 1
    and candidate_date is not null
  ),

  day_type_windows as (
  select
    lookup.area_code,
    lookup.trade_date,
    candidate_periods.time_code,
    -- The days present, added in a fixed order: the newest first.
    (candidate_periods.value_0
      + coalesce(candidate_periods.value_1, 0)
      + coalesce(candidate_periods.value_2, 0)
      + coalesce(candidate_periods.value_3, 0))
    / (1
      + cast(candidate_periods.value_1 is not null as int)
      + cast(candidate_periods.value_2 is not null as int)
      + cast(candidate_periods.value_3 is not null as int)) as mean_daytype_4d_demand_kwh,
    -- Weights 8, 4, 2, 1 from the newest day back, over the days present.
    (8 * candidate_periods.value_0
      + coalesce(4 * candidate_periods.value_1, 0)
      + coalesce(2 * candidate_periods.value_2, 0)
      + coalesce(candidate_periods.value_3, 0))
    / (8
      + 4 * cast(candidate_periods.value_1 is not null as int)
      + 2 * cast(candidate_periods.value_2 is not null as int)
      + cast(candidate_periods.value_3 is not null as int)) as ewm_daytype_4d_demand_kwh,
    -- How far back the window reaches (feature candidate #203): the days from D to
    -- its newest day, the candidate, and to its oldest. Every candidate is a
    -- complete day, so the 48 periods of a day read the same days and agree.
    datediff(lookup.trade_date, candidate_periods.date_key) as newest_daytype_4d_lag_days,
    datediff(lookup.trade_date, candidate_periods.oldest_date_key) as oldest_daytype_4d_lag_days,
    candidate_periods.available_at
  from
    lookup
    inner join candidate_periods
      on candidate_periods.area_code = lookup.area_code
      and candidate_periods.date_key = lookup.candidate_date
      and candidate_periods.day_type = lookup.day_type
  ),

  -- A day x period spine per area with no gap, from its first actual to the
  -- mart's last row, 28 days past the last actual, with the demand where there is
  -- one and the day's type. The day-type weekly lags (feature candidate #202)
  -- read it with lag() over rows, which is exact because no day is missing. A
  -- window read at D, not more shifts: the shifts are the row spine and the
  -- inputs of available_at, and these eight weeks must make no row.
  area_bounds as (
  select
    area_code,
    min(date_key) as first_day,
    max(date_key) as last_day
  from
    actuals
  group by
    area_code
  ),

  area_days as (
  select
    area_code,
    explode(sequence(first_day, date_add(last_day, 28))) as date_key
  from
    area_bounds
  ),

  day_periods as (
  select
    area_days.area_code,
    area_days.date_key,
    periods.time_code,
    day_types.day_type,
    actuals.demand_kwh,
    actuals.available_at
  from
    area_days
    cross join (select explode(sequence(1, 48)) as time_code) as periods
    left join day_types
      on day_types.area_code = area_days.area_code
      and day_types.trade_date = area_days.date_key
    left join actuals
      on actuals.area_code = area_days.area_code
      and actuals.date_key = area_days.date_key
      and actuals.time_code = periods.time_code
  ),

  -- The eight weekly lags D-7 to D-56 of every spine period, newest first, each
  -- with its day's type and its availability.
  weekly_candidates as (
  select
    area_code,
    date_key as trade_date,
    time_code,
    day_type,
    array(
      {%- for weeks in range(1, 9) %}
      named_struct(
        'lag_demand_kwh', lag(demand_kwh, {{ 7 * weeks }}) over same_period,
        'lag_day_type', lag(day_type, {{ 7 * weeks }}) over same_period,
        'lag_available_at', lag(available_at, {{ 7 * weeks }}) over same_period
      ){{ "," if not loop.last }}
      {%- endfor %}
    ) as weekly_lags
  from
    day_periods
  window same_period as (partition by area_code, time_code order by date_key)
  ),

  -- The first four of them that have D's day type and a value at the period:
  -- the values present, not complete days, as the plain weekly means read them,
  -- so where D-7 to D-28 are all present with D's day type these equal the plain
  -- ones. Integer sums and one division each; the weights 8, 4, 2, 1 go by order
  -- of use. Null when none is used, and when D has no calendar row.
  day_type_weekly_lags as (
  select
    area_code,
    trade_date,
    time_code,
    aggregate(used, cast(0 as bigint), (total, lag) -> total + lag.lag_demand_kwh)
      / nullif(size(used), 0) as mean_daytype_weekly_lags_demand_kwh,
    aggregate(
      transform(used, (lag, i) -> element_at(array(8L, 4L, 2L, 1L), i + 1) * lag.lag_demand_kwh),
      cast(0 as bigint), (total, term) -> total + term)
    / nullif(
      aggregate(
        transform(used, (lag, i) -> element_at(array(8L, 4L, 2L, 1L), i + 1)),
        cast(0 as bigint), (total, weight) -> total + weight), 0) as ewm_daytype_weekly_lags_demand_kwh,
    array_max(transform(used, lag -> lag.lag_available_at)) as available_at
  from (
    select
      area_code,
      trade_date,
      time_code,
      slice(
        filter(weekly_lags, lag -> lag.lag_demand_kwh is not null and lag.lag_day_type = day_type),
        1, 4) as used
    from
      weekly_candidates
  )
  ),

  features as (
  select
    by_period.area_code,
    by_period.trade_date,
    by_period.time_code,
    by_period.lag_2d_demand_kwh,
    by_period.lag_3d_demand_kwh,
    by_period.lag_7d_demand_kwh,
    by_period.lag_9d_demand_kwh,
    by_period.lag_14d_demand_kwh,
    by_period.lag_21d_demand_kwh,
    by_period.lag_28d_demand_kwh,
    by_period.lag_2d_wind_solar_generation_kwh,
    by_period.lag_7d_wind_solar_generation_kwh,
    -- The weekly lags present, added in a fixed order; null when none is.
    (coalesce(by_period.lag_7d_demand_kwh, 0)
      + coalesce(by_period.lag_14d_demand_kwh, 0)
      + coalesce(by_period.lag_21d_demand_kwh, 0)
      + coalesce(by_period.lag_28d_demand_kwh, 0))
    / nullif(
      cast(by_period.lag_7d_demand_kwh is not null as int)
      + cast(by_period.lag_14d_demand_kwh is not null as int)
      + cast(by_period.lag_21d_demand_kwh is not null as int)
      + cast(by_period.lag_28d_demand_kwh is not null as int), 0) as mean_weekly_lags_demand_kwh,
    -- Weights 8, 4, 2, 1 for D-7, D-14, D-21, D-28, over the lags present.
    (coalesce(8 * by_period.lag_7d_demand_kwh, 0)
      + coalesce(4 * by_period.lag_14d_demand_kwh, 0)
      + coalesce(2 * by_period.lag_21d_demand_kwh, 0)
      + coalesce(by_period.lag_28d_demand_kwh, 0))
    / nullif(
      8 * cast(by_period.lag_7d_demand_kwh is not null as int)
      + 4 * cast(by_period.lag_14d_demand_kwh is not null as int)
      + 2 * cast(by_period.lag_21d_demand_kwh is not null as int)
      + cast(by_period.lag_28d_demand_kwh is not null as int), 0) as ewm_weekly_lags_demand_kwh,
    -- The weighted standard deviation with the reliability-weight correction
    -- (pandas ewm().std()): sqrt((V1 Swyy - Swy^2) / (V1^2 - V2)), V1 = Sw and
    -- V2 = Sww over the lags present. The denominator is 0 with fewer than
    -- two lags, so the value is null.
    sqrt(
      (by_period.weekly_sum_w * by_period.weekly_sum_wyy - by_period.weekly_sum_wy * by_period.weekly_sum_wy)
      / nullif(by_period.weekly_sum_w * by_period.weekly_sum_w - by_period.weekly_sum_ww, 0))
      as ewstd_weekly_lags_demand_kwh,
    -- The slope, kWh per week: (n Sxy - Sx Sy) / (n Sxx - Sx^2). The
    -- denominator is 0 with fewer than two lags, so the value is null.
    (by_period.weekly_n * by_period.weekly_sum_xy - by_period.weekly_sum_x * by_period.weekly_sum_y)
    / nullif(
      by_period.weekly_n * by_period.weekly_sum_xx - by_period.weekly_sum_x * by_period.weekly_sum_x, 0)
      as trend_weekly_lags_demand_kwh,
    -- The sample standard deviation: sqrt((n Syy - Sy^2) / (n (n - 1))), null
    -- with fewer than two lags.
    sqrt(
      (by_period.weekly_n * by_period.weekly_sum_yy - by_period.weekly_sum_y * by_period.weekly_sum_y)
      / nullif(by_period.weekly_n * (by_period.weekly_n - 1), 0)) as std_weekly_lags_demand_kwh,
    -- The middle lag, or the mean of the middle two; null when none is present.
    case when by_period.weekly_n > 0 then
      (element_at(by_period.weekly_sorted, cast(div(by_period.weekly_n + 1, 2) as int))
        + element_at(by_period.weekly_sorted, cast(div(by_period.weekly_n + 2, 2) as int))) / 2
    end as median_weekly_lags_demand_kwh,
    -- D-7 against the three weeks before it: (D-7 - mean) / sample std, as
    -- (n D-7 - Sy) / (n sqrt((n Syy - Sy^2) / (n (n - 1)))). Null without
    -- D-7, with fewer than two prior weeks, or when their spread is 0.
    (by_period.prior_weeks_n * by_period.lag_7d_demand_kwh - by_period.prior_weeks_sum_y)
    / (by_period.prior_weeks_n * sqrt(
      nullif(
        by_period.prior_weeks_n * by_period.prior_weeks_sum_yy
        - by_period.prior_weeks_sum_y * by_period.prior_weeks_sum_y, 0)
      / nullif(by_period.prior_weeks_n * (by_period.prior_weeks_n - 1), 0)))
      as zscore_7d_vs_14d_28d_demand_kwh,
    by_period.lag_2d_demand_kwh - by_period.lag_9d_demand_kwh as change_2d_9d_demand_kwh,
    -- D-7 at t-1, t and t+1, the values present; null when none is.
    (coalesce(by_period.before_7d_demand_kwh, 0)
      + coalesce(by_period.lag_7d_demand_kwh, 0)
      + coalesce(by_period.after_7d_demand_kwh, 0))
    / nullif(
      cast(by_period.before_7d_demand_kwh is not null as int)
      + cast(by_period.lag_7d_demand_kwh is not null as int)
      + cast(by_period.after_7d_demand_kwh is not null as int), 0) as lag_7d_adjacent_mean_demand_kwh,
    by_period.ramp_7d_demand_kwh as lag_7d_ramp_demand_kwh,
    -- The weekly ramps present; null when none is.
    (coalesce(by_period.ramp_7d_demand_kwh, 0)
      + coalesce(by_period.ramp_14d_demand_kwh, 0)
      + coalesce(by_period.ramp_21d_demand_kwh, 0)
      + coalesce(by_period.ramp_28d_demand_kwh, 0))
    / nullif(
      cast(by_period.ramp_7d_demand_kwh is not null as int)
      + cast(by_period.ramp_14d_demand_kwh is not null as int)
      + cast(by_period.ramp_21d_demand_kwh is not null as int)
      + cast(by_period.ramp_28d_demand_kwh is not null as int), 0) as mean_weekly_lags_ramp_demand_kwh,
    day_type_windows.mean_daytype_4d_demand_kwh,
    day_type_windows.ewm_daytype_4d_demand_kwh,
    day_type_windows.newest_daytype_4d_lag_days,
    day_type_windows.oldest_daytype_4d_lag_days,
    day_type_weekly_lags.mean_daytype_weekly_lags_demand_kwh,
    day_type_weekly_lags.ewm_daytype_weekly_lags_demand_kwh,
    -- Weights 16, 8, 4, 2, 1 for D-2 to D-6, over the lags present.
    (coalesce(16 * by_period.lag_2d_demand_kwh, 0)
      + coalesce(8 * by_period.lag_3d_demand_kwh, 0)
      + coalesce(4 * by_period.lag_4d_demand_kwh, 0)
      + coalesce(2 * by_period.lag_5d_demand_kwh, 0)
      + coalesce(by_period.lag_6d_demand_kwh, 0))
    / nullif(
      16 * cast(by_period.lag_2d_demand_kwh is not null as int)
      + 8 * cast(by_period.lag_3d_demand_kwh is not null as int)
      + 4 * cast(by_period.lag_4d_demand_kwh is not null as int)
      + 2 * cast(by_period.lag_5d_demand_kwh is not null as int)
      + cast(by_period.lag_6d_demand_kwh is not null as int), 0) as ewm_5d_demand_kwh,
    -- The sample standard deviation of D-2 to D-6: sqrt((n Syy - Sy^2) /
    -- (n (n - 1))), null with fewer than two lags.
    sqrt(
      (by_period.recent_n * by_period.recent_sum_yy - by_period.recent_sum_y * by_period.recent_sum_y)
      / nullif(by_period.recent_n * (by_period.recent_n - 1), 0)) as std_5d_demand_kwh,
    -- The same weighted standard deviation as the weekly one, over D-2 to D-6.
    sqrt(
      (by_period.recent_sum_w * by_period.recent_sum_wyy - by_period.recent_sum_wy * by_period.recent_sum_wy)
      / nullif(by_period.recent_sum_w * by_period.recent_sum_w - by_period.recent_sum_ww, 0))
      as ewstd_5d_demand_kwh,
    -- The load over its day's mean, as one division so the integers stay
    -- exact: 48 x lag / the day's sum. Null unless the day is complete.
    48 * by_period.lag_2d_demand_kwh / nullif(day_2d.day_sum_demand_kwh, 0) as lag_2d_over_daily_mean_demand,
    48 * by_period.lag_7d_demand_kwh / nullif(day_7d.day_sum_demand_kwh, 0) as lag_7d_over_daily_mean_demand,
    -- D-2 between the lowest and highest of D-2 to D-29: 0 to 1, D-2 being
    -- inside the window. Null without D-2 or when the two are equal.
    (by_period.lag_2d_demand_kwh - ranges.min_28d_demand_kwh)
    / nullif(ranges.max_28d_demand_kwh - ranges.min_28d_demand_kwh, 0) as lag_2d_position_28d_demand,
    -- The window and the two days are inputs of the row, so it waits for them.
    {{ available_at([
      'by_period.available_at', 'day_type_windows.available_at',
      'day_type_weekly_lags.available_at',
      'ranges.available_at', 'day_2d.available_at', 'day_7d.available_at',
    ]) }} as available_at
  from
    by_period
    left join day_type_windows
      on day_type_windows.area_code = by_period.area_code
      and day_type_windows.trade_date = by_period.trade_date
      and day_type_windows.time_code = by_period.time_code
    left join day_type_weekly_lags
      on day_type_weekly_lags.area_code = by_period.area_code
      and day_type_weekly_lags.trade_date = by_period.trade_date
      and day_type_weekly_lags.time_code = by_period.time_code
    left join ranges
      on ranges.area_code = by_period.area_code
      and ranges.date_key = date_sub(by_period.trade_date, 2)
      and ranges.time_code = by_period.time_code
    left join complete_days as day_2d
      on day_2d.area_code = by_period.area_code
      and day_2d.date_key = date_sub(by_period.trade_date, 2)
    left join complete_days as day_7d
      on day_7d.area_code = by_period.area_code
      and day_7d.date_key = date_sub(by_period.trade_date, 7)
  ),

  final as (
  select
    features.*,
    -- The recent weighted level minus the weekly one; null when either is.
    ewm_5d_demand_kwh - ewm_weekly_lags_demand_kwh as ewm_5d_minus_ewm_weekly_lags_demand_kwh,
    -- The same difference as a fraction of the weekly level, and the weekly
    -- change as a fraction of D-9: fractions, not percentages. A zero
    -- denominator gives null.
    (ewm_5d_demand_kwh - ewm_weekly_lags_demand_kwh) / nullif(ewm_weekly_lags_demand_kwh, 0)
      as rel_ewm_5d_minus_ewm_weekly_lags_demand,
    change_2d_9d_demand_kwh / nullif(lag_9d_demand_kwh, 0) as rel_change_2d_9d_demand,
    -- D-7 against the middle of the weekly lags present, itself among them.
    lag_7d_demand_kwh - median_weekly_lags_demand_kwh as lag_7d_minus_median_weekly_lags_demand_kwh
  from
    features
  )

select * from final
