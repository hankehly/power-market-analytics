with
  staging as (
  select
    *
  from
    {{ ref('stg_occto__area_reserve_rate_dad') }}
  ),

  typed as (
  select
    *,
    -- 時刻 is a period-END label "00:30".."24:00": HH*2 + MM/30 is the JEPX
    -- time code (1 = 00:00-00:30 ... 48 = 23:30-24:00). div returns bigint.
    cast(
      cast(split(period_end_time, ':')[0] as int) * 2
      + cast(split(period_end_time, ':')[1] as int) div 30
      as int
    ) as time_code
  from
    staging
  ),

  final as (
  select
    target_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(target_date as timestamp)) as delivery_datetime,
    area_name_ja,
    -- Snake-case area codes matching dim_area ('okinawa' has no dim_area row).
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
    end as area_code,
    block_no,
    block_demand_mw,
    block_supply_capacity_mw,
    block_reserve_mw,
    -- Published as percentages (12.7); expose as fractions (0.127).
    wide_area_reserve_rate_pct / 100 as wide_area_reserve_rate,
    wide_area_usage_rate_pct / 100 as wide_area_usage_rate,
    area_demand_mw,
    area_supply_capacity_mw,
    area_reserve_mw
    -- kubun is dropped: it is the placeholder "－" on every row of the series.
  from
    typed
  )

select * from final
