with
  areas as (
  select
    area_key,
    area_code
  from
    {{ ref('dim_area') }}
  ),

  -- One standardized model per TSO, each publishing only its own service
  -- area; add a branch here when another TSO's actuals are loaded.
  tokyo as (
  select
    delivery_date,
    time_code,
    delivery_datetime,
    demand_kwh,
    generation_kwh,
    wind_solar_generation_kwh,
    'tokyo' as area_code
  from
    {{ ref('std_tepco__area_demand_generation_actual') }}
  ),

  kansai as (
  select
    delivery_date,
    time_code,
    delivery_datetime,
    demand_kwh,
    generation_kwh,
    wind_solar_generation_kwh,
    'kansai' as area_code
  from
    {{ ref('std_kansai__area_demand_generation_actual') }}
  ),

  actuals as (
  select * from tokyo
  union all
  select * from kansai
  ),

  final as (
  select
    actuals.delivery_date as date_key,
    actuals.time_code,
    areas.area_key,
    actuals.delivery_datetime,
    actuals.demand_kwh,
    actuals.generation_kwh,
    actuals.wind_solar_generation_kwh
  from
    actuals
    inner join areas
      on areas.area_code = actuals.area_code
  )

select * from final
