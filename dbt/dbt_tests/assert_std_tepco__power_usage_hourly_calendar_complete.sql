-- The hourly history must be gapless from its first day to its last: because
-- the (delivery_date, hour_start) grain is unique, the row count equals
-- span-days * 24 only when no day or hour is missing. Scoped to the whole
-- span (not per fiscal year like the JEPX test) because every load rebuilds
-- the full 2016-04-01 -> yesterday history, and a year-ago load feature
-- depends on every day being present.
select
  count(*) as n_rows,
  (datediff(max(delivery_date), min(delivery_date)) + 1) * 24 as n_expected
from {{ ref('std_tepco__power_usage_hourly') }}
having count(*) != (datediff(max(delivery_date), min(delivery_date)) + 1) * 24
