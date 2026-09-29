# Weather siblings of the load lag windows, part 1 of 4: the fact and the hour marts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `fct_area_weather_hourly`, the curated hourly population-weighted observation with its coverage, make `ftr_hour_jma_obs` read it with no value moving, and add the D-2 and D-7 temperature and radiation siblings to that mart and their four deltas to `ftr_hour_msm`: PR 1 of the spec's four.

**Architecture:** One dbt fact lifts the station weighting out of `ftr_hour_jma_obs` (same terms, same station order, same macro, so the mart's columns equal main to the bit). The mart then reads the fact for its windows and for the two lag siblings. `ftr_hour_msm` left-joins the mart on area, day and hour for the deltas, so a delta's row carries the vintage and no `available_at` moves. The generated Feast views and catalogue models follow from the tags; the pytest fixture's synthetic marts gain the eight columns.

**Tech Stack:** dbt (Spark SQL, dbt unit tests, contracts), the `ordered_weighted_mean` and `available_at` macros, `scripts/generate_feature_views.py`, pytest with the local Spark fixture, beeline on the compose thriftserver, `gh`.

**Spec:** `docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md` (sections 4, 5.1, 5.2, 6, 8, 9).

## Global Constraints

- Work only in the worktree `.claude/worktrees/lag-window-weather-siblings`; never `cd` to the main checkout. Compose commands from the worktree use `docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics …`.
- Host-side dbt from the worktree: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`. Never run two dbt processes in one project directory at once.
- A weighted mean or a plain mean of doubles in a mart goes through `ordered_weighted_mean` over an `array_sort`ed `collect_list`, never `sum()` or `avg()`.
- A feature column needs `meta.feature`, `meta.categorical`, `meta.expression`; physical names never change; expressions follow `docs/Feature-Naming.md`.
- Every model: `contract: enforced: true`, a `data_type` per column, a uniqueness test on its key.
- Every new input of a model feeds `available_at` through the `available_at()` macro (`greatest`).
- Physical names and expressions are the spec's, exactly: `lag_2d_popw_temperature_c`, `lag_7d_popw_temperature_c`, `lag_2d_popw_solar_radiation_mjm2`, `lag_7d_popw_solar_radiation_mjm2` in `ftr_hour_jma_obs`; `delta_` + each of those in `ftr_hour_msm`.
- Commits: Conventional Commits, `feat(dbt): …`, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never write the Codex mention (the bot's handle followed by "review") anywhere that shows in a diff or a PR body.

## Review Focus

1. An hour where no weighted station reports an element: the fact's mean is null with count 0 and share 0.0, never a division by zero (Task 1's unit test, hour 2).
2. A station with a row but no census weight (a summit station): it must not enter the mean or the share (Task 1's unit test, station s3).
3. The refactor of `ftr_hour_jma_obs` must not move a single existing value or `available_at` on real data, radiation-only hours included (Task 5's null-safe diff against the scratch copy).
4. A delivery hour with a vintage but no sibling row, and the reverse: the delta is null, the forecast row stays, and `available_at` stays the vintage's (Task 3's unit test, hour 2).
5. `greatest(available_at, null)` must pass the vintage's instant through: Spark's `greatest` skips nulls, and Task 3's test pins it with an hour that has no sibling.

---

### Task 0: The two feature-candidate issues and the branch name

**Files:**
- None in the repository.

- [ ] **Step 1: Open the temperature candidate**

Write the body to `/tmp/candidate-temperature.md` (a scratch path outside the repo), then create the issue:

```markdown
- **Task:** demand
- **Suggested by:** the researcher, 2026-09-28, after the analysis of run 8fb1b358's 2025-03-04 underforecast; the window order, the placement and the second element (radiation, the sibling issue) are Claude's proposals of the same day. Spec: docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md.

## Feature

For each of the four lag windows that carry 87 % of e219's permutation importance — the similar days, D-2, the day-type 4-day window and D-7 — the population-weighted observed temperature those days had at the period's hour, read with the load feature's own dates and weights, and its difference from D's population-weighted forecast at the same hour: 21 columns (section 5 of the spec).

## Why it should help

We believe the differences will lower MAE on days whose weather breaks with the days the load lags come from. On 2025-03-04 (Tokyo, a 2.4 °C overcast Tuesday two days after a 20 °C Sunday) every preset underforecast by 6 to 13 %; the recent-load features read the warmest week of the season and pulled the forecast 1.4 M kWh per period below the model's own similar days, and nothing in the feature set said the target day was 12 °C colder than those days. A tree model splits one column at a time: a forecast-minus-lag-day column is one split where today two columns and enough examples are needed.

## Follows from

Issue #230 (the pinned-window batch, whose e219 run is 8fb1b358) and the 2025-03-04 analysis in the spec.

## Expression

`LAG(MEAN(temperature_c, weight=population), 2d)` and its difference `MEAN(forecast_temperature_c, weight=population) - LAG(MEAN(temperature_c, weight=population), 2d)`; the same for D-7; `LAG(DAILY_MEAN(MEAN(temperature_c, weight=population)), 2d)` and the evening mean `time=18:00-22:00` with their differences; `EWA(MEAN(temperature_c, weight=population), gap=2d, window=4, halflife=1) by day_type` and `ROLLING_MEAN(…) by day_type` with their differences; `SIMILAR_DAY(MEAN(temperature_c, weight=population), gap=(2d, 335d), window=(30, 60), rank=r, holidays=similarity)` for r = 1 to 3, `SIMILAR_DAY_MEAN(…, k=3, weight=inverse_distance, holidays=similarity)` and their differences. The full list is section 5 of the spec.

## Source data

- fct_jma_weather_hourly (JMA observations)
- fct_jma_msm_weather_forecast_hourly (MSM forecast)
- fct_census_population_jma_station
- pma_ml.similar_day (the fit-and-score job), through ftr_period_similar_day's stored reference dates and distances
- a new curated fact, fct_area_weather_hourly, the hourly population-weighted observation with its coverage

## Grain

hour (D-2, D-7 siblings and deltas), day (the D-2 daily and evening means), period (the day-type window and the similar days).

## Mart

ftr_hour_jma_obs and ftr_hour_msm; ftr_day_msm and a new ftr_day_jma_obs; ftr_period_similar_day; a new ftr_period_daytype_weather reading the window dates ftr_period_actuals exposes.

## Categorical

No.

## Available at

An observation is public one hour after its hour, a load a day or more after, the vintage at 01:00 on D-1. A sibling never moves the availability of the row it joins; a delta lives in a mart whose row already waits for the vintage. No existing row's available_at changes.

## Build notes

Four PRs, each carrying this element and the radiation one: the fact with the hour marts; the day mart; the day-type window mart; the similar-day mart. No change to the similar-day job or its table. Three experiments follow, one preset file each on e219: temperature, radiation, both.

## Checks

- [x] Not already a mart column (the Feature Catalogue dashboard, or the marts' YAML)
- [x] Not among the set-aside ideas (closed feature-candidate issues)
- [x] The expression follows docs/Feature-Naming.md and no other feature has it
```

Run:

```bash
gh issue create --repo hankehly/power-market-analytics --label "feature candidate" \
  --title "Temperature of the load lag windows and its difference from the day's forecast" \
  --body-file /tmp/candidate-temperature.md
```

Expected: the issue URL; note its number as `<T>`.

- [ ] **Step 2: Open the radiation candidate**

Write `/tmp/candidate-radiation.md` with the same sections, these differences: **Suggested by:** "Claude, 2026-09-28, as the second element of the researcher's lag-window weather idea; the researcher approved it the same day." **Feature:** "the population-weighted observed solar radiation … 19 columns: the evening mean is left out because 18:00 to 22:00 is after sunset most of the year, and D's daily forecast radiation mean comes in with the batch." **Why it should help:** "On 2025-03-04 the three similar days matched the day on temperature to 1.5 °C and had three to four times its radiation (17.9, 12.1 and 15.7 MJ/m² against 4.9); the day's demand sat 2.0 M kWh per period above them through the afternoon and only 0.5 M below overnight. Mild working days (15 to 20 °C, 175 of them) put the effect at −1.1 GWh per day per MJ/m². Radiation is not in the similar-day distance." **Expression:** the temperature list with `solar_radiation_mjm2` and `forecast_solar_radiation_mjm2`, without the evening trio. **Build notes:** add "Radiation is recorded at 7 of Tokyo's 21 weighted stations and 3 of Kansai's 11, 55.4 % of each area's weight, the representative station carrying 75 % (東京) and 80 % (大阪) of that share; the fact carries the reporting share per hour and every column's description names the stations. A more representative radiation source is future work."

```bash
gh issue create --repo hankehly/power-market-analytics --label "feature candidate" \
  --title "Solar radiation of the load lag windows and its difference from the day's forecast" \
  --body-file /tmp/candidate-radiation.md
```

Expected: the issue URL; note its number as `<R>`.

- [ ] **Step 3: Set the Project's Task field on both**

```bash
gh project field-list 3 --owner hankehly --format json
```

Find the `Task` field id and its `demand` option id, then for each issue:

```bash
gh project item-list 3 --owner hankehly --format json --limit 200
gh project item-edit --project-id PVT_kwHOALGbus4BjoLc --id <item id> --field-id <Task field id> --single-select-option-id <demand option id>
```

Expected: no error. If the auto-add workflow has not admitted the issue yet, `item-list` will not show it; retry after a minute, and if it still is not there, leave the field for the researcher and say so in the PR body.

- [ ] **Step 4: Rename the branch after the issues**

```bash
git branch -m feature/lag-window-weather-siblings feature/issue-<T>-weather-siblings-hour-marts
git branch --show-current
```

Expected: `feature/issue-<T>-weather-siblings-hour-marts`.

---

### Task 1: `fct_area_weather_hourly`

**Files:**
- Create: `dbt/models/curated/fct_area_weather_hourly.sql`
- Create: `dbt/models/curated/fct_area_weather_hourly.yml`

**Interfaces:**
- Consumes: `fct_jma_weather_hourly` (`station_id`, `observed_at`, `observed_hour_start_at`, `date_key`, `temperature_c`, `solar_radiation_mjm2`, `available_at`), `fct_census_population_jma_station` (`census_year`, `area_key`, `station_id`, `area_population_weight`), `dim_area` (`area_key`).
- Produces: a table with `area_key int`, `date_key date`, `hour_ending int`, `observed_at timestamp`, `observed_hour_start_at timestamp`, `census_year int`, `popw_temperature_c double`, `n_stations_temperature int`, `weight_share_temperature double`, `popw_solar_radiation_mjm2 double`, `n_stations_solar_radiation int`, `weight_share_solar_radiation double`, `available_at timestamp`; key `(area_key, observed_at)`. Tasks 2 and 3 read `area_key`, `date_key`, `hour_ending`, `observed_hour_start_at`, the two `popw_` columns.

- [ ] **Step 1: Write the failing unit test**

Create `dbt/models/curated/fct_area_weather_hourly.yml`:

```yaml
models:
  - name: fct_area_weather_hourly
    config:
      contract:
        enforced: true
    description: >
      The area's population-weighted observed weather, one row per bidding zone and
      observation hour: an aggregate of fct_jma_weather_hourly over the area's staffed
      stations with fct_census_population_jma_station's weights of the latest census
      vintage, renormalised over the stations that report the element that hour and added
      in station order (the ordered_weighted_mean macro), as ftr_hour_msm weighs the
      forecast. Every feature that reads the weather of a past day reads it here, so the
      weighting is defined once. Grain: area_key x observed_at (the hour end, as
      fct_jma_weather_hourly places it; hour 24 on the day it ends). A row exists for every
      hour any weighted station has a row for; an element no station reports that hour is
      null with n_stations 0 and weight_share 0. Coverage differs by element: temperature is
      recorded at every weighted station; solar radiation at 7 of Tokyo's 21 (東京 0.417,
      つくば 0.046, 宇都宮 0.034, 前橋 0.034, 甲府 0.014, 銚子 0.010, 父島; 55.4 % of the
      weight, 横浜, 千葉 and 熊谷 the largest without) and 3 of Kansai's 11 (大阪 0.446, 奈良
      0.080, 彦根 0.029; 55.4 %, 京都, 神戸 and 姫路 the largest without), so a radiation
      mean is close to the representative station's value, and weight_share_solar_radiation
      says on every row how much of the area it rests on.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - area_key
              - observed_at
      - dbt_utils.expression_is_true:
          arguments:
            expression: "(popw_temperature_c is null) = (n_stations_temperature = 0)"
      - dbt_utils.expression_is_true:
          arguments:
            expression: "(popw_solar_radiation_mjm2 is null) = (n_stations_solar_radiation = 0)"
    columns:
      - name: area_key
        data_type: int
        description: Foreign key to dim_area.
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_area')
                field: area_key
      - name: date_key
        data_type: date
        description: >
          Foreign key to dim_date on the observation day, the hour-start date: hour 24
          stays on the day it measured.
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_key
      - name: hour_ending
        data_type: int
        description: Observation hour 1-24, hour ending, the JMA convention.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 24
      - name: observed_at
        data_type: timestamp
        description: End of the observation hour (JST), fct_jma_weather_hourly's.
        data_tests:
          - not_null
      - name: observed_hour_start_at
        data_type: timestamp
        description: Start of the observation hour, observed_at minus one hour.
        data_tests:
          - not_null
      - name: census_year
        data_type: int
        description: The census vintage whose weights the row uses, the latest loaded.
        data_tests:
          - not_null
      - name: popw_temperature_c
        data_type: double
        description: >
          The temperature weighted over the area's stations that report it this hour,
          renormalised over them, C; null when none does.
      - name: n_stations_temperature
        data_type: int
        description: How many weighted stations report a temperature this hour.
        data_tests:
          - not_null
      - name: weight_share_temperature
        data_type: double
        description: >
          The population weight of the stations that report a temperature this hour, 0 to
          1: the share of the area the mean rests on.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1
      - name: popw_solar_radiation_mjm2
        data_type: double
        description: >
          The global solar radiation over the hour weighted over the area's stations that
          record it, renormalised over them, MJ/m2; null when none does. Recorded at 7 of
          Tokyo's 21 weighted stations and 3 of Kansai's 11 (the model description), so
          the mean is 75 % (東京) or 80 % (大阪) the representative station's reading.
      - name: n_stations_solar_radiation
        data_type: int
        description: How many weighted stations report solar radiation this hour.
        data_tests:
          - not_null
      - name: weight_share_solar_radiation
        data_type: double
        description: >
          The population weight of the stations that report solar radiation this hour,
          0 to 1; 0.554 in Tokyo and Kansai when all of theirs do.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1
      - name: available_at
        data_type: timestamp
        description: >
          When the row became public, naive JST: the latest of its stations' rows,
          observed_at + 1 h under the standardized rule.
        data_tests:
          - not_null

