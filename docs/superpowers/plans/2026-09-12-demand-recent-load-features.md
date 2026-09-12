# Recent Load Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put thirteen recent-demand features into the feature marts, add the demand preset that reads them, run it against a fresh matched baseline, and record the result as `demand/R-006`.

**Architecture:** `ftr_period_actuals` is rebuilt as one union of the actuals shifted by 2, 3, 7, 9, 14, 21 and 28 days, grouped per period, with the matching-day-type window computed by window functions over complete days; a new day-grain mart `ftr_day_actuals` carries D−2's mean, max and range. `just feature-views` regenerates the Feast views and `fct_feature_value`; the preset `lightgbm_msm_popw_daytype_simday_lags` names the thirteen columns; two devcontainer backtests and the compare script give the numbers.

**Tech Stack:** dbt (Spark SQL on the thriftserver, dbt unit tests with `format: sql` fixtures), Feast (generated views), pandas/pytest (the synthetic fixture), LightGBM through `PresetLightGbmStrategy`, MLflow, `scripts/compare_demand_runs.py`.

**Spec:** `docs/superpowers/specs/2026-09-12-demand-recent-load-features-design.md`

## Global Constraints

- Branch `feature/demand-recent-load-features` already exists with the spec committed (`91f7e33`). Work in the main checkout; no worktree.
- Every dbt model has `contract: enforced: true` with a `data_type` per column and a uniqueness test on its key (`dbt_utils.unique_combination_of_columns`). Generic test args go under `arguments:`.
- Spark SQL: integer literals only in the weight arithmetic (a decimal literal makes the division decimal). `bigint / int` is double.
- `power_market_analytics/features/views.py` and `dbt/models/curated/fct_feature_value.sql` are generated: never edit them; run `just feature-views` (host-side; needs the thriftserver only for nothing — `dbt parse` opens no connection).
- `just test` gates coverage at 100 % over the whole suite, so a partial run must use `uv run pytest <files> -q` (no `--cov`). Run the full `just test` once in Task 5 and once before the PR.
- Long-running commands (`just dbt build`, the two backtests) run as background Bash tasks in the main session, never inside a subagent.
- A PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` file written: re-read a file before editing it again. Adding an import before its use gets the import stripped (F401): add the use first, or the import and the use in one edit.
- Anything that creates a SparkSession runs in the devcontainer: `just python scripts/…`, `just dbt …`. Host-side dbt: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt …`.
- Commits: Conventional Commits `type(scope): description`, lowercase imperative, ending with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Scope `dbt` for mart-only commits, `demand` for preset/experiment commits, `docs` for docs.
- Docstrings NumPy style. Writing style: plain, short sentences, one idea each.
- Never write the Codex mention (the bot handle followed by "review") in any file, commit or PR body.
- Weights of both exponentially weighted means: 8, 4, 2, 1 from the newest input, tied to the input's position, renormalised over the inputs present.

---

### Task 1: `ftr_day_actuals` — D−2's mean, max and range

**Files:**
- Create: `dbt/models/features/ftr_day_actuals.yml`
- Create: `dbt/models/features/ftr_day_actuals.sql`

**Interfaces:**
- Consumes: `fct_area_demand_generation_actual` (`date_key`, `time_code`, `area_key`, `demand_kwh` bigint nullable, `available_at`), `dim_area` (`area_key`, `area_code`), macro `available_at(columns)`.
- Produces: table `pma_features.ftr_day_actuals` with columns `area_code string`, `trade_date date`, `lag_2d_mean_demand_kwh double`, `lag_2d_max_demand_kwh bigint`, `lag_2d_range_demand_kwh bigint`, `available_at timestamp`; the Feast view `ftr_day_actuals` (Task 3) and the preset references `ftr_day_actuals:lag_2d_mean_demand_kwh`, `ftr_day_actuals:lag_2d_max_demand_kwh`, `ftr_day_actuals:lag_2d_range_demand_kwh` (Task 4).

- [ ] **Step 1: Write the YAML with the contract, tests and the unit test**

`dbt/models/features/ftr_day_actuals.yml`:

```yaml
models:
  - name: ftr_day_actuals
    config:
      contract:
        enforced: true
    description: >
      The previous complete day's demand summaries for each delivery day: D-2's mean, maximum and maximum-minus-minimum over its 48 periods of fct_area_demand_generation_actual (research demand/R-006). Grain: area_code x trade_date. A day with a hole (a null period) is not complete and gives no row. available_at is the newest of D-2's rows.
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
          The delivery day D (the summarised day + 2).
        data_tests:
          - not_null
      - name: lag_2d_mean_demand_kwh
        data_type: double
        description: >
          Mean of D-2's 48 half-hourly demand values, kWh per period.
        config:
          meta:
            feature: true
            categorical: false
        data_tests:
          - not_null
      - name: lag_2d_max_demand_kwh
        data_type: bigint
        description: >
          D-2's maximum half-hourly demand, kWh per period.
        config:
          meta:
            feature: true
            categorical: false
        data_tests:
          - not_null
      - name: lag_2d_range_demand_kwh
        data_type: bigint
        description: >
          D-2's maximum minus minimum half-hourly demand, kWh per period.
        config:
          meta:
            feature: true
            categorical: false
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST: the newest available_at among D-2's 48 rows.
        data_tests:
          - not_null

unit_tests:
  - name: ftr_day_actuals_complete_day_only
    description: >
      2025-03-01 is complete with demand 1000 + time_code, its period 48
      public five minutes after the rest; 2025-03-02 has a null period 48 and
      gives no row. The one row is D = 2025-03-03 with mean 1024.5, max 1048,
      range 47 and the newest availability.
    model: ftr_day_actuals
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo}
      - input: ref('fct_area_demand_generation_actual')
        format: sql
        rows: |
          select
            d as date_key,
            p as time_code,
            1 as area_key,
            case when d = date '2025-03-02' and p = 48 then null else 1000 + p end as demand_kwh,
            case
              when d = date '2025-03-01' and p = 48 then timestamp '2025-03-02 00:10:00'
              else timestampadd(minute, 5, timestampadd(day, 1, cast(d as timestamp)))
            end as available_at
          from
            (select explode(array(date '2025-03-01', date '2025-03-02')) as d)
            cross join (select explode(sequence(1, 48)) as p)
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-03, lag_2d_mean_demand_kwh: 1024.5, lag_2d_max_demand_kwh: 1048, lag_2d_range_demand_kwh: 47, available_at: "2025-03-02 00:10:00"}
```

- [ ] **Step 2: Run the build to see it fail on the missing model**

Run: `just dbt build --select ftr_day_actuals`
Expected: a parse/compilation error naming `ftr_day_actuals` (no SQL file yet).

- [ ] **Step 3: Write the SQL**

`dbt/models/features/ftr_day_actuals.sql`:

```sql
-- The previous complete day's demand summaries: D-2's mean, maximum and
-- range over its 48 periods. A day with a hole (a null period) is not
-- complete and gives no row. The sum of 48 integers is exact in a double,
-- so avg() gives the same value whatever order Spark reads the rows in.
with
  complete_days as (
  select
    areas.area_code,
    actuals.date_key,
    avg(actuals.demand_kwh) as mean_demand_kwh,
    max(actuals.demand_kwh) as max_demand_kwh,
    min(actuals.demand_kwh) as min_demand_kwh,
    max(actuals.available_at) as available_at
  from
    {{ ref('fct_area_demand_generation_actual') }} as actuals
    inner join {{ ref('dim_area') }} as areas
      on areas.area_key = actuals.area_key
  where
    actuals.demand_kwh is not null
  group by
    areas.area_code, actuals.date_key
  having
    count(*) = 48
  ),

  final as (
  select
    area_code,
    date_add(date_key, 2) as trade_date,
    mean_demand_kwh as lag_2d_mean_demand_kwh,
    max_demand_kwh as lag_2d_max_demand_kwh,
    max_demand_kwh - min_demand_kwh as lag_2d_range_demand_kwh,
    {{ available_at(['available_at']) }} as available_at
  from
    complete_days
  )

select * from final
```

- [ ] **Step 4: Build and test**

Run: `just dbt build --select ftr_day_actuals`
Expected: the unit test, the model and its data tests all PASS. If the unit test fails on the `format: sql` fixture, the error names the SQL; fix the fixture, not the model. If `avg` comes back `decimal`, the contract fails naming the type: the fact's `demand_kwh` is then not `bigint` — check `dbt/models/curated/fct_area_demand_generation_actual.yml` and wrap as `cast(avg(actuals.demand_kwh) as double)`.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_day_actuals.sql dbt/models/features/ftr_day_actuals.yml
git commit -m "feat(dbt): ftr_day_actuals, the D-2 mean, max and range of the area demand

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `ftr_period_actuals` — the seven lags, the weekly means, the change and the day-type window

**Files:**
- Modify: `dbt/models/features/ftr_period_actuals.sql` (replace the whole file)
- Modify: `dbt/models/features/ftr_period_actuals.yml` (replace the whole file)

**Interfaces:**
- Consumes: the same fact and dim as Task 1, plus `ftr_day_calendar` (`area_code`, `trade_date`, `day_type` int: 0 weekday, 1 weekend, 2 holiday).
- Produces: `pma_features.ftr_period_actuals` with the keys `area_code`, `trade_date`, `time_code` and the columns `lag_2d_demand_kwh`, `lag_3d_demand_kwh`, `lag_7d_demand_kwh`, `lag_9d_demand_kwh`, `lag_14d_demand_kwh`, `lag_21d_demand_kwh`, `lag_28d_demand_kwh` (bigint, nullable), `mean_weekly_lags_demand_kwh`, `ewm_weekly_lags_demand_kwh` (double), `change_2d_9d_demand_kwh` (bigint), `mean_daytype_4d_demand_kwh`, `ewm_daytype_4d_demand_kwh` (double), `available_at`. The preset references in Task 4 use these names.

