-- The station weights must partition each area's population: per census vintage
-- and JEPX area they sum to 1 (within floating-point tolerance), and every area
-- with a weighted station must also appear in dim_area. A mesh assigned to a
-- station whose area differs from the rest of the area's stations, or a
-- denominator computed over the wrong partition, would break this without
-- failing any column-level test.
with
  per_area as (
  select
    census_year,
    area_key,
    sum(area_population_weight) as weight_sum,
    sum(population_total) as population_sum,
    min(area_population_total) as area_population_total_min,
    max(area_population_total) as area_population_total_max
  from
    {{ ref('fct_census_population_jma_station') }}
  group by
    census_year,
    area_key
  )

select
  *
from
  per_area
where
  abs(weight_sum - 1) > cast(1 as double) / 1000000
  or population_sum <> area_population_total_min
  or area_population_total_min <> area_population_total_max
