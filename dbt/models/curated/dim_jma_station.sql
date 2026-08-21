-- SCD type 1: rebuilt from the jma_stations seed snapshot, so attribute changes
-- (coordinates, elevation, kansoku) overwrite in place and no history is kept.
-- Observation-environment breaks are tracked in the hourly facts via homogeneity
-- numbers instead; era-level history exists in JMA's mdrr metadata files
-- (docs/JMA-Weather-Data-Retrieval.md §4.3) if ever needed.
--
-- area_key rolls each station up to the JEPX area whose TSO supply region
-- contains it (curated in the jma_station_areas seed). The left join keeps a
-- station missing from the mapping seed visible, so the not_null test on
-- area_key fails loudly instead of the station silently disappearing.
with
  stations as (
  select * from {{ ref('jma_stations') }}
  ),

  station_areas as (
  select * from {{ ref('jma_station_areas') }}
  ),

  areas as (
  select * from {{ ref('jepx_areas') }}
  ),

  final as (
  select
    stations.station_id,
    case when stations.station_id like 's%' then 'staffed' else 'amedas' end as station_type,
    stations.prefecture_code,
    stations.station_name,
    stations.station_kana,
    areas.area_key,
    station_areas.area_code,
    stations.latitude,
    stations.longitude,
    stations.elevation_m,
    stations.kansoku,
    stations.obs_precipitation,
    stations.obs_wind,
    stations.obs_temperature,
    stations.obs_sunshine,
    stations.obs_snow,
    stations.obs_other,
    stations.observation_ended_on,
    stations.observation_ended_on is null as is_active
  from
    stations
    left join station_areas
      on station_areas.station_id = stations.station_id
    left join areas
      on areas.area_code = station_areas.area_code
  )

select * from final
