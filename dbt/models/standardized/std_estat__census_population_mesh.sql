with
  staging as (
  select
    *
  from
    {{ ref('stg_estat__census_population_mesh') }}
  ),

  -- Nine-digit 4次メッシュ code AABB C D E F G (JIS X 0410): AA = 2/3-degree
  -- latitude band, BB = longitude band east of 100°E, C/D = second-level row /
  -- column (1/12° x 1/8°, 0-7), E/F = third-level row / column (1/120° x 1/80°,
  -- 0-9), G = 500 m quadrant of the third-level mesh (1 SW, 2 SE, 3 NW, 4 NE).
  -- Reference implementation: power_market_analytics.estat.decode_mesh_code.
  parsed as (
  select
    *,
    cast(substr(mesh_code, 1, 2) as double) as lat_band,
    cast(substr(mesh_code, 3, 2) as double) as lon_band,
    cast(substr(mesh_code, 5, 1) as double) as second_row,
    cast(substr(mesh_code, 6, 1) as double) as second_col,
    cast(substr(mesh_code, 7, 1) as double) as third_row,
    cast(substr(mesh_code, 8, 1) as double) as third_col,
    cast(substr(mesh_code, 9, 1) as int) as quadrant
  from
    staging
  ),

  -- Lower-left (south-west) corner of the 500 m mesh in decimal degrees.
  corners as (
  select
    *,
    lat_band * 2 / 3 + second_row / 12 + third_row / 120
      + case when quadrant in (3, 4) then cast(1 as double) / 240 else cast(0 as double) end
      as south_latitude,
    100 + lon_band + second_col / 8 + third_col / 80
      + case when quadrant in (2, 4) then cast(1 as double) / 160 else cast(0 as double) end
      as west_longitude
  from
    parsed
  ),

  final as (
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
    source_file,
    south_latitude,
    south_latitude + cast(1 as double) / 240 as north_latitude,
    west_longitude,
    west_longitude + cast(1 as double) / 160 as east_longitude,
    south_latitude + cast(1 as double) / 480 as centroid_latitude,
    west_longitude + cast(1 as double) / 320 as centroid_longitude
  from
    corners
  )

select * from final
