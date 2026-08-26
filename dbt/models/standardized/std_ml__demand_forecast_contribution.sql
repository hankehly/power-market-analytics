with
  staging as (
  select
    *
  from
    {{ ref('stg_ml__demand_forecast_contribution') }}
  ),

  final as (
  select
    run_id,
    strategy,
    area_code,
    trade_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(trade_date as timestamp)) as trade_datetime,
    forecast_issued_ts,
    component,
    component_order,
    component = 'base' as is_base,
    feature_value,
    contribution_demand_kwh,
    published_at
  from
    staging
  )

select * from final
