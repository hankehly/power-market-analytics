{#- One text for both elements: the observed column of fct_area_weather_hourly,
    the forecast column of ftr_hour_msm and the suffix the feature names carry. -#}
{%- set elements = [
  ('temperature_c', 'popw_temperature_c', 'popw_forecast_temperature_c'),
  ('solar_radiation_mjm2', 'popw_solar_radiation_mjm2', 'popw_forecast_solar_radiation_mjm2'),
] -%}

with
  -- The load window's rows: the four dates the day-type means read, at every
  -- period, and the hour containing the period.
  windows as (
  select
    area_code,
    trade_date,
    time_code,
    cast(div(time_code + 1, 2) as int) as hour_ending,
    daytype_4d_date_1,
    daytype_4d_date_2,
    daytype_4d_date_3,
    daytype_4d_date_4,
    available_at
  from {{ ref('ftr_period_actuals') }}
  where daytype_4d_date_1 is not null
  ),

  -- The area's population-weighted observed weather by hour.
  hours as (
  select
    areas.area_code,
    fact.date_key,
    fact.hour_ending,
    fact.popw_temperature_c,
    fact.popw_solar_radiation_mjm2,
    fact.available_at
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
    popw_forecast_solar_radiation_mjm2,
    available_at
  from {{ ref('ftr_hour_msm') }}
  ),

  -- The window's four hours side by side, newest day first, and D's forecast.
  -- greatest() skips a null, so a missing hour or forecast leaves available_at
  -- to the inputs the row has.
  joined as (
  select
    windows.area_code,
    windows.trade_date,
    windows.time_code,
    {%- for suffix, observed, forecast_column in elements %}
    {%- for rank in [1, 2, 3, 4] %}
    hours_{{ rank }}.{{ observed }} as {{ suffix }}_{{ rank }},
    {%- endfor %}
    forecast.{{ forecast_column }},
    {%- endfor %}
    {{ available_at([
      'windows.available_at',
      'hours_1.available_at', 'hours_2.available_at', 'hours_3.available_at', 'hours_4.available_at',
      'forecast.available_at',
    ]) }} as available_at
  from windows
    {%- for rank in [1, 2, 3, 4] %}
    left join hours as hours_{{ rank }}
      on hours_{{ rank }}.area_code = windows.area_code
      and hours_{{ rank }}.date_key = windows.daytype_4d_date_{{ rank }}
      and hours_{{ rank }}.hour_ending = windows.hour_ending
    {%- endfor %}
    left join forecast
      on forecast.area_code = windows.area_code
      and forecast.trade_date = windows.trade_date
      and forecast.hour_ending = windows.hour_ending
  ),

  -- The load feature's arithmetic (ftr_period_actuals' day-type means) over the
  -- days whose hour is present: the plain mean, and the weights 8, 4, 2, 1 from the
  -- newest day back, each added newest first; null when no day has the hour.
  final as (
  select
    area_code,
    trade_date,
    time_code,
    {%- for suffix, observed, forecast_column in elements %}
    (coalesce({{ suffix }}_1, 0)
      + coalesce({{ suffix }}_2, 0)
      + coalesce({{ suffix }}_3, 0)
      + coalesce({{ suffix }}_4, 0))
    / nullif(cast({{ suffix }}_1 is not null as int)
      + cast({{ suffix }}_2 is not null as int)
      + cast({{ suffix }}_3 is not null as int)
      + cast({{ suffix }}_4 is not null as int), 0) as mean_daytype_4d_popw_{{ suffix }},
    (coalesce(8 * {{ suffix }}_1, 0)
      + coalesce(4 * {{ suffix }}_2, 0)
      + coalesce(2 * {{ suffix }}_3, 0)
      + coalesce({{ suffix }}_4, 0))
    / nullif(8 * cast({{ suffix }}_1 is not null as int)
      + 4 * cast({{ suffix }}_2 is not null as int)
      + 2 * cast({{ suffix }}_3 is not null as int)
      + cast({{ suffix }}_4 is not null as int), 0) as ewm_daytype_4d_popw_{{ suffix }},
    {%- endfor %}
    {%- for suffix, observed, forecast_column in elements %}
    {{ forecast_column }},
    {%- endfor %}
    available_at
  from joined
  )

select
  area_code,
  trade_date,
  time_code,
  {%- for suffix, observed, forecast_column in elements %}
  mean_daytype_4d_popw_{{ suffix }},
  ewm_daytype_4d_popw_{{ suffix }},
  {%- endfor %}
  {%- for suffix, observed, forecast_column in elements %}
  -- D's forecast minus the window's weather: positive when D is warmer, brighter.
  {{ forecast_column }} - mean_daytype_4d_popw_{{ suffix }} as delta_mean_daytype_4d_popw_{{ suffix }},
  {{ forecast_column }} - ewm_daytype_4d_popw_{{ suffix }} as delta_ewm_daytype_4d_popw_{{ suffix }},
  {%- endfor %}
  available_at
from final
