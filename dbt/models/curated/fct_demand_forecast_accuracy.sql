with
  forecast as (
  select
    *
  from
    {{ ref('fct_demand_forecast') }}
  ),

  actual as (
  select
    date_key,
    time_code,
    area_key,
    demand_kwh
  from
    {{ ref('fct_area_demand_generation_actual') }}
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
    forecast.forecast_demand_kwh,
    actual.demand_kwh as actual_demand_kwh,
    forecast.forecast_demand_kwh - actual.demand_kwh as error_kwh,
    abs(forecast.forecast_demand_kwh - actual.demand_kwh) as abs_error_kwh,
    case
      when actual.demand_kwh > 0
      then 100 * (forecast.forecast_demand_kwh - actual.demand_kwh) / actual.demand_kwh
    end as pct_error,
    case
      when actual.demand_kwh > 0
      then 100 * abs(forecast.forecast_demand_kwh - actual.demand_kwh) / actual.demand_kwh
    end as abs_pct_error
  from
    forecast
    left join actual
      on forecast.date_key = actual.date_key
      and forecast.time_code = actual.time_code
      and forecast.area_key = actual.area_key
  )

select * from final