unit_tests:
  - name: fct_area_weather_hourly_weights_renormalise_per_element
    description: >
      Two Tokyo stations with 2020 weights 0.75 (s1) and 0.25 (s2), a 2015 vintage that
      must be ignored, and s3 with rows but no census weight, which must not count. Hour 1:
      both report a temperature (10 and 20, so 12.5 over the whole weight), only s1 reports
      radiation (1.0 over its 0.75, renormalised to 1.0, share 0.75). Hour 2: neither
      reports a temperature (null, count 0, share 0) and s1 alone reports radiation 0.5.
      available_at is the stations' latest.
    model: fct_area_weather_hourly
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo}
      - input: ref('fct_census_population_jma_station')
        rows:
          - {census_year: 2015, area_key: 1, station_id: s1, area_population_weight: 1.0}
          - {census_year: 2020, area_key: 1, station_id: s1, area_population_weight: 0.75}
          - {census_year: 2020, area_key: 1, station_id: s2, area_population_weight: 0.25}
      - input: ref('fct_jma_weather_hourly')
        rows:
          - {station_id: s1, observed_at: "2025-03-01 01:00:00", observed_hour_start_at: "2025-03-01 00:00:00", date_key: 2025-03-01, temperature_c: 10.0, solar_radiation_mjm2: 1.0, available_at: "2025-03-01 02:00:00"}
          - {station_id: s2, observed_at: "2025-03-01 01:00:00", observed_hour_start_at: "2025-03-01 00:00:00", date_key: 2025-03-01, temperature_c: 20.0, solar_radiation_mjm2: null, available_at: "2025-03-01 02:00:00"}
          - {station_id: s3, observed_at: "2025-03-01 01:00:00", observed_hour_start_at: "2025-03-01 00:00:00", date_key: 2025-03-01, temperature_c: 99.0, solar_radiation_mjm2: 99.0, available_at: "2025-03-01 02:00:00"}
          - {station_id: s1, observed_at: "2025-03-01 02:00:00", observed_hour_start_at: "2025-03-01 01:00:00", date_key: 2025-03-01, temperature_c: null, solar_radiation_mjm2: 0.5, available_at: "2025-03-01 03:00:00"}
          - {station_id: s2, observed_at: "2025-03-01 02:00:00", observed_hour_start_at: "2025-03-01 01:00:00", date_key: 2025-03-01, temperature_c: null, solar_radiation_mjm2: null, available_at: "2025-03-01 03:00:00"}
    expect:
      rows:
        - {area_key: 1, date_key: 2025-03-01, hour_ending: 1, observed_at: "2025-03-01 01:00:00", observed_hour_start_at: "2025-03-01 00:00:00", census_year: 2020, popw_temperature_c: 12.5, n_stations_temperature: 2, weight_share_temperature: 1.0, popw_solar_radiation_mjm2: 1.0, n_stations_solar_radiation: 1, weight_share_solar_radiation: 0.75, available_at: "2025-03-01 02:00:00"}
        - {area_key: 1, date_key: 2025-03-01, hour_ending: 2, observed_at: "2025-03-01 02:00:00", observed_hour_start_at: "2025-03-01 01:00:00", census_year: 2020, popw_temperature_c: null, n_stations_temperature: 0, weight_share_temperature: 0.0, popw_solar_radiation_mjm2: 0.5, n_stations_solar_radiation: 1, weight_share_solar_radiation: 0.75, available_at: "2025-03-01 03:00:00"}
