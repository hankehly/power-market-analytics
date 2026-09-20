# The day types of the D-2, D-3 and D-7 lag days (#201) — design

Date: 2026-09-20. Status: **approved by the researcher on 2026-09-20 with one change, and
implemented**: `lag_3d_day_type` is added (decision 3). Branch:
`feature/issue-201-lag-day-types`. Feature candidate
[#201](https://github.com/hankehly/power-market-analytics/issues/201), suggested by Claude on
2026-09-20.

## 1. Goal

Three features in `ftr_day_calendar`: the day types of D-2, D-3 and D-7. The issue asked for
D-2 and D-7, and the researcher added D-3. The model reads the load of those days. It knows
D's day type and not theirs, so it cannot tell when a lag is a holiday's load.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions for the researcher

1. **Physical names, which never change:** `lag_2d_day_type`, `lag_3d_day_type` and
   `lag_7d_day_type`, after `lag_2d_demand_kwh`, `lag_3d_demand_kwh` and `lag_7d_demand_kwh`,
   the loads they describe.
2. **The same int codes as `day_type`:** 0 Weekday, 1 Weekend, 2 Holiday, a holiday winning
   over a weekend. Tagged categorical, as `day_type` is. The lag reads the mart's own
   `day_type` of the earlier day, so the rule stays written once.
3. **D-2, D-3 and D-7.** The draft had D-2 and D-7 only, as the issue says, and left D-3 to
   follow. The researcher's ruling: add `lag_3d_day_type` as well. The baseline preset reads
   `lag_3d_demand_kwh` too. The weekly lags beyond D-7 are #202's subject: a clean value in
   place of three more flags.
4. **Null where the earlier day is before `dim_date`'s first day**, 2016-01-01: the first
   two, three and seven days of the spine. The demand history starts on 2022-04-01, so
   no training row is null.
5. **`available_at` does not change.** The calendar is static and the mart's instant is its
   constant, 1900-01-01.
6. **No new primitive.** `LAG(day_type, 2d)` is `LAG` as `docs/Feature-Naming.md` has it.

## 3. Definition

`lag_2d_day_type(D) = day_type(D - 2)`, `lag_3d_day_type(D) = day_type(D - 3)` and
`lag_7d_day_type(D) = day_type(D - 7)`. Expressions, as the issue wrote them:
`LAG(day_type, 2d)`, `LAG(day_type, 3d)` and `LAG(day_type, 7d)`.

## 4. Measured

**The calendar**, the 1,614 delivery days from 2022-04-01 to 2026-08-31. It is the same for
every area.

| D's day type | Days | D-7: weekday / weekend / holiday | D-3: weekday / weekend / holiday | D-2: weekday / weekend / holiday |
|---|---:|---|---|---|
| Weekday | 1,052 | 958 / 0 / 94 | 573 / 411 / 68 | 601 / 394 / 57 |
| Weekend | 432 | 0 / 402 / 30 | 412 / 0 / 20 | 415 / 0 / 17 |
| Holiday | 130 | 94 / 30 / 6 | 69 / 19 / 42 | 37 / 37 / 56 |

- D-7 has D's weekday. So for a weekday or a weekend target, D-7 has D's own day type or is
  a holiday. The new information in `lag_7d_day_type` is that one case: 124 of 1,484 days,
  8 %.
- `day_of_week` already says when D-2 or D-3 is a weekend day. The new information in
  `lag_2d_day_type` and `lag_3d_day_type` is the holiday: 74 and 88 of the 1,484 days, 5 %
  and 6 %.
- What `days_since_holiday` already says. On the 124 days with a holiday D-7 it equals 7 on
  50, 40 %: a nearer holiday hides D-7 on the rest. On the 88 with a holiday D-3 it equals 3
  on 55, 63 %, and on the 74 with a holiday D-2 it equals 2 on 55, 74 %. R-005 E-003 rejected
  the two holiday distances, +6.5 %, on a baseline that had only the D-7 lag.

**The error where the lag days are holidays**, from the issue. Run `bd96e6e3…`
(`lightgbm_msm_popw_daytype_simday_lags_weather`, Tokyo, 2024-08-18 to 2026-08-17, from before
the 2026-09-14 similar-day switch), weekday targets, kWh per 30-minute period:

| Weekday targets | Days | MAE | Bias |
|---|---:|---:|---:|
| D-7 a weekday | 428 | 555,262 | −17,146 |
| D-7 a holiday | 46 | 564,057 | −107,317 |
| D-7 a weekday, D-2 not a holiday | 404 | 550,819 | −19,404 |
| D-7 a weekday, D-2 a holiday | 24 | 630,055 | +20,865 |

Facts for the experiment to weigh, not a verdict on the candidate.

## 5. Build

As built.

- `ftr_day_calendar`: `day_type` is a step of its own, and `final` joins that step three
  times on the date, at D-2, D-3 and D-7. Joins on the date, not `lag()` over rows, so a gap
  in the spine could not shift a value.
- The dbt unit test, written first, on ten real days around 成人の日, Monday 2025-01-13.
  Against a scaffold of nulls it failed on exactly its eight rows that expect a value.
  - Tuesday 01-14 reads Sunday 01-12 at D-2 and Saturday 01-11 at D-3: both 1;
  - Wednesday 01-15 reads the holiday at D-2 and Thursday 01-16 reads it at D-3: 2;
  - Monday 01-20 reads it at D-7: 2, and the Monday after reads a working day: 0;
  - a day whose earlier day is not in `dim_date` gives null.
- The three columns in the YAML: `int`, `categorical: true`, the expressions, and
  `accepted_values` 0, 1, 2. No `not_null`, by decision 4.
- The check against the real data, 39,447 rows, nine areas, 2016 to 2027: each column equals
  `day_type` joined on the date on every row; 18, 27 and 63 nulls, the first two, three and
  seven days of each area, and no other; all 15 earlier columns identical against a copy
  taken before the build.
- `just feature-views`, the fixture's three columns in `tests/conftest.py` from
  `synthetic_day_type` of the earlier day, the two tests that name the calendar's
  categoricals, `CLAUDE.md`.

## 6. Out of scope

The day types of the weekly lags beyond D-7, which #202 answers with a clean value.
`LAG(special_period, 7d)`, which
the issue names as a follow-up. The rank-1 similar day's day type: a different reference,
and its pool already leaves out special days.
