-- The area's own recent daily demand for each delivery day, from complete days
-- only (all 48 periods non-null). D-2, the newest complete day: its mean,
-- maximum, minimum and range, its load factor (mean / maximum), its mean over
-- the morning, afternoon and evening windows, the period of its peak and its
-- morning and evening ramps. Then the exponentially weighted means of the
-- daily maximum, mean and minimum over D-2 to D-6 and over D-7, D-14, D-21
-- and D-28, and the difference of each pair. A row exists wherever any of
-- those days is complete and a column is null where its days are not.
-- available_at is the greatest over the days the row used. Every sum is of
-- integers, so a value is the same on every build whatever order Spark reads
-- the rows in.
{#- Time-of-day windows, in time codes: 06:00-10:00, 13:00-17:00, 18:00-22:00. #}
{%- set windows = [('morning', 13, 20), ('afternoon', 27, 34), ('evening', 37, 44)] %}
{%- set ramp_windows = ['morning', 'evening'] %}
with
  actuals as (
  select
    areas.area_code,
    actuals.date_key,
    actuals.time_code,
    actuals.demand_kwh,
    actuals.available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    actuals.demand_kwh is not null
  ),

  complete_days as (
  select
    area_code,
    date_key,
    sum(demand_kwh) as sum_demand_kwh,
    avg(demand_kwh) as mean_demand_kwh,
    max(demand_kwh) as max_demand_kwh,
    min(demand_kwh) as min_demand_kwh,
    -- The period of the maximum; the earliest one on a tie.
    max_by(time_code, named_struct('demand_kwh', demand_kwh, 'earliest', -time_code)) as peak_time_code,
    {%- for name, first, last in windows %}
    avg(case when time_code between {{ first }} and {{ last }} then demand_kwh end) as {{ name }}_mean_demand_kwh,
    {%- endfor %}
    {%- for name, first, last in windows if name in ramp_windows %}
    -- The least-squares slope of the {{ name }} window against the time code,
    -- (n Sxy - Sx Sy) / (n Sxx - Sx^2), doubled to kWh per hour.
    2 * (
      count(case when time_code between {{ first }} and {{ last }} then 1 end)
        * sum(case when time_code between {{ first }} and {{ last }} then time_code * demand_kwh end)
      - sum(case when time_code between {{ first }} and {{ last }} then time_code end)
        * sum(case when time_code between {{ first }} and {{ last }} then demand_kwh end))
    / (
      count(case when time_code between {{ first }} and {{ last }} then 1 end)
        * sum(case when time_code between {{ first }} and {{ last }} then time_code * time_code end)
      - sum(case when time_code between {{ first }} and {{ last }} then time_code end)
        * sum(case when time_code between {{ first }} and {{ last }} then time_code end)) as {{ name }}_ramp_demand_kwh,
    {%- endfor %}
    max(available_at) as available_at
  from
    actuals
  group by
    area_code, date_key
  having
    count(*) = 48
  ),

  -- The days each delivery day reads, with the weights of the two
  -- exponentially weighted means: 16, 8, 4, 2, 1 for D-2 to D-6 and 8, 4, 2,
  -- 1 for D-7, D-14, D-21 and D-28.
  lags as (
  select * from values
    (2, 16, 0), (3, 8, 0), (4, 4, 0), (5, 2, 0), (6, 1, 0),
    (7, 0, 8), (14, 0, 4), (21, 0, 2), (28, 0, 1)
    as lags(lag_days, weight_5d, weight_weekly)
  ),

  shifted as (
  select
    complete_days.*,
    date_add(complete_days.date_key, lags.lag_days) as trade_date,
    lags.lag_days,
    lags.weight_5d,
    lags.weight_weekly
  from
    complete_days
    cross join lags
  ),

  by_day as (
  select
    area_code,
    trade_date,
    max(case when lag_days = 2 then mean_demand_kwh end) as lag_2d_mean_demand_kwh,
    max(case when lag_days = 2 then max_demand_kwh end) as lag_2d_max_demand_kwh,
    max(case when lag_days = 2 then min_demand_kwh end) as lag_2d_min_demand_kwh,
    max(case when lag_days = 2 then max_demand_kwh - min_demand_kwh end) as lag_2d_range_demand_kwh,
    max(case when lag_days = 2 then mean_demand_kwh / max_demand_kwh end) as lag_2d_load_factor_demand,
    {%- for name, first, last in windows %}
    max(case when lag_days = 2 then {{ name }}_mean_demand_kwh end) as lag_2d_{{ name }}_mean_demand_kwh,
    {%- endfor %}
    max(case when lag_days = 2 then peak_time_code end) as lag_2d_peak_time_code,
    {%- for name in ramp_windows %}
    max(case when lag_days = 2 then {{ name }}_ramp_demand_kwh end) as lag_2d_{{ name }}_ramp_demand_kwh,
    {%- endfor %}
    {%- for window in ['5d', 'weekly'] %}
    -- Over the complete days present; null when none is. The daily mean is
    -- the day's sum over 48, so its weighted sum stays an integer.
    sum(weight_{{ window }} * max_demand_kwh) / nullif(sum(weight_{{ window }}), 0) as ewm_{{ window }}{{ '_lags' if window == 'weekly' }}_daily_max_demand_kwh,
    sum(weight_{{ window }} * sum_demand_kwh) / nullif(48 * sum(weight_{{ window }}), 0) as ewm_{{ window }}{{ '_lags' if window == 'weekly' }}_daily_mean_demand_kwh,
    sum(weight_{{ window }} * min_demand_kwh) / nullif(sum(weight_{{ window }}), 0) as ewm_{{ window }}{{ '_lags' if window == 'weekly' }}_daily_min_demand_kwh,
    {%- endfor %}
    max(available_at) as available_at
  from
    shifted
  group by
    area_code, trade_date
  ),

  final as (
  select
    area_code,
    trade_date,
    lag_2d_mean_demand_kwh,
    lag_2d_max_demand_kwh,
    lag_2d_min_demand_kwh,
    lag_2d_range_demand_kwh,
    lag_2d_load_factor_demand,
    {%- for name, first, last in windows %}
    lag_2d_{{ name }}_mean_demand_kwh,
    {%- endfor %}
    lag_2d_peak_time_code,
    {%- for name in ramp_windows %}
    lag_2d_{{ name }}_ramp_demand_kwh,
    {%- endfor %}
    {%- for stat in ['max', 'mean', 'min'] %}
    ewm_5d_daily_{{ stat }}_demand_kwh,
    ewm_weekly_lags_daily_{{ stat }}_demand_kwh,
    -- The recent weighted level minus the same weekday's; null when either is.
    ewm_5d_daily_{{ stat }}_demand_kwh - ewm_weekly_lags_daily_{{ stat }}_demand_kwh
      as ewm_5d_minus_ewm_weekly_lags_daily_{{ stat }}_demand_kwh,
    {%- endfor %}
    {{ available_at(['available_at']) }} as available_at
  from
    by_day
  )

select * from final