```

- [ ] **Step 2: Run the unit test to see it fail**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "fct_area_weather_hourly,test_type:unit"`
Expected: a parse error that `fct_area_weather_hourly` has a YAML entry but no model file (the SQL does not exist yet).

- [ ] **Step 3: Write the model**

Create `dbt/models/curated/fct_area_weather_hourly.sql`:

```sql
{#- The observed elements weighted over the area's stations, in output order. Each
    gets the same station-ordered weighted mean, named popw_<element>, with the
    count and the population share of the stations that reported it. #}
{%- set weighted_elements = ['temperature_c', 'solar_radiation_mjm2'] -%}

with
  -- The latest census vintage's station weights, as ftr_hour_msm takes them.
  weights as (
  select census_year, area_key, station_id, area_population_weight
  from {{ ref('fct_census_population_jma_station') }}
  where census_year = (select max(census_year) from {{ ref('fct_census_population_jma_station') }})
  ),

  -- Each element's stations that report it this hour, in station order: a fixed
  -- order whatever order Spark reads the rows in, so the sums below are the same
  -- on every build. A station without a weight (no census row) is not an input.
  hour_terms as (
  select
    weights.area_key,
    weights.census_year,
    weather.observed_at,
    weather.observed_hour_start_at,
    weather.date_key,
    hour(weather.observed_hour_start_at) + 1 as hour_ending,
    {%- for element in weighted_elements %}
    array_sort(collect_list(
      case when weather.{{ element }} is not null
        then named_struct(
          'station_id', weather.station_id,
          'weight', weights.area_population_weight,
          'value', weather.{{ element }}
        )
      end
    )) as {{ element }}_terms,
    {%- endfor %}
    max(weather.available_at) as available_at
  from
    {{ ref('fct_jma_weather_hourly') }} as weather
    inner join weights on weights.station_id = weather.station_id
  group by
    weights.area_key, weights.census_year, weather.observed_at, weather.observed_hour_start_at, weather.date_key
  ),

  final as (
  select
    area_key,
    date_key,
    hour_ending,
    observed_at,
    observed_hour_start_at,
    census_year,
    {%- for element in weighted_elements %}
    -- Added in station order, renormalised over the stations present; null when none is.
    {{ ordered_weighted_mean(element ~ '_terms') }} as popw_{{ element }},
    size({{ element }}_terms) as n_stations_{{ element }},
    aggregate({{ element }}_terms, cast(0 as double), (acc, x) -> acc + x.weight)
      as weight_share_{{ element }},
    {%- endfor %}
    available_at
  from
    hour_terms
  )

select * from final
```

- [ ] **Step 4: Run the unit test to see it pass**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "fct_area_weather_hourly,test_type:unit"`
Expected: `PASS 1`. If `size()` returns bigint under the contract, cast it: `cast(size(…) as int)`.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/curated/fct_area_weather_hourly.sql dbt/models/curated/fct_area_weather_hourly.yml
git commit -m "feat(dbt): fct_area_weather_hourly, the population-weighted observed hour with its coverage" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `ftr_hour_jma_obs` reads the fact, then gains the four siblings

**Files:**
- Modify: `dbt/models/features/ftr_hour_jma_obs.sql` (the `station_weights`, `area_hour_terms` and `area_hours` CTEs, lines 28–74; the `final` CTE, lines 168–192)
- Modify: `dbt/models/features/ftr_hour_jma_obs.yml`

**Interfaces:**
- Consumes: Task 1's fact (`area_key`, `date_key`, `hour_ending`, `observed_hour_start_at`, `popw_temperature_c`, `popw_solar_radiation_mjm2`); `dim_area` for `area_code`.
- Produces: the four sibling columns Task 3 joins on `(area_code, trade_date, hour_ending)`, plus `available_at`.

- [ ] **Step 1: Rewrite the second unit test's inputs to the fact**

In `dbt/models/features/ftr_hour_jma_obs.yml`, in `ftr_hour_jma_obs_population_weighted_24h_and_72h_windows`, replace the `fct_census_population_jma_station` input and the `fct_jma_weather_hourly` input with these two (the representative station's own rows stay in `fct_jma_weather_hourly`; the weighted hourly values move to the fact, with the same numbers the old two-station input produced: s2 + 1, the 03-01 10:00 row missing, 03-03 07:00 the renormalised s2-alone value 16.5):

```yaml
      - input: ref('fct_jma_weather_hourly')
        rows:
          - {station_id: s1, date_key: 2025-03-03, observed_hour_start_at: "2025-03-03 23:00:00", temperature_c: 8.0}
          - {station_id: s1, date_key: 2025-03-04, observed_hour_start_at: "2025-03-04 23:00:00", temperature_c: 9.0}
      - input: ref('fct_area_weather_hourly')
        format: sql
        rows: |
          with hours as (
            select explode(sequence(
              timestamp '2025-03-01 00:00:00', timestamp '2025-03-05 23:00:00', interval 1 hour)) as ts
          )
          select 1 as area_key, cast(ts as date) as date_key, hour(ts) + 1 as hour_ending,
            ts as observed_hour_start_at,
            case when ts = timestamp '2025-03-03 07:00:00'
              then cast(10 + hour(ts) * 0.5 + day(ts) as double)
              else cast(11 + hour(ts) * 0.5 + day(ts) as double) end as popw_temperature_c,
            cast(null as double) as popw_solar_radiation_mjm2
          from hours where ts <> timestamp '2025-03-01 10:00:00'
