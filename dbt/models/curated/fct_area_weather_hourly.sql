{#- The observed elements weighted over the area's stations, in output order. Each
    gets the same station-ordered weighted mean, named popw_<element>, with the
    count and the population share of the stations that reported it. #}
{%- set weighted_elements = ['temperature_c', 'solar_radiation_mjm2'] -%}

with
  -- The latest census vintage's station weights, as ftr_hour_msm takes them.
  weights as (
  select census_year, area_key, station_id, area_population_weight
  from {{ ref('fct_census_population_jma_station') }}
  where census_year = (select max(census_year) from {{ ref('fct_census_population_jma_station') }})
  ),

  -- Each element's stations that report it this hour, in station order: a fixed
  -- order whatever order Spark reads the rows in, so the sums below are the same
  -- on every build. A station without a weight (no census row) is not an input.
  hour_terms as (
  select
    weights.area_key,
    weights.census_year,
    weather.observed_at,
    weather.observed_hour_start_at,
    weather.date_key,
    hour(weather.observed_hour_start_at) + 1 as hour_ending,
    {%- for element in weighted_elements %}
    array_sort(collect_list(
      case when weather.{{ element }} is not null
        then named_struct(
          'station_id', weather.station_id,
          'weight', weights.area_population_weight,
          'value', weather.{{ element }}
        )
      end
    )) as {{ element }}_terms,
    {%- endfor %}
    max(weather.available_at) as available_at
  from
    {{ ref('fct_jma_weather_hourly') }} as weather
    inner join weights on weights.station_id = weather.station_id
  group by
    weights.area_key, weights.census_year, weather.observed_at, weather.observed_hour_start_at, weather.date_key
  ),

  final as (
  select
    area_key,
    date_key,
    hour_ending,
    observed_at,
    observed_hour_start_at,
    census_year,
    {%- for element in weighted_elements %}
    {%- set label = element.rsplit('_', 1)[0] %}
    -- Added in station order, renormalised over the stations present; null when none is.
    {{ ordered_weighted_mean(element ~ '_terms') }} as popw_{{ element }},
    -- The count and the share carry the element's name without its unit.
    cast(size({{ element }}_terms) as int) as n_stations_{{ label }},
    -- A coverage number, rounded to twelve decimals: an area's weights add up to 1
    -- only within floating rounding (Kyushu's 25 give 1.0000000000000002).
    round(aggregate({{ element }}_terms, cast(0 as double), (acc, x) -> acc + x.weight), 12)
      as weight_share_{{ label }},
    {%- endfor %}
    available_at
  from
    hour_terms
  )

select * from final
