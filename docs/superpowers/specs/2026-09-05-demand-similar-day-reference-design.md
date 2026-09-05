# Learned similar-day reference load for the demand task — design

Date: 2026-09-05. Status: **approved in chat 2026-09-05**; implemented the same day per
`docs/superpowers/plans/2026-09-05-demand-similar-day-reference.md`, with the deviations noted
in §4.1–4.2. Research: `demand/R-004`, experiment E-002.

## 1. Goal

Add one feature to the demand model: the load of a similar day from one year earlier. A
learned selector picks that day. For each delivery day D, it scores every day in a window
one year back by a weighted distance. The distance combines the calendar, the holiday
context and the weather: the target's forecast against the candidate's observation. The
weights are learned from pairs of past days whose loads we already know.
The model gets the chosen day's load as `similar_day_demand_kwh`.

R-004 (rejected 2026-09-05) picked the reference day with a fixed rule. The rule failed on
proximity days such as 2026-08-10, because history often has no day with the same calendar
shape. A learned selector always returns the nearest day, and the data decide how much
calendar shape, season and temperature matter. The method is section 2.2 of Park, Song and
Kwon (2020), adapted to the Japanese calendar as discussed in the researcher's consultation
of 2026-09-02 to 09-04.

## 2. Decisions

1. **Weights are non-negative and sum to one, and the fit enforces this** (softmax). This
   is the researcher's requirement. Scaling all weights does not change the ranking, so
   this only pins down α and makes each weight a share of the squared distance.
2. **Candidate loads come from the でんき予報 hourly series** (`fct_area_power_usage_hourly`,
   Tokyo, no gaps from 2016-04-01). The researcher chose it over the A-1 series (from
   2022-04-01) because it goes back further. R-004's `AreaHourlyLoad`,
   `load_area_hourly_load` and the hour-to-period join (÷2), removed in PR #35, come back.
   The strategy works for Tokyo only until another TSO's でんき予報 is loaded.
3. **Window: D − 364 ± 30 days.** That is 61 days centred on the same weekday one year
   back. The ±30 is the researcher's choice.
4. **Weather pairs the target's forecast with the candidate's observation.** A target day
   needs its MSM forecast profiles (the D-2 12 UTC vintage, from 2019-04-01; checked
   2026-09-05: 2,716 days with no gaps through 2026-09-06). A candidate needs observed
   profiles from `fct_jma_weather_hourly` (from 2016-01-01). A candidate's load came from
   its actual weather, and the target has only a forecast; the model already mixes the two
   this way (forecast for D, observed lags). So the first day that can be scored is
   2019-04-01, every A-1 training row has the feature, and E-002 needs no `--train-start`.
