with
  final as (
  select
    census_year,
    census_date,
    mesh_code,
    population_total
  from
    {{ ref('std_estat__census_population_mesh') }}
  )

select * from final
