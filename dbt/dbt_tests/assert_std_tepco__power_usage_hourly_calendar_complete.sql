-- The hourly history must be gapless and start where the source starts.
-- Because the (delivery_date, hour_start) grain is unique, the row count
-- equals span-days * 24 only when no day or hour is missing; pinning the
-- first day to 2016-04-01 (power_usage.HISTORY_START, the first hour TEPCO
-- publishes) also catches a reload that lost a leading yearly file, which
-- min()/max() alone would tolerate, and an empty relation fails outright
-- (the count comparison alone evaluates to null there and would pass).
-- The last day is not pinned: its expectation ("yesterday") depends on the
-- clock at load time, which a data test cannot know without failing every
-- build that runs later than the load — the downloader enforces member
-- coverage for every settled month, and `just refresh-*` aborts before the
-- load when a download fails.
select
  count(*) as n_rows,
  min(delivery_date) as first_day,
  (datediff(max(delivery_date), min(delivery_date)) + 1) * 24 as n_expected
from {{ ref('std_tepco__power_usage_hourly') }}
having
  count(*) = 0
  or min(delivery_date) != date '2016-04-01'
  or count(*) != (datediff(max(delivery_date), min(delivery_date)) + 1) * 24