5. **Seven distance parts, all continuous** (the researcher's list, 2026-09-05): distance
   in calendar days, temperature difference, humidity difference, rainfall difference, days
   since the last holiday, days until the next holiday,
   and 休日度合い (degree of holiday-ness, patent JP 4448226 B2, 新日本製鐵, 伊勢・藤崎,
   2000). No 0/1 flags: weekday, day type and the special-period flag of the first draft are
   dropped. Solar irradiation, the paper's third weather term, is left out: only 7 of the
   21 weighted Tokyo stations observe it (55.5 % of the population weight; 横浜, 千葉 and
   熊谷 do not). Each weather part sets the target's forecast against the candidate's
   observation (decision 4). Definitions in section 4.3.
6. **Load difference = the paper's Eq. (3) on the 24 hourly values:**
   `y(T, C) = (1/24) Σ_h |L_T(h) − L_C(h)| / L_T(h)`. Both curves come from the でんき予報
   hourly series (confirmed 2026-09-05): one series, one unit, and pairs for every target
   from 2019-04-01. The A-1 target would have limited the fit to targets from 2022-04.
7. **The weights are fitted once per run**, on every pair whose target day is before the
   first forecast day. They are then frozen and logged to MLflow (confirmed 2026-09-05).
   Two follow-ups, not part of E-002: check how much the weights move over time (fit them
   on successive yearly or rolling windows of targets and compare), and refit them during
   the backtest at each LightGBM refit if they do move.
8. **One feature: the single nearest day.** Top-K days, the distance itself and a blended
   curve are later experiments.
9. **Holiday attributes come from `dim_date`.** "Holiday" for the two distances means a
   named holiday, `is_holiday = true`: 祝日 plus 年末年始 12/30 to 1/3, ゴールデンウィーク 4/30
   to 5/2 and お盆 8/13 to 8/16. Weekends do not count. This follows Rubattu, Maroni and
   Corani (AALTD 2023), whose "days since last / until next holiday" sit beside separate
   Holiday and Weekend features (confirmed 2026-09-05); weekends are graded by
   `holiday_degree` instead. 休日度合い is `dim_date.holiday_degree`
   (spec `2026-09-05-dim-date-holiday-degree-design.md`, branch
   `feature/dim-date-holiday-degree`): a double in {0, 0.3, 0.5, 0.8, 1.0}, the greatest of
   the calendar grade (Sunday or 祝日 1.0, Saturday 0.8), the special-period grade (first day
   0.8, other days 1.0) and the sandwiched-day grade (one bridge day 0.5, two 0.3).
10. **Which days can be scored.** D can be scored when two things hold: D has its own
    forecast profile (MSM, from 2019-04-01), and every day of its window, D − 394 to
    D − 334, is on or after the first day with both an hourly load and an observed profile
    (2016-04-01 for Tokyo). For Tokyo the forecast is the binding one: the first scorable
    day is 2019-04-01, whose window is 2018-03-03 to 2018-05-02. A window day that lacks a
    load hour, a weather hour or a calendar row is simply not a candidate; D is still
    scored on the others. Two candidates at the same distance: the one nearer D − 364
    wins, then the earlier date.
11. **Research record:** E-002 of `docs/research/demand/R-004-prior-year-load-lag.md`
    (the researcher's choice, 2026-09-05: same question, same triggers, a new way of
    choosing the day). R-004 was reopened and its question broadened the same day. The `papers.md` rows for
    Park et al. (2020) and the 新日本製鐵 patent JP 4448226 B2 were added on 2026-09-05.

## 3. What it builds on

- `SlidingWindowLightGbmStrategy` (`forecasting/lgbm.py`). A subclass sets `feature_cols`,
  `eval_set_cls`, `lookback_days` and `_add_features`. One code path builds training rows
  and prediction rows. A NaN feature drops a training row or skips a target day.
  `_extra_params()` adds run params. TreeSHAP values are recorded per day.
- The baseline `lightgbm_msm_popw_daytype`. Features: `time_code, month, day_of_week,
  wavg_temperature_c, lag_7d_demand_kwh, popw_forecast_temperature_c, day_type`. Inputs:
  `AreaTemperature`, the population-weighted `AreaTemperatureForecast` (one row per
  `trade_date × hour_ending` 1..24) and `DayTypeCalendar`.
- `fct_area_power_usage_hourly`: hourly `demand_kwh` (万kW × 10,000), Tokyo, no gaps
  2016-04-01 to 2026-08-30. Commit `83b1f91` had its reader, frame, test fixture table and
  the `join_prior_year_load` join. Commit `6a9d10a` removed them.
- `fct_jma_msm_weather_forecast_hourly`: 2019-04-01 to 2026-09-06, no gaps. Its
  `temperature_c`, `relative_humidity_pct` and `precipitation_mm` (mm over the hour) are
  the target side of the three weather parts.
- `fct_jma_weather_hourly`: hourly observations at the staffed stations, 2016-01-01 to the
  present, with the same three variables (`humidity_pct` for humidity) at all 21 weighted
  Tokyo stations, under 0.3 % of hours missing at any of them; the candidate side of the
  weather parts.
- `dim_date` columns: `is_weekend`, `is_holiday`, `holiday_degree` (decision 9).
- `scipy` 1.18.0 is already in `uv.lock` (LightGBM and SHAP need it) but is not declared.

## 4. The selector — `tasks/demand/similar_day.py`

One module holds everything: day attributes, windows, pair differences, the load
difference, the weight fit, selection, the feature join and the retrieval check. The fit
code does not depend on the demand task and can move to `forecasting/` later.

### 4.1 `DayCalendar` (frame, `tasks/demand/frames.py`)

One row per `dim_date` day that has a day before and after it. Key: `trade_date`.

| column | dtype | definition |
|---|---|---|
| `day_type` | int64 | `day_type_code(is_weekend, is_holiday)`: 0 Weekday, 1 Weekend, 2 Holiday — for the parent strategy only |
| `days_since_holiday` | int64 | 0 on a named holiday; else days since the last one (`is_holiday`, decision 9). No cap: `s_j` absorbs the scale |
| `days_until_holiday` | int64 | 0 on a named holiday; else days until the next one |
| `holiday_degree` | float64 | `dim_date.holiday_degree`, in {0, 0.3, 0.5, 0.8, 1.0} (decision 9) |

`DayCalendar.day_types()` returns the `DayTypeCalendar` the parent strategy needs, so
`dim_date` is read once. `datasets.load_day_calendar(spark=None)` reads the columns above and
computes the two holiday distances in pandas with a forward and a backward fill over the
spine (no gaps; as implemented, in place of SQL window functions), then drops the days before
the spine's first holiday and after its last so both distances are defined (the spine starts
2016-01-01, a holiday), and logs the counts.
`load_day_types` does not change.

