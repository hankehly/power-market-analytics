with
  forecast as (
  select
    *
  from
    {{ ref('std_ml__spot_price_forecast') }}
  ),

  final as (
  select
    forecast.trade_date as date_key,
    forecast.time_code,
    dim_area.area_key,
    forecast.run_id,
    forecast.strategy,
    forecast.trade_datetime,
    forecast.forecast_issued_ts,
    cast(
      (unix_timestamp(forecast.trade_datetime) - unix_timestamp(forecast.forecast_issued_ts)) / 3600
      as double
    ) as horizon_hours,
    forecast.forecast_price_jpy_kwh
  from
    forecast
    left join {{ ref('dim_area') }} as dim_area
      on forecast.area_code = dim_area.area_code
  )

select * from final