- [ ] **Step 1: Replace the YAML**

`dbt/models/features/ftr_period_actuals.yml`:

```yaml
models:
  - name: ftr_period_actuals
    config:
      contract:
        enforced: true
    description: >
      The area's own recent demand for each delivery period, from fct_area_demand_generation_actual: the demand 2, 3, 7, 9, 14, 21 and 28 days before, a plain and an exponentially weighted mean over the four weekly lags, the D-2 minus D-9 change, and a plain and an exponentially weighted mean over the same period of the last four complete days of D's day type (the lag_7d_demand_kwh of research demand/R-001 to R-005; the rest research demand/R-006). Grain: area_code x trade_date x time_code. A row exists wherever at least one lag exists; a column is null where its input is absent (a TSO hole, or a day before the history starts). Both exponentially weighted means use the weights 8, 4, 2, 1 from the newest input back, tied to the input's position and renormalised over the inputs present. A complete day has all 48 periods non-null. available_at is the greatest over the rows the row used, so the whole row is usable only once its newest input is public.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - area_code
              - trade_date
              - time_code
      - dbt_utils.expression_is_true:
          arguments:
            expression: >
              coalesce(lag_2d_demand_kwh, lag_3d_demand_kwh, lag_7d_demand_kwh, lag_9d_demand_kwh,
              lag_14d_demand_kwh, lag_21d_demand_kwh, lag_28d_demand_kwh) is not null
      - dbt_utils.expression_is_true:
          arguments:
            expression: >
              (change_2d_9d_demand_kwh is null) = (lag_2d_demand_kwh is null or lag_9d_demand_kwh is null)
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
          The delivery day D.
        data_tests:
          - not_null
      - name: time_code
        data_type: int
        description: >
          JEPX time code 1-48 of the delivery period.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 48
      - name: lag_2d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-2, kWh. Public about 00:05 on D-1.
        config:
          meta:
            feature: true
            categorical: false
      - name: lag_3d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-3, kWh.
        config:
          meta:
            feature: true
            categorical: false
      - name: lag_7d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-7, kWh.
        config:
          meta:
            feature: true
            categorical: false
      - name: lag_9d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-9, kWh: the older side of change_2d_9d_demand_kwh.
        config:
          meta:
            feature: true
            categorical: false
      - name: lag_14d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-14, kWh.
        config:
          meta:
            feature: true
            categorical: false
      - name: lag_21d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-21, kWh.
        config:
          meta:
            feature: true
            categorical: false
      - name: lag_28d_demand_kwh
        data_type: bigint
        description: >
          Area demand over the same period on D-28, kWh.
        config:
          meta:
            feature: true
            categorical: false
      - name: mean_weekly_lags_demand_kwh
        data_type: double
        description: >
          Mean of the D-7, D-14, D-21 and D-28 lags present, kWh; null when none is.
        config:
          meta:
            feature: true
            categorical: false
      - name: ewm_weekly_lags_demand_kwh
        data_type: double
        description: >
          Exponentially weighted mean of the D-7, D-14, D-21 and D-28 lags with weights 8, 4, 2, 1, renormalised over the lags present, kWh; null when none is.
        config:
          meta:
            feature: true
            categorical: false
      - name: change_2d_9d_demand_kwh
        data_type: bigint
        description: >
          The D-2 lag minus the D-9 lag, kWh: the week-on-week change of the newest complete day. Null when either is absent.
        config:
          meta:
            feature: true
            categorical: false
      - name: mean_daytype_4d_demand_kwh
        data_type: double
        description: >
          Mean over the same period of the last four complete days of D's day type (ftr_day_calendar.day_type) at or before D-2, kWh; fewer days at the start of the history; null when there is none.
        config:
          meta:
            feature: true
            categorical: false
      - name: ewm_daytype_4d_demand_kwh
        data_type: double
        description: >
          The same four days with weights 8, 4, 2, 1 from the newest back, renormalised over the days present, kWh.
        config:
          meta:
            feature: true
            categorical: false
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST: the greatest available_at over the lag rows and the day-type window's days the row used.
        data_tests:
          - not_null

unit_tests:
  - name: ftr_period_actuals_lags_means_change_and_day_type_window
    description: >
      Five complete Saturdays (2025-01-25 = 40, 02-01 = 100, 02-08 = 220,
      02-15 = 280, 02-22 = 340 kWh in every period) and one all-null Saturday
      (03-01). Every actual feeds the seven delivery days it is a lag of. The
      weekly means take the D-7, D-14, D-21 and D-28 lags present with the
      weights 8, 4, 2, 1 of their positions (03-08 has no D-7: 2140 / 7). The
      change is D-2 minus D-9, null when either is absent (03-03: 03-01 is a
      hole, so its row has a null lag_2d, not no row). A Saturday D takes the
      last four complete Saturdays at or before D-2: 03-01's window ends at
      02-22 and leaves 01-25 out; 03-08's skips the incomplete 03-01. Mondays
      and Tuesdays have no complete weekday, so their window is null. The
      02-01 file is public only 03-10, so every row that used it, through a
      lag or the window alone (03-08, 03-15, 03-22), carries that instant.
    model: ftr_period_actuals
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo}
      - input: ref('ftr_day_calendar')
        format: sql
        rows: |
          select
            'tokyo' as area_code,
            d as trade_date,
            case when dayofweek(d) in (1, 7) then 1 else 0 end as day_type
          from
            (select explode(sequence(date '2025-01-20', date '2025-03-31')) as d)
      - input: ref('fct_area_demand_generation_actual')
        format: sql
        rows: |
          select
            d as date_key,
            p as time_code,
            1 as area_key,
            case d
              when date '2025-01-25' then 40
              when date '2025-02-01' then 100
              when date '2025-02-08' then 220
              when date '2025-02-15' then 280
              when date '2025-02-22' then 340
            end as demand_kwh,
            case
              when d = date '2025-02-01' then timestamp '2025-03-10 00:05:00'
              else timestampadd(minute, 5, timestampadd(day, 1, cast(d as timestamp)))
            end as available_at
          from
            (select explode(array(
              date '2025-01-25', date '2025-02-01', date '2025-02-08',
              date '2025-02-15', date '2025-02-22', date '2025-03-01')) as d)
            cross join (select explode(sequence(1, 48)) as p)
    expect:
      format: sql
      rows: |
        select
          'tokyo' as area_code,
          t.trade_date,
          p.time_code,
          cast(t.lag_2d as bigint) as lag_2d_demand_kwh,
          cast(t.lag_3d as bigint) as lag_3d_demand_kwh,
          cast(t.lag_7d as bigint) as lag_7d_demand_kwh,
          cast(t.lag_9d as bigint) as lag_9d_demand_kwh,
          cast(t.lag_14d as bigint) as lag_14d_demand_kwh,
          cast(t.lag_21d as bigint) as lag_21d_demand_kwh,
          cast(t.lag_28d as bigint) as lag_28d_demand_kwh,
          cast(t.mean_weekly as double) as mean_weekly_lags_demand_kwh,
          cast(t.ewm_weekly as double) as ewm_weekly_lags_demand_kwh,
          cast(t.change as bigint) as change_2d_9d_demand_kwh,
          cast(t.mean_daytype as double) as mean_daytype_4d_demand_kwh,
          cast(t.ewm_daytype as double) as ewm_daytype_4d_demand_kwh,
          t.available_at
        from (values
          (date '2025-01-27',   40, null, null, null, null, null, null, null, null, null, null, null, timestamp '2025-01-26 00:05:00'),
          (date '2025-01-28', null,   40, null, null, null, null, null, null, null, null, null, null, timestamp '2025-01-26 00:05:00'),
          (date '2025-02-01', null, null,   40, null, null, null, null,   40,   40, null,   40,   40, timestamp '2025-01-26 00:05:00'),
          (date '2025-02-03',  100, null, null,   40, null, null, null, null, null,   60, null, null, timestamp '2025-03-10 00:05:00'),
          (date '2025-02-04', null,  100, null, null, null, null, null, null, null, null, null, null, timestamp '2025-03-10 00:05:00'),
          (date '2025-02-08', null, null,  100, null,   40, null, null,   70,   80, null,   70,   80, timestamp '2025-03-10 00:05:00'),
          (date '2025-02-10',  220, null, null,  100, null, null, null, null, null,  120, null, null, timestamp '2025-03-10 00:05:00'),
          (date '2025-02-11', null,  220, null, null, null, null, null, null, null, null, null, null, timestamp '2025-02-09 00:05:00'),
          (date '2025-02-15', null, null,  220, null,  100,   40, null,  120,  160, null,  120,  160, timestamp '2025-03-10 00:05:00'),
          (date '2025-02-17',  280, null, null,  220, null, null, null, null, null,   60, null, null, timestamp '2025-02-16 00:05:00'),
          (date '2025-02-18', null,  280, null, null, null, null, null, null, null, null, null, null, timestamp '2025-02-16 00:05:00'),
          (date '2025-02-22', null, null,  280, null,  220,  100,   40,  160,  224, null,  160,  224, timestamp '2025-03-10 00:05:00'),
          (date '2025-02-24',  340, null, null,  280, null, null, null, null, null,   60, null, null, timestamp '2025-02-23 00:05:00'),
          (date '2025-02-25', null,  340, null, null, null, null, null, null, null, null, null, null, timestamp '2025-02-23 00:05:00'),
          (date '2025-03-01', null, null,  340, null,  280,  220,  100,  235,  292, null,  235,  292, timestamp '2025-03-10 00:05:00'),
          (date '2025-03-03', null, null, null,  340, null, null, null, null, null, null, null, null, timestamp '2025-02-23 00:05:00'),
          (date '2025-03-08', null, null, null, null,  340,  280,  220,  280, 2140 / 7, null, 235, 292, timestamp '2025-03-10 00:05:00'),
          (date '2025-03-15', null, null, null, null, null,  340,  280,  310,  320, null,  235,  292, timestamp '2025-03-10 00:05:00'),
          (date '2025-03-22', null, null, null, null, null, null,  340,  340,  340, null,  235,  292, timestamp '2025-03-10 00:05:00')
        ) as t(trade_date, lag_2d, lag_3d, lag_7d, lag_9d, lag_14d, lag_21d, lag_28d,
               mean_weekly, ewm_weekly, change, mean_daytype, ewm_daytype, available_at)
        cross join (select explode(sequence(1, 48)) as time_code) as p
```

