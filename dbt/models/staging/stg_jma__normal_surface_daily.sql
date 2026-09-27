with
  source as (
  select
    normal_kind,
    station_number,
    element_code,
    n_years,
    statistic_start_year,
    statistic_end_year,
    month,
    {% for d in range(1, 32) -%}
    value_d{{ '%02d' % d }},
    flag_d{{ '%02d' % d }},
    {% endfor -%}
    normals_period_start_year,
    normals_period_end_year,
    normals_version,
    in_use_since,
    source_file
  from
    {{ source('jma', 'jma_normal_surface_daily') }}
  )

select * from source
