# The similar day's distance and lag as features (#138) — design

Date: 2026-09-19. Status: **approved by the researcher on 2026-09-19 as written, and
implemented**. Branch: `feature/issue-138-similar-day-distance-and-lag`. Feature candidate
[#138](https://github.com/hankehly/power-market-analytics/issues/138), suggested by Claude on
2026-09-15.

## 1. Goal

Two features next to the rank-1 similar day's load, as the issue describes them: how far
that day is from D by the selector's distance, and how many days back it lies.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions for the researcher

1. **No change to the fit-and-score job, and no new scoring run.** Both values are in
   `ftr_period_similar_day` already: `similar_day_rank1_distance` is a column without a
   feature tag, and the lag is `trade_date` minus `similar_day_rank1_reference_date`. So
   `pma_ml.similar_day` is not dropped and no day is scored again.
2. **Physical names, which never change:** `similar_day_rank1_distance`, the column as it
   is, and a new `similar_day_rank1_lag_days`. Neither is a retired name.
3. **Expressions are the names**, as the issue wrote them: columns passed through.
4. **On a same-holiday day the distance is null.** Those days are not ranked, so they have
   no distance, and LightGBM reads NaN. The lag is known on them: the days back to the same
   holiday last year. The issue does not say otherwise, so nothing is filled in.
5. **Rank 1 only.** Ranks 2 and 3 have distances and dates too. The issue does not ask for
   them.
6. **`available_at` and `published_at` do not change.** Both values are in the row already.

## 3. Measured, Tokyo, the newest scoring run of each day, 2,714 days

`ftr_period_similar_day` holds Tokyo only.

| | Ranked by similarity | Same holiday last year |
|---|---|---|
| Days | 2,521 | 193, 7 % |
| Distance: null / min / median / max | 0 / 0.055 / 0.230 / 0.854 | 193 / — / — / — |
| Lag in days: null / min / max | 0 / 2 / 394 | 0 / 361 / 374 |
| Rank 1 from the last 30 days | 1,467, 58 % | 0 |
| Rank 1 from a year back | 1,054, 42 % | 193 |

The lag takes values in two bands, 2 to 31 and 335 to 394, and nothing between: the pool
has no other days.

## 4. Build

- `ftr_period_similar_day.yml`: the feature tag, `categorical: false` and an expression on
  `similar_day_rank1_distance`; the new column with the same.
- `ftr_period_similar_day.sql`: `datediff(trade_date, similar_day_rank1_reference_date)`.
- A dbt unit test, written first: a ranked row gives its lag and keeps its distance; a
  same-holiday row gives its lag and a null distance. Every published row has a rank-1
  day, so the lag is tested not null.
- The check against the real data: no earlier column moved against a copy taken before the
  build; the lag's two bands as in section 3.
- `just feature-views`, the fixture's two columns in `tests/conftest.py`, `CLAUDE.md`.

## 5. Out of scope

Ranks 2 and 3. The candidate count and the fit cutoff, which the mart also carries. #129,
the ranks' loads and their mean: those columns are tagged already.
