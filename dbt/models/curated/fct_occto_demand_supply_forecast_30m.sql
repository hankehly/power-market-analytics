with
  forecasts as (
  select
    *
  from
    {{ ref('std_occto__area_reserve_rate_dad') }}
  where
    -- Okinawa is not a JEPX bidding zone (no dim_area row); the wide-area
    -- block columns stay in std, this fact carries the area's own forecasts.
    area_code != 'okinawa'
  ),

  final as (
  select
    forecasts.target_date as date_key,
    forecasts.time_code,
    dim_area.area_key,
    forecasts.delivery_datetime,
    forecasts.area_demand_mw as demand_mw,
    forecasts.area_supply_capacity_mw as supply_capacity_mw
  from
    forecasts
    inner join {{ ref('dim_area') }} as dim_area
      on forecasts.area_code = dim_area.area_code
  )

select * from final
