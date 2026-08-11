-- The source is written by a separate Spark application (the backtest
-- script), and inserts into an existing partitioned table don't invalidate
-- the thriftserver's cached file listing the way the raw loaders' full
-- table overwrites do — refresh before reading.
{{ config(pre_hook="REFRESH TABLE {{ source('ml', 'spot_price_forecast') }}") }}

with
  source as (
  select
    run_id,
    strategy,
    area_code,
    forecast_issued_ts,
    trade_date,
    time_code,
    forecast_price_jpy_kwh
  from
    {{ source('ml', 'spot_price_forecast') }}
  )

select * from source
