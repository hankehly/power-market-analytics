# OCCTO's half-hourly forecast for D as a feature (#140) — design

Date: 2026-09-19. Status: **set aside by the researcher on 2026-09-19. Nothing is built.**
The researcher's reason: the availability is too short to build a bias-correction model
with this value. OCCTO publishes the half-hourly forecast from 2025-04-01 only. #140 is
closed as not planned, Decision `Set aside`. The design below is kept for what it measured. Feature candidate
[#140](https://github.com/hankehly/power-market-analytics/issues/140), suggested by Claude on
2026-09-15. Nothing is built.

## 1. Goal

The TSO's half-hourly demand forecast for the delivery day D, as OCCTO publishes it at
18:00 on D-2, at the period it forecasts. From 2025-04-01; earlier training rows are null.

The column only. No preset and no backtest: an experiment tests it later.

## 2. The decision that comes first

The issue, and #139 with it, is marked "Needs a decision" on the Project. In the issue's
words: the forecast "may dominate SHAP and make the model a corrector of the TSO's
forecast". Whether the model may read the TSO's own number for the period it forecasts is
the researcher's call. This spec does not make it. It records what was measured.

**OCCTO's forecast against the actuals**, every period both have, from 2025-04-01:

| | Tokyo | Kansai |
|---|---|---|
| Periods / days | 25,690 / 536 | 25,706 / 536 |
| MAE, MWh per period | 407.1 | 252.5 |
| MAPE | 2.52 % | 3.19 % |
| Bias, forecast − actual, MWh | +45.4 | +27.6 |
| Mean actual, MWh per period | 15,715 | 7,981 |

For scale: the Tokyo reference runs have an MAE of about 560 MWh (`d04e9d0c…`, 577; its
weather variant `e6d6d4ef…`, 561), on another window, 2024-08 to 2026-08. The two numbers
are not a matched comparison. OCCTO forecasts at 18:00 on D-2; the demand task at 09:30 on
D-1, fifteen and a half hours later.

**#139 needs no build.** Its three columns are `ftr_day_occto`'s, on `main` since the feature
catalogue. It waits on the same decision, then on an experiment.

## 3. Decisions for the researcher, if the answer to section 2 is yes

1. **A new period mart, `ftr_period_occto`**, over `fct_occto_demand_supply_forecast_30m`,
   as the issue's build note says. Grain `area_code × trade_date × time_code`. It would be
   the tenth feature mart.
2. **Physical name, which never changes: `occto_forecast_demand_mw`.** `ftr_day_occto` names
   its columns after the fact's (`max_demand_mw`), which here would give `demand_mw`. In a
   SHAP table or an importance chart that reads as the target. The expression is the
   issue's: `forecast_demand_mw`.
3. **MW, as published.** The target is kWh per 30 minutes, which is MW × 500. A tree does not
   need the units to match, and `ftr_day_occto` keeps MW too.
4. **Demand only.** The fact also holds `supply_capacity_mw`. The issue does not ask for it.
5. **`available_at` is the fact's**: 18:00 on D-2, the bound in
   `std_occto__area_reserve_rate_dad`.

## 4. Measured on the fact

| | |
|---|---|
| Rows | 232,416 = 538 days × 48 periods × 9 areas |
| Span | 2025-04-01 to 2026-09-20, no day missing |
| Null `demand_mw` | 0 |

A backtest that tests this column trains on windows that reach back before 2025-04-01, so
most training rows are null at first. Since 2026-09-13 a missing feature is NaN to LightGBM
in training and prediction alike, as `lightgbm_occto` already relies on for the daily
forecast before 2024-04-01.

## 5. Build

- `dbt/models/features/ftr_period_occto.sql` and `.yml`: the fact joined to `dim_area`, as
  `ftr_day_occto` is.
- A dbt unit test, written first: two areas and two periods pass through with their
  `available_at`; an area outside the fact yields no row.
- A new mart is added by hand in five places the generator does not reach:
  `tests/test_feature_views.py`, `tests/test_feature_store.py`,
  `tests/test_feature_value_fact.py`, the Feature candidate issue template's list of marts,
  and the fixture's synthetic mart in `tests/conftest.py`. `CLAUDE.md` counts ten marts.
- The check against the real data: the mart equals the fact row for row.
- `just feature-views`, then `just dbt build --select ftr_period_occto+ dim_feature`.

## 6. Out of scope

OCCTO's forecast as a benchmark on the dashboards: the issue mentions it, and it is a
dashboard change, not a feature. A residual target, the actual minus OCCTO's forecast. The
spot-price task.
