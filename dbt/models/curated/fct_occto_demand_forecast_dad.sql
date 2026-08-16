with
  forecasts as (
  select
    *
  from
    {{ ref('std_occto__demand_forecast_dad') }}
  where
    -- Atomic grain only: the エリア計 rows are roll-ups of the area rows, and
    -- Okinawa is not a JEPX bidding zone (no dim_area row).
    not is_area_total
    and area_code != 'okinawa'
  ),

  final as (
  select
    forecasts.target_date as date_key,
    dim_area.area_key,
    forecasts.formulated_date,
    forecasts.forecast_horizon_days,
    forecasts.min_demand_hour_ending,
    forecasts.min_demand_mw,
    forecasts.max_demand_hour_ending,
    forecasts.max_demand_mw,
    forecasts.max_supply_capacity_mw,
    forecasts.usage_rate,
    forecasts.reserve_rate
  from
    forecasts
    inner join {{ ref('dim_area') }} as dim_area
      on forecasts.area_code = dim_area.area_code
  )

select * from final
