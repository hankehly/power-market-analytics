# `available_at` in the standardized layer — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every standardized model of a source that feeds features gets an `available_at` column, the naive-JST instant its row became public, and the seven curated facts built from them carry it through.

**Architecture:** Each rule is one SQL expression in the source's `std_*` model, next to its typed time axis, documented in that model's YAML with the evidence for the lag. Facts select the column through. No Python changes.

**Tech Stack:** dbt 1.11 on dbt-spark (thrift), Spark SQL, `dbt_utils` 1.3.3.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §2 decision 3, §3, §11 PR 1.

## Global Constraints

- Every model keeps an enforced contract: a new column needs `data_type: timestamp`.
- Warehouse timestamps are naive JST; `available_at` is too.
- dbt 1.11 generic-test arguments go under `arguments:`.
- Branch `feature/available-at-standardized` off `main`; commit type `feat(dbt)`; PR label `enhancement` + `documentation`.
- `dbt build` runs in the devcontainer (`just dbt build …`) as a main-session background task.
- A rule is an upper bound on the publication lag, never a best case: later is safe, earlier leaks.

## The rules

| Model | `available_at` | Evidence |
|---|---|---|
| `std_jma__hourly` | `timestampadd(hour, 1, observed_at)` | JMA posts an hour's values within about 10 to 30 minutes; the demand task already relies on the 09:00 observation being public at 09:30. No per-row record, so 1 h is a bound. |
| `std_jma__msm_surface_forecast` | `timestampadd(hour, 4, forecast_reference_at)` | Only the 12 UTC run (21:00 JST) is loaded; RISH distribution observed ~23:30 JST, 2.5 h after reference (`docs/JMA-MSM-GPV-Retrieval.md` §3). 4 h is the bound: 01:00 JST D-1. |
| `std_tepco__area_demand_generation_actual` | `greatest(file_updated_at, timestampadd(minute, 30, delivery_datetime))` | Daily files are written ~00:05 on D+1 (median lag 12.1 h, p99 23.6 h, max 321.8 h on re-issued days). The frozen 2025-06-14 file carries 05:05 on the day itself, before its own periods; `greatest` with the period end keeps a value from being available before its period ends. |
| `std_kansai__area_demand_generation_actual` | same expression | Min lag 0.2 h, median 12.0 h, max 23.7 h; the same rule for symmetry. |
| `std_tepco__power_usage_hourly` | `case when source_file like 'juyo-%' then timestampadd(day, 2, cast(delivery_date as timestamp)) else greatest(file_updated_at, timestampadd(hour, hour_ending, cast(delivery_date as timestamp))) end` | Daily files (2022-04 →) are last updated at 23:55 on the day, 5 min before the last hour ends (median lag 11.4 h, max 22.9 h). The yearly `juyo-YYYY.csv` rows (2016-04 → 2022-03) carry the yearly file's update time, months to years later, so they get D+2 00:00, above every observed daily lag. |
| `std_kansai__power_usage_hourly` | `greatest(file_updated_at, timestampadd(hour, hour_ending, cast(delivery_date as timestamp)))` | Min lag 0.7 h, median 12.4 h, max 24.2 h, all daily files. |
| `std_occto__demand_forecast_dad` | `timestampadd(hour, 18, cast(formulated_date as timestamp))` | Rule 「17時30分以降速やかに」, portal notices observed 17:45–17:48, OCCTO's timeline says 「18時頃」 (`docs/OCCTO-Demand-Forecast-Retrieval.md` §7.1); `formulated_date` = target − 2 on every row. |
| `std_occto__area_reserve_rate_dad` | `timestampadd(hour, 18, timestampadd(day, -2, cast(target_date as timestamp)))` | Same publication as the daily dataset, formulated D−2, Web公表 preamble e.g. 17:49 (doc §9). |
| `std_jepx__spot` | `timestampadd(hour, 12, timestampadd(day, -1, cast(trade_date as timestamp)))` | Bids close 10:00 on D−1 and results are published promptly after (JEPX 取引ガイド); OCCTO's timeline has 「D−1 10時頃 スポット約定 → 12時 BG翌日計画提出期限」, so 12:00 is a bound the market itself relies on. |
| `std_ml__demand_forecast`, `std_ml__spot_price_forecast` | `forecast_issued_ts as available_at` | The forecast's issue time, simulated in a backtest, real in production. |

