with
  spot as (
  select
    *
  from
    {{ ref('std_jepx__spot') }}
  ),

  unpivoted as (
  select
    trade_date,
    time_code,
    trade_datetime,
    stack(
      9,
      'hokkaido', area_price_hokkaido_jpy_kwh,
      'tohoku', area_price_tohoku_jpy_kwh,
      'tokyo', area_price_tokyo_jpy_kwh,
      'chubu', area_price_chubu_jpy_kwh,
      'hokuriku', area_price_hokuriku_jpy_kwh,
      'kansai', area_price_kansai_jpy_kwh,
      'chugoku', area_price_chugoku_jpy_kwh,
      'shikoku', area_price_shikoku_jpy_kwh,
      'kyushu', area_price_kyushu_jpy_kwh
    ) as (area_code, area_price_jpy_kwh)
  from
    spot
  ),

  final as (
  select
    unpivoted.trade_date as date_key,
    unpivoted.time_code,
    dim_area.area_key,
    unpivoted.trade_datetime,
    unpivoted.area_price_jpy_kwh
  from
    unpivoted
    left join {{ ref('dim_area') }} as dim_area
      on unpivoted.area_code = dim_area.area_code
  )

select * from final
