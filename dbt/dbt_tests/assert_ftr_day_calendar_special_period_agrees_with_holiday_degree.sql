-- ftr_day_calendar.special_period writes the three special periods as date
-- ranges of its own, while dim_date.holiday_degree holds them too. This test
-- ties the two: a row fails when they disagree, so neither can change alone.
--   1, 2, 3 (a period): never a working day, and holiday_degree is the
--     period's, 0.8 on its first day and 1.0 after, so at least 0.8.
--   4 (another holiday): never a working day.
--   5 (sandwiched): a working day whose holiday_degree is 0.5 or 0.3. A working
--     day takes no degree from the calendar or a period, so that is dim_date's
--     sandwiched rule alone, and every such day must be 5.
select
  area_code,
  trade_date,
  special_period,
  is_business_day,
  holiday_degree
from {{ ref('ftr_day_calendar') }}
where
  (special_period in (1, 2, 3) and (is_business_day = 1 or holiday_degree < 0.8))
  or (special_period = 4 and is_business_day = 1)
  or (special_period = 5 and not (is_business_day = 1 and holiday_degree in (0.3, 0.5)))
  or (special_period != 5 and is_business_day = 1 and holiday_degree > 0)