```

Update its description: the fact now carries the weighted hourly value; the renormalised hour is the fact's job (its own unit test), so the input states 16.5 outright. In the first unit test, `ftr_hour_jma_obs_recency_weights_renormalisation_and_shift`, replace the `fct_census_population_jma_station` input (`rows: []`) with `- input: ref('fct_area_weather_hourly')` and `rows: []`.

- [ ] **Step 2: Run the mart's unit tests to see them fail**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "ftr_hour_jma_obs,test_type:unit"`
Expected: both fail, because the model does not yet reference `fct_area_weather_hourly` (dbt refuses a `given` input the model does not depend on).

- [ ] **Step 3: Read the fact in the model**

In `dbt/models/features/ftr_hour_jma_obs.sql`, delete the `station_weights`, `area_hour_terms` and `area_hours` CTEs and put this in their place:

```sql
  -- The area's population-weighted observed hour, from the curated fact, which
  -- weighs the stations as ftr_hour_msm weighs the forecast (the weighting was
  -- computed here until 2026-09-29; the fact gives the same value to the bit).
  -- hour_index counts the hours along the clock, so a window can cross midnight.
  area_hours as (
  select
    all_areas.area_code,
    fact.date_key as obs_date,
    fact.hour_ending,
    cast(div(unix_timestamp(fact.observed_hour_start_at), 3600) as bigint) as hour_index,
    fact.popw_temperature_c,
    fact.popw_solar_radiation_mjm2
  from
    {{ ref('fct_area_weather_hourly') }} as fact
    inner join {{ ref('dim_area') }} as all_areas on all_areas.area_key = fact.area_key
  ),
```

The `hour_windows` and `accumulated` CTEs read `area_hours` unchanged.

- [ ] **Step 4: Run the mart's unit tests to see them pass**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "ftr_hour_jma_obs,test_type:unit"`
Expected: `PASS 2`, the expected doubles unchanged (19.708333333333332, 20.75, 19.73611111111111, 20.854779715085353, 21.75, 20.73611111111111, 21.85990364459824).

- [ ] **Step 5: Commit the refactor on its own**

```bash
git add dbt/models/features/ftr_hour_jma_obs.sql dbt/models/features/ftr_hour_jma_obs.yml
git commit -m "refactor(dbt): ftr_hour_jma_obs reads its weighted hour from fct_area_weather_hourly" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Write the failing unit test for the siblings**

Append to `unit_tests:` in `dbt/models/features/ftr_hour_jma_obs.yml`:

```yaml
  - name: ftr_hour_jma_obs_lag_2d_and_7d_weather_siblings
    description: >
      The representative station's hour-1 readings on 2025-03-07 and 03-08 make the
      mart's rows for 03-09 to 03-16. The fact holds hour 1 of 03-07 (12 C, 0.3 MJ/m2)
      and of 03-02 (9 C, 0.1). 03-09 reads 03-07 as D-2 and 03-02 as D-7; 03-14 reads
      03-07 as D-7; every other row has no fact hour two or seven days back and is null.
      wavg_temperature_c and available_at are the first test's, untouched.
    model: ftr_hour_jma_obs
    given:
      - input: ref('dim_area')
        rows:
          - {area_key: 1, area_code: tokyo, representative_jma_station_id: s1}
      - input: ref('fct_jma_weather_hourly')
        rows:
          - {station_id: s1, date_key: 2025-03-07, observed_hour_start_at: "2025-03-07 00:00:00", temperature_c: 11.0}
          - {station_id: s1, date_key: 2025-03-08, observed_hour_start_at: "2025-03-08 00:00:00", temperature_c: 5.0}
      - input: ref('fct_area_weather_hourly')
        rows:
          - {area_key: 1, date_key: 2025-03-07, hour_ending: 1, observed_hour_start_at: "2025-03-07 00:00:00", popw_temperature_c: 12.0, popw_solar_radiation_mjm2: 0.3}
          - {area_key: 1, date_key: 2025-03-02, hour_ending: 1, observed_hour_start_at: "2025-03-02 00:00:00", popw_temperature_c: 9.0, popw_solar_radiation_mjm2: 0.1}
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-09, hour_ending: 1, wavg_temperature_c: 11.0, lag_2d_popw_temperature_c: 12.0, lag_7d_popw_temperature_c: 9.0, lag_2d_popw_solar_radiation_mjm2: 0.3, lag_7d_popw_solar_radiation_mjm2: 0.1, available_at: "2025-03-07 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 1, wavg_temperature_c: 7.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: null, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-08 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-11, hour_ending: 1, wavg_temperature_c: 7.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: null, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-09 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-12, hour_ending: 1, wavg_temperature_c: 7.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: null, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-10 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-13, hour_ending: 1, wavg_temperature_c: 7.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: null, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-11 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-14, hour_ending: 1, wavg_temperature_c: 7.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: 12.0, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: 0.3, available_at: "2025-03-12 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-15, hour_ending: 1, wavg_temperature_c: 7.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: null, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-13 02:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-16, hour_ending: 1, wavg_temperature_c: 5.0, lag_2d_popw_temperature_c: null, lag_7d_popw_temperature_c: null, lag_2d_popw_solar_radiation_mjm2: null, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-14 02:00:00"}
```

Add the four columns to the contract, after `ewm_72h_popw_temperature_c` and before `available_at`:

```yaml
      - name: lag_2d_popw_temperature_c
        data_type: double
        description: >
          The area's population-weighted observed temperature at this hour on D-2, C, from
          fct_area_weather_hourly: the weather the load lag LAG(demand_kwh, 2d) was recorded
          under (the researcher's lag-window weather idea, 2026-09-28; spec
          2026-09-29-lag-window-weather-siblings). Null when no weighted station reported
          that hour. Its difference from D's forecast is ftr_hour_msm.delta_lag_2d_popw_temperature_c.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(MEAN(temperature_c, weight=population), 2d)"
      - name: lag_7d_popw_temperature_c
        data_type: double
        description: >
          The same at this hour on D-7: the weather under LAG(demand_kwh, 7d).
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(MEAN(temperature_c, weight=population), 7d)"
      - name: lag_2d_popw_solar_radiation_mjm2
        data_type: double
        description: >
          The area's population-weighted observed solar radiation over this hour on D-2,
          MJ/m2, from fct_area_weather_hourly, weighted over the stations that record it: 7
          of Tokyo's 21 weighted stations (東京 three quarters of their weight; 横浜, 千葉
          and 熊谷 the largest without) and 3 of Kansai's 11 (大阪 four fifths; 京都, 神戸
          and 姫路 without), 55.4 % of each area's weight, so the value is close to the
          representative station's; fct_area_weather_hourly.weight_share_solar_radiation
          gives the hour's share. A more representative radiation source is future work.
          Null when none of them reported the hour.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(MEAN(solar_radiation_mjm2, weight=population), 2d)"
      - name: lag_7d_popw_solar_radiation_mjm2
        data_type: double
        description: >
          The same over this hour on D-7.
        config:
          meta:
            feature: true
            categorical: false
            expression: "LAG(MEAN(solar_radiation_mjm2, weight=population), 7d)"
