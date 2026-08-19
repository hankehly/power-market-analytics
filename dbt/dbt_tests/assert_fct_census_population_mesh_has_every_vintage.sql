-- Both configured census vintages must be present: a refresh that loaded only
-- one year (e.g. a partial --years download) would otherwise pass every
-- column-level test while halving the fact.
with
  expected as (
  select explode(array(2015, 2020)) as census_year
  ),

  loaded as (
  select distinct census_year from {{ ref('fct_census_population_mesh') }}
  )

select
  expected.census_year as missing_census_year
from
  expected
  left join loaded
    on loaded.census_year = expected.census_year
where
  loaded.census_year is null
