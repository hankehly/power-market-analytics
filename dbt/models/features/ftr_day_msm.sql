with
  -- One row per delivery day and vintage. Every hour that has a temperature goes
  -- into one array in hour order: a fixed order whatever order Spark reads the
  -- rows in, so the mean below is the same on every build.
  days as (
  select
    area_code,
    trade_date,
    forecast_reference_at,
    count(popw_forecast_temperature_c) as n_hours,
    max(popw_forecast_temperature_c) as max_temperature_c,
    min(popw_forecast_temperature_c) as min_temperature_c,
    -- The earliest hour on a tie: among equal temperatures the largest -hour_ending wins.
    max_by(
      hour_ending,
      named_struct('temperature_c', popw_forecast_temperature_c, 'earliest', -hour_ending)
    ) as max_temperature_hour_ending,
    array_sort(collect_list(
      case when popw_forecast_temperature_c is not null
        then named_struct(
          'hour_ending', hour_ending,
          'weight', cast(1 as double),
          'value', popw_forecast_temperature_c
        )
      end
    )) as temperature_terms,
    -- The four evening hours, ending 19:00 to 22:00, the load's 18:00 to 22:00.
    array_sort(collect_list(
      case when hour_ending between 19 and 22 and popw_forecast_temperature_c is not null
        then named_struct(
          'hour_ending', hour_ending,
          'weight', cast(1 as double),
          'value', popw_forecast_temperature_c
        )
      end
    )) as evening_temperature_terms,
    count(popw_forecast_solar_radiation_mjm2) as n_radiation_hours,
    array_sort(collect_list(
      case when popw_forecast_solar_radiation_mjm2 is not null
        then named_struct(
          'hour_ending', hour_ending,
          'weight', cast(1 as double),
          'value', popw_forecast_solar_radiation_mjm2
        )
      end
    )) as radiation_terms,
    -- The four morning hours, ending 07:00 to 10:00, for the trend.
    max(case when hour_ending = 7 then popw_forecast_temperature_c end) as temperature_07_c,
    max(case when hour_ending = 8 then popw_forecast_temperature_c end) as temperature_08_c,
    max(case when hour_ending = 9 then popw_forecast_temperature_c end) as temperature_09_c,
    max(case when hour_ending = 10 then popw_forecast_temperature_c end) as temperature_10_c,
    max(available_at) as available_at
  from {{ ref('ftr_hour_msm') }}
  group by area_code, trade_date, forecast_reference_at
  ),

  -- Complete days only: a day with fewer than 24 temperatures has no summary.
  forecast as (
  select
    area_code,
    trade_date,
    forecast_reference_at,
    case when n_hours = 24 then max_temperature_c end as max_popw_forecast_temperature_c,
    case when n_hours = 24 then min_temperature_c end as min_popw_forecast_temperature_c,
    case when n_hours = 24 then {{ ordered_weighted_mean('temperature_terms') }} end
      as mean_popw_forecast_temperature_c,
    case when n_hours = 24 then max_temperature_hour_ending end
      as max_popw_forecast_temperature_hour_ending,
    -- The least-squares slope of the temperature against the hour over the four
    -- morning hours, C per hour. With the hours fixed at 7 to 10 the weights are
    -- (t - 8.5) / 5 = -0.3, -0.1, 0.1, 0.3, written over differences. The textbook
    -- (n Stx - St Sx) / (n Stt - St^2) is the same number and subtracts two large,
    -- nearly equal sums: it is off by 3e-14 where this is within one ulp. Null
    -- unless all four hours have a temperature; the rest of the day is not needed.
    (3 * (temperature_10_c - temperature_07_c) + (temperature_09_c - temperature_08_c)) / 10
      as morning_trend_popw_forecast_temperature_c,
    -- The evening mean needs its four hours only; the radiation mean, complete days only.
    case when size(evening_temperature_terms) = 4 then {{ ordered_weighted_mean('evening_temperature_terms') }} end
      as evening_mean_popw_forecast_temperature_c,
    case when n_radiation_hours = 24 then {{ ordered_weighted_mean('radiation_terms') }} end
      as mean_popw_forecast_solar_radiation_mjm2,
    {{ available_at(['available_at']) }} as available_at
  from days
  ),

  -- D-2's observed means, the weather the D-2 daily load features were recorded under.
  observed as (
  select
    area_code,
    trade_date,
    lag_2d_mean_popw_temperature_c,
    lag_2d_evening_mean_popw_temperature_c,
    lag_2d_mean_popw_solar_radiation_mjm2,
    available_at
  from {{ ref('ftr_day_jma_obs') }}
  ),

  final as (
  select
    forecast.area_code,
    forecast.trade_date,
    forecast.forecast_reference_at,
    forecast.max_popw_forecast_temperature_c,
    forecast.min_popw_forecast_temperature_c,
    forecast.mean_popw_forecast_temperature_c,
    forecast.max_popw_forecast_temperature_hour_ending,
    forecast.morning_trend_popw_forecast_temperature_c,
    forecast.evening_mean_popw_forecast_temperature_c,
    forecast.mean_popw_forecast_solar_radiation_mjm2,
    -- D's forecast minus D-2's observation: positive when D is warmer or brighter
    -- than the day the D-2 load features come from. Null without a sibling row.
    forecast.mean_popw_forecast_temperature_c - observed.lag_2d_mean_popw_temperature_c
      as delta_lag_2d_mean_popw_temperature_c,
    forecast.evening_mean_popw_forecast_temperature_c - observed.lag_2d_evening_mean_popw_temperature_c
      as delta_lag_2d_evening_mean_popw_temperature_c,
    forecast.mean_popw_forecast_solar_radiation_mjm2 - observed.lag_2d_mean_popw_solar_radiation_mjm2
      as delta_lag_2d_mean_popw_solar_radiation_mjm2,
    -- The vintage's instant: the sibling's, 01:00 on D-1, is never later.
    {{ available_at(['forecast.available_at', 'observed.available_at']) }} as available_at
  from
    forecast
    left join observed
      on observed.area_code = forecast.area_code
      and observed.trade_date = forecast.trade_date
  )

select * from final
