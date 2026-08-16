with
  staging as (
  select
    *
  from
    {{ ref('stg_occto__demand_forecast_dad') }}
  ),

  final as (
  select
    target_date,
    formulated_date,
    datediff(target_date, formulated_date) as forecast_horizon_days,
    area_name_ja,
    -- Snake-case area codes matching dim_area; the two total rows get their
    -- own codes so they can be told apart from real supply areas.
    case area_name_ja
      when '北海道' then 'hokkaido'
      when '東北' then 'tohoku'
      when '東京' then 'tokyo'
      when '中部' then 'chubu'
      when '北陸' then 'hokuriku'
      when '関西' then 'kansai'
      when '中国' then 'chugoku'
      when '四国' then 'shikoku'
      when '九州' then 'kyushu'
      when '沖縄' then 'okinawa'
      when '9エリア計' then 'total_9_areas'
      when '10エリア計' then 'total_10_areas'
    end as area_code,
    area_name_ja like '%エリア計' as is_area_total,
    -- Hour-ending labels "01:00".."24:00" -> 1..24 (24 = the hour ending at midnight).
    cast(split(min_demand_time, ':')[0] as int) as min_demand_hour_ending,
    min_demand_mw,
    cast(split(max_demand_time, ':')[0] as int) as max_demand_hour_ending,
    max_demand_mw,
    max_supply_capacity_mw,
    -- Published as percentages (92.4); expose as fractions (0.924).
    usage_rate_pct / 100 as usage_rate,
    reserve_rate_pct / 100 as reserve_rate
  from
    staging
  )

select * from final
