-- The similar-day job's mean rule, applied by the inverse_distance_mean macro to the
-- stored pool rank loads and distances, must give the job's own
-- wavg_similar_day_pool_top3_demand_kwh: the proof that the weather siblings of
-- ftr_period_similar_day carry the job's weights (spec
-- 2026-09-29-lag-window-weather-siblings, section 8). The rank loads are the hourly
-- loads halved and the job halves after summing; halving is exact in binary, so the
-- two agree to the bit (0 rows differed on the first build, 2026-09-29).
{%- set rule = inverse_distance_mean(
  ['similar_day_pool_rank1_demand_kwh', 'similar_day_pool_rank2_demand_kwh', 'similar_day_pool_rank3_demand_kwh'],
  ['similar_day_pool_rank1_distance', 'similar_day_pool_rank2_distance', 'similar_day_pool_rank3_distance']
) %}
select
  similar_day_run_id,
  area_code,
  trade_date,
  time_code,
  wavg_similar_day_pool_top3_demand_kwh,
  {{ rule }} as rule_mean
from {{ ref('ftr_period_similar_day') }}
where similar_day_pool_rank1_demand_kwh is not null
  and not (wavg_similar_day_pool_top3_demand_kwh <=> {{ rule }})