### 4.2 Candidates

`SIMILAR_DAY_CENTER_LAG_DAYS = 364`, `SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS = 30`. The window
of D is `D − 394` to `D − 334`. A candidate is a window day with all 24 hourly loads, full
24-hour observed profiles of temperature, humidity and rain, and a `DayCalendar` row. D
itself can be scored when it has full 24-hour forecast profiles of the same three variables,
a calendar row, and `D − 394` on or after `first_candidate_day`, the later of the
hourly series' first day and the observed profiles' first day (2016-04-01). Every candidate
is at least 334 days before D, well inside the D − 2 cutoff. The hourly frame is loaded
whole, so the window is what keeps selections in the past.

Both profile frames are population-weighted over the area's staffed stations with the census
weights, renormalised over the stations that have a value for the hour, one row per
`trade_date × hour_ending` 1..24:

- Forecast, target side: a new frame `AreaWeatherForecast` (`forecast_temperature_c`,
  `forecast_relative_humidity_pct`, `forecast_precipitation_mm`, nullable) from
  `load_area_weather_forecast_population_weighted`; its `temperature_forecast()` view is the
  parent's `AreaTemperatureForecast`, so that frame and its loader stay as they were (as
  implemented: many tests build `AreaTemperatureForecast`, so widening it was not worth it).
- Observed, candidate side: a new frame `AreaObservedWeather` (`temperature_c`,
  `humidity_pct`, `precipitation_mm`, nullable; keyed `obs_date × hour_ending` like
  `AreaTemperature`) and
  `load_area_observed_weather_population_weighted(area_code, census_year)` over
  `fct_jma_weather_hourly` and `fct_census_population_jma_station`. The parent's observed
  `wavg_temperature_c` feature stays single-station.

### 4.3 `DayPairDifferences` (frame)

Key: `(target_date, candidate_date)`. The parts, in this fixed order:

| part | raw definition | unit |
|---|---|---|
| `calendar_days` | `abs((T − C) − 364)`: days away from the same weekday one year back | days |
| `temperature` | RMSE over hours 1..24 of the target's forecast profile against the candidate's observed profile; the paper's Eq. (1) Euclidean distance ÷ √24 | °C |
| `humidity` | the same for relative humidity | % |
| `rain` | the same for rain | mm per hour |
| `days_since_holiday` | `abs(Δ days_since_holiday)` | days |
| `days_until_holiday` | `abs(Δ days_until_holiday)` | days |
| `holiday_degree` | `abs(Δ holiday_degree)` | 0 to 1 |

Every part is a non-negative number; there are no flags. `calendar_days` is 0 at D − 364,
the window's centre (decision 3; 2026-09-05, a Saturday, has 2025-09-06, also a Saturday,
364 days back), and 30 at the window edges; multiples of 7 are same-weekday days. The three
weather parts are RMSEs over the 24 hourly values, as in the paper's Eq. (1).

`SimilarDayTrainingPairs` adds `load_difference` (decision 6, never null). It exists only
for past targets. At selection time the distance has no load term.

### 4.4 `SimilarDayWeights` (frozen dataclass)

```
d(T, C) = sqrt( Σ_j w_j · (Δ_j / s_j)² )     Σ_j w_j = 1,  w_j ≥ 0
ŷ(T, C) = α · d(T, C) + β
```

- `s_j`: the RMS of each part over the training pairs (all seven are continuous). Frozen
  with the weights and reused at selection time.
- Fields: `components`, `weights`, `scales`, `alpha`, `beta`, `n_pairs`, `n_targets`,
  `fit_from`, `fit_through`, `fit_rmse`. Methods: `distance(differences)` and
  `as_params()` (the MLflow view; weights written as
  `calendar_days=0.17,temperature=0.42,…`).
