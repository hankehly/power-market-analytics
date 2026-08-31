-- Pins the prior-year reference date on the researcher's worked examples
-- (A-E of the 2026-08-28 design discussion), the two observation days that
-- motivated it (demand/O-002, O-003), the calendar's own corner cases
-- (Olympic 海の日 / スポーツの日 moves, a substitute 休日) and the first days
-- of the spine, whose references precede it. Every value was measured on the
-- calendar before the column existed; a change in the rule set or in the
-- holiday seed that moves one of them fails here with the row named.
with
  expected as (
  select * from values
    (date '2024-09-22', date '2023-09-23', 'same_holiday'),             -- A: 秋分の日, Sunday -> Saturday
    (date '2020-02-23', date '2019-02-23', 'nearest_non_working_day'),  -- B: first 天皇誕生日 on 2/23, no 2019 twin -> the Saturday
    (date '2026-08-10', date '2025-08-18', 'same_weekday_shifted'),     -- C: D-364 is 山の日 -> D-357
    (date '2026-07-04', date '2025-07-05', 'same_weekday'),             -- D: a plain Saturday -> the Saturday 52 weeks back
    (date '2024-05-06', date '2023-05-06', 'nearest_non_working_day'),  -- E: 休日, no 休日 near 2023-05-06 -> itself, a Saturday
    (date '2026-02-11', date '2025-02-11', 'same_holiday'),             -- O-003: 建国記念の日, Wednesday -> Tuesday
    (date '2026-08-12', date '2025-08-20', 'same_weekday_shifted'),     -- O-002: D-364 is お盆 -> D-357
    (date '2025-08-12', date '2024-08-20', 'same_weekday_shifted'),     -- O-002: the 2025 twin, same collision
    (date '2027-01-04', date '2026-01-05', 'same_weekday'),             -- first working day of 2027
    (date '2021-07-22', date '2020-07-23', 'same_holiday'),             -- 海の日 moved for the Olympics both years
    (date '2020-07-24', date '2019-07-21', 'nearest_non_working_day'),  -- first スポーツの日 (Olympics); 2019 had 体育の日 in October -> nearest non-working day, tie -> earlier
    (date '2016-03-21', date '2015-03-21', 'nearest_non_working_day'),  -- 休日 for 春分の日 on a Sunday -> 2015's 春分の日, a Saturday
    (date '2016-01-01', date '2015-01-01', 'same_holiday'),             -- spine start: the reference precedes the spine
    (date '2016-01-04', date '2015-01-05', 'same_weekday')              -- first working day of the spine
    as t(date_key, expected_reference_date, expected_rule)
  ),

  actual as (
  select
    date_key,
    prior_year_reference_date,
    prior_year_reference_rule
  from
    {{ ref('dim_date') }}
  )

select
  expected.date_key,
  expected.expected_reference_date,
  actual.prior_year_reference_date,
  expected.expected_rule,
  actual.prior_year_reference_rule
from
  expected
  left join actual on actual.date_key = expected.date_key
where
  actual.date_key is null
  or actual.prior_year_reference_date is null
  or actual.prior_year_reference_date <> expected.expected_reference_date
  or actual.prior_year_reference_rule <> expected.expected_rule
