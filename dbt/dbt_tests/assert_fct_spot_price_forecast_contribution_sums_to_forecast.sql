-- Per period the base plus the feature contributions must reproduce the
-- published forecast (TreeSHAP is exactly additive; the tolerance absorbs
-- floating-point summation noise), and every explained period must have a
-- forecast row. A failure means the two write-backs came from different runs
-- or the melt lost a component.
with
  decomposed as (
  select
    run_id,
    date_key,
    time_code,
    area_key,
    sum(contribution_price_jpy_kwh) as contribution_total_jpy_kwh
  from
    {{ ref('fct_spot_price_forecast_contribution') }}
  group by
    run_id,
    date_key,
    time_code,
    area_key
  )

select
  decomposed.run_id,
  decomposed.date_key,
  decomposed.time_code,
  decomposed.area_key,
  decomposed.contribution_total_jpy_kwh,
  forecast.forecast_price_jpy_kwh
from
  decomposed
  left join {{ ref('fct_spot_price_forecast') }} as forecast
    on decomposed.run_id = forecast.run_id
    and decomposed.date_key = forecast.date_key
    and decomposed.time_code = forecast.time_code
    and decomposed.area_key = forecast.area_key
where
  forecast.forecast_price_jpy_kwh is null
  or abs(decomposed.contribution_total_jpy_kwh - forecast.forecast_price_jpy_kwh)
    > cast(1 as double) / 1000000 * greatest(abs(forecast.forecast_price_jpy_kwh), cast(1 as double))
