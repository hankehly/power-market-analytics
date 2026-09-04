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
  -- Start is the earliest date across all fact sources: JMA weather begins
  -- 2016-01-01 (JEPX spot begins fiscal year 2016 = 2016-04-01, later).
  spine_bounds as (
  select
    to_date('2016-01-01') as start_date,
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

  -- Customary non-working days that are not 国民の祝日, as fixed month/day
  -- rules (no seed: every year of the spine gets them automatically):
  -- 年末年始 and ゴールデンウィーク are the 休日 set shared by the family-A TSO
  -- 託送供給等約款 (北海道・東京・中部・関西・四国・九州; 東北, 北陸, 中国 and
  -- 沖縄 use slightly different dates), お盆 has no statutory or tariff basis
  -- but is observed nationwide.
  customary_holidays as (
  select
    date_key,
    case
      when month(date_key) = 12 and day(date_key) in (30, 31) then '年末年始'
      when month(date_key) = 1 and day(date_key) in (2, 3) then '年末年始'
      when month(date_key) = 4 and day(date_key) = 30 then 'ゴールデンウィーク'
      when month(date_key) = 5 and day(date_key) in (1, 2) then 'ゴールデンウィーク'
      when month(date_key) = 8 and day(date_key) between 13 and 16 then 'お盆'
    end as holiday_name_ja
  from
    date_spine
  ),

  -- One row per holiday: the seed's 国民の祝日, plus the customary days that
  -- are not already one (2019-05-01 即位の日 keeps its official name).
  all_holidays as (
  select
    holiday_date,
    holiday_name_ja
  from
    holidays
  union all
  select
    customary_holidays.date_key as holiday_date,
    customary_holidays.holiday_name_ja
  from
    customary_holidays
    left anti join holidays on customary_holidays.date_key = holidays.holiday_date
  where
    customary_holidays.holiday_name_ja is not null
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
    all_holidays.holiday_date is not null as is_holiday,
    coalesce(all_holidays.holiday_name_ja, 'Not Applicable') as holiday_name_ja,
    weekday(date_spine.date_key) < 5 and all_holidays.holiday_date is null as is_business_day
  from
    date_spine
    left join all_holidays on date_spine.date_key = all_holidays.holiday_date
  )

select * from final
