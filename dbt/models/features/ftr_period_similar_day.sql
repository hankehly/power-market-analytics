-- The demand similar-day feature as scripts/fit_similar_day.py scored and wrote
-- it (pma_ml.similar_day): the staging model passed through, one row per
-- scoring run and period. The as-of join takes the newest run available at
-- the issue time; among rows tied on available_at the newest published wins,
-- so a refit-and-rescore replaces the feature wherever it scored.
with
  final as (
  select
    area_code,
    trade_date,
    time_code,
    run_id as similar_day_run_id,
    similar_day_demand_kwh,
    similar_day_reference_date,
    similar_day_reference_lag_days,
    similar_day_distance,
    similar_day_n_candidates,
    available_at,
    published_at
  from
    {{ ref('stg_ml__similar_day') }}
  )

select * from final
