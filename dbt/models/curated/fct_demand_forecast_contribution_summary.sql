with
  contribution as (
  select
    *
  from
    {{ ref('fct_demand_forecast_contribution') }}
  ),

  summary as (
  select
    run_id,
    area_key,
    strategy,
    published_at,
    component,
    component_order,
    is_base,
    count(*) as n_periods,
    avg(contribution_demand_kwh) as mean_contribution_demand_kwh,
    avg(abs(contribution_demand_kwh)) as mean_abs_contribution_demand_kwh
  from
    contribution
  group by
    run_id,
    area_key,
    strategy,
    published_at,
    component,
    component_order,
    is_base
  ),

  final as (
  select
    run_id,
    area_key,
    strategy,
    published_at,
    component,
    component_order,
    is_base,
    case
      when not is_base
      then cast(row_number() over (
        partition by run_id, area_key, is_base
        order by mean_abs_contribution_demand_kwh desc, component
      ) as int)
    end as feature_rank,
    n_periods,
    mean_contribution_demand_kwh,
    mean_abs_contribution_demand_kwh
  from
    summary
  )

select * from final
