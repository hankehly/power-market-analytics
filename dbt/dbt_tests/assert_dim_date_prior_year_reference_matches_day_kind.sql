-- The reference day must be the same kind of day as the day it stands for,
-- which needs the reference's own dim_date row (a self-join, hence a
-- singular test): a working day's reference is a working day, a holiday's
-- is a non-working day, and a same_holiday reference carries the same
-- holiday name. References that precede the spine (the first year) have no
-- row to check and pass.
with
  days as (
  select
    date_key,
    is_business_day,
    is_holiday,
    holiday_name_ja,
    prior_year_reference_date,
    prior_year_reference_rule
  from
    {{ ref('dim_date') }}
  ),

  checked as (
  select
    d.date_key,
    d.prior_year_reference_date,
    d.prior_year_reference_rule,
    d.is_business_day,
    d.is_holiday,
    d.holiday_name_ja,
    r.is_business_day as reference_is_business_day,
    r.holiday_name_ja as reference_holiday_name_ja
  from
    days d
    inner join days r on r.date_key = d.prior_year_reference_date
  )

select *
from checked
where
  (is_business_day and not reference_is_business_day)
  or (is_holiday and reference_is_business_day)
  or (prior_year_reference_rule = 'same_holiday' and reference_holiday_name_ja <> holiday_name_ja)
