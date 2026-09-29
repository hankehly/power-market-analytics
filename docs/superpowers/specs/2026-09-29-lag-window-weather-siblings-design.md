# Weather siblings of the load lag windows — design

Date: 2026-09-29. Status: **approved by the researcher on 2026-09-29** (the design, section by
section, in chat; this file is the written spec for review before the plan). The idea is the
researcher's, stated 2026-09-28: for every window the load lag features read, carry the
weather those days had, and its difference from the target day's forecast. Claude proposed
the window order, radiation as the second element and the placement, on the 2025-03-04
analysis (issue to be opened with the feature candidates).

## 1. Goal

Forty columns over the four lag windows that carry 87 % of the permutation importance of
the demand baseline `e219` (run `8fb1b358…`): the similar days, the day-type window, D-2
and D-7. For each window and each of two elements, temperature and solar radiation, a
**sibling** — the element the window's days had, read with the load feature's dates and
weights — and a **delta**, the target day's population-weighted forecast minus the sibling.

Why. On Tokyo 2025-03-04, a cold overcast Tuesday two days after a 20 °C Sunday, every
preset underforecast by 6 to 13 %. The lag features read the warmest week of the season
and pulled the forecast 1.4 M kWh per period below the model's own similar days; those
similar days, sunny cold-wave days, sat 0.75 M below the actual in daylight. Nothing in
the feature set said how far the target day's weather sat from the days the load came
from: the lag windows carry temperature only at D-2 and only in overlapping windows, and
no window carries a difference. The design gives every dominant window both.

## 2. Decisions

1. **Four windows, by importance.** Similar days (80 % of the lag-window importance), D-2
   (3.8 %), the day-type 4-day window (3.1 %), D-7 (0.8 %). The other seven windows wait.
2. **Two elements: temperature and solar radiation.** Both observed hourly, both
   population-weighted over the area's staffed stations as `ftr_hour_msm` weights the
   forecast. Radiation is the element 2025-03-04 turned on: the similar days matched the
   day on temperature to 1.5 °C and had three to four times its radiation.
3. **A sibling reads the load feature's dates and weights.** The day-type siblings take the
   four dates the load window picked, with 8:4:2:1; the similar-day siblings take the three
   stored reference dates, the mean with the stored distances under the job's
   inverse-distance rule; D-2 and D-7 are the same days. Never its own day selection.
4. **A delta is the forecast minus the sibling**, on the same population weighting and at
   the same hour, so a positive delta means D is warmer or brighter than the days the load
   came from. The forecast side is D's `ftr_hour_msm` row, or `ftr_day_msm` for a daily
   sibling.
5. **The evening window is temperature only.** The 18:00 to 22:00 mean is after sunset most
   of the year, so a radiation evening mean would be zero. Radiation gets 19 columns,
   temperature 21.
6. **Two forecast columns come in with the batch**, because a delta needs them:
   `DAILY_MEAN(MEAN(forecast_temperature_c, weight=population), time=18:00-22:00)` and
   `DAILY_MEAN(MEAN(forecast_solar_radiation_mjm2, weight=population))`.
7. **One curated fact holds the hourly population-weighted observation**,
   `fct_area_weather_hourly`, with its coverage as columns (section 4). It is lifted out of
   `ftr_hour_jma_obs`, which computes it inside its own SQL today, and every sibling reads
   it. A curated fact rather than a features-layer intermediate: it is an area-grain rollup
   of the station fact, so it belongs in the star, the dashboards can read it, and the
   similar-day job can later read it instead of recomputing the same means in Python.
8. **Radiation coverage is data and prose.** Radiation is recorded at 7 of Tokyo's 21
   weighted stations and 3 of Kansai's 11, 55.4 % of each area's weight, and the
   representative station carries 75 % (東京) and 80 % (大阪) of that share. The fact
   carries the reporting share per hour; every radiation column's description says which
   stations it rests on. A more representative radiation source is future work.
