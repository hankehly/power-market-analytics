-- The only populated territory outside every JEPX area is Okinawa Prefecture,
-- and it has no staffed station in dim_jma_station (the seed keeps JEPX-area
-- stations only). A nearest-station assignment over the nationwide census
-- would therefore hand every Okinawa mesh to the nearest Kyushu station
-- (沖永良部 s47942 — 1.5 M people on a 12 k-person island) and inflate the
-- Kyushu weights. The model excludes those meshes by a geographic rule; this
-- test pins the rule to the official census population of Okinawa Prefecture
-- (令和2年国勢調査 1,467,480; 平成27年 1,433,566): the population the model
-- leaves unassigned must equal it exactly, per vintage.
with
  expected as (
  select 2015 as census_year, 1433566 as okinawa_population
  union all
  select 2020 as census_year, 1467480 as okinawa_population
  ),

  meshes as (
  select census_year, sum(population_total) as population_total
  from {{ ref('fct_census_population_mesh') }}
  group by census_year
  ),

  assigned as (
  select census_year, sum(population_total) as population_total
  from {{ ref('fct_census_population_jma_station') }}
  group by census_year
  )

select
  expected.census_year,
  expected.okinawa_population,
  meshes.population_total - coalesce(assigned.population_total, 0) as unassigned_population
from
  expected
  left join meshes
    on meshes.census_year = expected.census_year
  left join assigned
    on assigned.census_year = expected.census_year
where
  meshes.population_total is null
  or meshes.population_total - coalesce(assigned.population_total, 0) <> expected.okinawa_population