- How to read a weight: `w_j` is the share of the squared distance that a typical
  difference in part j adds.

### 4.5 `fit_similar_day_weights(pairs) -> SimilarDayWeights`

Nonlinear least squares on the paper's cost `Σ_k (y_k − (α·d_k + β))²`, solved with
`scipy.optimize.least_squares` (`trf`).

- Parameters: raw scores `z_1 … z_6`, with `z_7` fixed at 0 so the softmax has one answer;
  `α ≥ 0`; `β` free. `w = softmax([z, 0])`.
- Start: equal weights; `α` and `β` from a straight-line fit of `y` on that distance. No
  randomness.
- If the solver fails: `RuntimeError`. Fewer than 8 pairs: `ValueError`.
- E-002 size: about 120 k pairs, well under a second.

### 4.6 `SimilarDaySelector`

```
SimilarDaySelector(calendar, weather_forecast, weather_observed, hourly_load, *,
                   center_lag_days=364, half_width_days=30)
  .training_pairs(through) -> SimilarDayTrainingPairs   # scorable targets up to `through`
  .fit(through) -> SimilarDayWeights                     # stores; .weights raises before
  .ensure_fitted(through)                                # fit once
  .differences(days) -> DayPairDifferences
  .select(days) -> SimilarDaySelection
  .retrieval(selection) -> SimilarDayRetrieval
```

`through` is passed in because the hourly frame runs to the present. The strategy passes
the newest day it may see.

`SimilarDaySelection` (key `trade_date`): `reference_date`, `distance`,
`reference_lag_days`, `n_candidates`, `lag_364_rank` (rank of the plain 52-week day; NaN if
it is not a candidate). The pick is the smallest distance, with the tie rule of decision
10. NumPy over `n_days × 61` pairs; no cache needed.

### 4.7 `join_similar_day_load(points, selection, hourly_load, *, name)`

For `(D, time_code)`: the hourly `demand_kwh` at
`(reference_date[D], hour_ending_of(time_code))`, divided by 2 so it is on the target's
per-period scale. NaN when D has no selection. This is R-004's join with the selection
frame in place of the calendar column.

### 4.8 `SimilarDayRetrieval` (frame, key `trade_date`)

The paper's after-the-fact check, and the researcher's "compare against t − 364". Once a
forecast day's hourly load is known, compute the realised `y` of every candidate and report
`reference_date`, `distance`, `selected_load_difference`, `lag_364_load_difference`,
`oracle_date`, `oracle_load_difference` and `selected_rank_by_outcome`. This tests the
selector by itself, apart from what LightGBM does with the feature.

## 5. Strategy — `lightgbm_msm_popw_daytype_simday`

`LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(LightGbmMsmPopWeightedDayTypeStrategy)`:

- `feature_cols` = the parent's plus `similar_day_demand_kwh`, with a matching eval-set
  class. `categorical_feature_cols` does not change.
- `lookback_days` stays 8. The feature reads the hourly frame, not the history slice.
- `__init__(temperature, weather_forecast, day_calendar, weather_observed, hourly_load, *,
  census_year, window_half_width_days=30, **kwargs)`: passes `weather_forecast.temperature_forecast()`
  and `day_calendar.day_types()` to the parent and builds the selector.
- `predict`: calls `selector.ensure_fitted(through=history.df["trade_date"].max())` before
  `super().predict`. The first history the engine passes ends at the first target day's
  cutoff, which is the right fitting set, and the parent's refit needs the weights already
  fitted. If there are no pairs by that cutoff, the day raises `ForecastUnavailableError`
  and a later day fits. The target day's selection is recorded, like `_shap_records`.