9. **A delta lives where the vintage already is.** The forecast vintage is public at 01:00
   on D-1 (reference + 4 h); the load lags at 00:30. A delta inside `ftr_period_actuals`
   would move every row's `available_at` to 01:00 and couple the load mart to the MSM. So
   the hour and day deltas sit in `ftr_hour_msm` and `ftr_day_msm`, whose rows carry the
   vintage; the similar-day deltas sit in `ftr_period_similar_day`, whose `available_at`
   already holds the vintage; and the day-type siblings and deltas get a mart of their own,
   `ftr_period_daytype_weather`, reading the window's dates that `ftr_period_actuals`
   exposes untagged. No existing row's `available_at` moves. This refines the placement
   table shown in chat, which put the day-type deltas in `ftr_period_actuals`.
10. **One vintage per delivery day.** `ftr_hour_msm` holds the 12 UTC D-2 run only; the
    period marts join it on area, day and hour. A singular test asserts one vintage per
    delivery day, so a second vintage cannot double the rows unnoticed.
11. **Physical names** replace `demand_kwh` in the load feature's name with
    `popw_<element>`; a delta is `delta_` plus its sibling's name; the two forecast columns
    follow `ftr_day_msm`'s names. Names never change; expressions are the load feature's
    with the element swapped in.
12. **Base preset `e219`**, the pool variant, so the similar-day siblings (`holidays=similarity`)
    sit beside the load features they mirror; the same-holiday twins are not built until
    #219 is decided. Three presets, each a file of its own, `base: e219` with an `add`
    list: temperature, radiation, both.
13. **Both areas' marts build; the experiments are Tokyo only**, because the similar-day
    features exist for Tokyo only.
14. **The similar-day job and `pma_ml.similar_day` are untouched**: the siblings are a mart
    join on the stored reference dates, so no re-scoring and no table drop.
15. **The new D-2 same-hour temperature is not a duplicate.** `mean_24h_popw_temperature_c`
    averages the 24 hours ending at the target hour on D-2, and `wavg_temperature_c` gives
    D-2 half its weight over D-2 to D-8. `LAG(MEAN(temperature_c, weight=population), 2d)`
    is the one hour's value. The three stay.

## 3. What the run said

Permutation importance of `e219` on the pinned window, summed by lag window:

| Window | Features | Σ ΔMAE (kWh) | Share |
|---|---|---|---|
| Similar days | 6 | 2,224,227 | 80.2 % |
| D-2 | 15 | 104,662 | 3.8 % |
| Day-type 4 newest | 4 | 85,480 | 3.1 % |
| D-7 | 7 | 22,631 | 0.8 % |
| The other seven windows | 41 | 39,466 | 1.4 % |

Radiation coverage of the weighted stations, 2025 (`fct_census_population_jma_station`,
census 2020):

| Area | Stations with radiation | Weight | Largest without |
|---|---|---|---|
| Tokyo | 東京 0.417, つくば 0.046, 宇都宮 0.034, 前橋 0.034, 甲府 0.014, 銚子 0.010, 父島 | 0.554 | 横浜 0.210, 千葉 0.076, 熊谷 0.071 |
| Kansai | 大阪 0.446, 奈良 0.080, 彦根 0.029 | 0.554 | 京都 0.150, 神戸 0.131, 姫路 0.073 |

Temperature, humidity, rain and sunshine are recorded at every weighted station.

## 4. The fact: `fct_area_weather_hourly`

Grain: `area_key × observed_at`, one row per area and observed hour end. Columns:

| Column | Meaning |
|---|---|
| `area_key`, `date_key`, `hour_ending`, `observed_at` | The hour as `fct_jma_weather_hourly` places it: hour ending 1 to 24, hour 24 on the day it ends |
| `census_year` | The vintage whose weights were used, the latest |
| `popw_temperature_c`, `popw_solar_radiation_mjm2` | The element weighted over the area's stations that report it that hour, renormalised, added in station order through `ordered_weighted_mean`; null when none reports |
| `n_stations_temperature`, `n_stations_solar_radiation` | How many stations went into the mean |
| `weight_share_temperature`, `weight_share_solar_radiation` | The population weight of those stations, 0 to 1: the coverage of that hour |
| `available_at` | The hour's, observed hour end + 1 h, the standardized rule |

