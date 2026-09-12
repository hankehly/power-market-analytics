# R-007 — Forecast weather elements beyond temperature

- **Status:** In progress
- **Last updated:** 2026-09-12 (E-001 run; the researcher's decision pending)
- **Created:** 2026-09-12
- **Triggering observation:** None — modeling idea
- **Related investigations:**
  [R-001 — Forecast temperature](research/demand/R-001-forecast-temperature.md)
  and [R-002 — Population-weighted temperature](research/demand/R-002-population-weighted-temperature.md)
  (they put the one MSM variable the model reads today into it);
  [R-006 — Recent load features](research/demand/R-006-recent-load-features.md)
  (its E-001 preset `lightgbm_msm_popw_daytype_simday_lags` is the baseline here)

## Question

The model reads one of the MSM forecast's variables: temperature. The surface
product carries thirteen more. Do the forecast's relative humidity,
precipitation and solar radiation carry information the model does not have,
and does adding them lower out-of-sample MAE?

## Motivation

The researcher's reasoning, as stated on 2026-09-12: forecasted
precipitation, solar radiation and humidity affect energy demand, and the
model is currently blind to those signals.

What the model has today of the forecast: `popw_forecast_temperature_c`, the
population-weighted MSM temperature. What the warehouse already had but no
preset referenced: `popw_forecast_relative_humidity_pct` and
`popw_forecast_precipitation_mm`, in `ftr_hour_msm` since the feature
catalogue rollout. Solar radiation was added to the mart for this
investigation.

All three come from the same vintage as the temperature — the 12 UTC D−2 run,
public at reference + 4 h — so they add no new availability risk. None has a
null anywhere in `fct_jma_msm_weather_forecast_hourly`: 9,715,992 rows, 149
stations, 2019-04-01 to 2026-09-07.

## Current predictive hypothesis

> Forecasted precipitation, solar radiation and humidity affect energy demand
> — rainy means fewer people go outside, humid means more air conditioning,
> and solar radiation means demand drops because solar energy is being used —
> and our model is currently blind to those signals.

(The researcher's words, 2026-09-12.)

## Scope and constraints

- **Forecast target:** the 48 half-hourly `demand_kwh` values of
  `fct_area_demand_generation_actual` for day D, Tokyo area (`--area tokyo`)
- **Information cutoff:** the [task defaults](research/demand/README.md).
  Every feature is retrieved through Feast as of 09:30 on D−1; the MSM
  vintage is public at 01:00 on D−1
- **Baseline:** `lightgbm_msm_popw_daytype_simday_lags` (R-006 E-001), re-run
  on the same window as run
  [`d04e9d0c8de04bae90a8ad0603dfad39`](http://localhost:5005/#/experiments/2/runs/d04e9d0c8de04bae90a8ad0603dfad39).
  It reproduces R-006's `34707ed6…` exactly — MAE 577,355, 723 days, 34,666
  predictions, the same seven skipped days — which is also the check that
  adding a column to `ftr_hour_msm` moved none of the columns already in it
- **Primary metric:** MAE (kWh per 30-minute period)
- **Important segments:** day part (solar acts only in daylight), season
  (humidity and air conditioning), day type, the top-10 % demand days
- **Evaluation method:** rolling out-of-sample backtest over identical
  delivery dates and training rows for baseline and candidate

## E-001 — The three population-weighted forecast elements

### Why this experiment

All three sit in one mart at the grain and vintage of the temperature the
model already reads, so the change is three feature references and no new
data dependency. Testing them together answers the question in one run; if it
lands, which of the three carries it is the next question, not this one.

### Experiment hypothesis

Adding the population-weighted forecast humidity, precipitation and solar
radiation to the baseline lowers MAE, most in daylight periods and in the
seasons where air conditioning drives load.

### Change

Preset `lightgbm_msm_popw_daytype_simday_lags_weather` = the baseline plus
`ftr_hour_msm:popw_forecast_relative_humidity_pct`,
`ftr_hour_msm:popw_forecast_precipitation_mm` and
`ftr_hour_msm:popw_forecast_solar_radiation_mjm2`. The forecast temperature is
not repeated — every preset since `lightgbm_msm_popw` carries it — so this is
three added features, 21 to 24. None is categorical.

`popw_forecast_solar_radiation_mjm2` is new in `ftr_hour_msm`: MSM's global
solar radiation weighted like the other three columns, MJ/m², the unit
`fct_jma_weather_hourly` observes in.

### Expected evidence

- MAE lower overall, with a bootstrap interval over days that excludes zero
- A larger gain in daytime periods than overnight
- A larger gain in the air-conditioning season if humidity is doing that work
- What would make the hypothesis less plausible: the gain concentrated
  overnight, or the features taking importance without lowering MAE

### Decision rule

Practical magnitude, consistency across months and day parts, the bootstrap
interval over days, and whether the segment pattern matches the mechanism.
The researcher decides.

### Execution

- **MLflow experiment:** `demand`
- **Baseline run:**
  [`d04e9d0c8de04bae90a8ad0603dfad39`](http://localhost:5005/#/experiments/2/runs/d04e9d0c8de04bae90a8ad0603dfad39)
  (2026-09-12, `lightgbm_msm_popw_daytype_simday_lags --area tokyo
  --start-date 2024-08-18 --end-date 2026-08-17`, no `--train-start`; 723
  days, 34,666 predictions, seven skipped days, 21 features)
- **Candidate run:**
  [`e6d6d4efd96345bf86da9758bc2b71cc`](http://localhost:5005/#/experiments/2/runs/e6d6d4efd96345bf86da9758bc2b71cc)
  (2026-09-12, `lightgbm_msm_popw_daytype_simday_lags_weather`, the same
  flags; 723 days, 34,666 predictions, the same seven skipped days, 24
  features)
- **Code or pull request:** branch `feature/msm-forecast-weather-elements`

### Results

Both runs scored the same 723 delivery days and the same 34,666 periods, so
`--common-days` drops nothing. kWh per 30-minute period.

| Metric | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| Overall MAE | 577,355 | 560,508 | −16,846 | −2.9 % |
| MAPE | 3.55 % | 3.44 % | −0.11 pt | −3.0 % |
| Mean error / bias | −23,607 | −21,878 | +1,729 | — |
| R² | 0.9492 | 0.9517 | +0.0025 | — |

By day part:

| Segment | n | Baseline MAE | Candidate MAE | Relative change |
|---|---:|---:|---:|---:|
| Overnight | 8,674 | 357,893 | 350,102 | −2.2 % |
| Morning | 2,888 | 539,341 | 519,063 | −3.8 % |
| Daytime | 14,440 | 748,128 | 721,798 | −3.5 % |
| Evening | 8,664 | 525,119 | 516,156 | −1.7 % |

By day type: weekday −2.4 %, weekend −3.6 %, holiday −4.6 %.

By season:

| Segment | n | Baseline MAE | Candidate MAE | Relative change |
|---|---:|---:|---:|---:|
| Winter (Dec–Feb) | 8,640 | 639,717 | 617,684 | −3.4 % |
| Spring (Mar–May) | 8,832 | 535,617 | 495,920 | −7.4 % |
| Summer (Jun–Aug) | 8,458 | 656,948 | 648,865 | −1.2 % |
| Autumn (Sep–Nov) | 8,736 | 480,814 | 483,714 | +0.6 % |

High-demand days: the top 10 % −1.0 %, the other 90 % −3.2 %.

Daily paired comparison: the candidate is lower on 56.6 % of days (409 of
723); mean daily-MAE difference −16,815 kWh, 95 % bootstrap CI over days
[−24,697, −8,739] (10,000 resamples, seed 0); median −11,664 kWh; the ten
most-improved days account for 33 % of the total error reduction. Twenty of
25 calendar months improve. The best months are 2025-04 (−15.6 %), 2025-05
(−10.8 %), 2025-02 (−8.5 %) and 2025-03 (−7.9 %); the worst are 2025-09
(+8.9 %), 2025-06 (+2.3 %) and 2025-11 (+1.8 %).

Walk-forward permutation importance of the candidate run (ΔMAE, mean over 5
repeats), where the three new features land among its 24:

| Feature | ΔMAE | Rank |
|---|---:|---:|
| `similar_day_demand_kwh` | 1,634,608 | 1 |
| `popw_forecast_temperature_c` | 375,958 | 2 |
| `popw_forecast_relative_humidity_pct` | 14,463 | 14 |
| `popw_forecast_precipitation_mm` | 13,713 | 15 |
| `popw_forecast_solar_radiation_mjm2` | 3,187 | 22 |

### Interpretation

The three elements lower MAE by 2.9 %, five times R-006's gain on the same
window, and the bootstrap interval over days is clear of zero. The gain is
broad rather than carried by a few days: 20 of 25 months improve, though the
ten best days still hold a third of the total reduction.

Two things do not match the stated mechanism. Spring is the largest seasonal
gain (−7.4 %) and summer the smallest (−1.2 %), where air conditioning would
put it the other way round; and autumn is slightly worse (+0.6 %), driven by
2025-09 (+8.9 %). The daytime gain (−3.5 %) does exceed the evening (−1.7 %)
and overnight (−2.2 %), which is the direction a daylight variable would
give, but morning (−3.8 %) is larger than daytime.

No single new feature's permutation importance approaches the 16,846 kWh the
three together remove, and solar radiation's is the smallest of the three
(3,187, rank 22 of 24). Permuting one feature leaves its correlates in place,
so correlated features share importance and each reads low; the three are
correlated with each other and with the temperature the model already had.
The measurement therefore does not say which element carries the gain, and
the individual numbers should not be read as a ranking of their value.

The candidate's daytime bias worsens (−30,524 to −40,141 kWh, a deeper
under-forecast) even as daytime MAE falls.

Improved forecasting supports incremental predictive value; it does not by
itself establish that any of the three mechanisms named in the hypothesis is
what the model picked up.

### Decision

**Decision:** Pending the researcher's review.

### Follow-up ideas

- Ablate the three one at a time on this baseline and window, to see which
  carries the gain — the permutation importance cannot answer it.
- Look at 2025-09, the one month clearly worse (+8.9 %), before adopting.
- The remaining ten MSM columns — cloud cover in particular, which is what
  makes solar radiation vary — are in the fact but in no mart. One of the ten,
  `shortwave_radiation_wm2`, is the solar radiation this preset now uses at
  1 / 0.0036 the scale (the two agree to 1e-6 across all 9,715,992 rows), so
  nine of them carry something the mart does not already have.

---

## Current conclusion

One experiment, run on 2026-09-12: the three forecast elements lower Tokyo
MAE 2.9 % on 723 matched days with a bootstrap interval clear of zero, and
the improvement holds in 20 of 25 months. The seasonal pattern does not match
the air-conditioning reasoning in the hypothesis, and which of the three
carries the gain is not yet known.

## Open questions

- Which of humidity, precipitation and solar radiation carries the effect?
- Why is spring the best season and summer the worst, if humidity is acting
  through air conditioning?
- What happens in 2025-09?

## Final disposition

- **Investigation status:** In progress
- **Recommended action:** the researcher's decision on E-001
- **Superseded by:** —
