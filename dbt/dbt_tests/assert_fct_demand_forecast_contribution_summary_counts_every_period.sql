-- The summary's means cover every explained period of a run: per run and
-- area, n_periods on the summary's base row must equal the number of base
-- rows in the contribution fact (one per period), and neither side may hold
-- a run the other lacks.
with
  fact as (
  select
    run_id,
    area_key,
    count(*) as n_periods
  from
    {{ ref('fct_demand_forecast_contribution') }}
  where
    is_base
  group by
    run_id,
    area_key
  ),

  summary as (
  select
    run_id,
    area_key,
    n_periods
  from
    {{ ref('fct_demand_forecast_contribution_summary') }}
  where
    is_base
  )

select
  coalesce(fact.run_id, summary.run_id) as run_id,
  coalesce(fact.area_key, summary.area_key) as area_key,
  fact.n_periods as fact_periods,
  summary.n_periods as summary_periods
from
  fact
  full outer join summary
    on fact.run_id = summary.run_id
    and fact.area_key = summary.area_key
where
  fact.n_periods is null
  or summary.n_periods is null
  or fact.n_periods <> summary.n_periods
