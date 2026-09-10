# Feature marts — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Six dbt feature marts under `models/features/` that hold every feature today's strategies use, except similar day, as tagged columns with a propagated `available_at`, each proven equal to the Python builder it replaces.

**Architecture:** One model per source family at its natural grain: day (area × trade_date), hour (area × trade_date × hour_ending), period (area × trade_date × time_code). Each mart reads the curated facts, computes its features in SQL, and carries `available_at` through the `available_at()` macro. A singular test guards that every model under `models/features/` declares the column.

**Tech Stack:** dbt 1.11 on dbt-spark (thrift), Spark SQL, `dbt_utils` 1.3.3, dbt unit tests where the adapter runs them.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §4, §11 PR 2.

## Global Constraints

- Every model keeps an enforced contract with a `data_type` for every column and a uniqueness test on its grain.
- Column metadata goes under `config: meta:` (dbt 1.10+): `feature: true`, `categorical: true|false`. Keys and `available_at` carry no tag.
- Schema `pma_features`; model names `ftr_<grain>_<family>`; the area key is `area_code`, the `dim_area` code the entity frame uses; the `system` area is not a bidding zone and never appears.
- Feature values must equal today's Python builders for Tokyo over 2025-01-01 to 2025-12-31: integers exactly, doubles within 1e-9.
- Warehouse timestamps are naive JST.
- Branch `feature/feature-marts` off `main`; commit type `feat(dbt)`; PR labels `enhancement` + `documentation`.

## The marts

