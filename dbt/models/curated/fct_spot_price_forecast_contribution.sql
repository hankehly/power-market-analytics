with
  contribution as (
  select
    *
  from
    {{ ref('std_ml__spot_price_forecast_contribution') }}
  ),

  final as (
  select
    contribution.trade_date as date_key,
    contribution.time_code,
    dim_area.area_key,
    contribution.run_id,
    contribution.strategy,
    contribution.component,
    contribution.component_order,
    contribution.is_base,
    contribution.trade_datetime,
    contribution.forecast_issued_ts,
    contribution.feature_value,
    contribution.contribution_price_jpy_kwh,
    contribution.published_at
  from
    contribution
    left join {{ ref('dim_area') }} as dim_area
      on contribution.area_code = dim_area.area_code
  )

select * from final
