# Holiday degree (休日度合い) on `dim_date` — design

Date: 2026-09-05. Status: **implemented** on 2026-09-05. Branch:
`feature/dim-date-holiday-degree`.

## 1. Goal

Add one column to `dim_date`: `holiday_degree`, a double from 0 to 1 that grades how much
of a non-working day each date is. It is the 休日度合い of the 新日本製鐵 patent JP 4448226 B2
(伊勢・藤崎, filed 2000; row in `docs/research/papers.md`), expressed with this warehouse's
holiday calendar. The demand model can then read the degree, or a window of it, as a
feature instead of the 0/1 holiday flag.

## 2. Decisions

1. **One column only.** The type that wins (1, 2 or 3) is not exposed. It can be added
   later if a model wants it as a categorical.
2. **Double, not decimal.** The value lands in a pandas float column as a LightGBM feature.
3. **The special periods are the ones `dim_date` already uses**: the family-A TSO 休日 set
   plus the 祝日 those days adjoin, so 年末年始 12/30 to 1/3, ゴールデンウィーク 4/29 to 5/5
   and お盆 8/13 to 8/16. The patent's worked example treats 年末年始 as 12/30 to 1/2. The
   visible difference is 1/3: this column gives it 1.0, the patent's example gives 0.5.
4. **"Off day" for the sandwiched rule means any day above 0 on type 1 or type 2.** That
   is exactly `not is_business_day`. Three or more working days in a row score 0.
5. **A spine edge with no neighbour counts as not off.** Both edges are holidays anyway
   (1/1 and 12/31), so the rule never fires there.

## 3. Definition

`holiday_degree = greatest(type_1, type_2, type_3)`.

| type | rule | value |
|---|---|---|
| 1, calendar | Sunday, or a 国民の祝日 from the seed | 1.0 |
| | Saturday | 0.8 |
| | Monday to Friday | 0 |
| 2, special period | first day of a period: 12/30, 4/29, 8/13 | 0.8 |
| | any other day of 12/30–1/3, 4/29–5/5, 8/13–8/16 | 1.0 |
| | outside the periods | 0 |
| 3, sandwiched | one working day with an off day on each side (飛び石連休の中日) | 0.5 |
| | each of two consecutive working days with an off day on each side (二飛び石連休の中日) | 0.3 |
| | otherwise | 0 |

Type 3 is evaluated only on working days (`is_business_day`); it is 0 on every other day.

Worked examples, checked against the seed with a throwaway Python reference on
2026-09-05:

| date | why | degree |
|---|---|---|
| 2026-08-10 (Mon) | between Sunday 8/9 and 山の日 8/11; the R-004 proximity day | 0.5 |
| 2026-08-12 (Wed) | between 山の日 and 8/13, the first day of お盆 | 0.5 |
| 2026-05-07, 05-08 (Thu, Fri) | between the 5/6 振替休日 and Saturday 5/9 | 0.3 each |
| 2023-11-24 (Fri) | between 勤労感謝の日 and a Saturday | 0.5 |
| 2024-12-30 (Mon) | first day of 年末年始 | 0.8 |
| 2024-12-31, 2025-01-03 | inside 年末年始 | 1.0 |
| 2025-04-29 (Tue) | 昭和の日 (type 1) beats the period's first-day 0.8 | 1.0 |
| 2025-08-13 (Wed) | first day of お盆 | 0.8 |

Expected counts over the 2016-01-01 to 2027-12-31 spine (4,383 days) from the same
reference: 1.0 × 906, 0.8 × 609, 0.5 × 36, 0.3 × 68, 0 × 2,764. Checked on 2026-09-05:
the built column equals the reference on every day, and the counts match.

## 4. Build

In `dbt/models/curated/dim_date.sql`, after the current `final` CTE:

- `graded`: type 1 from `day_of_week_iso` and the seed join, type 2 from month/day rules.
- `neighbours`: `lag`/`lead` of `is_business_day` one and two days each way, ordered by
  `date_key`. The spine has no gaps, so row offsets are day offsets.
- type 3 from the neighbours, then `greatest` of the three as `holiday_degree`.

No new seed, no new join. The column is appended to the contract in `dim_date.yml`.

## 5. Tests

`dim_date.yml`:

- `not_null` and `accepted_values` (0, 0.3, 0.5, 0.8, 1.0) on the column.
- Expression tests: a Sunday or a seed 祝日 is 1.0; a non-business day is at least 0.8; a
  business day is 0, 0.3 or 0.5.

Singular test `dbt_tests/assert_dim_date_holiday_degree_examples.sql`: the worked examples
above, failing on any row whose degree differs.

## 6. Docs

- Column description in `dim_date.yml`.
- The `dim_date` block in `docs/README.md` and the `dim_date` bullet in `CLAUDE.md`.
- The patent row's "Cited by" cell in `docs/research/papers.md` names this column.

No Python changes: the demand loader selects named columns, and the test fixture's
three-column `dim_date` still satisfies it.

## 7. Validation

`just dbt build --select dim_date` in the devcontainer (contract, tests), the value counts
above, and a full-column diff against the Python reference. `just dbt parse` for CI. Then
one commit, `feat(dbt): holiday degree (休日度合い) on dim_date`, and a PR through the
Codex-then-Copilot loop.

## 8. Not in scope

A feature or strategy that uses the column. The 15-day pattern and its compression in the
patent. Exposing the winning type.
