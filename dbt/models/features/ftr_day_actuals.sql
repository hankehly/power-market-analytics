-- The previous complete day's demand summaries: D-2's mean, maximum and
-- range over its 48 periods. A day with a hole (a null period) is not
-- complete and gives no row. The sum of 48 integers is exact in a double,
-- so avg() gives the same value whatever order Spark reads the rows in.
with
  complete_days as (
  select
    areas.area_code,
    actuals.date_key,
    avg(actuals.demand_kwh) as mean_demand_kwh,
    max(actuals.demand_kwh) as max_demand_kwh,
    min(actuals.demand_kwh) as min_demand_kwh,
    max(actuals.available_at) as available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    actuals.demand_kwh is not null
  group by
    areas.area_code, actuals.date_key
  having
    count(*) = 48
  ),

  final as (
  select
    area_code,
    date_add(date_key, 2) as trade_date,
    mean_demand_kwh as lag_2d_mean_demand_kwh,
    max_demand_kwh as lag_2d_max_demand_kwh,
    max_demand_kwh - min_demand_kwh as lag_2d_range_demand_kwh,
    {{ available_at(['available_at']) }} as available_at
  from
    complete_days
  )

select * from final
