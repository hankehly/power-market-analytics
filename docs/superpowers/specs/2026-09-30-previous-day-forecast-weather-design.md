# The forecast weather of D-1 — design

Date: 2026-09-30. Status: **the approach was chosen by the researcher on 2026-09-30** in chat
(option A of three); this file is the written spec for review before the plan. Claude pointed
at the weather of D-1 on 2026-09-29, from the analysis of Tokyo 2025-03-04 (observation #247).
The researcher decided on 2026-09-30 to include it, as forecasts only, from the rows the
warehouse already holds.

## 1. Goal

Nine columns that tell the model what the day before the delivery day is forecast to be like.
Today no preset holds any weather of D-1: the observations stop at D-2 and the forecast
covers D.

Why. D-1's temperature says something about D's load that D's own does not. In a daily
regression over 316 Dec–Mar working days the load falls 16.2 GWh per °C of D and a further
4.6 per °C of D-1 (t −4.3); D-2 adds nothing once D-1 is in. On 2025-03-04 the newest day
the model saw, D-2, had a mean of 14.2 °C, while D-1 was already cold and wet at 6.1 °C.

What to expect. Little, and not the fix for that day. Section 3 has the numbers.

## 2. Decisions

Decisions 1 to 4 are the researcher's to confirm. The rest follow from option A.

1. **Base preset `e245`**, against its run `8adc4ecc…`. The probe ran on `e245`. #243 to #245
   have no decision yet and the README keeps `e212` as the Tokyo baseline, so `e219` is the
   other choice.
2. **Physical names** are in section 4. They never change, so they are worth a read.
3. **Two of the nine mix a forecast with an observation**: D-1's forecast mean minus D-2's
   observed mean, and the mean of the three days. The existing deltas mix the same way. The
   model used both in the probe. Proposed: keep them.
4. **Rain is a daily mean, mm per hour, not a day's total.** A tree splits the two the same
   way, the radiation mean is already per hour, and no new primitive is needed.
5. **Forecasts only.** No observation of D-1 goes in. In the probe the observed hours
   01:00 to 08:00 added 0.4 points to the forecast alone, inside the noise, and a blended
   day equalled separate columns to 81 kWh.
6. **D-1's forecast is D-1's own row**: the 12 UTC run of D-3, the one the warehouse loaded
   as delivery day D-1. No download, no new run, no new availability rule.
7. **The join is on the run one day earlier.** D's row of run V reads D-1's row of run
   V − 24 h. One row in, one row out, whatever else is loaded.
8. **The columns sit in the MSM marts**, one in `ftr_hour_msm` and eight in `ftr_day_msm`.
   No new mart.
9. **No existing row's `available_at` moves.** D-1's run is public a day before D's.
10. **Both areas' marts build; the experiment is Tokyo only**, as for #243 to #245.

## 3. What the probe said

A scratch walk-forward with the published run's anchors, window and settings. It reproduces
the published `e245` forecasts of the week of 2025-03-03 to 0.0 kWh. Two winters,
2024-12-02 to 2025-03-31 and 2025-12-01 to 2026-03-31, 241 days. Facts for the experiment,
not a verdict.

| Days | `e245` MAE (kWh) | With the nine | Change |
|---|---:|---:|---:|
| All 241 | 538,691 | 533,025 | −1.1 % |
| Working days, 156 | 496,815 | 497,460 | +0.1 % |
| Working days below 6 °C, 43 | 623,199 | 613,417 | −1.6 % |
| Forecast 5 °C or more below D-2 … D-6, 16 | 827,225 | 796,275 | −3.7 % |
| Snow on the ground at 東京, 7 | 1,364,954 | 1,290,396 | −5.5 % |

The mean daily-MAE difference is −5,666 kWh, 95 % CI over days [−16,448, +5,313], lower on
50.6 % of days. 2025-03-04 goes from −11.2 % to −11.5 %.

The model uses all nine. Over five refits they take about 0.6 % of the gain and rank 8th
to 80th of 154 columns. The same-hour temperature ranks 8th to 14th in four of the five.

D-1's own run is 24 hours older than the run that gives D. For temperature that costs
little: at the midnight seam the area's MAE is 1.09 °C at lead 28 and 1.14 °C at lead 51,
over 2,556 pairs. For rain it costs more: the hourly threat score is 0.33 at leads 28 to 30
and 0.24 at leads 49 to 51.

## 4. The columns

`T_fc` stands for `MEAN(forecast_temperature_c, weight=population)`, `R_fc` for
`MEAN(forecast_solar_radiation_mjm2, weight=population)`, `P_fc` for
`MEAN(forecast_precipitation_mm, weight=population)` and `T` for
`MEAN(temperature_c, weight=population)`. The mart YAML writes them out.

### 4.1 `ftr_hour_msm`, 1 column

| Physical name | Expression |
|---|---|
| `lag_1d_popw_forecast_temperature_c` | `LAG(T_fc, 1d)` |

D-1's forecast at the same hour ending. Null where D-1 has no row for the hour.

### 4.2 `ftr_day_msm`, 8 columns

| Physical name | Expression |
|---|---|
| `lag_1d_mean_popw_forecast_temperature_c` | `LAG(DAILY_MEAN(T_fc), 1d)` |
| `lag_1d_min_popw_forecast_temperature_c` | `LAG(DAILY_MIN(T_fc), 1d)` |
| `lag_1d_evening_mean_popw_forecast_temperature_c` | `LAG(DAILY_MEAN(T_fc, time=18:00-22:00), 1d)` |
| `lag_1d_mean_popw_forecast_solar_radiation_mjm2` | `LAG(DAILY_MEAN(R_fc), 1d)` |
| `lag_1d_mean_popw_forecast_precipitation_mm` | `LAG(DAILY_MEAN(P_fc), 1d)` |
| `delta_lag_1d_mean_popw_forecast_temperature_c` | `DAILY_MEAN(T_fc) - LAG(DAILY_MEAN(T_fc), 1d)` |
| `change_1d_2d_mean_popw_temperature_c` | `LAG(DAILY_MEAN(T_fc), 1d) - LAG(DAILY_MEAN(T), 2d)` |
| `mean_3d_popw_temperature_c` | `(DAILY_MEAN(T_fc) + LAG(DAILY_MEAN(T_fc), 1d) + LAG(DAILY_MEAN(T), 2d)) / 3` |

- The first four are D-1's values of columns the mart has for D, read off D-1's row. They
  keep those columns' rules: complete days only for the mean, the minimum and the
  radiation, the four evening hours only for the evening mean.
- The rain mean is new on both sides. It is computed for D-1 only, over D-1's 24 hours in
  hour order through `ordered_weighted_mean`, complete days only. D's own rain mean is not
  added: nothing here needs it.
- `delta_lag_1d_…` is positive when D is forecast warmer than D-1. Both sides are
  forecasts, so the forecast's bias against the stations cancels.
- `change_1d_2d_…` is positive when D-1 is forecast warmer than D-2 was. It follows
  `change_2d_9d_demand_kwh`'s name. The D-2 side is `ftr_day_jma_obs`'s column, which the
  mart joins already.
- `mean_3d_…` is null unless all three days have a value.

## 5. Availability

D-1's run is public at 01:00 on D-2, D's run at 01:00 on D-1, D-2's observed mean at 01:00
on D-1. So every row's `available_at` stays the vintage's. A delivery day whose D-1 has no
forecast keeps its own columns and has the nine null.

## 6. Preset and experiment

One feature-candidate issue holds the nine columns. It closes when the column PR merges,
with the Decision left to the experiment. One experiment issue, one preset file named
`e<issue>` under `conf/presets/demand/`, `base: e245` with an `add` list of the nine
expressions. One Tokyo run on the pinned window with no date arguments, compared with
`8adc4ecc…` by `compare_demand_runs.py`. The accuracy, contribution and importance marts are rebuilt
after the run.

## 7. Tests and proof

- dbt unit tests, written first. For `ftr_hour_msm`: a day with D-1 present, and a day
  without. For `ftr_day_msm`: a complete D-1; a D-1 missing one hour, whose evening mean is
  there and whose other summaries are null; no D-1 row; a second run loaded for D-1, which
  the join on the run must ignore.
- The scratch-copy diff of both marts against main: no existing column moved, in value or
  in `available_at`.
- `just feature-views`, then the generated files checked; the fixture marts in
  `tests/conftest.py` and the field-list tests gain the nine columns.
- A Feast retrieval of the nine in the devcontainer.
- Acceptance on Tokyo 2025-03-04, the probe's values:

| Column | Value |
|---|---:|
| `lag_1d_popw_forecast_temperature_c`, hour ending 14:00 | 3.53 °C |
| `lag_1d_mean_popw_forecast_temperature_c` | 5.07 °C |
| `lag_1d_min_popw_forecast_temperature_c` | 2.72 °C |
| `lag_1d_evening_mean_popw_forecast_temperature_c` | 3.05 °C |
| `lag_1d_mean_popw_forecast_solar_radiation_mjm2` | 0.109 MJ/m² per hour |
| `lag_1d_mean_popw_forecast_precipitation_mm` | 1.146 mm per hour |
| `delta_lag_1d_mean_popw_forecast_temperature_c` | −2.65 °C |
| `change_1d_2d_mean_popw_temperature_c` | −9.16 °C |
| `mean_3d_popw_temperature_c` | 7.24 °C |

## 8. Rollout

Two PRs.

1. The nine columns, their tests, the generated files, CLAUDE.md's mart paragraphs and the
   row in `docs/superpowers/README.md`. The candidate issue closes.
2. The preset, the three registry tests a new preset needs, the run's figure and the
   README pointer. The results go to the experiment issue.

## 9. Out of scope

- Observations of D-1.
- D-1 from the run that gives D (leads 4 to 27 of the 12 UTC D-2 run), and the 15 and 18 UTC
  runs. Both need downloads. They come up again only if the experiment keeps these columns
  and D-1's rain or cloud matters.
- Snow.
- D-1's humidity, cloud, wind and pressure, and its radiation and rain by the hour.
- A Kansai experiment.
- The dashboard changes suggested with #247.
