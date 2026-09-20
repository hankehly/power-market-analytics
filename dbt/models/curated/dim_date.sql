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

  -- holiday_name_ja gives every holiday a name no other holiday of its
  -- calendar year has, so the same holiday can be found a year earlier.
  -- The seed's own names first: 体育の日 takes its current name スポーツの日
  -- in every year; 天皇誕生日 carries its era, as the emperor and the date
  -- changed on 2019-05-01 (12/23 in 平成, 2/23 in 令和); the two 2019
  -- enthronement days, published as 休日（祝日扱い）, take the names of their
  -- law. The generic 休日 rows are named in the next two steps.
  seed_names as (
  select
    holiday_date,
    case
      when holiday_name_ja in ('体育の日', '体育の日（スポーツの日）') then 'スポーツの日'
      when holiday_name_ja = '天皇誕生日' and holiday_date < date '2019-05-01' then '天皇誕生日（平成）'
      when holiday_name_ja = '天皇誕生日' then '天皇誕生日（令和）'
      when holiday_name_ja = '休日（祝日扱い）' and holiday_date = date '2019-05-01' then '即位の日'
      when holiday_name_ja = '休日（祝日扱い）' and holiday_date = date '2019-10-22' then '即位礼正殿の儀'
      else holiday_name_ja
    end as holiday_name_ja
  from
    holidays
  ),

  -- 振替休日 (祝日法 3条2項): a 休日 right after a run of consecutive seed
  -- holidays that holds a Sunday 祝日 is named after that holiday. The run
  -- can span several days: 2020-05-03 (a Sunday) moved past 5/4 and 5/5 to
  -- 5/6. A run is unbroken when it holds one seed row per day.
  substitute_candidates as (
  select
    kyujitsu.holiday_date,
    sunday.holiday_date as sunday_date,
    sunday.holiday_name_ja as sunday_name
  from
    seed_names kyujitsu
    inner join seed_names sunday
      on sunday.holiday_date < kyujitsu.holiday_date
      and dayofweek(sunday.holiday_date) = 1
      and sunday.holiday_name_ja <> '休日'
    inner join seed_names run_day
      on run_day.holiday_date >= sunday.holiday_date
      and run_day.holiday_date < kyujitsu.holiday_date
  where
    kyujitsu.holiday_name_ja = '休日'
  group by
    kyujitsu.holiday_date,
    sunday.holiday_date,
    sunday.holiday_name_ja
  having
    count(*) = datediff(kyujitsu.holiday_date, sunday.holiday_date)
  ),

  substitute_holidays as (
  select
    holiday_date,
    concat(max_by(sunday_name, sunday_date), '（振替休日）') as holiday_name_ja
  from
    substitute_candidates
  group by
    holiday_date
  ),

  -- 国民の休日 (祝日法 3条3項): any other 休日 lies between two seed holidays
  -- and is named after both.
  citizens_holidays as (
  select
    kyujitsu.holiday_date,
    concat(previous_day.holiday_name_ja, '・', next_day.holiday_name_ja, '（国民の休日）') as holiday_name_ja
  from
    seed_names kyujitsu
    inner join seed_names previous_day on previous_day.holiday_date = date_sub(kyujitsu.holiday_date, 1)
    inner join seed_names next_day on next_day.holiday_date = date_add(kyujitsu.holiday_date, 1)
    left anti join substitute_holidays on kyujitsu.holiday_date = substitute_holidays.holiday_date
  where
    kyujitsu.holiday_name_ja = '休日'
  ),

  national_holidays as (
  select
    seed_names.holiday_date,
    coalesce(
      substitute_holidays.holiday_name_ja,
      citizens_holidays.holiday_name_ja,
      seed_names.holiday_name_ja
    ) as holiday_name_ja
  from
    seed_names
    left join substitute_holidays on seed_names.holiday_date = substitute_holidays.holiday_date
    left join citizens_holidays on seed_names.holiday_date = citizens_holidays.holiday_date
  ),

  -- Names by date, for every year of the spine (no seed):
  -- - The New Year block, 小晦日 (12/30), 大晦日 (12/31) and 正月（1日目）
  --   to 正月（3日目） (1/1-1/3), is named by the date even where the seed has
  --   the day: 元日 is 正月（1日目）, and the 振替休日 of a Sunday 元日, which
  --   always falls on 1/2, is 正月（2日目）.
  -- - ゴールデンウィーク (4/30-5/2) and お盆 (8/13-8/16) days are numbered by
  --   their place in the week 4/29-5/5 and in 8/13-8/16, the holiday degree's
  --   special periods, and yield to a seed holiday on the same day. 4/29 is
  --   always 昭和の日, so ゴールデンウィーク starts at 2日目.
  -- The same dates, less 1/1 (always 元日 in the seed), are the customary
  -- non-working days that are not 国民の祝日: 年末年始 and ゴールデンウィーク are
  -- the 休日 set shared by the family-A TSO 託送供給等約款 (北海道・東京・中部・
  -- 関西・四国・九州; 東北, 北陸, 中国 and 沖縄 use slightly different dates),
  -- お盆 has no statutory or tariff basis but is observed nationwide.
  calendar_names as (
  select
    date_key,
    case
      when month(date_key) = 12 and day(date_key) = 30 then '小晦日'
      when month(date_key) = 12 and day(date_key) = 31 then '大晦日'
      when month(date_key) = 1 and day(date_key) <= 3
        then concat('正月（', cast(day(date_key) as string), '日目）')
    end as new_year_name,
    case
      when (month(date_key) = 4 and day(date_key) = 30) or (month(date_key) = 5 and day(date_key) <= 2)
        then concat(
          'ゴールデンウィーク（',
          cast(datediff(date_key, make_date(year(date_key), 4, 29)) + 1 as string),
          '日目）'
        )
      when month(date_key) = 8 and day(date_key) between 13 and 16
        then concat('お盆（', cast(day(date_key) - 12 as string), '日目）')
    end as customary_name
  from
    date_spine
  ),

  -- One row per holiday: the seed's 国民の祝日, plus the customary days that
  -- are not already one.
  all_holidays as (
  select
    holiday_date,
    holiday_name_ja
  from
    national_holidays
  union all
  select
    calendar_names.date_key as holiday_date,
    coalesce(calendar_names.new_year_name, calendar_names.customary_name) as holiday_name_ja
  from
    calendar_names
    left anti join holidays on calendar_names.date_key = holidays.holiday_date
  where
    coalesce(calendar_names.new_year_name, calendar_names.customary_name) is not null
  ),

  -- Every day of the spine with its calendar flags; the holiday degree
  -- below is derived from these and the seed.
  days as (
  select
    date_spine.date_key,
    year(date_spine.date_key) as year,
    case when month(date_spine.date_key) <= 6 then 1 else 2 end as half,
    quarter(date_spine.date_key) as quarter,
    month(date_spine.date_key) as month,
    day(date_spine.date_key) as day_of_month,
    datediff(date_spine.date_key, trunc(date_spine.date_key, 'QUARTER')) + 1 as day_of_quarter,
    dayofyear(date_spine.date_key) as day_of_year,
    weekday(date_spine.date_key) + 1 as day_of_week_iso,
    date_format(date_spine.date_key, 'EEEE') as day_name,
    date_format(date_spine.date_key, 'MMMM') as month_name,
    case when month(date_spine.date_key) >= 4 then year(date_spine.date_key) else year(date_spine.date_key) - 1 end as fiscal_year,
    cast((month(date_spine.date_key) + 8) % 12 div 3 + 1 as int) as fiscal_quarter,
    weekday(date_spine.date_key) >= 5 as is_weekend,
    all_holidays.holiday_date is not null as is_holiday,
    coalesce(calendar_names.new_year_name, all_holidays.holiday_name_ja, 'Not Applicable') as holiday_name_ja,
    weekday(date_spine.date_key) < 5 and all_holidays.holiday_date is null as is_business_day
  from
    date_spine
    left join all_holidays on date_spine.date_key = all_holidays.holiday_date
    inner join calendar_names on date_spine.date_key = calendar_names.date_key
  ),

  -- 休日度合い (holiday degree) after JP 4448226 B2 (新日本製鐵, 2000; see
  -- docs/research/literature-review.md and the design in
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
    half,
    quarter,
    month,
    day_of_month,
    day_of_quarter,
    day_of_year,
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
