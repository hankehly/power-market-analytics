# The forecast weather of D-1 — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `ftr_hour_msm` three and `ftr_day_msm` eight columns that read D-1's own MSM forecast off the marts' rows one day earlier (feature candidate #248), then test them in one Tokyo run on the pinned window as preset `e249` against `e245` (experiment #249).

**Architecture:** Each MSM mart self-joins its own vintage rows on the run one day earlier — D's row of run V reads D-1's row of run V − 24 h — so one row goes in and one comes out whatever else is loaded, nothing is downloaded, and every row's `available_at` stays the vintage's. The day mart also computes D-1's rain mean from the hour mart's rain column, which it did not read before, and two columns read the D-2 observed mean the mart already joins. The generated files, the fixture marts and the docs follow the columns as every column PR before this one; the preset PR follows the three experiment PRs of #243 to #245.

**Tech Stack:** dbt (Spark SQL, unit tests, enforced contracts), the `ordered_weighted_mean` and `available_at` macros, `scripts/generate_feature_views.py`, pytest with the local Spark fixture, the devcontainer's Spark session for the proofs and the run, `scripts/demand_backtest.py`, `scripts/compare_demand_runs.py`, `gh`.

**Spec:** `docs/superpowers/specs/2026-09-30-previous-day-forecast-weather-design.md` (sections 2, 4, 5, 6, 7, 8).

## Global Constraints

- Work only in the worktree `.claude/worktrees/d1-forecast-weather-spec`, branch `feature/issue-248-d1-forecast-weather` for PR 1; PR 2 is a second worktree cut from PR 1's branch, `feature/issue-249-d1-forecast-experiment`. Compose commands use `docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics …`; the devcontainer sees the worktree at `/workspace/.claude/worktrees/d1-forecast-weather-spec`.
- Host-side dbt from the worktree: `DBT_THRIFT_HOST=localhost uv run dbt <cmd> --project-dir dbt --profiles-dir dbt`, one dbt process at a time, after `uv sync --locked` and `uv run dbt deps --project-dir dbt --profiles-dir dbt`.
- A unit-test input for a relation given as `format: sql` must carry every column the model reads from it: `ftr_day_msm`'s third unit test gains `popw_forecast_precipitation_mm`.
- Means of doubles go through `ordered_weighted_mean` over an `array_sort`ed `collect_list`, weight 1, never `avg()`.
- Physical names, expressions and rules are the spec's, section 4, verbatim. A `LAG(…, 1d)` of a forecast is D-1's own forecast, from the run one day earlier; every description says so.
- Every new input feeds `available_at` through `greatest()`; no existing row's `available_at` may move.
- Commits: Conventional Commits with `Refs #248` (PR 1) or `Refs #249` (PR 2) and the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; never the Codex mention in anything that shows in a diff.
- Long-running operations (`dbt build`, the backtest) run as main-session background tasks.

## Review Focus

