# Paper-style similar days for the demand task — design

Date: 2026-09-14. Status: **draft**, awaiting the researcher's review.
Branch: `feature/similar-day-top-k`.

## 1. Goal

Make the demand task's similar-day features follow Park, Song and Kwon (2020), the
paper the selector comes from (`docs/research/papers.md`). Today the job picks one day
from D − 364 ± 30. After this change:

1. **The candidates are the paper's blended pool:** the 30 days before the delivery
   day and 60 days one year back, ranked together.
2. **Six features come out of it:** the loads of the 5 nearest days, and their
   distance-weighted mean.
3. **A special day takes the same holiday last year** instead of a ranked pick.
4. **Today's `similar_day_demand_kwh` is retired.** The presets that use it read the
   new rank 1 instead.

The features still go through the walk-forward job, `pma_ml.similar_day` and the
`ftr_period_similar_day` mart, so a preset reads them through Feast.

## 2. Decisions

The researcher's answers of 2026-09-13 and 2026-09-14.

1. **Ranks 1 to 5 and one weighted mean**, weighted by inverse distance (§7). There is
   no plain mean.
2. **The pool is the paper's:** 30 recent days and 60 days one year back, in one
   ranking.
3. **A special day is a `dim_date.is_holiday` day:** 国民の祝日, 年末年始 12/30–1/3,
   ゴールデンウィーク 4/30–5/2 and お盆 8/13–16.
4. **A special day's reference is the same holiday last year** (§5). Rank 1 and the
   weighted mean carry its load. Ranks 2 to 5 are null.
5. **`similar_day_demand_kwh` is retired.** The seven presets that read it switch to
   the new rank 1.
6. **Flat expressions.** `SIMILAR_DAY` gains `rank` and `holidays` arguments, and a new
   primitive `SIMILAR_DAY_MEAN` takes `k` and `weight`. The nested form
   (`NTH(SIMILAR_DAYS(…), 2)`) was rejected: one feature could be spelled with
   different `k`, and it would change what `WEIGHTED_MEAN` means.
7. **`/ 2` stays in every expression.** The source is the hourly でんき予報 load. Each
   half-hour gets half of its hour's kWh, and the label should name that scale.
8. **One fit per scoring step.** A day's five nearest days and their distances come
   from the one fit that scores the day.
9. **No new preset.** The existing similar-day presets change their feature (decision
   5). `--add` tries the other five on any preset.

## 3. The paper, and where this design differs

What §2.2 and §3 of the paper say, next to this design:

| Topic | Paper | This design |
|---|---|---|
| Candidate pool | "the past 30 days and 60 days from the previous year" (§3). §2.2's own range is "determined through trial and error". | D − 2 … D − 31 and D − 335 … D − 394 (§4) |
| Special days | Left out of the test days and the training episodes | Same holiday last year (§5), the researcher's rule. Left out of the pool and the fit, as in the paper. |
| Distance parts (Eq. 1) | Days between the dates; 24-hour temperature, sun irradiation, rain | Days between the dates; 24-hour temperature, humidity, rain; days since and until a holiday; holiday degree. Unchanged apart from the calendar part. |
| Target day's weather | Actual values, "to avoid the prediction error" | The MSM forecast. The actual weather is not public at the issue time. Unchanged. |
| Weight fit (Eq. 2) | Least squares, α · WED + β against the load difference | The same. Unchanged. |
| Load difference (Eq. 3) | (1/24) Σ √(((ld_f − ld_i) / ld_f)²), the mean absolute relative difference; the prose calls it an RMS percent error | The same formula. Unchanged. |
| Similar days used | 3, fed to a neural network | 5 and a weighted mean, fed to LightGBM |
| Newest recent day | D − 1 | D − 2. D − 1 is not over at the 09:30 D − 1 issue time. |

The year-ago 60 days' position is not in the paper. Its example for 14 March 2018 picks
days from 22 to 30 March 2017, which is consistent with a window around the same date. §4 puts it
around D − 364, the same weekday one year back.

## 4. The pool

