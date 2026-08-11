with
  forecast as (
  select
    *
  from
    {{ ref('fct_spot_price_forecast') }}
  ),

  actual as (
  select
    date_key,
    time_code,
    area_key,
    area_price_jpy_kwh
  from
    {{ ref('fct_jepx_spot_area_price') }}
  ),

  final as (
  select
    forecast.date_key,
    forecast.time_code,
    forecast.area_key,
    forecast.run_id,
    forecast.strategy,
    forecast.trade_datetime,
    forecast.forecast_issued_ts,
    forecast.horizon_hours,
    forecast.published_at,
    forecast.forecast_price_jpy_kwh,
    actual.area_price_jpy_kwh as actual_price_jpy_kwh,
    forecast.forecast_price_jpy_kwh - actual.area_price_jpy_kwh as error_jpy_kwh,
    abs(forecast.forecast_price_jpy_kwh - actual.area_price_jpy_kwh) as abs_error_jpy_kwh,
    case
      when actual.area_price_jpy_kwh > 0
      then 100 * (forecast.forecast_price_jpy_kwh - actual.area_price_jpy_kwh)
        / actual.area_price_jpy_kwh
    end as pct_error,
    case
      when actual.area_price_jpy_kwh > 0
      then 100 * abs(forecast.forecast_price_jpy_kwh - actual.area_price_jpy_kwh)
        / actual.area_price_jpy_kwh
    end as abs_pct_error
  from
    forecast
    left join actual
      on forecast.date_key = actual.date_key
      and forecast.time_code = actual.time_code
      and forecast.area_key = actual.area_key
  )

select * from final
