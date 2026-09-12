{#- The MSM elements weighted over the area's stations, in output order. Each gets
    the same station-ordered weighted mean, named popw_forecast_<element>. #}
{%- set weighted_elements = [
  'temperature_c',
  'relative_humidity_pct',
  'precipitation_mm',
  'solar_radiation_mjm2',
  'total_cloud_cover_pct',
  'high_cloud_cover_pct',
  'middle_cloud_cover_pct',
  'low_cloud_cover_pct',
  'wind_speed_ms',
  'u_wind_ms',
  'v_wind_ms',
  'surface_pressure_hpa',
  'sea_level_pressure_hpa',
] -%}

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
    {%- for element in weighted_elements %}
    {{ element }},
    {%- endfor %}
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

  -- Each element's stations that have a value for the hour, in station order: a
  -- fixed order whatever order Spark reads the rows in, so the sums below are the
  -- same on every build.
  station_terms as (
  select
    areas.area_code,
    forecasts.trade_date,
    forecasts.hour_ending,
    forecasts.forecast_reference_at,
    weights.census_year,
    {%- for element in weighted_elements %}
    array_sort(collect_list(
      case when forecasts.{{ element }} is not null
        then named_struct(
          'station_id', forecasts.station_id,
          'weight', weights.area_population_weight,
          'value', forecasts.{{ element }}
        )
      end
    )) as {{ element }}_terms,
    {%- endfor %}
    max(forecasts.available_at) as available_at
  from
    forecasts
    inner join weights on weights.station_id = forecasts.station_id
    inner join areas on areas.area_key = weights.area_key
  group by
    areas.area_code, forecasts.trade_date, forecasts.hour_ending, forecasts.forecast_reference_at, weights.census_year
  ),

  -- Each element added in station order, renormalised over the stations present.
  weighted as (
  select
    area_code,
    trade_date,
    hour_ending,
    forecast_reference_at,
    census_year,
    {%- for element in weighted_elements %}
    {{ ordered_weighted_mean(element ~ '_terms') }} as popw_forecast_{{ element }},
    {%- endfor %}
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
    {%- for element in weighted_elements %}
    weighted.popw_forecast_{{ element }},
    {%- endfor %}
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