How the expected rows were derived (keep for the reviewer, not for the file): the lag of k days of D is the value of D−k when that day is in the list; the weekly mean is the plain mean of the D−7/14/21/28 lags present and the ewm is (8·y₇ + 4·y₁₄ + 2·y₂₁ + 1·y₂₈) over the weights of the lags present (02-08: (800 + 160) / 12 = 80; 02-15: 2240 / 14 = 160; 02-22: 3360 / 15 = 224; 03-01: 4380 / 15 = 292; 03-08: (4·340 + 2·280 + 220) / 7 = 2140 / 7; 03-15: (2·340 + 280) / 3 = 320); the day-type window of a Saturday D is the last four complete Saturdays at or before D−2 (02-08: 02-01, 01-25 → 70 and 80; 03-01 and later: 02-22, 02-15, 02-08, 02-01 → 235 and 292); `available_at` is the greatest over the days used, 02-01's being 03-10.

- [ ] **Step 2: Run the build to see the unit test fail against the old SQL**

Run: `just dbt build --select ftr_period_actuals`
Expected: the unit test FAILS (the old model has one lag column and no calendar input; dbt reports the contract or the column mismatch).

- [ ] **Step 3: Replace the SQL**

`dbt/models/features/ftr_period_actuals.sql`:

```sql
-- The area's own recent demand for every delivery period: the lags of 2, 3,
-- 7, 9, 14, 21 and 28 days, two means over the weekly lags, the two-day-old
-- weekly change and two means over the last four complete days of D's day
-- type. The shifted actuals, grouped per period, are the row spine: a row
-- exists wherever any lag exists and a column is null where its input is
-- absent. available_at is the greatest over the rows a row used.
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
    -- A TSO hole has no value: the lag it feeds is null, not a row.
    actuals.demand_kwh is not null
  ),

  lags as (
  select explode(array(2, 3, 7, 9, 14, 21, 28)) as lag_days
  ),

  -- Every actual shifted to each delivery day it is a lag of.
  shifted as (
  select
    actuals.area_code,
    date_add(actuals.date_key, lags.lag_days) as trade_date,
    actuals.time_code,
    lags.lag_days,
    actuals.demand_kwh,
    actuals.available_at
  from
    actuals
    cross join lags
  ),

  by_period as (
  select
    area_code,
    trade_date,
    time_code,
    max(case when lag_days = 2 then demand_kwh end) as lag_2d_demand_kwh,
    max(case when lag_days = 3 then demand_kwh end) as lag_3d_demand_kwh,
    max(case when lag_days = 7 then demand_kwh end) as lag_7d_demand_kwh,
    max(case when lag_days = 9 then demand_kwh end) as lag_9d_demand_kwh,
    max(case when lag_days = 14 then demand_kwh end) as lag_14d_demand_kwh,
    max(case when lag_days = 21 then demand_kwh end) as lag_21d_demand_kwh,
    max(case when lag_days = 28 then demand_kwh end) as lag_28d_demand_kwh,
    max(available_at) as available_at
  from
    shifted
  group by
    area_code, trade_date, time_code
  ),

  -- A complete day has all 48 periods; it is public when its newest row is.
  complete_days as (
  select
    area_code,
    date_key,
    max(available_at) as available_at
  from
    actuals
  group by
    area_code, date_key
  having
    count(*) = 48
  ),

  -- The one definition of the day type (research demand/R-003).
  day_types as (
  select area_code, trade_date, day_type
  from {{ ref('ftr_day_calendar') }}
  ),

  -- Every period of every complete day with the same period of the three
  -- previous complete days of the same day type: the last four such days
  -- ending at the candidate, and the newest availability among them.
  candidate_periods as (
  select
    actuals.area_code,
    actuals.date_key,
    day_types.day_type,
    actuals.time_code,
    actuals.demand_kwh as value_0,
    lag(actuals.demand_kwh, 1) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as value_1,
    lag(actuals.demand_kwh, 2) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as value_2,
    lag(actuals.demand_kwh, 3) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
    ) as value_3,
    max(complete_days.available_at) over (
      partition by actuals.area_code, day_types.day_type, actuals.time_code
      order by actuals.date_key
      rows between 3 preceding and current row
    ) as available_at
  from
    actuals
    inner join complete_days
      on complete_days.area_code = actuals.area_code
      and complete_days.date_key = actuals.date_key
    inner join day_types
      on day_types.area_code = actuals.area_code
      and day_types.trade_date = actuals.date_key
  ),

  -- The delivery days the mart has, with their day type, and every complete
  -- day at the first delivery day it can serve (two days later): one spine,
  -- so a window finds the newest candidate at or before D-2.
  spine as (
  select
    targets.area_code,
    day_types.day_type,
    targets.trade_date as at_date,
    cast(null as date) as candidate_date,
    1 as is_target
  from
    (select distinct area_code, trade_date from by_period) as targets
    inner join day_types
      on day_types.area_code = targets.area_code
      and day_types.trade_date = targets.trade_date
  union all
  select
    complete_days.area_code,
    day_types.day_type,
    date_add(complete_days.date_key, 2) as at_date,
    complete_days.date_key as candidate_date,
    0 as is_target
  from
    complete_days
    inner join day_types
      on day_types.area_code = complete_days.area_code
      and day_types.trade_date = complete_days.date_key
  ),

  lookup as (
  select
    area_code,
    at_date as trade_date,
    day_type,
    candidate_date
  from (
    select
      area_code,
      day_type,
      at_date,
      is_target,
      -- A candidate sorts before a target of the same date, so D-2 counts.
      last_value(candidate_date, true) over (
        partition by area_code, day_type
        order by at_date, is_target
        rows between unbounded preceding and current row
      ) as candidate_date
    from
      spine
  )
  where
    is_target = 1
    and candidate_date is not null
  ),

  day_type_windows as (
  select
    lookup.area_code,
    lookup.trade_date,
    candidate_periods.time_code,
    -- The days present, added in a fixed order: the newest first.
    (candidate_periods.value_0
      + coalesce(candidate_periods.value_1, 0)
      + coalesce(candidate_periods.value_2, 0)
      + coalesce(candidate_periods.value_3, 0))
    / (1
      + cast(candidate_periods.value_1 is not null as int)
      + cast(candidate_periods.value_2 is not null as int)
      + cast(candidate_periods.value_3 is not null as int)) as mean_daytype_4d_demand_kwh,
    -- Weights 8, 4, 2, 1 from the newest day back, over the days present.
    (8 * candidate_periods.value_0
      + coalesce(4 * candidate_periods.value_1, 0)
      + coalesce(2 * candidate_periods.value_2, 0)
      + coalesce(candidate_periods.value_3, 0))
    / (8
      + 4 * cast(candidate_periods.value_1 is not null as int)
      + 2 * cast(candidate_periods.value_2 is not null as int)
      + cast(candidate_periods.value_3 is not null as int)) as ewm_daytype_4d_demand_kwh,
    candidate_periods.available_at
  from
    lookup
    inner join candidate_periods
      on candidate_periods.area_code = lookup.area_code
      and candidate_periods.date_key = lookup.candidate_date
      and candidate_periods.day_type = lookup.day_type
  ),

  final as (
  select
    by_period.area_code,
    by_period.trade_date,
    by_period.time_code,
    by_period.lag_2d_demand_kwh,
    by_period.lag_3d_demand_kwh,
    by_period.lag_7d_demand_kwh,
    by_period.lag_9d_demand_kwh,
    by_period.lag_14d_demand_kwh,
    by_period.lag_21d_demand_kwh,
    by_period.lag_28d_demand_kwh,
    -- The weekly lags present, added in a fixed order; null when none is.
    (coalesce(by_period.lag_7d_demand_kwh, 0)
      + coalesce(by_period.lag_14d_demand_kwh, 0)
      + coalesce(by_period.lag_21d_demand_kwh, 0)
      + coalesce(by_period.lag_28d_demand_kwh, 0))
    / nullif(
      cast(by_period.lag_7d_demand_kwh is not null as int)
      + cast(by_period.lag_14d_demand_kwh is not null as int)
      + cast(by_period.lag_21d_demand_kwh is not null as int)
      + cast(by_period.lag_28d_demand_kwh is not null as int), 0) as mean_weekly_lags_demand_kwh,
    -- Weights 8, 4, 2, 1 for D-7, D-14, D-21, D-28, over the lags present.
    (coalesce(8 * by_period.lag_7d_demand_kwh, 0)
      + coalesce(4 * by_period.lag_14d_demand_kwh, 0)
      + coalesce(2 * by_period.lag_21d_demand_kwh, 0)
      + coalesce(by_period.lag_28d_demand_kwh, 0))
    / nullif(
      8 * cast(by_period.lag_7d_demand_kwh is not null as int)
      + 4 * cast(by_period.lag_14d_demand_kwh is not null as int)
      + 2 * cast(by_period.lag_21d_demand_kwh is not null as int)
      + cast(by_period.lag_28d_demand_kwh is not null as int), 0) as ewm_weekly_lags_demand_kwh,
    by_period.lag_2d_demand_kwh - by_period.lag_9d_demand_kwh as change_2d_9d_demand_kwh,
    day_type_windows.mean_daytype_4d_demand_kwh,
    day_type_windows.ewm_daytype_4d_demand_kwh,
    {{ available_at(['by_period.available_at', 'day_type_windows.available_at']) }} as available_at
  from
    by_period
    left join day_type_windows
      on day_type_windows.area_code = by_period.area_code
      and day_type_windows.trade_date = by_period.trade_date
      and day_type_windows.time_code = by_period.time_code
  )

select * from final
```

