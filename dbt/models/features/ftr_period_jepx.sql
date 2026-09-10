with
  final as (
  select
    areas.area_code,
    date_add(prices.date_key, 1) as trade_date,
    prices.time_code,
    prices.area_price_jpy_kwh as lag_1d_price,
    {{ available_at(['prices.available_at']) }} as available_at
  from
    {{ ref('fct_jepx_spot_area_price') }} as prices
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = prices.area_key
  where
    -- Hokkaido's 2018 suspension has null prices; the lag row is absent, not null.
    prices.area_price_jpy_kwh is not null
  )

select * from final
