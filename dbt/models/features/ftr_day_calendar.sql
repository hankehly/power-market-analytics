with
  days as (
  select * from {{ ref('dim_date') }}
  ),

  -- Every bidding zone; 'system' is the nationwide reference price, not an area.
  areas as (
  select area_code from {{ ref('dim_area') }} where area_code != 'system'
  ),

  holidays as (
  select
    date_key,
    max(case when is_holiday then date_key end)
      over (order by date_key rows between unbounded preceding and current row) as last_holiday,
    min(case when is_holiday then date_key end)
      over (order by date_key rows between current row and unbounded following) as next_holiday
  from days
  ),

  -- The one definition of the day type (research demand/R-003), a step of its own
  -- so the lag days below read it and the rule stays written once.
  day_types as (
  select
    date_key,
    case when is_holiday then 2 when is_weekend then 1 else 0 end as day_type
  from days
  ),

  final as (
  select
    areas.area_code,
    days.date_key as trade_date,
    days.month,
    -- pandas dayofweek: Monday = 0.
    days.day_of_week_iso - 1 as day_of_week,
    day_types.day_type,
    -- The first match wins. The three periods are holiday_degree's (dim_date), by
    -- date, so a weekend or a 祝日 inside one takes the period. 4 is any other
    -- holiday, a 振替休日 on 5/6 included. 5 is a sandwiched working day: no working
    -- day falls inside a period, so a working day's holiday_degree, 0.5 or 0.3,
    -- comes from dim_date's sandwiched rule alone.
    case
      when (days.month = 12 and days.day_of_month >= 30) or (days.month = 1 and days.day_of_month <= 3) then 1
      when (days.month = 4 and days.day_of_month >= 29) or (days.month = 5 and days.day_of_month <= 5) then 2
      when days.month = 8 and days.day_of_month between 13 and 16 then 3
      when days.is_holiday then 4
      when days.is_business_day and days.holiday_degree > 0 then 5
      else 0
    end as special_period,
    -- The day types of the days the load lags read (feature candidate #201), joined
    -- on the date, so a gap in the spine could not shift a value. Null where the
    -- earlier day is before the spine's first day.
    lag_2d.day_type as lag_2d_day_type,
    lag_3d.day_type as lag_3d_day_type,
    lag_7d.day_type as lag_7d_day_type,
    days.holiday_degree,
    days.half,
    days.quarter,
    days.day_of_month,
    days.day_of_quarter,
    days.day_of_year,
    cast(days.is_business_day as int) as is_business_day,
    days.fiscal_quarter,
    datediff(days.date_key, holidays.last_holiday) as days_since_holiday,
    datediff(holidays.next_holiday, days.date_key) as days_until_holiday,
    -- The calendar is static: the holiday seed is published a year ahead.
    timestamp '1900-01-01 00:00:00' as available_at
  from
    days
    inner join holidays on holidays.date_key = days.date_key
    inner join day_types on day_types.date_key = days.date_key
    left join day_types as lag_2d on lag_2d.date_key = date_sub(days.date_key, 2)
    left join day_types as lag_3d on lag_3d.date_key = date_sub(days.date_key, 3)
    left join day_types as lag_7d on lag_7d.date_key = date_sub(days.date_key, 7)
    cross join areas
  )

select * from final