- [ ] **Step 4: Build and test**

Run: `just dbt build --select ftr_period_actuals`
Expected: unit test, model, data tests PASS. Known ways this can fail and what they mean: a contract failure on a `decimal` column means an integer literal was written with a decimal point; `DIVIDE_BY_ZERO` means a `nullif` is missing; a unit-test diff on `available_at` of 03-08/03-15/03-22 means the window's availability is not reaching `final` (the `greatest` or the `max … over` frame).

- [ ] **Step 5: Commit**

```bash
git add dbt/models/features/ftr_period_actuals.sql dbt/models/features/ftr_period_actuals.yml
git commit -m "feat(dbt): recent demand lags, weekly means, change and day-type window in ftr_period_actuals

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Regenerate the views and the fact; the synthetic fixture and the feature tests

**Files:**
- Regenerate (never edit): `power_market_analytics/features/views.py`, `dbt/models/curated/fct_feature_value.sql`
- Modify: `tests/conftest.py` (module-level helpers after `similar_day_load`; `_write_feature_marts` around lines 776-966)
- Modify: `tests/test_feature_views.py:17-26`, `tests/test_feature_store.py:14-22`, `tests/test_feature_value_fact.py:80-90`

**Interfaces:**
- Consumes: the two marts of Tasks 1 and 2 (their column names and types).
- Produces: `views.FTR_DAY_ACTUALS` and the regenerated `views.FTR_PERIOD_ACTUALS` (fields per tagged column, `Int64` for bigint, `Float64` for double); fixture tables `pma_features.ftr_period_actuals` (13 feature columns) and `pma_features.ftr_day_actuals`; conftest helpers `synthetic_day_type(day) -> int`, `mean_of_present(values) -> float | None`, `ewm_of_present(values) -> float | None` and the constants `ACTUALS_LAG_DAYS`, `EWM_WEIGHTS`, `RECENT_LOAD_COLUMNS` used by Task 4's tests.

- [ ] **Step 1: Regenerate the generated files**

Run: `just feature-views`
Expected: `power_market_analytics/features/views.py` gains `FTR_DAY_ACTUALS` (a `SparkSource` + `FeatureView` on the `day` entities with three fields) and `FTR_PERIOD_ACTUALS` lists the thirteen tagged columns (`lag_9d_demand_kwh` included); `dbt/models/curated/fct_feature_value.sql` gains a `ftr_day_actuals` CTE broadcast to 48 periods and the union branch. `git diff --stat` shows both files. Then `uv run python scripts/generate_feature_views.py --check` exits 0.

- [ ] **Step 2: Update the three view-name lists**

`tests/test_feature_views.py`: in `test_one_view_per_mart_on_the_entities_of_its_grain`, the sorted list becomes

```python
    assert sorted(by_name) == [
        "ftr_day_actuals",
        "ftr_day_calendar",
        "ftr_day_occto",
        "ftr_hour_jma_obs",
        "ftr_hour_msm",
        "ftr_period_actuals",
        "ftr_period_jepx",
        "ftr_period_similar_day",
    ]
    day = [AREA_CODE.name, TRADE_DATE_KEY.name]
    assert by_name["ftr_day_actuals"].entities == day
```

and add, after `test_fields_carry_the_marts_types_and_categorical_tags`:

```python
def test_the_actuals_views_carry_the_recent_load_columns():
    period = {field.name: field for field in views.FTR_PERIOD_ACTUALS.features}
    assert list(period) == [
        "lag_2d_demand_kwh",
        "lag_3d_demand_kwh",
        "lag_7d_demand_kwh",
        "lag_9d_demand_kwh",
        "lag_14d_demand_kwh",
        "lag_21d_demand_kwh",
        "lag_28d_demand_kwh",
        "mean_weekly_lags_demand_kwh",
        "ewm_weekly_lags_demand_kwh",
        "change_2d_9d_demand_kwh",
        "mean_daytype_4d_demand_kwh",
        "ewm_daytype_4d_demand_kwh",
    ]
    assert period["lag_2d_demand_kwh"].dtype == Int64
    assert period["ewm_daytype_4d_demand_kwh"].dtype == Float64
    day = {field.name: field for field in views.FTR_DAY_ACTUALS.features}
    assert list(day) == [
        "lag_2d_mean_demand_kwh",
        "lag_2d_max_demand_kwh",
        "lag_2d_range_demand_kwh",
    ]
    assert day["lag_2d_mean_demand_kwh"].dtype == Float64
    assert day["lag_2d_max_demand_kwh"].dtype == Int64
    assert all(field.tags == {"categorical": "false"} for field in [*period.values(), *day.values()])
```

`tests/test_feature_store.py`: `MART_NAMES` gains `"ftr_day_actuals"` as its first entry.

`tests/test_feature_value_fact.py`: the set literal in `test_every_tagged_cell_appears_once_at_its_periods` gains `"ftr_day_actuals"` (before `"ftr_day_calendar"`).

- [ ] **Step 3: Run the feature tests to see them fail on the missing fixture table**

Run: `uv run pytest tests/test_feature_views.py tests/test_feature_store.py tests/test_feature_value_fact.py -q`
Expected: the views tests PASS (the generated file is current), the store test PASSES, `test_feature_value_fact` FAILS: `pma_features.ftr_day_actuals` does not exist in the fixture.

- [ ] **Step 4: Add the fixture helpers and constants**

In `tests/conftest.py`, right after `similar_day_load` (before `def synthetic_holiday_degree`), add:

```python
#: The lags ``ftr_period_actuals`` carries, in days before the delivery day.
ACTUALS_LAG_DAYS = (2, 3, 7, 9, 14, 21, 28)
#: The weights of the mart's two exponentially weighted means, newest input first.
EWM_WEIGHTS = (8, 4, 2, 1)
#: The thirteen recent-load feature columns of research demand/R-006, in preset order.
RECENT_LOAD_COLUMNS = (
    "lag_2d_demand_kwh",
    "lag_3d_demand_kwh",
    "lag_14d_demand_kwh",
    "lag_21d_demand_kwh",
    "lag_28d_demand_kwh",
    "mean_weekly_lags_demand_kwh",
    "ewm_weekly_lags_demand_kwh",
    "change_2d_9d_demand_kwh",
    "mean_daytype_4d_demand_kwh",
    "ewm_daytype_4d_demand_kwh",
    "lag_2d_mean_demand_kwh",
    "lag_2d_max_demand_kwh",
    "lag_2d_range_demand_kwh",
)


def synthetic_day_type(day: pd.Timestamp) -> int:
    """``ftr_day_calendar.day_type`` of the fixture.

    Parameters
    ----------
    day : pandas.Timestamp
        The calendar day.

    Returns
    -------
    int
        2 on a day in ``HOLIDAYS_2024_SPRING``, 1 on a Saturday or Sunday, else 0.
    """
    if day in HOLIDAYS_2024_SPRING:
        return 2
    return 1 if day.dayofweek >= 5 else 0


def mean_of_present(values: list[int | None]) -> float | None:
    """The plain mean over the values present, as ``ftr_period_actuals`` takes it.

    Parameters
    ----------
    values : list of int or None
        The inputs, newest first; None where an input is absent.

    Returns
    -------
    float or None
        None when no value is present.
    """
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def ewm_of_present(values: list[int | None]) -> float | None:
    """The 8, 4, 2, 1 weighted mean over the values present, weights by position.

    Parameters
    ----------
    values : list of int or None
        At most four inputs, newest first; None where an input is absent.

    Returns
    -------
    float or None
        None when no value is present.
    """
    present = [(w, v) for w, v in zip(EWM_WEIGHTS, values, strict=True) if v is not None]
    if not present:
        return None
    return sum(w * v for w, v in present) / sum(w for w, _ in present)


def nullable_column(values: pd.Series, cast: type) -> pd.Series:
    """A column of ``cast`` values and SQL nulls: None where pandas made a NaN of a None.

    Parameters
    ----------
    values : pandas.Series
        The column as ``pd.DataFrame`` built it from dicts holding None.
    cast : type
        ``int`` for a bigint column, ``float`` for a double one.

    Returns
    -------
    pandas.Series
        dtype object, so Spark reads each value as its own type.
    """
    return pd.Series([None if pd.isna(v) else cast(v) for v in values], dtype=object)
