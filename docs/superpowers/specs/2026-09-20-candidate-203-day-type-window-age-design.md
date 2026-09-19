# The age of the day-type window's days (#203) — design

Date: 2026-09-20. Status: **approved by the researcher on 2026-09-20 as written, and
implemented**. Branch: `feature/issue-203-day-type-window-age`. Feature candidate
[#203](https://github.com/hankehly/power-market-analytics/issues/203), suggested by Claude on
2026-09-20.

## 1. Goal

Two features next to `mean_daytype_4d_demand_kwh` and `ewm_daytype_4d_demand_kwh`, as the
issue describes them: how many days back the window's newest day lies, and its oldest.

The window is the last four complete days of D's day type at or before D-2, with no bound
on how far back it reaches. The model reads its two means and cannot tell a window of last
week's days from one that reaches back a season. #138 gave the similar day the same kind of
fact, its lag in days.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions for the researcher

1. **The mart is `ftr_period_actuals`, not `ftr_day_actuals` as the issue says.** The values
   are one per day. But they describe two columns of the period mart, and the lookup that
   finds the four days lives there. #138 is the precedent: `similar_day_rank1_lag_days` is
   one value per day in the period similar-day mart, next to the load it describes. In
   `ftr_day_actuals` the lookup would be written a second time, or moved to a model both
   marts read, a layer the repo does not have. Feast joins either the same way.
2. **Physical names, which never change:** `newest_daytype_4d_lag_days` and
   `oldest_daytype_4d_lag_days`, after `mean_daytype_4d_demand_kwh` and
   `similar_day_rank1_lag_days`.
3. **Expressions are the names**, as #138's are. The issue drafted
   `DAYS_SINCE(is_complete_day, gap=2d, rank=4) by day_type`: two new arguments and a flag
   that is no column, for one use.
4. **With fewer than four days in the window, the oldest is the oldest present.** It happens
   at the start of the history only: nine Tokyo delivery days.
5. **`int`, not categorical. Null where the window is**: no complete day of D's day type at
   or before D-2.
6. **`available_at` does not change.** The two dates are those of days the window already
   reads and already waits for.

## 3. Definition

With d1 > d2 > … the newest complete days of D's day type at or before D-2, up to four:

- `newest_daytype_4d_lag_days = D - d1`
- `oldest_daytype_4d_lag_days = D - dn`, n the number of days present, 1 to 4.

A complete day has all 48 periods, the window's own rule. So the 48 periods of a day read
the same four days, and both values are the same on all 48 rows.

The mart has rows up to 28 days after the last actual. There the newest day falls further
behind with every row, and both ages grow past the numbers below.

## 4. Measured

Tokyo, the delivery days from 2022-04-04 to 2026-08-31, with the window's own rule. Kansai
is the same but for one weekend day.

| D's day type | Days | Newest: min / median / max | Oldest: min / median / max | Oldest over 30 days back |
|---|---:|---|---|---:|
| Weekday | 1,051 | 2 / 2 / 12 | 3 / 7 / 15 | 0 |
| Weekend | 430 | 6 / 7 / 14 | 7 / 15 / 22 | 0 |
| Holiday | 128 | 2 / 6 / 76 | 2 / 60 / 111 | 103, 80 % |

- On weekdays and weekends the window is always fresh, so the columns say little there. The
  newest day is more than a week back on 15 weekday and 25 weekend targets, after the long
  holidays.
- On holidays they vary widely. The newest day is more than a week back on 62 of the 128.
- Fewer than four days: 4 weekday, 2 weekend and 3 holiday targets, all in April and May
  2022.
- The error by the window's age, from the issue: run `bd96e6e3…`, Tokyo, 2024-08-18 to
  2026-08-17, the ages from `dim_date` alone. Holiday targets whose oldest day is 33 to 108
  days back, 50 days, have MAE 659,527 kWh per 30-minute period. Those at 5 to 27 days, 10
  days, have 581,042.

Facts for the experiment to weigh, not a verdict on the candidate.

## 5. Build

As built.

- `ftr_period_actuals`: `candidate_periods` also carries the date of the oldest of its four
  days, by the same `lag()` that reads their loads, the oldest present when there are fewer.
  `day_type_windows` takes the two differences from the delivery date. No new join and no
  new row.
- The dbt unit test, written first. The mart's all-columns test gained the two ages on its
  Saturdays. Against a scaffold of nulls it failed on exactly its eight Saturday targets,
  384 rows. It covers:
  - a window of one, two and three days: the oldest is the oldest present, 7, 14 and 21
    days back;
  - four days: 7 and 28;
  - an incomplete day is skipped, so the ages jump over it: 14 and 35 on 03-08, then 21 and
    42, then 28 and 49;
  - a target with no complete day of its type gives null;
  - the 48 periods of a day agree.
- The draft also listed a weekday target after a run of holidays and a holiday target whose
  days span months, as a test of their own. That test was not written: a SQL expectation
  must supply every column of the mart, and 32 days of 48 periods as rows would be
  unreadable. The real data proves both cases instead, on every row.
- The check against the real data, 159,168 rows, 3,316 area-days, Tokyo and Kansai: both
  columns equal section 4's computation, written apart from the mart, on every day; the 48
  periods agree on every day; the ages are null exactly where the window is, on six days,
  2022-04-03, 04-29 and 04-30 in both areas; all 33 earlier columns, every row and
  `available_at` identical against a copy taken before the build.
- `just feature-views`, the fixture's two columns in `tests/conftest.py`, the test that
  lists the mart's fields, `CLAUDE.md`. No change to `docs/Feature-Naming.md`, by decision 3.

## 6. Out of scope

The ages of the mart's other windows: the weekly lags and D-2 to D-6 are fixed by their
definition. How far back #202's window reached, which can follow #202. The similar day's
lag, which is #138's and built.
