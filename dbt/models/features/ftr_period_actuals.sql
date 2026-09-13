-- The area's own recent demand for every delivery period: the lags of 2, 3,
-- 7, 9, 14, 21 and 28 days, two means, a least-squares trend, a standard
-- deviation and a median over the weekly lags, D-7's z-score against the
-- three weeks before it, an exponentially weighted mean over D-2 to D-6 and
-- its difference from the weekly one, the two-day-old weekly change and two
-- means over the last four complete days of D's day type. The shifted
-- actuals, grouped per period, are the row spine: a row exists wherever any
-- lag exists and a column is null where its input is absent. available_at
-- is the greatest over the rows a row used.
with
  actuals as (
  select
    areas.area_code,
    actuals.date_key,
    actuals.time_code,
    actuals.demand_kwh,
    actuals.available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    -- A TSO hole has no value: the lag it feeds is null, not a row.
    actuals.demand_kwh is not null
  ),

  lags as (
  -- 4, 5 and 6 feed ewm_5d_demand_kwh only.
  select explode(array(2, 3, 4, 5, 6, 7, 9, 14, 21, 28)) as lag_days
  ),

  -- Every actual shifted to each delivery day it is a lag of.
  shifted as (
  select
    actuals.area_code,
    date_add(actuals.date_key, lags.lag_days) as trade_date,
    actuals.time_code,
    lags.lag_days,
    actuals.demand_kwh,
    actuals.available_at
  from
    actuals
    cross join lags
  ),

  lag_values as (
  select
    area_code,
    trade_date,
    time_code,
    max(case when lag_days = 2 then demand_kwh end) as lag_2d_demand_kwh,
    max(case when lag_days = 3 then demand_kwh end) as lag_3d_demand_kwh,
    max(case when lag_days = 4 then demand_kwh end) as lag_4d_demand_kwh,
    max(case when lag_days = 5 then demand_kwh end) as lag_5d_demand_kwh,
    max(case when lag_days = 6 then demand_kwh end) as lag_6d_demand_kwh,
    max(case when lag_days = 7 then demand_kwh end) as lag_7d_demand_kwh,
    max(case when lag_days = 9 then demand_kwh end) as lag_9d_demand_kwh,
    max(case when lag_days = 14 then demand_kwh end) as lag_14d_demand_kwh,
    max(case when lag_days = 21 then demand_kwh end) as lag_21d_demand_kwh,
    max(case when lag_days = 28 then demand_kwh end) as lag_28d_demand_kwh,
    max(available_at) as available_at
  from
    shifted
  group by
    area_code, trade_date, time_code
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
      + coalesce(lag_28d_demand_kwh * lag_28d_demand_kwh, 0) as prior_weeks_sum_yy
  from
    lag_values
  ),

  -- A complete day has all 48 periods; it is public when its newest row is.
  complete_days as (
  select
    area_code,
    date_key,
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
    candidate_periods.available_at
  from
    lookup
    inner join candidate_periods
      on candidate_periods.area_code = lookup.area_code
      and candidate_periods.date_key = lookup.candidate_date
      and candidate_periods.day_type = lookup.day_type
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
    day_type_windows.mean_daytype_4d_demand_kwh,
    day_type_windows.ewm_daytype_4d_demand_kwh,
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
    {{ available_at(['by_period.available_at', 'day_type_windows.available_at']) }} as available_at
  from
    by_period
    left join day_type_windows
      on day_type_windows.area_code = by_period.area_code
      and day_type_windows.trade_date = by_period.trade_date
      and day_type_windows.time_code = by_period.time_code
  ),

  final as (
  select
    features.*,
    -- The recent weighted level minus the weekly one; null when either is.
    ewm_5d_demand_kwh - ewm_weekly_lags_demand_kwh as ewm_5d_minus_ewm_weekly_lags_demand_kwh
  from
    features
  )

select * from final
