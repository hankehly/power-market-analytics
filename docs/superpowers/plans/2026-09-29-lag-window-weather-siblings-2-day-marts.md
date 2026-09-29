# Weather siblings of the load lag windows, part 2 of 4: the day marts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ftr_day_jma_obs`, the tenth feature mart, with the D-2 daily and evening means of the population-weighted observed temperature and the D-2 daily mean of the observed radiation, and give `ftr_day_msm` the two forecast means they pair with and the three deltas: PR 2 of the spec's four (section 5.3 and 5.4).

**Architecture:** The new day mart reads `fct_area_weather_hourly` (PR 1) day by day: complete days only for the daily means, the four evening hours only for the evening mean, every sum in hour order through `ordered_weighted_mean` with weight 1. `ftr_day_msm` gets the same two aggregates over `ftr_hour_msm`'s forecast columns and left-joins the new mart on area and day for the deltas, so the deltas sit with the vintage and no `available_at` moves. A new mart touches the five hand-kept places the generator does not reach.

**Tech Stack:** dbt (Spark SQL, unit tests, contracts), the `ordered_weighted_mean` and `available_at` macros, `scripts/generate_feature_views.py`, pytest with the local Spark fixture, the devcontainer's Spark session for the proofs, `gh`.

**Spec:** `docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md` (sections 5.3, 5.4, 6, 8, 9). Part 1: `docs/superpowers/plans/2026-09-29-lag-window-weather-siblings-1-hour-marts.md` (merged as PR #239).

## Global Constraints

- Work only in the worktree `.claude/worktrees/weather-siblings-day-marts`, branch `feature/issue-237-weather-siblings-day-marts`; compose commands use `docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics …`; the devcontainer sees the worktree at `/workspace/.claude/worktrees/weather-siblings-day-marts`.
- Host-side dbt: `DBT_THRIFT_HOST=localhost uv run dbt <cmd> --project-dir dbt --profiles-dir dbt`, one dbt process at a time.
- A unit-test input for a relation that is not built yet, or built without the columns the model reads, is `format: sql` with typed columns (part 1's ruling).
- Means of doubles go through `ordered_weighted_mean` over an `array_sort`ed `collect_list`, weight 1, never `avg()`.
- Physical names and expressions are the spec's: `lag_2d_mean_popw_temperature_c`, `lag_2d_evening_mean_popw_temperature_c`, `lag_2d_mean_popw_solar_radiation_mjm2` in `ftr_day_jma_obs`; `evening_mean_popw_forecast_temperature_c`, `mean_popw_forecast_solar_radiation_mjm2` and `delta_` + each sibling name in `ftr_day_msm`.
- The evening window is the hours ending 19:00 to 22:00, the load's `time=18:00-22:00`, all four required; the daily means need all 24 hours.
- Every model: enforced contract, a `data_type` per column, a uniqueness test on its key; every new input feeds `available_at` through `greatest()`.
- Commits: Conventional Commits with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; never the Codex mention in anything that shows in a diff.

## Review Focus

1. A day with 23 observed hours: both daily means null, the evening mean present when its four hours are (Task 1's unit test, day 2).
2. A day missing an evening hour: the evening mean null while the daily means stand (Task 1's unit test, day 3).
3. A day whose radiation is null at one hour while every temperature is present: the radiation mean null, the temperature means present (Task 1's unit test, day 4).
4. A vintage with no sibling row: null deltas, the forecast row kept, `available_at` the vintage's (Task 2's unit test, day 2); the reverse, a sibling with no vintage, adds no row (Task 2's unit test, the extra sibling day).
5. The new mart's `available_at` is 01:00 on D-1 for every row, never later than the vintage's, so `ftr_day_msm`'s does not move (Task 4's diff).

---

### Task 1: `ftr_day_jma_obs`

**Files:**
- Create: `dbt/models/features/ftr_day_jma_obs.sql`
- Create: `dbt/models/features/ftr_day_jma_obs.yml`

**Interfaces:**
- Consumes: `fct_area_weather_hourly` (`area_key`, `date_key`, `hour_ending`, `popw_temperature_c`, `popw_solar_radiation_mjm2`, `available_at`), `dim_area` (`area_key`, `area_code`).
- Produces: grain `area_code × trade_date`; `lag_2d_mean_popw_temperature_c`, `lag_2d_evening_mean_popw_temperature_c`, `lag_2d_mean_popw_solar_radiation_mjm2` (doubles, tagged), `available_at`. Task 2 joins on `(area_code, trade_date)`.

- [ ] **Step 1: Write the failing unit test**

Create `dbt/models/features/ftr_day_jma_obs.yml`:

```yaml
models:
  - name: ftr_day_jma_obs
    config:
      contract:
        enforced: true
    description: >
      Day-level summaries of the area's population-weighted observed weather on D-2, the
      newest complete observation day before the 09:30 D-1 issue time, from
      fct_area_weather_hourly: the daily mean of the temperature and of the solar
      radiation, and the evening mean of the temperature over the four hours ending 19:00
      to 22:00, the load's 18:00 to 22:00 (the researcher's lag-window weather idea of
      2026-09-28, feature candidates #237 and #238; spec
      docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md). Complete
      days only for the daily means: null unless all 24 hours have a value; the evening
      mean needs its four hours only. Every mean is added in hour order through the
      ordered_weighted_mean macro with weight 1, so the value is the same on every build.
      Their differences from D's forecast are in ftr_day_msm. Grain: area_code x
      trade_date; a row exists for every day D whose D-2 has at least one hour in the fact.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - area_code
              - trade_date
    columns:
      - name: area_code
        data_type: string
        description: >
          dim_area.area_code of the bidding zone.
        data_tests:
          - not_null
      - name: trade_date
        data_type: date
        description: >
          The delivery day D; the row reads D-2.
        data_tests:
          - not_null
      - name: lag_2d_mean_popw_temperature_c
        data_type: double
        description: >
          The mean over the 24 hours of D-2 of the area's population-weighted observed
          temperature, C: the temperature the D-2 daily load features
          (ftr_day_actuals' LAG(DAILY_MEAN(demand_kwh), 2d) and its neighbours) were
          recorded under (feature candidate #237). Null unless all 24 hours have a value.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)"
      - name: lag_2d_evening_mean_popw_temperature_c
        data_type: double
        description: >
          The same over the four hours ending 19:00 to 22:00 of D-2, the load's evening
          window 18:00 to 22:00 (feature candidate #237): the temperature under
          ftr_day_actuals' LAG(DAILY_MEAN(demand_kwh, time=18:00-22:00), 2d). Null unless
          all four hours have a value; the other twenty are not needed.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(temperature_c, weight=population), time=18:00-22:00), 2d)"
      - name: lag_2d_mean_popw_solar_radiation_mjm2
        data_type: double
        description: >
          The mean over the 24 hours of D-2 of the area's population-weighted observed
          solar radiation, MJ/m2 per hour (feature candidate #238), weighted over the
          stations that record it: 7 of Tokyo's 21 weighted stations and 3 of Kansai's
          11, 55.4 % of each area's weight, the representative station three quarters or
          more of that share (fct_area_weather_hourly's description); a more
          representative radiation source is future work. Null unless all 24 hours have a
          value.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(solar_radiation_mjm2, weight=population)), 2d)"
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST: the latest available_at of D-2's hours
          in fct_area_weather_hourly, hour 24's observed end + 1 h, 01:00 on D-1.
        data_tests:
          - not_null

unit_tests:
  - name: ftr_day_jma_obs_complete_days_evening_hours_and_a_null_element
    description: >
      Four Tokyo observation days, read two days later. 2025-03-01 is complete: the
      temperature 10 + 0.5 x the hour ending gives a daily mean of 16.25 and an evening
      mean (hours 19 to 22: 19.5, 20, 20.5, 21) of 20.25, the radiation 0.25 every hour a
      mean of 0.25, every sum exact in doubles. 2025-03-02 has no hour 5, so both daily
      means are null while the evening mean stands. 2025-03-03 has no hour 20: the evening
      mean is null and the daily means are null too, the day being incomplete.
      2025-03-04 has every temperature but a null radiation at hour 12: the temperature
      means stand, the radiation mean is null. available_at is each day's hour-24 end
      + 1 h, 01:00 on the next day, whatever hours are missing.
    model: ftr_day_jma_obs
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo}
      - input: ref('fct_area_weather_hourly')
        format: sql
        rows: |
          with hours as (
            select explode(sequence(1, 24)) as h
          )
          select 1 as area_key, date '2025-03-01' as date_key, h as hour_ending,
            cast(10 + 0.5 * h as double) as popw_temperature_c, cast(0.25 as double) as popw_solar_radiation_mjm2,
            timestamp '2025-03-01 00:00:00' + make_interval(0, 0, 0, 0, h + 1) as available_at
          from hours
          union all
          select 1, date '2025-03-02', h, cast(10 + 0.5 * h as double), cast(0.25 as double),
            timestamp '2025-03-02 00:00:00' + make_interval(0, 0, 0, 0, h + 1)
          from hours where h <> 5
          union all
          select 1, date '2025-03-03', h, cast(10 + 0.5 * h as double), cast(0.25 as double),
            timestamp '2025-03-03 00:00:00' + make_interval(0, 0, 0, 0, h + 1)
          from hours where h <> 20
          union all
          select 1, date '2025-03-04', h, cast(10 + 0.5 * h as double),
            case when h = 12 then cast(null as double) else cast(0.25 as double) end,
            timestamp '2025-03-04 00:00:00' + make_interval(0, 0, 0, 0, h + 1)
          from hours
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-03, lag_2d_mean_popw_temperature_c: 16.25, lag_2d_evening_mean_popw_temperature_c: 20.25, lag_2d_mean_popw_solar_radiation_mjm2: 0.25, available_at: "2025-03-02 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-04, lag_2d_mean_popw_temperature_c: null, lag_2d_evening_mean_popw_temperature_c: 20.25, lag_2d_mean_popw_solar_radiation_mjm2: null, available_at: "2025-03-03 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-05, lag_2d_mean_popw_temperature_c: null, lag_2d_evening_mean_popw_temperature_c: null, lag_2d_mean_popw_solar_radiation_mjm2: null, available_at: "2025-03-04 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-06, lag_2d_mean_popw_temperature_c: 16.25, lag_2d_evening_mean_popw_temperature_c: 20.25, lag_2d_mean_popw_solar_radiation_mjm2: null, available_at: "2025-03-05 01:00:00"}
```

- [ ] **Step 2: Run it to see it fail**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_day_jma_obs,test_type:unit"`
Expected: a parsing error, no model file for `ftr_day_jma_obs`.

- [ ] **Step 3: Write the model**

Create `dbt/models/features/ftr_day_jma_obs.sql`:

```sql
with
  -- Each observation day's hours, from the curated fact, with the bidding zone's code.
  hours as (
  select
    areas.area_code,
    fact.date_key as obs_date,
    fact.hour_ending,
    fact.popw_temperature_c,
    fact.popw_solar_radiation_mjm2,
    fact.available_at
  from
    {{ ref('fct_area_weather_hourly') }} as fact
    inner join {{ ref('dim_area') }} as areas on areas.area_key = fact.area_key
  ),

  -- One row per observation day. Every hour that has a value goes into one array
  -- in hour order: a fixed order whatever order Spark reads the rows in, so the
  -- means below are the same on every build.
  days as (
  select
    area_code,
    obs_date,
    array_sort(collect_list(
      case when popw_temperature_c is not null
        then named_struct('hour_ending', hour_ending, 'weight', cast(1 as double), 'value', popw_temperature_c)
      end
    )) as temperature_terms,
    array_sort(collect_list(
      case when hour_ending between 19 and 22 and popw_temperature_c is not null
        then named_struct('hour_ending', hour_ending, 'weight', cast(1 as double), 'value', popw_temperature_c)
      end
    )) as evening_temperature_terms,
    array_sort(collect_list(
      case when popw_solar_radiation_mjm2 is not null
        then named_struct('hour_ending', hour_ending, 'weight', cast(1 as double), 'value', popw_solar_radiation_mjm2)
      end
    )) as radiation_terms,
    max(available_at) as available_at
  from
    hours
  group by
    area_code, obs_date
  ),

  -- Read two days later, the newest complete observation day before the issue time.
  -- Complete days only for the daily means; the evening mean needs its four hours.
  final as (
  select
    area_code,
    date_add(obs_date, 2) as trade_date,
    case when size(temperature_terms) = 24 then {{ ordered_weighted_mean('temperature_terms') }} end
      as lag_2d_mean_popw_temperature_c,
    case when size(evening_temperature_terms) = 4 then {{ ordered_weighted_mean('evening_temperature_terms') }} end
      as lag_2d_evening_mean_popw_temperature_c,
    case when size(radiation_terms) = 24 then {{ ordered_weighted_mean('radiation_terms') }} end
      as lag_2d_mean_popw_solar_radiation_mjm2,
    -- The day's latest hour, hour 24's end + 1 h: 01:00 on D-1.
    available_at
  from
    days
  )

select * from final
```

- [ ] **Step 4: Run the unit test to see it pass**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_day_jma_obs,test_type:unit"`
Expected: `PASS 1`. If Spark refuses `make_interval` with five arguments, write the availability as `timestampadd(hour, h + 1, timestamp '2025-03-01 00:00:00')` in the input instead.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_day_jma_obs.sql dbt/models/features/ftr_day_jma_obs.yml
git commit -m "feat(dbt): ftr_day_jma_obs, the D-2 daily and evening means of the observed area weather" -m "Refs #237, #238" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `ftr_day_msm`'s two forecast means and three deltas

**Files:**
- Modify: `dbt/models/features/ftr_day_msm.sql`
- Modify: `dbt/models/features/ftr_day_msm.yml`

**Interfaces:**
- Consumes: `ftr_hour_msm` (`popw_forecast_temperature_c`, `popw_forecast_solar_radiation_mjm2`, per vintage), Task 1's mart keyed `(area_code, trade_date)`.
- Produces: `evening_mean_popw_forecast_temperature_c`, `mean_popw_forecast_solar_radiation_mjm2`, `delta_lag_2d_mean_popw_temperature_c`, `delta_lag_2d_evening_mean_popw_temperature_c`, `delta_lag_2d_mean_popw_solar_radiation_mjm2` (doubles, tagged).

- [ ] **Step 1: Give the two existing unit tests the new input and write the failing test**

In `dbt/models/features/ftr_day_msm.yml`, add to the `given:` of both existing unit tests, after the `ftr_hour_msm` input:

```yaml
      - input: ref('ftr_day_jma_obs')
        format: sql
        rows: |
          select cast(null as string) as area_code, cast(null as date) as trade_date,
            cast(null as double) as lag_2d_mean_popw_temperature_c,
            cast(null as double) as lag_2d_evening_mean_popw_temperature_c,
            cast(null as double) as lag_2d_mean_popw_solar_radiation_mjm2,
            cast(null as timestamp) as available_at
          where false
```

Append a third unit test:

```yaml
  - name: ftr_day_msm_evening_and_radiation_means_and_the_deltas_to_d_2
    description: >
      One vintage per day, 24 hours each, the forecast temperature 20 + 0.5 x the hour
      (daily mean 26.25, evening hours 19 to 22 mean 30.25) and the forecast radiation
      0.5 every hour (mean 0.5), every sum exact. 2025-03-03 has a D-2 sibling row
      (16.25, 20.25, 0.25): the deltas are 10.0, 10.0 and 0.25. 2025-03-04 has no
      sibling row: its two means stand and its deltas are null. 2025-03-05 has no hour 20:
      its evening mean and evening delta are null, its daily means and the other deltas
      stand. A sibling row for 2025-03-06, a day with no vintage, makes no row. available_at
      stays the vintage's on every row: the sibling's 01:00 on D-1 equals it.
    model: ftr_day_msm
    given:
      - input: ref('ftr_hour_msm')
        format: sql
        rows: |
          with hours as (select explode(sequence(1, 24)) as h)
          select 'tokyo' as area_code, date '2025-03-03' as trade_date, h as hour_ending,
            timestamp '2025-03-01 21:00:00' as forecast_reference_at,
            cast(20 + 0.5 * h as double) as popw_forecast_temperature_c,
            cast(0.5 as double) as popw_forecast_solar_radiation_mjm2,
            timestamp '2025-03-02 01:00:00' as available_at
          from hours
          union all
          select 'tokyo', date '2025-03-04', h, timestamp '2025-03-02 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), timestamp '2025-03-03 01:00:00'
          from hours
          union all
          select 'tokyo', date '2025-03-05', h, timestamp '2025-03-03 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), timestamp '2025-03-04 01:00:00'
          from hours where h <> 20
      - input: ref('ftr_day_jma_obs')
        format: sql
        rows: |
          select 'tokyo' as area_code, date '2025-03-03' as trade_date,
            cast(16.25 as double) as lag_2d_mean_popw_temperature_c,
            cast(20.25 as double) as lag_2d_evening_mean_popw_temperature_c,
            cast(0.25 as double) as lag_2d_mean_popw_solar_radiation_mjm2,
            timestamp '2025-03-02 01:00:00' as available_at
          union all
          select 'tokyo', date '2025-03-06', cast(16.25 as double), cast(20.25 as double),
            cast(0.25 as double), timestamp '2025-03-05 01:00:00'
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-03, forecast_reference_at: "2025-03-01 21:00:00", mean_popw_forecast_temperature_c: 26.25, evening_mean_popw_forecast_temperature_c: 30.25, mean_popw_forecast_solar_radiation_mjm2: 0.5, delta_lag_2d_mean_popw_temperature_c: 10.0, delta_lag_2d_evening_mean_popw_temperature_c: 10.0, delta_lag_2d_mean_popw_solar_radiation_mjm2: 0.25, available_at: "2025-03-02 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-04, forecast_reference_at: "2025-03-02 21:00:00", mean_popw_forecast_temperature_c: 26.25, evening_mean_popw_forecast_temperature_c: 30.25, mean_popw_forecast_solar_radiation_mjm2: 0.5, delta_lag_2d_mean_popw_temperature_c: null, delta_lag_2d_evening_mean_popw_temperature_c: null, delta_lag_2d_mean_popw_solar_radiation_mjm2: null, available_at: "2025-03-03 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-05, forecast_reference_at: "2025-03-03 21:00:00", mean_popw_forecast_temperature_c: null, evening_mean_popw_forecast_temperature_c: null, mean_popw_forecast_solar_radiation_mjm2: null, delta_lag_2d_mean_popw_temperature_c: null, delta_lag_2d_evening_mean_popw_temperature_c: null, delta_lag_2d_mean_popw_solar_radiation_mjm2: null, available_at: "2025-03-04 01:00:00"}
```

Note on the third row: with hour 20 missing the day is incomplete, so its daily means are null too; the row still pins that the evening mean needs its four hours. Add the five columns to the contract after `morning_trend_popw_forecast_temperature_c`:

```yaml
      - name: evening_mean_popw_forecast_temperature_c
        data_type: double
        description: >
          The mean of the population-weighted forecast temperature over the four hours
          ending 19:00 to 22:00, the load's evening window 18:00 to 22:00, C, added in hour
          order (the ordered_weighted_mean macro, weight 1): D's side of
          delta_lag_2d_evening_mean_popw_temperature_c (feature candidate #237). Null unless
          all four hours have a temperature; the other twenty are not needed.
        config:
          meta:
            feature: true
            categorical: false
            expression: "DAILY_MEAN(MEAN(forecast_temperature_c, weight=population), time=18:00-22:00)"
      - name: mean_popw_forecast_solar_radiation_mjm2
        data_type: double
        description: >
          The mean of the day's 24 hourly population-weighted forecast solar radiation,
          MJ/m2 per hour, added in hour order: D's side of
          delta_lag_2d_mean_popw_solar_radiation_mjm2 (feature candidate #238). Null unless
          all 24 hours have a value.
        config:
          meta:
            feature: true
            categorical: false
            expression: "DAILY_MEAN(MEAN(forecast_solar_radiation_mjm2, weight=population))"
      - name: delta_lag_2d_mean_popw_temperature_c
        data_type: double
        description: >
          mean_popw_forecast_temperature_c minus ftr_day_jma_obs.lag_2d_mean_popw_temperature_c:
          how far D's forecast daily mean sits from the observed one two days earlier, C;
          positive when D is warmer (feature candidate #237). It lives here because a delta
          needs the vintage, which this row already waits for. Null without a sibling row.
        config:
          meta:
            feature: true
            categorical: false
            expression: "DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)) - LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)"
      - name: delta_lag_2d_evening_mean_popw_temperature_c
        data_type: double
        description: >
          evening_mean_popw_forecast_temperature_c minus
          ftr_day_jma_obs.lag_2d_evening_mean_popw_temperature_c, C (feature candidate #237).
        config:
          meta:
            feature: true
            categorical: false
            expression: "DAILY_MEAN(MEAN(forecast_temperature_c, weight=population), time=18:00-22:00) - LAG(DAILY_MEAN(MEAN(temperature_c, weight=population), time=18:00-22:00), 2d)"
      - name: delta_lag_2d_mean_popw_solar_radiation_mjm2
        data_type: double
        description: >
          mean_popw_forecast_solar_radiation_mjm2 minus
          ftr_day_jma_obs.lag_2d_mean_popw_solar_radiation_mjm2, MJ/m2 per hour; positive
          when D is forecast brighter (feature candidate #238). The observed side rests on
          the stations that record radiation, 55.4 % of the area; the forecast side on every
          station.
        config:
          meta:
            feature: true
            categorical: false
            expression: "DAILY_MEAN(MEAN(forecast_solar_radiation_mjm2, weight=population)) - LAG(DAILY_MEAN(MEAN(solar_radiation_mjm2, weight=population)), 2d)"
```

Extend the model description with: "Since 2026-09-29 also the evening mean of the forecast temperature and the daily mean of the forecast radiation, and the differences of the daily mean, the evening mean and the radiation mean from D-2's observed ones in ftr_day_jma_obs (feature candidates #237 and #238)." Update `available_at`'s description: "…the vintage's, reference + 4 h, or the D-2 sibling's if later, which it never is (01:00 on D-1 both)."

- [ ] **Step 2: Run the mart's unit tests to see the new one fail**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_day_msm,test_type:unit"`
Expected: the new test fails on the invalid expected column `evening_mean_popw_forecast_temperature_c` (the existing two may error on the unused input until the model reads it). RED.

- [ ] **Step 3: Add the aggregates and the join**

In `dbt/models/features/ftr_day_msm.sql`, in the `days` CTE add after `temperature_terms`:

```sql
    array_sort(collect_list(
      case when hour_ending between 19 and 22 and popw_forecast_temperature_c is not null
        then named_struct(
          'hour_ending', hour_ending,
          'weight', cast(1 as double),
          'value', popw_forecast_temperature_c
        )
      end
    )) as evening_temperature_terms,
    count(popw_forecast_solar_radiation_mjm2) as n_radiation_hours,
    array_sort(collect_list(
      case when popw_forecast_solar_radiation_mjm2 is not null
        then named_struct(
          'hour_ending', hour_ending,
          'weight', cast(1 as double),
          'value', popw_forecast_solar_radiation_mjm2
        )
      end
    )) as radiation_terms,
```

Rename the existing `final` CTE to `forecast`, add to its select after `morning_trend_popw_forecast_temperature_c`:

```sql
    -- The evening mean needs its four hours only; the radiation mean, complete days only.
    case when size(evening_temperature_terms) = 4 then {{ ordered_weighted_mean('evening_temperature_terms') }} end
      as evening_mean_popw_forecast_temperature_c,
    case when n_radiation_hours = 24 then {{ ordered_weighted_mean('radiation_terms') }} end
      as mean_popw_forecast_solar_radiation_mjm2,
```

and add after it:

```sql
  -- D-2's observed means, the weather the D-2 daily load features were recorded under.
  observed as (
  select
    area_code,
    trade_date,
    lag_2d_mean_popw_temperature_c,
    lag_2d_evening_mean_popw_temperature_c,
    lag_2d_mean_popw_solar_radiation_mjm2,
    available_at
  from {{ ref('ftr_day_jma_obs') }}
  ),

  final as (
  select
    forecast.area_code,
    forecast.trade_date,
    forecast.forecast_reference_at,
    forecast.max_popw_forecast_temperature_c,
    forecast.min_popw_forecast_temperature_c,
    forecast.mean_popw_forecast_temperature_c,
    forecast.max_popw_forecast_temperature_hour_ending,
    forecast.morning_trend_popw_forecast_temperature_c,
    forecast.evening_mean_popw_forecast_temperature_c,
    forecast.mean_popw_forecast_solar_radiation_mjm2,
    -- D's forecast minus D-2's observation: positive when D is warmer or brighter
    -- than the day the D-2 load features come from. Null without a sibling row.
    forecast.mean_popw_forecast_temperature_c - observed.lag_2d_mean_popw_temperature_c
      as delta_lag_2d_mean_popw_temperature_c,
    forecast.evening_mean_popw_forecast_temperature_c - observed.lag_2d_evening_mean_popw_temperature_c
      as delta_lag_2d_evening_mean_popw_temperature_c,
    forecast.mean_popw_forecast_solar_radiation_mjm2 - observed.lag_2d_mean_popw_solar_radiation_mjm2
      as delta_lag_2d_mean_popw_solar_radiation_mjm2,
    -- The vintage's instant: the sibling's, 01:00 on D-1, is never later.
    {{ available_at(['forecast.available_at', 'observed.available_at']) }} as available_at
  from
    forecast
    left join observed
      on observed.area_code = forecast.area_code
      and observed.trade_date = forecast.trade_date
  )
```

- [ ] **Step 4: Run the mart's unit tests to see them pass**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_day_msm,test_type:unit"`
Expected: `PASS 3`.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_day_msm.sql dbt/models/features/ftr_day_msm.yml
git commit -m "feat(dbt): the evening and radiation forecast means and the D-2 deltas in ftr_day_msm" -m "Refs #237, #238" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The generated files, the five hand-kept places, the fixture and the suite

**Files:**
- Regenerate: `power_market_analytics/features/views.py`, `dbt/models/curated/fct_feature_value.sql`, `dbt/models/curated/dim_feature.sql`
- Modify: `tests/test_feature_views.py` (the sorted list at lines 18–28, plus one entity assertion), `tests/test_feature_store.py` (`MART_NAMES`), `tests/test_feature_value_fact.py` (the expected set near line 92), `.github/ISSUE_TEMPLATE/feature-candidate.md` (the Mart line), `tests/conftest.py` (`_write_feature_marts`: its docstring, `day_msm_summary`, a new `day_jma_obs_rows`, the two `write_table` calls)

- [ ] **Step 1: Regenerate and watch the suite fail**

```bash
DBT_THRIFT_HOST=localhost uv run dbt parse --project-dir dbt --profiles-dir dbt && uv run python scripts/generate_feature_views.py
uv run pytest tests/test_feature_views.py tests/test_feature_store.py -x -q -p no:cacheprovider
```

Expected: the generated files gain `FTR_DAY_JMA_OBS` and the eight fields; the two tests fail on the mart lists (`ftr_day_jma_obs` present in the views, absent from the expected lists).

- [ ] **Step 2: The five places**

`tests/test_feature_views.py`: insert `"ftr_day_jma_obs",` after `"ftr_day_calendar",` in the sorted list and add `assert by_name["ftr_day_jma_obs"].entities == day` after the `ftr_day_calendar` assertion. `tests/test_feature_store.py`: insert `"ftr_day_jma_obs",` after `"ftr_day_calendar",`. `tests/test_feature_value_fact.py`: insert `"ftr_day_jma_obs",` after `"ftr_day_calendar",` in the expected set. `.github/ISSUE_TEMPLATE/feature-candidate.md`: the Mart comment lists `ftr_day_jma_obs` after `ftr_day_calendar`. In `tests/conftest.py`: the docstring "The nine feature marts" becomes "The ten feature marts"; add after `day_msm_summary`:

```python
def day_jma_obs_row(day: pd.Timestamp) -> dict | None:
    """``ftr_day_jma_obs``'s row of the fixture for delivery day ``day``, reading D-2.

    Only the representative station observes, so the area's population-weighted
    hour is its ``synthetic_temperature``. The daily mean needs all 24 hours, the
    evening mean the hours ending 19 to 22; an hour in ``TEMPERATURE_MISSING_HOURS``
    makes the mean that needs it None. The fixture records no radiation. None
    when D-2 is not a ``DEMAND_DAYS`` day.
    """
    obs_day = day - pd.Timedelta(days=2)
    if obs_day not in DEMAND_DAYS:
        return None

    def mean(hours: range) -> float | None:
        total = 0.0
        for hour in hours:
            if (obs_day, hour) in TEMPERATURE_MISSING_HOURS:
                return None
            total += synthetic_temperature(obs_day, hour)
        return total / len(hours)

    return {
        "area_code": "tokyo",
        "trade_date": day.date(),
        "lag_2d_mean_popw_temperature_c": mean(range(1, 25)),
        "lag_2d_evening_mean_popw_temperature_c": mean(range(19, 23)),
        "lag_2d_mean_popw_solar_radiation_mjm2": None,
        "available_at": obs_day + pd.Timedelta(days=1, hours=1),
    }
```

In `day_msm_summary`, add before `"available_at"`:

```python
        "evening_mean_popw_forecast_temperature_c": sum(temperatures[18:22]) / 4,
        "mean_popw_forecast_solar_radiation_mjm2": (
            sum(row["popw_forecast_solar_radiation_mjm2"] for row in hours) / 24
        ),
```

replacing the two `sum`s by the same in-order loop as `total` if the values do not reproduce (the mart adds in hour order; `sum` does too). Then, where `day_msm_rows` is built, add the deltas: after the list comprehension,

```python
    day_jma_obs_rows = [row for row in (day_jma_obs_row(day) for day in DEMAND_DAYS) if row]
    obs_by_day = {row["trade_date"]: row for row in day_jma_obs_rows}
    for row in day_msm_rows:
        obs = obs_by_day.get(row["trade_date"])
        for name, forecast_key, obs_key in (
            ("delta_lag_2d_mean_popw_temperature_c", "mean_popw_forecast_temperature_c", "lag_2d_mean_popw_temperature_c"),
            ("delta_lag_2d_evening_mean_popw_temperature_c", "evening_mean_popw_forecast_temperature_c", "lag_2d_evening_mean_popw_temperature_c"),
            ("delta_lag_2d_mean_popw_solar_radiation_mjm2", "mean_popw_forecast_solar_radiation_mjm2", "lag_2d_mean_popw_solar_radiation_mjm2"),
        ):
            row[name] = None if obs is None or obs[obs_key] is None else row[forecast_key] - obs[obs_key]
```

Write the new mart before `ftr_day_msm`'s `write_table`:

```python
    day_jma_obs = pd.DataFrame(day_jma_obs_rows)
    for col in (
        "lag_2d_mean_popw_temperature_c",
        "lag_2d_evening_mean_popw_temperature_c",
        "lag_2d_mean_popw_solar_radiation_mjm2",
    ):
        day_jma_obs[col] = nullable_column(day_jma_obs[col], float)
    write_table(
        spark,
        day_jma_obs,
        "area_code string, trade_date date, lag_2d_mean_popw_temperature_c double, "
        "lag_2d_evening_mean_popw_temperature_c double, "
        "lag_2d_mean_popw_solar_radiation_mjm2 double, available_at timestamp",
        "pma_features.ftr_day_jma_obs",
    )
```

and extend `ftr_day_msm`'s frame and schema: build `day_msm = pd.DataFrame(day_msm_rows)`, pass its three delta columns through `nullable_column(…, float)`, and add `evening_mean_popw_forecast_temperature_c double, mean_popw_forecast_solar_radiation_mjm2 double, delta_lag_2d_mean_popw_temperature_c double, delta_lag_2d_evening_mean_popw_temperature_c double, delta_lag_2d_mean_popw_solar_radiation_mjm2 double, ` before `available_at timestamp`.

- [ ] **Step 3: Run the suite and the static checks**

```bash
uv run pytest --cov --cov-report=term-missing -q -p no:cacheprovider
uv run ruff check . && uv run ruff format --check tests/conftest.py && uv run mypy
uv run python scripts/generate_feature_views.py --check && uv run python scripts/check_docs_links.py
```

Expected: all pass, coverage 100 %, every check clean.

- [ ] **Step 4: Commit**

```bash
git add power_market_analytics/features/views.py dbt/models/curated/fct_feature_value.sql dbt/models/curated/dim_feature.sql tests/test_feature_views.py tests/test_feature_store.py tests/test_feature_value_fact.py .github/ISSUE_TEMPLATE/feature-candidate.md tests/conftest.py
git commit -m "feat(forecasting): ftr_day_jma_obs and ftr_day_msm's new columns in the views, the catalogue, the tests and the fixture" -m "Refs #237, #238" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The warehouse proof

**Files:**
- Create under `scratch/2026-09-29-weather-siblings-day-marts/`: `README.md`, `before.sql`, `diff.sql`, `after.sql`, `feast_check.py`, `run_sql.py` (copy part 1's from `scratch/2026-09-29-weather-siblings-hour-marts/run_sql.py` in the main checkout)

- [ ] **Step 1: Copy `ftr_day_msm` before the build** — `before.sql`: `create table pma_scratch.wsib2_ftr_day_msm_before as select * from pma_features.ftr_day_msm; select count(*) from pma_scratch.wsib2_ftr_day_msm_before;`, run through `run_sql.py` in the devcontainer (`docker compose … exec -T -w /workspace/.claude/worktrees/weather-siblings-day-marts -e PYTHONPATH=/workspace/.claude/worktrees/weather-siblings-day-marts devcontainer python - /workspace/.claude/worktrees/weather-siblings-day-marts/scratch/2026-09-29-weather-siblings-day-marts/before.sql < scratch/2026-09-29-weather-siblings-day-marts/run_sql.py`). Expected: a count; note it.

- [ ] **Step 2: Build** — `DBT_THRIFT_HOST=localhost uv run dbt build --project-dir dbt --profiles-dir dbt --select "ftr_day_jma_obs+ ftr_day_msm+ dim_feature"` in the background. Expected: PASS on every node and test.

- [ ] **Step 3: Diff** — `diff.sql`: a null-safe full outer join of the copy against `pma_features.ftr_day_msm` on `(area_code, trade_date, forecast_reference_at)` with one `_moved` sum per old column (`max_`, `min_`, `mean_popw_forecast_temperature_c`, `max_popw_forecast_temperature_hour_ending`, `morning_trend_popw_forecast_temperature_c`, `available_at`) and `unmatched_rows`. Expected: 0 everywhere.

- [ ] **Step 4: Acceptance** — `after.sql`: the row counts of both marts; Tokyo 2025-03-04 from both marts joined on area and day (`lag_2d_mean_popw_temperature_c` near 14 to 16 °C, `mean_popw_forecast_temperature_c` near 2.5, so `delta_lag_2d_mean_popw_temperature_c` between −15 and −10; the evening delta more negative still, since the D-2 evening was 15 to 17 °C and D's forecast evening about 4); per-area counts of non-null siblings and deltas. Expected: those ranges; the counts near the number of days.

- [ ] **Step 5: Feast** — `feast_check.py` as part 1's, with the eight `ftr_day_jma_obs:` and `ftr_day_msm:` references, Tokyo 2025-03-02 to 03-05. Expected: 192 rows, the 2025-03-04 rows carrying the acceptance values on every period.

- [ ] **Step 6: Drop the copy; README** — `drop table pma_scratch.wsib2_ftr_day_msm_before;` and a one-line README.

---

### Task 5: Docs and the pull request

**Files:**
- Modify: `CLAUDE.md` (the marts bullet: "Today's nine" → ten; a `ftr_day_jma_obs` entry after `ftr_day_calendar`'s; the `ftr_day_msm` entry; the `feature_marts` fixture count at line 71), `docs/superpowers/README.md` (the spec row's Plan cell: `[plan 1](superpowers/plans/2026-09-29-lag-window-weather-siblings-1-hour-marts.md) · [plan 2](superpowers/plans/2026-09-29-lag-window-weather-siblings-2-day-marts.md)`)

- [ ] **Step 1: Document** — in CLAUDE.md's marts bullet: "Today's ten" and, after the `ftr_day_calendar` entry, "`ftr_day_jma_obs` (since 2026-09-29, feature candidates #237 and #238, spec `docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md`, PR 2 of 4: D-2's daily mean and evening mean (hours ending 19–22, the load's 18:00–22:00) of the population-weighted observed temperature and D-2's daily mean of the observed radiation, from `fct_area_weather_hourly`; complete days only for the daily means, the four evening hours for the evening mean, every sum in hour order; available at 01:00 on D-1; in no preset yet)"; in the `ftr_day_msm` entry, after the morning trend: "since 2026-09-29 also the evening mean of the forecast temperature and the daily mean of the forecast radiation, and the three `delta_lag_2d_*` columns, D's means minus `ftr_day_jma_obs`'s, placed here because a delta needs the vintage; in no preset yet"; line 71's "the nine `pma_features` marts" → ten. Also the `tests/conftest.py` docstring already says ten (Task 3).

- [ ] **Step 2: Check, commit, push, PR**

```bash
uv run python scripts/check_docs_links.py && uv run python scripts/generate_feature_views.py --check
git add CLAUDE.md docs/superpowers/README.md docs/superpowers/plans/2026-09-29-lag-window-weather-siblings-2-day-marts.md
git commit -m "docs: ftr_day_jma_obs and ftr_day_msm's weather siblings in CLAUDE.md; the part-2 plan" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin feature/issue-237-weather-siblings-day-marts
gh pr create --repo hankehly/power-market-analytics --base main --title "feat(dbt): ftr_day_jma_obs and the D-2 daily weather siblings and deltas of ftr_day_msm" --body-file scratch/2026-09-29-weather-siblings-day-marts/pr-body.md
gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label forecasting
```

The body follows `.github/pull_request_template.md` as part 1's did: Summary (PR 2 of 4, `Refs #237, #238`), Changes, Effect on what exists (the `ftr_day_msm` diff, the new mart, the ten-mart count), Checks, Decisions, Evidence, the attribution line.

- [ ] **Step 3: The review loop** — poll Codex as CLAUDE.md describes (part 1's `codex_poll.py`, copied into this scratch directory, with the push instant as SINCE); fix or rebut every finding, resolve threads; when a round ends clean with CI green, merge with `gh pr merge <n> --merge --delete-branch` (the researcher's standing instruction of 2026-09-29: merge when ready).
