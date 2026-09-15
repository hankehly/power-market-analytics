with
  periods as (
  select
    explode(sequence(1, 48)) as time_code
  ),

  final as (
  select
    time_code,
    (time_code - 1) * 30 as start_minute_of_day,
    cast((time_code - 1) div 2 as int) as hour_of_day,
    lpad(cast((time_code - 1) * 30 div 60 as string), 2, '0') || ':' || lpad(cast((time_code - 1) * 30 % 60 as string), 2, '0') as period_start_time,
    lpad(cast(time_code * 30 div 60 as string), 2, '0') || ':' || lpad(cast(time_code * 30 % 60 as string), 2, '0') as period_end_time,
    -- JEPX daytime trading window (8:00-18:00)
    time_code between 17 and 36 as is_daytime,
    case
      when time_code <= 12 then 'Overnight'   -- 00:00-06:00
      when time_code <= 16 then 'Morning'     -- 06:00-08:00
      when time_code <= 36 then 'Daytime'     -- 08:00-18:00
      else 'Evening'                          -- 18:00-24:00
    end as day_part
  from
    periods
  )

select * from final
