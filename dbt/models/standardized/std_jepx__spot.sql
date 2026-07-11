with
  staging as (
  select
    *
  from
    {{ ref('stg_jepx__spot') }}
  ),

  final as (
  select
    trade_date,
    time_code,
    timestampadd(minute, (time_code - 1) * 30, cast(trade_date as timestamp)) as trade_datetime,
    sell_bid_volume_kwh,
    buy_bid_volume_kwh,
    contract_volume_kwh,
    system_price_jpy_kwh,
    area_price_hokkaido_jpy_kwh,
    area_price_tohoku_jpy_kwh,
    area_price_tokyo_jpy_kwh,
    area_price_chubu_jpy_kwh,
    area_price_hokuriku_jpy_kwh,
    area_price_kansai_jpy_kwh,
    area_price_chugoku_jpy_kwh,
    area_price_shikoku_jpy_kwh,
    area_price_kyushu_jpy_kwh,
    spot_intraday_avg_price_jpy_kwh,
    alpha_upper_x_avg_price_jpy_kwh,
    alpha_lower_x_avg_price_jpy_kwh,
    alpha_preliminary_x_avg_price_jpy_kwh,
    alpha_final_x_avg_price_jpy_kwh,
    avoidable_cost_national_jpy_kwh,
    avoidable_cost_hokkaido_jpy_kwh,
    avoidable_cost_tohoku_jpy_kwh,
    avoidable_cost_tokyo_jpy_kwh,
    avoidable_cost_chubu_jpy_kwh,
    avoidable_cost_hokuriku_jpy_kwh,
    avoidable_cost_kansai_jpy_kwh,
    avoidable_cost_chugoku_jpy_kwh,
    avoidable_cost_shikoku_jpy_kwh,
    avoidable_cost_kyushu_jpy_kwh,
    sell_block_bid_volume_kwh,
    sell_block_contract_volume_kwh,
    buy_block_bid_volume_kwh,
    buy_block_contract_volume_kwh,
    fip_reference_price_national_jpy_kwh,
    fip_reference_price_hokkaido_jpy_kwh,
    fip_reference_price_tohoku_jpy_kwh,
    fip_reference_price_tokyo_jpy_kwh,
    fip_reference_price_chubu_jpy_kwh,
    fip_reference_price_hokuriku_jpy_kwh,
    fip_reference_price_kansai_jpy_kwh,
    fip_reference_price_chugoku_jpy_kwh,
    fip_reference_price_shikoku_jpy_kwh,
    fip_reference_price_kyushu_jpy_kwh
  from
    staging
  )

select * from final