```

`ewm_of_present` needs the four-slot list: callers pass exactly `[y7, y14, y21, y28]` or the up-to-four window days padded with None (see Step 5). `zip(..., strict=True)` therefore needs the callers to pad; do not drop `strict=True`.

- [ ] **Step 5: Build the two marts' rows in `_write_feature_marts`**

In `tests/conftest.py`, replace the calendar rows' inline `"day_type": 2 if day in holidays else 1 if day.dayofweek >= 5 else 0,` with `"day_type": synthetic_day_type(day),`. Update the function docstring's first line to `"""The eight feature marts of ``pma_features``, from the fixture's data (tokyo facts).`. Then replace the block

```python
    actuals = warehouse.demand.dropna(subset=["demand_kwh"])
    actuals_rows = [
        {
            "area_code": "tokyo",
            "trade_date": (pd.Timestamp(row["date_key"]) + pd.Timedelta(days=7)).date(),
            "time_code": int(row["time_code"]),
            "lag_7d_demand_kwh": int(row["demand_kwh"]),
            "available_at": pd.Timestamp(row["date_key"]) + pd.Timedelta(days=1, hours=5),
        }
        for row in actuals.to_dict("records")
    ]
```

with

```python
    actuals = warehouse.demand.dropna(subset=["demand_kwh"])
    demand_at = {
        (pd.Timestamp(row["date_key"]), int(row["time_code"])): int(row["demand_kwh"])
        for row in actuals.to_dict("records")
    }
    # The fixture's daily file lands at 05:00 on the next day.
    file_available_at = {day: day + pd.Timedelta(days=1, hours=5) for day in DEMAND_DAYS}
    complete_days = [day for day in DEMAND_DAYS if day != DEMAND_HOLE_DAY]
    delivery_days = pd.date_range(
        DEMAND_DAYS[0] + pd.Timedelta(days=2), DEMAND_DAYS[-1] + pd.Timedelta(days=28), freq="D"
    )
    actuals_rows = []
    for day in delivery_days:
        # The last four complete days of D's day type at or before D-2, newest
        # first; none when the fixture's calendar has no row for D.
        window_days = (
            [
                d
                for d in reversed(complete_days)
                if d <= day - pd.Timedelta(days=2)
                and synthetic_day_type(d) == synthetic_day_type(day)
            ][:4]
            if day in CALENDAR_DAYS
            else []
        )
        for tc in range(1, 49):
            lags = {k: demand_at.get((day - pd.Timedelta(days=k), tc)) for k in ACTUALS_LAG_DAYS}
            if all(v is None for v in lags.values()):
                continue
            weekly = [lags[7], lags[14], lags[21], lags[28]]
            window = [demand_at[(d, tc)] for d in window_days] + [None] * (4 - len(window_days))
            used_days = [day - pd.Timedelta(days=k) for k, v in lags.items() if v is not None]
            actuals_rows.append(
                {
                    "area_code": "tokyo",
                    "trade_date": day.date(),
                    "time_code": tc,
                    **{f"lag_{k}d_demand_kwh": lags[k] for k in ACTUALS_LAG_DAYS},
                    "mean_weekly_lags_demand_kwh": mean_of_present(weekly),
                    "ewm_weekly_lags_demand_kwh": ewm_of_present(weekly),
                    "change_2d_9d_demand_kwh": (
                        None if lags[2] is None or lags[9] is None else lags[2] - lags[9]
                    ),
                    "mean_daytype_4d_demand_kwh": mean_of_present(window),
                    "ewm_daytype_4d_demand_kwh": ewm_of_present(window),
                    "available_at": max(file_available_at[d] for d in [*used_days, *window_days]),
                }
            )
    period_actuals = pd.DataFrame(actuals_rows)
    for col in [f"lag_{k}d_demand_kwh" for k in ACTUALS_LAG_DAYS] + ["change_2d_9d_demand_kwh"]:
        period_actuals[col] = nullable_column(period_actuals[col], int)
    for col in (
        "mean_weekly_lags_demand_kwh",
        "ewm_weekly_lags_demand_kwh",
        "mean_daytype_4d_demand_kwh",
        "ewm_daytype_4d_demand_kwh",
    ):
        period_actuals[col] = nullable_column(period_actuals[col], float)
    day_actuals_rows = []
    for day in complete_days:
        values = [demand_at[(day, tc)] for tc in range(1, 49)]
        day_actuals_rows.append(
            {
                "area_code": "tokyo",
                "trade_date": (day + pd.Timedelta(days=2)).date(),
                "lag_2d_mean_demand_kwh": sum(values) / len(values),
                "lag_2d_max_demand_kwh": max(values),
                "lag_2d_range_demand_kwh": max(values) - min(values),
                "available_at": file_available_at[day],
            }
        )
```

and replace the `pma_features.ftr_period_actuals` write

```python
    spark.createDataFrame(
        pd.DataFrame(actuals_rows),
        "area_code string, trade_date date, time_code int, lag_7d_demand_kwh bigint, "
        "available_at timestamp",
    ).write.mode("overwrite").saveAsTable("pma_features.ftr_period_actuals")
```

with

```python
    spark.createDataFrame(
        period_actuals,
        "area_code string, trade_date date, time_code int, lag_2d_demand_kwh bigint, "
        "lag_3d_demand_kwh bigint, lag_7d_demand_kwh bigint, lag_9d_demand_kwh bigint, "
        "lag_14d_demand_kwh bigint, lag_21d_demand_kwh bigint, lag_28d_demand_kwh bigint, "
        "mean_weekly_lags_demand_kwh double, ewm_weekly_lags_demand_kwh double, "
        "change_2d_9d_demand_kwh bigint, mean_daytype_4d_demand_kwh double, "
        "ewm_daytype_4d_demand_kwh double, available_at timestamp",
    ).write.mode("overwrite").saveAsTable("pma_features.ftr_period_actuals")
    spark.createDataFrame(
        pd.DataFrame(day_actuals_rows),
        "area_code string, trade_date date, lag_2d_mean_demand_kwh double, "
        "lag_2d_max_demand_kwh bigint, lag_2d_range_demand_kwh bigint, available_at timestamp",
    ).write.mode("overwrite").saveAsTable("pma_features.ftr_day_actuals")
```

`ewm_of_present(window)` gets a four-slot list because `window` is padded with None; `mean_of_present` ignores the padding. The `lag_7d_demand_kwh` values the existing tests read (`tests/test_demand_strategies.py:79`, `:120-123`) are unchanged: the same D−7 value at the same key.

- [ ] **Step 6: Run the feature and demand tests**

Run: `uv run pytest tests/test_feature_views.py tests/test_feature_store.py tests/test_feature_value_fact.py tests/test_feature_retrieval.py tests/test_demand_strategies.py tests/test_demand_scripts.py tests/test_forecasting_preset_lgbm.py -q`
Expected: all PASS. If `createDataFrame` rejects a value ("LongType can not accept object 100.0"), a bigint column missed `nullable_column(..., int)`.

- [ ] **Step 7: Commit**

```bash
git add power_market_analytics/features/views.py dbt/models/curated/fct_feature_value.sql tests/conftest.py tests/test_feature_views.py tests/test_feature_store.py tests/test_feature_value_fact.py
git commit -m "feat(forecasting): Feast views and fct_feature_value for the recent load features

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The preset `lightgbm_msm_popw_daytype_simday_lags`

**Files:**
- Modify: `power_market_analytics/tasks/demand/presets.py`
- Modify: `tests/test_demand_presets.py`, `tests/test_features_presets.py:30-42`, `tests/test_demand_strategies.py:40-56` and the class `TestBuildPreset`

**Interfaces:**
- Consumes: the generated views (Task 3), `Preset.with_changes(add=…, name=…)`, `feature_dtypes`, `categorical_columns`, conftest's `RECENT_LOAD_COLUMNS`, `synthetic_demand`, `synthetic_day_type`.
- Produces: `RECENT_LOAD_FEATURES: tuple[str, ...]` (thirteen `<view>:<column>` references) and `LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS`, registered last in `PRESETS`; strategy name `lightgbm_msm_popw_daytype_simday_lags`, the `--strategy` value of Task 6.

- [ ] **Step 1: Write the failing preset tests**

In `tests/test_demand_presets.py`, add `LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS` and `RECENT_LOAD_FEATURES` to the import from `power_market_analytics.tasks.demand.presets`, add `from tests.conftest import RECENT_LOAD_COLUMNS` after it, rename `test_the_nine_presets_keep_the_old_feature_order` to `test_the_ten_presets_keep_the_old_feature_order` and append `"lightgbm_msm_popw_daytype_simday_lags",` to its expected list. Then add:

```python
def test_the_lags_preset_appends_the_thirteen_recent_load_features():
    assert RECENT_LOAD_FEATURES == (
        "ftr_period_actuals:lag_2d_demand_kwh",
        "ftr_period_actuals:lag_3d_demand_kwh",
        "ftr_period_actuals:lag_14d_demand_kwh",
        "ftr_period_actuals:lag_21d_demand_kwh",
        "ftr_period_actuals:lag_28d_demand_kwh",
        "ftr_period_actuals:mean_weekly_lags_demand_kwh",
        "ftr_period_actuals:ewm_weekly_lags_demand_kwh",
        "ftr_period_actuals:change_2d_9d_demand_kwh",
        "ftr_period_actuals:mean_daytype_4d_demand_kwh",
        "ftr_period_actuals:ewm_daytype_4d_demand_kwh",
        "ftr_day_actuals:lag_2d_mean_demand_kwh",
        "ftr_day_actuals:lag_2d_max_demand_kwh",
        "ftr_day_actuals:lag_2d_range_demand_kwh",
    )
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS.name == "lightgbm_msm_popw_daytype_simday_lags"
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS.base == "lightgbm_msm_popw_daytype_simday"
    assert LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS.columns == (*SIMDAY_COLUMNS, *RECENT_LOAD_COLUMNS)
    # The D-9 lag is a mart column for the change, not a feature of the preset.
    assert "ftr_period_actuals:lag_9d_demand_kwh" not in RECENT_LOAD_FEATURES
    dtypes = feature_dtypes(LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS)
    assert {c: dtypes[c] for c in RECENT_LOAD_COLUMNS} == {
        "lag_2d_demand_kwh": "int64",
        "lag_3d_demand_kwh": "int64",
        "lag_14d_demand_kwh": "int64",
        "lag_21d_demand_kwh": "int64",
        "lag_28d_demand_kwh": "int64",
        "mean_weekly_lags_demand_kwh": "float64",
        "ewm_weekly_lags_demand_kwh": "float64",
        "change_2d_9d_demand_kwh": "int64",
        "mean_daytype_4d_demand_kwh": "float64",
        "ewm_daytype_4d_demand_kwh": "float64",
        "lag_2d_mean_demand_kwh": "float64",
        "lag_2d_max_demand_kwh": "int64",
        "lag_2d_range_demand_kwh": "int64",
    }
    assert categorical_columns(LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS) == ("day_type",)
```

In `tests/test_features_presets.py`, append `"demand__lightgbm_msm_popw_daytype_simday_lags",` to `REGISTERED_SERVICES` right after the `holidaydistance` entry (the list is sorted).

In `tests/test_demand_strategies.py`, append `"lightgbm_msm_popw_daytype_simday_lags",` to the tuple in `test_registered_names_are_the_presets`, add `RECENT_LOAD_COLUMNS` and `synthetic_day_type` to the `tests.conftest` import, and add to `TestBuildPreset`, after `test_calendar_variants_append_their_columns`:

```python
    def test_lags_preset_appends_the_thirteen_recent_load_columns(self, feature_marts):
        days = pd.date_range("2024-04-01", "2024-04-25", freq="D")
        strategy = build_strategy(
            "lightgbm_msm_popw_daytype_simday_lags", area_code="tokyo", days=days
        )
        assert type(strategy) is PresetLightGbmStrategy
        assert strategy.feature_cols == (*SIMDAY_FEATURE_COLS, *RECENT_LOAD_COLUMNS)
        assert strategy.categorical_feature_cols == ("day_type",)
        frame = frame_by_period(strategy)
        day, tc = pd.Timestamp("2024-04-05"), 10
        row = frame.loc[(day, tc)]
        lag = {k: synthetic_demand(day - pd.Timedelta(days=k), tc) for k in (2, 7, 9, 14, 21, 28)}
        assert row["lag_2d_demand_kwh"] == lag[2]
        assert row["lag_28d_demand_kwh"] == lag[28]
        assert row["mean_weekly_lags_demand_kwh"] == (lag[7] + lag[14] + lag[21] + lag[28]) / 4
        assert row["ewm_weekly_lags_demand_kwh"] == (
            (8 * lag[7] + 4 * lag[14] + 2 * lag[21] + lag[28]) / 15
        )
        assert row["change_2d_9d_demand_kwh"] == lag[2] - lag[9]
        # A Friday: the four weekdays at or before D-2 are 04-03, 04-02, 04-01 and 03-29.
        window = [
            synthetic_demand(pd.Timestamp(d), tc)
            for d in ("2024-04-03", "2024-04-02", "2024-04-01", "2024-03-29")
        ]
        assert all(synthetic_day_type(pd.Timestamp(d)) == 0 for d in ("2024-04-05", "2024-03-29"))
        assert row["mean_daytype_4d_demand_kwh"] == sum(window) / 4
        assert row["ewm_daytype_4d_demand_kwh"] == (
            (8 * window[0] + 4 * window[1] + 2 * window[2] + window[3]) / 15
        )
        d2 = [synthetic_demand(day - pd.Timedelta(days=2), p) for p in range(1, 49)]
        assert row["lag_2d_mean_demand_kwh"] == sum(d2) / 48
        assert row["lag_2d_max_demand_kwh"] == max(d2)
        assert row["lag_2d_range_demand_kwh"] == max(d2) - min(d2)
        # The hole day (periods 11-48 null) two days before 04-22: a null D-2 lag
        # at period 11, no D-2 summaries at all, the D-7 lag untouched.
        after_hole = DEMAND_HOLE_DAY + pd.Timedelta(days=2)
        assert np.isnan(frame.loc[(after_hole, 11), "lag_2d_demand_kwh"])
        assert frame.loc[(after_hole, 1), "lag_2d_demand_kwh"] == synthetic_demand(DEMAND_HOLE_DAY, 1)
        assert frame.loc[after_hole, "lag_2d_mean_demand_kwh"].isna().all()
        assert frame.loc[after_hole, "lag_7d_demand_kwh"].notna().all()
```

- [ ] **Step 2: Run them to see the import fail**

Run: `uv run pytest tests/test_demand_presets.py tests/test_features_presets.py tests/test_demand_strategies.py -q`
Expected: ImportError on `RECENT_LOAD_FEATURES`.

- [ ] **Step 3: Add the preset**

In `power_market_analytics/tasks/demand/presets.py`, after `CALENDAR_COUNT_FEATURES`, add:

```python
#: The thirteen recent-load features of research demand/R-006, in the
#: researcher's bracket order: recent demand, the added weekly lags, the
#: typical weekly profile, the recent weekly change, the recent
#: matching-day-type load and D-2's summaries. The D-9 lag stays out: the
#: mart carries it for the change.
RECENT_LOAD_FEATURES: tuple[str, ...] = (
    "ftr_period_actuals:lag_2d_demand_kwh",
    "ftr_period_actuals:lag_3d_demand_kwh",
    "ftr_period_actuals:lag_14d_demand_kwh",
    "ftr_period_actuals:lag_21d_demand_kwh",
    "ftr_period_actuals:lag_28d_demand_kwh",
    "ftr_period_actuals:mean_weekly_lags_demand_kwh",
    "ftr_period_actuals:ewm_weekly_lags_demand_kwh",
    "ftr_period_actuals:change_2d_9d_demand_kwh",
    "ftr_period_actuals:mean_daytype_4d_demand_kwh",
    "ftr_period_actuals:ewm_daytype_4d_demand_kwh",
    "ftr_day_actuals:lag_2d_mean_demand_kwh",
    "ftr_day_actuals:lag_2d_max_demand_kwh",
    "ftr_day_actuals:lag_2d_range_demand_kwh",
)
```

After `LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_HOLIDAYDISTANCE`, add:

```python
#: Plus the thirteen recent-load features (demand/R-006 E-001).
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS = LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY.with_changes(
    name="lightgbm_msm_popw_daytype_simday_lags", add=RECENT_LOAD_FEATURES
)
```

Append `LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY_LAGS,` to the tuple inside `PRESETS`. Change the module docstring's first line to `"""The demand task's presets: the feature sets its LightGBM strategy runs on.` (unchanged) and its mention "the five similar-day presets" to "the six similar-day presets".

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_demand_presets.py tests/test_features_presets.py tests/test_demand_strategies.py -q`
Expected: all PASS. A `feature_dtypes` ValueError naming a view means Task 3's regeneration did not land (`git status` shows `views.py` unchanged).

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/tasks/demand/presets.py tests/test_demand_presets.py tests/test_features_presets.py tests/test_demand_strategies.py
git commit -m "feat(demand): preset lightgbm_msm_popw_daytype_simday_lags with the thirteen recent load features

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Full build, warehouse checks, the whole test suite

**Files:** none changed unless a check fails.

**Interfaces:**
- Consumes: everything above.
- Produces: the built marts and `fct_feature_value` in the warehouse; the numbers for the PR body's *Proof* (hidden December-2022 days, a normal day's `available_at`, the retrieval check).

- [ ] **Step 1: Build the marts and everything downstream (background task)**

Run as a background Bash task: `just dbt build --select ftr_period_actuals+ ftr_day_actuals+`
Expected: `ftr_period_actuals`, `ftr_day_actuals`, `fct_feature_value` and every test PASS, including the singular test `assert_fct_feature_value_covers_every_tagged_column` and the two unit tests. Note the build time of `fct_feature_value` for the PR.

- [ ] **Step 2: Count the Tokyo delivery days the mart hides from the 09:30 D−1 issue time**

Run host-side:

```bash
cd dbt && DBT_THRIFT_HOST=localhost uv run dbt show --inline "
select area_code, count(distinct trade_date) as hidden_days,
  min(trade_date) as first_day, max(trade_date) as last_day,
  count(distinct case when lag_7d_demand_kwh is not null then trade_date end) as hidden_days_with_lag_7d
