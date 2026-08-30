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
  -- The lookup calendar starts 13 months earlier: a published day's
  -- prior-year reference lies at most 380 days back (a holiday's anchor is
  -- the same calendar date a year earlier, 365 or 366 days back, and the
  -- search reaches 14 days beyond it) and resolving it needs that day's kind
  -- and holiday name; 13 months is at least 396 days. Lookup-only days are
  -- not published.
  spine_bounds as (
  select
    to_date('2016-01-01') as start_date,
    add_months(to_date('2016-01-01'), -13) as lookup_start_date,
    make_date(year(max(holiday_date)), 12, 31) as end_date
  from
    holidays
  ),

  date_spine as (
  select
    explode(sequence(lookup_start_date, end_date, interval 1 day)) as date_key
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

  -- The kind of every day of the lookup calendar: what the prior-year
  -- reference is resolved against.
  calendar as (
  select
    date_spine.date_key,
    weekday(date_spine.date_key) >= 5 as is_weekend,
    all_holidays.holiday_date is not null as is_holiday,
    coalesce(all_holidays.holiday_name_ja, 'Not Applicable') as holiday_name_ja,
    weekday(date_spine.date_key) < 5 and all_holidays.holiday_date is null as is_business_day
  from
    date_spine
    left join all_holidays on date_spine.date_key = all_holidays.holiday_date
  ),

  published as (
  select
    calendar.*
  from
    calendar
    cross join spine_bounds
  where
    calendar.date_key >= spine_bounds.start_date
  ),

  -- Prior-year reference date: the day one year earlier that stands for this
  -- day in a year-over-year comparison (the year-ago load feature of the
  -- demand task). Three exclusive branches by the kind of day; every
  -- published day resolves (not_null is tested).
  candidates as (
  select
    date_key,
    is_business_day,
    is_holiday,
    holiday_name_ja,
    date_sub(date_key, 364) as minus_364,  -- 52 weeks back: the same weekday
    date_sub(date_key, 357) as minus_357,  -- 51 weeks back
    date_sub(date_key, 371) as minus_371,  -- 53 weeks back
    add_months(date_key, -12) as anchor    -- the same calendar date a year earlier
  from
    published
  ),

  -- Working day: the same weekday 52 weeks back when it is a working day
  -- too; when that day is a holiday, the same weekday one week nearer
  -- (D-357), else one week farther (D-371). One of the three is a working
  -- day for every day of the spine — three holidays on the same weekday in
  -- consecutive weeks do not occur in the Japanese calendar.
  working_day_references as (
  select
    c.date_key,
    case
      when w364.is_business_day then c.minus_364
      when w357.is_business_day then c.minus_357
      when w371.is_business_day then c.minus_371
    end as prior_year_reference_date,
    case
      when w364.is_business_day then 'same_weekday'
      when w357.is_business_day or w371.is_business_day then 'same_weekday_shifted'
    end as prior_year_reference_rule
  from
    candidates c
    left join calendar w364 on w364.date_key = c.minus_364
    left join calendar w357 on w357.date_key = c.minus_357
    left join calendar w371 on w371.date_key = c.minus_371
  where
    c.is_business_day
  ),

  -- Weekend that is not a holiday: the same weekday 52 weeks back, whatever
  -- kind of day it is (it is never a working day).
  weekend_references as (
  select
    c.date_key,
    c.minus_364 as prior_year_reference_date,
    'same_weekday' as prior_year_reference_rule
  from
    candidates c
  where
    not c.is_business_day
    and not c.is_holiday
  ),

  -- Holiday: the nearest day carrying the same holiday name within 14 days
  -- of the same calendar date a year earlier (a tie goes to the earlier
  -- day); a holiday with no such twin — a new holiday, a one-off move, a
  -- substitute 休日 — takes the nearest non-working day to that date instead
  -- (a 29-day window always holds a weekend).
  same_holiday_references as (
  select
    c.date_key,
    min_by(h.date_key, struct(abs(datediff(h.date_key, c.anchor)), h.date_key)) as prior_year_reference_date
  from
    candidates c
    inner join calendar h
      on h.holiday_name_ja = c.holiday_name_ja
      and abs(datediff(h.date_key, c.anchor)) <= 14
  where
    c.is_holiday
  group by
    c.date_key
  ),

  nearest_non_working_day_references as (
  select
    c.date_key,
    min_by(h.date_key, struct(abs(datediff(h.date_key, c.anchor)), h.date_key)) as prior_year_reference_date
  from
    candidates c
    inner join calendar h
      on (h.is_holiday or h.is_weekend)
      and abs(datediff(h.date_key, c.anchor)) <= 14
  where
    c.is_holiday
  group by
    c.date_key
  ),

  holiday_references as (
  select
    c.date_key,
    coalesce(s.prior_year_reference_date, n.prior_year_reference_date) as prior_year_reference_date,
    case
      when s.prior_year_reference_date is not null then 'same_holiday'
      when n.prior_year_reference_date is not null then 'nearest_non_working_day'
    end as prior_year_reference_rule
  from
    candidates c
    left join same_holiday_references s on s.date_key = c.date_key
    left join nearest_non_working_day_references n on n.date_key = c.date_key
  where
    c.is_holiday
  ),

  prior_year_references as (
  select * from working_day_references
  union all
  select * from weekend_references
  union all
  select * from holiday_references
  ),

  final as (
  select
    published.date_key,
    year(published.date_key) as year,
    quarter(published.date_key) as quarter,
    month(published.date_key) as month,
    day(published.date_key) as day_of_month,
    weekday(published.date_key) + 1 as day_of_week_iso,
    date_format(published.date_key, 'EEEE') as day_name,
    date_format(published.date_key, 'MMMM') as month_name,
    case when month(published.date_key) >= 4 then year(published.date_key) else year(published.date_key) - 1 end as fiscal_year,
    cast((month(published.date_key) + 8) % 12 div 3 + 1 as int) as fiscal_quarter,
    published.is_weekend,
    published.is_holiday,
    published.holiday_name_ja,
    published.is_business_day,
    prior_year_references.prior_year_reference_date,
    prior_year_references.prior_year_reference_rule
  from
    published
    left join prior_year_references on prior_year_references.date_key = published.date_key
  )

select * from final
