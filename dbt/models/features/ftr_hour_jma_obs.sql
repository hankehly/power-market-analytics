{#- The weights of the 72-hour exponentially weighted mean, oldest hour first: a
    half-life of 24 hours, 0.5^(k / 24) for k = 71 ... 0 hours back. They are
    computed here, when the model compiles, and land in the SQL as literals, so
    the SQL only adds and multiplies. Spark's pow() and another system's can
    differ in the last bit; a literal cannot. #}
{%- set ewm_72h_weights = [] -%}
{%- for i in range(72) -%}
  {%- do ewm_72h_weights.append(0.5 ** ((71 - i) / 24)) -%}
{%- endfor -%}

with
  areas as (
  select area_code, representative_jma_station_id as station_id
  from {{ ref('dim_area') }}
  where representative_jma_station_id is not null
  ),

  observations as (
  select
    areas.area_code,
    weather.date_key as obs_date,
    hour(weather.observed_hour_start_at) + 1 as hour_ending,
    weather.temperature_c
  from
    {{ ref('fct_jma_weather_hourly') }} as weather
    inner join areas on areas.station_id = weather.station_id
  ),

  -- The area's population-weighted observed hour, from the curated fact, which
  -- weighs the stations as ftr_hour_msm weighs the forecast (the weighting was
  -- computed here until 2026-09-29; the fact gives the same value to the bit).
  -- hour_index counts the hours along the clock, so a window can cross midnight.
  area_hours as (
  select
    all_areas.area_code,
    fact.date_key as obs_date,
    fact.hour_ending,
    cast(div(unix_timestamp(fact.observed_hour_start_at), 3600) as bigint) as hour_index,
    fact.popw_temperature_c,
    fact.popw_solar_radiation_mjm2
  from
    {{ ref('fct_area_weather_hourly') }} as fact
    inner join {{ ref('dim_area') }} as all_areas on all_areas.area_key = fact.area_key
  ),

  -- The 72 hours ending at each hour, those that have a value, oldest first.
  hour_windows as (
  select
    area_code,
    obs_date,
    hour_ending,
    hour_index,
    array_sort(collect_list(
      case when popw_temperature_c is not null
        then named_struct('idx', hour_index, 'value', popw_temperature_c)
      end
    ) over (
      partition by area_code
      order by hour_index
      range between 71 preceding and current row
    )) as last_72_hours
  from
    area_hours
  ),

  -- The windows read at the target hour on D-2. Complete windows only: a missing
  -- station does not break one, an hour no station reports does. Every sum is
  -- taken oldest hour first.
  accumulated as (
  select
    area_code,
    date_add(obs_date, 2) as trade_date,
    hour_ending,
    case when size(last_24_hours) = 24 then
      aggregate(last_24_hours, cast(0 as double), (acc, h) -> acc + h.value) / 24
    end as mean_24h_popw_temperature_c,
    case when size(last_72_hours) = 72 then
      aggregate(last_72_hours, cast(0 as double), (acc, h) -> acc + h.value) / 72
    end as mean_72h_popw_temperature_c,
    case when size(last_72_hours) = 72 then
      aggregate(
        zip_with(last_72_hours, ewm_72h_weights, (h, w) -> h.value * w),
        cast(0 as double), (acc, x) -> acc + x)
      / aggregate(ewm_72h_weights, cast(0 as double), (acc, w) -> acc + w)
    end as ewm_72h_popw_temperature_c
  from (
    select
      hour_windows.*,
      filter(last_72_hours, h -> h.idx > hour_index - 24) as last_24_hours,
      array(
        {%- for weight in ewm_72h_weights %}
        cast({{ weight }} as double){{ "," if not loop.last }}
        {%- endfor %}
      ) as ewm_72h_weights
    from
      hour_windows
  )
  ),

  -- D-2 .. D-8: the seven complete observation days before 09:30 on D-1.
  lags as (
  select explode(sequence(2, 8)) as lag_days
  ),

  contributions as (
  select
    observations.area_code,
    date_add(observations.obs_date, lags.lag_days) as trade_date,
    observations.hour_ending,
    lags.lag_days,
    -- Weight halves for every day further back: D-2 -> 1, D-3 -> 1/2, ... D-8 -> 1/64.
    pow(0.5, lags.lag_days - 2) as weight,
    observations.temperature_c
  from
    observations
    cross join lags
  ),

  windows as (
  select
    area_code,
    trade_date,
    hour_ending,
    -- The lags that have a value, D-2 first and D-8 last: a fixed order whatever
    -- order Spark reads the rows in, so the sum below is the same on every build.
    array_sort(collect_list(
      case when temperature_c is not null
        then named_struct('lag_days', lag_days, 'weight', weight, 'value', temperature_c)
      end
    )) as terms
  from
    contributions
  group by
    area_code, trade_date, hour_ending
  ),

  final as (
  select
    windows.area_code,
    windows.trade_date,
    windows.hour_ending,
    -- Added in lag order, renormalised over the lags present; null when none is.
    {{ ordered_weighted_mean('windows.terms') }} as wavg_temperature_c,
    accumulated.mean_24h_popw_temperature_c,
    accumulated.mean_72h_popw_temperature_c,
    accumulated.ewm_72h_popw_temperature_c,
    -- The weather the load lags were recorded under: the fact at the same hour
    -- two and seven days back. Null where the fact has no such hour.
    lag_2d.popw_temperature_c as lag_2d_popw_temperature_c,
    lag_7d.popw_temperature_c as lag_7d_popw_temperature_c,
    lag_2d.popw_solar_radiation_mjm2 as lag_2d_popw_solar_radiation_mjm2,
    lag_7d.popw_solar_radiation_mjm2 as lag_7d_popw_solar_radiation_mjm2,
    -- Public once the newest observation the window can hold, D-2's, is:
    -- its hour end + 1 h. The accumulated windows and the D-2 siblings end at
    -- the same hour, and the D-7 siblings are older, so the same instant holds.
    timestampadd(hour, windows.hour_ending + 1, cast(date_sub(windows.trade_date, 2) as timestamp)) as available_at
  from
    windows
    left join accumulated
      on accumulated.area_code = windows.area_code
      and accumulated.trade_date = windows.trade_date
      and accumulated.hour_ending = windows.hour_ending
    left join area_hours as lag_2d
      on lag_2d.area_code = windows.area_code
      and lag_2d.obs_date = date_sub(windows.trade_date, 2)
      and lag_2d.hour_ending = windows.hour_ending
    left join area_hours as lag_7d
      on lag_7d.area_code = windows.area_code
      and lag_7d.obs_date = date_sub(windows.trade_date, 7)
      and lag_7d.hour_ending = windows.hour_ending
  )

select * from final
