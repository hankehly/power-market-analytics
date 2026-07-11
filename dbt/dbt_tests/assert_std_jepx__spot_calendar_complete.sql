-- Each fiscal year must be gapless: because the (trade_date, time_code)
-- grain is unique, the row count per fiscal year equals span-days * 48 only
-- when no date or 30-minute period is missing. Scoped per fiscal year (not
-- the full table span) because source files are per fiscal year and not all
-- years need to be loaded.
select
  fiscal_year,
  count(*) as n_rows,
  (datediff(max(trade_date), min(trade_date)) + 1) * 48 as n_expected
from {{ ref('std_jepx__spot') }}
group by fiscal_year
having count(*) != (datediff(max(trade_date), min(trade_date)) + 1) * 48
