# Weather siblings of the load lag windows, part 3 of 4: the day-type window

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the day-type window's four dates from `ftr_period_actuals`, build `ftr_period_daytype_weather`, the eleventh mart, with the window's mean and 8:4:2:1 weighted mean of the population-weighted observed temperature and radiation at the period's hour plus their four deltas to D's forecast, and add the singular test that pins one MSM vintage per delivery day: PR 3 of the spec's four (sections 5.5, 5.6, decision 10).

**Architecture:** `ftr_period_actuals` already picks the window's dates inside its `candidate_periods` CTE (the newest complete day of D's day type at or before D-2 and its three predecessors); it gains three `lag(date_key, k)` columns there and exposes the four dates untagged, so the window keeps its one definition. The new mart joins those dates to `fct_area_weather_hourly` at the period's hour, four left joins, and to `ftr_hour_msm` for the forecast, computing the means as explicit arithmetic over the four named columns with the load feature's weights; its `available_at` is the greatest of the window row's, the four hours' and the vintage's. A new period mart touches the five hand-kept places.

**Tech Stack:** dbt (Spark SQL, unit tests, a singular test, contracts), the `available_at` macro, `scripts/generate_feature_views.py`, pytest with the local Spark fixture, the devcontainer's Spark session for the proofs, `gh`.

**Spec:** `docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md` (sections 5.5, 5.6, 6, 8, 9; decision 10). Parts 1 and 2: PRs #239 and #240.

## Global Constraints

- Work in a worktree cut from `origin/main` after #240 merges, branch `feature/issue-237-weather-siblings-daytype-window`; compose commands use `docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics …`; the devcontainer sees the worktree at `/workspace/.claude/worktrees/<name>`.
- Host-side dbt: `DBT_THRIFT_HOST=localhost uv run dbt <cmd> --project-dir dbt --profiles-dir dbt`, one dbt process at a time.
- A unit-test input for a relation not yet built, or built without the columns the model reads, is `format: sql` with typed columns. The `ftr_period_actuals` all-columns unit test's `expect` is `format: sql` and must list every column of the model, the four new dates included.
- Means over the window are explicit arithmetic over the four named columns, newest first, the load feature's weights (1:1:1:1 and 8:4:2:1), over the dates whose value is present; never `avg()` or `sum()` over rows.
- Physical names and expressions are the spec's (section 5.6): `mean_daytype_4d_popw_temperature_c`, `ewm_daytype_4d_popw_temperature_c`, `mean_daytype_4d_popw_solar_radiation_mjm2`, `ewm_daytype_4d_popw_solar_radiation_mjm2` and `delta_` + each; the untagged dates are `daytype_4d_date_1` … `daytype_4d_date_4`, newest first.
- The period's hour is `hour_ending = (time_code + 1) div 2`.
- Every model: enforced contract, a `data_type` per column, a uniqueness test on its key; every input feeds `available_at` through `greatest()`.
- Commits: Conventional Commits with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; never the Codex mention in anything that shows in a diff.

## Review Focus