For a delivery day D that is not a special day:

| Window | Lags | Days |
|---|---|---|
| Recent | 2 … 31 | 30 |
| One year back | 335 … 394 | 60, with D − 364 the 30th from the newest |

- **Special days are not candidates.** A holiday in either window is left out, as in
  the paper. So a pool can have fewer than 90 days.
- **A candidate counts only if its whole day's load was public by D's issue time**
  (09:30 on D − 1): the latest `available_at` of its 24 hours. From 2022-04-01 the
  daily files make D − 2 public by 00:00 on D − 1. Before, the yearly files make a day
  public at day + 2, 00:00. D − 2 then misses the issue time, and the recent window
  starts at D − 3.
- **Observed weather needs no rule.** A JMA observation is public an hour after it is
  taken, so D − 2's last hour is public at 01:00 on D − 1.
- **The calendar part is the days between the dates**, the lag: the paper's Δ D_dd. A
  year-back candidate is always far on this part. The fit decides how much that
  weighs against the weather and holiday parts.
- **Ties** go to the smaller lag.
- **One fit for the whole pool**, on the same schedule as today: every 7 days, on the
  730 days before the cutoff. A training pair (target T, candidate C) is used only if
  neither day is a special day, C was in T's pool and public by T's issue time, and T's
  load was public by the cutoff.
- **The retrieval check compares the pick with D − 7**, the paper's previous-week
  model, where D − 7 is in the pool.

## 5. Special days

For a delivery day D with `dim_date.is_holiday`, the reference day R is:

1. **The same calendar date last year** for five names that do not name one holiday:
   休日 (振替休日 and 国民の休日), 休日（祝日扱い）, 年末年始, ゴールデンウィーク and
   お盆. 休日 occurs only once in some years (2016, 2017, 2021, 2023), so without this
   rule one substitute holiday would be matched to an unrelated one.
2. **The same holiday last year** for every other name, when it occurs exactly once in
   D's calendar year and exactly once in the year before. R is that day.
   Example: 成人の日 2026-01-12 → 成人の日 2025-01-13.
3. **Otherwise the same calendar date last year.** This covers a name missing last
   year, such as 天皇誕生日 on 2020-02-23 (none in 2019) → 2019-02-23.
4. **Three names count as one:** 体育の日, 体育の日（スポーツの日） and スポーツの日.
   They are the same holiday under its old and new names. So スポーツの日 on
   2020-07-24, moved for the Olympics, → 体育の日（スポーツの日） on 2019-10-14.

The seed has no holiday on 29 February, so the same calendar date always exists.

A special day's row:

- `similar_day_rank1_demand_kwh` and `wavg_similar_day_top5_demand_kwh` both hold R's
  hourly load, halved.
- The rank 2 … 5 loads, all distances, `similar_day_n_candidates` and
  `similar_day_fit_cutoff` are null.
- `similar_day_rank1_reference_date` is R, and `similar_day_method` names the rule:
  `same_holiday` or `same_date`.
- `available_at` is R's load availability. No forecast and no fit enter the row.
- R's load missing raises, as a ranked day's does today.

## 6. The columns

`pma_ml.similar_day` and `ftr_period_similar_day` keep their grain: one row per scoring
run × area × delivery day × period. The table stays wide, one column per feature,
because a Feast field is a column.

Feature columns, tagged `feature: true`, `categorical: false`:

| Column | Type | Expression |
|---|---|---|
| `similar_day_rank1_demand_kwh` | double, not null | `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=1, holidays=last_year) / 2` |
| `similar_day_rank2_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=2, holidays=last_year) / 2` |
| `similar_day_rank3_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=3, holidays=last_year) / 2` |
| `similar_day_rank4_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=4, holidays=last_year) / 2` |
| `similar_day_rank5_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), rank=5, holidays=last_year) / 2` |
| `wavg_similar_day_top5_demand_kwh` | double, not null | `SIMILAR_DAY_MEAN(power_usage_demand_kwh, gap=(2d, 335d), window=(30, 60), k=5, weight=inverse_distance, holidays=last_year) / 2` |