Sanity test per model (`dbt_utils.expression_is_true`, model level): JMA `available_at > observed_at`; MSM `available_at > forecast_reference_at`; A-1 `available_at >= timestampadd(minute, 30, delivery_datetime)`; でんき予報 `available_at >= timestampadd(hour, hour_ending, cast(delivery_date as timestamp))`; OCCTO daily `available_at < cast(target_date as timestamp)`; OCCTO 30m `available_at < delivery_datetime`; JEPX `available_at < trade_datetime`; ML `available_at = forecast_issued_ts`. Every column also gets `not_null`.

---

### Task 1: Branch and commit the spec and this plan

**Files:**
- Create (already in the working tree, untracked): `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md`
- Create: `docs/superpowers/plans/2026-09-10-available-at-standardized.md`

- [ ] **Step 1: Branch off main**

```bash
git switch -c feature/available-at-standardized main
```

- [ ] **Step 2: Commit both documents**

```bash
git add docs/superpowers/specs/2026-09-10-feature-catalogue-design.md docs/superpowers/plans/2026-09-10-available-at-standardized.md
git commit -m "docs: feature catalogue design and the available_at plan"
```

### Task 2: JMA observations and MSM forecast

**Files:**
- Modify: `dbt/models/standardized/std_jma__hourly.sql`, `.yml`
- Modify: `dbt/models/standardized/std_jma__msm_surface_forecast.sql`, `.yml`

**Interfaces:**
- Produces: column `available_at timestamp` on both models, consumed by Task 8.

- [ ] **Step 1: Add the column to both YAML contracts** (last entry under `columns:`), and the sanity test under the model's `data_tests:`.

`std_jma__hourly.yml`:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the observation became public, naive JST: observed_at + 1 h.
          JMA posts an hour's values within about 10 to 30 minutes of the
          hour; there is no per-row publication record, so one hour is the
          bound. The demand task already relies on the 09:00 observation
          being public at 09:30.
        data_tests:
          - not_null
```

and under the model's `data_tests:`:

```yaml
      - dbt_utils.expression_is_true:
          arguments:
            expression: "available_at > observed_at"
```

`std_jma__msm_surface_forecast.yml`:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the run became public, naive JST: forecast_reference_at + 4 h.
          Only the 12 UTC run (21:00 JST) is loaded; RISH distribution was
          observed at ~23:30 JST, 2.5 h after reference
          (docs/JMA-MSM-GPV-Retrieval.md §3), so 4 h is the bound: 01:00 JST
          on D-1 for a delivery day D.
        data_tests:
          - not_null
```

model-level test: `expression: "available_at > forecast_reference_at"`.

- [ ] **Step 2: Build to see the contract fail**

Run: `just dbt build --select std_jma__hourly std_jma__msm_surface_forecast`
Expected: FAIL, the contract lists `available_at` and the model does not produce it.

- [ ] **Step 3: Add the SQL**

`std_jma__hourly.sql`, after `solar_radiation_homogeneity_no`:

```sql
    solar_radiation_homogeneity_no,
    -- When the hour's values became public: a one-hour bound on JMA's
    -- posting delay (no per-row record exists).
    timestampadd(hour, 1, observed_at) as available_at
```

`std_jma__msm_surface_forecast.sql`, after `source_file_name`:

