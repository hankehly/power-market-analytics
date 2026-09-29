{#- One text for both elements: the observed column of fct_area_weather_hourly,
    the forecast column of ftr_hour_msm and the suffix the feature names carry. -#}
{%- set elements = [
  ('temperature_c', 'popw_temperature_c', 'popw_forecast_temperature_c'),
  ('solar_radiation_mjm2', 'popw_solar_radiation_mjm2', 'popw_forecast_solar_radiation_mjm2'),
] -%}
{%- set ranks = [1, 2, 3] -%}

-- The demand similar-day features as scripts/fit_similar_day.py scored them
-- walking forward and wrote them (pma_ml.similar_day): the staging model passed
-- through, one row per scoring run and period, plus (since 2026-09-29) the pool
-- days' observed weather at the hour containing the period and its deltas to D's
-- forecast. The as-of join takes the newest row usable at the issue time; among
-- rows tied on available_at the newest published wins.
with
  similar_days as (
  select
    *,
    cast(div(time_code + 1, 2) as int) as hour_ending
  from {{ ref('stg_ml__similar_day') }}
  ),

  -- The area's population-weighted observed weather by hour.
  hours as (
  select
    areas.area_code,
    fact.date_key,
    fact.hour_ending,
    fact.popw_temperature_c,
    fact.popw_solar_radiation_mjm2
  from {{ ref('fct_area_weather_hourly') }} as fact
    inner join {{ ref('dim_area') }} as areas on areas.area_key = fact.area_key
  ),

  -- D's population-weighted forecast at the hour: one vintage per delivery day
  -- (the singular test assert_ftr_hour_msm_one_vintage_per_delivery_day), so the
  -- join adds no row.
  forecast as (
  select
    area_code,
    trade_date,
    hour_ending,
    popw_forecast_temperature_c,
    popw_forecast_solar_radiation_mjm2
  from {{ ref('ftr_hour_msm') }}
  ),

  -- Each pool rank's weather on its reference date at the hour, and D's forecast.
  -- A rank the pool lacks, or a day the fact has no hour for, joins nothing.
  joined as (
  select
    similar_days.*,
    {%- for suffix, observed, forecast_column in elements %}
    {%- for rank in ranks %}
    hours_{{ rank }}.{{ observed }} as similar_day_pool_rank{{ rank }}_popw_{{ suffix }},
    {%- endfor %}
    forecast.{{ forecast_column }}{{ ',' if not loop.last }}
    {%- endfor %}
  from similar_days
    {%- for rank in ranks %}
    left join hours as hours_{{ rank }}
      on hours_{{ rank }}.area_code = similar_days.area_code
      and hours_{{ rank }}.date_key = similar_days.similar_day_pool_rank{{ rank }}_reference_date
      and hours_{{ rank }}.hour_ending = similar_days.hour_ending
    {%- endfor %}
    left join forecast
      on forecast.area_code = similar_days.area_code
      and forecast.trade_date = similar_days.trade_date
      and forecast.hour_ending = similar_days.hour_ending
  ),

  -- The job's inverse-distance mean over the ranks whose value is present (the
  -- macro shared with the singular test that proves it reproduces the job's own
  -- wavg_similar_day_pool_top3_demand_kwh).
  final as (
  select
    *,
    {%- for suffix, observed, forecast_column in elements %}
    {{ inverse_distance_mean(
      ['similar_day_pool_rank1_popw_' ~ suffix, 'similar_day_pool_rank2_popw_' ~ suffix, 'similar_day_pool_rank3_popw_' ~ suffix],
      ['similar_day_pool_rank1_distance', 'similar_day_pool_rank2_distance', 'similar_day_pool_rank3_distance']
    ) }} as wavg_similar_day_pool_top3_popw_{{ suffix }}{{ ',' if not loop.last }}
    {%- endfor %}
  from joined
  )

select
  area_code,
  trade_date,
  time_code,
  run_id as similar_day_run_id,
  similar_day_rank1_demand_kwh,
  similar_day_rank2_demand_kwh,
  similar_day_rank3_demand_kwh,
  wavg_similar_day_top3_demand_kwh,
  similar_day_rank1_reference_date,
  similar_day_rank2_reference_date,
  similar_day_rank3_reference_date,
  similar_day_rank1_distance,
  similar_day_rank2_distance,
  similar_day_rank3_distance,
  similar_day_n_candidates,
  similar_day_fit_cutoff,
  similar_day_method,
  -- How many days back the rank-1 day lies: within 2 to 31 or 335 to 394 on a
  -- ranked day, the pool's two windows; the same holiday last year otherwise.
  datediff(trade_date, similar_day_rank1_reference_date) as similar_day_rank1_lag_days,
  -- The same four features and their traceability from the pool ranking, which
  -- runs on every day: a preset picks between copying last year's same holiday
  -- and ranking every day. On a day the override never touched the two agree.
  similar_day_pool_rank1_demand_kwh,
  similar_day_pool_rank2_demand_kwh,
  similar_day_pool_rank3_demand_kwh,
  wavg_similar_day_pool_top3_demand_kwh,
  similar_day_pool_rank1_reference_date,
  similar_day_pool_rank2_reference_date,
  similar_day_pool_rank3_reference_date,
  similar_day_pool_rank1_distance,
  similar_day_pool_rank2_distance,
  similar_day_pool_rank3_distance,
  similar_day_pool_n_candidates,
  similar_day_pool_fit_cutoff,
  -- Always one of the pool's two windows: the override cannot reach it.
  datediff(trade_date, similar_day_pool_rank1_reference_date) as similar_day_pool_rank1_lag_days,
  {%- for suffix, observed, forecast_column in elements %}
  {%- for rank in ranks %}
  similar_day_pool_rank{{ rank }}_popw_{{ suffix }},
  {%- endfor %}
  wavg_similar_day_pool_top3_popw_{{ suffix }},
  {%- endfor %}
  {%- for suffix, observed, forecast_column in elements %}
  -- D's forecast minus the pool day's weather: positive when D is warmer, brighter.
  {%- for rank in ranks %}
  {{ forecast_column }} - similar_day_pool_rank{{ rank }}_popw_{{ suffix }} as delta_similar_day_pool_rank{{ rank }}_popw_{{ suffix }},
  {%- endfor %}
  {{ forecast_column }} - wavg_similar_day_pool_top3_popw_{{ suffix }} as delta_wavg_similar_day_pool_top3_popw_{{ suffix }},
  {%- endfor %}
  available_at,
  published_at
from final
