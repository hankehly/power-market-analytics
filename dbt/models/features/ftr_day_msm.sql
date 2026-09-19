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
    max(available_at) as available_at
  from {{ ref('ftr_hour_msm') }}
  group by area_code, trade_date, forecast_reference_at
  ),

  -- Complete days only: a day with fewer than 24 temperatures has no summary.
  final as (
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
    {{ available_at(['available_at']) }} as available_at
  from days
  )

select * from final
