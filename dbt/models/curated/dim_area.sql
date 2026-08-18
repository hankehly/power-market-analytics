with
  final as (
  select
    area_key,
    area_code,
    area_name_en,
    area_name_ja,
    tso_name_en,
    grid_frequency,
    grid_region,
    representative_jma_station_id
  from
    {{ ref('jepx_areas') }}
  )

select * from final
