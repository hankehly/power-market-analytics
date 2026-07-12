-- The unpivot must produce exactly 9 area rows per standardized period row.
-- Guards against the dim_area join silently dropping rows if an area_code
-- ever drifts between the stack() literals and the jepx_areas seed.
select
  actual.n as actual_rows,
  expected.n as expected_rows
from
  (select count(*) as n from {{ ref('fct_jepx_spot_area_price') }}) as actual
  cross join (select count(*) * 9 as n from {{ ref('std_jepx__spot') }}) as expected
where
  actual.n != expected.n
