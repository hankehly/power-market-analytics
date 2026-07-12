with
  -- Spine end matches the coverage of the jpn_national_holidays seed
  -- (Cabinet Office publishes through the end of the next calendar year).
  -- Extend after refreshing the seed with scripts/update_holidays_seed.py.
  date_spine as (
  select
    explode(sequence(to_date('2016-04-01'), to_date('2027-12-31'), interval 1 day)) as date_key
  ),

  holidays as (
  select
    holiday_date,
    holiday_name_ja
  from
    {{ ref('jpn_national_holidays') }}
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
