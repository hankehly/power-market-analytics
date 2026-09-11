with
  areas as (
  select area_key, area_code, representative_jma_station_id
  from {{ ref('dim_area') }}
  where representative_jma_station_id is not null
  ),

  -- The latest census vintage's station weights, as the Python loader picks them.
  weights as (
  select census_year, area_key, station_id, area_population_weight
  from {{ ref('fct_census_population_jma_station') }}
  where census_year = (select max(census_year) from {{ ref('fct_census_population_jma_station') }})
  ),

  forecasts as (
  select
    station_id,
    date_key as trade_date,
    hour(forecast_hour_start_at) + 1 as hour_ending,
    forecast_reference_at,
    temperature_c,
    relative_humidity_pct,
    precipitation_mm,
    available_at
  from {{ ref('fct_jma_msm_weather_forecast_hourly') }}
  ),

  representative as (
  select
    areas.area_code,
    forecasts.trade_date,
    forecasts.hour_ending,
    forecasts.forecast_reference_at,
    forecasts.temperature_c as forecast_temperature_c,
    forecasts.available_at
  from
    forecasts
    inner join areas on areas.representative_jma_station_id = forecasts.station_id
  ),

  -- Each measure's stations that have a value for the hour, in station order: a
  -- fixed order whatever order Spark reads the rows in, so the sums below are the
  -- same on every build.
  station_terms as (
  select
    areas.area_code,
    forecasts.trade_date,
    forecasts.hour_ending,
    forecasts.forecast_reference_at,
    weights.census_year,
    array_sort(collect_list(
      case when forecasts.temperature_c is not null
        then named_struct(
          'station_id', forecasts.station_id,
          'weight', weights.area_population_weight,
          'value', forecasts.temperature_c
        )
      end
    )) as temperature_terms,
    array_sort(collect_list(
      case when forecasts.relative_humidity_pct is not null
        then named_struct(
          'station_id', forecasts.station_id,
          'weight', weights.area_population_weight,
          'value', forecasts.relative_humidity_pct
        )
      end
    )) as humidity_terms,
    array_sort(collect_list(
      case when forecasts.precipitation_mm is not null
        then named_struct(
          'station_id', forecasts.station_id,
          'weight', weights.area_population_weight,
          'value', forecasts.precipitation_mm
        )
      end
    )) as precipitation_terms,
    max(forecasts.available_at) as available_at
  from
    forecasts
    inner join weights on weights.station_id = forecasts.station_id
    inner join areas on areas.area_key = weights.area_key
  group by
    areas.area_code, forecasts.trade_date, forecasts.hour_ending, forecasts.forecast_reference_at, weights.census_year
  ),

  -- Each measure added in station order, renormalised over the stations present.
  weighted as (
  select
    area_code,
    trade_date,
    hour_ending,
    forecast_reference_at,
    census_year,
    {{ ordered_weighted_mean('temperature_terms') }} as popw_forecast_temperature_c,
    {{ ordered_weighted_mean('humidity_terms') }} as popw_forecast_relative_humidity_pct,
    {{ ordered_weighted_mean('precipitation_terms') }} as popw_forecast_precipitation_mm,
    available_at
  from
    station_terms
  ),

  final as (
  select
    coalesce(representative.area_code, weighted.area_code) as area_code,
    coalesce(representative.trade_date, weighted.trade_date) as trade_date,
    coalesce(representative.hour_ending, weighted.hour_ending) as hour_ending,
    coalesce(representative.forecast_reference_at, weighted.forecast_reference_at) as forecast_reference_at,
    weighted.census_year,
    representative.forecast_temperature_c,
    weighted.popw_forecast_temperature_c,
    weighted.popw_forecast_relative_humidity_pct,
    weighted.popw_forecast_precipitation_mm,
    {{ available_at(['representative.available_at', 'weighted.available_at']) }} as available_at
  from
    representative
    full outer join weighted
      on weighted.area_code = representative.area_code
      and weighted.trade_date = representative.trade_date
      and weighted.hour_ending = representative.hour_ending
      and weighted.forecast_reference_at = representative.forecast_reference_at
  )

select * from final