Untagged:

| Column | Type | Null when |
|---|---|---|
| `similar_day_rank1_reference_date` … `similar_day_rank5_reference_date` | date | the rank is absent |
| `similar_day_rank1_distance` … `similar_day_rank5_distance` | double | a special day, or the rank is absent |
| `similar_day_n_candidates` | int | a special day |
| `similar_day_fit_cutoff` | timestamp | a special day |
| `similar_day_method` | string: `similarity`, `same_holiday`, `same_date` | never |

- **A ranked day's rank 2 … 5 is absent** only when its pool has fewer days than the
  rank.
- **The old untagged columns are dropped** with the retired feature:
  `similar_day_reference_date`, `similar_day_reference_lag_days` and
  `similar_day_distance`. `similar_day_n_candidates` and `similar_day_fit_cutoff` keep
  their names and meaning.
- **Rank 1 takes a new name.** A retired name is never reused for a different feature,
  and old runs' labels come from the `retired_features` row.

## 7. The weighted mean

For a ranked day D with nearest days 1 … k (k = 5, or fewer when the pool is smaller),
distances d₁ ≤ … ≤ d_k and hourly loads L₁ … L_k for the period's hour:

```
w_r  = (1 / d_r) / Σ_s (1 / d_s)
wavg = (Σ_r w_r · L_r) / 2
```

- **d is the distance the selector ranks by**, `sqrt(Σ_j w_j (Δ_j / s_j)²)`. All k
  distances come from the same fit, so they are on one scale.
- **One set of weights per day.** The 48 periods of D share them. Only the loads
  change from period to period.
- **Summed in rank order**, one rank at a time, so a re-run gives the same value to
  the bit. The mart rule behind `ordered_weighted_mean` asks the same of SQL.
- **A distance of 0** gets all the weight: the days at distance 0 share it equally,
  and the rest get none. This is the limit of the formula. It is practically
  impossible, because the weather parts are continuous.
- **On a special day** the mean is R's load (§5).

Why not weight by the fit's predicted load difference, 1 / (α·d + β)? β has no lower
bound. In run `30ab12e9…`, 35 of the 388 fits have β < 0, all with cutoffs in 2019,
and with α between 0.09 and 0.19 those fits can predict a zero or negative difference
for a near day. Inverse distance has no such case.

## 8. The job

`tasks/demand/similar_day.py`:

- `SimilarDayPool`: a frozen tuple of windows, each an inclusive `(newest, oldest)` lag
  pair. `SIMILAR_DAY_POOL = ((2, 31), (335, 394))`. `SimilarDaySelector` takes a pool
  instead of `center_lag_days` / `half_width_days`.
- **The calendar part is the lag**, and the tie-break is the smaller lag.
- **The pool rules of §4** filter both the scoring pairs and the training pairs: no
  special day on either side, and the candidate's load public by the target's issue
  time.
- `SIMILAR_DAY_TOP_K = 5`. It is a constant, not a flag, because the column names
  carry it.
- `SimilarDaySelector.rank(days, k)` returns a new frame, `SimilarDayRanking` (grain
  `trade_date × rank`: `reference_date`, `distance`, `reference_lag_days`).
  `select(days)` takes rank 1 of the same sort, so the two cannot disagree.
- **Retrieval compares with D − 7.** The frames' `lag_364_rank` and
  `lag_364_load_difference` become `lag_7_rank` and `lag_7_load_difference`.
- `inverse_distance_weights(distances)`: a pure function from a days × k array (NaN
  for a missing rank) to the weights of §7, with the zero-distance rule.
- `special_day_references(calendar, days)`: the reference day and rule of §5 for every
  special day among `days`. `DayCalendar` gains `is_holiday` and `holiday_name_ja`.

`tasks/demand/similar_day_feature.py`:

- `score_walk_forward` scores the non-special days as today, and adds `ranking` to
  `WalkForwardScoring`.
