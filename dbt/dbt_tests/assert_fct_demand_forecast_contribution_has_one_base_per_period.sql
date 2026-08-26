-- Every explained period carries exactly one base row (the model's expected
-- value): the dashboard's base-value tile and the additivity test rely on it.
select
  run_id,
  date_key,
  time_code,
  area_key,
  sum(case when is_base then 1 else 0 end) as n_base_rows
from
  {{ ref('fct_demand_forecast_contribution') }}
group by
  run_id,
  date_key,
  time_code,
  area_key
having
  sum(case when is_base then 1 else 0 end) <> 1
