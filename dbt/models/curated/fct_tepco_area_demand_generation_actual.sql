with
  actuals as (
  select
    *
  from
    {{ ref('std_tepco__area_demand_generation_actual') }}
  ),

  -- TEPCO publishes only its own service area. The area dimension is still
  -- part of the grain so the fact conforms with fct_jepx_spot_area_price and
  -- other TSOs' area actuals can be added later.
  tokyo as (
  select
    area_key
  from
    {{ ref('dim_area') }}
  where
    area_code = 'tokyo'
  ),

  final as (
  select
    actuals.delivery_date as date_key,
    actuals.time_code,
    tokyo.area_key,
    actuals.delivery_datetime,
    actuals.demand_kwh,
    actuals.generation_kwh,
    actuals.wind_solar_generation_kwh
  from
    actuals
    cross join tokyo
  )

select * from final
