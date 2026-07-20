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
