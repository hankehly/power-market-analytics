with
  -- Each observation day's hours, from the curated fact, with the bidding zone's code.
  hours as (
  select
    areas.area_code,
    fact.date_key as obs_date,
    fact.hour_ending,
    fact.popw_temperature_c,
    fact.popw_solar_radiation_mjm2,
    fact.available_at
  from
    {{ ref('fct_area_weather_hourly') }} as fact
    inner join {{ ref('dim_area') }} as areas on areas.area_key = fact.area_key
  ),

  -- One row per observation day. Every hour that has a value goes into one array
  -- in hour order: a fixed order whatever order Spark reads the rows in, so the
  -- means below are the same on every build.
  days as (
  select
    area_code,
    obs_date,
    array_sort(collect_list(
      case when popw_temperature_c is not null
        then named_struct('hour_ending', hour_ending, 'weight', cast(1 as double), 'value', popw_temperature_c)
      end
    )) as temperature_terms,
    array_sort(collect_list(
      case when hour_ending between 19 and 22 and popw_temperature_c is not null
        then named_struct('hour_ending', hour_ending, 'weight', cast(1 as double), 'value', popw_temperature_c)
      end
    )) as evening_temperature_terms,
    array_sort(collect_list(
      case when popw_solar_radiation_mjm2 is not null
        then named_struct('hour_ending', hour_ending, 'weight', cast(1 as double), 'value', popw_solar_radiation_mjm2)
      end
    )) as radiation_terms
  from
    hours
  group by
    area_code, obs_date
  ),

  -- Read two days later, the newest complete observation day before the issue time.
  -- Complete days only for the daily means; the evening mean needs its four hours.
  final as (
  select
    area_code,
    date_add(obs_date, 2) as trade_date,
    case when size(temperature_terms) = 24 then {{ ordered_weighted_mean('temperature_terms') }} end
      as lag_2d_mean_popw_temperature_c,
    case when size(evening_temperature_terms) = 4 then {{ ordered_weighted_mean('evening_temperature_terms') }} end
      as lag_2d_evening_mean_popw_temperature_c,
    case when size(radiation_terms) = 24 then {{ ordered_weighted_mean('radiation_terms') }} end
      as lag_2d_mean_popw_solar_radiation_mjm2,
    -- Hour 24's observed end + 1 h under the fact's rule: 01:00 on D-1, a rule
    -- rather than the latest hour present, so a day missing its last hours
    -- stamps the same instant, never earlier than any value the row holds.
    timestampadd(hour, 1, cast(date_add(obs_date, 1) as timestamp)) as available_at
  from
    days
  )

select * from final
