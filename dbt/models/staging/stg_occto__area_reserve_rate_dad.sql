with
  source as (
  select
    target_date,
    kubun,
    period_end_time,
    area_name_ja,
    wide_area_reserve_rate_pct,
    wide_area_usage_rate_pct,
    block_no,
    block_demand_mw,
    block_supply_capacity_mw,
    block_reserve_mw,
    area_demand_mw,
    area_supply_capacity_mw,
    area_reserve_mw
  from
    {{ source('occto', 'occto_area_reserve_rate_dad') }}
  )

select * from source
