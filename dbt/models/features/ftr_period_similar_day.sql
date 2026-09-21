-- The demand similar-day features as scripts/fit_similar_day.py scored them
-- walking forward and wrote them (pma_ml.similar_day): the staging model passed
-- through, one row per scoring run and period. The as-of join takes the
-- newest row usable at the issue time; among rows tied on available_at the
-- newest published wins.
with
  final as (
  select
    area_code,
    trade_date,
    time_code,
    run_id as similar_day_run_id,
    similar_day_rank1_demand_kwh,
    similar_day_rank2_demand_kwh,
    similar_day_rank3_demand_kwh,
    wavg_similar_day_top3_demand_kwh,
    similar_day_rank1_reference_date,
    similar_day_rank2_reference_date,
    similar_day_rank3_reference_date,
    similar_day_rank1_distance,
    similar_day_rank2_distance,
    similar_day_rank3_distance,
    similar_day_n_candidates,
    similar_day_fit_cutoff,
    similar_day_method,
    -- How many days back the rank-1 day lies: within 2 to 31 or 335 to 394 on a
    -- ranked day, the pool's two windows; the same holiday last year otherwise.
    datediff(trade_date, similar_day_rank1_reference_date) as similar_day_rank1_lag_days,
    -- The same four features and their traceability from the pool ranking, which
    -- runs on every day: a preset picks between copying last year's same holiday
    -- and ranking every day. On a day the override never touched the two agree.
    similar_day_pool_rank1_demand_kwh,
    similar_day_pool_rank2_demand_kwh,
    similar_day_pool_rank3_demand_kwh,
    wavg_similar_day_pool_top3_demand_kwh,
    similar_day_pool_rank1_reference_date,
    similar_day_pool_rank2_reference_date,
    similar_day_pool_rank3_reference_date,
    similar_day_pool_rank1_distance,
    similar_day_pool_rank2_distance,
    similar_day_pool_rank3_distance,
    similar_day_pool_n_candidates,
    similar_day_pool_fit_cutoff,
    -- Always one of the pool's two windows: the override cannot reach it.
    datediff(trade_date, similar_day_pool_rank1_reference_date) as similar_day_pool_rank1_lag_days,
    available_at,
    published_at
  from
    {{ ref('stg_ml__similar_day') }}
  )

select * from final