```sql
    source_file_name,
    -- When the run became public: a four-hour bound on dissemination
    -- (RISH distribution observed ~2.5 h after reference).
    timestampadd(
      hour, 4, timestampadd(hour, 9, to_timestamp(forecast_reference_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
    ) as available_at
```

- [ ] **Step 4: Build again**

Run: `just dbt build --select std_jma__hourly std_jma__msm_surface_forecast`
Expected: PASS, tests included.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/standardized/std_jma__hourly.sql dbt/models/standardized/std_jma__hourly.yml dbt/models/standardized/std_jma__msm_surface_forecast.sql dbt/models/standardized/std_jma__msm_surface_forecast.yml
git commit -m "feat(dbt): available_at on the JMA observation and MSM forecast models"
```

### Task 3: TSO area actuals (A-1)

**Files:**
- Modify: `dbt/models/standardized/std_tepco__area_demand_generation_actual.sql`, `.yml`
- Modify: `dbt/models/standardized/std_kansai__area_demand_generation_actual.sql`, `.yml`

- [ ] **Step 1: YAML** — column after `file_updated_at`:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST: the daily file's
          file_updated_at, but never before the period ends. Files are
          written about 00:05 on the next day (median lag 12 h, p99 24 h;
          re-issued days later). The frozen 2025-06-14 file carries 05:05 on
          the day itself, before its own periods, hence the floor.
        data_tests:
          - not_null
```

model-level: `expression: "available_at >= timestampadd(minute, 30, delivery_datetime)"`. Kansai's description: same rule; observed lag 0.2 h to 23.7 h, median 12 h, no frozen file, the floor kept for symmetry.

- [ ] **Step 2: Build, expect the contract failure**

Run: `just dbt build --select std_tepco__area_demand_generation_actual std_kansai__area_demand_generation_actual`

- [ ] **Step 3: SQL** — in both models, after `file_updated_at`:

```sql
    file_updated_at,
    -- Public at the file's update time, never before the period ends.
    greatest(file_updated_at, timestampadd(minute, 30, delivery_datetime)) as available_at
```

`delivery_datetime` is defined in the same select list; repeat the expression instead of the alias:

```sql
    greatest(
      file_updated_at,
      timestampadd(minute, time_code * 30, cast(target_date as timestamp))
    ) as available_at
```

- [ ] **Step 4: Build, expect PASS**

- [ ] **Step 5: Commit** — `feat(dbt): available_at on the TSO area-actuals models`

### Task 4: でんき予報 hourly

**Files:**
- Modify: `dbt/models/standardized/std_tepco__power_usage_hourly.sql`, `.yml`
- Modify: `dbt/models/standardized/std_kansai__power_usage_hourly.sql`, `.yml`

- [ ] **Step 1: YAML** — column after `source_file`. TEPCO:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST. Daily files (2022-04-01 on):
          file_updated_at, never before the hour ends; the archive keeps the
          live file's last update at 23:55, five minutes before the last hour
          ends (median lag 11 h, max 23 h). Yearly juyo-YYYY.csv rows
          (2016-04-01 to 2022-03-31) carry the yearly file's update time,
          months to years later, so they get the delivery day + 2 days at
          00:00, above every daily lag observed.
        data_tests:
          - not_null
```

Kansai: `file_updated_at`, never before the hour ends; observed lag 0.7 h to 24.2 h, median 12 h, daily files throughout. Model-level test for both: `expression: "available_at >= timestampadd(hour, hour_ending, cast(delivery_date as timestamp))"`.

- [ ] **Step 2: Build, expect the contract failure**

- [ ] **Step 3: SQL**. TEPCO, after `source_file`:

```sql
    source_file,
    -- Public at the daily file's update time, never before the hour ends;
    -- yearly-file rows have no useful update time and get D+2 00:00.
    case
      when source_file like 'juyo-%' then timestampadd(day, 2, cast(target_date as timestamp))
      else greatest(file_updated_at, timestampadd(hour, hour_start + 1, cast(target_date as timestamp)))
    end as available_at
