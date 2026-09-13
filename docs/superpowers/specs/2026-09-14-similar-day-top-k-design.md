# Top-k similar days for the demand task — design

Date: 2026-09-14. Status: **draft**, awaiting the researcher's review.
Branch: `feature/similar-day-top-k`.

## 1. Goal

The feature store has one similar-day feature: the load of the single nearest day,
`similar_day_demand_kwh`. Add the loads of the 2nd to 5th nearest days as four more
features, and a distance-weighted mean of the five as a sixth. All six go through
the existing walk-forward job, `pma_ml.similar_day` and the `ftr_period_similar_day`
mart, so a preset reads them through Feast like any other feature.

## 2. Decisions

The researcher's answers of 2026-09-13 and 2026-09-14.

1. **Ranks 1 to 5.** Rank 1 is today's feature and must not move. The job adds ranks
   2, 3, 4 and 5.
2. **One distance-weighted mean of the five, weighted by inverse distance** (§4).
   There is no plain mean.
3. **Flat expressions.** `SIMILAR_DAY` gains a `rank` argument, and a new primitive
   `SIMILAR_DAY_MEAN` takes `k` and `weight`. The nested form
   (`NTH(SIMILAR_DAYS(…), 2)`) was rejected: one feature could be spelled with
   different `k`, and it would change what `WEIGHTED_MEAN` means.
4. **`/ 2` stays in every expression.** The source is the hourly でんき予報 load. Each
   half-hour gets half of its hour's kWh, and the label should name that scale.
5. **One fit per day, as today.** A day's five nearest days and their distances come
   from the one fit that scores the day. Nothing is fitted again.
6. **No new preset.** `--add ftr_period_similar_day:<column>` tries a feature on any
   preset. Which experiment to run is the researcher's call.

## 3. The columns

`pma_ml.similar_day` and `ftr_period_similar_day` keep their grain: one row per scoring
run × area × delivery day × period. The table stays wide: one column per feature,
because a Feast field is a column.

Feature columns, tagged `feature: true`, `categorical: false`:

| Column | Type | Expression |
|---|---|---|
| `similar_day_demand_kwh` | double, not null | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=1) / 2` (existing; only the expression changes) |
| `similar_day_rank2_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=2) / 2` |
| `similar_day_rank3_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=3) / 2` |
| `similar_day_rank4_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=4) / 2` |
| `similar_day_rank5_demand_kwh` | double | `SIMILAR_DAY(power_usage_demand_kwh, gap=334d, window=61, rank=5) / 2` |
| `wavg_similar_day_top5_demand_kwh` | double, not null | `SIMILAR_DAY_MEAN(power_usage_demand_kwh, gap=334d, window=61, k=5, weight=inverse_distance) / 2` |

Untagged columns, next to rank 1's existing `similar_day_reference_date` and
`similar_day_distance`:

| Column | Type |
|---|---|
| `similar_day_rank2_reference_date` … `similar_day_rank5_reference_date` | date |
| `similar_day_rank2_distance` … `similar_day_rank5_distance` | double |

A rank column is null only when D has fewer candidates than the rank. That never
happened in the latest run (`30ab12e9…`): all 2,714 scored days had 61 candidates.
Rank 1's columns keep their names. `similar_day_demand_kwh` is a feature, and a
feature is never renamed; its untagged neighbours keep theirs to match.

## 4. The weighted mean

For a delivery day D with nearest days 1 … k (k = 5, or fewer when D has fewer
candidates), distances d₁ ≤ … ≤ d_k and hourly loads L₁ … L_k for the period's hour:

```
w_r  = (1 / d_r) / Σ_s (1 / d_s)
wavg = (Σ_r w_r · L_r) / 2
```

- **d is the distance the selector ranks by**, `sqrt(Σ_j w_j (Δ_j / s_j)²)`: the same
  number stored as `similar_day_distance`. All k distances come from the same fit, so
  they are on one scale.
- **One set of weights per day.** The 48 periods of D share them. Only the loads
  change from period to period.
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

## 5. The job

`tasks/demand/similar_day.py`:

- `SIMILAR_DAY_TOP_K = 5`. It is a constant, not a flag, because the column names
  carry it.
- `SimilarDaySelector.rank(days, k)` returns a new frame, `SimilarDayRanking` (grain
  `trade_date × rank`: `reference_date`, `distance`, `reference_lag_days`). It sorts by
  distance, then nearness to D − 364, then the earlier date (today's tie-break) and
  numbers the first k from 1.
- `select(days)` keeps its output and takes its day from rank 1 of the same sort, so
  the two cannot disagree.
- `inverse_distance_weights(distances)`: a pure function from a days × k array (NaN
  for a missing rank) to the weights of §4, with the zero-distance rule.

`tasks/demand/similar_day_feature.py`:

- `score_walk_forward` keeps `selection` and adds `ranking` to `WalkForwardScoring`.
  Each block of days is ranked by the fit that selects it.
- `build_feature_records` joins each rank's hourly load, halves it and computes the
  weighted mean in rank order. A ranked day without a load raises, as rank 1 does
  today. `available_at` becomes the latest of D's forecast availability, the fit's
  cutoff and the load availability of every ranked day. Every candidate is at least
  334 days old, so in practice the value does not change.
- `SimilarDayFeatureRecords` adds the columns of §3. It also checks: distances
  non-decreasing by rank; a null rank is followed only by null ranks; ranked days
  distinct and before D; loads positive; the weighted mean between the smallest and
  largest rank load, within a relative 1e-9 for rounding.
- `publish_feature_records` writes the new columns. The table is created if absent
  and never altered (the `pma_ml` gotcha), so the rollout drops it first (§8).

`scripts/fit_similar_day.py` logs the param `similar_day_top_k` and a new artifact,
`similar_day_ranking.csv`: every scored day × rank with its day, distance and weight.
`similar_day_selection.csv`, `similar_day_retrieval.csv` and the four retrieval
metrics stay on rank 1.

## 6. dbt

- `models/raw/ml.yml`: document the new source columns.
- `stg_ml__similar_day`: select the new columns, and add them to the empty-frame
  branch and the contract. Tests:
  - `not_null` on each rank column, with `where: similar_day_n_candidates >= <rank>`,
    and on the weighted mean.
  - `expression_is_true`: the distances do not decrease by rank.
  - `expression_is_true`: the weighted mean recomputed in SQL from the rank loads and
    distances matches within a relative 1e-9, on rows with five ranks and no zero
    distance.
- `ftr_period_similar_day`: pass the columns through, tag the six features with the
  expressions of §3, and extend its unit test's rows.
- `just feature-views` regenerates `views.py`, `fct_feature_value.sql` and
  `dim_feature.sql`. `fct_feature_value` gains about 5 × 48 × 2,714 ≈ 650,000 Tokyo rows.

## 7. Feature naming

`docs/Feature-Naming.md`:

- Rule 5 adds `rank` (which nearest day), `k` (how many) and `weight`, after
  `halflife`.
- The `SIMILAR_DAY` row of the primitives table gains `rank`.
- A new row: `SIMILAR_DAY_MEAN(x, gap, window, k, weight)`, the weighted mean of `x`
  over the `k` most similar of `window` candidate days. Example: the expression in §3.

CLAUDE.md's `fit_similar_day.py` and feature-mart bullets name the new columns.

## 8. Rollout

1. On `main`, run `scripts/fit_similar_day.py --area tokyo` (7–11 minutes) so the
   baseline sees the warehouse as it is now. Copy that run's rows to
   `pma_ml.similar_day_before_top_k`.
2. Drop `pma_ml.similar_day`.
3. On the branch, run the job, then `just dbt build --select stg_ml__similar_day+
   dim_feature`.
4. Compare the new run with the copy on every row: `similar_day_demand_kwh`,
   `similar_day_reference_date`, `similar_day_reference_lag_days`,
   `similar_day_distance`, `similar_day_n_candidates`, `similar_day_fit_cutoff` and
   `available_at` must be equal to the bit. Then drop the copy.

The drop removes the older scoring runs' partitions. No forecast reads them any more:
Feast serves the newest published run.

## 9. Tests

`tests/test_demand_similar_day.py`:
- The ranking's order and tie-break.
- Rank 1 equals `select`.
- Fewer candidates than k.
- Weights sum to 1; equal distances give equal weights; a zero distance takes all
  the weight; NaN ranks are ignored.

`tests/test_demand_similar_day_feature.py`:
- The rank loads halved.
- The weighted mean on a hand-computed day.
- `available_at` with a late-public ranked day.
- Each new frame check rejects a bad row.
- The published table's new columns.

`tests/test_fit_similar_day_script.py`: the new param and artifact.
`tests/conftest.py`: the `feature_marts` fixture gains the new columns.
Coverage stays at 100 %.

## 10. Proof

The PR shows:
- The §8 comparison: zero differences on the rank-1 columns.
- The dbt build passing.
- For the new run: the distribution of the five weights (largest and smallest
  weight per day), and a few days with their five days, distances and weights.

## 11. Out of scope

- A preset or an experiment with the new features.
- A plain (unweighted) mean.
- Ranks beyond 5.
- A load-difference weighting.
- A Kansai run: only Tokyo is scored, as today.