- `build_feature_records` builds the ranked days' rows from the ranking and the special
  days' rows from their references. Both kinds come from the job's day list: every day
  with a full forecast profile whose issue time is on or after the first fit's cutoff.
  A ranked day's `available_at` is the latest of its forecast availability, its fit's
  cutoff and the load availability of its five ranked days.
- `SimilarDayFeatureRecords` holds the columns of §6 and checks them:
  - `similar_day_method` agrees with `is_holiday`.
  - On special days, the columns §5 makes null are null.
  - On ranked days, distances are non-decreasing by rank, and a null rank is followed
    only by null ranks.
  - Reference days are distinct and before D, and loads are positive.
  - The weighted mean lies between the smallest and largest rank load, within a
    relative 1e-9.
- `publish_feature_records` writes the new columns. The table is created if absent
  and never altered (the `pma_ml` gotcha), so the rollout drops it first (§11).

`scripts/fit_similar_day.py`:

- **Kept:** the params, `similar_day_fits.csv`, `similar_day_selection.csv` and
  `similar_day_retrieval.csv`. The last two describe ranked days' rank 1.
- **New params:** `similar_day_pool` and `similar_day_top_k`.
- **New counts:** `n_days_ranked`, `n_days_same_holiday` and `n_days_same_date`.
- **New artifacts:** `similar_day_ranking.csv`, every ranked day × rank with its day,
  lag, distance and weight; and `similar_day_special_days.csv`, every special day with
  its reference and rule.
- **Renamed metrics:** `similar_day_load_difference_lag_7` and
  `similar_day_share_better_than_lag_7` replace the `lag_364` pair.
  `…_selected` and `…_oracle` stay.

## 9. Retiring the old feature

- **Remove `similar_day_demand_kwh`** and the three untagged columns of §6 from the
  job, the table, `stg_ml__similar_day` and the mart.
- **Add its row to `dbt/seeds/retired_features.csv`:** the name, the expression
  `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61) / 2`, `false`, and a
  description that ends "retired on 2026-09-14 for the paper-style pool". Stored runs
  keep their labels.
- **Point the presets at the new rank 1:** `SIMILAR_DAY_FEATURE` in
  `tasks/demand/presets.py` becomes `ftr_period_similar_day:similar_day_rank1_demand_kwh`.
  That changes seven presets: `lightgbm_msm_popw_daytype_simday` and its `_calendar`,
  `_holidaydegree`, `_holidaydistance`, `_calendarcounts`, `_lags` and
  `_lags_weather` variants.
- **Their reference runs no longer come from these presets:** `008868fe…`,
  `e3e3bd61…`, `a8da46c5…`, `f7153839…`, `9182d469…`, `a3fde7eb…`, `34707ed6…`,
  `d04e9d0c…` and `e6d6d4ef…`. CLAUDE.md says so next to each preset. The research
  documents stay as written, because they describe what was run. A comparison with the
  new feature needs fresh baseline runs.

## 10. dbt

- `models/raw/ml.yml`: the source columns of §6.
- `stg_ml__similar_day`: the new column list in the select, the empty-frame branch
  and the contract. Tests:
  - `accepted_values` on `similar_day_method`.
  - `not_null` on rank 1, the weighted mean, rank 1's reference date and the method.
  - `not_null` on each rank 2 … 5 load, reference date and distance where
    `similar_day_method = 'similarity'` and `similar_day_n_candidates` reaches the
    rank.
  - `expression_is_true`: on special days the distances, `similar_day_n_candidates`,
    `similar_day_fit_cutoff` and ranks 2 … 5 are null, and the weighted mean equals
    rank 1.
  - `expression_is_true`: on ranked days the distances do not decrease by rank.
  - `expression_is_true`: on ranked days with five ranks and no zero distance, the
    weighted mean recomputed in SQL matches within a relative 1e-9.
- `ftr_period_similar_day`: the new columns, the six tags and expressions of §6, and
  the unit test's rows: two scoring runs, one ranked day, one special day.
