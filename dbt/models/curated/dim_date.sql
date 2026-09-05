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

  -- Every day of the spine with its calendar flags; the holiday degree
  -- below is derived from these and the seed.
  days as (
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
  ),

  -- 休日度合い (holiday degree) after JP 4448226 B2 (新日本製鐵, 2000; see
  -- docs/research/papers.md and the design in
  -- docs/superpowers/specs/2026-09-05-dim-date-holiday-degree-design.md):
  -- three graded values per day, the largest wins.
  -- Type 1, calendar: 1.0 on a Sunday or a 国民の祝日 (the seed), 0.8 on a
  -- Saturday, else 0. Type 2, special period: 0.8 on the first day of 年末年始
  -- (12/30), ゴールデンウィーク (4/29) and お盆 (8/13), 1.0 on their other days
  -- (12/30-1/3, 4/29-5/5, 8/13-8/16), else 0. Type 3 (next CTE) reads the
  -- neighbours taken here: "off" is not is_business_day, so the customary
  -- periods count on both sides. The spine has no gaps, so a row offset is a
  -- day offset; a missing neighbour at the spine's edge counts as a working
  -- day (both edges are holidays, so the rule never fires there).
  graded as (
  select
    days.*,
    case
      when days.day_of_week_iso = 7 or holidays.holiday_date is not null then 1.0
      when days.day_of_week_iso = 6 then 0.8
      else 0.0
    end as calendar_degree,
    case
      when days.month = 12 and days.day_of_month = 30 then 0.8
      when days.month = 4 and days.day_of_month = 29 then 0.8
      when days.month = 8 and days.day_of_month = 13 then 0.8
      when days.month = 12 and days.day_of_month = 31 then 1.0
      when days.month = 1 and days.day_of_month <= 3 then 1.0
      when days.month = 4 and days.day_of_month = 30 then 1.0
      when days.month = 5 and days.day_of_month <= 5 then 1.0
      when days.month = 8 and days.day_of_month between 14 and 16 then 1.0
      else 0.0
    end as special_period_degree,
    coalesce(lag(days.is_business_day, 1) over (order by days.date_key), true) as is_business_day_1_before,
    coalesce(lag(days.is_business_day, 2) over (order by days.date_key), true) as is_business_day_2_before,
    coalesce(lead(days.is_business_day, 1) over (order by days.date_key), true) as is_business_day_1_after,
    coalesce(lead(days.is_business_day, 2) over (order by days.date_key), true) as is_business_day_2_after
  from
    days
    left join holidays on days.date_key = holidays.holiday_date
  ),

  -- Type 3, sandwiched: a single working day between off days (飛び石連休の
  -- 中日) scores 0.5; each of two consecutive working days between off days
  -- (二飛び石連休の中日) scores 0.3; longer runs and non-working days score 0.
  -- The 0.3 branches run after the 0.5 one, so the day's other neighbour is
  -- a working day there.
  sandwiched as (
  select
    graded.*,
    case
      when not graded.is_business_day then 0.0
      when not graded.is_business_day_1_before and not graded.is_business_day_1_after then 0.5
      when not graded.is_business_day_1_before and not graded.is_business_day_2_after then 0.3
      when not graded.is_business_day_1_after and not graded.is_business_day_2_before then 0.3
      else 0.0
    end as sandwiched_degree
  from
    graded
  ),

  final as (
  select
    date_key,
    year,
    quarter,
    month,
    day_of_month,
    day_of_week_iso,
    day_name,
    month_name,
    fiscal_year,
    fiscal_quarter,
    is_weekend,
    is_holiday,
    holiday_name_ja,
    is_business_day,
    cast(greatest(calendar_degree, special_period_degree, sandwiched_degree) as double) as holiday_degree
  from
    sandwiched
  )

select * from final