1. A second run loaded for D-1: D's row must read the run one day before its own, not the other (Task 1's unit test, hour 1 of 2025-03-10 against the 09:00 run of 2025-03-09; Task 2's unit test, 2025-03-07 against the two runs of 2025-03-06).
2. D-1 incomplete: the day-grain lag columns null while D's own summaries stand (Task 2's unit test, 2025-03-05).
3. A D-1 hour with a temperature and no radiation: the temperature lag and the difference present, the radiation lag null (Task 1's unit test, hour 2).
4. No D-1 row at all: the eleven null, D's row kept with its own columns and `available_at` (Task 1's hour 3, Task 2's 2025-03-02).
5. The rain mean on a D-1 missing one hour is null, complete days only, like the radiation mean (Task 2's unit test, 2025-03-05 reading 2025-03-04).

---

### Task 1: `ftr_hour_msm`'s three columns

**Files:**
- Modify: `dbt/models/features/ftr_hour_msm.sql` (the `observed` and `final` CTEs, lines 152–201)
- Modify: `dbt/models/features/ftr_hour_msm.yml` (the contract after `delta_lag_7d_popw_solar_radiation_mjm2`, the model description, a fifth unit test)

**Interfaces:**
- Consumes: the model's own `forecast` CTE (`area_code`, `trade_date`, `hour_ending`, `forecast_reference_at`, `popw_forecast_temperature_c`, `popw_forecast_solar_radiation_mjm2`, `available_at`).
- Produces: `lag_1d_popw_forecast_temperature_c`, `lag_1d_popw_forecast_solar_radiation_mjm2`, `delta_lag_1d_popw_forecast_temperature_c` (doubles, tagged). Task 3 reads the names.

- [ ] **Step 1: Write the failing unit test**

Append to `unit_tests:` in `dbt/models/features/ftr_hour_msm.yml`:

```yaml
  - name: ftr_hour_msm_previous_day_forecast_from_the_run_one_day_earlier
    description: >
      One Tokyo station carrying the whole weight. 2025-03-09 has hours 1 and 2 of the
      run of 2025-03-07 21:00 (10 C and 0.5 MJ/m2, 12 C and no radiation) and hour 1 of
      a second run, 2025-03-08 09:00, at 99 C. 2025-03-10 has hours 1 to 3 of the run of
      2025-03-08 21:00 (8, 9, 7 C; 1.0, 1.5, 2.0 MJ/m2). D's row reads the run one day
      before its own: hour 1 of 03-10 gets 10 C and 0.5, the difference 8 - 10 = -2, not
      the 99 of the other run; hour 2 gets 12 C, a null radiation and 9 - 12 = -3; hour 3
      has no D-1 row and the three are null. Every 03-09 row has the three null: no
      03-08 row exists, and the 09:00 run finds no run of 2025-03-07 09:00. available_at
      stays each vintage's own; the D-1 vintage's, a day earlier, never wins greatest().
    model: ftr_hour_msm
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo, representative_jma_station_id: s1}
      - input: ref('fct_census_population_jma_station')
        rows:
          - {census_year: 2020, area_key: 1, station_id: s1, area_population_weight: 1.0}
      - input: ref('fct_jma_msm_weather_forecast_hourly')
        rows:
          - {station_id: s1, date_key: 2025-03-09, forecast_hour_start_at: "2025-03-09 00:00:00", forecast_reference_at: "2025-03-07 21:00:00", temperature_c: 10.0, solar_radiation_mjm2: 0.5, available_at: "2025-03-08 01:00:00"}
          - {station_id: s1, date_key: 2025-03-09, forecast_hour_start_at: "2025-03-09 01:00:00", forecast_reference_at: "2025-03-07 21:00:00", temperature_c: 12.0, solar_radiation_mjm2: null, available_at: "2025-03-08 01:00:00"}
          - {station_id: s1, date_key: 2025-03-09, forecast_hour_start_at: "2025-03-09 00:00:00", forecast_reference_at: "2025-03-08 09:00:00", temperature_c: 99.0, solar_radiation_mjm2: 9.0, available_at: "2025-03-08 13:00:00"}
          - {station_id: s1, date_key: 2025-03-10, forecast_hour_start_at: "2025-03-10 00:00:00", forecast_reference_at: "2025-03-08 21:00:00", temperature_c: 8.0, solar_radiation_mjm2: 1.0, available_at: "2025-03-09 01:00:00"}
          - {station_id: s1, date_key: 2025-03-10, forecast_hour_start_at: "2025-03-10 01:00:00", forecast_reference_at: "2025-03-08 21:00:00", temperature_c: 9.0, solar_radiation_mjm2: 1.5, available_at: "2025-03-09 01:00:00"}
          - {station_id: s1, date_key: 2025-03-10, forecast_hour_start_at: "2025-03-10 02:00:00", forecast_reference_at: "2025-03-08 21:00:00", temperature_c: 7.0, solar_radiation_mjm2: 2.0, available_at: "2025-03-09 01:00:00"}
      - input: ref('ftr_hour_jma_obs')
        format: sql
        rows: |
          select cast(null as string) as area_code, cast(null as date) as trade_date,
            cast(null as int) as hour_ending,
            cast(null as double) as lag_2d_popw_temperature_c, cast(null as double) as lag_7d_popw_temperature_c,
            cast(null as double) as lag_2d_popw_solar_radiation_mjm2, cast(null as double) as lag_7d_popw_solar_radiation_mjm2,
            cast(null as timestamp) as available_at
          where false
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-09, hour_ending: 1, forecast_reference_at: "2025-03-07 21:00:00", popw_forecast_temperature_c: 10.0, lag_1d_popw_forecast_temperature_c: null, lag_1d_popw_forecast_solar_radiation_mjm2: null, delta_lag_1d_popw_forecast_temperature_c: null, available_at: "2025-03-08 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-09, hour_ending: 2, forecast_reference_at: "2025-03-07 21:00:00", popw_forecast_temperature_c: 12.0, lag_1d_popw_forecast_temperature_c: null, lag_1d_popw_forecast_solar_radiation_mjm2: null, delta_lag_1d_popw_forecast_temperature_c: null, available_at: "2025-03-08 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-09, hour_ending: 1, forecast_reference_at: "2025-03-08 09:00:00", popw_forecast_temperature_c: 99.0, lag_1d_popw_forecast_temperature_c: null, lag_1d_popw_forecast_solar_radiation_mjm2: null, delta_lag_1d_popw_forecast_temperature_c: null, available_at: "2025-03-08 13:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 1, forecast_reference_at: "2025-03-08 21:00:00", popw_forecast_temperature_c: 8.0, lag_1d_popw_forecast_temperature_c: 10.0, lag_1d_popw_forecast_solar_radiation_mjm2: 0.5, delta_lag_1d_popw_forecast_temperature_c: -2.0, available_at: "2025-03-09 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 2, forecast_reference_at: "2025-03-08 21:00:00", popw_forecast_temperature_c: 9.0, lag_1d_popw_forecast_temperature_c: 12.0, lag_1d_popw_forecast_solar_radiation_mjm2: null, delta_lag_1d_popw_forecast_temperature_c: -3.0, available_at: "2025-03-09 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 3, forecast_reference_at: "2025-03-08 21:00:00", popw_forecast_temperature_c: 7.0, lag_1d_popw_forecast_temperature_c: null, lag_1d_popw_forecast_solar_radiation_mjm2: null, delta_lag_1d_popw_forecast_temperature_c: null, available_at: "2025-03-09 01:00:00"}
```

Add the three columns to the contract after `delta_lag_7d_popw_solar_radiation_mjm2`:

```yaml
      - name: lag_1d_popw_forecast_temperature_c
        data_type: double
        description: >
          D-1's population-weighted forecast temperature at this hour, C, read off the
          mart's own row for D-1: the 12 UTC run of D-3, the one the warehouse loaded as
          delivery day D-1, so the run one day before the run that gives D. Not an earlier
          copy of D's forecast (feature candidate #248; spec
          docs/superpowers/specs/2026-09-30-previous-day-forecast-weather-design.md). Null
          where D-1 has no row for the hour under that run.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(MEAN(forecast_temperature_c, weight=population), 1d)"
      - name: lag_1d_popw_forecast_solar_radiation_mjm2
        data_type: double
        description: >
          D-1's population-weighted forecast solar radiation over this hour, MJ/m2, from the
          same D-1 row (feature candidate #248). Null where D-1 has no row for the hour, or
          no radiation at it.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(MEAN(forecast_solar_radiation_mjm2, weight=population), 1d)"
      - name: delta_lag_1d_popw_forecast_temperature_c
        data_type: double
        description: >
          popw_forecast_temperature_c minus lag_1d_popw_forecast_temperature_c: D's forecast
          minus D-1's at the same hour, C; positive when D is forecast warmer than D-1. Both
          sides are forecasts, so the forecast's bias against the stations cancels
          (feature candidate #248). Null without a D-1 row.
        config:
          meta:
            feature: true
            categorical: false
            expression: "DIFF(MEAN(forecast_temperature_c, weight=population), 1d)"
```

Extend the model description with: "Since 2026-09-30 also D-1's forecast temperature and radiation at the same hour and D's forecast temperature minus D-1's, read off the mart's own row for D-1 under the run one day earlier (feature candidate #248)." Extend `available_at`'s description with: "The D-1 row's, a day earlier, never wins either."

- [ ] **Step 2: Run the mart's unit tests to see the new one fail**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_hour_msm,test_type:unit"`
Expected: the new test fails on the unknown expected column `lag_1d_popw_forecast_temperature_c`; the four existing ones pass. RED.

- [ ] **Step 3: Add the self-join**

In `dbt/models/features/ftr_hour_msm.sql`, after the `observed` CTE add:

```sql
  -- D-1's forecast at the same hour, read off this mart's own row for D-1 under the
  -- run one day before the run that gives D: the 12 UTC run of D-3, the one loaded as
  -- delivery day D-1. Keyed on the run so a second run loaded for D-1 cannot double D.
  previous_day as (
  select
    area_code,
    date_add(trade_date, 1) as trade_date,
    hour_ending,
    timestampadd(day, 1, forecast_reference_at) as forecast_reference_at,
    popw_forecast_temperature_c as lag_1d_popw_forecast_temperature_c,
    popw_forecast_solar_radiation_mjm2 as lag_1d_popw_forecast_solar_radiation_mjm2,
    available_at
  from forecast
  ),
```

In `final`, after `delta_lag_7d_popw_solar_radiation_mjm2` add:

```sql
    -- D-1's forecast and D's minus it: positive when D is forecast warmer than D-1.
    -- Both sides are forecasts, so the forecast's bias against the stations cancels.
    -- Null where D-1 has no row under the run one day earlier.
    previous_day.lag_1d_popw_forecast_temperature_c,
    previous_day.lag_1d_popw_forecast_solar_radiation_mjm2,
    forecast.popw_forecast_temperature_c - previous_day.lag_1d_popw_forecast_temperature_c
      as delta_lag_1d_popw_forecast_temperature_c,
```

replace the `available_at` line with:

```sql
    -- The vintage's instant: the sibling's is never later (greatest skips a null one),
    -- and the D-1 row's is a day earlier.
    {{ available_at(['forecast.available_at', 'observed.available_at', 'previous_day.available_at']) }} as available_at
```

and add to the `from` clause after the `observed` join:

```sql
    left join previous_day
      on previous_day.area_code = forecast.area_code
      and previous_day.trade_date = forecast.trade_date
      and previous_day.hour_ending = forecast.hour_ending
      and previous_day.forecast_reference_at = forecast.forecast_reference_at
```

- [ ] **Step 4: Run the mart's unit tests to see them pass**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_hour_msm,test_type:unit"`
Expected: `PASS 5`.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_hour_msm.sql dbt/models/features/ftr_hour_msm.yml
git commit -m "feat(dbt): D-1's forecast temperature and radiation at the same hour, and D minus D-1, in ftr_hour_msm" -m "Refs #248" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `ftr_day_msm`'s eight columns

**Files:**
- Modify: `dbt/models/features/ftr_day_msm.sql`
- Modify: `dbt/models/features/ftr_day_msm.yml` (the contract after `delta_lag_2d_mean_popw_solar_radiation_mjm2`, the model description, the third unit test's input, a fourth unit test)

**Interfaces:**
- Consumes: `ftr_hour_msm` (`popw_forecast_temperature_c`, `popw_forecast_solar_radiation_mjm2`, and now `popw_forecast_precipitation_mm`, per vintage), `ftr_day_jma_obs` (`lag_2d_mean_popw_temperature_c`), the model's own `forecast` CTE.
- Produces: `lag_1d_mean_popw_forecast_temperature_c`, `lag_1d_min_popw_forecast_temperature_c`, `lag_1d_evening_mean_popw_forecast_temperature_c`, `lag_1d_mean_popw_forecast_solar_radiation_mjm2`, `lag_1d_mean_popw_forecast_precipitation_mm`, `delta_lag_1d_mean_popw_forecast_temperature_c`, `change_1d_2d_mean_popw_temperature_c`, `mean_3d_popw_temperature_c` (doubles, tagged).

- [ ] **Step 1: Give the third unit test the rain column and write the failing test**

In `dbt/models/features/ftr_day_msm.yml`, in the test `ftr_day_msm_evening_and_radiation_means_and_the_deltas_to_d_2`, give the `ftr_hour_msm` input a rain column: after `cast(0.5 as double) as popw_forecast_solar_radiation_mjm2,` in the first select add `cast(null as double) as popw_forecast_precipitation_mm,`, and in the two `union all` selects add `cast(null as double),` after `cast(0.5 as double),`.

Append a fourth unit test:

```yaml
  - name: ftr_day_msm_previous_day_forecast_from_the_run_one_day_earlier
    description: >
      Tokyo, one run a day, 24 hours each unless said. 2025-03-02 (run 02-28 21:00) has
      the temperature 10 + 0.5 x the hour (mean 16.25, minimum 10.5, evening hours 19 to
      22 mean 20.25), radiation 0.25 and rain 0.125; no D-1 row, so its eight are null.
      2025-03-03 (run 03-01 21:00) has 20 + 0.5 x the hour (26.25, 20.5, 30.25), 0.5 and
      0.25: it reads 03-02's five values, the difference is 26.25 - 16.25 = 10.0, and with
      a D-2 sibling of 12.25 the change is 16.25 - 12.25 = 4.0 and the three-day mean
      (26.25 + 16.25 + 12.25) / 3 = 18.25, every sum exact. 2025-03-04 (run 03-02 21:00)
      has no hour 20: its own summaries are null, so the difference and the three-day mean
      are null, while its five lag columns read the complete 03-03. 2025-03-05 (run 03-03
      21:00) is complete but reads the incomplete 03-04: all five lag columns null, the
      rain mean too, complete days only. 2025-03-06 has two runs, 03-04 21:00 complete and
      03-04 09:00 with hour 1 alone at 99 C. 2025-03-07 (run 03-05 21:00) reads 03-06
      under the run one day before its own, 03-04 21:00: 26.25, not 99; the 09:00 run's
      own row finds no run of 03-03 09:00 and has the eight null. available_at stays
      each vintage's own on every row.
    model: ftr_day_msm
    given:
      - input: ref('ftr_hour_msm')
        format: sql
        rows: |
          with hours as (select explode(sequence(1, 24)) as h)
          select 'tokyo' as area_code, date '2025-03-02' as trade_date, h as hour_ending,
            timestamp '2025-02-28 21:00:00' as forecast_reference_at,
            cast(10 + 0.5 * h as double) as popw_forecast_temperature_c,
            cast(0.25 as double) as popw_forecast_solar_radiation_mjm2,
            cast(0.125 as double) as popw_forecast_precipitation_mm,
            timestamp '2025-03-01 01:00:00' as available_at
          from hours
          union all
          select 'tokyo', date '2025-03-03', h, timestamp '2025-03-01 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), cast(0.25 as double),
            timestamp '2025-03-02 01:00:00'
          from hours
          union all
          select 'tokyo', date '2025-03-04', h, timestamp '2025-03-02 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), cast(0.25 as double),
            timestamp '2025-03-03 01:00:00'
          from hours where h <> 20
          union all
          select 'tokyo', date '2025-03-05', h, timestamp '2025-03-03 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), cast(0.25 as double),
            timestamp '2025-03-04 01:00:00'
          from hours
          union all
          select 'tokyo', date '2025-03-06', h, timestamp '2025-03-04 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), cast(0.25 as double),
            timestamp '2025-03-05 01:00:00'
          from hours
          union all
          select 'tokyo', date '2025-03-06', 1, timestamp '2025-03-04 09:00:00',
            cast(99 as double), cast(9 as double), cast(9 as double),
            timestamp '2025-03-04 13:00:00'
          union all
          select 'tokyo', date '2025-03-07', h, timestamp '2025-03-05 21:00:00',
            cast(20 + 0.5 * h as double), cast(0.5 as double), cast(0.25 as double),
            timestamp '2025-03-06 01:00:00'
          from hours
      - input: ref('ftr_day_jma_obs')
        format: sql
        rows: |
          select 'tokyo' as area_code, date '2025-03-03' as trade_date,
            cast(12.25 as double) as lag_2d_mean_popw_temperature_c,
            cast(14.25 as double) as lag_2d_evening_mean_popw_temperature_c,
            cast(0.125 as double) as lag_2d_mean_popw_solar_radiation_mjm2,
            timestamp '2025-03-02 01:00:00' as available_at
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-02, forecast_reference_at: "2025-02-28 21:00:00", mean_popw_forecast_temperature_c: 16.25, lag_1d_mean_popw_forecast_temperature_c: null, lag_1d_min_popw_forecast_temperature_c: null, lag_1d_evening_mean_popw_forecast_temperature_c: null, lag_1d_mean_popw_forecast_solar_radiation_mjm2: null, lag_1d_mean_popw_forecast_precipitation_mm: null, delta_lag_1d_mean_popw_forecast_temperature_c: null, change_1d_2d_mean_popw_temperature_c: null, mean_3d_popw_temperature_c: null, available_at: "2025-03-01 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-03, forecast_reference_at: "2025-03-01 21:00:00", mean_popw_forecast_temperature_c: 26.25, lag_1d_mean_popw_forecast_temperature_c: 16.25, lag_1d_min_popw_forecast_temperature_c: 10.5, lag_1d_evening_mean_popw_forecast_temperature_c: 20.25, lag_1d_mean_popw_forecast_solar_radiation_mjm2: 0.25, lag_1d_mean_popw_forecast_precipitation_mm: 0.125, delta_lag_1d_mean_popw_forecast_temperature_c: 10.0, change_1d_2d_mean_popw_temperature_c: 4.0, mean_3d_popw_temperature_c: 18.25, available_at: "2025-03-02 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-04, forecast_reference_at: "2025-03-02 21:00:00", mean_popw_forecast_temperature_c: null, lag_1d_mean_popw_forecast_temperature_c: 26.25, lag_1d_min_popw_forecast_temperature_c: 20.5, lag_1d_evening_mean_popw_forecast_temperature_c: 30.25, lag_1d_mean_popw_forecast_solar_radiation_mjm2: 0.5, lag_1d_mean_popw_forecast_precipitation_mm: 0.25, delta_lag_1d_mean_popw_forecast_temperature_c: null, change_1d_2d_mean_popw_temperature_c: null, mean_3d_popw_temperature_c: null, available_at: "2025-03-03 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-05, forecast_reference_at: "2025-03-03 21:00:00", mean_popw_forecast_temperature_c: 26.25, lag_1d_mean_popw_forecast_temperature_c: null, lag_1d_min_popw_forecast_temperature_c: null, lag_1d_evening_mean_popw_forecast_temperature_c: null, lag_1d_mean_popw_forecast_solar_radiation_mjm2: null, lag_1d_mean_popw_forecast_precipitation_mm: null, delta_lag_1d_mean_popw_forecast_temperature_c: null, change_1d_2d_mean_popw_temperature_c: null, mean_3d_popw_temperature_c: null, available_at: "2025-03-04 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-06, forecast_reference_at: "2025-03-04 21:00:00", mean_popw_forecast_temperature_c: 26.25, lag_1d_mean_popw_forecast_temperature_c: 26.25, lag_1d_min_popw_forecast_temperature_c: 20.5, lag_1d_evening_mean_popw_forecast_temperature_c: 30.25, lag_1d_mean_popw_forecast_solar_radiation_mjm2: 0.5, lag_1d_mean_popw_forecast_precipitation_mm: 0.25, delta_lag_1d_mean_popw_forecast_temperature_c: 0.0, change_1d_2d_mean_popw_temperature_c: null, mean_3d_popw_temperature_c: null, available_at: "2025-03-05 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-06, forecast_reference_at: "2025-03-04 09:00:00", mean_popw_forecast_temperature_c: null, lag_1d_mean_popw_forecast_temperature_c: null, lag_1d_min_popw_forecast_temperature_c: null, lag_1d_evening_mean_popw_forecast_temperature_c: null, lag_1d_mean_popw_forecast_solar_radiation_mjm2: null, lag_1d_mean_popw_forecast_precipitation_mm: null, delta_lag_1d_mean_popw_forecast_temperature_c: null, change_1d_2d_mean_popw_temperature_c: null, mean_3d_popw_temperature_c: null, available_at: "2025-03-04 13:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-07, forecast_reference_at: "2025-03-05 21:00:00", mean_popw_forecast_temperature_c: 26.25, lag_1d_mean_popw_forecast_temperature_c: 26.25, lag_1d_min_popw_forecast_temperature_c: 20.5, lag_1d_evening_mean_popw_forecast_temperature_c: 30.25, lag_1d_mean_popw_forecast_solar_radiation_mjm2: 0.5, lag_1d_mean_popw_forecast_precipitation_mm: 0.25, delta_lag_1d_mean_popw_forecast_temperature_c: 0.0, change_1d_2d_mean_popw_temperature_c: null, mean_3d_popw_temperature_c: null, available_at: "2025-03-06 01:00:00"}
```

Add the eight columns to the contract after `delta_lag_2d_mean_popw_solar_radiation_mjm2`:

```yaml
      - name: lag_1d_mean_popw_forecast_temperature_c
        data_type: double
        description: >
          D-1's mean_popw_forecast_temperature_c, C, read off the mart's own row for D-1:
          the 12 UTC run of D-3, the one the warehouse loaded as delivery day D-1, so the
          run one day before the run that gives D. Not an earlier copy of D's forecast
          (feature candidate #248; spec
          docs/superpowers/specs/2026-09-30-previous-day-forecast-weather-design.md). Null
          unless D-1 has all 24 hours under that run.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d)"
      - name: lag_1d_min_popw_forecast_temperature_c
        data_type: double
        description: >
          D-1's min_popw_forecast_temperature_c, C, from the same D-1 row (feature candidate
          #248). Null unless D-1 has all 24 hours.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MIN(MEAN(forecast_temperature_c, weight=population)), 1d)"
      - name: lag_1d_evening_mean_popw_forecast_temperature_c
        data_type: double
        description: >
          D-1's evening_mean_popw_forecast_temperature_c, C, the hours ending 19:00 to
          22:00, from the same D-1 row (feature candidate #248). Null unless D-1 has all
          four evening hours.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population), time=18:00-22:00), 1d)"
      - name: lag_1d_mean_popw_forecast_solar_radiation_mjm2
        data_type: double
        description: >
          D-1's mean_popw_forecast_solar_radiation_mjm2, MJ/m2 per hour, from the same D-1
          row (feature candidate #248). Null unless D-1 has all 24 hours.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(forecast_solar_radiation_mjm2, weight=population)), 1d)"
      - name: lag_1d_mean_popw_forecast_precipitation_mm
        data_type: double
        description: >
          The mean of D-1's 24 hourly population-weighted forecast precipitation, mm per
          hour, added in hour order (the ordered_weighted_mean macro, weight 1), from the
          same D-1 row (feature candidate #248). A mean and not a day's total: a tree splits
          the two the same way, and the radiation mean is per hour already. D's own rain
          mean is not a column. Null unless D-1 has all 24 hours.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(forecast_precipitation_mm, weight=population)), 1d)"
      - name: delta_lag_1d_mean_popw_forecast_temperature_c
        data_type: double
        description: >
          mean_popw_forecast_temperature_c minus lag_1d_mean_popw_forecast_temperature_c:
          D's forecast daily mean minus D-1's, C; positive when D is forecast warmer than
          D-1. Both sides are forecasts, so the forecast's bias against the stations
          cancels (feature candidate #248). Null when either is.
        config:
          meta:
            feature: true
            categorical: false
            expression: "DIFF(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d)"
      - name: change_1d_2d_mean_popw_temperature_c
        data_type: double
        description: >
          lag_1d_mean_popw_forecast_temperature_c minus
          ftr_day_jma_obs.lag_2d_mean_popw_temperature_c: D-1's forecast daily mean minus
          D-2's observed one, C; positive when D-1 is forecast warmer than D-2 was, so the
          model can tell whether the change in the weather had arrived by D-1 (feature
          candidate #248). A forecast against an observation, as the delta_lag_2d columns
          are. Null when either is.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d) - LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)"
      - name: mean_3d_popw_temperature_c
        data_type: double
        description: >
          The mean of D's forecast daily mean, D-1's forecast daily mean and D-2's observed
          daily mean, C, added in that order and divided by 3 (feature candidate #248). Null
          unless all three are present.
        config:
          meta:
            feature: true
            categorical: false
            expression: "(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)) + LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d) + LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)) / 3"
```

Extend the model description with: "Since 2026-09-30 also D-1's daily mean, minimum, evening mean, radiation mean and rain mean, D's daily mean minus D-1's, D-1's minus D-2's observed one, and the mean of the three days, read off the mart's own row for D-1 under the run one day earlier (feature candidate #248)." Extend `available_at`'s description with: "The D-1 row's, a day earlier, never wins either."

- [ ] **Step 2: Run the mart's unit tests to see the new one fail**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_day_msm,test_type:unit"`
Expected: the fourth test fails on the unknown expected column `lag_1d_mean_popw_forecast_temperature_c`; the first three pass. RED.

- [ ] **Step 3: Add the rain aggregate and the self-join**

In `dbt/models/features/ftr_day_msm.sql`, in the `days` CTE add `popw_forecast_precipitation_mm` next to the radiation, after `radiation_terms,`:

```sql
    count(popw_forecast_precipitation_mm) as n_rain_hours,
    array_sort(collect_list(
      case when popw_forecast_precipitation_mm is not null
        then named_struct(
          'hour_ending', hour_ending,
          'weight', cast(1 as double),
          'value', popw_forecast_precipitation_mm
        )
      end
    )) as rain_terms,
```

In the `forecast` CTE, after `mean_popw_forecast_solar_radiation_mjm2,` add (an intermediate: D's own rain mean is not a column of the mart, only D-1's is):

```sql
    case when n_rain_hours = 24 then {{ ordered_weighted_mean('rain_terms') }} end
      as mean_popw_forecast_precipitation_mm,
```

After the `observed` CTE add:

```sql
  -- D-1's summaries, read off this mart's own row for D-1 under the run one day before
  -- the run that gives D: the 12 UTC run of D-3, the one loaded as delivery day D-1.
  -- Keyed on the run so a second run loaded for D-1 cannot double D.
  previous_day as (
  select
    area_code,
    date_add(trade_date, 1) as trade_date,
    timestampadd(day, 1, forecast_reference_at) as forecast_reference_at,
    mean_popw_forecast_temperature_c as lag_1d_mean_popw_forecast_temperature_c,
    min_popw_forecast_temperature_c as lag_1d_min_popw_forecast_temperature_c,
    evening_mean_popw_forecast_temperature_c as lag_1d_evening_mean_popw_forecast_temperature_c,
    mean_popw_forecast_solar_radiation_mjm2 as lag_1d_mean_popw_forecast_solar_radiation_mjm2,
    mean_popw_forecast_precipitation_mm as lag_1d_mean_popw_forecast_precipitation_mm,
    available_at
  from forecast
  ),
```

In `final`, after `delta_lag_2d_mean_popw_solar_radiation_mjm2` add:

```sql
    -- D-1's forecast summaries and the three differences that need them. D minus D-1
    -- is forecast against forecast; D-1 minus D-2 is a forecast against an observation,
    -- as the delta_lag_2d columns are; the three-day mean is added in day order. Null
    -- where D-1 has no row under the run one day earlier, or a term is missing.
    previous_day.lag_1d_mean_popw_forecast_temperature_c,
    previous_day.lag_1d_min_popw_forecast_temperature_c,
    previous_day.lag_1d_evening_mean_popw_forecast_temperature_c,
    previous_day.lag_1d_mean_popw_forecast_solar_radiation_mjm2,
    previous_day.lag_1d_mean_popw_forecast_precipitation_mm,
    forecast.mean_popw_forecast_temperature_c - previous_day.lag_1d_mean_popw_forecast_temperature_c
      as delta_lag_1d_mean_popw_forecast_temperature_c,
    previous_day.lag_1d_mean_popw_forecast_temperature_c - observed.lag_2d_mean_popw_temperature_c
      as change_1d_2d_mean_popw_temperature_c,
    (
      forecast.mean_popw_forecast_temperature_c
      + previous_day.lag_1d_mean_popw_forecast_temperature_c
      + observed.lag_2d_mean_popw_temperature_c
    ) / 3 as mean_3d_popw_temperature_c,
```

replace the `available_at` line with:

```sql
    -- The vintage's instant: the sibling's, 01:00 on D-1, is never later, and the D-1
    -- row's is a day earlier.
    {{ available_at(['forecast.available_at', 'observed.available_at', 'previous_day.available_at']) }} as available_at
```

and add to the `from` clause after the `observed` join:

```sql
    left join previous_day
      on previous_day.area_code = forecast.area_code
      and previous_day.trade_date = forecast.trade_date
      and previous_day.forecast_reference_at = forecast.forecast_reference_at
```

- [ ] **Step 4: Run the mart's unit tests to see them pass**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_day_msm,test_type:unit"`
Expected: `PASS 4`.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_day_msm.sql dbt/models/features/ftr_day_msm.yml
git commit -m "feat(dbt): D-1's forecast summaries and the three differences in ftr_day_msm" -m "Refs #248" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The generated files, the fixture and the suite

**Files:**
- Regenerate: `power_market_analytics/features/views.py`, `dbt/models/curated/fct_feature_value.sql`, `dbt/models/curated/dim_feature.sql`
- Modify: `tests/conftest.py` (`msm_rows` at lines 1683–1745, `day_msm_summary` at 1060, the `day_msm_rows` loop at 1754–1785, the two `write_table` schemas at 2286–2342)

**Interfaces:**
- Consumes: Task 1's and Task 2's column names.
- Produces: the fixture marts carrying the eleven, so every preset that names them (Task 6's `e249`) retrieves them through Feast in the tests.

- [ ] **Step 1: Regenerate and run the field-list tests**

```bash
DBT_THRIFT_HOST=localhost uv run dbt parse --project-dir dbt --profiles-dir dbt && uv run python scripts/generate_feature_views.py
uv run pytest tests/test_feature_views.py tests/test_feature_store.py tests/test_feature_value_fact.py -x -q -p no:cacheprovider
```

Expected: the three generated files gain the eleven fields; the tests pass (no mart was added, so the mart lists are unchanged) or fail on a fixture mart missing a field, which Step 2 fixes.

- [ ] **Step 2: The fixture**

In `tests/conftest.py`, add after `delta_to_lag_popw_temperature`:

```python
def previous_day_forecast(day: pd.Timestamp, hour_ending: int) -> dict[str, float | None]:
    """``ftr_hour_msm``'s three D-1 columns of the fixture for a delivery-day hour.

    D-1's population-weighted forecast temperature and radiation at the same hour,
    and D's forecast temperature minus D-1's; None on every one when D-1 is not a
    fixture forecast day (``FORECAST_MISSING_DAY`` or outside ``DEMAND_DAYS``).
    """
    previous = day - pd.Timedelta(days=1)
    if previous not in DEMAND_DAYS or previous == FORECAST_MISSING_DAY:
        return {
            "lag_1d_popw_forecast_temperature_c": None,
            "lag_1d_popw_forecast_solar_radiation_mjm2": None,
            "delta_lag_1d_popw_forecast_temperature_c": None,
        }
    temperature = popw_forecast(
        previous,
        hour_ending,
        synthetic_forecast_temperature(previous, hour_ending),
        SECOND_STATION_FORECAST_OFFSET_C,
    )
    today = popw_forecast(
        day, hour_ending, synthetic_forecast_temperature(day, hour_ending), SECOND_STATION_FORECAST_OFFSET_C
    )
    return {
        "lag_1d_popw_forecast_temperature_c": temperature,
        "lag_1d_popw_forecast_solar_radiation_mjm2": popw_forecast(
            previous,
            hour_ending,
            synthetic_forecast_solar_radiation(previous, hour_ending),
            SECOND_STATION_FORECAST_SOLAR_OFFSET_MJM2,
        ),
        "delta_lag_1d_popw_forecast_temperature_c": today - temperature,
    }
```

In the `msm_rows` comprehension, after `"delta_lag_7d_popw_solar_radiation_mjm2": None,` add `**previous_day_forecast(day, hour),`. In `day_msm_summary`, after the radiation mean add the rain mean the same way, as an intermediate the loop below reads and the table does not carry:

```python
        "mean_popw_forecast_precipitation_mm": (
            sum(row["popw_forecast_precipitation_mm"] for row in hours) / 24
        ),
```

After the `day_msm_rows` delta loop, add:

```python
    # D-1's summaries off the previous fixture day's row, D minus D-1, D-1 minus D-2's
    # observed mean and the three-day mean: None without a D-1 row or a term.
    summary_by_day = {pd.Timestamp(row["trade_date"]): row for row in day_msm_rows}
    for day_row in day_msm_rows:
        previous = summary_by_day.get(pd.Timestamp(day_row["trade_date"]) - pd.Timedelta(days=1))
        obs = obs_by_day.get(day_row["trade_date"])
        for name, key in (
            ("lag_1d_mean_popw_forecast_temperature_c", "mean_popw_forecast_temperature_c"),
            ("lag_1d_min_popw_forecast_temperature_c", "min_popw_forecast_temperature_c"),
            ("lag_1d_evening_mean_popw_forecast_temperature_c", "evening_mean_popw_forecast_temperature_c"),
            ("lag_1d_mean_popw_forecast_solar_radiation_mjm2", "mean_popw_forecast_solar_radiation_mjm2"),
            ("lag_1d_mean_popw_forecast_precipitation_mm", "mean_popw_forecast_precipitation_mm"),
        ):
            day_row[name] = None if previous is None else previous[key]
        previous_mean = day_row["lag_1d_mean_popw_forecast_temperature_c"]
        observed_mean = None if obs is None else obs["lag_2d_mean_popw_temperature_c"]
        day_row["delta_lag_1d_mean_popw_forecast_temperature_c"] = (
            None if previous_mean is None else day_row["mean_popw_forecast_temperature_c"] - previous_mean
        )
        day_row["change_1d_2d_mean_popw_temperature_c"] = (
            None if previous_mean is None or observed_mean is None else previous_mean - observed_mean
        )
        day_row["mean_3d_popw_temperature_c"] = (
            None
            if previous_mean is None or observed_mean is None
            else (day_row["mean_popw_forecast_temperature_c"] + previous_mean + observed_mean) / 3
        )
    for day_row in day_msm_rows:
        del day_row["mean_popw_forecast_precipitation_mm"]
```

`trade_date` in the rows is a `datetime.date`, so both sides of the lookup are `pd.Timestamp`s. Pass the three hour columns and the eight day columns through `nullable_column(…, float)` before their `write_table` calls, and extend the schemas: for `ftr_hour_msm`, `lag_1d_popw_forecast_temperature_c double, lag_1d_popw_forecast_solar_radiation_mjm2 double, delta_lag_1d_popw_forecast_temperature_c double, ` before `available_at timestamp`; for `ftr_day_msm`, `lag_1d_mean_popw_forecast_temperature_c double, lag_1d_min_popw_forecast_temperature_c double, lag_1d_evening_mean_popw_forecast_temperature_c double, lag_1d_mean_popw_forecast_solar_radiation_mjm2 double, lag_1d_mean_popw_forecast_precipitation_mm double, delta_lag_1d_mean_popw_forecast_temperature_c double, change_1d_2d_mean_popw_temperature_c double, mean_3d_popw_temperature_c double, ` before `available_at timestamp`.

- [ ] **Step 3: Run the suite and the static checks**

```bash
uv run pytest --cov --cov-report=term-missing -q -p no:cacheprovider
uv run ruff check . && uv run ruff format --check tests/conftest.py && uv run mypy
uv run python scripts/generate_feature_views.py --check && uv run python scripts/check_docs_links.py
```

Expected: all pass, coverage 100 %, every check clean.

- [ ] **Step 4: Commit**

```bash
git add power_market_analytics/features/views.py dbt/models/curated/fct_feature_value.sql dbt/models/curated/dim_feature.sql tests/conftest.py
git commit -m "feat(forecasting): the D-1 forecast columns in the views, the catalogue and the fixture" -m "Refs #248" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The warehouse proof

**Files:**
- Create under `scratch/2026-09-30-d1-forecast-weather/`: `before.sql`, `diff.sql`, `after.sql`, `feast_check.py`, `run_sql.py` (a copy of `/Users/hankehly/Projects/power-market-analytics/scratch/2026-09-29-243-weather-siblings-experiments/run_sql.py`), and `README.md`

- [ ] **Step 1: Copy both marts before the build** — `before.sql`: `create table pma_scratch.d1fc_ftr_hour_msm_before as select * from pma_features.ftr_hour_msm; create table pma_scratch.d1fc_ftr_day_msm_before as select * from pma_features.ftr_day_msm; select count(*) from pma_scratch.d1fc_ftr_hour_msm_before; select count(*) from pma_scratch.d1fc_ftr_day_msm_before;` run through `run_sql.py` in the devcontainer (`docker compose … exec -T -w /workspace/.claude/worktrees/d1-forecast-weather-spec -e PYTHONPATH=/workspace/.claude/worktrees/d1-forecast-weather-spec devcontainer python - /workspace/.claude/worktrees/d1-forecast-weather-spec/scratch/2026-09-30-d1-forecast-weather/before.sql < scratch/2026-09-30-d1-forecast-weather/run_sql.py`). Expected: two counts; note them.

- [ ] **Step 2: Build** — `DBT_THRIFT_HOST=localhost uv run dbt build --project-dir dbt --profiles-dir dbt --select "ftr_hour_msm+ dim_feature"` in the background (`ftr_hour_msm+` reaches `ftr_day_msm`, the period marts that join the hour mart, and `fct_feature_value`). Expected: PASS on every node and test.

- [ ] **Step 3: Diff** — `diff.sql`: for each mart a null-safe full outer join of the copy against the built table on its key (`area_code, trade_date, hour_ending, forecast_reference_at` / `area_code, trade_date, forecast_reference_at`) with one `_moved` sum per old column (`sum(case when not (old.c <=> new.c) then 1 else 0 end)`), `available_at` included, and `unmatched_rows`. Expected: 0 everywhere, the counts of Step 1.

- [ ] **Step 4: Acceptance** — `after.sql`: Tokyo 2025-03-04 from `ftr_hour_msm` at hour 14 and from `ftr_day_msm`: the spec's section 7 table (3.53 °C, 0.402 MJ/m², +1.67 °C; 5.07, 2.72, 3.05 °C, 0.109 MJ/m², 1.146 mm, −2.65, −9.16, 7.24 °C, to two decimals); per area, the counts of non-null values of each new column over 2022-04-01 … 2026-03-31 (near the number of days, or hours, minus the first day of the archive and the days after a gap). Expected: those values.

- [ ] **Step 5: Feast** — `feast_check.py`: `open_store()`, `entity_frame("tokyo", pd.date_range("2025-03-02", "2025-03-05"), TASK.issue_offset)`, `historical_features(store, frame, [the eleven "ftr_hour_msm:…" and "ftr_day_msm:…" references])`, printed for 2025-03-04 at time codes 27 and 28 (hour 14). Run in the devcontainer with the worktree as PYTHONPATH. Expected: 192 rows, the 2025-03-04 rows carrying the acceptance values on every period.

- [ ] **Step 6: Drop the copies; README** — `drop table pma_scratch.d1fc_ftr_hour_msm_before; drop table pma_scratch.d1fc_ftr_day_msm_before;` through `run_sql.py`, and a one-line README.

---

### Task 5: Docs and PR 1

**Files:**
- Modify: `CLAUDE.md` (the `ftr_hour_msm` and `ftr_day_msm` entries of the marts bullet), `docs/superpowers/README.md` (a new top row), `docs/Feature-Naming.md` (the `DIFF` example gains `DIFF(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d)`)

- [ ] **Step 1: Document** — in CLAUDE.md's `ftr_hour_msm` entry, before the sentence about the singular test: "since 2026-09-30 (feature candidate #248, spec `docs/superpowers/specs/2026-09-30-previous-day-forecast-weather-design.md`) also `lag_1d_popw_forecast_temperature_c`, `lag_1d_popw_forecast_solar_radiation_mjm2` and `delta_lag_1d_popw_forecast_temperature_c`, D-1's own forecast at the same hour read off the mart's row for D-1 under the run one day earlier (the 12 UTC run of D-3), and D's forecast minus it — forecast against forecast, so the forecast's bias against the stations cancels; nothing downloaded, no `available_at` moved; in no preset yet". In the `ftr_day_msm` entry, after the `delta_lag_2d_*` sentence: "since 2026-09-30 (feature candidate #248) also eight D-1 columns off the mart's own row for D-1 under the run one day earlier: `lag_1d_mean_`, `lag_1d_min_`, `lag_1d_evening_mean_popw_forecast_temperature_c`, `lag_1d_mean_popw_forecast_solar_radiation_mjm2`, `lag_1d_mean_popw_forecast_precipitation_mm` (a mean, mm per hour, over the rain column the mart did not read before; D's own rain mean is not a column), `delta_lag_1d_mean_popw_forecast_temperature_c` (D minus D-1, expression `DIFF(…, 1d)`), `change_1d_2d_mean_popw_temperature_c` (D-1's forecast mean minus D-2's observed) and `mean_3d_popw_temperature_c` (the three days' mean); in no preset yet". `docs/superpowers/README.md`: a top row dated 2026-09-30, "The forecast weather of D-1 — eleven columns in the MSM marts read off D-1's own row under the run one day earlier, no download; forecasts only, the researcher's choice of three options after the 2025-03-04 analysis (#247); candidate #248, experiment #249", spec and plan links.

- [ ] **Step 2: Check, commit, push, PR**

```bash
uv run python scripts/check_docs_links.py && uv run python scripts/generate_feature_views.py --check
git add CLAUDE.md docs/superpowers/README.md docs/Feature-Naming.md docs/superpowers/plans/2026-09-30-previous-day-forecast-weather.md
git commit -m "docs: the D-1 forecast columns in CLAUDE.md, the naming guide and the design history" -m "Refs #248" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin feature/issue-248-d1-forecast-weather
gh pr create --repo hankehly/power-market-analytics --base main --title "feat(dbt): the forecast weather of D-1 in ftr_hour_msm and ftr_day_msm" --body-file scratch/2026-09-30-d1-forecast-weather/pr-body.md
gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label forecasting
```

The body follows `.github/pull_request_template.md`: Summary (`Closes #248`; the spec), Changes, Effect on what exists (the two diffs, 0 moved), Checks, Decisions, Evidence (the acceptance table, the Feast read-through).

- [ ] **Step 3: The review loop** — poll Codex as CLAUDE.md describes; fix or rebut every finding, resolve threads; report the PR ready when a round ends clean with CI green. The researcher merges.

---

### Task 6: Preset `e249` and the run (PR 2, stacked on PR 1)

**Files:**
- Create: `conf/presets/demand/e249.yaml`
- Modify: `tests/test_demand_presets.py` (the registry set at line 207 and a pin of what `e249` adds), `tests/test_demand_strategies.py` (`STRATEGIES`, lines 55–71), `tests/test_features_presets.py` (`REGISTERED_SERVICES`, lines 40–56)
- Create: `docs/research/demand/assets/249-mae-by-month.png`
- Modify: `docs/research/demand/README.md` (the reference-runs paragraph)

- [ ] **Step 1: Worktree** — `git worktree add -b feature/issue-249-d1-forecast-experiment /Users/hankehly/Projects/power-market-analytics/.claude/worktrees/d1-forecast-experiment feature/issue-248-d1-forecast-weather` from the main checkout's git (the worktree command is read-only on the main checkout's working tree), then `uv sync --locked` there.

- [ ] **Step 2: The failing registry tests** — in `tests/test_demand_presets.py` add `"e249"` to the set at line 207 and to the exclusion at line 271, and append:

```python
def test_e249_adds_the_eleven_d1_forecast_columns_to_e245():
    # Experiment #249, 2026-09-30: e245 plus feature candidate #248's eleven columns,
    # three in ftr_hour_msm and eight in ftr_day_msm; nothing dropped.
    baseline = PRESETS["e245"]
    preset = PRESETS["e249"]
    added = tuple(f for f in preset.features if f not in baseline.features)
    assert preset.base == "e245"
    assert set(baseline.features) <= set(preset.features)
    assert added == (
        "ftr_hour_msm:lag_1d_popw_forecast_temperature_c",
        "ftr_hour_msm:lag_1d_popw_forecast_solar_radiation_mjm2",
        "ftr_hour_msm:delta_lag_1d_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_mean_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_min_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_evening_mean_popw_forecast_temperature_c",
        "ftr_day_msm:lag_1d_mean_popw_forecast_solar_radiation_mjm2",
        "ftr_day_msm:lag_1d_mean_popw_forecast_precipitation_mm",
        "ftr_day_msm:delta_lag_1d_mean_popw_forecast_temperature_c",
        "ftr_day_msm:change_1d_2d_mean_popw_temperature_c",
        "ftr_day_msm:mean_3d_popw_temperature_c",
    )
    assert len(preset.features) == len(baseline.features) + 11
    assert categorical_columns(preset) == categorical_columns(baseline)
```

Add `"e249",` after `"e245",` in `tests/test_demand_strategies.py`'s `STRATEGIES` tuple and `"demand__e249",` after `"demand__e245",` in `tests/test_features_presets.py`'s `REGISTERED_SERVICES`. Run `uv run pytest tests/test_demand_presets.py -x -q -p no:cacheprovider`. Expected: FAIL, `KeyError: 'e249'`.

- [ ] **Step 3: The preset** — create `conf/presets/demand/e249.yaml`:

```yaml
description: >-
  e245 plus the forecast weather of D-1, read off the MSM marts' own rows for
  D-1 under the run one day earlier: at each hour D-1's temperature and
  radiation and D's temperature minus D-1's; per day D-1's mean, minimum,
  evening mean, radiation mean and rain mean, D's mean minus D-1's, D-1's minus
  D-2's observed mean, and the mean of the three days. The eleven columns of
  feature candidate #248, 155 features. Experiment #249.
base: e245
add:
  - LAG(MEAN(forecast_temperature_c, weight=population), 1d)
  - LAG(MEAN(forecast_solar_radiation_mjm2, weight=population), 1d)
  - DIFF(MEAN(forecast_temperature_c, weight=population), 1d)
  - LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d)
  - LAG(DAILY_MIN(MEAN(forecast_temperature_c, weight=population)), 1d)
  - LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population), time=18:00-22:00), 1d)
  - LAG(DAILY_MEAN(MEAN(forecast_solar_radiation_mjm2, weight=population)), 1d)
  - LAG(DAILY_MEAN(MEAN(forecast_precipitation_mm, weight=population)), 1d)
  - DIFF(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d)
  - LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d) - LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)
  - (DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)) + LAG(DAILY_MEAN(MEAN(forecast_temperature_c, weight=population)), 1d) + LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)) / 3
```

Run the whole suite: `uv run pytest --cov --cov-report=term-missing -q -p no:cacheprovider`, then `uv run ruff check . && uv run mypy`. Expected: all pass. Commit: `git commit -m "feat(demand): preset e249, the D-1 forecast columns on e245" -m "Refs #249" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`.

- [ ] **Step 4: The run** — in the devcontainer, detached, with the worktree as PYTHONPATH so the new preset file is found: `docker compose … exec -T -d -w /workspace/.claude/worktrees/d1-forecast-experiment -e PYTHONPATH=/workspace/.claude/worktrees/d1-forecast-experiment devcontainer sh -c 'python scripts/demand_backtest.py --strategy e249 --area tokyo > scratch/2026-09-30-d1-forecast-experiment/run-e249.log 2>&1; echo $? > scratch/2026-09-30-d1-forecast-experiment/e249.done'`. Expected after about 40 minutes: `MAE=… MAPE=…`, 730 days, 35,002 predictions, 0 skipped, a run id in the log.

- [ ] **Step 5: Marts and compare** — `DBT_THRIFT_HOST=localhost uv run dbt build --project-dir dbt --profiles-dir dbt --select "+fct_demand_forecast_accuracy +fct_demand_forecast_contribution_summary +fct_demand_forecast_importance"` in the background; then `docker compose … exec -T -w … -e PYTHONPATH=… devcontainer python scripts/compare_demand_runs.py --baseline 8adc4ecc00be443dab382b2b4f1b87b9 --candidate <run> --mae-by-month-png docs/research/demand/assets/249-mae-by-month.png > scratch/2026-09-30-d1-forecast-experiment/compare-e249.md`. Expected: every point matched, the markdown tables, the figure.

- [ ] **Step 6: The write-up** — post the results to #249 as a comment in the shape of #245's (the table, the daily paired comparison with the CI over days, the figure by raw URL on main, the new columns' share of the permutation ΔMAE from `fct_demand_forecast_importance`, and 2025-03-04's daily error), fill the issue body's Execution and Results sections, and add to `docs/research/demand/README.md`'s reference-runs paragraph: "On 2026-09-30 `e249` (the eleven D-1 forecast columns of #248 on `e245`) ran on the same window against `8adc4ecc…`; the numbers and the researcher's decision are in #249." The Decision is the researcher's.

- [ ] **Step 7: PR 2** — commit the figure and the README, push, `gh pr create --base feature/issue-248-d1-forecast-weather --title "feat(demand): experiment e249, the forecast weather of D-1 on e245"`, labels enhancement + forecasting + research, the body with the run in Evidence; the review loop as Task 5's.
