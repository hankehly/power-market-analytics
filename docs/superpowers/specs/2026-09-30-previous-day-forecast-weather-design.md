# The forecast weather of D-1 — design

Date: 2026-09-30. Status: **the design was ruled on by the researcher on 2026-09-30** in
chat; this file is the written spec for review before the plan. Claude pointed at the
weather of D-1 on 2026-09-29, from the analysis of Tokyo 2025-03-04 (observation #247). The
researcher decided on 2026-09-30 to include it, as forecasts only, from the rows the
warehouse already holds, and ruled on the columns (section 2). Feature candidate #248,
experiment #249. It supersedes #133 (Claude, 2026-09-15: D-1 from D's own run, 13:00 to
24:00, needing a re-download), closed as not planned on 2026-09-30 at the researcher's
word, Decision `Superseded`.

## 1. Goal

Eleven columns that tell the model what the day before the delivery day is forecast to be
like. Today no preset holds any weather of D-1: the observations stop at D-2 and the
forecast covers D.

Why. D-1's temperature says something about D's load that D's own does not. In a daily
regression over 316 Dec–Mar working days the load falls 16.2 GWh per °C of D and a further
4.6 per °C of D-1 (t −4.3); D-2 adds nothing once D-1 is in. On 2025-03-04 the newest day
the model saw, D-2, had a mean of 14.2 °C, while D-1 was already cold and wet at 6.1 °C.

What to expect. Little, and not the fix for that day. Section 3 has the numbers.

## 2. Decisions

Decisions 1 to 7 are the researcher's rulings of 2026-09-30. The rest follow from them.

1. **Forecasts only, from the rows the warehouse holds.** No observation of D-1 goes in, and
   nothing is downloaded. In the probe the observed hours 01:00 to 08:00 added 0.4 points to
   the forecast alone, inside the noise, and a blended day equalled separate columns to
   81 kWh.
2. **Base preset `e245`**, against its run `8adc4ecc…`.
3. **The hour grain carries temperature and radiation, not rain.** On top of the day-grain
   columns the hour-grain radiation lowered the winter MAE 0.9 % in the probe and the
   hour-grain rain 0.1 %. The forecast cannot place rain by the hour at these leads.
4. **The hour grain also carries the temperature difference, D minus D-1.** Added at the
   researcher's word, untested: no probe has run it.
5. **The two columns that mix a forecast with an observation stay**: D-1's forecast mean
   minus D-2's observed mean, and the mean of the three days. The existing deltas mix the
   same way, and the model used both in the probe.
6. **Rain is a daily mean, mm per hour, not a day's total.** A tree splits the two the same
   way, the radiation mean is already per hour, and no new primitive is needed.
7. **A difference of one series is written with `DIFF` and named `delta_lag_1d_…`.** The
   expression follows the naming guide's rule 4. The name follows the 40 sibling columns:
   `delta_` plus the name of the column it is taken from.
8. **D-1's forecast is D-1's own row**: the 12 UTC run of D-3, the one the warehouse loaded
   as delivery day D-1. No new run and no new availability rule.
9. **The join is on the run one day earlier.** D's row of run V reads D-1's row of run
   V − 24 h. One row in, one row out, whatever else is loaded.
10. **The columns sit in the MSM marts**, three in `ftr_hour_msm` and eight in
    `ftr_day_msm`. No new mart.
11. **No existing row's `available_at` moves.** D-1's run is public a day before D's.
12. **Both areas' marts build; the experiment is Tokyo only**, as for #243 to #245.

## 3. What the probes said

Scratch walk-forwards with the published run's anchors, window and settings. The loop
reproduces the published `e245` forecasts of the week of 2025-03-03 to 0.0 kWh. Two winters,
2024-12-02 to 2025-03-31 and 2025-12-01 to 2026-03-31, 241 days. Facts for the experiment,
not a verdict.

The nine columns of sections 4.1 and 4.2 without the hour-grain radiation and difference:

