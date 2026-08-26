-- Written by a separate Spark application (the backtest script); refresh the
-- thriftserver's cached file listing before reading, as for the forecasts.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'spot_price_forecast_contribution') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    forecast_issued_ts,
    trade_date,
    time_code,
    component,
    component_order,
    feature_value,
    contribution_price_jpy_kwh,
    published_at
  from
    {{ source('ml', 'spot_price_forecast_contribution') }}
  )

select * from source
