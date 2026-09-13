# Top-k and recent similar days for the demand task — design

Date: 2026-09-14. Status: **draft**, awaiting the researcher's review.
Branch: `feature/similar-day-top-k`.

## 1. Goal

The feature store has one similar-day feature: the load of the single nearest day in a
window one year back, `similar_day_demand_kwh`. This design adds two things.

1. **Top 5, one year back.** The loads of the 2nd to 5th nearest days, and a
   distance-weighted mean of the five.
2. **A recent window.** The same set, ranks 1 to 5 and the weighted mean, chosen
   from the 60 days before the delivery day.

All twelve go through the existing walk-forward job, `pma_ml.similar_day` and the
`ftr_period_similar_day` mart, so a preset reads them through Feast like any other
feature.

## 2. Decisions

The researcher's answers of 2026-09-13 and 2026-09-14.

1. **Ranks 1 to 5 in each window.** Rank 1 one year back is today's feature and must
   not move.
2. **One distance-weighted mean per window, weighted by inverse distance** (§5).
   There is no plain mean.
3. **Flat expressions.** `SIMILAR_DAY` gains a `rank` argument, and a new primitive
   `SIMILAR_DAY_MEAN` takes `k` and `weight`. The nested form
   (`NTH(SIMILAR_DAYS(…), 2)`) was rejected: one feature could be spelled with
   different `k`, and it would change what `WEIGHTED_MEAN` means.
4. **`/ 2` stays in every expression.** The source is the hourly でんき予報 load. Each
   half-hour gets half of its hour's kWh, and the label should name that scale.
5. **One fit per window per scoring step.** A day's five nearest days and their
   distances come from the one fit that scores the day in that window. Nothing is
   fitted again for the extra ranks.
6. **No new preset.** `--add ftr_period_similar_day:<column>` tries a feature on any
   preset. Which experiment to run is the researcher's call.
7. **The recent window is D − 2 … D − 61**, 60 candidate days. D − 1 is not over at the
   09:30 D − 1 issue time.
8. **Its calendar part is the days back from D − 2**, |lag − 2|: a newer day is closer.
   The fit decides how much that matters. The other six parts are the year-ago
   window's.

## 3. The windows

Both windows run through the same selector with their own settings:

| Setting | One year back | Recent |
|---|---|---|
| Candidate lags | 334 … 394 days | 2 … 61 days |
| Anchor lag | 364 | 2 |
| Calendar part | \|lag − 364\| | \|lag − 2\| |
| Tie-break after distance | nearer the anchor, then the earlier date | the same |
| Retrieval check compares the pick with | D − 364 | D − 2 |

- **The anchor is one new setting.** It replaces today's window centre in the
  calendar part, the tie-break and the retrieval check. For the year-ago window it is
  364, as now, so nothing changes there.
- **Each window has its own fit.** The distance weights, α and β are fitted
  separately. The two candidate pools relate distance to load difference in different
  ways. Both fit on the same schedule: every 7 days, on the 730 days before the
  cutoff.
- **The load series is the same:** the でんき予報 hourly load of
  `fct_area_power_usage_hourly`. It goes back to 2016, and the fit's load difference is
  measured on its 24 hourly values.

## 4. The columns

`pma_ml.similar_day` and `ftr_period_similar_day` keep their grain: one row per scoring
run × area × delivery day × period. The table stays wide: one column per feature,
because a Feast field is a column.

**One year back.** Feature columns, tagged `feature: true`, `categorical: false`:

| Column | Type | Expression |
|---|---|---|
| `similar_day_demand_kwh` | double, not null | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=1) / 2` (existing; only the expression changes) |
| `similar_day_rank2_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=2) / 2` |
| `similar_day_rank3_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=3) / 2` |
| `similar_day_rank4_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=4) / 2` |
| `similar_day_rank5_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=5) / 2` |
| `wavg_similar_day_top5_demand_kwh` | double, not null | `SIMILAR_DAY_MEAN(power_usage_demand_kwh, gap=334d, window=61, k=5, weight=inverse_distance) / 2` |

Untagged, next to the existing `similar_day_reference_date`,
`similar_day_reference_lag_days`, `similar_day_distance`, `similar_day_n_candidates`
and `similar_day_fit_cutoff`:

| Column | Type |
|---|---|
| `similar_day_rank2_reference_date` … `similar_day_rank5_reference_date` | date |
| `similar_day_rank2_distance` … `similar_day_rank5_distance` | double |

**Recent.** Feature columns, tagged the same way:

| Column | Type | Expression |
|---|---|---|
| `similar_day_recent_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=2d, window=60, rank=1) / 2` |
| `similar_day_recent_rank2_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=2d, window=60, rank=2) / 2` |
| `similar_day_recent_rank3_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=2d, window=60, rank=3) / 2` |
| `similar_day_recent_rank4_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=2d, window=60, rank=4) / 2` |
| `similar_day_recent_rank5_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=2d, window=60, rank=5) / 2` |
| `wavg_similar_day_recent_top5_demand_kwh` | double | `SIMILAR_DAY_MEAN(power_usage_demand_kwh, gap=2d, window=60, k=5, weight=inverse_distance) / 2` |

Untagged:

| Column | Type |
|---|---|
| `similar_day_recent_reference_date`, `similar_day_recent_rank2_reference_date` … `similar_day_recent_rank5_reference_date` | date |
| `similar_day_recent_reference_lag_days` | int |
| `similar_day_recent_distance`, `similar_day_recent_rank2_distance` … `similar_day_recent_rank5_distance` | double |
| `similar_day_recent_n_candidates` | int |
| `similar_day_recent_fit_cutoff` | timestamp |

**Which rows exist and what is null:**

- **A row exists where the year-ago window scored the day**, as today. Its columns
  follow today's rules.
- **A year-ago rank 2 … 5 column is null** only when D has fewer candidates than the
  rank. That never happened in the latest run (`30ab12e9…`): all 2,714 scored days
  had 61 candidates.
- **The recent columns are all null** where the recent window scored nothing for D:
  before its first fit, or with no eligible candidate. A recent rank 2 … 5 column is
  null when D has fewer eligible candidates than the rank.
- **Rank 1's columns one year back keep their names.** `similar_day_demand_kwh` is a
  feature, and a feature is never renamed; its untagged neighbours keep theirs to
  match. The recent window has no stored runs, so its rank 1 takes the
  `similar_day_recent_` prefix with no rank number, the same pattern.

## 5. The weighted mean

For a delivery day D and one window, with nearest days 1 … k (k = 5, or fewer when D
has fewer candidates), distances d₁ ≤ … ≤ d_k and hourly loads L₁ … L_k for the
period's hour:

```
w_r  = (1 / d_r) / Σ_s (1 / d_s)
wavg = (Σ_r w_r · L_r) / 2
```

- **d is the distance the window's selector ranks by**, `sqrt(Σ_j w_j (Δ_j / s_j)²)`:
  the same number stored as the rank's distance. All k distances come from the same
  fit, so they are on one scale. The two windows' distances come from different fits
  and are never mixed.
- **One set of weights per day and window.** The 48 periods of D share them. Only the
  loads change from period to period.
- **Summed in rank order**, one rank at a time, so a re-run gives the same value to
  the bit. The mart rule behind `ordered_weighted_mean` asks the same of SQL.
- **A distance of 0** gets all the weight: the days at distance 0 share it equally,
  and the rest get none. This is the limit of the formula. It is practically
  impossible, because the weather parts are continuous.
- **Fewer than k candidates:** the mean runs over the ranks that exist.

Why not weight by the fit's predicted load difference, 1 / (α·d + β)? β has no lower
bound. In run `30ab12e9…`, 35 of the 388 fits have β < 0, all with cutoffs in 2019,
and with α between 0.09 and 0.19 those fits can predict a zero or negative difference
for a near day. Inverse distance has no such case.

## 6. What a candidate needs to be public

A year-ago candidate is at least 334 days old, so its data was always public by the
issue time. A recent candidate can be two days old, so the selector gets a rule:

**A candidate counts for D only if its whole day's load was public by D's issue time**
(09:30 on D − 1). "Its whole day's load" means the latest `available_at` of its 24
hours.

What the rule does in practice, from the `available_at` rules of
`std_tepco__power_usage_hourly`:

- **From 2022-04-01 (daily files):** D − 2's load is public by 00:00 on D − 1. Every
  recent candidate counts, 60 of them.
- **Before 2022-04-01 (yearly files):** a day is public at day + 2, 00:00. D − 2 is
  public only at 00:00 on D, after the issue time, so it does not count. The newest
  candidate is D − 3, and there are 59.

**The fit follows the same rule.** A training pair (target T, candidate C) is used only
if C's load was public by T's issue time. The fit then learns from the pools that
scoring will actually see. For the year-ago window, every pair passes, so its fits do
not change.

**Observed weather needs no rule.** A JMA observation is public an hour after it is
taken. D − 2's last hour is public at 01:00 on D − 1, before the issue time.

## 7. The job

`tasks/demand/similar_day.py`:

- `SimilarDayWindow`: a frozen setting of `newest_lag_days`, `oldest_lag_days` and
  `anchor_lag_days`. Two constants: `YEAR_AGO_WINDOW = (334, 394, 364)` and
  `RECENT_WINDOW = (2, 61, 2)`. `SimilarDaySelector` takes a window instead of
  `center_lag_days` / `half_width_days`.
- The selector's calendar part, tie-break and retrieval check use the anchor. The
  frames' `lag_364_rank` and `lag_364_load_difference` columns become
  `anchor_rank` and `anchor_load_difference`.
- **Candidate eligibility (§6)** filters the scoring pairs by D's issue time and the
  training pairs by the target's issue time.
- `SIMILAR_DAY_TOP_K = 5`. It is a constant, not a flag, because the column names
  carry it.
- `SimilarDaySelector.rank(days, k)` returns a new frame, `SimilarDayRanking` (grain
  `trade_date × rank`: `reference_date`, `distance`, `reference_lag_days`). It sorts by
  distance, then nearness to the anchor, then the earlier date, and numbers the first
  k from 1.
- `select(days)` keeps its output and takes its day from rank 1 of the same sort, so
  the two cannot disagree.
- `inverse_distance_weights(distances)`: a pure function from a days × k array (NaN
  for a missing rank) to the weights of §5, with the zero-distance rule.

`tasks/demand/similar_day_feature.py`:

- `score_walk_forward` runs once per window, each with its own first fit and cutoffs.
  It keeps `selection` and adds `ranking` to `WalkForwardScoring`.
- `build_feature_records` takes both scorings. The year-ago scoring sets the rows, as
  today. For each window it joins every rank's hourly load, halves it and computes
  the weighted mean in rank order. A ranked day without a load raises, as rank 1 does
  today.
- **The row's `available_at`** is the latest of D's forecast availability, both fits'
  cutoffs, and the load availability of every ranked day in both windows. One row
  carries both windows, so one `available_at` covers them. In practice D's forecast
  vintage (01:00 on D − 1) stays the latest: a recent candidate's load is public by
  00:00 on D − 1, and the weekly cutoffs fall at the time of day of the first fit's
  instant. §9 checks this on every row.
- `SimilarDayFeatureRecords` adds the columns of §4. It also checks, per window:
  distances non-decreasing by rank; a null rank is followed only by null ranks; ranked
  days distinct and before D; loads positive; the weighted mean between the smallest
  and largest rank load, within a relative 1e-9 for rounding. For the recent window it
  also checks that the recent columns are all null or rank 1 is present.
- `publish_feature_records` writes the new columns. The table is created if absent
  and never altered (the `pma_ml` gotcha), so the rollout drops it first (§9).

`scripts/fit_similar_day.py`:

- **Year-ago outputs keep their names.** The params, `similar_day_fits.csv`,
  `similar_day_selection.csv`, `similar_day_retrieval.csv` and the four
  `similar_day_*` metrics still describe rank 1 one year back. `similar_day_selection.csv`
  and `similar_day_retrieval.csv` rename their `lag_364_*` columns to `anchor_*`.
- **The recent window logs the same set under `similar_day_recent_`:** its params,
  `similar_day_recent_fits.csv`, `…_selection.csv`, `…_retrieval.csv`, and the metrics
  `similar_day_recent_load_difference_selected`, `…_lag_2`, `…_oracle` and
  `similar_day_recent_share_better_than_lag_2`.
- **Both windows log a ranking:** `similar_day_ranking.csv` and
  `similar_day_recent_ranking.csv`, every scored day × rank with its day, distance and
  weight.
- **New param:** `similar_day_top_k`.
- **Run time** roughly doubles, from 7–11 to about 15–20 minutes.

## 8. dbt

- `models/raw/ml.yml`: document the new source columns.
- `stg_ml__similar_day`: select the new columns, and add them to the empty-frame
  branch and the contract. Tests, per window:
  - `not_null` on each rank column where the window's `n_candidates` reaches the rank,
    and on the weighted mean where the window scored the day.
  - `expression_is_true`: the distances do not decrease by rank.
  - `expression_is_true`: the weighted mean recomputed in SQL from the rank loads and
    distances matches within a relative 1e-9, on rows with five ranks and no zero
    distance.
  - Recent only, `expression_is_true`: `similar_day_recent_reference_lag_days` between
    2 and 61, and `similar_day_recent_fit_cutoff` on or before D's issue time.
- `ftr_period_similar_day`: pass the columns through, tag the twelve features with the
  expressions of §4, and extend its unit test's rows.
- `just feature-views` regenerates `views.py`, `fct_feature_value.sql` and
  `dim_feature.sql`. `fct_feature_value` gains about 11 × 48 × 2,714 ≈ 1.4 million Tokyo
  rows.

## 9. Rollout

1. On `main`, run `scripts/fit_similar_day.py --area tokyo` (7–11 minutes) so the
   baseline sees the warehouse as it is now. Copy that run's rows to
   `pma_ml.similar_day_before_top_k`.
2. Drop `pma_ml.similar_day`.
3. On the branch, run the job, then `just dbt build --select stg_ml__similar_day+
   dim_feature`.
4. Compare the new run with the copy on every row:
   - `similar_day_demand_kwh`, `similar_day_reference_date`,
     `similar_day_reference_lag_days`, `similar_day_distance`,
     `similar_day_n_candidates` and `similar_day_fit_cutoff` must be equal to the bit.
   - The two runs must have the same rows.
   - `available_at` may only move later, and only because of a recent-window input.
     The PR reports how many rows moved.
   Then drop the copy.

The drop removes the older scoring runs' partitions. No forecast reads them any more:
Feast serves the newest published run.

## 10. Feature naming

`docs/Feature-Naming.md`:

- Rule 5 adds `rank` (which nearest day), `k` (how many) and `weight`, after
  `halflife`.
- The `SIMILAR_DAY` row of the primitives table gains `rank`, and a second example
  with `gap=2d, window=60`.
- A new row: `SIMILAR_DAY_MEAN(x, gap, window, k, weight)`, the weighted mean of `x`
  over the `k` most similar of `window` candidate days. Example: a weighted-mean
  expression from §4.

CLAUDE.md's `fit_similar_day.py` and feature-mart bullets name the new columns and the
recent window.

## 11. Tests

`tests/test_demand_similar_day.py`:
- The ranking's order and tie-break.
- Rank 1 equals `select`.
- Fewer candidates than k.
- Weights sum to 1; equal distances give equal weights; a zero distance takes all
  the weight; NaN ranks are ignored.
- The calendar part and the tie-break measured from the anchor.
- A candidate whose load is public after D's issue time is not scored, and does not
  enter a training pair.
- The recent window before 2022-04-01 starts at D − 3.
- The year-ago window's pairs and fit are unchanged by the rule.

`tests/test_demand_similar_day_feature.py`:
- The rank loads halved.
- The weighted mean on a hand-computed day.
- `available_at` with a late-public ranked day in either window.
- Recent columns null before the recent window's first fit.
- Each new frame check rejects a bad row.
- The published table's new columns.

`tests/test_fit_similar_day_script.py`: the new params, artifacts and metrics.
`tests/conftest.py`: the `feature_marts` fixture gains the new columns.
Coverage stays at 100 %.

## 12. Proof

The PR shows:
- The §9 comparison: zero differences on the year-ago rank-1 columns, the same rows,
  and the count of rows whose `available_at` moved.
- The dbt build passing.
- For each window: the distribution of the five weights (largest and smallest weight
  per day), and a few days with their five days, distances and weights.
- For the recent window: its first fit, the count of days it scored, and the
  retrieval metrics next to the year-ago window's.

## 13. Out of scope

- A preset or an experiment with the new features.
- A plain (unweighted) mean.
- Ranks beyond 5.
- A load-difference weighting.
- The 30-minute A-1 actuals as the recent window's load series.
- Command-line flags for the recent window.
- A Kansai run: only Tokyo is scored, as today.