- `_add_features`: the parent's, then `join_similar_day_load(featured,
  selector.select(days), hourly_load)`.
- `build_eval_set` before any `predict` raises `RuntimeError` (weights not fitted).
- `_extra_params()`: the parent's plus `similar_day_center_lag_days`,
  `similar_day_window_half_width_days`, `similar_day_components`,
  `SimilarDayWeights.as_params()`, `similar_day_first_selectable_day`,
  `similar_day_hourly_load_span`, `similar_day_periods_per_hour`.
- `diagnostics(history, run)` (section 6): returns the `similar_day_selection` and
  `similar_day_retrieval` frames and logs four metrics:
  `similar_day_load_difference_selected`, `…_lag_364`, `…_oracle` (means over forecast
  days) and `similar_day_share_better_than_lag_364`.
- A registry entry, and a `build_strategy` branch before the day-type one that also loads
  `load_day_calendar`, `load_area_observed_weather_population_weighted(area_code,
  census_year)` and `load_area_hourly_load(area_code)`. The loader raises for an area
  without the series. The baseline and the script default do not change.

**Leakage.** D's selection uses calendar attributes, the MSM forecast for D (available
before 09:30 on D-1) and candidate data — loads and observed weather — at least 334 days
old. The weights use targets up
to the first forecast day's cutoff. Training rows get their feature from a metric that saw
their own loads. That is in-sample for the training set only; no forecast day's actual is
used anywhere.

## 6. Hook — `ForecastStrategy.diagnostics`

```
def diagnostics(self, history: HistoryT, run: BacktestRun) -> dict[str, pd.DataFrame]:
    """Per-run artifacts computed after the backtest, keyed by artifact stem.
    Called inside the MLflow run; may log metrics. Default: {}."""
```

Both backtest scripts call it after publishing and log each frame as `<stem>.csv`.
Existing strategies return `{}`. Same pattern as `contributions()`; the scripts never check
the strategy type.

## 7. Dependencies and docs

- `dim_date.holiday_degree` from branch `feature/dim-date-holiday-degree`, merged to `main`
  as PR #42 on 2026-09-05 before the E-002 branch was cut.
- `pyproject.toml`: declare `scipy>=1.18`. Add `scipy-stubs` or an `ignore_missing_imports`
  entry if mypy needs it.
- No dbt or dashboard change. The feature shows up as one more SHAP component.
- `CLAUDE.md`: the strategy, the selector, the window, the feature's first day and the
  hook. R-001 still gives the MSM start as 2022-04; it is 2019-04-01 since PR #36.
- R-004's E-002 section, status and README index row: written with this spec.

## 8. Errors

- Frame checks: day-type codes in range, holiday distances ≥ 0, `holiday_degree` in
  {0, 0.3, 0.5, 0.8, 1.0}, every part ≥ 0, `load_difference` never null and ≥ 0, reference
  dates inside the window.
- `load_day_calendar`, `load_area_observed_weather_population_weighted` and
  `load_area_hourly_load`: empty result raises `ValueError`.
- Fit: too few pairs raises `ValueError`; solver failure raises `RuntimeError`.
- A day that cannot be scored gets a NaN feature, and the framework drops or skips it as
  usual.

## 9. Research record

E-002 of `docs/research/demand/R-004-prior-year-load-lag.md` holds the experiment: why,
hypothesis, change, expected evidence, decision rule and execution (the E-002 command,
baseline run `0a6b8a55…`, the fitting targets 2019-04-01 to 2024-08-16). This spec adds
nothing to it; the investigation is the record, the spec the design.

## 10. Out of scope

Top-K features; the distance as a feature; a blended curve; the paper's RL selector;
re-fitting the weights during the backtest; candidates outside the one-year window; a
Kansai run; the A-1 series as candidate source; stitching the two load series; warehouse or
dashboard changes.

## 11. Tests and verification

- `just test` (100 % coverage), `just lint`, `just mypy`.
- `test_demand_similar_day.py`: calendar attributes; windows and candidate filtering; each
  part on hand-built pairs; the load difference; scaling; the fit recovers planted weights
  from synthetic pairs and rejects too few; selection, tie rule and `lag_364_rank`; the
  join; the retrieval frame.
- `test_demand_lgbm.py`: the strategy end to end on synthetic frames. The first `predict`
  fits; an early day is skipped; `build_eval_set` before `predict` raises; the eval set and
  the contributions carry the feature; the params carry the weights; `diagnostics` returns
  both frames and logs four metrics.
- `test_demand_datasets.py`: `load_day_calendar` (the `dim_date` fixture gains
  `holiday_degree`; the holiday distances on a hand-built fortnight with one 祝日), the forecast loader's humidity and rain columns, the observed
  population-weighted loader (a `fct_jma_weather_hourly` fixture with the three variables),
  and the restored
  `load_area_hourly_load` with its fixture table.
- `test_demand_strategies.py`: `build_strategy`. Both script tests: the hook's artifacts.
- In the devcontainer: the E-002 run, `just dbt build --select
  +fct_demand_forecast_accuracy +fct_demand_forecast_contribution`,
  `compare_demand_runs.py`, and the retrieval summary.

## 12. Open points

None. Every decision above was settled with the researcher on 2026-09-05.