from pma_features.ftr_period_actuals
where available_at > timestampadd(minute, 570, timestampadd(day, -1, cast(trade_date as timestamp)))
group by area_code" --limit 5
```

Expected: `tokyo` about 10 days between 2022-12-03 and 2022-12-16 (the spec's hand count: 12-03 to 12-11 and 12-15), `kansai` none. Record the exact count and dates in the PR body and, if it differs from ten, in the spec's §6 (edit the sentence; the count is the build's).

- [ ] **Step 3: Check a normal day's availability and the day-type window**

```bash
cd dbt && DBT_THRIFT_HOST=localhost uv run dbt show --inline "
select trade_date, time_code, lag_2d_demand_kwh, lag_7d_demand_kwh, mean_weekly_lags_demand_kwh,
  ewm_weekly_lags_demand_kwh, change_2d_9d_demand_kwh, mean_daytype_4d_demand_kwh,
  ewm_daytype_4d_demand_kwh, available_at
from pma_features.ftr_period_actuals
where area_code = 'tokyo' and trade_date in (date '2025-03-10', date '2025-05-06') and time_code = 20" --limit 5
```

Expected: `available_at` about 00:05 on D−1 for both; 2025-05-06 (振替休日, day type 2) has a day-type mean over four holidays, which are weeks apart, not the last four days. Cross-check the 2025-03-10 (Monday) window by hand: the four weekdays at or before 03-08 are 03-07, 03-06, 03-05, 03-04; query their period-20 demand from `fct_area_demand_generation_actual` joined to `dim_area` and confirm the mean and the (8, 4, 2, 1)/15 weighted mean.

- [ ] **Step 4: The Feast retrieval check (devcontainer)**

Run: `just python - <<'EOF'` with

```python
import pandas as pd
from power_market_analytics.common.spark import get_spark_session
from power_market_analytics.features.retrieval import entity_frame, historical_features
from power_market_analytics.features.store import open_store
from power_market_analytics.tasks.demand import TASK
from power_market_analytics.tasks.demand.presets import RECENT_LOAD_FEATURES