| Days | `e245` MAE (kWh) | With the nine | Change |
|---|---:|---:|---:|
| All 241 | 538,691 | 533,025 | −1.1 % |
| Working days, 156 | 496,815 | 497,460 | +0.1 % |
| Working days below 6 °C, 43 | 623,199 | 613,417 | −1.6 % |
| Forecast 5 °C or more below D-2 … D-6, 16 | 827,225 | 796,275 | −3.7 % |
| Snow on the ground at 東京, 7 | 1,364,954 | 1,290,396 | −5.5 % |

The mean daily-MAE difference is −5,666 kWh, 95 % CI over days [−16,448, +5,313], lower on
50.6 % of days. 2025-03-04 goes from −11.2 % to −11.5 %.

Hour-grain lags added to the nine:

| Added | MAE (kWh) | Against the nine | 95 % CI over days (kWh) | Against `e245` |
|---|---:|---:|---|---:|
| Radiation | 528,280 | −0.9 % | [−10,206, +730] | −1.9 % |
| Rain | 532,368 | −0.1 % | [−5,592, +4,373] | −1.2 % |
| Both | 528,018 | −0.9 % | [−10,900, +557] | −2.0 % |

The model uses all nine. Over five refits they take about 0.6 % of the gain and rank 8th
to 80th of 154 columns; the same-hour temperature ranks 8th to 14th in four of the five.
The two hour-grain lags rank 121st to 149th of 156.

The eleven have not been run as one batch. The hour-grain difference is in no probe.

How good the forecast is by the hour, leads 28 to 51, Tokyo 2019-04 to 2026-03:

| Element | Skill by the hour | By the day | D-1's hour against D's same hour |
|---|---:|---:|---:|
| Temperature | 0.91 | 0.94 | 0.95 |
| Radiation, 08:00 to 17:00 | 0.76 | 0.82 | 0.61 |
| Rain | threat score 0.24 to 0.33 | | 0.03 |

Skill is the correlation of the forecast's and the observation's anomalies against the
month and hour. The last column is how much of D-1's hour the model already has in D's.

D-1's own run is 24 hours older than the run that gives D. For temperature that costs
little: at the midnight seam the area's MAE is 1.09 °C at lead 28 and 1.14 °C at lead 51,
over 2,556 pairs. For rain it costs more: the threat score is 0.33 at leads 28 to 30 and
0.24 at leads 49 to 51.

## 4. The columns

`T_fc` stands for `MEAN(forecast_temperature_c, weight=population)`, `R_fc` for
`MEAN(forecast_solar_radiation_mjm2, weight=population)`, `P_fc` for
`MEAN(forecast_precipitation_mm, weight=population)` and `T` for
`MEAN(temperature_c, weight=population)`. The mart YAML writes them out.

### 4.1 `ftr_hour_msm`, 3 columns

| Physical name | Expression |
|---|---|
| `lag_1d_popw_forecast_temperature_c` | `LAG(T_fc, 1d)` |
| `lag_1d_popw_forecast_solar_radiation_mjm2` | `LAG(R_fc, 1d)` |
| `delta_lag_1d_popw_forecast_temperature_c` | `DIFF(T_fc, 1d)` |

D-1's forecast at the same hour ending, and D's forecast minus it. Null where D-1 has no
row for the hour. The difference is positive when D is forecast warmer than D-1.

### 4.2 `ftr_day_msm`, 8 columns

| Physical name | Expression |
|---|---|
| `lag_1d_mean_popw_forecast_temperature_c` | `LAG(DAILY_MEAN(T_fc), 1d)` |
| `lag_1d_min_popw_forecast_temperature_c` | `LAG(DAILY_MIN(T_fc), 1d)` |
| `lag_1d_evening_mean_popw_forecast_temperature_c` | `LAG(DAILY_MEAN(T_fc, time=18:00-22:00), 1d)` |
| `lag_1d_mean_popw_forecast_solar_radiation_mjm2` | `LAG(DAILY_MEAN(R_fc), 1d)` |
| `lag_1d_mean_popw_forecast_precipitation_mm` | `LAG(DAILY_MEAN(P_fc), 1d)` |
| `delta_lag_1d_mean_popw_forecast_temperature_c` | `DIFF(DAILY_MEAN(T_fc), 1d)` |
| `change_1d_2d_mean_popw_temperature_c` | `LAG(DAILY_MEAN(T_fc), 1d) - LAG(DAILY_MEAN(T), 2d)` |
| `mean_3d_popw_temperature_c` | `(DAILY_MEAN(T_fc) + LAG(DAILY_MEAN(T_fc), 1d) + LAG(DAILY_MEAN(T), 2d)) / 3` |

