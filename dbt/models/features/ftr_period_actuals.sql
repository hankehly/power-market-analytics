with
  final as (
  select
    areas.area_code,
    date_add(actuals.date_key, 7) as trade_date,
    actuals.time_code,
    actuals.demand_kwh as lag_7d_demand_kwh,
    {{ available_at(['actuals.available_at']) }} as available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    -- A TSO hole has no lag value; the row is absent rather than null.
    actuals.demand_kwh is not null
  )

select * from final