spark = get_spark_session()
days = pd.DatetimeIndex(["2022-12-02", "2022-12-05", "2022-12-08", "2025-03-10"])
got = historical_features(open_store(), entity_frame("tokyo", days, TASK.issue_offset), RECENT_LOAD_FEATURES)
got = got[got["time_code"] == 20].set_index("trade_date")
print(got.T)
mart = spark.sql("select trade_date, lag_2d_demand_kwh, mean_daytype_4d_demand_kwh, lag_2d_mean_demand_kwh from pma_features.ftr_period_actuals p left join pma_features.ftr_day_actuals d using (area_code, trade_date) where area_code = 'tokyo' and time_code = 20 and trade_date in ('2022-12-02', '2025-03-10')").toPandas()
print(mart)
EOF
```

Expected: 2022-12-02 and 2025-03-10 return the mart's values; 2022-12-05 and 2022-12-08 return NaN for every `ftr_period_actuals` column (hidden by the re-issued files) while their `ftr_day_actuals` columns are present (D−2 = 12-03 / 12-06, normal files). If `get_spark_session` is not the session helper's name, `grep -n "^def " power_market_analytics/common/spark.py`.

- [ ] **Step 5: The whole suite, lint, types**

Run: `just test` then `just lint` then `just mypy`
Expected: all tests PASS with coverage 100 %; ruff and mypy clean. Coverage below 100 % names the uncovered lines: a fixture branch never taken (e.g. `else []` in `window_days`) needs a test that reaches it or a simpler expression.

- [ ] **Step 6: Commit anything the checks changed**

If Step 2 changed the spec's count: `git add docs/superpowers/specs/2026-09-12-demand-recent-load-features-design.md && git commit -m "docs(demand): the build's count of the hidden December-2022 days" …` with the trailer. Otherwise nothing to commit.

---

### Task 6: The matched runs and the comparison

**Files:**
- Create: `docs/research/demand/assets/R-006-E-001-mae-by-month.png` (written by the compare script)

**Interfaces:**
- Consumes: the preset (Task 4), the built marts (Task 5), MLflow run `429eca360efe4c3b9e89e1258b932cc7` (the newest baseline run on the old mart, MAE 583,561 kWh, 729 days).
- Produces: two MLflow runs in experiment `demand` (their ids, for Task 7), the compare-script markdown for candidate vs fresh baseline and for fresh baseline vs `429eca36…`, the by-month figure.

- [ ] **Step 1: The fresh baseline (background task, about 10 min)**

Run as a background Bash task: `just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday --area tokyo --start-date 2024-08-18 --end-date 2026-08-17`
Expected: 729 delivery days scored, one skipped (2025-06-21), 105 refits; the log ends with the MLflow run id. Save the id as `BASELINE`.

- [ ] **Step 2: The candidate (background task, after Step 1 ends)**

Run: `just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype_simday_lags --area tokyo --start-date 2024-08-18 --end-date 2026-08-17`
Expected: up to seven skipped days after the 2025-06-14 hole (2025-06-16, 06-17, 06-21, 06-23, 06-28, 07-05, 07-12) and the run id, `CANDIDATE`. Record `n_days`, `n_days_skipped` and the skipped dates from the log.

- [ ] **Step 3: Build the accuracy, contribution and importance marts (background task)**

Run: `just dbt build --select +fct_demand_forecast_accuracy +fct_demand_forecast_contribution +fct_demand_forecast_importance`
Expected: PASS.

- [ ] **Step 4: The comparisons**

```bash
just python scripts/compare_demand_runs.py --baseline $BASELINE --candidate $CANDIDATE --common-days --mae-by-month-png docs/research/demand/assets/R-006-E-001-mae-by-month.png > /tmp/r006-e001.md
just python scripts/compare_demand_runs.py --baseline 429eca360efe4c3b9e89e1258b932cc7 --candidate $BASELINE > /tmp/r006-mart-shift.md
```

`--common-days` on the first: the candidate skips six days the baseline scores, and the
script refuses unmatched runs without it (the option was added during execution, commit
`88c5e39`; the plan as first written assumed the script already compared on common
periods). The second pair scored the same days.

Expected: two markdown reports. The second is the mart-change shift: expect a small MAE difference with the CI over days including zero; if it is large (more than ±1 %), stop and report before writing R-006 — the availability change moved more than the December-2022 rows. Keep both files for Task 7. Also read the candidate's `permutation_importance.csv` from its MLflow run (`http://localhost:5005/#/experiments/2/runs/$CANDIDATE`) for the importance table.

---

### Task 7: R-006, the indexes, CLAUDE.md and the other docs

