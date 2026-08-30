with
  areas as (
  select
    area_key,
    area_code
  from
    {{ ref('dim_area') }}
  ),

  -- One standardized model per TSO でんき予報 feed, each publishing only its
  -- own service area; union another branch here when a second TSO's series
  -- is loaded (as fct_area_demand_generation_actual does).
  tokyo as (
  select
    delivery_date,
    hour_start,
    delivery_datetime,
    demand_mankw,
    'tokyo' as area_code
  from
    {{ ref('std_tepco__power_usage_hourly') }}
  ),

  final as (
  select
    tokyo.delivery_date as date_key,
    tokyo.hour_start as hour_of_day,
    areas.area_key,
    tokyo.delivery_datetime,
    -- The published 1時間平均 in 万kW over one hour is 万kWh; x 10,000 gives
    -- kWh, the unit of fct_area_demand_generation_actual.
    cast(tokyo.demand_mankw as bigint) * 10000 as demand_kwh
  from
    tokyo
    inner join areas
      on areas.area_code = tokyo.area_code
  )

select * from final
