-- Written by a separate Spark application (the backtest script); refresh the
-- thriftserver's cached file listing before reading, as for the forecasts.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'spot_price_forecast_importance') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    feature,
    feature_order,
    repeat_index,
    n_periods,
    mae_price_jpy_kwh,
    permuted_mae_price_jpy_kwh,
    published_at
  from
    {{ source('ml', 'spot_price_forecast_importance') }}
  )

select * from source
