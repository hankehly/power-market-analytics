with
  spot as (
  select
    *
  from
    {{ ref('std_jepx__spot') }}
  ),

  final as (
  select
    trade_date as date_key,
    time_code,
    trade_datetime,
    sell_bid_volume_kwh,
    buy_bid_volume_kwh,
    contract_volume_kwh,
    sell_block_bid_volume_kwh,
    sell_block_contract_volume_kwh,
    buy_block_bid_volume_kwh,
    buy_block_contract_volume_kwh,
    system_price_jpy_kwh
  from
    spot
  )

select * from final
