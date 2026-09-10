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

  final as (
  select
    areas.area_code,
    days.date_key as trade_date,
    days.month,
    -- pandas dayofweek: Monday = 0.
    days.day_of_week_iso - 1 as day_of_week,
    case when days.is_holiday then 2 when days.is_weekend then 1 else 0 end as day_type,
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
    cross join areas
  )

select * from final