```

Kansai:

```sql
    source_file,
    -- Public at the daily file's update time, never before the hour ends.
    greatest(file_updated_at, timestampadd(hour, hour_start + 1, cast(target_date as timestamp))) as available_at
```

- [ ] **Step 4: Build, expect PASS**

- [ ] **Step 5: Commit** — `feat(dbt): available_at on the でんき予報 hourly models`

### Task 5: OCCTO 翌々日

**Files:**
- Modify: `dbt/models/standardized/std_occto__demand_forecast_dad.sql`, `.yml`
- Modify: `dbt/models/standardized/std_occto__area_reserve_rate_dad.sql`, `.yml`

- [ ] **Step 1: YAML** — daily, column after `reserve_rate`:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the forecast became public, naive JST: formulated_date at
          18:00. OCCTO's rule is 「翌々日：毎日17時30分以降速やかに」, portal
          notices show 17:45–17:48 and OCCTO's own timeline says 「18時頃」
          (docs/OCCTO-Demand-Forecast-Retrieval.md §7.1); 18:00 is the bound.
        data_tests:
          - not_null
```

model-level: `expression: "available_at < cast(target_date as timestamp)"`. 30m, column after `area_reserve_mw`: same wording with "target_date − 2 days at 18:00 (formulated on D−2, Web公表 preamble e.g. 17:49, doc §9)"; model-level `expression: "available_at < delivery_datetime"`.

- [ ] **Step 2: Build, expect the contract failure**

- [ ] **Step 3: SQL**. Daily, after `reserve_rate_pct / 100 as reserve_rate`:

```sql
    reserve_rate_pct / 100 as reserve_rate,
    -- Public at 18:00 on the formulation day (rule: 17:30以降速やかに).
    timestampadd(hour, 18, cast(formulated_date as timestamp)) as available_at
```

30m, after `area_reserve_mw`:

```sql
    area_reserve_mw,
    -- Formulated on D-2 and public at 18:00 that day (rule: 17:30以降速やかに).
    timestampadd(hour, 18, timestampadd(day, -2, cast(target_date as timestamp))) as available_at
```

- [ ] **Step 4: Build, expect PASS**

- [ ] **Step 5: Commit** — `feat(dbt): available_at on the OCCTO 翌々日 models`

### Task 6: JEPX spot

**Files:**
- Modify: `dbt/models/standardized/std_jepx__spot.sql`, `.yml`

- [ ] **Step 1: YAML** — column after `fip_reference_price_kyushu_jpy_kwh`:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the day's results became public, naive JST: trade_date − 1 day
          at 12:00. Bids close at 10:00 on D−1 and JEPX publishes the results
          promptly after (取引ガイド); OCCTO's timeline has the spot results at
          about 10:00 and the BG next-day plan deadline at 12:00, so 12:00 is
          a bound the market itself relies on.
        data_tests:
          - not_null
```

model-level: `expression: "available_at < trade_datetime"`.

- [ ] **Step 2: Build, expect the contract failure**

- [ ] **Step 3: SQL** — after `fip_reference_price_kyushu_jpy_kwh`:

```sql
    fip_reference_price_kyushu_jpy_kwh,
    -- Results are public soon after the 10:00 D-1 gate closure; 12:00 is the bound.
    timestampadd(hour, 12, timestampadd(day, -1, cast(trade_date as timestamp))) as available_at
```

- [ ] **Step 4: Build, expect PASS**

- [ ] **Step 5: Commit** — `feat(dbt): available_at on the JEPX spot model`

### Task 7: Forecast write-backs

**Files:**
- Modify: `dbt/models/standardized/std_ml__demand_forecast.sql`, `.yml`
- Modify: `dbt/models/standardized/std_ml__spot_price_forecast.sql`, `.yml`

- [ ] **Step 1: YAML** — column after `published_at`:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the forecast became public, naive JST: forecast_issued_ts
          under the name every standardized feature source uses. Simulated in
          a backtest, real in production.
        data_tests:
          - not_null
```