```

Also extend the model description with one sentence: "Since 2026-09-29 the weighted hour comes from fct_area_weather_hourly, and the mart carries the D-2 and D-7 population-weighted temperature and solar radiation at the hour, the weather the two load lags were recorded under."

- [ ] **Step 7: Run the new unit test to see it fail**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "ftr_hour_jma_obs,test_type:unit"`
Expected: the new test fails on the missing columns (the contract names columns the model does not produce, so the run may stop at the contract check; either way, RED).

- [ ] **Step 8: Add the siblings to `final`**

Replace the `final` CTE:

```sql
  final as (
  select
    windows.area_code,
    windows.trade_date,
    windows.hour_ending,
    -- Added in lag order, renormalised over the lags present; null when none is.
    {{ ordered_weighted_mean('windows.terms') }} as wavg_temperature_c,
    accumulated.mean_24h_popw_temperature_c,
    accumulated.mean_72h_popw_temperature_c,
    accumulated.ewm_72h_popw_temperature_c,
    -- The weather the load lags were recorded under: the fact at the same hour
    -- two and seven days back. Null where the fact has no such hour.
    lag_2d.popw_temperature_c as lag_2d_popw_temperature_c,
    lag_7d.popw_temperature_c as lag_7d_popw_temperature_c,
    lag_2d.popw_solar_radiation_mjm2 as lag_2d_popw_solar_radiation_mjm2,
    lag_7d.popw_solar_radiation_mjm2 as lag_7d_popw_solar_radiation_mjm2,
    -- Public once the newest observation the window can hold, D-2's, is:
    -- its hour end + 1 h. The accumulated windows and the D-2 sibling end at
    -- the same hour, and the D-7 sibling is older, so the same instant holds.
    timestampadd(hour, windows.hour_ending + 1, cast(date_sub(windows.trade_date, 2) as timestamp)) as available_at
  from
    windows
    left join accumulated
      on accumulated.area_code = windows.area_code
      and accumulated.trade_date = windows.trade_date
      and accumulated.hour_ending = windows.hour_ending
    left join area_hours as lag_2d
      on lag_2d.area_code = windows.area_code
      and lag_2d.obs_date = date_sub(windows.trade_date, 2)
      and lag_2d.hour_ending = windows.hour_ending
    left join area_hours as lag_7d
      on lag_7d.area_code = windows.area_code
      and lag_7d.obs_date = date_sub(windows.trade_date, 7)
      and lag_7d.hour_ending = windows.hour_ending
  )
```

- [ ] **Step 9: Run the mart's unit tests to see them pass**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "ftr_hour_jma_obs,test_type:unit"`
Expected: `PASS 3`.

- [ ] **Step 10: Commit**

```bash
git add dbt/models/features/ftr_hour_jma_obs.sql dbt/models/features/ftr_hour_jma_obs.yml
git commit -m "feat(dbt): the D-2 and D-7 population-weighted temperature and radiation in ftr_hour_jma_obs" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The four deltas in `ftr_hour_msm`

**Files:**
- Modify: `dbt/models/features/ftr_hour_msm.sql` (the `final` CTE, lines 121–151)
- Modify: `dbt/models/features/ftr_hour_msm.yml`

**Interfaces:**
- Consumes: Task 2's four sibling columns and `available_at` from `ftr_hour_jma_obs`, keyed `(area_code, trade_date, hour_ending)`.
- Produces: `delta_lag_2d_popw_temperature_c`, `delta_lag_7d_popw_temperature_c`, `delta_lag_2d_popw_solar_radiation_mjm2`, `delta_lag_7d_popw_solar_radiation_mjm2`, doubles, tagged.

- [ ] **Step 1: Give the three existing unit tests the new input**

In `dbt/models/features/ftr_hour_msm.yml`, add to the `given:` of each of the three unit tests:

```yaml
      - input: ref('ftr_hour_jma_obs')
        rows: []
```

- [ ] **Step 2: Write the failing unit test for the deltas**

Append to `unit_tests:`:

```yaml
  - name: ftr_hour_msm_deltas_to_the_lag_day_weather
    description: >
      One Tokyo station carrying the whole weight, two forecast hours of one vintage.
      Hour 1 has a sibling row (D-2 13 C and 0.5 MJ/m2, D-7 7 C and no radiation): the
      deltas are the forecast minus it, 10 - 13, 10 - 7, 2.0 - 0.5, and null for the
      missing radiation. Hour 2 has no sibling row: every delta is null and the forecast
      row stays. available_at stays the vintage's, 01:00 on D-1, on both: the sibling's
      02:00 on D-2 is earlier, and greatest() skips the null one.
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
          - {station_id: s1, date_key: 2025-03-10, forecast_hour_start_at: "2025-03-10 00:00:00", forecast_reference_at: "2025-03-08 21:00:00", temperature_c: 10.0, solar_radiation_mjm2: 2.0, available_at: "2025-03-09 01:00:00"}
          - {station_id: s1, date_key: 2025-03-10, forecast_hour_start_at: "2025-03-10 01:00:00", forecast_reference_at: "2025-03-08 21:00:00", temperature_c: 8.0, solar_radiation_mjm2: 1.5, available_at: "2025-03-09 01:00:00"}
      - input: ref('ftr_hour_jma_obs')
        rows:
          - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 1, lag_2d_popw_temperature_c: 13.0, lag_7d_popw_temperature_c: 7.0, lag_2d_popw_solar_radiation_mjm2: 0.5, lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-08 02:00:00"}
    expect:
      rows:
        - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 1, forecast_reference_at: "2025-03-08 21:00:00", popw_forecast_temperature_c: 10.0, delta_lag_2d_popw_temperature_c: -3.0, delta_lag_7d_popw_temperature_c: 3.0, delta_lag_2d_popw_solar_radiation_mjm2: 1.5, delta_lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-09 01:00:00"}
        - {area_code: tokyo, trade_date: 2025-03-10, hour_ending: 2, forecast_reference_at: "2025-03-08 21:00:00", popw_forecast_temperature_c: 8.0, delta_lag_2d_popw_temperature_c: null, delta_lag_7d_popw_temperature_c: null, delta_lag_2d_popw_solar_radiation_mjm2: null, delta_lag_7d_popw_solar_radiation_mjm2: null, available_at: "2025-03-09 01:00:00"}