**Files:**
- Create: `docs/research/demand/R-006-recent-load-features.md`
- Modify: `docs/research/demand/README.md` (the scope defaults' baseline sentence, the index table), `docs/_sidebar.md:29`, `docs/superpowers/README.md` (the table), `docs/Forecast-Analysis.md:26-31`, `CLAUDE.md:59-60`, `CLAUDE.md:465-470`, `CLAUDE.md` Demand task bullet, `CLAUDE.md:777-783`

**Interfaces:**
- Consumes: the run ids and the two compare reports of Task 6, the counts of Task 5.

- [ ] **Step 1: Write the investigation**

`docs/research/demand/R-006-recent-load-features.md`, from the template, with these contents (replace `<…>` with Task 6's numbers; never leave one):

```markdown
# R-006 — Recent load features

- **Status:** In progress
- **Last updated:** 2026-09-12 (E-001 run; the researcher's decision pending)
- **Created:** 2026-09-12
- **Triggering observation:** None — modeling idea
- **Related investigations:**
  [R-004 — Year-ago load from a prior-year reference day](research/demand/R-004-prior-year-load-lag.md)
  (its E-002 preset `lightgbm_msm_popw_daytype_simday` is the baseline here);
  [R-005 — Calendar features from dim_date](research/demand/R-005-calendar-features.md)
  (the last feature-set experiment on the same baseline and window)

## Question

The model reads one value of the area's own demand history: the same period
seven days earlier. Does the demand of the last four weeks — the newest
complete days, more weekly lags, their means, the week-on-week change, the
last four days of D's day type and D−2's daily summaries — carry information
the model does not have, and does adding it lower out-of-sample MAE?

## Motivation

The researcher's reasoning, as stated on 2026-09-12: the model is missing
information on recent demand; giving it more information about recent
behavior is going to improve prediction accuracy.

What the model has today of its own history: `lag_7d_demand_kwh` and the
similar day's load one year earlier. The newest public actuals, D−2's
(public about 00:05 on D−1, before the 09:30 issue time), are not read at
all.

## Current predictive hypothesis

> The model is missing information on recent demand. Giving it more
> information about recent behavior is going to improve prediction accuracy.

(The researcher's words, 2026-09-12.)

## Scope and constraints

- **Forecast target:** the 48 half-hourly `demand_kwh` values of
  `fct_area_demand_generation_actual` for day D, Tokyo area (`--area tokyo`)
- **Information cutoff:** the [task defaults](research/demand/README.md).
  Every feature is retrieved through Feast as of 09:30 on D−1; the D−2
  actuals are public by then except on re-issued days (below)
- **Baseline:** `lightgbm_msm_popw_daytype_simday`, re-run on the changed
  `ftr_period_actuals` (run [`<BASELINE>`](http://localhost:5005/#/experiments/2/runs/<BASELINE>)),
  because the mart's `available_at` now takes the newest of every lag it
  carries: the re-issued 2022-12-01/02 files hide <n> Tokyo delivery days of
  December 2022 from the whole row, where before they hid two. Against the
  newest baseline run on the old mart,
  [`429eca36…`](http://localhost:5005/#/experiments/2/runs/429eca360efe4c3b9e89e1258b932cc7),
  the fresh baseline's MAE is <shift> (<shift %>), CI over days <…>
- **Primary metric:** MAE (kWh per 30-minute period)
- **Important segments:** overall MAE (the researcher's stated expectation);
  day type, day part, calendar month, season and the top-10 % demand days as
  consistency checks; the daily paired comparison's bootstrap interval
- **Evaluation method:** rolling out-of-sample backtest over identical
  delivery dates and training rows (`--start-date 2024-08-18 --end-date
  2026-08-17`, no `--train-start`); the compare script counts the periods
  both runs scored

## E-001 — Add the thirteen recent-load features

### Why this experiment

The direct test of the hypothesis: the same model, window and baseline, with
every feature of the researcher's list added at once.

### Experiment hypothesis

The researcher's, above: more information on recent demand improves
accuracy.

### Change

The preset `lightgbm_msm_popw_daytype_simday_lags` = the baseline plus, from
`ftr_period_actuals`: y(D−2, p), y(D−3, p), y(D−14, p), y(D−21, p),
y(D−28, p); the mean and the 8:4:2:1 exponentially weighted mean of the
D−7, D−14, D−21 and D−28 lags; y(D−2, p) − y(D−9, p); the mean and the
8:4:2:1 weighted mean at p over the last four complete days of D's day type
at or before D−2 — and, from `ftr_day_actuals`: D−2's mean, maximum and
maximum-minus-minimum demand. Design:
`docs/superpowers/specs/2026-09-12-demand-recent-load-features-design.md`.

### Expected evidence

- Lower overall MAE than the fresh baseline
- Consistent across months and day parts
- The result that would weaken the hypothesis: no change or a rise in overall
  MAE with the CI over days including zero

### Decision rule

The researcher decides: practical magnitude, consistency across months and
day types, the bootstrap interval over days, and the worst days.

### Execution

- **MLflow experiment:** `demand`
- **Baseline run:** [`<BASELINE>`](http://localhost:5005/#/experiments/2/runs/<BASELINE>)
  (2026-09-12, `lightgbm_msm_popw_daytype_simday`, 729 days, one skipped:
  2025-06-21)
- **Candidate runs:** [`<CANDIDATE>`](http://localhost:5005/#/experiments/2/runs/<CANDIDATE>)
  (2026-09-12, `lightgbm_msm_popw_daytype_simday_lags`, <n> days, <k>
  skipped: <dates> — the 2025-06-14 hole reaches every lag)
- **Code or pull request:** branch `feature/demand-recent-load-features`

### Results

<the compare script's tables: overall MAE / MAPE / bias, by day type, day
part, season, top-10 % days, the daily paired comparison with its CI, the
by-month figure ![MAE by month](assets/R-006-E-001-mae-by-month.png), and
the permutation importance of the candidate's features>

### Interpretation

<left for the researcher; Claude adds only what the tables show: where the
change concentrates, which features carry importance>

### Decision

**Decision:** Pending — the researcher's.

### Follow-up ideas

- (the researcher's)

## Current conclusion

<one paragraph of what the numbers show, no interpretation beyond them>

## Open questions

- The researcher's verdict.

## Final disposition

**Investigation status:** In progress  
**Recommended action:** —  
**Superseded by:** —
```

Write the Results section from `/tmp/r006-e001.md` in the R-005 table shape (Baseline / Candidate / Absolute / Relative), then the daily paired paragraph and the by-month sentence, then the importance table (feature, ΔMAE, importance %). Interpretation records only what the tables show.

- [ ] **Step 2: The research index and the sidebar**

`docs/research/demand/README.md`: append the row

```markdown
| R-006 | [Recent load features](research/demand/R-006-recent-load-features.md) | In progress | E-001, 2026-09-12. Thirteen features from the area's own last four weeks of demand (`lightgbm_msm_popw_daytype_simday_lags`) against a fresh baseline run on the changed mart (`<BASELINE prefix>…`): MAE <baseline> → <candidate> (<rel %>), CI over days <…>. The researcher's decision pending. |
```

and, in the scope defaults' *Baseline* paragraph, after the reference run sentence, add: "Since 2026-09-12 `ftr_period_actuals` carries every recent lag, so a matched baseline is a fresh run on the current mart (R-006 explains why `008868fe…` is no longer matched)."

`docs/_sidebar.md`: after the R-005 line add `    - [R-006 — Recent Load Features](research/demand/R-006-recent-load-features.md)`.

- [ ] **Step 3: The design-history row**

`docs/superpowers/README.md`: insert at the top of the table

```markdown
| 2026-09-12 | Recent load features — thirteen features from the area's last four weeks of demand in the feature marts, one preset and a matched run (demand R-006) | [spec](superpowers/specs/2026-09-12-demand-recent-load-features-design.md) | [plan](superpowers/plans/2026-09-12-demand-recent-load-features.md) |
```

- [ ] **Step 4: `docs/Forecast-Analysis.md`**

Replace "which is why the delivery days 2022-12-08 and 2022-12-09 get no D-7 lag from `ftr_period_actuals`: TEPCO re-issued the files behind them on 2022-12-14, days after those forecasts were due." with "which is why <n> Tokyo delivery days of December 2022 get no row from `ftr_period_actuals`: TEPCO re-issued the 2022-12-01 and 12-02 files on 2022-12-14, days after those forecasts were due, and since 2026-09-12 the mart's row is usable only once every lag it carries is public (before, only the D-7 lag counted, and two days were hidden)."

- [ ] **Step 5: CLAUDE.md**

Four edits:

1. Line 59: "`feature_marts` (the seven `pma_features` marts" → "`feature_marts` (the eight `pma_features` marts".
2. The marts list (lines 465-470): "Today's seven: `ftr_day_calendar`, `ftr_day_occto`, …" → "Today's eight: `ftr_day_actuals` (since 2026-09-12, research `demand/R-006`: D-2's mean, max and range over its 48 periods, complete days only), `ftr_day_calendar`, `ftr_day_occto`, `ftr_hour_jma_obs`, `ftr_hour_msm`, `ftr_period_actuals` (since 2026-09-12 the lags of 2, 3, 7, 9, 14, 21 and 28 days, the plain and 8:4:2:1 weighted means of the four weekly lags, the D-2 − D-9 change and the same means over the last four complete days of D's `ftr_day_calendar` day type at or before D-2 — one union of the shifted actuals grouped per period, so a row exists wherever any lag exists and a column is null where its input is absent; `available_at` is the greatest over the rows used, so the whole row waits for the newest lag's file), `ftr_period_jepx` (each proven equal to the Python builder it mirrors for Tokyo 2025) and `ftr_period_similar_day` …" (keep the rest of the sentence).
3. The Demand task bullet, after the `…_calendarcounts` sentence (ending "`half` / `quarter` never split on)."): add "`lightgbm_msm_popw_daytype_simday_lags` (research `demand/R-006` E-001, run 2026-09-12 `<CANDIDATE prefix>…` against the fresh baseline `<BASELINE prefix>…`: MAE <rel %>, CI over days <…>; the researcher's decision pending) = the Tokyo baseline + `RECENT_LOAD_FEATURES`, the thirteen columns of `ftr_period_actuals` and `ftr_day_actuals` above but `lag_9d_demand_kwh`."
4. The Gotchas bullet on the December-2022 files (lines 777-783): replace "so `ftr_period_actuals` gives them no lag while the deleted class-based demand code read the final value: a run whose 730-day training window reaches December 2022 differs from it by those 96 training rows." with "so `ftr_period_actuals` gave them no lag while the deleted class-based demand code read the final value: a run whose 730-day training window reaches December 2022 differed from it by those 96 training rows. Since 2026-09-12 the mart carries every lag from D-2 to D-28 under one `available_at`, so the same two files hide <n> delivery days (2022-12-03 … ) from the whole row: a baseline run on the old mart (`429eca36…`) and one on the new (`<BASELINE prefix>…`) differ by <shift %> MAE, and a matched comparison needs both runs on the same mart."

Also the sentence at line 603 "and `ftr_period_actuals:lag_7d_demand_kwh`" stays as is.

- [ ] **Step 6: Commit**

```bash
git add docs/research/demand/R-006-recent-load-features.md docs/research/demand/README.md docs/research/demand/assets/R-006-E-001-mae-by-month.png docs/_sidebar.md docs/superpowers/README.md docs/Forecast-Analysis.md CLAUDE.md docs/superpowers/plans/2026-09-12-demand-recent-load-features.md
git commit -m "docs(demand): R-006 recent load features, E-001 numbers and the mart change

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Pull request and the review loop

**Files:** none.

- [ ] **Step 1: Final verification**

Run: `just test && just lint && just mypy && uv run python scripts/generate_feature_views.py --check && git status --short`
Expected: all clean, nothing uncommitted.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feature/demand-recent-load-features
gh pr create --title "feat(demand): recent load features in the marts, the simday_lags preset and R-006" --body-file /tmp/pr-body.md
gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label forecasting --add-label research
```

`/tmp/pr-body.md`, sections *Why* (the researcher's request and hypothesis in one paragraph), *What* (the two marts, the availability rule and its cost, the generated files, the fixture, the preset, R-006), *Proof* (the unit tests; the build time; Task 5's hidden-day count and the availability spot check; the retrieval check; the two compare results — candidate vs fresh baseline and fresh baseline vs `429eca36…` — with MAE, relative change and the CI over days; `just test` 100 %, lint, mypy, `--check` clean), ending with the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. No Codex mention anywhere.

- [ ] **Step 3: Wait for Codex and CI, address every finding**

Poll every 60 s as a main-session background task (`gh api --method GET -F per_page=100 repos/hankehly/power-market-analytics/pulls/<n>/reviews`, `…/issues/<n>/reactions`, `…/pulls/<n>/comments`, `…/issues/<n>/comments`) until a `Codex Review` by `chatgpt-codex-connector[bot]` newer than the push or a 👍 reaction; never conclude "no findings" from silence; the manual trigger only after 20 min with neither 👀 nor a review. Fix or rebut each finding with evidence, reply in the thread, resolve it, push, and repeat until a round ends clean. Then report the PR as ready; the researcher merges.

- [ ] **Step 4: Memory**

Write `~/.claude/projects/-Users-hankehly-Projects-power-market-analytics/memory/demand-r006-recent-load-features.md` (type `project`): the decisions, the run ids, the numbers, the PR number and its state, and add its line to `MEMORY.md`.
