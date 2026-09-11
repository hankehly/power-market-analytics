# Similar day as a fitted feature — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The demand similar-day feature becomes a mart column scored in SQL from weights a script fits in Python (the spec's "fitted feature"). The five similar-day strategies become presets over it; their classes, the Python feature join and the calendar join go away. Run `008868fe…` is reproduced.

**Architecture:** `scripts/fit_similar_day.py` fits the seven weights with the existing `SimilarDaySelector` on the pairs up to `--fit-through`, logs the fit to MLflow and writes one row to `pma_ml.similar_day_parameters` (partitioned by `run_id`, like the forecast tables). `stg_ml__similar_day_parameters` reads it behind the same guard as the importance tables. `ftr_period_similar_day` scores every delivery day with a forecast profile against its D − 364 ± 30 window in SQL, per parameter vintage, and emits the chosen day's hourly load ÷ 2 per period with the selection next to it. The feature reaches the model through Feast like any other; the presets add `ftr_period_similar_day:similar_day_demand_kwh` to `lightgbm_msm_popw_daytype`.

**Tech Stack:** dbt on Spark SQL (arrays, `zip_with`, `aggregate`, the `ordered_weighted_mean` macro), feast 0.66 (`created_timestamp_column`), scipy, the `forecasting` and `features` packages.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §4 (marts), §5 (fitted features), §6 (Feast), §10 (reproduction), §11 PR 7.

## Rework, 2026-09-11

After PR #67 was reviewed and green, the researcher found it larger than expected and chose
the write-back design over SQL scoring ("I don't care if dbt scores the feature"). Tasks 1
and 2 were reworked in place: the script fits, scores every day and writes the feature
values to `pma_ml.similar_day` (`tasks/demand/similar_day_feature.py`); the mart is a
pass-through of the guarded `stg_ml__similar_day`; the parameters table, its staging
model, the `profile_rmse` macro and the SQL scoring are gone; `AreaWeatherForecast` carries
the vintage's `available_at`, which the written rows take. The vintage rule of the SQL
design (the oldest vintage backfilling history) is not needed: a run scores every day it
can, and the newest published run wins wherever it scored. Task 3 is unchanged. The
reproduction of Task 4 was rerun on the write-back; its results are under Task 4.

## Global Constraints