| Model | Grain | Feature columns | `available_at` |
|---|---|---|---|
| `ftr_day_calendar` | area_code × trade_date | `month`, `day_of_week` (Monday = 0, pandas), `day_type` (0 Weekday, 1 Weekend, 2 Holiday; holiday wins), `holiday_degree`, `half`, `quarter`, `day_of_month`, `day_of_quarter`, `day_of_year`, `is_business_day` (1/0), `fiscal_quarter`, `days_since_holiday`, `days_until_holiday` (null before the spine's first holiday / after its last) | `timestamp '1900-01-01'`: the calendar is static |
| `ftr_day_occto` | area_code × trade_date | `max_demand_hour_ending`, `max_demand_mw`, `max_supply_capacity_mw` | the fact's |
| `ftr_hour_jma_obs` | area_code × trade_date × hour_ending | `wavg_temperature_c`: the representative station's same-hour temperature over D−8..D−2, weight `0.5^(k−2)`, renormalised over the lags present, null when all are missing | D−2's hour end + 1 h, the newest observation the window can hold |
| `ftr_hour_msm` | area_code × trade_date × hour_ending × forecast_reference_at | `forecast_temperature_c` (representative station), `popw_forecast_temperature_c`, `popw_forecast_relative_humidity_pct`, `popw_forecast_precipitation_mm` (population-weighted over the area's stations, latest census, renormalised over stations with a value); `census_year` untagged | greatest of the representative and weighted rows' |
| `ftr_period_actuals` | area_code × trade_date × time_code | `lag_7d_demand_kwh` | the D−7 row's |
| `ftr_period_jepx` | area_code × trade_date × time_code | `lag_1d_price` | the D−1 row's |

---

### Task 1: Project config, macro, guard test

**Files:**
- Modify: `dbt/dbt_project.yml`
- Create: `dbt/macros/available_at.sql`
- Create: `dbt/dbt_tests/assert_feature_marts_declare_available_at.sql`

- [ ] **Step 1: Schema for the folder** — in `dbt_project.yml` under `models: pma:` add

```yaml
    features:
      +schema: features
```

- [ ] **Step 2: The macro**

```sql
{% macro available_at(columns) -%}
{#- The instant a feature row became public: the greatest of its inputs'
    available_at values. Spark's greatest() needs two arguments, so a single
    input passes through. -#}
{%- if columns | length == 1 -%}
{{ columns[0] }}
{%- else -%}
greatest({{ columns | join(', ') }})
{%- endif -%}
{%- endmacro %}
```

- [ ] **Step 3: The guard test** — one row per model under `models/features/` whose contract lacks `available_at`:

```sql
{% set missing = [] %}
{% for node in graph.nodes.values() %}
  {% if node.resource_type == 'model' and node.path.startswith('features/') and 'available_at' not in node.columns %}
    {% do missing.append(node.name) %}
  {% endif %}
{% endfor %}
{% if missing %}
select model from values {% for name in missing %}('{{ name }}'){% if not loop.last %}, {% endif %}{% endfor %} as t(model)
{% else %}
select cast(null as string) as model where false
{% endif %}
```

- [ ] **Step 4: Commit** — `feat(dbt): features folder, available_at macro and the contract guard`

### Task 2: `ftr_day_calendar`

**Files:** `dbt/models/features/ftr_day_calendar.sql`, `.yml`

- [ ] **Step 1: SQL**

```sql
with
  days as (
  select * from {{ ref('dim_date') }}
  ),

  -- Every bidding zone; 'system' is the nationwide reference price, not an area.
  areas as (
  select area_code from {{ ref('dim_area') }} where area_code != 'system'
  ),

  holidays as (
  select
    date_key,
    max(case when is_holiday then date_key end)
      over (order by date_key rows between unbounded preceding and current row) as last_holiday,
    min(case when is_holiday then date_key end)
      over (order by date_key rows between current row and unbounded following) as next_holiday
  from days
  ),

  final as (
  select
    areas.area_code,
    days.date_key as trade_date,
    days.month,
    -- pandas dayofweek: Monday = 0.
    days.day_of_week_iso - 1 as day_of_week,
    case when days.is_holiday then 2 when days.is_weekend then 1 else 0 end as day_type,
    days.holiday_degree,
    days.half,
    days.quarter,
    days.day_of_month,
    days.day_of_quarter,
    days.day_of_year,
    cast(days.is_business_day as int) as is_business_day,
    days.fiscal_quarter,
    datediff(days.date_key, holidays.last_holiday) as days_since_holiday,
    datediff(holidays.next_holiday, days.date_key) as days_until_holiday,
    -- The calendar is static: the holiday seed is published a year ahead.
    timestamp '1900-01-01 00:00:00' as available_at
  from
    days
    inner join holidays on holidays.date_key = days.date_key
    cross join areas
  )

select * from final
```

- [ ] **Step 2: YAML** — enforced contract; unique on (area_code, trade_date); not_null on the keys, `available_at` and every feature except the two holiday distances; tags: `day_type` categorical, the rest numeric. A unit test with `dim_date` fixture rows around one holiday and `dim_area` rows `tokyo` + `system` expecting the distances and the day types.
- [ ] **Step 3: Build** — `just dbt build --select ftr_day_calendar`; PASS.
- [ ] **Step 4: Commit** — `feat(dbt): ftr_day_calendar`

### Task 3: `ftr_day_occto`

- [ ] **Step 1: SQL**

```sql
with
  final as (
  select
    areas.area_code,
    forecasts.date_key as trade_date,
    forecasts.max_demand_hour_ending,
    forecasts.max_demand_mw,
    forecasts.max_supply_capacity_mw,
    {{ available_at(['forecasts.available_at']) }} as available_at
  from
    {{ ref('fct_occto_demand_supply_forecast_daily') }} as forecasts
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = forecasts.area_key
  )

select * from final
```

- [ ] **Step 2: YAML**, **Step 3: Build**, **Step 4: Commit** — `feat(dbt): ftr_day_occto`

### Task 4: `ftr_hour_jma_obs`

- [ ] **Step 1: SQL**

```sql
with
  areas as (
  select area_code, representative_jma_station_id as station_id
  from {{ ref('dim_area') }}
  where representative_jma_station_id is not null
  ),

  observations as (
  select
    areas.area_code,
    weather.date_key as obs_date,
    hour(weather.observed_hour_start_at) + 1 as hour_ending,
    weather.temperature_c
  from
    {{ ref('fct_jma_weather_hourly') }} as weather
    inner join areas on areas.station_id = weather.station_id
  ),

  -- D-2 .. D-8: the seven complete observation days before 09:30 on D-1.
  lags as (
  select explode(sequence(2, 8)) as lag_days
  ),

  contributions as (
  select
    observations.area_code,
    date_add(observations.obs_date, lags.lag_days) as trade_date,
    observations.hour_ending,
    pow(0.5, lags.lag_days - 2) as weight,
    observations.temperature_c
  from
    observations
    cross join lags
  ),

  final as (
  select
    area_code,
    trade_date,
    hour_ending,
    sum(weight * temperature_c)
      / sum(case when temperature_c is not null then weight end) as wavg_temperature_c,
    -- Public once the newest observation the window can hold, D-2's, is:
    -- its hour end + 1 h.
    timestampadd(hour, hour_ending + 1, cast(date_sub(trade_date, 2) as timestamp)) as available_at
  from
    contributions
  group by
    area_code, trade_date, hour_ending
  )

select * from final
```

- [ ] **Step 2: YAML** with a unit test: seven observations for hour 1 on D−8..D−2 with temperatures 8..2 (D−2 = 2) expecting the weighted mean, one missing lag expecting renormalisation.
- [ ] **Step 3: Build**, **Step 4: Commit** — `feat(dbt): ftr_hour_jma_obs`

### Task 5: `ftr_hour_msm`

- [ ] **Step 1: SQL**

```sql
with
  areas as (
  select area_key, area_code, representative_jma_station_id
  from {{ ref('dim_area') }}
  where representative_jma_station_id is not null
  ),

  -- The latest census vintage, as the Python loader picks it.
  weights as (
  select census_year, area_key, station_id, area_population_weight
  from {{ ref('fct_census_population_jma_station') }}
  where census_year = (select max(census_year) from {{ ref('fct_census_population_jma_station') }})
  ),

  forecasts as (
  select
    station_id,
    date_key as trade_date,
    hour(forecast_hour_start_at) + 1 as hour_ending,
    forecast_reference_at,
    temperature_c,
    relative_humidity_pct,
    precipitation_mm,
    available_at
  from {{ ref('fct_jma_msm_weather_forecast_hourly') }}
  ),

  representative as (
  select
    areas.area_code,
    forecasts.trade_date,
    forecasts.hour_ending,
    forecasts.forecast_reference_at,
    forecasts.temperature_c as forecast_temperature_c,
    forecasts.available_at
  from
    forecasts
    inner join areas on areas.representative_jma_station_id = forecasts.station_id
  ),

  weighted as (
  select
    areas.area_code,
    forecasts.trade_date,
    forecasts.hour_ending,
    forecasts.forecast_reference_at,
    weights.census_year,
    sum(weights.area_population_weight * forecasts.temperature_c)
      / sum(case when forecasts.temperature_c is not null then weights.area_population_weight end)
      as popw_forecast_temperature_c,
    sum(weights.area_population_weight * forecasts.relative_humidity_pct)
      / sum(case when forecasts.relative_humidity_pct is not null then weights.area_population_weight end)
      as popw_forecast_relative_humidity_pct,
    sum(weights.area_population_weight * forecasts.precipitation_mm)
      / sum(case when forecasts.precipitation_mm is not null then weights.area_population_weight end)
      as popw_forecast_precipitation_mm,
    max(forecasts.available_at) as available_at
  from
    forecasts
    inner join weights on weights.station_id = forecasts.station_id
    inner join areas on areas.area_key = weights.area_key
  group by
    areas.area_code, forecasts.trade_date, forecasts.hour_ending, forecasts.forecast_reference_at, weights.census_year
  ),

  final as (
  select
    coalesce(representative.area_code, weighted.area_code) as area_code,
    coalesce(representative.trade_date, weighted.trade_date) as trade_date,
    coalesce(representative.hour_ending, weighted.hour_ending) as hour_ending,
    coalesce(representative.forecast_reference_at, weighted.forecast_reference_at) as forecast_reference_at,
    weighted.census_year,
    representative.forecast_temperature_c,
    weighted.popw_forecast_temperature_c,
    weighted.popw_forecast_relative_humidity_pct,
    weighted.popw_forecast_precipitation_mm,
    {{ available_at(['representative.available_at', 'weighted.available_at']) }} as available_at
  from
    representative
    full outer join weighted
      on weighted.area_code = representative.area_code
      and weighted.trade_date = representative.trade_date
      and weighted.hour_ending = representative.hour_ending
      and weighted.forecast_reference_at = representative.forecast_reference_at
  )

select * from final
```

- [ ] **Step 2: YAML** (unique on area_code, trade_date, hour_ending, forecast_reference_at), **Step 3: Build**, **Step 4: Commit** — `feat(dbt): ftr_hour_msm`

### Task 6: `ftr_period_actuals`

- [ ] **Step 1: SQL**

```sql
with
  final as (
  select
    areas.area_code,
    date_add(actuals.date_key, 7) as trade_date,
    actuals.time_code,
    actuals.demand_kwh as lag_7d_demand_kwh,
    {{ available_at(['actuals.available_at']) }} as available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    -- A TSO hole has no lag value; the row is absent rather than null.
    actuals.demand_kwh is not null
  )

select * from final
```

- [ ] **Step 2: YAML**, **Step 3: Build**, **Step 4: Commit** — `feat(dbt): ftr_period_actuals`

### Task 7: `ftr_period_jepx`

- [ ] **Step 1: SQL**

```sql
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
```

- [ ] **Step 2: YAML**, **Step 3: Build**, **Step 4: Commit** — `feat(dbt): ftr_period_jepx`

### Task 8: Proof, docs, PR

- [ ] **Step 1: Equality check** — a throwaway script run with `just python` inside the devcontainer: build the Tokyo 2025 points grid (365 × 48), run today's builders (`recency_weighted_temperature`, `join_forecast_temperature` at the station and population-weighted, `join_day_type`, `join_day_calendar`, `join_lag` for the 7-day demand lag and the 1-day price lag, the OCCTO merge) on the frames the loaders return, read each mart for `tokyo`, join on the grain and report row counts and the largest absolute difference per column. Pass: every integer column equal, every double within 1e-9, and the null sets equal.
- [ ] **Step 2: Docs** — CLAUDE.md: a `features` layer line under *Architecture*; the spec §11 row for PR 2 marked done.
- [ ] **Step 3: PR** — title `feat(dbt): feature marts for today's features`, body Why / What / Proof with the equality table, labels, assignee; Codex loop.
