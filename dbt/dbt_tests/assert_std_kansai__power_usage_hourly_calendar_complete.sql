-- The hourly history must be gapless and start where the source starts,
-- with one known hole: Kansai never published 2024-03-31 (site maintenance
-- that day; the downloader's known_missing_days lets the settled month
-- through). Because the (delivery_date, hour_start) grain is unique, the
-- row count equals (span days - 1) * 24 only when no other day or hour is
-- missing AND the hole is still there; pinning the first day to 2016-04-01
-- catches a reload that lost a leading month, and an empty relation fails
-- outright. Should Kansai ever publish 2024-03-31, both the count clause
-- and the last clause fail: drop the day from the kansai.power_usage spec
-- and from this test. The last day is not pinned (see the TEPCO test).
select
  count(*) as n_rows,
  min(delivery_date) as first_day,
  datediff(max(delivery_date), min(delivery_date)) * 24 as n_expected,
  sum(case when delivery_date = date '2024-03-31' then 1 else 0 end) as n_rows_on_missing_day
from {{ ref('std_kansai__power_usage_hourly') }}
having
  count(*) = 0
  or min(delivery_date) != date '2016-04-01'
  or count(*) != datediff(max(delivery_date), min(delivery_date)) * 24
  or sum(case when delivery_date = date '2024-03-31' then 1 else 0 end) != 0