- The scoring in SQL reproduces `tasks/demand/similar_day.py`: window D − 364 ± 30 from the parameters row; a candidate needs all 24 hours of population-weighted observed temperature, humidity and rain (the latest census vintage's station weights, added in station order), all 24 hourly loads and a calendar row with both holiday distances; a target needs all 24 hours of the `ftr_hour_msm` population-weighted forecast of one vintage and a calendar row, and a window that starts on or after the area's first candidate day; parts: `abs(lag − 364)`, the three 24-hour RMSEs (target forecast against candidate observation), `abs(Δ days_since_holiday)`, `abs(Δ days_until_holiday)`, `abs(Δ holiday_degree)`; distance `sqrt(Σ w_j (part_j / s_j)²)`; the smallest distance wins, ties to the candidate nearest D − 364, then the earlier date; the feature is the chosen day's hourly load at `(time_code + 1) div 2` ÷ 2.
- Sums are in a fixed order (hour order through `zip_with` + `aggregate`, station order through the macro), so a rebuild gives the same values.
- **Vintages.** The parameters row carries `available_at` = the midnight after `fit_through` (the spec's fit-window end). A mart row's `available_at` is the greatest of its data's (the forecast vintage, the window's candidates) and, for every vintage but the area's oldest, the vintage's own. The oldest vintage scores the history before it, because a backtest's training rows lie before the fit that serves its forecasts (the similar-day spec's decision 7: weights frozen at the first forecast day, in-sample for the training window). Among rows tied on `available_at` the newest `published_at` wins (Feast's `created_timestamp_column`), so a refit re-scores every row from its fit-window end on and older rows keep their vintage. This deviates from the spec's "the join picks the newest parameters available at the issue time" for rows older than the first fit; it is flagged to the researcher in the PR.
- Feature order of each preset = the old class's: the `lightgbm_msm_popw_daytype` columns, `similar_day_demand_kwh`, then the calendar columns in `DAY_CALENDAR_FEATURE_COLS` order. `time_code` first.
- Reproduction (spec §10): on the worktree's build, the fit through 2024-08-16 must give run `008868fe…`'s weights, scales, α, β, pair and target counts; the mart's reference day must equal the run's `similar_day_selection.csv` on every forecast day and the Python selector's on every scorable day; then `main` (the Python-joined feature) and the branch run `lightgbm_msm_popw_daytype_simday` for Tokyo 2024-08-18..2026-08-17 without `--train-start` and `--importance-repeats 1`: features and forecasts identical to the bit. The pair against `008868fe…` itself (old marts, old code) is reported as a matched comparison.
- Coverage 100 %; `just lint`, `just mypy`, `dbt parse` and `just feature-views --check` clean.
- Branch `feature/similar-day-feature`, worktree `.claude/worktrees/similar-day-feature`; commit types `feat(demand)`, `feat(dbt)`, `feat(features)`, `refactor(forecasting)`; PR labels `enhancement` + `documentation`.
- Container runs from the worktree: `docker compose --project-directory /Users/hankehly/Projects/power-market-analytics exec -T -w /workspace/.claude/worktrees/similar-day-feature -e PYTHONPATH=/workspace/.claude/worktrees/similar-day-feature devcontainer python …`; one Spark job at a time; warehouse comparisons through the devcontainer session, not beeline.

## The parameters table

`pma_ml.similar_day_parameters`, parquet partitioned by `run_id`, one row per fit:

| column | type | value |
|---|---|---|
| `area_code` | string | the area fitted |
| `fit_from`, `fit_through` | date | first and last target day of the pairs |
| `center_lag_days`, `window_half_width_days` | int | the window, 364 ± 30 |
| `census_year` | int | the station weights' vintage |
| `weight_<part>` × 7, `scale_<part>` × 7 | double | `SimilarDayWeights.weights` / `.scales` in `SIMILAR_DAY_COMPONENTS` order |
| `alpha`, `beta`, `fit_rmse` | double | the straight line and its RMSE |
| `n_pairs` | bigint; `n_targets` int | the fit's size |
| `available_at` | timestamp | `fit_through` + 1 day 00:00 |
| `published_at` | timestamp | when the row was written |
| `run_id` | string | the fit's MLflow run (experiment `similar_day`) |

## The mart

`ftr_period_similar_day`, grain `area_code × trade_date × time_code × forecast_reference_at × parameters_run_id`: `similar_day_demand_kwh` (double, the feature), `similar_day_reference_date` (date), `similar_day_reference_lag_days` (int), `similar_day_distance` (double), `similar_day_n_candidates` (int), `available_at`, `published_at` (the vintage's). Only the feature is tagged.

---

### Task 1: The parameters table and the fit script

**Files:**
- Create: `power_market_analytics/tasks/demand/similar_day_parameters.py`, `scripts/fit_similar_day.py`
- Modify: `power_market_analytics/forecasting/publish.py` (`create_run_partitioned_table`, `overwrite_run_partitions` public), `power_market_analytics/tasks/demand/similar_day.py` (nothing yet)
- Test: `tests/test_demand_similar_day_parameters.py`, `tests/test_fit_similar_day_script.py` (new)

**Interfaces:**
```python
# similar_day_parameters.py
MLFLOW_EXPERIMENT = "similar_day"
PARAMETERS_TABLE = "pma_ml.similar_day_parameters"
class SimilarDayParameterRecords(DomainFrame)   # the table's columns; keys ["run_id"]; weights >= 0 summing to 1, scales > 0, alpha >= 0, fit_from <= fit_through, available_at == fit_through + 1 day
def build_parameter_records(weights: SimilarDayWeights, *, run_id, area_code, center_lag_days, window_half_width_days, census_year, published_at: pd.Timestamp) -> SimilarDayParameterRecords
def publish_parameter_records(records, spark=None) -> int   # create_run_partitioned_table + overwrite_run_partitions
```
Script: `--area` (AREA_CODES, tokyo), `--fit-through YYYY-MM-DD` (default: the last day with an hourly load), `--window-half-width-days` (30). Loads the four inputs with the demand loaders, builds the selector, `fit(through)`, logs `area`, `fit_through`, `population_weight_census_year`, `n_stations`, the selector's params (`as_params()`, window, components, first scorable day, load span, periods per hour); publishes the row (tag `parameters_table`); selection over every scorable day → `similar_day_selection.csv`; retrieval over the days with a known load, column `in_fit` = `trade_date <= fit_through` → `similar_day_retrieval.csv`; the four `retrieval_metrics` over the out-of-sample days when there are any.

- [x] **Step 1: Failing tests** — records: columns and dtypes, `available_at` is the midnight after `fit_through`, weights sum to one, a bad record is rejected; publish writes one partition and a republish replaces it. Script: over the synthetic frames of `tests/test_demand_similar_day.py` (loaders monkeypatched in the script's namespace): a FINISHED run in experiment `similar_day` named `similar_day-tokyo`, the params, the tag, both CSVs, the metrics when days after `fit_through` have a load, no metrics when the fit runs through the last day, the published row.
- [x] **Step 2: Run** — `uv run pytest tests/test_demand_similar_day_parameters.py tests/test_fit_similar_day_script.py -q --no-cov`; ImportError.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** — pass.
- [x] **Step 5: Commit** — `feat(demand): fit the similar-day weights once and publish them to pma_ml.similar_day_parameters`.

### Task 2: The mart, its staging model and the view

**Files:**
- Create: `dbt/models/staging/stg_ml__similar_day_parameters.sql/.yml`, `dbt/models/features/ftr_period_similar_day.sql/.yml`
- Modify: `dbt/models/raw/ml.yml` (the source, no tests), `scripts/generate_feature_views.py` (`published_at` → `created_timestamp_column`), `power_market_analytics/features/views.py` (regenerated)
- Test: dbt unit test `ftr_period_similar_day_scores_the_window_per_vintage` (`format: sql` fixtures: two vintages, three window days one of which lacks an observed hour, 96 expected rows), `tests/test_generate_feature_views.py`, `tests/test_feature_views.py`

- [x] **Step 1: Failing tests** — generator: a mart with `published_at` gets `created_timestamp_column="published_at"` and selects it; one without does not. Views: seven names; `FTR_PERIOD_SIMILAR_DAY_SOURCE.created_timestamp_column == "published_at"`.
- [x] **Step 2: Run** — fail.
- [x] **Step 3: Implement** — the models, the generator, `just feature-views`; `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse`; the unit test and the build run in the container in Task 4.
- [x] **Step 4: Run** — pass.
- [x] **Step 5: Commit** — `feat(dbt): ftr_period_similar_day scores the similar day per parameter vintage`.

### Task 3: The five presets; delete the classes

**Files:**
- Modify: `power_market_analytics/tasks/demand/presets.py`, `strategies/__init__.py` (`STRATEGIES = tuple(PRESETS)`, no similar-day branch, no `spark`), `similar_day.py` (drop `join_similar_day_load`, `SIMILAR_DAY_FEATURE`), `frames.py` (`DayCalendar` = `trade_date` + the three holiday attributes; drop `DAY_TYPE_LEVELS`, `CALENDAR_COUNT_RANGES`), `datasets.py` (`load_day_calendar` reads only those), `forecasting/preset_lgbm.py` (drop `extra_dtypes`), `scripts/demand_backtest.py` (docstring), `tests/conftest.py` (`ftr_period_similar_day` from the fixture: reference D − 364, `similar_day_load(day, time_code)`)
- Delete: `tasks/demand/strategies/lgbm.py`, `tasks/demand/features.py`, `tests/test_demand_lgbm.py`, `tests/test_demand_features.py`
- Test: `tests/test_demand_presets.py`, `tests/test_demand_strategies.py`, `tests/test_demand_scripts.py` (a `simday` run over the fixture), `tests/test_demand_similar_day.py`, `tests/test_demand_frames.py`, `tests/test_demand_datasets.py`, `tests/test_forecasting_preset_lgbm.py`, `tests/test_features_presets.py`

**Interfaces:**
```python
SIMILAR_DAY_FEATURE = "ftr_period_similar_day:similar_day_demand_kwh"
DAY_CALENDAR_FEATURES, HOLIDAY_DEGREE_FEATURES, HOLIDAY_DISTANCE_FEATURES, CALENDAR_COUNT_FEATURES: tuple[str, ...]   # ftr_day_calendar refs in the old column order
LIGHTGBM_MSM_POPW_DAYTYPE_SIMDAY = LIGHTGBM_MSM_POPW_DAYTYPE.with_changes(name="lightgbm_msm_popw_daytype_simday", add=(SIMILAR_DAY_FEATURE,))
…_SIMDAY_CALENDAR / _CALENDARCOUNTS / _HOLIDAYDEGREE / _HOLIDAYDISTANCE = …_SIMDAY.with_changes(name=…, add=<the tuple>)
PRESETS: the four, simday, calendar, calendarcounts, holidaydegree, holidaydistance
```

- [x] **Step 1: Failing tests** — presets: nine names in that order, the simday feature columns, `feature_dtypes` of the calendar variants (`holiday_degree` float64, the rest int64), `day_type` the only categorical; registry `STRATEGIES == tuple(PRESETS)`; `build_strategy("lightgbm_msm_popw_daytype_simday", …)` is a `PresetLightGbmStrategy` whose frame carries `similar_day_load(day, tc)` and NaN on `FORECAST_MISSING_DAY`; script: a simday run publishes eight components; the catalogue lists eleven services.
- [x] **Step 2: Run** — fail.
- [x] **Step 3: Implement** — then `just test`, `just lint`, `just mypy`.
- [x] **Step 4: Run** — pass, 100 %.
- [x] **Step 5: Commit** — `feat(demand): the similar-day strategies become presets over ftr_period_similar_day`.

### Task 4: Reproduction, docs, PR

- [x] **Step 1: Fit** — `python scripts/fit_similar_day.py --area tokyo --fit-through 2024-08-16` in the container, run `00c8b8553d3c46a4bae2ae06c35b29b3`: weights `calendar_days=0.0530, temperature=0.4320, humidity=0.0000, rain=0.0003, days_since_holiday=0.0381, days_until_holiday=0.0000, holiday_degree=0.4766`, scales `17.61, 4.557, 19.65, 1.272, 19.29, 19.41, 0.6167`, α 0.108417, β 0.022378, 119,865 pairs over 1,965 targets (2019-04-01 to 2024-08-16), RMSE 0.075297 — every value equal to run `008868fe…`'s params. 2,717 days selected, 2,715 checked (750 after the fit).
- [x] **Step 2: Build** — `dbt build --select stg_ml__similar_day_parameters ftr_period_similar_day`: 63 nodes pass (the unit test included). The mart holds 2,717 Tokyo days, 2019-04-01 to 2026-09-07, 48 periods each, one vintage; `available_at` from 2019-03-31 01:00 to 2026-09-06 01:00 (the forecast vintage's, the fit being the oldest vintage).
- [x] **Step 3: Selection parity** — against the run's `similar_day_selection.csv`: the same reference day and candidate count on all 729 forecast days, distance within 3.6e-9 (the run's fit was a separate least-squares solve, converged to its 1e-8 tolerance). Against `SimilarDaySelector.select` over every scorable day (the fit above): the same reference day and candidate count on all 2,717 days, distance within 8.0e-16. The feature equals the chosen day's hourly load ÷ 2 exactly on 2,400 sampled periods.
- [x] **Step 4: Runs** — `main` (the Python-joined feature) run `425ae3977acf4d9ca8d991c49c5af4af` and the branch run `7db80fbe3eb84ca5bff19609ed2d7360`, both `--strategy lightgbm_msm_popw_daytype_simday --area tokyo --start-date 2024-08-18 --end-date 2026-08-17 --importance-repeats 1`: 729 days, 34,954 periods, one skipped day (2025-06-21) each, MAE 585,065 kWh both. Every feature value of every scored period is identical (8 features × 34,954 periods, max difference 0); the forecasts are identical on 34,953 of 34,954 periods and 1.9e-9 kWh apart on the last (multi-threaded LightGBM); the SHAP contributions identical on 277,175 of 279,632 and within 2.3e-10 kWh. Against the reference run `008868fe…` (the pre-PR-66 marts, the class-based code, MAE 585,362 kWh): MAE 585,362 → 585,065 kWh (−0.1 %); the forecasts are identical to the digit from 2025-01 on (every month, season and band from then shows +0), and differ only in 2024-08 to 2024-12 (+0.5 % to −1.2 % by month) — the months whose 730-day training window still reaches the two December-2022 delivery days whose D-7 file was re-published after the issue time (the as-of join hides them, the class-based code read them; see CLAUDE.md Gotchas). Candidate lower on 71 of 729 days, equal on most; 95 % bootstrap CI over days [−1,081, +465] kWh. The identical months also show that the ordered weighted-mean marts of PR #66 equal the deleted pandas builders to the bit: the 1e-14 differences of PR 6's reproduction are gone..
- [x] **Step 5: Docs** — this entry, the spec (§3 table, §5 as built with the vintage rule, §7, §11, §13), CLAUDE.md, memory.
- [ ] **Step 6: PR** — `feat(demand): the similar day as a fitted feature mart; delete the last strategy classes`, labels `enhancement` + `documentation`, Codex loop.