```

Add the four columns to the contract after `cum_popw_forecast_solar_radiation_mjm2`:

```yaml
      - name: delta_lag_2d_popw_temperature_c
        data_type: double
        description: >
          D's population-weighted forecast temperature at this hour minus the area's
          population-weighted observed temperature at the same hour on D-2
          (ftr_hour_jma_obs.lag_2d_popw_temperature_c), C: how far the target hour's
          weather sits from the weather the load lag LAG(demand_kwh, 2d) was recorded
          under; positive when D is warmer. The researcher's lag-window weather idea of
          2026-09-28 (spec 2026-09-29-lag-window-weather-siblings). It lives here, not in
          the observation mart, because a delta needs the vintage, public at 01:00 on
          D-1, which this row already waits for. Null without a sibling row.
        config:
          meta:
            feature: true
            categorical: false
            expression: "MEAN(forecast_temperature_c, weight=population) - LAG(MEAN(temperature_c, weight=population), 2d)"
      - name: delta_lag_7d_popw_temperature_c
        data_type: double
        description: >
          The same against the hour on D-7 (ftr_hour_jma_obs.lag_7d_popw_temperature_c).
        config:
          meta:
            feature: true
            categorical: false
            expression: "MEAN(forecast_temperature_c, weight=population) - LAG(MEAN(temperature_c, weight=population), 7d)"
      - name: delta_lag_2d_popw_solar_radiation_mjm2
        data_type: double
        description: >
          D's population-weighted forecast solar radiation over this hour minus the
          observed one on D-2 (ftr_hour_jma_obs.lag_2d_popw_solar_radiation_mjm2), MJ/m2;
          positive when D is forecast brighter. The observed side is weighted over the
          stations that record radiation, 55.4 % of the area (that column's description);
          the forecast side over every station.
        config:
          meta:
            feature: true
            categorical: false
            expression: "MEAN(forecast_solar_radiation_mjm2, weight=population) - LAG(MEAN(solar_radiation_mjm2, weight=population), 2d)"
      - name: delta_lag_7d_popw_solar_radiation_mjm2
        data_type: double
        description: >
          The same against the hour on D-7.
        config:
          meta:
            feature: true
            categorical: false
            expression: "MEAN(forecast_solar_radiation_mjm2, weight=population) - LAG(MEAN(solar_radiation_mjm2, weight=population), 7d)"
```

Update `available_at`'s description: "the vintage's, reference + 4 h, or the D-2 sibling's if later, which it never is (the sibling's newest hour is public at 01:00 on D-1 at the latest)."

- [ ] **Step 3: Run the mart's unit tests to see the new one fail**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "ftr_hour_msm,test_type:unit"`
Expected: the new test fails (the model has no `ftr_hour_jma_obs` dependency and no delta columns); the three others may fail for the same dependency reason. RED.

- [ ] **Step 4: Join the siblings in the model**

In `dbt/models/features/ftr_hour_msm.sql`, rename the existing `final` CTE to `forecast` (its body unchanged) and add after it:

```sql
  -- The weather the load lags were recorded under, at the same hour on D-2 and D-7.
  observed as (
  select
    area_code,
    trade_date,
    hour_ending,
    lag_2d_popw_temperature_c,
    lag_7d_popw_temperature_c,
    lag_2d_popw_solar_radiation_mjm2,
    lag_7d_popw_solar_radiation_mjm2,
    available_at
  from {{ ref('ftr_hour_jma_obs') }}
  ),

  final as (
  select
    forecast.area_code,
    forecast.trade_date,
    forecast.hour_ending,
    forecast.forecast_reference_at,
    forecast.census_year,
    forecast.forecast_temperature_c,
    {%- for element in weighted_elements %}
    forecast.popw_forecast_{{ element }},
    {%- endfor %}
    forecast.popw_forecast_discomfort_index,
    forecast.cum_popw_forecast_solar_radiation_mjm2,
    -- D's forecast minus the observed hour two and seven days earlier: positive
    -- when D is warmer or brighter than the days the load lags come from. They
    -- live here because a delta needs the vintage, which this row already waits
    -- for; null where the observation mart has no row for the hour.
    forecast.popw_forecast_temperature_c - observed.lag_2d_popw_temperature_c
      as delta_lag_2d_popw_temperature_c,
    forecast.popw_forecast_temperature_c - observed.lag_7d_popw_temperature_c
      as delta_lag_7d_popw_temperature_c,
    forecast.popw_forecast_solar_radiation_mjm2 - observed.lag_2d_popw_solar_radiation_mjm2
      as delta_lag_2d_popw_solar_radiation_mjm2,
    forecast.popw_forecast_solar_radiation_mjm2 - observed.lag_7d_popw_solar_radiation_mjm2
      as delta_lag_7d_popw_solar_radiation_mjm2,
    -- The vintage's instant: the sibling's is never later (greatest skips a null one).
    {{ available_at(['forecast.available_at', 'observed.available_at']) }} as available_at
  from
    forecast
    left join observed
      on observed.area_code = forecast.area_code
      and observed.trade_date = forecast.trade_date
      and observed.hour_ending = forecast.hour_ending
  )
```

- [ ] **Step 5: Run the mart's unit tests to see them pass**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "ftr_hour_msm,test_type:unit"`
Expected: `PASS 4`.

- [ ] **Step 6: Run every unit test of the three models and the dependents**

Run: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt test --select "fct_area_weather_hourly ftr_hour_jma_obs ftr_hour_msm ftr_day_msm,test_type:unit"`
Expected: all PASS (`ftr_day_msm`'s tests give `ftr_hour_msm` rows in dict form, which tolerate the new columns).

- [ ] **Step 7: Commit**

```bash
git add dbt/models/features/ftr_hour_msm.sql dbt/models/features/ftr_hour_msm.yml
git commit -m "feat(dbt): the forecast-minus-D-2 and D-7 temperature and radiation deltas in ftr_hour_msm" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The generated files, the fixture and the Python suite

**Files:**
- Regenerate: `power_market_analytics/features/views.py`, `dbt/models/curated/fct_feature_value.sql`, `dbt/models/curated/dim_feature.sql`
- Modify: `tests/conftest.py` (the `jma_rows` loop at lines 1483–1503, the `msm_rows` comprehension at lines 1504–1560, the two `write_table` schema strings at lines 1972–1988)

**Interfaces:**
- Consumes: the eight tagged columns of Tasks 2 and 3.
- Produces: synthetic marts whose columns match the generated views, so `test_feature_value_fact` and every Feast retrieval in the suite run.

- [ ] **Step 1: Regenerate the three files**

```bash
cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse && cd .. && uv run python scripts/generate_feature_views.py
git status --short
```

Expected: the three generated files modified; `views.py` gains four `Field`s on `FTR_HOUR_JMA_OBS` and four on `FTR_HOUR_MSM`, each tagged with its expression. The generator refuses a duplicate name or expression, so a refusal here means a typo in a YAML `expression`.

- [ ] **Step 2: Run the suite to see the fixture fail**

Run: `uv run pytest tests/test_feature_value_fact.py -x -q`
Expected: FAIL, the generated `fct_feature_value.sql` selects `lag_2d_popw_temperature_c` from a synthetic `ftr_hour_jma_obs` that lacks it.

- [ ] **Step 3: Give the fixture the eight columns**

In `tests/conftest.py`, add after `accumulated_temperature` (before `popw_forecast`):

```python
def lag_popw_temperature(day: pd.Timestamp, hour_ending: int, lag_days: int) -> float | None:
    """``ftr_hour_jma_obs.lag_<k>d_popw_temperature_c`` of the fixture for a delivery-day hour.

    Only the representative station observes in the fixture, so the area's
    population-weighted temperature at the hour on D-k is its
    ``synthetic_temperature``; None outside ``DEMAND_DAYS`` or in
    ``TEMPERATURE_MISSING_HOURS``. The fixture records no solar radiation, so the
    radiation siblings are None everywhere.
    """
    obs_day = day - pd.Timedelta(days=lag_days)
    if obs_day not in DEMAND_DAYS or (obs_day, hour_ending) in TEMPERATURE_MISSING_HOURS:
        return None
    return synthetic_temperature(obs_day, hour_ending)
```

In the `jma_rows` loop, add to each row dict, after `**accumulated_temperature(day, hour),`:

```python
                    "lag_2d_popw_temperature_c": lag_popw_temperature(day, hour, 2),
                    "lag_7d_popw_temperature_c": lag_popw_temperature(day, hour, 7),
                    "lag_2d_popw_solar_radiation_mjm2": None,
                    "lag_7d_popw_solar_radiation_mjm2": None,
```

and extend the `nullable_column` loop's tuple with those four names. In the `msm_rows` comprehension, add after `"popw_forecast_discomfort_index": …,` (the deltas are the fixture's forecast minus the sibling, None without one; radiation deltas None because the fixture observes no radiation):