- The first four are D-1's values of columns the mart has for D, read off D-1's row. They
  keep those columns' rules: complete days only for the mean, the minimum and the
  radiation, the four evening hours only for the evening mean.
- The rain mean is new on both sides. It is computed for D-1 only, over D-1's 24 hours in
  hour order through `ordered_weighted_mean`, complete days only. D's own rain mean is not
  added: nothing here needs it.
- `delta_lag_1d_mean_…` is positive when D is forecast warmer than D-1. Both sides are
  forecasts, so the forecast's bias against the stations cancels.
- `change_1d_2d_…` is positive when D-1 is forecast warmer than D-2 was. It follows
  `change_2d_9d_demand_kwh`'s name. Its two sides are different series, so it is written
  infix. The D-2 side is `ftr_day_jma_obs`'s column, which the mart joins already.
- `mean_3d_…` is null unless all three days have a value.

### 4.3 What the expressions leave to the descriptions

`LAG(x, 1d)` of a forecast is D-1's own forecast, from the run one day earlier. It is not
an earlier copy of D's forecast. Every column's description says so.

## 5. Availability

D-1's run is public at 01:00 on D-2, D's run at 01:00 on D-1, D-2's observed mean at 01:00
on D-1. So every row's `available_at` stays the vintage's. A delivery day whose D-1 has no
forecast keeps its own columns and has the eleven null.

## 6. Preset and experiment

Feature candidate #248 holds the eleven columns. It closes when the column PR merges,
with the Decision left to the experiment. Experiment #249, one preset file `e249` under
`conf/presets/demand/`, `base: e245` with an `add` list of the eleven expressions. One Tokyo run on the pinned window with no date arguments, compared with
`8adc4ecc…` by `compare_demand_runs.py`. The accuracy, contribution and importance marts
are rebuilt after the run.

## 7. Tests and proof

- dbt unit tests, written first. For `ftr_hour_msm`: a day with D-1 present; a day without;
  a D-1 hour with a temperature and no radiation. For `ftr_day_msm`: a complete D-1; a D-1
  missing one hour, whose evening mean is there and whose other summaries are null; no D-1
  row; a second run loaded for D-1, which the join on the run must ignore.
- The scratch-copy diff of both marts against main: no existing column moved, in value or
  in `available_at`.
- `just feature-views`, then the generated files checked; the fixture marts in
  `tests/conftest.py` and the field-list tests gain the eleven columns.
- A Feast retrieval of the eleven in the devcontainer.
- Acceptance on Tokyo 2025-03-04, the values the warehouse gives today:

| Column | Value |
|---|---:|
| `lag_1d_popw_forecast_temperature_c`, hour ending 14:00 | 3.53 °C |
| `lag_1d_popw_forecast_solar_radiation_mjm2`, hour ending 14:00 | 0.402 MJ/m² |
| `delta_lag_1d_popw_forecast_temperature_c`, hour ending 14:00 | +1.67 °C |
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

1. The eleven columns, their tests, the generated files, CLAUDE.md's mart paragraphs and
   the row in `docs/superpowers/README.md`. The candidate issue closes.
2. The preset, the three registry tests a new preset needs, the run's figure and the
   README pointer. The results go to the experiment issue.

## 9. Out of scope

- Observations of D-1.
- D-1 from the run that gives D (leads 4 to 27 of the 12 UTC D-2 run, the variant #133
  described), and the 15 and 18 UTC runs of D-2. Both need downloads. They come up again
  only if the experiment keeps these columns and D-1's rain or cloud matters; the same-run
  variant would then need another physical name and expression, since this batch takes
  #133's. A fresher run for D itself is #141, set aside on 2026-09-15 and untouched.
- Snow.
- D-1's rain by the hour, and its humidity, cloud, wind and pressure.
- The hour-grain difference of radiation.
- A Kansai experiment.
- The dashboard changes suggested with #247.