The rule is the one `ftr_hour_jma_obs` applies today, moved and not changed:
`ftr_hour_jma_obs` then reads its three windows from the fact, and the acceptance check is
that every one of its columns equals main to the bit. The elements are a Jinja list, as in
`ftr_hour_msm`, so a third element is one line. Tests: the uniqueness of the key; a unit test
with a station missing one element for one hour, checking the renormalised mean, the count
and the share; shares between 0 and 1; a count of at least 1 wherever a value is not null.

## 5. The columns

Forty, by mart. A sibling of a period-grain window reads the hour containing the period,
`(time_code + 1) div 2`. `T` stands for `MEAN(temperature_c, weight=population)`, `R` for
`MEAN(solar_radiation_mjm2, weight=population)`, `T_fc` for
`MEAN(forecast_temperature_c, weight=population)`, `R_fc` for
`MEAN(forecast_solar_radiation_mjm2, weight=population)`; the mart YAML writes them out.

### 5.1 `ftr_hour_jma_obs`, 4 siblings

| Physical name | Expression |
|---|---|
| `lag_2d_popw_temperature_c` | `LAG(T, 2d)` |
| `lag_7d_popw_temperature_c` | `LAG(T, 7d)` |
| `lag_2d_popw_solar_radiation_mjm2` | `LAG(R, 2d)` |
| `lag_7d_popw_solar_radiation_mjm2` | `LAG(R, 7d)` |

The fact read two and seven days back at the same hour ending. `available_at` is unchanged:
the mart already waits for D-2's hour.

### 5.2 `ftr_hour_msm`, 4 deltas

| Physical name | Expression |
|---|---|
| `delta_lag_2d_popw_temperature_c` | `T_fc - LAG(T, 2d)` |
| `delta_lag_7d_popw_temperature_c` | `T_fc - LAG(T, 7d)` |
| `delta_lag_2d_popw_solar_radiation_mjm2` | `R_fc - LAG(R, 2d)` |
| `delta_lag_7d_popw_solar_radiation_mjm2` | `R_fc - LAG(R, 7d)` |

A left join to `ftr_hour_jma_obs` on area, day and hour; null where the sibling is.
`available_at` takes the greatest of the vintage's and the sibling's, which is the vintage's:
the newest sibling hour, D-2's hour 24, is public at 01:00 on D-1, the vintage's instant.

### 5.3 `ftr_day_jma_obs`, new, 3 siblings

Grain `area_code × trade_date`. Daily means over the fact's 24 hours of D-2 in hour order
through `ordered_weighted_mean` with every weight 1, complete days only, as `ftr_day_msm`
does; the evening mean over the four hours ending 19:00 to 22:00, the load's 18:00 to 22:00,
all four required.

| Physical name | Expression |
|---|---|
| `lag_2d_mean_popw_temperature_c` | `LAG(DAILY_MEAN(T), 2d)` |
| `lag_2d_evening_mean_popw_temperature_c` | `LAG(DAILY_MEAN(T, time=18:00-22:00), 2d)` |
| `lag_2d_mean_popw_solar_radiation_mjm2` | `LAG(DAILY_MEAN(R), 2d)` |

`available_at`: D-2's hour 24 + 1 h, 01:00 on D-1.

### 5.4 `ftr_day_msm`, 2 forecast columns and 3 deltas