model-level: `expression: "available_at = forecast_issued_ts"`.

- [ ] **Step 2: Build, expect the contract failure**

- [ ] **Step 3: SQL** — after `published_at` in both:

```sql
    published_at,
    forecast_issued_ts as available_at
```

- [ ] **Step 4: Build, expect PASS**

- [ ] **Step 5: Commit** — `feat(dbt): available_at on the forecast write-back models`

### Task 8: The seven facts carry the column through

**Files:**
- Modify: `dbt/models/curated/fct_jma_weather_hourly.sql`, `.yml`
- Modify: `dbt/models/curated/fct_jma_msm_weather_forecast_hourly.sql`, `.yml`
- Modify: `dbt/models/curated/fct_area_demand_generation_actual.sql`, `.yml`
- Modify: `dbt/models/curated/fct_area_power_usage_hourly.sql`, `.yml`
- Modify: `dbt/models/curated/fct_occto_demand_supply_forecast_daily.sql`, `.yml`
- Modify: `dbt/models/curated/fct_occto_demand_supply_forecast_30m.sql`, `.yml`
- Modify: `dbt/models/curated/fct_jepx_spot_area_price.sql`, `.yml`

- [ ] **Step 1: YAML** — last column on each fact:

```yaml
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST, from the standardized model
          (its YAML states the rule). The as-of join of the feature layer
          reads it; a feature model takes the greatest across its inputs.
        data_tests:
          - not_null
```

- [ ] **Step 2: Build the seven facts, expect the contract failure**

Run: `just dbt build --select fct_jma_weather_hourly fct_jma_msm_weather_forecast_hourly fct_area_demand_generation_actual fct_area_power_usage_hourly fct_occto_demand_supply_forecast_daily fct_occto_demand_supply_forecast_30m fct_jepx_spot_area_price`

- [ ] **Step 3: SQL** — add `available_at` as the last select item of each fact's `final` CTE, and to both union branches of `fct_area_demand_generation_actual` and `fct_area_power_usage_hourly`. `fct_jepx_spot_area_price` also selects it in the `unpivoted` CTE (with `trade_datetime`), then `unpivoted.available_at` in `final`.

- [ ] **Step 4: Build, expect PASS**

- [ ] **Step 5: Commit** — `feat(dbt): the seven feature-source facts carry available_at through`

### Task 9: Full build and the proof query

- [ ] **Step 1: Build the changed models and everything downstream**

Run as a background task: `just dbt build --select std_jma__hourly+ std_jma__msm_surface_forecast+ std_tepco__area_demand_generation_actual+ std_kansai__area_demand_generation_actual+ std_tepco__power_usage_hourly+ std_kansai__power_usage_hourly+ std_occto__demand_forecast_dad+ std_occto__area_reserve_rate_dad+ std_jepx__spot+ std_ml__demand_forecast+ std_ml__spot_price_forecast+`
Expected: every model and test PASS.

- [ ] **Step 2: The proof query** — smallest and largest lag from the event's end to `available_at`, per model, for the PR body:

