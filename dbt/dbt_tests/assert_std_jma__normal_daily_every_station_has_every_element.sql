-- JMA ships every element for every station (81 today). A station missing
-- one would leave a fact column null with nothing saying so; a code the seed
-- lacks fails the relationships test instead.
with
  expected as (
  select count(*) as n_elements from {{ ref('jma_normal_elements') }}
  ),

  per_station as (
  select
    normals_period_end_year,
    station_id,
    count(distinct element_code) as n_elements
  from
    {{ ref('std_jma__normal_daily') }}
  group by
    normals_period_end_year,
    station_id
  )

select
  per_station.normals_period_end_year,
  per_station.station_id,
  per_station.n_elements,
  expected.n_elements as expected_n_elements
from
  per_station
  cross join expected
where
  per_station.n_elements <> expected.n_elements
