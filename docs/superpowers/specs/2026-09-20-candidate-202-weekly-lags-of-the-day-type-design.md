# Weekly lags kept to the delivery day's day type (#202) — design

Date: 2026-09-20. Status: **approved by the researcher on 2026-09-20 as written, and
implemented**. Branch: `feature/issue-202-weekly-lags-of-the-day-type`. Feature candidate
[#202](https://github.com/hankehly/power-market-analytics/issues/202), suggested by Claude on
2026-09-20.

## 1. Goal

Two columns in `ftr_period_actuals`, as the issue describes them: the mean and the 8:4:2:1
weighted mean at the period over the newest four weekly lags, D-7, D-14 and so on, whose day
has D's day type.

`mean_weekly_lags_demand_kwh` and `ewm_weekly_lags_demand_kwh` take D-7 to D-28 whatever
those days were. `ewm_daytype_4d_demand_kwh` keeps the day type and loses the weekday: a
Monday's window is Friday back to Tuesday. These keep both.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions for the researcher

1. **Physical names, which never change:** `mean_daytype_weekly_lags_demand_kwh` and
   `ewm_daytype_weekly_lags_demand_kwh`, after `mean_weekly_lags_demand_kwh` and
   `mean_daytype_4d_demand_kwh`.
2. **The look-back is eight weeks**, D-7 to D-56: the first four, newest first, that have
   D's day type and a value at the period. The issue left the bound to the build. Measured:
   every weekday target finds its four within seven weeks, every weekend target within five.
3. **The values present at the period, not complete days.** These columns clean the plain
   weekly means, which read whatever value the period has. So where the four plain weekly
   lags are all present and have D's day type, the new column equals the old one to the
   bit, and the build checks that on every row. `ewm_daytype_4d_demand_kwh` needs complete days so that every
   period sees the same four days. Here the calendar fixes the candidates, and a hole only
   sends that one period a week further back.
4. **Fewer than four values give the mean of those present; none gives null.** The mart's
   rule for its other windows. A holiday target mostly has none: of the 130 holidays
   measured, 92 have no holiday among D-7 to D-56 and 38 have one to three. The other choice
   is null on every holiday target. Claude's suggestion: keep the one rule, and let
   `ewm_daytype_4d_demand_kwh` serve the holidays.
5. **The weights 8, 4, 2, 1 go by order of use**, the newest value used taking 8 even when it
   is D-14. `ewm_daytype_4d_demand_kwh` weighs its days the same way.
6. **The window's days feed `available_at` through `greatest()`**, like every input. It
   should move on no row. A window day within 28 days is already one of the row's weekly
   lags, and an older one is older than every lag the row has. The build proves it.
7. **No new primitive.** `… by day_type` exists, and `step=7d` keeps the weekday.

## 3. Definition

For period p of delivery day D with day type τ, the candidates are `D - 7k`, k = 1 … 8. The
values used are those of the first four k, smallest first, whose day has day type τ and a
demand at p.

- The mean is their sum over their count.
- The weighted mean is `Σ w·y / Σ w`, with w = 8, 4, 2, 1 in the order of use.

Demand is a whole number of kWh, so each sum is exact and each column is one division: no
order of addition to fix.

Expressions, as the issue wrote them:
`ROLLING_MEAN(demand_kwh, gap=7d, window=4, step=7d) by day_type` and
`EWA(demand_kwh, gap=7d, window=4, step=7d, halflife=1) by day_type`. The eight-week bound
goes in the column's description, where `docs/Feature-Naming.md` puts such rules.

## 4. Measured

**How far back the four lie**, from the calendar, the 1,614 delivery days from 2022-04-01 to
2026-08-31:

| D's day type | Days | Four within D-7 … D-28 | Need a 5th week | A 6th | A 7th | An 8th |
|---|---:|---:|---:|---:|---:|---:|
| Weekday | 1,052 | 700 | 309 | 41 | 2 | 0 |
| Weekend | 432 | 316 | 116 | 0 | 0 | 0 |

So a third of the weekday targets, 352 of 1,052, have another day type among their four
plain weekly lags. Of the 130 holiday targets none finds four within eight weeks, and one
does within twelve.

**The columns, from a prototype of the definition**, against the mart's plain weekly mean.
Rows with an actual, 2022-06-01 to 2026-08-31, MWh per 30-minute period:

| | Rows | Differs from the plain mean | Mean absolute difference, MWh | Null |
|---|---:|---:|---:|---:|
| Tokyo, weekday targets | 48,672 | 15,886, 33 % | 724 | 0 |
| Tokyo, weekend targets | 19,930 | 5,335, 27 % | 371 | 0 |
| Tokyo, holiday targets | 5,904 | 1,776, all that have a value | 2,083 | 4,128 |
| Kansai, weekday targets | 48,672 | 15,888, 33 % | 392 | 0 |
| Kansai, weekend targets | 19,946 | 5,272, 26 % | 186 | 0 |
| Kansai, holiday targets | 5,904 | 1,776, all that have a value | 1,212 | 4,128 |

On 94,836 of the 149,028 rows the four plain weekly lags are all present with D's day
type, and there both new columns equal the mart's plain ones on every row. That is decision 3's check, run on
the prototype.

**Each mean as a forecast of D by itself**, weekday targets, 2024-08-18 to 2026-08-17, on
the rows where the plain window held another day type. MAE, MWh per 30-minute period:

| | Rows | Plain mean | Day-type mean | Plain weighted | Day-type weighted |
|---|---:|---:|---:|---:|---:|
| Tokyo | 8,158 | 1,339.3 | 1,343.5, +0.3 % | 1,275.1 | 1,162.3, −8.8 % |
| Kansai | 8,160 | 736.1 | 695.8, −5.5 % | 675.1 | 596.3, −11.7 % |

The weighted mean gains in both areas. The plain mean gains in Kansai and not in Tokyo:
reaching a week further back costs it about what the clean day gives. The model error
behind the idea is in the issue: on run `bd96e6e3…` the weekday under-forecast grows from
+418 to −57,239 to −235,320 kWh with zero, one and two holidays among D-7 to D-28.

Facts for the experiment to weigh, not a verdict on the candidate.

## 5. Build

As built.

- `ftr_period_actuals`: a day × period spine per area with no gap, from its first actual to
  the mart's last row, left-joined to the actuals and the calendar. On it the eight weekly
  values are read with `lag()` over rows, exact because the spine has no gap, each with its
  day's type and its availability. An array of the eight is filtered to D's day type and
  the values present, cut to four, and summed with integer weights.
- The result is left-joined to `by_period`, so the mart's rows do not change. Not more
  shifts: the shift union is the row spine and the input of `available_at`, the lesson of
  #149's 28-day range.
- dbt unit tests, written first. Against a scaffold of nulls both failed on exactly the rows
  that expect a value.
  - The mart's all-columns test gained the two columns on its Saturdays: through 03-01 they
    equal the plain weekly means; from 03-08 on, where the plain ones lose D-7 to the
    all-null Saturday, these reach back to 02-01 and give 235 and 292, the weight 8 going
    to the newest value used.
  - A test of its own, `ftr_period_actuals_day_type_weekly_lags_skip_other_day_types`: ten
    Wednesdays in a calendar with five holidays, 82 rows, the expected values and
    `available_at` from a reference written in Python from section 3. A holiday at D-7 is
    skipped and the weights go by order of use; a working day with a value at D-63 is never
    read; a window skips four holidays in its middle and reaches D-56; a holiday target
    reads four holidays, one, or none (null); a hole at one period sends that period
    further back and the period beside it not; a target on another weekday is null.
  - The same test replaces one file late. The three rows that read it through these weekly
    lags alone wait for it, and no other row's `available_at` moves. With the window taken
    out of `greatest()` on purpose the test failed on exactly those three rows.
- The check against the real data, 159,168 rows, Tokyo and Kansai:
  - decision 3's equality holds on all 96,048 rows whose four plain weekly lags are present
    with D's day type;
  - both columns equal an independent computation, joins on the date and a ranking where
    the mart uses a spine and arrays, on every row;
  - all 35 earlier columns, every row and `available_at` are identical against a copy taken
    before the build. So the window moved no `available_at`, as decision 6 expected.
- The built columns give section 4's forecast table again, to the digit.
- `just feature-views`, the fixture's two columns in `tests/conftest.py`, the test that
  lists the mart's fields, the `… by col` row of `docs/Feature-Naming.md` with a `step=7d`
  example, `CLAUDE.md`.

## 6. Out of scope

The other weekly statistics by day type: the trend, the standard deviations, the median,
the z-score and the ramps. The daily weekly means of `ftr_day_actuals`. How far back the
window reached, as a feature: #203 asks that of the day-type window. #201's flags, which
say a lag is a holiday's where this gives a clean value.
