with
  source as (
  select
    census_year,
    census_date,
    geodetic_datum,
    stats_id,
    primary_mesh_code,
    mesh_code,
    privacy_processing_code,
    aggregation_destination_mesh_code,
    aggregation_source_mesh_codes,
    population_total,
    source_file
  from
    {{ source('estat', 'estat_census_population_mesh') }}
  )

select * from source
