-- Worked examples of the standard calendar features (half, day_of_quarter,
-- day_of_year) at year, half and quarter boundaries and on a leap day.
-- Expected values come from Python's date and timetuple(), checked on
-- 2026-09-06. A row fails when the date is missing or any feature differs.
with
  expected as (
  select * from values
    -- The spine's first day, and year ends in a common year (2018) and a
    -- leap year (2020).
    (date '2016-01-01', 1, 1, 1),
    (date '2018-12-31', 2, 92, 365),
    (date '2020-12-31', 2, 92, 366),
    -- Leap day.
    (date '2024-02-29', 1, 60, 60),
    -- The half switches on July 1; the quarter's day count restarts.
    (date '2025-06-30', 1, 91, 181),
    (date '2025-07-01', 2, 1, 182),
    (date '2025-09-30', 2, 92, 273),
    (date '2026-04-01', 1, 1, 91),
    -- The spine's last day (holiday seed through 2027).
    (date '2027-12-31', 2, 92, 365)
    as t(date_key, half, day_of_quarter, day_of_year)
  )

select
  expected.date_key,
  expected.half as expected_half,
  dim_date.half,
  expected.day_of_quarter as expected_day_of_quarter,
  dim_date.day_of_quarter,
  expected.day_of_year as expected_day_of_year,
  dim_date.day_of_year
from
  expected
  left join {{ ref('dim_date') }} as dim_date
    on dim_date.date_key = expected.date_key
where
  dim_date.date_key is null
  or dim_date.half <> expected.half
  or dim_date.day_of_quarter <> expected.day_of_quarter
  or dim_date.day_of_year <> expected.day_of_year