1. The four dates equal the days the load window used: the newest is D minus `newest_daytype_4d_lag_days`, the oldest present is D minus `oldest_daytype_4d_lag_days`, and a window of fewer than four days has nulls after its last date (Task 1's expectation derives the dates from those two ages).
2. A window date whose hour is missing in the fact: dropped from the sibling's mean, the weights renormalised over the dates present, the load feature untouched (Task 2's unit test, row B).
3. A window of two days: the EWA weights 8 and 4 over 12 (Task 2's unit test, row C).
4. A row with no forecast: siblings present, deltas null, and `available_at` the greatest of the window row's and the hours' (Task 2's unit test, row D).
5. A second vintage on a delivery day would double the new mart's rows: the singular test on `ftr_hour_msm` fails the build the day one is loaded (Task 3).

---

### Task 1: The window's four dates on `ftr_period_actuals`

**Files:**
- Modify: `dbt/models/features/ftr_period_actuals.sql` (the `candidate_periods` CTE, the `day_type_windows` CTE, the `final` select)
- Modify: `dbt/models/features/ftr_period_actuals.yml` (four untagged columns; the first unit test's `expect`)

**Interfaces:**
- Produces: `daytype_4d_date_1` … `daytype_4d_date_4` (date, untagged, the same on all 48 periods of a day, null where the window is or beyond its last day), read by Task 2 with `area_code`, `trade_date`, `time_code`, `available_at`.

- [ ] **Step 1: Write the failing expectation**

In `dbt/models/features/ftr_period_actuals.yml`, in the unit test `ftr_period_actuals_lags_means_change_and_day_type_window`, add to the `expect` select, after `cast(t.oldest_age as int) as oldest_daytype_4d_lag_days,`:

```sql
          -- The window's dates: one Saturday a week, so the k-th date is the newest
          -- plus 7 (k - 1) days back, present while the oldest reaches that far.
          date_sub(t.trade_date, t.newest_age) as daytype_4d_date_1,
          case when t.oldest_age >= t.newest_age + 7 then date_sub(t.trade_date, t.newest_age + 7) end as daytype_4d_date_2,
          case when t.oldest_age >= t.newest_age + 14 then date_sub(t.trade_date, t.newest_age + 14) end as daytype_4d_date_3,
          case when t.oldest_age >= t.newest_age + 21 then date_sub(t.trade_date, t.newest_age + 21) end as daytype_4d_date_4,
```

Add the four columns to the contract after `oldest_daytype_4d_lag_days`:

```yaml
      - name: daytype_4d_date_1
        data_type: date
        description: >
          The newest day of the day-type window: the newest complete day of D's day type at or before D-2, the day newest_daytype_4d_lag_days counts back to; the same on all 48 periods of a day; null where the window is. Not a feature: ftr_period_daytype_weather reads the window's dates here, so the window has one definition (the lag-window weather idea, feature candidates #237 and #238).
      - name: daytype_4d_date_2
        data_type: date
        description: >
          The window's second newest day; null when the window holds one day.
      - name: daytype_4d_date_3
        data_type: date
        description: >
          The window's third newest day; null when the window holds fewer than three.
      - name: daytype_4d_date_4
        data_type: date
        description: >
          The window's oldest day when it holds four, the day oldest_daytype_4d_lag_days counts back to; null when it holds fewer.
```

- [ ] **Step 2: Run the unit test to see it fail**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_period_actuals_lags_means_change_and_day_type_window"`
Expected: FAIL, the expectation names `daytype_4d_date_1`, which the model does not produce (or the contract check fails first). RED.

- [ ] **Step 3: Carry the dates through the model**

In `candidate_periods`, after the three `value_k` lags, add:

```sql
    lag(actuals.date_key, 1) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as date_key_1,
    lag(actuals.date_key, 2) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as date_key_2,
    lag(actuals.date_key, 3) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as date_key_3,
```

In `day_type_windows`, after `oldest_daytype_4d_lag_days`, add:

```sql
    -- The window's dates themselves, newest first, for the marts that read the
    -- weather of the same days (ftr_period_daytype_weather); null beyond the
    -- window's last day.
    candidate_periods.date_key as daytype_4d_date_1,
    candidate_periods.date_key_1 as daytype_4d_date_2,
    candidate_periods.date_key_2 as daytype_4d_date_3,
    candidate_periods.date_key_3 as daytype_4d_date_4,
```

In `final`, after `day_type_windows.oldest_daytype_4d_lag_days,`:

```sql
    day_type_windows.daytype_4d_date_1,
    day_type_windows.daytype_4d_date_2,
    day_type_windows.daytype_4d_date_3,
    day_type_windows.daytype_4d_date_4,
```

- [ ] **Step 4: Run every unit test of the model to see them pass**

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select "ftr_period_actuals,test_type:unit"`
Expected: all seven PASS (the dict-format tests ignore the new columns; the sql-format one lists them).

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_period_actuals.sql dbt/models/features/ftr_period_actuals.yml
git commit -m "feat(dbt): ftr_period_actuals exposes the day-type window's four dates" -m "Refs #237, #238" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `ftr_period_daytype_weather`

**Files:**
- Create: `dbt/models/features/ftr_period_daytype_weather.sql`
- Create: `dbt/models/features/ftr_period_daytype_weather.yml`

**Interfaces:**
- Consumes: Task 1's dates; `fct_area_weather_hourly` (`area_key`, `date_key`, `hour_ending`, `popw_temperature_c`, `popw_solar_radiation_mjm2`, `available_at`); `dim_area`; `ftr_hour_msm` (`area_code`, `trade_date`, `hour_ending`, `popw_forecast_temperature_c`, `popw_forecast_solar_radiation_mjm2`, `available_at`).
- Produces: grain `area_code × trade_date × time_code`; the eight tagged doubles of spec 5.6; `available_at`.

- [ ] **Step 1: Write the failing unit test**

Create `dbt/models/features/ftr_period_daytype_weather.yml` with the contract (eight tagged columns; expressions exactly as spec 5.6: `ROLLING_MEAN(MEAN(temperature_c, weight=population), gap=2d, window=4) by day_type`, `EWA(MEAN(temperature_c, weight=population), gap=2d, window=4, halflife=1) by day_type`, the radiation twins, and the deltas `MEAN(forecast_temperature_c, weight=population) - (…)` with the sibling's expression in parentheses), the uniqueness test on `(area_code, trade_date, time_code)`, descriptions carrying the coverage note for radiation and "the same four dates as the load window, the values present", and this unit test:

```yaml
unit_tests:
  - name: ftr_period_daytype_weather_window_means_weights_and_deltas
    description: >
      Four Tokyo rows of the load mart at time code 24, hour 12. Row A (2025-03-04)
      has the four window dates 02-28, 02-27, 02-26, 02-25, whose hour-12 temperatures
      are 12, 14, 16, 18 and radiations 1.0, 0.5, 0.25, 0.25 (newest first): the mean is
      15 and 0.5, the 8:4:2:1 mean (96 + 56 + 32 + 18) / 15 and (8 + 2 + 0.5 + 0.25) / 15;
      the forecast is 5 C and 0.75, so the deltas are 5 - 15, 5 - 202 / 15, 0.75 - 0.5,
      0.75 - 10.75 / 15. Row B (2025-03-05) has the same dates but 02-27 has no hour 12
      in the fact: the means run over the three dates present, (12 + 16 + 18) / 3 and
      (96 + 32 + 18) / 11. Row C (2025-03-06) has two dates, 02-28 and 02-27: 13 and
      (96 + 56) / 12. Row D (2025-03-07) has the four dates and no forecast row: the
      siblings stand, the deltas are null. available_at is the greatest of the load
      row's, the hours' and the vintage's: the vintage's 01:00 on D-1 on A, B and C,
      the load row's on D. The expected doubles are the same divisions in Python.
    model: ftr_period_daytype_weather
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo}
      - input: ref('ftr_period_actuals')
        format: sql
        rows: |
          select 'tokyo' as area_code, date '2025-03-04' as trade_date, 24 as time_code,
            date '2025-02-28' as daytype_4d_date_1, date '2025-02-27' as daytype_4d_date_2,
            date '2025-02-26' as daytype_4d_date_3, date '2025-02-25' as daytype_4d_date_4,
            timestamp '2025-03-01 00:30:00' as available_at
          union all
          select 'tokyo', date '2025-03-05', 24, date '2025-02-28', date '2025-02-27', date '2025-02-26', date '2025-02-25', timestamp '2025-03-01 00:30:00'
          union all
          select 'tokyo', date '2025-03-06', 24, date '2025-02-28', date '2025-02-27', cast(null as date), cast(null as date), timestamp '2025-03-01 00:30:00'
          union all
          select 'tokyo', date '2025-03-07', 24, date '2025-02-28', date '2025-02-27', date '2025-02-26', date '2025-02-25', timestamp '2025-03-01 00:30:00'
      - input: ref('fct_area_weather_hourly')
        format: sql
        rows: |
          select 1 as area_key, date '2025-02-28' as date_key, 12 as hour_ending, cast(12 as double) as popw_temperature_c, cast(1.0 as double) as popw_solar_radiation_mjm2, timestamp '2025-02-28 13:00:00' as available_at
          union all select 1, date '2025-02-27', 12, cast(14 as double), cast(0.5 as double), timestamp '2025-02-27 13:00:00'
          union all select 1, date '2025-02-26', 12, cast(16 as double), cast(0.25 as double), timestamp '2025-02-26 13:00:00'
          union all select 1, date '2025-02-25', 12, cast(18 as double), cast(0.25 as double), timestamp '2025-02-25 13:00:00'
      - input: ref('ftr_hour_msm')
        format: sql
        rows: |
          select 'tokyo' as area_code, date '2025-03-04' as trade_date, 12 as hour_ending, cast(5 as double) as popw_forecast_temperature_c, cast(0.75 as double) as popw_forecast_solar_radiation_mjm2, timestamp '2025-03-03 01:00:00' as available_at
          union all select 'tokyo', date '2025-03-05', 12, cast(5 as double), cast(0.75 as double), timestamp '2025-03-04 01:00:00'
          union all select 'tokyo', date '2025-03-06', 12, cast(5 as double), cast(0.75 as double), timestamp '2025-03-05 01:00:00'
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-04, time_code: 24, mean_daytype_4d_popw_temperature_c: 15.0, ewm_daytype_4d_popw_temperature_c: 13.466666666666667, mean_daytype_4d_popw_solar_radiation_mjm2: 0.5, ewm_daytype_4d_popw_solar_radiation_mjm2: 0.7166666666666667, delta_mean_daytype_4d_popw_temperature_c: -10.0, delta_ewm_daytype_4d_popw_temperature_c: -8.466666666666667, delta_mean_daytype_4d_popw_solar_radiation_mjm2: 0.25, delta_ewm_daytype_4d_popw_solar_radiation_mjm2: 0.033333333333333326, available_at: "2025-03-03 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-06, time_code: 24, mean_daytype_4d_popw_temperature_c: 13.0, ewm_daytype_4d_popw_temperature_c: 12.666666666666666, mean_daytype_4d_popw_solar_radiation_mjm2: 0.75, ewm_daytype_4d_popw_solar_radiation_mjm2: 0.8333333333333334, delta_mean_daytype_4d_popw_temperature_c: -8.0, delta_ewm_daytype_4d_popw_temperature_c: -7.666666666666666, delta_mean_daytype_4d_popw_solar_radiation_mjm2: 0.0, delta_ewm_daytype_4d_popw_solar_radiation_mjm2: -0.08333333333333337, available_at: "2025-03-05 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-07, time_code: 24, mean_daytype_4d_popw_temperature_c: 15.0, ewm_daytype_4d_popw_temperature_c: 13.466666666666667, mean_daytype_4d_popw_solar_radiation_mjm2: 0.5, ewm_daytype_4d_popw_solar_radiation_mjm2: 0.7166666666666667, delta_mean_daytype_4d_popw_temperature_c: null, delta_ewm_daytype_4d_popw_temperature_c: null, delta_mean_daytype_4d_popw_solar_radiation_mjm2: null, delta_ewm_daytype_4d_popw_solar_radiation_mjm2: null, available_at: "2025-03-01 00:30:00"}
```

Row B (2025-03-05) is written into the expectation by the executor after removing the 02-27 hour for that row's dates: give row B its own dates 03-01 … 02-26 shifted, or simpler, give it the same dates and delete the 02-27 fact row while adding a 02-27 row only for hour 11; then row A and D lose 02-27 too. The cleanest: row B uses dates 02-28, 02-24, 02-26, 02-25 where 02-24 has no fact row; its expected means are (12 + 16 + 18) / 3 = 15.333333333333334, (96 + 32 + 18) / 11 = 13.272727272727273, radiation (1.0 + 0.25 + 0.25) / 3 = 0.5, (8 + 0.5 + 0.25) / 11 = 0.7954545454545454, deltas 5 − 15.333333333333334 = −10.333333333333334, 5 − 13.272727272727273 = −8.272727272727273, 0.75 − 0.5 = 0.25, 0.75 − 0.7954545454545454 = −0.04545454545454541, available_at 2025-03-04 01:00:00. Compute every expected double in Python with the same operations before writing it; a last-bit disagreement is a wrong operation order in the SQL, not a rounding to accept.

- [ ] **Step 2: Run it to see it fail** — Expected: a parsing error, no model file.

- [ ] **Step 3: Write the model**

```sql
with
  -- The load window's rows: the four dates the day-type means read, at every period.
  windows as (
  select area_code, trade_date, time_code,
    cast(div(time_code + 1, 2) as int) as hour_ending,
    daytype_4d_date_1, daytype_4d_date_2, daytype_4d_date_3, daytype_4d_date_4,
    available_at
  from {{ ref('ftr_period_actuals') }}
  where daytype_4d_date_1 is not null
  ),

  hours as (
  select areas.area_code, fact.date_key, fact.hour_ending,
    fact.popw_temperature_c, fact.popw_solar_radiation_mjm2, fact.available_at
  from {{ ref('fct_area_weather_hourly') }} as fact
    inner join {{ ref('dim_area') }} as areas on areas.area_key = fact.area_key
  ),

  forecast as (
  select area_code, trade_date, hour_ending,
    popw_forecast_temperature_c, popw_forecast_solar_radiation_mjm2, available_at
  from {{ ref('ftr_hour_msm') }}
  ),

  joined as (
  select
    w.area_code, w.trade_date, w.time_code,
    h1.popw_temperature_c as t1, h2.popw_temperature_c as t2, h3.popw_temperature_c as t3, h4.popw_temperature_c as t4,
    h1.popw_solar_radiation_mjm2 as r1, h2.popw_solar_radiation_mjm2 as r2, h3.popw_solar_radiation_mjm2 as r3, h4.popw_solar_radiation_mjm2 as r4,
    f.popw_forecast_temperature_c, f.popw_forecast_solar_radiation_mjm2,
    {{ available_at(['w.available_at', 'h1.available_at', 'h2.available_at', 'h3.available_at', 'h4.available_at', 'f.available_at']) }} as available_at
  from windows as w
    left join hours as h1 on h1.area_code = w.area_code and h1.date_key = w.daytype_4d_date_1 and h1.hour_ending = w.hour_ending
    left join hours as h2 on h2.area_code = w.area_code and h2.date_key = w.daytype_4d_date_2 and h2.hour_ending = w.hour_ending
    left join hours as h3 on h3.area_code = w.area_code and h3.date_key = w.daytype_4d_date_3 and h3.hour_ending = w.hour_ending
    left join hours as h4 on h4.area_code = w.area_code and h4.date_key = w.daytype_4d_date_4 and h4.hour_ending = w.hour_ending
    left join forecast as f on f.area_code = w.area_code and f.trade_date = w.trade_date and f.hour_ending = w.hour_ending
  )
```

and a `final` that, for each element `x` in (`t`, `r`), computes the plain mean `(coalesce(x1,0)+coalesce(x2,0)+coalesce(x3,0)+coalesce(x4,0)) / nullif(cast(x1 is not null as int)+…, 0)` and the weighted mean `(coalesce(8*x1,0)+coalesce(4*x2,0)+coalesce(2*x3,0)+coalesce(x4,0)) / nullif(8*cast(x1 is not null as int)+4*…+2*…+…, 0)`, the deltas as `popw_forecast_… - <mean>`, written out once per element with a Jinja list `[('t', 'temperature_c', 'popw_forecast_temperature_c'), ('r', 'solar_radiation_mjm2', 'popw_forecast_solar_radiation_mjm2')]` so the arithmetic is one text. Spark's `greatest` skips nulls, so a missing hour or forecast does not null the row's `available_at`.

- [ ] **Step 4: Run the unit test to see it pass** — Expected: `PASS 1`.

- [ ] **Step 5: Commit** — `feat(dbt): ftr_period_daytype_weather, the day-type window's observed weather and its deltas` with `Refs #237, #238` and the trailer.

---

### Task 3: The one-vintage singular test

**Files:**
- Create: `dbt/dbt_tests/assert_ftr_hour_msm_one_vintage_per_delivery_day.sql`

- [ ] **Step 1: Write the test and see it pass on the warehouse**

```sql
-- A period mart joins ftr_hour_msm on area, day and hour (ftr_period_daytype_weather,
-- ftr_period_similar_day), so a second vintage on a delivery day would double its rows.
-- Only the 12 UTC D-2 run is loaded (spec 2026-09-29-lag-window-weather-siblings,
-- decision 10); this fails the build the day another is.
select area_code, trade_date, hour_ending, count(*) as n_vintages
from {{ ref('ftr_hour_msm') }}
group by area_code, trade_date, hour_ending
having count(*) > 1
```

Run: `DBT_THRIFT_HOST=localhost uv run dbt test --project-dir dbt --profiles-dir dbt --select assert_ftr_hour_msm_one_vintage_per_delivery_day`. Expected: PASS (0 rows). Prove it bites: in a scratch session, `select count(*) from (select area_code, trade_date, hour_ending from pma_features.ftr_hour_msm union all select area_code, trade_date, hour_ending from pma_features.ftr_hour_msm limit 1) group by 1,2,3 having count(*) > 1` returns one row — the same SQL shape over a doubled input.

- [ ] **Step 2: Commit** — `test(dbt): one MSM vintage per delivery day and hour` with the trailer.

---

### Task 4: The generated files, the five hand-kept places, the fixture and the suite

**Files:**
- Regenerate the three files; modify `tests/test_feature_views.py` (list + `assert by_name["ftr_period_daytype_weather"].entities == [*day, TIME_CODE.name]`), `tests/test_feature_store.py`, `tests/test_feature_value_fact.py`, `.github/ISSUE_TEMPLATE/feature-candidate.md`, `tests/conftest.py` ("ten" → "eleven"; a synthetic `ftr_period_daytype_weather` built in the same loop that computes `window_days` for the actuals mart: at each (day, tc) with a window, the sibling means over `synthetic_temperature(d, hour_of(tc))` for the window days not in `TEMPERATURE_MISSING_HOURS`, `hour_of(tc) = (tc + 1) // 2`, with `mean_of_present` / `ewm_of_present`; radiation None; deltas `popw_forecast(day, hour, synthetic_forecast_temperature(day, hour), SECOND_STATION_FORECAST_OFFSET_C) - sibling` where the day has a forecast (`day != FORECAST_MISSING_DAY`), else None; `available_at` the load row's), and its `write_table` with the eight doubles.

- [ ] Regenerate, watch the mart-list tests fail, make the edits, run the suite and the static checks, commit as `feat(forecasting): ftr_period_daytype_weather in the views, the catalogue, the tests and the fixture`.

---

### Task 5: The warehouse proof

- [ ] Copy `pma_features.ftr_period_actuals` to `pma_scratch.wsib3_ftr_period_actuals_before`; build `--select "ftr_period_actuals+ ftr_period_daytype_weather+ dim_feature" assert_ftr_hour_msm_one_vintage_per_delivery_day`; diff every one of the 36 tagged columns and `available_at` null-safe on `(area_code, trade_date, time_code)`, expecting 0 unmatched and 0 moved; acceptance on Tokyo 2025-03-04 period 24: dates 2025-02-28, 02-27, 02-26, 02-25 (the warm week), `delta_ewm_daytype_4d_popw_temperature_c` well below −5 °C; a Feast read-through of the eight columns; drop the copy.

---

### Task 6: Docs and the pull request

- [ ] CLAUDE.md: "Today's ten" → eleven and line 71's count; the `ftr_period_actuals` entry gains "since 2026-09-29 the window's four dates as untagged columns `daytype_4d_date_1` … `_4`"; a `ftr_period_daytype_weather` entry after `ftr_period_actuals`'; the singular test in the `ftr_hour_msm` entry. `docs/superpowers/README.md`: `· [plan 3](…)`. Commit `docs: ftr_period_daytype_weather and the window dates in CLAUDE.md; the part-3 plan`. Push, `gh pr create` with a body in the template's shape (Summary: PR 3 of 4, `Refs #237, #238`), labels `enhancement` + `forecasting`, assignee, the Codex loop, merge when clean (the researcher's standing instruction of 2026-09-29).
