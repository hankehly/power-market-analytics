# Accumulated observed temperature over 24 and 72 hours (#150) — design

Date: 2026-09-19. Status: **approved by the researcher on 2026-09-19 as written, and
implemented**. Branch: `feature/issue-150-accumulated-observed-temperature`. Feature candidate
[#150](https://github.com/hankehly/power-market-analytics/issues/150), suggested by Codex on
2026-09-13.

## 1. Goal

Three features of recent continuous heat or cold, as the issue describes them: the
population-weighted observed temperature averaged over the 24 and the 72 hourly
observations that end at the target hour on D-2, and a 72-hour exponentially weighted
average with a 24-hour half-life.

Today's observed feature, `wavg_temperature_c`, averages the same hour across D-2 to D-8 at
one station. These run along the clock instead, over every hour, and over the area's
stations.

The columns only. No preset and no backtest: an experiment tests them later in a batch.

## 2. Decisions for the researcher

1. **A population-weighted observed temperature comes to a mart for the first time.**
   `ftr_hour_jma_obs` reads one station. Python has a weighted observed series, for the
   similar-day selector (`load_area_observed_weather_population_weighted`), but no mart
   does. The build adds it inside `ftr_hour_jma_obs` by `ftr_hour_msm`'s rule: the latest
   census vintage's weights, renormalised over the stations that report the hour, added in
   station order. The issue asks for that rule.
2. **The weighted hourly value itself is not a feature.** The issue asks for the three
   windows only. `wavg_temperature_c` stays as it is, at the representative station.
3. **Physical names, which never change**, after `ewm_5d_demand_kwh` and
   `mean_weekly_lags_demand_kwh`:
   - `mean_24h_popw_temperature_c`
   - `mean_72h_popw_temperature_c`
   - `ewm_72h_popw_temperature_c`
4. **The window ends at the target hour on D-2.** For hour ending h on D, the 24-hour
   window is the hours ending from h + 1 on D-3 to h on D-2. The 72-hour window starts at
   h + 1 on D-5.
5. **Complete windows only**, as the issue says: null unless all 24, or all 72, hours have a
   weighted value. A missing station does not break a window; an hour no station reports
   does. No such hour exists today.
6. **The weights of the weighted average** are `0.5^(k / 24)`, with k = 0 for the newest
   hour and 71 for the oldest, divided by their sum. The window is always complete, so the
   sum is a constant.

   **As built:** the 72 weights are computed when the model compiles and land in the SQL as
   literals, so the SQL only adds and multiplies. Spark's `pow()` and another system's can
   differ in the last bit, which would make the value impossible to test on exact doubles; a
   literal cannot. The unit test's Python reference reproduces the column to the bit.
7. **`available_at` does not change.** The newest observation is hour h on D-2, public one
   hour after it ends (`std_jma__hourly`'s rule). That is already the mart's
   `available_at`.

## 3. Definition

Hourly series per area: `x(ts) = Σ w·t / Σ w` over the stations with a temperature at `ts`.

- `mean_24h` = the mean of the 24 values of `x` ending at (D-2, h).
- `mean_72h` = the mean of the 72 values ending there.
- `ewm_72h` = `Σ 0.5^(k/24)·x_k / Σ 0.5^(k/24)`, k = 0 … 71.

Expressions, as the issue wrote them:
`ROLLING_MEAN(MEAN(temperature_c, weight=population), gap=2d, window=24, step=1h)`, the same
with `window=72`, and
`EWA(MEAN(temperature_c, weight=population), gap=2d, window=72, step=1h, halflife=24)`.
All three primitives exist. `step=1h` is new as a value; `docs/Feature-Naming.md` gets the
example.

Every sum is taken in a fixed order, stations then hours, through `aggregate()` on a sorted
array, never `avg()`: over `ftr_day_msm`'s 24,570 days a plain `avg()` of doubles differed
from the ordered mean in the last bits on 55 % of rows.

## 4. Measured, Tokyo and Kansai

**The weighted observed series has no gap**, 2019-03-25 to 2026-09-17, 65,616 hours each:

| | Tokyo | Kansai |
|---|---|---|
| Weighted stations | 21 | 11 |
| Hours no station reports | 0 | 0 |
| Hours where some station is missing, renormalised | 518, 0.8 % | 132, 0.2 % |

So every 24- and 72-hour window is complete today.

**Overlap with what the model already has**, the 8,760 target hours of 2025:

| Correlation | Tokyo | Kansai |
|---|---|---|
| 24 h mean with `wavg_temperature_c`, the present observed feature | 0.962 | 0.971 |
| 72 h mean with `wavg_temperature_c` | 0.961 | 0.971 |
| 24 h mean with `popw_forecast_temperature_c` for D | 0.904 | 0.921 |
| 24 h mean with 72 h mean | 0.986 | 0.990 |

The 24 h mean and `wavg_temperature_c` differ by 2.0 °C (Tokyo) and 1.8 °C (Kansai) on
average in absolute value: one is a day's mean, the other the temperature at that hour. The
24 h and 72 h means differ by 1.1 and 1.0 °C.

Facts for the experiment to weigh, not a verdict on the candidate.

## 5. Build

- `ftr_hour_jma_obs`: a CTE for the weighted hourly series, then the three windows over a
  gapless hourly spine, read at (D-2, h). Nine areas, as the mart has today.
- dbt unit tests, written first:
  - two stations at 0.75 / 0.25, one missing for an hour: the hour renormalises, and the
    window is still complete;
  - 24 known values give their mean, in hour order;
  - a window one hour short, and a window holding an hour no station reports, give null;
  - the weighted average of a constant series is the constant; of a step, hand-computed.
- The check against the real data: each column against a plain windowed mean over the same
  series, on every row; `wavg_temperature_c` and `available_at` unchanged against a copy
  taken before the build.
- `just feature-views`, the fixture's three columns in `tests/conftest.py`,
  `docs/Feature-Naming.md`, `CLAUDE.md`.

## 6. Out of scope

Humidity or rain windows. Windows ending later than D-2: D-1's observations stop at 09:30,
which is #133's subject. A weighted replacement for `wavg_temperature_c`.
