with
  holidays as (
  select
    holiday_date,
    holiday_name_ja
  from
    {{ ref('jpn_national_holidays') }}
  ),

  -- Spine runs to the end of the last calendar year covered by the holiday
  -- seed, so refreshing the seed (scripts/update_holidays_seed.py) extends
  -- the calendar automatically and is_holiday is never silently false for
  -- dates beyond holiday coverage.
  spine_bounds as (
  select
    to_date('2016-04-01') as start_date,
    make_date(year(max(holiday_date)), 12, 31) as end_date
  from
    holidays
  ),

  date_spine as (
  select
    explode(sequence(start_date, end_date, interval 1 day)) as date_key
  from
    spine_bounds
  ),

  final as (
  select
    date_spine.date_key,
    year(date_spine.date_key) as year,
    quarter(date_spine.date_key) as quarter,
    month(date_spine.date_key) as month,
    day(date_spine.date_key) as day_of_month,
    weekday(date_spine.date_key) + 1 as day_of_week_iso,
    date_format(date_spine.date_key, 'EEEE') as day_name,
    date_format(date_spine.date_key, 'MMMM') as month_name,
    case when month(date_spine.date_key) >= 4 then year(date_spine.date_key) else year(date_spine.date_key) - 1 end as fiscal_year,
    cast((month(date_spine.date_key) + 8) % 12 div 3 + 1 as int) as fiscal_quarter,
    weekday(date_spine.date_key) >= 5 as is_weekend,
    holidays.holiday_date is not null as is_holiday,
    coalesce(holidays.holiday_name_ja, 'Not Applicable') as holiday_name_ja,
    weekday(date_spine.date_key) < 5 and holidays.holiday_date is null as is_business_day
  from
    date_spine
    left join holidays on date_spine.date_key = holidays.holiday_date
  )

select * from final
