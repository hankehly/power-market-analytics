-- The period marts ftr_period_daytype_weather and ftr_period_similar_day join
-- ftr_hour_msm on area, day and hour, so a second vintage on a delivery day would double
-- their rows.
-- Only the 12 UTC D-2 run is loaded (spec 2026-09-29-lag-window-weather-siblings,
-- decision 10); this fails the build the day another is.
select area_code, trade_date, hour_ending, count(*) as n_vintages
from {{ ref('ftr_hour_msm') }}
group by area_code, trade_date, hour_ending
having count(*) > 1
