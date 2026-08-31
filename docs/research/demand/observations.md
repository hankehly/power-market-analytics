# Observation Log — demand

Record notable demand-forecast behavior here before trying to explain it.
Record only observations and ideas supplied by the researcher or directly
established from the cited evidence; do not generate possible explanations or
hypotheses on the researcher's behalf unless explicitly asked. Conventions,
IDs and statuses: [research README](research/README.md); scope defaults:
[demand README](research/demand/README.md).

---

## O-001 — Holidays dominate the worst days and are over-forecast

- **Recorded:** 2026-08-25
- **Data period:** 2024-08-18 through 2026-08-17 (729 delivery days)
- **Strategy:** `lightgbm_msm_popw` (the demand baseline since R-002)
- **Area:** tokyo
- **MLflow run:** [`2556e3f2b94c4cf59efc6b2fff1bddef`](http://localhost:5005/#/experiments/2/runs/2556e3f2b94c4cf59efc6b2fff1bddef)
- **Status:** Investigated
- **Related investigations:** [R-003 — Day type as a categorical feature](research/demand/R-003-day-type-feature.md)

### Observation

In the Superset **Demand Forecast Analysis** dashboard's **Worst days** table
(the 20 delivery days with the highest daily MAE) for the run above, the
researcher noticed that many of the worst days fall on holidays, and that on
those days the forecast is above the actual — the model over-forecasts, i.e.
less electricity is used than forecast.

Queried from `fct_demand_forecast_accuracy` joined to `dim_date` (day type as
the compare script defines it: *Holiday* when `dim_date.is_holiday` — a
国民の祝日 or, since 2026-08-25, a customary non-working day 年末年始 12/30–1/3,
ゴールデンウィーク 4/30–5/2, お盆 8/13–16 — else *Weekend* on a Saturday/Sunday,
else *Weekday*; daily MAE and bias = the mean over the day's 48 periods of
|forecast − actual| and forecast − actual, in kWh per 30-minute period):

| # | Delivery day | Weekday | Day type | Holiday | Daily MAE (kWh) | Daily bias (kWh) |
|---:|---|---|---|---|---:|---:|
| 1 | 2026-01-01 | Thu | Holiday | 元日 | 4,874,440 | +4,874,440 |
| 2 | 2025-01-01 | Wed | Holiday | 元日 | 4,245,892 | +4,245,892 |
| 3 | 2026-01-02 | Fri | Holiday | 年末年始 | 4,089,108 | +4,089,108 |
| 4 | 2025-12-31 | Wed | Holiday | 年末年始 | 3,826,590 | +3,826,590 |
| 5 | 2025-08-13 | Wed | Holiday | お盆 | 3,691,456 | +3,691,456 |
| 6 | 2024-12-31 | Tue | Holiday | 年末年始 | 3,538,519 | +3,538,519 |
| 7 | 2025-01-03 | Fri | Holiday | 年末年始 | 3,487,994 | +3,487,994 |
| 8 | 2025-08-12 | Tue | Weekday | — | 3,345,966 | +3,345,966 |
| 9 | 2024-12-20 | Fri | Weekday | — | 3,140,294 | −3,140,294 |
| 10 | 2025-01-02 | Thu | Holiday | 年末年始 | 3,131,748 | +3,131,748 |
| 11 | 2024-12-30 | Mon | Holiday | 年末年始 | 3,126,408 | +3,126,408 |
| 12 | 2025-08-14 | Thu | Holiday | お盆 | 3,069,397 | +3,069,397 |
| 13 | 2026-02-08 | Sun | Weekend | — | 2,862,238 | −2,862,238 |
| 14 | 2025-08-15 | Fri | Holiday | お盆 | 2,821,602 | +2,821,602 |
| 15 | 2025-08-06 | Wed | Weekday | — | 2,559,708 | −2,559,708 |
| 16 | 2026-08-12 | Wed | Weekday | — | 2,555,156 | +2,555,156 |
| 17 | 2026-08-13 | Thu | Holiday | お盆 | 2,552,324 | +2,552,324 |
| 18 | 2024-09-23 | Mon | Holiday | 休日 (振替休日) | 2,509,672 | +2,509,672 |
| 19 | 2026-05-05 | Tue | Holiday | こどもの日 | 2,445,803 | +2,445,803 |
| 20 | 2025-05-05 | Mon | Holiday | こどもの日 | 2,298,120 | +2,298,120 |

- 15 of the 20 worst days are holidays, although holidays are 60 of the 729
  days (8.2 %); the seven worst days are all holidays (元日 ×2, 年末年始 ×4,
  お盆 ×1). On every one of the 15 the daily bias equals the daily MAE: all 48
  periods are over-forecast.
- The five non-holidays split both ways: 2025-08-12 and 2026-08-12 (the
  working days before お盆) are over-forecast by 3.35 M and 2.56 M kWh,
  2024-12-20, 2026-02-08 and 2025-08-06 are under-forecast.
- By day type over the whole window:

| Day type | Days | Points | MAE (kWh) | Bias (kWh) | MAPE |
|---|---:|---:|---:|---:|---:|
| Holiday | 60 | 2,880 | 1,760,466 | +1,688,435 | 12.58 % |
| Weekday | 474 | 22,752 | 665,046 | −209,608 | 3.82 % |
| Weekend | 195 | 9,322 | 564,823 | −43,863 | 3.74 % |

  Holiday MAE is 2.6× the weekday MAE, and 96 % of it is bias (+1,688,435 of
  1,760,466 kWh): the holiday error is almost entirely a systematic
  over-forecast. 59 of the 60 holidays have a positive daily bias (the one
  exception is 2026-02-11 建国記念の日, −1,130,018 kWh). The largest relative
  errors are 元日 (MAPE 32 % and 37 %), the 年末年始 days (15–28 %), the お盆
  weekdays (17–23 %) and こどもの日 (21–22 %); the holidays with the smallest
  errors fall on Saturdays and Sundays (e.g. 2025-11-23 勤労感謝の日, a Sunday:
  MAE 233,536 kWh).

**Researcher's reading (recorded as supplied):** the model is not given the
day category during training, so it has no guidance that these days represent
the special human behaviour of not working, which changes electricity usage;
the over-forecast is attributed to there being less commercial activity on
holidays, hence less electricity used than forecast. The model's calendar
features are `time_code`, `month` and `day_of_week` only (plus the D-7 demand
lag and the temperature features), so a holiday on a Monday–Friday carries the
same calendar input as any working day of that weekday.

### References

- MLflow run: [`2556e3f2b94c4cf59efc6b2fff1bddef`](http://localhost:5005/#/experiments/2/runs/2556e3f2b94c4cf59efc6b2fff1bddef)
  (the R-002 E-001 candidate, `lightgbm_msm_popw-tokyo`)
- Superset dashboard: **Demand Forecast Analysis** → **Worst days** (top 20
  by daily MAE, with day type) and the day-type slices of the error section;
  the tables above are the same numbers queried from
  `pma_curated.fct_demand_forecast_accuracy` × `pma_curated.dim_date` on
  2026-08-25.

---

## O-002 — The working day between 山の日 and お盆 is heavily over-forecast, driven by the D-7 lag

- **Recorded:** 2026-08-27
- **Data period:** 2024-08-18 through 2026-08-17 (729 delivery days)
- **Strategy:** `lightgbm_msm_popw_daytype` (the demand baseline since R-003)
- **Area:** tokyo
- **MLflow run:** [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae)
  (the dashboard's default run: the R-003 E-001 strategy re-run on 2026-08-26
  with TreeSHAP contributions; same forecasts and MAE as the E-001 candidate
  `7ce89125…`)
- **Status:** Unreviewed
- **Related investigations:** [R-003 — Day type as a categorical feature](research/demand/R-003-day-type-feature.md)
  (its *Open questions* already record that the weekday before a holiday got
  worse, +17 %, with 2025-08-12 and 2026-08-12 the candidate's worst and
  third-worst days);
  [O-001](research/demand/observations.md#o-001-holidays-dominate-the-worst-days-and-are-over-forecast)
  (both days were among the R-002 baseline's 20 worst, over-forecast)

### Observation

In the Superset **Demand Forecast Analysis** dashboard for the run above, the
researcher noticed that 2026-08-12 and 2025-08-12 are heavily over-forecast,
and read the **Explanation (SHAP)** tab's per-day decomposition (mean per
period) for them:

- the `lag_7d_demand_kwh` contribution pushes the forecast up, because the
  D-7 day (2026-08-05) was high;
- the forecast temperature (`popw_forecast_temperature_c`) counteracts this,
  but the D-7 lag is overpowering.

Both days are the single working day squeezed between two off-days — 山の日
(8/11) and the first お盆 day (8/13; `dim_date.is_holiday` covers お盆
8/13–16 since 2026-08-25). Queried from `fct_demand_forecast_accuracy` ×
`dim_date` (daily MAE and bias = the mean over the day's 48 periods of
|forecast − actual| and forecast − actual, kWh per 30-minute period):

| Delivery day | Weekday | Day type | Holiday | Mean forecast (kWh) | Mean actual (kWh) | Daily MAE (kWh) | Daily bias (kWh) | MAPE |
|---|---|---|---|---:|---:|---:|---:|---:|
| 2025-08-10 | Sun | Weekend | — | 15,962,490 | 14,805,104 | 1,157,386 | +1,157,386 | 7.60 % |
| 2025-08-11 | Mon | Holiday | 山の日 | 15,606,076 | 15,335,729 | 661,546 | +270,347 | 4.35 % |
| **2025-08-12** | Tue | Weekday | — | 19,809,482 | 15,950,833 | 3,858,649 | +3,858,649 | 23.41 % |
| 2025-08-13 | Wed | Holiday | お盆 | 16,939,860 | 15,561,792 | 1,378,069 | +1,378,069 | 8.73 % |
| 2026-08-10 | Mon | Weekday | — | 17,857,409 | 16,325,750 | 1,531,659 | +1,531,659 | 8.86 % |
| 2026-08-11 | Tue | Holiday | 山の日 | 14,100,452 | 13,852,146 | 414,818 | +248,306 | 2.91 % |
| **2026-08-12** | Wed | Weekday | — | 17,007,182 | 14,169,792 | 2,837,390 | +2,837,390 | 19.70 % |
| 2026-08-13 | Thu | Holiday | お盆 | 14,705,247 | 14,101,604 | 604,150 | +603,643 | 4.17 % |

2025-08-12 is the run's worst day and 2026-08-12 its third-worst; on both,
all 48 periods are over-forecast (daily bias = daily MAE). The Monday before
山の日 2026 (2026-08-10) is also over-forecast, by +1,531,659 kWh.

The per-day decomposition as the dashboard shows it (mean per period of the
TreeSHAP contributions in `fct_demand_forecast_contribution`; `base` is the
model's expected value; feature values are the means of what the model saw;
`base` + Σ contributions = the mean forecast):

| Component | 2025-08-12 feature value | 2025-08-12 contribution (kWh) | 2026-08-12 feature value | 2026-08-12 contribution (kWh) |
|---|---:|---:|---:|---:|
| base | — | 16,024,867 | — | 15,926,426 |
| time_code | 24.5 | −218,111 | 24.5 | −4,420 |
| month | 8 | +63,825 | 8 | +17,244 |
| day_of_week | 1 (Tue) | +168,996 | 2 (Wed) | +199,310 |
| wavg_temperature_c | 27.64 °C | +137,862 | 27.46 °C | +290,316 |
| lag_7d_demand_kwh | 22,337,604 | +1,645,623 | 17,371,000 | +743,499 |
| popw_forecast_temperature_c | 27.99 °C | +1,446,555 | 24.64 °C | −637,310 |
| day_type | 0 (Weekday) | +539,865 | 0 (Weekday) | +472,117 |
| **Forecast** | | **19,809,482** | | **17,007,182** |
| Actual | | 15,950,833 | | 14,169,792 |

- On 2026-08-12 the reading holds as stated: `lag_7d_demand_kwh` is the
  largest positive contribution (+743,499 kWh per period; +2,107,491 in the
  daytime periods 08–18) and `popw_forecast_temperature_c` the only sizeable
  negative one (−637,310; −646,383 in the daytime), and the lag outweighs it.
  `day_type` (Weekday) and `wavg_temperature_c` add another +472,117 and
  +290,316.
- On 2025-08-12 the lag is again the largest contribution (+1,645,623;
  +2,825,973 in the daytime), but the forecast temperature does not counteract
  it — at 27.99 °C (29.62 °C in the daytime) it adds +1,446,555 (+2,368,341
  in the daytime), and `day_type` +539,865. The daytime bias is +5,333,359 kWh
  (MAPE 29.4 %).
- The D-7 days the lag carried (Tokyo actuals from
  `fct_area_demand_generation_actual`): 2026-08-05 (Wed) averaged
  17,371,000 kWh per period with a 21,937,000 peak (16:00–16:30) against
  14,169,792 / 16,780,000 on 2026-08-12 — the target day ran at 0.816× its
  D-7 level with an almost identical profile (correlation of the 48-period
  profiles 0.992); 2025-08-05 (Tue) averaged 22,337,604 with a 28,567,000
  peak against 15,950,833 / 18,398,000 on 2025-08-12 (0.714×, correlation
  0.992).
- The same calendar day of the previous year, queried as context for the
  idea below: 2025-08-12 vs 2026-08-12 — profile correlation 0.976, 2026 at
  0.888× the 2025 level.

**Researcher's reading and ideas (recorded as supplied):**

- The same day one week ago does not always reflect the target day's load. A
  better alternative would be to tell the model the load for the same day on
  the previous year.
- This day is squeezed in between two off-days, so people are likely on
  vacation. Adding a window around holidays, or specifically marking business
  days straddled by weekends/holidays, could improve forecasts in this
  situation.

### References

- MLflow run: [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae)
  (`lightgbm_msm_popw_daytype-tokyo`, the SHAP rollout run)
- Superset dashboard: **Demand Forecast Analysis** → **Accuracy** → **Worst
  days**; **Explanation (SHAP)** → **SHAP waterfall**, **Feature values &
  contributions** and the contributions-by-period chart with the Day filter
  set to 2026-08-12 / 2025-08-12; the tables above are the same numbers
  queried from `pma_curated.fct_demand_forecast_accuracy`,
  `pma_curated.fct_demand_forecast_contribution` × `pma_curated.dim_date`
  and `pma_curated.fct_area_demand_generation_actual` on 2026-08-27 (day
  parts as in R-003: overnight 00–06, morning 06–08, daytime 08–18, evening
  18–24).

---

## O-003 — 建国記念の日 is heavily under-forecast, the day type outweighing the D-7 lag

- **Recorded:** 2026-08-27
- **Data period:** 2024-08-18 through 2026-08-17 (729 delivery days)
- **Strategy:** `lightgbm_msm_popw_daytype` (the demand baseline since R-003)
- **Area:** tokyo
- **MLflow run:** [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae)
  (the same run as O-002)
- **Status:** Unreviewed
- **Related investigations:** [R-003 — Day type as a categorical feature](research/demand/R-003-day-type-feature.md)
  (both days are among the seven holidays the day-type feature made worse:
  2026-02-11 went from −1,147,127 to −3,071,435 kWh and is the run's
  second-worst day, 2025-02-11 from 1,196,317 to 1,534,427 MAE; the residual
  holiday error being two-sided is an R-003 open question);
  [O-002](research/demand/observations.md#o-002-the-working-day-between-山の日-and-お盆-is-heavily-over-forecast-driven-by-the-d-7-lag)
  (the same idea — the load of the same day the previous year — from the
  opposite failure)

### Observation

In the same dashboard and run, the researcher noticed that 2026-02-11 and
2025-02-11 (建国記念の日 both years) are heavily under-forecast, and read the
**Explanation (SHAP)** tab's per-day decomposition for them: here the
`lag_7d_demand_kwh` contribution pushes the daytime load forecast up as
expected, but `day_type` counteracts it heavily and pushes the forecast down
too low.

Queried from `fct_demand_forecast_accuracy` × `dim_date` (same definitions
as O-002):

| Delivery day | Weekday | Day type | Holiday | Mean forecast (kWh) | Mean actual (kWh) | Daily MAE (kWh) | Daily bias (kWh) | MAPE |
|---|---|---|---|---:|---:|---:|---:|---:|
| 2025-02-10 | Mon | Weekday | — | 19,230,349 | 18,903,271 | 595,989 | +327,078 | 3.14 % |
| **2025-02-11** | Tue | Holiday | 建国記念の日 | 16,463,344 | 17,997,771 | 1,534,427 | −1,534,427 | 8.63 % |
| 2025-02-12 | Wed | Weekday | — | 18,568,970 | 19,174,146 | 786,506 | −605,176 | 3.99 % |
| 2026-02-10 | Tue | Weekday | — | 18,693,833 | 20,249,938 | 1,556,104 | −1,556,104 | 7.63 % |
| **2026-02-11** | Wed | Holiday | 建国記念の日 | 15,850,440 | 18,921,875 | 3,071,435 | −3,071,435 | 15.64 % |
| 2026-02-12 | Thu | Weekday | — | 18,306,678 | 19,113,646 | 806,967 | −806,967 | 4.24 % |

On both holidays all 48 periods are under-forecast. In 2026 the whole week
around the holiday is under-forecast (2026-02-07 Sat through 2026-02-13 Fri,
daily bias −806,967 to −2,449,929 kWh; 2026-02-08 is also among the run's
worst days), the holiday most.

The per-day decomposition (mean per period, as in O-002):

| Component | 2025-02-11 feature value | 2025-02-11 contribution (kWh) | 2026-02-11 feature value | 2026-02-11 contribution (kWh) |
|---|---:|---:|---:|---:|
| base | — | 15,906,978 | — | 16,085,150 |
| time_code | 24.5 | −136,972 | 24.5 | −104,952 |
| month | 2 | +213,426 | 2 | +340,924 |
| day_of_week | 1 (Tue) | −10,777 | 2 (Wed) | +34,417 |
| wavg_temperature_c | 4.88 °C | +337,723 | 2.69 °C | +472,539 |
| lag_7d_demand_kwh | 18,894,583 | +969,225 | 18,955,521 | +822,224 |
| popw_forecast_temperature_c | 3.76 °C | +1,031,951 | 7.73 °C | −211,256 |
| day_type | 2 (Holiday) | −1,848,210 | 2 (Holiday) | −1,588,606 |
| **Forecast** | | **16,463,344** | | **15,850,440** |
| Actual | | 17,997,771 | | 18,921,875 |

- The reading holds on both days: `day_type` (Holiday) is the largest
  contribution in absolute terms and the only large negative one, and it
  outweighs the D-7 lag. In the daytime periods (08–18), where the error is
  largest, the lag contributes +1,353,941 (2025) / +1,115,219 (2026) kWh per
  period and the day type −2,274,605 / −2,060,199; the daytime bias is
  −1,987,589 (2025; MAPE 10.9 %) and −4,368,264 (2026; MAPE 21.0 %, forecast
  16,430,086 against an actual of 20,798,350).
- The day-type contribution on these days (−1.85 M / −1.59 M kWh per period)
  is of the same size as on the お盆-adjacent 山の日 2025-08-11 (−1,758,221),
  where the day was forecast within +270,347.
- The forecast temperature pushes the two years in opposite directions:
  +1,031,951 at 3.76 °C in 2025, −211,256 at 7.73 °C in 2026.
- The same calendar day of the previous year, queried for the researcher's
  reading that the two loads are similar in shape: 2025-02-11 averaged
  17,997,771 kWh per period (min 15,264,000, peak 20,937,000 at 18:30–19:00)
  and 2026-02-11 18,921,875 (min 15,032,000, peak 22,165,000 at
  10:00–10:30); correlation of the two 48-period profiles 0.804, 2026 at
  1.051× the 2025 level. For comparison, the D-7 days the lag carried:
  2026-02-04 (Wed) vs 2026-02-11 — correlation 0.868, the holiday at 0.998×
  the D-7 level; 2025-02-04 (Tue) vs 2025-02-11 — 0.909 and 0.953×.

**Researcher's reading and idea (recorded as supplied):** the D-7 lag pushes
the daytime forecast up as expected, but the day type counteracts it heavily
and pushes the forecast too low. The load for 2025-02-11 is similar in shape
to 2026-02-11 (the same day the previous year), so the model could benefit
from knowing the load for the same day the previous year.

### References

- MLflow run: [`0a6b8a5560d445d5b9705bde99cf13ae`](http://localhost:5005/#/experiments/2/runs/0a6b8a5560d445d5b9705bde99cf13ae)
- Superset dashboard: **Demand Forecast Analysis** → **Accuracy** → **Worst
  days**; **Explanation (SHAP)** with the Day filter set to 2026-02-11 /
  2025-02-11; the numbers above were queried from the same marts as O-002 on
  2026-08-27.

---

<!--
## O-001 — Short title

- **Recorded:** YYYY-MM-DD
- **Data period:** YYYY-MM-DD through YYYY-MM-DD
- **Strategy:** `lightgbm`
- **Area:** tokyo
- **MLflow run:** [`<run_id>`](http://localhost:5005/#/experiments/<id>/runs/<run_id>)
- **Status:** Unreviewed
- **Related investigations:** —

### Observation

What was seen, with the numbers as read or queried (say which).

### References

- MLflow run, Superset chart (dashboard → chart name), plot under `assets/`
  (e.g. `![title](assets/O-001-<slug>.png)`).

---

Copy the structure above for each new observation. Use the next stable O-XXX
identifier within this task. Add possible causes only when the researcher
supplies them.
-->
