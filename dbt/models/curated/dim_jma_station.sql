-- SCD type 1: rebuilt from the jma_stations seed snapshot, so attribute changes
-- (coordinates, elevation, kansoku) overwrite in place and no history is kept.
-- Observation-environment breaks are tracked in the hourly facts via homogeneity
-- numbers instead; era-level history exists in JMA's mdrr metadata files
-- (docs/JMA-Weather-Data-Retrieval.md §4.3) if ever needed.
with
  final as (
  select
    station_id,
    case when station_id like 's%' then 'staffed' else 'amedas' end as station_type,
    prefecture_code,
    station_name,
    station_kana,
    latitude,
    longitude,
    elevation_m,
    kansoku,
    obs_precipitation,
    obs_wind,
    obs_temperature,
    obs_sunshine,
    obs_snow,
    obs_other,
    observation_ended_on,
    observation_ended_on is null as is_active
  from
    {{ ref('jma_stations') }}
  )

select * from final
