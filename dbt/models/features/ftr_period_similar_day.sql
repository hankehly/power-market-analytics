{%- set parts = ['calendar_days', 'temperature', 'humidity', 'rain', 'days_since_holiday', 'days_until_holiday', 'holiday_degree'] -%}
{%- set measures = ['temperature', 'humidity', 'precipitation'] %}
-- The similar day of every delivery day, scored in SQL with the weights a fit
-- published (scripts/fit_similar_day.py -> pma_ml.similar_day_parameters): the
-- selector of tasks/demand/similar_day.py, one row per period and parameter
-- vintage. Sums run in a fixed order (hours, stations), so a rebuild gives the
-- same values to the bit.
with
  -- Every fit, oldest first per area. The oldest vintage also scores the history
  -- before its fit: a backtest's training rows lie before the fit that serves its
  -- forecasts, as the weights were frozen at the first forecast day before.
  parameters as (
  select
    *,
    row_number() over (
      partition by area_code order by available_at, published_at, run_id
    ) as vintage_rank
  from {{ ref('stg_ml__similar_day_parameters') }}
  ),

  areas as (
  select area_key, area_code
  from {{ ref('dim_area') }}
  where area_code in (select area_code from parameters)
  ),

  -- The latest census vintage's station weights, as ftr_hour_msm applies them.
  weights as (
  select area_key, station_id, area_population_weight
  from {{ ref('fct_census_population_jma_station') }}
  where census_year = (select max(census_year) from {{ ref('fct_census_population_jma_station') }})
  ),

  -- A day needs both holiday distances to be a target or a candidate.
  calendar as (
  select area_code, trade_date, holiday_degree, days_since_holiday, days_until_holiday
  from {{ ref('ftr_day_calendar') }}
  where days_since_holiday is not null and days_until_holiday is not null
  ),

  -- Candidate side: the population-weighted observation of each hour, each
  -- measure over the stations reporting it, added in station order.
  observed_terms as (
  select
    areas.area_code,
    weather.date_key as obs_date,
    hour(weather.observed_hour_start_at) + 1 as hour_ending,
    {% for measure, column in [('temperature', 'temperature_c'), ('humidity', 'humidity_pct'), ('precipitation', 'precipitation_mm')] %}
    array_sort(collect_list(
      case when weather.{{ column }} is not null
        then named_struct(
          'station_id', weather.station_id,
          'weight', weights.area_population_weight,
          'value', weather.{{ column }}
        )
      end
    )) as {{ measure }}_terms,
    {% endfor %}
    max(weather.available_at) as available_at
  from
    {{ ref('fct_jma_weather_hourly') }} as weather
    inner join weights on weights.station_id = weather.station_id
    inner join areas on areas.area_key = weights.area_key
  group by
    areas.area_code, weather.date_key, hour(weather.observed_hour_start_at) + 1
  ),

  observed_hours as (
  select
    area_code,
    obs_date,
    hour_ending,
    {% for measure in measures %}
    {{ ordered_weighted_mean(measure ~ '_terms') }} as {{ measure }},
    {% endfor %}
    available_at
  from
    observed_terms
  ),

  -- A candidate's weather profile: all 24 hours of every measure, in hour order.
  observed_days as (
  select
    area_code,
    obs_date as candidate_date,
    array_sort(collect_list(named_struct(
      'hour_ending', hour_ending,
      'temperature', temperature,
      'humidity', humidity,
      'precipitation', precipitation
    ))) as hours,
    max(available_at) as available_at
  from
    observed_hours
  where
    temperature is not null and humidity is not null and precipitation is not null
  group by
    area_code, obs_date
  having
    count(*) = 24
  ),

  -- A candidate's load profile: all 24 hourly loads, in hour order.
  load_days as (
  select
    areas.area_code,
    loads.date_key as candidate_date,
    array_sort(collect_list(named_struct(
      'hour_ending', loads.hour_of_day + 1,
      'demand_kwh', loads.demand_kwh
    ))) as hours,
    max(loads.available_at) as available_at
  from
    {{ ref('fct_area_power_usage_hourly') }} as loads
    inner join areas on areas.area_key = loads.area_key
  where
    loads.demand_kwh is not null
  group by
    areas.area_code, loads.date_key
  having
    count(*) = 24
  ),

  candidates as (
  select
    observed.area_code,
    observed.candidate_date,
    {% for measure in measures %}
    transform(observed.hours, x -> x.{{ measure }}) as {{ measure }},
    {% endfor %}
    transform(loads.hours, x -> x.demand_kwh) as demand_kwh,
    calendar.holiday_degree,
    calendar.days_since_holiday,
    calendar.days_until_holiday,
    greatest(observed.available_at, loads.available_at) as available_at
  from
    observed_days as observed
    inner join load_days as loads
      on loads.area_code = observed.area_code
      and loads.candidate_date = observed.candidate_date
    inner join calendar
      on calendar.area_code = observed.area_code
      and calendar.trade_date = observed.candidate_date
  ),

  first_candidates as (
  select area_code, min(candidate_date) as first_candidate_date
  from candidates
  group by area_code
  ),

  -- Target side: the population-weighted forecast profile of one vintage, all
  -- 24 hours of every measure, in hour order.
  forecast_days as (
  select
    area_code,
    trade_date,
    forecast_reference_at,
    array_sort(collect_list(named_struct(
      'hour_ending', hour_ending,
      'temperature', popw_forecast_temperature_c,
      'humidity', popw_forecast_relative_humidity_pct,
      'precipitation', popw_forecast_precipitation_mm
    ))) as hours,
    max(available_at) as available_at
  from
    {{ ref('ftr_hour_msm') }}
  where
    area_code in (select area_code from parameters)
    and popw_forecast_temperature_c is not null
    and popw_forecast_relative_humidity_pct is not null
    and popw_forecast_precipitation_mm is not null
  group by
    area_code, trade_date, forecast_reference_at
  having
    count(*) = 24
  ),

  targets as (
  select
    forecasts.area_code,
    forecasts.trade_date,
    forecasts.forecast_reference_at,
    {% for measure in measures %}
    transform(forecasts.hours, x -> x.{{ measure }}) as {{ measure }},
    {% endfor %}
    calendar.holiday_degree,
    calendar.days_since_holiday,
    calendar.days_until_holiday,
    forecasts.available_at
  from
    forecast_days as forecasts
    inner join calendar
      on calendar.area_code = forecasts.area_code
      and calendar.trade_date = forecasts.trade_date
  ),

  -- Every window day of every target under every vintage. A target whose window
  -- would start before the area's first candidate day is not scored.
  windows as (
  select
    targets.area_code,
    targets.trade_date,
    targets.forecast_reference_at,
    {% for measure in measures %}
    targets.{{ measure }} as target_{{ measure }},
    {% endfor %}
    targets.holiday_degree as target_holiday_degree,
    targets.days_since_holiday as target_days_since_holiday,
    targets.days_until_holiday as target_days_until_holiday,
    targets.available_at as target_available_at,
    parameters.run_id as parameters_run_id,
    parameters.vintage_rank,
    parameters.center_lag_days,
    {% for part in parts %}
    parameters.weight_{{ part }},
    parameters.scale_{{ part }},
    {% endfor %}
    parameters.available_at as parameters_available_at,
    parameters.published_at,
    lags.lag_days,
    date_sub(targets.trade_date, lags.lag_days) as candidate_date
  from
    targets
    inner join parameters on parameters.area_code = targets.area_code
    inner join first_candidates on first_candidates.area_code = targets.area_code
    lateral view explode(sequence(
      parameters.center_lag_days - parameters.window_half_width_days,
      parameters.center_lag_days + parameters.window_half_width_days
    )) lags as lag_days
  where
    date_sub(targets.trade_date, parameters.center_lag_days + parameters.window_half_width_days)
      >= first_candidates.first_candidate_date
  ),

  -- The seven parts of every (target, candidate) pair.
  pairs as (
  select
    windows.area_code,
    windows.trade_date,
    windows.forecast_reference_at,
    windows.target_available_at,
    windows.parameters_run_id,
    windows.vintage_rank,
    windows.center_lag_days,
    {% for part in parts %}
    windows.weight_{{ part }},
    windows.scale_{{ part }},
    {% endfor %}
    windows.parameters_available_at,
    windows.published_at,
    windows.lag_days,
    windows.candidate_date,
    abs(windows.lag_days - windows.center_lag_days) as calendar_days,
    {% for measure, part in [('temperature', 'temperature'), ('humidity', 'humidity'), ('precipitation', 'rain')] %}
    {{ profile_rmse('windows.target_' ~ measure, 'candidates.' ~ measure) }} as {{ part }},
    {% endfor %}
    abs(windows.target_days_since_holiday - candidates.days_since_holiday) as days_since_holiday,
    abs(windows.target_days_until_holiday - candidates.days_until_holiday) as days_until_holiday,
    abs(windows.target_holiday_degree - candidates.holiday_degree) as holiday_degree,
    candidates.demand_kwh,
    candidates.available_at as candidate_available_at
  from
    windows
    inner join candidates
      on candidates.area_code = windows.area_code
      and candidates.candidate_date = windows.candidate_date
  ),

  -- d = sqrt(sum_j w_j (part_j / s_j)^2), the parts in their fixed order.
  distances as (
  select
    *,
    sqrt(
      {% for part in parts %}
      {{ '+ ' if not loop.first }}weight_{{ part }} * pow({{ part }} / scale_{{ part }}, 2)
      {% endfor %}
    ) as distance
  from
    pairs
  ),

  -- The nearest candidate; ties go to the day nearest the window's centre,
  -- then the earlier date.
  ranked as (
  select
    *,
    row_number() over (
      partition by area_code, trade_date, forecast_reference_at, parameters_run_id
      order by distance, abs(lag_days - center_lag_days), candidate_date
    ) as distance_rank,
    count(*) over (
      partition by area_code, trade_date, forecast_reference_at, parameters_run_id
    ) as n_candidates,
    max(candidate_available_at) over (
      partition by area_code, trade_date, forecast_reference_at, parameters_run_id
    ) as candidates_available_at
  from
    distances
  ),

  final as (
  select
    ranked.area_code,
    ranked.trade_date,
    periods.time_code,
    ranked.forecast_reference_at,
    ranked.parameters_run_id,
    -- The chosen day's load over the hour containing the period, halved.
    element_at(ranked.demand_kwh, cast((periods.time_code + 1) div 2 as int)) / 2
      as similar_day_demand_kwh,
    ranked.candidate_date as similar_day_reference_date,
    ranked.lag_days as similar_day_reference_lag_days,
    ranked.distance as similar_day_distance,
    cast(ranked.n_candidates as int) as similar_day_n_candidates,
    -- The data's instant (the forecast vintage, the window's candidates) and,
    -- for every vintage but the area's oldest, the fit's own.
    case
      when ranked.vintage_rank = 1
        then {{ available_at(['ranked.target_available_at', 'ranked.candidates_available_at']) }}
      else {{ available_at(['ranked.target_available_at', 'ranked.candidates_available_at', 'ranked.parameters_available_at']) }}
    end as available_at,
    ranked.published_at
  from
    ranked
    lateral view explode(sequence(1, 48)) periods as time_code
  where
    ranked.distance_rank = 1
  )

select * from final
