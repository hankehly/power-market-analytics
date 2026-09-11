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
    area_code,
    trade_date,
    hour_ending,
    -- Added in lag order, renormalised over the lags present; null when none is.
    {{ ordered_weighted_mean('terms') }} as wavg_temperature_c,
    -- Public once the newest observation the window can hold, D-2's, is:
    -- its hour end + 1 h.
    timestampadd(hour, hour_ending + 1, cast(date_sub(trade_date, 2) as timestamp)) as available_at
  from
    windows
  )

select * from final