- `just feature-views` regenerates `views.py`, `fct_feature_value.sql` and
  `dim_feature.sql`. `fct_feature_value` loses the retired feature and gains five, about
  4 × 48 × 2,714 ≈ 520,000 more Tokyo rows.

## 11. Rollout

1. Drop `pma_ml.similar_day`.
2. Run `scripts/fit_similar_day.py --area tokyo`. The pool is 90 days instead of 61,
   so it runs somewhat longer than today's 7–11 minutes.
3. `just dbt build --select retired_features stg_ml__similar_day+ dim_feature`.
4. Run one backtest of `lightgbm_msm_popw_daytype_simday` on Tokyo with the default
   window, as proof that a preset reads the new column end to end.

The drop removes the older scoring runs' partitions, which only the retired feature
read.

## 12. Feature naming

`docs/Feature-Naming.md`:

- **Rule 5:**
  - `gap` and `window` may be tuples, pairwise, for a pool of several windows:
    `gap=(2d, 335d), window=(30, 60)`.
  - `rank` (which nearest day), `k` (how many), `weight` and `holidays` come after
    `halflife`.
  - `holidays=last_year` means a holiday takes the same holiday last year instead of a
    ranked pick.
- **The `SIMILAR_DAY` row** becomes `SIMILAR_DAY(x, gap, window, rank, holidays)`:
  `x` on the `rank`-th most similar day of the pool. Its example is rank 1 of §6.
- **A new row:** `SIMILAR_DAY_MEAN(x, gap, window, k, weight, holidays)`, the weighted
  mean of `x` over the `k` most similar days of the pool. Its example is the weighted
  mean of §6.

CLAUDE.md's `fit_similar_day.py`, feature-mart and demand-task bullets describe the pool,
the special-day rule, the new columns and the retired one.

## 13. Tests

`tests/test_demand_similar_day.py`:
- The pool's two windows, and a holiday left out of the pool.
- A candidate public after D's issue time is left out, and so is D − 2 before
  2022-04-01.
- Training pairs follow the same rules.
- The calendar part is the lag; ties go to the smaller lag.
- The ranking's order, and rank 1 equal to `select`.
- Fewer candidates than k.
- Weights sum to 1; equal distances give equal weights; a zero distance takes all the
  weight; NaN ranks are ignored.
- The retrieval check against D − 7.
- `special_day_references`:
  - A unique name → the same holiday last year (成人の日).
  - The five fixed names → the same calendar date, including a year with one 休日.
  - A name missing last year → the same date (天皇誕生日 2020).
  - The スポーツの日 aliases.

`tests/test_demand_similar_day_feature.py`:
- A ranked day's rank loads halved and its weighted mean on a hand-computed day.
- A special day's row: rank 1 and the mean equal R's load, the rest null.
- `available_at` for both kinds of day.
- Each frame check rejects a bad row.
- The published table's columns.

`tests/test_fit_similar_day_script.py`: the new params, counts, artifacts and metric
names.

`tests/test_demand_presets.py`: the new reference.

`tests/conftest.py`: the `feature_marts` fixture writes the new columns, with a special
day among its rows.

Coverage stays at 100 %.

## 14. Proof

The PR shows:
- **The job run:**
  - Days by rule: ranked, `same_holiday`, `same_date`.
  - The share of each rank's picks from the recent window and from one year back.
  - The largest and smallest weight per ranked day.
  - A few days with their five picks, distances and weights.
  - The retrieval metrics against D − 7 and the oracle.
- **The dbt build passing,** including the `retired_features` row in `dim_feature`.
- **The generator's `--check` passing.**
- **The backtest of §11 finishing,** with its run id and MAE. No comparison is drawn:
  that is a research question.

## 15. Out of scope

- Re-running the reference runs, or any experiment with the new features.
- The paper's reinforcement-learning selector.
- The paper's distance parts: sun irradiation instead of humidity, and no holiday
  parts.
- Actual weather for the target day.
- A plain mean, or ranks beyond 5.
- A load-difference weighting.
- Command-line flags for the pool.
- A Kansai run: only Tokyo is scored, as today.
