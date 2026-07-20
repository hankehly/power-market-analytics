with
  staffed as (
  select
    station_id,
    observed_at,
    precipitation_mm,
    precipitation_phenomenon_absent,
    precipitation_quality_flag,
    precipitation_homogeneity_no,
    temperature_c,
    temperature_quality_flag,
    temperature_homogeneity_no,
    wind_speed_ms,
    wind_speed_quality_flag,
    wind_direction,
    wind_direction_quality_flag,
    wind_homogeneity_no,
    sunshine_duration_h,
    sunshine_phenomenon_absent,
    sunshine_quality_flag,
    sunshine_homogeneity_no
  from
    {{ ref('stg_jma__hourly_staffed') }}
  ),

  amedas as (
  select
    station_id,
    observed_at,
    precipitation_mm,
    -- AMeDAS files carry no 現象なし情報 columns, so the trace-vs-none
    -- distinction (value 0 with phenomenon observed) is unknowable there.
    cast(null as int) as precipitation_phenomenon_absent,
    precipitation_quality_flag,
    precipitation_homogeneity_no,
    temperature_c,
    temperature_quality_flag,
    temperature_homogeneity_no,
    wind_speed_ms,
    wind_speed_quality_flag,
    wind_direction,
    wind_direction_quality_flag,
    wind_homogeneity_no,
    sunshine_duration_h,
    cast(null as int) as sunshine_phenomenon_absent,
    sunshine_quality_flag,
    sunshine_homogeneity_no
  from
    {{ ref('stg_jma__hourly_amedas') }}
  ),

  unioned as (
  select * from staffed
  union all
  select * from amedas
  ),

  final as (
  select
    station_id,
    observed_at,
    timestampadd(hour, -1, observed_at) as observed_hour_start_at,
    cast(timestampadd(hour, -1, observed_at) as date) as observed_date,
    case
      when month(timestampadd(hour, -1, observed_at)) >= 4
      then year(timestampadd(hour, -1, observed_at))
      else year(timestampadd(hour, -1, observed_at)) - 1
    end as fiscal_year,
    precipitation_mm,
    precipitation_phenomenon_absent,
    precipitation_quality_flag,
    precipitation_homogeneity_no,
    temperature_c,
    temperature_quality_flag,
    temperature_homogeneity_no,
    wind_speed_ms,
    wind_speed_quality_flag,
    wind_direction,
    wind_direction_quality_flag,
    wind_homogeneity_no,
    sunshine_duration_h,
    sunshine_phenomenon_absent,
    sunshine_quality_flag,
    sunshine_homogeneity_no
  from
    unioned
  )

select * from final
