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
