# The forecast temperature of the hours before the target hour (#204) — design

Date: 2026-09-20. Status: **not approved: put off by the researcher on 2026-09-20. Nothing is
built.** The researcher's words: post your findings in a comment on the issue and let's
sideline this one for now. #204 stays open, and the findings of section 4 are in a
[comment on it](https://github.com/hankehly/power-market-analytics/issues/204#issuecomment-5745789416).
The design is kept for that later look. Feature candidate
[#204](https://github.com/hankehly/power-market-analytics/issues/204), suggested by Claude on
2026-09-20.

## 1. Goal

Two features in `ftr_hour_msm`, as the issue describes them, from the same MSM run that
forecasts D: the population-weighted forecast temperature averaged over the six hours
ending at the target hour, and its value three hours earlier.

Each row sees the forecast temperature of its own hour. `ftr_day_msm` adds the day's
maximum, minimum and mean, and #150 the observed temperature up to D-2. Nothing says how
warm the hours just before were, which is what a building's thermal mass answers to.

If it is taken up later: the columns only, with no preset and no backtest. An experiment
would test them in a batch.

## 2. Decisions for the researcher

1. **Two columns or one.** The issue asks for both. Measured, section 4: once the hour's own
   temperature is taken out, the two correlate at 0.98 in every season, and they track the
   error equally. They are one signal. Claude's suggestion: build the six-hour mean only.
   The rest of this spec describes both, so either ruling can be built.
2. **Physical names, which never change:** `mean_6h_popw_forecast_temperature_c` and
   `lag_3h_popw_forecast_temperature_c`, after #150's `mean_24h_popw_temperature_c` and the
   `lag_1d_popw_forecast_temperature_c` that #133's spec proposes.
3. **The window ends at the target hour and includes it**: the hours ending h-5 to h. The
   lag is the hour ending h-3. The 3 and the 6 are the issue's first guesses; measured,
   offsets from 1 to 6 hours differ little.
4. **Within D's own vintage, complete windows only.** The vintage holds the 24 hours of D.
   So the mean is null for the hours ending 01:00 to 05:00 and the lag for 01:00 to 03:00,
   21 % and 12.5 % of rows. No other row is null today.
5. **#133 would fill those hours**, from leads 23 to 27 of the same run. Its spec, in
   PR #199, keeps the mart's present columns on leads 28 to 51. These columns would read
   the earlier leads next to #133's own column, a note for #133's build. The window is
   keyed by the hour on the clock within the vintage, so it crosses midnight by itself once
   the rows exist. Built before #133, nothing here changes later but the nulls.
6. **`available_at` does not change.** Every hour read is of the same run as the row.
7. **Two small additions to `docs/Feature-Naming.md`:** an offset in hours for `LAG`, which
   moves along the clock as one in minutes does, and `gap=0h` for a window that ends at the
   current term.

## 3. Definition

With `x(h)` the mart's `popw_forecast_temperature_c` at the hour ending h of D's vintage:

- `mean_6h = (x(h-5) + x(h-4) + x(h-3) + x(h-2) + x(h-1) + x(h)) / 6`
- `lag_3h = x(h-3)`

Null unless every term is present.

The six terms are read as six named values, each by its offset on the clock, and added in
the order written, oldest first. So the order of addition is in the SQL and not in how Spark
walks a frame, and `avg()` is not used, by the repo's rule. A prototype of the named sum
equals Spark's `avg()` over the ordered frame on all 466,830 rows that have a value.

Expressions, as the issue wrote them:
`ROLLING_MEAN(MEAN(forecast_temperature_c, weight=population), gap=0h, window=6, step=1h)`
and `LAG(MEAN(forecast_temperature_c, weight=population), 3h)`.

## 4. Measured

**The rows**, from the prototype: 589,680, nine areas, 2019-04-01 to 2026-09-20. Every
area-day has one vintage of 24 hours, each with a temperature. The mean is null on 122,850
rows, the hours ending 01:00 to 05:00, and the lag on 73,710, and on no other row.

**How each candidate tracks the error.** Tokyo, run `bd96e6e3…`
(`lightgbm_msm_popw_daytype_simday_lags_weather`, 2024-08-18 to 2026-08-17, from before the
2026-09-14 similar-day switch). The correlation of the run's hourly error, forecast minus
actual, with the candidate minus the hour's own forecast temperature. Both are taken
against their mean within the same season, hour and 3 °C band of the hour's temperature.
Summer is June to September, winter December to March.

| Candidate, minus the hour's temperature | Summer, hours ending 12:00–24:00 | Winter, 12:00–24:00 | Summer, 07:00–24:00 | Winter, 07:00–24:00 |
|---|---:|---:|---:|---:|
| 1 hour earlier | −0.113 | +0.108 | −0.040 | +0.090 |
| 3 hours earlier | −0.132 | +0.110 | −0.057 | +0.091 |
| 6 hours earlier | −0.110 | +0.098 | −0.055 | +0.094 |
| Mean of the last 3 hours | −0.125 | +0.110 | −0.048 | +0.092 |
| Mean of the last 6 hours | −0.133 | +0.113 | −0.059 | +0.096 |
| Mean of the last 12 hours | −0.094 | +0.100 | — | — |
| The day's maximum, built with #132 | −0.129 | +0.097 | −0.118 | +0.054 |
| The day's mean, built with #132 | −0.025 | +0.066 | 0.000 | +0.046 |
| The day's minimum, built with #132 | +0.037 | +0.020 | +0.056 | +0.011 |

- The signs are the ones thermal lag gives. In summer, warmer earlier hours go with an
  under-forecast. In winter, with an over-forecast. In the other months every correlation
  is within ±0.02.
- The signal is weak: at most 0.13, under 2 % of the error's variance in those hours.
- The offset matters little, and a lag does as well as a mean.
- `max_popw_forecast_temperature_c`, built with #132 and in no preset, tracks the summer
  error as well in the afternoon and better over the whole day. In winter the earlier hours
  track it better than any of the day's three summaries.
- The six-hour mean and the three-hour lag, taken the same way, correlate at 0.98 with each
  other in every season, and at 0.42 to 0.56 with the day's mean.

The issue shows the same signal another way: at the same season, hour and temperature, the
bias swings about 190,000 kWh per period between the thirds of the earlier hours' warmth.

Facts for the experiment to weigh, not a verdict on the candidate.

## 5. Build, as the draft proposed it, not built

- `ftr_hour_msm`: after `weighted`, a step reads the five earlier hours by their offset on
  an hour index, within the area and the vintage. The next step does the arithmetic. The
  columns join `final` as `accumulated`'s running total does today.
- dbt unit tests, written first:
  - six known temperatures give their mean, added oldest first, on exact doubles, with a
    Python reference that matches to the bit;
  - the hours ending 01:00 to 05:00 give a null mean, and 01:00 to 03:00 a null lag;
  - an hour without a temperature gives a null mean on the six hours that read it;
  - the lag is the value three hours earlier;
  - two vintages of one day do not mix.
- The check against the real data: each column against the definition on every row; the
  nulls are the first hours and no others; no earlier column, row or `available_at` of
  `ftr_hour_msm` moved, against a copy taken before the build, and `ftr_day_msm` is
  unchanged.
- `just feature-views`, the fixture's columns in `tests/conftest.py`,
  `docs/Feature-Naming.md`, `CLAUDE.md`. Wang, Liu and Hong (2016), the paper the issue
  cites, gets its row in `docs/research/literature-review.md`.

## 6. Out of scope

The same windows over humidity or radiation; the running radiation total exists. Windows
longer than D's own hours allow, which need #133's leads. The observed temperature of the
hours before: the mart's observations end at D-2, and #150 accumulates those. The day's
summaries, which are #132's and built.