```python
            "delta_lag_2d_popw_temperature_c": (
                None
                if lag_popw_temperature(day, hour, 2) is None
                else popw_forecast(
                    day, hour, synthetic_forecast_temperature(day, hour), SECOND_STATION_FORECAST_OFFSET_C
                )
                - lag_popw_temperature(day, hour, 2)
            ),
            "delta_lag_7d_popw_temperature_c": (
                None
                if lag_popw_temperature(day, hour, 7) is None
                else popw_forecast(
                    day, hour, synthetic_forecast_temperature(day, hour), SECOND_STATION_FORECAST_OFFSET_C
                )
                - lag_popw_temperature(day, hour, 7)
            ),
            "delta_lag_2d_popw_solar_radiation_mjm2": None,
            "delta_lag_7d_popw_solar_radiation_mjm2": None,
```

Update the two schema strings: `ftr_hour_jma_obs` gains `"lag_2d_popw_temperature_c double, lag_7d_popw_temperature_c double, lag_2d_popw_solar_radiation_mjm2 double, lag_7d_popw_solar_radiation_mjm2 double, "` before `available_at timestamp`; `ftr_hour_msm` gains `"delta_lag_2d_popw_temperature_c double, delta_lag_7d_popw_temperature_c double, delta_lag_2d_popw_solar_radiation_mjm2 double, delta_lag_7d_popw_solar_radiation_mjm2 double, "` before `available_at timestamp`. If `pd.DataFrame(msm_rows)` types an all-None column as object, pass it through `nullable_column(..., float)` as the `jma_obs` columns are.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest --cov --cov-report=term-missing -q`
Expected: all pass, coverage 100 %. `tests/test_feature_views.py` and `tests/test_feature_store.py` enumerate marts, not fields, so they need no change; if a test pins the field list of a hour view, extend it with the new names in view order.

- [ ] **Step 5: Lint, types, the generator's check, the docs links**

```bash
uv run ruff check . && uv run ruff format --check tests/conftest.py
uv run mypy
uv run python scripts/generate_feature_views.py --check
uv run python scripts/check_docs_links.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/features/views.py dbt/models/curated/fct_feature_value.sql dbt/models/curated/dim_feature.sql tests/conftest.py
git commit -m "feat(forecasting): the D-2 and D-7 weather siblings and deltas in the Feast views, the catalogue and the fixture" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The warehouse proof

**Files:**
- Create: `scratch/2026-09-29-weather-siblings-hour-marts/README.md`, `before.sql`, `diff.sql`, `after.sql`, `feast_check.py` (gitignored scratch; the outputs go to the PR body)

- [ ] **Step 1: Copy the two marts before the build**

Write `scratch/2026-09-29-weather-siblings-hour-marts/before.sql`:

```sql
create table pma_scratch.wsib1_ftr_hour_jma_obs_before as select * from pma_features.ftr_hour_jma_obs;
create table pma_scratch.wsib1_ftr_hour_msm_before as select * from pma_features.ftr_hour_msm;
select count(*) from pma_scratch.wsib1_ftr_hour_jma_obs_before;
select count(*) from pma_scratch.wsib1_ftr_hour_msm_before;
```

Run it through beeline from the worktree:

```bash
docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics exec -T thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin --silent=true --outputformat=tsv2 -f /dev/stdin < scratch/2026-09-29-weather-siblings-hour-marts/before.sql
```

Expected: two counts; note them.

- [ ] **Step 2: Build the fact and everything downstream**

Run, as a background task (several minutes): `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt build --select "fct_area_weather_hourly+ dim_feature"`
Expected: PASS on every model and test, the singular tests `assert_dim_feature_covers_every_tagged_column` and `assert_fct_feature_value_covers_every_tagged_column` included. Note the fact's row count from the run log.

- [ ] **Step 3: Diff every existing column against the copies**

Write `diff.sql`, one block per mart; for `ftr_hour_jma_obs` list its seven old columns, for `ftr_hour_msm` its twenty (the `columns:` of each YAML before this PR, `available_at` included):

```sql
select
  count(*) as rows_before_and_after,
  sum(case when a.area_code is null or b.area_code is null then 1 else 0 end) as unmatched_rows,
  sum(case when not (a.wavg_temperature_c <=> b.wavg_temperature_c) then 1 else 0 end) as wavg_temperature_c_moved,
  sum(case when not (a.mean_24h_popw_temperature_c <=> b.mean_24h_popw_temperature_c) then 1 else 0 end) as mean_24h_moved,
  sum(case when not (a.mean_72h_popw_temperature_c <=> b.mean_72h_popw_temperature_c) then 1 else 0 end) as mean_72h_moved,
  sum(case when not (a.ewm_72h_popw_temperature_c <=> b.ewm_72h_popw_temperature_c) then 1 else 0 end) as ewm_72h_moved,
  sum(case when not (a.available_at <=> b.available_at) then 1 else 0 end) as available_at_moved
from pma_scratch.wsib1_ftr_hour_jma_obs_before a
full outer join pma_features.ftr_hour_jma_obs b
  on b.area_code = a.area_code and b.trade_date = a.trade_date and b.hour_ending = a.hour_ending;
```

and the same shape for `ftr_hour_msm` joined on `area_code, trade_date, hour_ending, forecast_reference_at` with one `_moved` sum per old column. Run it through beeline as in Step 1.
Expected: `unmatched_rows` 0 and every `_moved` 0 on both marts. Any non-zero is a defect in Task 2 or 3; stop and fix before going on.

- [ ] **Step 4: The acceptance numbers**

Write `after.sql`:

```sql
select hour_ending, popw_temperature_c, n_stations_temperature, weight_share_temperature,
       popw_solar_radiation_mjm2, n_stations_solar_radiation, weight_share_solar_radiation
from pma_curated.fct_area_weather_hourly f join pma_curated.dim_area d on d.area_key = f.area_key
where d.area_code = 'tokyo' and f.date_key = date '2025-03-04' and hour_ending in (6, 12, 15) order by hour_ending;
select o.hour_ending, o.lag_2d_popw_temperature_c, o.lag_7d_popw_temperature_c, o.lag_2d_popw_solar_radiation_mjm2,
       m.popw_forecast_temperature_c, m.delta_lag_2d_popw_temperature_c, m.delta_lag_7d_popw_temperature_c, m.delta_lag_2d_popw_solar_radiation_mjm2, m.available_at
from pma_features.ftr_hour_jma_obs o join pma_features.ftr_hour_msm m
  on m.area_code = o.area_code and m.trade_date = o.trade_date and m.hour_ending = o.hour_ending
where o.area_code = 'tokyo' and o.trade_date = date '2025-03-04' and o.hour_ending in (6, 12, 15) order by o.hour_ending;
select count(*) as rows_with_two_vintages from (
  select area_code, trade_date, hour_ending from pma_features.ftr_hour_msm group by 1, 2, 3 having count(*) > 1);
```

