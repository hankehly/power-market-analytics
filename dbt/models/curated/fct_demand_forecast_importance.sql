with
  importance as (
  select
    *
  from
    {{ ref('stg_ml__demand_forecast_importance') }}
  ),

  final as (
  select
    dim_area.area_key,
    importance.run_id,
    importance.strategy,
    importance.feature,
    importance.feature_order,
    importance.repeat_index,
    importance.n_periods,
    importance.mae_demand_kwh,
    importance.permuted_mae_demand_kwh,
    importance.published_at
  from
    importance
    left join {{ ref('dim_area') }} as dim_area
      on importance.area_code = dim_area.area_code
  )

select * from final
