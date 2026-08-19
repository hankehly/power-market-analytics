-- One row per 500 m mesh that appears in any loaded census vintage. The
-- geography is a pure function of mesh_code (decoded in
-- std_estat__census_population_mesh), so the distinct over every attribute
-- yields exactly one row per mesh; if two vintages ever disagreed on an
-- attribute (e.g. a JGD2011 product), the mesh_code unique test would fail
-- rather than one vintage silently winning.
with
  meshes as (
  select distinct
    mesh_code,
    primary_mesh_code,
    geodetic_datum,
    south_latitude,
    north_latitude,
    west_longitude,
    east_longitude,
    centroid_latitude,
    centroid_longitude
  from
    {{ ref('std_estat__census_population_mesh') }}
  )

select * from meshes
