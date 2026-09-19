# Heating and cooling exposure, hourly and daily (#152, #153) — design

Date: 2026-09-19. Status: **not approved: put off by the researcher on 2026-09-19. Nothing is
built.** The researcher's words: needs more looking into, we'll put this off until later.
#152 and #153 stay open. The design and its measurements are kept for that later look. Feature candidates
[#152](https://github.com/hankehly/power-market-analytics/issues/152) and
[#153](https://github.com/hankehly/power-market-analytics/issues/153), both suggested by
Codex on 2026-09-13.

One spec for the two: they share the thresholds and the `POSITIVE_PART` primitive, and #153
is the day's total of #152's hourly columns.

## 1. Goal

From the MSM forecast for the delivery day D, as the issues describe them:

- **#152, at each hour:** the population-weighted degrees above a heat threshold, the
  degrees below a cold threshold, and the share of the population above the heat
  threshold. The threshold is applied to each station before the stations are averaged.
- **#153, per day:** the two exposures added up over D's 24 hours, as degree-hours.

If it is taken up later: the columns only, with no preset and no backtest. An experiment
would test them in a batch.

## 2. The decision that blocks both: the thresholds

Both issues say: choose the thresholds on training data only, then hold them fixed. Both
call their expressions drafts until the values are chosen. That choice is the researcher's.
What follows is measured evidence for it, and Claude's suggestion, 2026-09-19.

**Measured**, on training data only: weekdays from 2022-04-01 to 2024-08-17, the day before
the first test day of the reference backtests. Each hour's demand is divided by the mean
demand of that hour of the day in the same area, then averaged by the population-weighted
forecast temperature of the hour, in 1 °C bins. 1.0 is the hour's average.

| °C | Tokyo | Kansai | | °C | Tokyo | Kansai |
|---|---|---|---|---|---|---|
| 10 | 0.951 | 0.944 | | 19 | 0.862 | 0.864 |
| 11 | 0.928 | 0.926 | | 20 | 0.873 | 0.867 |
| 12 | 0.906 | 0.905 | | 21 | 0.889 | 0.882 |
| 13 | 0.882 | 0.887 | | 22 | 0.921 | 0.908 |
| 14 | 0.870 | 0.876 | | 23 | 0.968 | 0.951 |
| 15 | 0.866 | 0.868 | | 24 | 1.000 | 1.001 |
| 16 | 0.859 | 0.860 | | 25 | 1.057 | 1.041 |
| 17 | 0.858 | 0.855 | | 26 | 1.093 | 1.086 |
| 18 | 0.856 | 0.853 | | | | |

Each cell holds 386 to 657 hours. Outside the table the curve keeps climbing: 1.22 at 0 °C,
1.27 at 30 °C, in both areas.

- The floor is at 17 to 18 °C in both areas.
- On the cold side the curve is nearly flat down to 14 °C: from 18 to 14 °C it rises by
  0.014 in Tokyo and 0.023 in Kansai in all. Below 13 °C it rises by about 0.02 per °C.
- On the hot side it rises by 0.016 from 20 to 21 °C, then by 0.03 to 0.06 per °C above
  21 °C.

**Options.**

| | Cold threshold | Heat threshold | What it is |
|---|---|---|---|
| A | 14 °C | 21 °C | Where the measured curve leaves its floor: a dead band between them. |
| B | 18 °C | 18 °C | The measured floor: one balance point, no dead band. |

Claude suggests A. #152's reasoning is about the thresholds "where load responds
nonlinearly", and its third column, the share of the population above the heat threshold,
only says something near that point: with 18 °C it is 1 all summer. The two measured areas
agree, so one pair for all nine areas. The other seven have no demand loaded to measure.

## 3. Other decisions for the researcher

1. **The threshold is part of the physical name.** A name never changes, and a column with
   another threshold is another feature. With option A:
   - `ftr_hour_msm.popw_forecast_degrees_above_21c`
   - `ftr_hour_msm.popw_forecast_degrees_below_14c`
   - `ftr_hour_msm.popw_share_forecast_above_21c`
   - `ftr_day_msm.sum_popw_forecast_degree_hours_above_21c`
   - `ftr_day_msm.sum_popw_forecast_degree_hours_below_14c`
2. **The expressions carry the number too**, in place of the issues' `heat_threshold`:
   `MEAN(POSITIVE_PART(forecast_temperature_c - 21), weight=population)`,
   `MEAN(POSITIVE_PART(14 - forecast_temperature_c), weight=population)`,
   `PERCENT_TRUE(forecast_temperature_c > 21, weight=population)`, and
   `DAILY_SUM(…)` of the first two.
3. **Three new primitives** in `docs/Feature-Naming.md`, as the issues propose:
   `POSITIVE_PART(x)` is `x` where it is above 0, else 0; `PERCENT_TRUE(flag, weight)` is
   the weighted share of stations where the flag is true, 0 to 1; `DAILY_SUM(x)` is the
   total over the day's 24 hours.
4. **#153 goes in `ftr_day_msm`**, which #132 built. The issue asks for that.
5. **The share is strictly above the threshold**, `>`, as the issue wrote it.
6. **Complete days only for the daily totals**, as in `ftr_day_msm`: null unless all 24
   hours have the exposure.

## 4. Definition

Per station and hour: `above = greatest(t − 21, 0)`, `below = greatest(14 − t, 0)`,
`is_above = 1 if t > 21 else 0`, with `t` the station's forecast temperature. Each goes
through the hour mart's weighted mean as the thirteen elements do: the latest census
vintage's weights, renormalised over the stations that have a temperature, added in station
order. A station without a temperature is left out of all three.

The daily totals add the two hourly columns over hours 1 to 24 in hour order, °C·h.

`available_at` is the mart's: the vintage's, reference + 4 h.

## 5. Measured: does the station-level threshold differ from the area mean?

#152's premise is that an area-average temperature can hide that a populated part of the
area crossed a threshold. Tokyo and Kansai, 2025, 8,760 hours each, thresholds 14 and 21 °C.
The gap is the station-level exposure minus the same hinge applied to the area's mean
temperature. It is never negative.

| | Tokyo | Kansai |
|---|---|---|
| Hours with a cooling gap over 0.1 °C / over 0.5 °C | 456 / 38 | 308 / 12 |
| Hours with a heating gap over 0.1 °C / over 0.5 °C | 539 / 44 | 399 / 11 |
| Mean cooling gap / mean heating gap, °C | 0.018 / 0.022 | 0.010 / 0.015 |
| Largest cooling gap / heating gap, °C | 0.97 / 1.00 | 0.94 / 0.61 |

So the premise holds, and it is small: over 0.1 °C on about 5 % of hours, over 0.5 °C on
under 0.5 %.

**Counting hours where the exposure is above 0 misleads.** Tokyo has 6,059 such cooling
hours out of 8,760, and in 2,972 of them the area mean is at or below 21 °C. That is the
area's warm islands, not its towns: 父島 (weight 0.00006), 三宅島, 八丈島 and 大島 (0.00015 to
0.00017) are above 21 °C most of the year. For the same reason Tokyo's share column is
strictly between 0 and 1 on 4,532 hours, often at a value near 0.0001.

Facts for the experiment, not a verdict on the candidates.

## 6. Build

- `ftr_hour_msm`: the three per-station values are added to the model's Jinja list of
  weighted elements, so they take the existing ordered weighted mean. The output names are
  set apart from the `popw_forecast_<element>` pattern.
- `ftr_day_msm`: two ordered sums over the hour mart's two exposure columns.
- dbt unit tests, written first. Two stations at weights 0.75 / 0.25:
  - 19 °C and 25 °C: degrees above 21 is `0.25 × 4 = 1.0` while the mean, 20.5 °C, gives 0;
    the share above is 0.25; degrees below 14 is 0.
  - 21.0 °C exactly is not above: share 0.
  - a station with no temperature drops out and the weights renormalise.
  - a day of 24 hours sums to its degree-hours; a day of 23 gives null.
- The check against the real data: each column against a plain weighted sum over the
  station rows, and no earlier column of either mart moved, against a copy taken before
  the build.
- `just feature-views`, the fixture's columns in `tests/conftest.py`,
  `docs/Feature-Naming.md`, `CLAUDE.md`.

## 7. Out of scope

Thresholds fitted per area, per season or per hour of the day. A humidity-adjusted exposure:
#136 built the 不快指数. Observed, not forecast, exposure.