| Physical name | Expression |
|---|---|
| `evening_mean_popw_forecast_temperature_c` | `DAILY_MEAN(T_fc, time=18:00-22:00)` |
| `mean_popw_forecast_solar_radiation_mjm2` | `DAILY_MEAN(R_fc)` |
| `delta_lag_2d_mean_popw_temperature_c` | `DAILY_MEAN(T_fc) - LAG(DAILY_MEAN(T), 2d)` |
| `delta_lag_2d_evening_mean_popw_temperature_c` | `DAILY_MEAN(T_fc, time=18:00-22:00) - LAG(DAILY_MEAN(T, time=18:00-22:00), 2d)` |
| `delta_lag_2d_mean_popw_solar_radiation_mjm2` | `DAILY_MEAN(R_fc) - LAG(DAILY_MEAN(R), 2d)` |

The evening mean needs its four hours only, like the morning trend; the radiation mean is
complete days only, like the temperature mean. A left join to `ftr_day_jma_obs` on area and
day; `available_at` unchanged, by the same argument as 5.2.

### 5.5 `ftr_period_actuals`, 4 untagged columns

`daytype_4d_date_1` to `daytype_4d_date_4`: the dates of the day-type window, newest first,
null where the window holds fewer, the same on all 48 periods of a day. Not features: the
window's one definition stays here, and the new mart reads it, as the similar-day mart
stores its reference dates. No feature column and no `available_at` changes.

### 5.6 `ftr_period_daytype_weather`, new, 4 siblings and 4 deltas

Grain `area_code × trade_date × time_code`. The fact at the period's hour on the four dates,
with the load's weights by position over the dates whose value is present.

| Physical name | Expression |
|---|---|
| `mean_daytype_4d_popw_temperature_c` | `ROLLING_MEAN(T, gap=2d, window=4) by day_type` |
| `ewm_daytype_4d_popw_temperature_c` | `EWA(T, gap=2d, window=4, halflife=1) by day_type` |
| `mean_daytype_4d_popw_solar_radiation_mjm2` | `ROLLING_MEAN(R, gap=2d, window=4) by day_type` |
| `ewm_daytype_4d_popw_solar_radiation_mjm2` | `EWA(R, gap=2d, window=4, halflife=1) by day_type` |
| `delta_mean_daytype_4d_popw_temperature_c` | `T_fc - (ROLLING_MEAN(T, gap=2d, window=4) by day_type)` |
| `delta_ewm_daytype_4d_popw_temperature_c` | `T_fc - (EWA(T, gap=2d, window=4, halflife=1) by day_type)` |
| `delta_mean_daytype_4d_popw_solar_radiation_mjm2` | `R_fc - (ROLLING_MEAN(R, gap=2d, window=4) by day_type)` |
| `delta_ewm_daytype_4d_popw_solar_radiation_mjm2` | `R_fc - (EWA(R, gap=2d, window=4, halflife=1) by day_type)` |

`available_at`: the greatest of the window row's, the four dates' hours' and the vintage's,
01:00 on D-1 in practice.

### 5.7 `ftr_period_similar_day`, 8 siblings and 8 deltas

The fact at the period's hour on `similar_day_pool_rank<r>_reference_date` for r = 1 to 3.
The mean uses the stored `similar_day_pool_rank<r>_distance` under the job's rule:
`w_r = (1 / d_r) / Σ_s (1 / d_s)` over the ranks whose value is present, the denominator
summed in rank order, zero distances sharing the weight equally. Written with `S` for
`SIMILAR_DAY(·, gap=(2d, 335d), window=(30, 60), rank=r, holidays=similarity)` and `S_MEAN`
for `SIMILAR_DAY_MEAN(·, gap=(2d, 335d), window=(30, 60), k=3, weight=inverse_distance, holidays=similarity)`:

| Physical name | Expression |
|---|---|
| `similar_day_pool_rank1_popw_temperature_c` … `rank3` | `S(T)`, rank 1 to 3 |
| `wavg_similar_day_pool_top3_popw_temperature_c` | `S_MEAN(T)` |
| `similar_day_pool_rank1_popw_solar_radiation_mjm2` … `rank3` | `S(R)`, rank 1 to 3 |
| `wavg_similar_day_pool_top3_popw_solar_radiation_mjm2` | `S_MEAN(R)` |
| `delta_similar_day_pool_rank1_popw_temperature_c` … `rank3` | `T_fc - S(T)` |
| `delta_wavg_similar_day_pool_top3_popw_temperature_c` | `T_fc - S_MEAN(T)` |
| `delta_similar_day_pool_rank1_popw_solar_radiation_mjm2` … `rank3` | `R_fc - S(R)` |
| `delta_wavg_similar_day_pool_top3_popw_solar_radiation_mjm2` | `R_fc - S_MEAN(R)` |

No `/ 2`: the load is halved per period, a temperature is not. `available_at` is unchanged:
it already holds the vintage, and a reference day's weather is public long before its load.
The rows stay one per scoring run; the siblings ride the run's row.

## 6. Availability, in one place

An observation is public one hour after its hour; a load a day or more after; the vintage at
01:00 on D-1. So a sibling never moves the availability of the row it joins, a delta's row
already waits for the vintage, and no existing row's `available_at` changes. The two new
marts are available at 01:00 on D-1. A day without a forecast has null deltas and its
siblings intact; a window day without weather drops from the sibling's mean and stays in
the load's.

## 7. Presets and experiments

Three preset files under `conf/presets/demand/`, named `e<issue>` after their experiments,
each `base: e219` with an `add` list of expressions: the 21 temperature columns; the 19
radiation columns; all 40. Three experiment issues, each one Tokyo run on the pinned window
with no date arguments, compared with `8fb1b358…` by `compare_demand_runs.py`, and with each
other. Two feature-candidate issues, temperature and radiation, hold the columns; they close
when the last PR carrying their columns merges, with the Decision left to the experiments.

## 8. Tests and proof

- dbt unit tests: the fact's renormalisation, count and share (section 4); each new column
  against hand-computed rows, including a window day with no weather and a similar-day row
  with a zero distance; the singular test of one vintage per day.
- A singular test that the similar-day mean rule, applied to the stored rank loads and
  distances, reproduces `wavg_similar_day_pool_top3_demand_kwh` — the proof that the SQL
  weights are the job's. Expected to the bit, since both sum in rank order; the tolerance is
  what the first build measures.
- Python: the two new marts in the five places the generator does not reach
  (`tests/test_feature_views.py`, `tests/test_feature_store.py`, `tests/test_feature_value_fact.py`,
  the feature-candidate issue template, `tests/conftest.py` with CLAUDE.md's mart count);
  `just feature-views` regenerated and checked.
- Per PR: the scratch-copy diff against main showing no existing column moved, in value or
  in `available_at`; a Feast retrieval of every new column in the devcontainer; `dbt build`.
- Acceptance, in the last PR, on Tokyo 2025-03-04: `delta_lag_2d_mean_popw_temperature_c`
  near −12 °C; the similar-day radiation deltas negative through the afternoon; the day-type
  window's four dates 2025-02-25 to 02-28.

## 9. Rollout

Four PRs in dependency order, each carrying both elements, since the SQL is one loop:

1. `fct_area_weather_hourly`; `ftr_hour_jma_obs` reading it, equal to the bit, plus its 4
   siblings; `ftr_hour_msm`'s 4 deltas.
2. `ftr_day_jma_obs`; `ftr_day_msm`'s 2 forecast columns and 3 deltas.
3. `ftr_period_actuals`' 4 window dates; `ftr_period_daytype_weather`.
4. `ftr_period_similar_day`'s 16.

Then the two candidate issues close, the three experiment issues and presets open, and the
three runs go. CLAUDE.md's mart list and counts, the naming guide's examples and the demand
README's baseline paragraph are updated with the PR that changes them.

## 10. Out of scope

The same-holiday twins; the other seven windows; humidity, rain and sunshine; cross-window
weather differences; departures from the climatological normals; the similar-day job reading
the fact; a more representative radiation source; Kansai experiments.