Expected: on 2025-03-04 Tokyo hour 12, `weight_share_solar_radiation` about 0.554, `delta_lag_2d_popw_temperature_c` between −16 and −8 (a 4 to 5 °C forecast against a 17 to 18 °C observation on 03-02), `delta_lag_2d_popw_solar_radiation_mjm2` negative, `available_at` 2025-03-03 01:00; `rows_with_two_vintages` 0. Put the numbers in the PR body's Evidence.

- [ ] **Step 5: Read the eight columns through Feast**

Write `feast_check.py`:

```python
"""Retrieve the eight new columns for Tokyo through Feast, as a preset would."""

import pandas as pd

from power_market_analytics.common.spark import get_spark_session
from power_market_analytics.features.retrieval import entity_frame, historical_features
from power_market_analytics.features.store import open_store
from power_market_analytics.tasks.demand import TASK

spark = get_spark_session("weather-siblings-feast-check")
spark.conf.set("spark.sql.session.timeZone", "UTC")
store = open_store()
days = pd.date_range("2025-03-02", "2025-03-05", freq="D")
features = [
    "ftr_hour_jma_obs:lag_2d_popw_temperature_c",
    "ftr_hour_jma_obs:lag_7d_popw_temperature_c",
    "ftr_hour_jma_obs:lag_2d_popw_solar_radiation_mjm2",
    "ftr_hour_jma_obs:lag_7d_popw_solar_radiation_mjm2",
    "ftr_hour_msm:delta_lag_2d_popw_temperature_c",
    "ftr_hour_msm:delta_lag_7d_popw_temperature_c",
    "ftr_hour_msm:delta_lag_2d_popw_solar_radiation_mjm2",
    "ftr_hour_msm:delta_lag_7d_popw_solar_radiation_mjm2",
]
frame = historical_features(store, entity_frame("tokyo", days, TASK.issue_offset), features)
print(frame.df.shape)
print(frame.df[frame.df["time_code"].isin([12, 24, 30])].to_string())
print(frame.df.isna().sum().to_string())
```

Check `retrieval.py` for the exact names of `entity_frame` and `historical_features` and the frame attribute (`.df` or the frame itself) before running; adjust the script to them. Run it in the devcontainer from the worktree:

```bash
docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics exec -T -w /workspace/.claude/worktrees/lag-window-weather-siblings -e PYTHONPATH=/workspace/.claude/worktrees/lag-window-weather-siblings devcontainer python - < scratch/2026-09-29-weather-siblings-hour-marts/feast_check.py
```

Expected: 192 rows (4 days × 48), the temperature columns non-null on every row, the radiation columns non-null, and the values at 2025-03-04 period 24 equal to Step 4's hour 12 row.

- [ ] **Step 6: Drop the copies and record the results**

Run through beeline: `drop table pma_scratch.wsib1_ftr_hour_jma_obs_before; drop table pma_scratch.wsib1_ftr_hour_msm_before;`. Write the README:

```
PR 1 of the lag-window weather siblings (spec 2026-09-29): the scratch copies, the null-safe diff proving no existing column of ftr_hour_jma_obs / ftr_hour_msm moved, the 2025-03-04 acceptance numbers and the Feast read-through. Outputs pasted into the PR body.
```

---

### Task 6: Docs and the pull request

**Files:**
- Modify: `CLAUDE.md` (the dbt marts bullet's `ftr_hour_jma_obs` and `ftr_hour_msm` entries near line 810; the JMA weather architecture bullet; the "five hand-kept places" note is unchanged since no mart was added)
- Create: the PR body file in the scratch directory

- [ ] **Step 1: Document the fact and the columns**

In `CLAUDE.md`, in the Architecture section's JMA weather bullet, add after the `pma_raw.jma_hourly_staffed` sentence: "`fct_area_weather_hourly` (curated, since 2026-09-29) is the area-grain rollup of `fct_jma_weather_hourly`: the population-weighted observed temperature and solar radiation per hour with, per element, the count and the population share of the stations that reported it — the one definition of the observed weighting, which `ftr_hour_jma_obs` reads (it computed the same means inside its own SQL until then, equal to the bit). Radiation is recorded at 7 of Tokyo's 21 and 3 of Kansai's 11 weighted stations, 55.4 % of each area's weight." In the marts bullet, extend the `ftr_hour_jma_obs` entry: "since 2026-09-29 also `lag_2d_popw_temperature_c`, `lag_7d_popw_temperature_c`, `lag_2d_popw_solar_radiation_mjm2` and `lag_7d_popw_solar_radiation_mjm2`, the fact at the same hour two and seven days back — the weather the load lags were recorded under (the researcher's lag-window weather idea, spec `docs/superpowers/specs/2026-09-29-lag-window-weather-siblings-design.md`, PR 1 of 4)", and the `ftr_hour_msm` entry: "since 2026-09-29 also the four `delta_lag_<2|7>d_popw_<temperature_c|solar_radiation_mjm2>` columns, D's forecast minus the sibling of `ftr_hour_jma_obs` at the same hour — placed here because a delta needs the vintage, so no `available_at` of the observation mart moves; in no preset yet". Keep each addition to the sentences above.

- [ ] **Step 2: Run the docs and generated-file checks once more, commit**

```bash
uv run python scripts/check_docs_links.py && uv run python scripts/generate_feature_views.py --check
git add CLAUDE.md docs/superpowers/plans/2026-09-29-lag-window-weather-siblings-1-hour-marts.md
git commit -m "docs(dbt): fct_area_weather_hourly and the hour marts' weather siblings in CLAUDE.md; the part-1 plan" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open the PR**

Write the body in the shape of `.github/pull_request_template.md` to `scratch/2026-09-29-weather-siblings-hour-marts/pr-body.md`: **Summary** (PR 1 of 4 of the spec; `Refs #<T>, #<R>`; nothing closes), **Changes** (one line each: the fact; the mart refactor; the four siblings; the four deltas; the generated files; the fixture; CLAUDE.md; the spec and plan), **Effect on what exists** (a table: `ftr_hour_jma_obs` now reads the fact, every existing column equal to the bit on N rows; `ftr_hour_msm` gains a left join, no `available_at` moved on N rows; no preset changes; the fact is new), **Checks** (the dbt unit tests, `dbt build`, `just test` with coverage, lint, mypy, the generator check, docs-links), the `<details>` **Decisions** (the deltas' placement; the fact as curated; coverage as columns) and **Evidence** (the diff counts, the 2025-03-04 numbers, the Feast read-through shape), ending with the attribution line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

```bash
git push -u origin feature/issue-<T>-weather-siblings-hour-marts
gh pr create --repo hankehly/power-market-analytics --title "feat(dbt): fct_area_weather_hourly and the D-2, D-7 weather siblings of the hour marts" --body-file scratch/2026-09-29-weather-siblings-hour-marts/pr-body.md
gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label forecasting
```

- [ ] **Step 4: The review loop**

Poll for Codex as CLAUDE.md describes (a background script, 60 s apart, on `pulls/<n>/reviews`, `issues/<n>/reactions`, `pulls/<n>/comments`, `issues/<n>/comments`, with `--method GET`), address every finding in a commit or a rebutted reply with evidence, resolve threads, and stop when a round ends clean with CI green. Report the PR as ready; the researcher merges.