```sql
select 'std_jma__hourly' as model, min(timestampdiff(minute, observed_at, available_at)) / 60.0 as min_lag_h, max(timestampdiff(minute, observed_at, available_at)) / 60.0 as max_lag_h from pma_standardized.std_jma__hourly
union all select 'std_jma__msm_surface_forecast', min(timestampdiff(minute, forecast_reference_at, available_at)) / 60.0, max(timestampdiff(minute, forecast_reference_at, available_at)) / 60.0 from pma_standardized.std_jma__msm_surface_forecast
union all select 'std_tepco__area_demand_generation_actual', min(timestampdiff(minute, timestampadd(minute, 30, delivery_datetime), available_at)) / 60.0, max(timestampdiff(minute, timestampadd(minute, 30, delivery_datetime), available_at)) / 60.0 from pma_standardized.std_tepco__area_demand_generation_actual
union all select 'std_kansai__area_demand_generation_actual', min(timestampdiff(minute, timestampadd(minute, 30, delivery_datetime), available_at)) / 60.0, max(timestampdiff(minute, timestampadd(minute, 30, delivery_datetime), available_at)) / 60.0 from pma_standardized.std_kansai__area_demand_generation_actual
union all select 'std_tepco__power_usage_hourly', min(timestampdiff(minute, timestampadd(hour, hour_ending, cast(delivery_date as timestamp)), available_at)) / 60.0, max(timestampdiff(minute, timestampadd(hour, hour_ending, cast(delivery_date as timestamp)), available_at)) / 60.0 from pma_standardized.std_tepco__power_usage_hourly
union all select 'std_kansai__power_usage_hourly', min(timestampdiff(minute, timestampadd(hour, hour_ending, cast(delivery_date as timestamp)), available_at)) / 60.0, max(timestampdiff(minute, timestampadd(hour, hour_ending, cast(delivery_date as timestamp)), available_at)) / 60.0 from pma_standardized.std_kansai__power_usage_hourly
union all select 'std_occto__demand_forecast_dad', min(timestampdiff(minute, available_at, cast(target_date as timestamp))) / 60.0, max(timestampdiff(minute, available_at, cast(target_date as timestamp))) / 60.0 from pma_standardized.std_occto__demand_forecast_dad
union all select 'std_occto__area_reserve_rate_dad', min(timestampdiff(minute, available_at, delivery_datetime)) / 60.0, max(timestampdiff(minute, available_at, delivery_datetime)) / 60.0 from pma_standardized.std_occto__area_reserve_rate_dad
union all select 'std_jepx__spot', min(timestampdiff(minute, available_at, trade_datetime)) / 60.0, max(timestampdiff(minute, available_at, trade_datetime)) / 60.0 from pma_standardized.std_jepx__spot
```

For the OCCTO and JEPX rows the lag is from `available_at` to the delivery instant, which must be positive.

### Task 10: Documentation

**Files:**
- Modify: `CLAUDE.md` (the `## dbt` section)
- Modify: `docs/OCCTO-Demand-Forecast-Retrieval.md` (the two model tables, lines ~430 and ~536)
- Modify: `docs/JMA-MSM-GPV-Retrieval.md` (the model table, line ~377)
- Modify: `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` (§3 table statuses, §11 PR 1 row)

- [ ] **Step 1: CLAUDE.md** — add under `## dbt`:

```markdown
- Every standardized model of a source that feeds features carries `available_at`
  (naive JST): when the row became public, computed there once from the source's
  publication column or a documented bound (the rule and its evidence are in the
  model's YAML; `docs/superpowers/plans/2026-09-10-available-at-standardized.md`
  lists them). The curated facts built from those models pass the column through,
  and a model that joins several inputs takes `greatest()`. Forecast write-backs
  expose `forecast_issued_ts` under the same name.
```

- [ ] **Step 2: OCCTO and MSM docs** — append to each std row's column summary: "`available_at` (18:00 on the formulation day)" / "`available_at` (reference + 4 h)".

- [ ] **Step 3: Spec** — in §3 replace the three "to confirm" statuses with the confirmed bounds and evidence; in §11 mark PR 1 with its PR number.

- [ ] **Step 4: Commit** — `docs(dbt): document the available_at rules`

### Task 11: PR

- [ ] **Step 1: Push and open the PR** with title `feat(dbt): available_at on the standardized feature sources`, body *Why* / *What* / *Proof* (the build result and the proof-query table), labels `enhancement` + `documentation`, assignee `hankehly`.
- [ ] **Step 2: Codex loop** per CLAUDE.md: background poll, address every finding, report when 👍 and CI are green.
