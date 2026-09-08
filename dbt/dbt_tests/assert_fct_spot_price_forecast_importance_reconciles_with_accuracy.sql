-- The baseline of the permutation importance is the run's own error: every
-- importance row must carry the accuracy mart's period count and MAE for its
-- run and area (relative tolerance 1e-9 on the MAE).
with
  importance as (
  select
    run_id,
    area_key,
    feature,
    repeat_index,
    n_periods,
    mae_price_jpy_kwh
  from
    {{ ref('fct_spot_price_forecast_importance') }}
  ),

  accuracy as (
  select
    run_id,
    area_key,
    count(*) as n_periods,
    avg(abs_error_jpy_kwh) as mae
  from
    {{ ref('fct_spot_price_forecast_accuracy') }}
  where
    abs_error_jpy_kwh is not null
  group by
    run_id,
    area_key
  )

select
  importance.run_id,
  importance.area_key,
  importance.feature,
  importance.repeat_index,
  importance.n_periods,
  accuracy.n_periods as accuracy_n_periods,
  importance.mae_price_jpy_kwh,
  accuracy.mae as accuracy_mae
from
  importance
  left join accuracy
    on importance.run_id = accuracy.run_id
    and importance.area_key = accuracy.area_key
where
  accuracy.run_id is null
  or importance.n_periods <> accuracy.n_periods
  or abs(importance.mae_price_jpy_kwh - accuracy.mae) > 1e-9 * greatest(accuracy.mae, 1)
