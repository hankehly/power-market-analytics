-- Census population represented by each staffed JMA station: every 500 m census
-- mesh is assigned to the station nearest its centroid (great-circle distance
-- on the mesh's JGD2000 / station's WGS84 coordinates, which agree to well under
-- a mesh width), and the station's population is the sum over its meshes.
--
-- Candidate stations are the staffed stations mapped to a JEPX area with an
-- elevation of at most 1,000 m. The five higher stations (富士山 3,775 m,
-- 剣山 1,945 m, 伊吹山 1,376 m, 奥日光 1,292 m, 阿蘇山 1,142 m) sit far above the
-- lowland towns that would otherwise be nearest to them, and the MSM forecast
-- at their grid points is documented as unrepresentative of those towns
-- (docs/JMA-MSM-GPV-Retrieval.md §9.4); their meshes fall to the next-nearest
-- lower station instead, so they have no row here.
--
-- area_population_weight is the station's share of its JEPX area's population,
-- where an area's population is the sum over its weighted stations — so a mesh
-- belongs to the area of its nearest station, an approximation of the TSO
-- supply-area boundary that is exact away from area borders. Weights sum to 1
-- per (census_year, area_key) (asserted by a singular test) and are the
-- population weights for any "area-wide" station average (e.g. a
-- population-weighted area temperature).
--
-- Grain: one row per census_year and station_id (stations that are nearest to
-- no populated mesh are absent, i.e. weight 0).
with
  meshes as (
  select
    f.census_year,
    f.mesh_code,
    f.population_total,
    radians(d.centroid_latitude) as lat,
    radians(d.centroid_longitude) as lon
  from
    {{ ref('fct_census_population_mesh') }} f
    join {{ ref('dim_population_mesh_500m') }} d
      on d.mesh_code = f.mesh_code
  ),

  stations as (
  select
    station_id,
    area_key,
    radians(latitude) as lat,
    radians(longitude) as lon
  from
    {{ ref('dim_jma_station') }}
  where
    station_type = 'staffed'
    and area_key is not null
    and elevation_m <= 1000
  ),

  -- Nearest station per mesh: min_by over the haversine great-circle distance
  -- (Earth radius 6,371.0088 km; only the ordering matters).
  nearest as (
  select
    m.census_year,
    m.mesh_code,
    m.population_total,
    min_by(
      s.station_id,
      2 * 6371.0088 * asin(sqrt(
        pow(sin((s.lat - m.lat) / 2), 2)
        + cos(m.lat) * cos(s.lat) * pow(sin((s.lon - m.lon) / 2), 2)
      ))
    ) as station_id
  from
    meshes m
    cross join stations s
  group by
    m.census_year,
    m.mesh_code,
    m.population_total
  ),

  per_station as (
  select
    n.census_year,
    n.station_id,
    s.area_key,
    cast(count(*) as int) as n_meshes,
    sum(n.population_total) as population_total
  from
    nearest n
    join stations s
      on s.station_id = n.station_id
  group by
    n.census_year,
    n.station_id,
    s.area_key
  ),

  final as (
  select
    census_year,
    station_id,
    area_key,
    n_meshes,
    population_total,
    sum(population_total) over (partition by census_year, area_key) as area_population_total,
    population_total
      / sum(population_total) over (partition by census_year, area_key) as area_population_weight
  from
    per_station
  )

select * from final
