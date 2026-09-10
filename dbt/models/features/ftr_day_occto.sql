with
  final as (
  select
    areas.area_code,
    forecasts.date_key as trade_date,
    forecasts.max_demand_hour_ending,
    forecasts.max_demand_mw,
    forecasts.max_supply_capacity_mw,
    {{ available_at(['forecasts.available_at']) }} as available_at
  from
    {{ ref('fct_occto_demand_supply_forecast_daily') }} as forecasts
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = forecasts.area_key
  )

select * from final
