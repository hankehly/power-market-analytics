# Demand presets — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The four demand LightGBM strategies without similar day become presets over Feast, run by the one `PresetLightGbmStrategy`; their classes, eval sets, feature builders, frames and loaders go away. The similar-day family keeps its Python feature until PR 7 but reads its base features from Feast too, so nothing inherits from the deleted classes.

**Architecture:** `tasks/demand/presets.py` names the four feature sets as `<view>:<column>` references in today's feature order. `build_strategy` retrieves a preset's features once per run (PR 5's path) and builds `PresetLightGbmStrategy`; for a similar-day name it retrieves the `lightgbm_msm_popw_daytype` preset and builds the family class, which now subclasses `PresetLightGbmStrategy`, merges the retrieved frame and then joins the similar day's load (and, for the calendar variants, the `DayCalendar` columns) in Python as before. `--add`, `--drop` and `--name` work as in the spot script.

**Tech Stack:** feast 0.66, pandas, LightGBM, the `forecasting` framework, the `features` package of PR 4/5.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §6 (presets), §7 (the strategy), §10 (reproduction), §11 PR 6.

## Global Constraints

- Feature column names stay the Feast field names, which are today's names (`wavg_temperature_c`, `lag_7d_demand_kwh`, `popw_forecast_temperature_c`, `day_type`, …), so SHAP components, the contribution fact and the dashboard keep their labels.
- Each preset lists its features in the old class's order; `time_code` is always first.
- A preset declares no categoricals: `day_type` is categorical because the mart tags it (`categorical_columns`, PR 5's rule).
- Reproduction (spec §10): old code on `main` and new code on the branch run `lightgbm_msm_popw_daytype` for Tokyo over the R-003 window 2024-08-18..2026-08-17 with `--train-start 2023-01-01` and `--importance-repeats 1`; feature values and forecasts must match exactly. The train start excludes the two December-2022 delivery days (2022-12-08, 2022-12-09; 96 rows) whose TEPCO file was re-published after the issue time: the as-of join hides them and the old code did not, so a run without a train start differs by those training rows. The same pair is run for `lightgbm_msm_popw_daytype_simday`, whose base features now come from Feast.
- Feast retrieval needs a UTC Spark session; the tests switch the fixture session for the marts (the `feature_marts` fixture).
- Coverage 100 %; `just lint`, `just mypy` clean.
- Branch `feature/demand-presets`, worktree `.claude/worktrees/demand-presets`; commit types `feat(demand)`, `feat(forecasting)`, `feat(features)`; PR labels `enhancement` + `documentation`.
- Container runs from the worktree: `docker compose --project-directory /Users/hankehly/Projects/power-market-analytics exec -T -w /workspace/.claude/worktrees/demand-presets -e PYTHONPATH=/workspace/.claude/worktrees/demand-presets devcontainer python scripts/demand_backtest.py …`; one backtest at a time (two at once hit a Spark internal error on PR 5).

## Presets

| Preset | Features (`view:column`) |
|---|---|
| `lightgbm` | `ftr_day_calendar:month`, `ftr_day_calendar:day_of_week`, `ftr_hour_jma_obs:wavg_temperature_c`, `ftr_period_actuals:lag_7d_demand_kwh` |
| `lightgbm_msm` | `lightgbm` + `ftr_hour_msm:forecast_temperature_c` |
| `lightgbm_msm_popw` | `lightgbm` + `ftr_hour_msm:popw_forecast_temperature_c` |
| `lightgbm_msm_popw_daytype` | `lightgbm_msm_popw` + `ftr_day_calendar:day_type` (categorical by its tag) |

The five similar-day names stay strategies: `lightgbm_msm_popw_daytype_simday` and its four calendar variants, each built over the `lightgbm_msm_popw_daytype` preset. The hour marts join on `hour_ending = (time_code + 1) // 2`, which `entity_frame` already stamps on every row.

---

### Task 1: Demand presets in the catalogue

**Files:**
- Create: `power_market_analytics/tasks/demand/presets.py`
- Modify: `power_market_analytics/features/catalogue.py` (the demand presets' services next to the spot ones)
- Test: `tests/test_demand_presets.py` (new), `tests/test_features_presets.py::TestFeatureService::test_the_catalogue_lists_the_spot_presets` and `TestStoreServices` (the demand services appear)

**Interfaces:**
- Consumes: `features.presets.Preset`, `Preset.with_changes`, `categorical_columns`, `feature_dtypes`.
- Produces:
```python
TASK_NAME = "demand"
LIGHTGBM: Preset                 # the four references above
LIGHTGBM_MSM: Preset             # LIGHTGBM.with_changes(name="lightgbm_msm", add=("ftr_hour_msm:forecast_temperature_c",))
LIGHTGBM_MSM_POPW: Preset        # LIGHTGBM.with_changes(name="lightgbm_msm_popw", add=("ftr_hour_msm:popw_forecast_temperature_c",))
LIGHTGBM_MSM_POPW_DAYTYPE: Preset  # LIGHTGBM_MSM_POPW.with_changes(name="lightgbm_msm_popw_daytype", add=("ftr_day_calendar:day_type",))
PRESETS: dict[str, Preset]       # by name, in that order
```

- [x] **Step 1: Failing tests** — `tests/test_demand_presets.py`:
```python
def test_the_four_presets_keep_the_old_feature_order():
    assert list(PRESETS) == ["lightgbm", "lightgbm_msm", "lightgbm_msm_popw", "lightgbm_msm_popw_daytype"]
    assert PRESETS["lightgbm"].feature_cols == ("time_code", "month", "day_of_week", "wavg_temperature_c", "lag_7d_demand_kwh")
    assert PRESETS["lightgbm_msm"].columns[-1] == "forecast_temperature_c"
    assert PRESETS["lightgbm_msm_popw"].columns == ("month", "day_of_week", "wavg_temperature_c", "lag_7d_demand_kwh", "popw_forecast_temperature_c")
    assert PRESETS["lightgbm_msm_popw_daytype"].columns[-1] == "day_type"

def test_types_and_categoricals_come_from_the_views():
    assert feature_dtypes(PRESETS["lightgbm_msm_popw_daytype"]) == {"month": "int64", "day_of_week": "int64", "wavg_temperature_c": "float64", "lag_7d_demand_kwh": "int64", "popw_forecast_temperature_c": "float64", "day_type": "int64"}
    assert categorical_columns(PRESETS["lightgbm_msm_popw_daytype"]) == ("day_type",)
    assert all(categorical_columns(p) == () for n, p in PRESETS.items() if n != "lightgbm_msm_popw_daytype")
```
  and in `tests/test_features_presets.py` the catalogue / store lists become the six names (`demand__lightgbm`, `demand__lightgbm_msm`, `demand__lightgbm_msm_popw`, `demand__lightgbm_msm_popw_daytype`, `spot_price__lightgbm`, `spot_price__lightgbm_occto`).
- [x] **Step 2: Run them** — `uv run pytest tests/test_demand_presets.py tests/test_features_presets.py -q --no-cov`; expect ImportError / list mismatch.
- [x] **Step 3: Implement** — `presets.py` as in Interfaces (module docstring: the demand task's presets; a comment that the order is the old classes' feature order); `catalogue.feature_services()` imports `tasks.demand.presets` inside the function like the spot one and returns the services of both.
- [x] **Step 4: Run them again** — pass.
- [x] **Step 5: Commit** — `feat(demand): the four demand presets in the feature catalogue`.

### Task 2: Extra columns on a preset eval set

The similar-day family's design matrix is the preset's columns plus the columns it adds in Python.

**Files:**
- Modify: `power_market_analytics/forecasting/preset_lgbm.py` (`preset_eval_set_cls`)
- Test: `tests/test_forecasting_preset_lgbm.py::TestEvalSet`

**Interfaces:**
```python
def preset_eval_set_cls(task: TaskSpec, preset: Preset, dtypes: dict[str, str], *, extra_dtypes: dict[str, str] | None = None) -> type[LightGbmEvalSetBase]
# feature_cols = (*preset.feature_cols, *extra_dtypes); schema and non_null_cols include the extras, in that order
```

- [x] **Step 1: Failing test**:
```python
def test_extra_columns_follow_the_presets(self):
    cls = preset_eval_set_cls(TASK, LIGHTGBM, DTYPES, extra_dtypes={"similar_day_demand_kwh": "float64", "half": "int64"})
    assert cls.feature_cols == (*LIGHTGBM.feature_cols, "similar_day_demand_kwh", "half")
    assert cls.schema["half"] == "int64" and cls.schema["similar_day_demand_kwh"] == "float64"
    assert cls.non_null_cols[-2:] == [TASK.actual_col, TASK.forecast_col] and "half" in cls.non_null_cols
```
- [x] **Step 2: Run** — fails on the unexpected keyword.
- [x] **Step 3: Implement** — build `feature_cols`, `schema` and `non_null_cols` from `(*preset.columns, *extra)`; docstring gains the parameter.
- [x] **Step 4: Run** — pass; the existing tests unchanged.
- [x] **Step 5: Commit** — `feat(forecasting): preset eval sets take extra columns`.

### Task 3: Registry, `build_strategy` and the script for the four presets

The four names go through Feast; the similar-day family is built as today (its classes still exist and still inherit from the old ones until Task 4).

**Files:**
- Modify: `power_market_analytics/tasks/demand/strategies/__init__.py`
- Modify: `scripts/demand_backtest.py`
- Modify: `tests/conftest.py` (`_write_feature_marts` gains `ftr_hour_jma_obs`, `ftr_hour_msm`, `ftr_period_actuals`; `feature_marts` docstring)
- Test: `tests/test_demand_strategies.py` (rewrite), `tests/test_demand_scripts.py` (the LightGBM tests take `feature_marts`; the deleted params; add/drop/name)

**Interfaces:**
```python
SIMILAR_DAY_STRATEGIES: dict[str, type[LightGbmMsmPopWeightedDayTypeSimilarDayStrategy]]  # the five, by name
STRATEGIES: tuple[str, ...] = (*PRESETS, *SIMILAR_DAY_STRATEGIES)
def build_strategy(name, *, area_code, days: pd.DatetimeIndex | None = None, train_start_date=None, add: Sequence[str] = (), drop: Sequence[str] = (), label: str | None = None, spark: SparkSession | None = None) -> ForecastStrategy
# preset name: with_changes when add/drop (label required), days required, open_store → historical_features(entity_frame(area_code, days, TASK.issue_offset), preset.features) → PresetLightGbmStrategy(TASK, preset, feature_frame(retrieved, preset.columns), dtypes=feature_dtypes(preset), categorical=categorical_columns(preset), name=label, train_start_date=…)
# similar-day name (this task): add/drop/label raise ValueError("… takes no feature changes until it is a preset"), built as today from its loaders
# unknown: KeyError
```
Script: `--add REF [REF …]`, `--drop REF [REF …]`, `--name`, `parser.error` when add/drop without name; `label = args.name or args.strategy` for the run name, tags, params and records; `first_day = max(demand.df["trade_date"].min(), start_date - pd.Timedelta(days=DEFAULT_TRAIN_WINDOW_DAYS + 1))`, `days=pd.date_range(first_day, end_date, freq="D")` passed to `build_strategy` (the spot script's wording).

Fixture marts (tokyo only, `CuratedWarehouse` data; `available_at` any instant before the 09:30 D-1 issue time):
- `ftr_hour_jma_obs`: for every day D of `DEMAND_DAYS` from the ninth on and hour 1..24, `wavg_temperature_c` = the weights-halving mean of `synthetic_temperature` over D-2..D-8 skipping `TEMPERATURE_MISSING_HOURS`, NaN when all seven are missing (a private helper in conftest, `_wavg_temperature(day, hour)`); `available_at = D - 1 day + 01:00`.
- `ftr_hour_msm`: every day but `FORECAST_MISSING_DAY`, hour 1..24: `forecast_temperature_c` = the representative station's value, `popw_forecast_temperature_c` = the two stations weighted by `STATION_POPULATION_WEIGHTS[2020]`, renormalised at `SECOND_STATION_MISSING_HOUR` (the single station's value); the humidity and rain columns the same way; `available_at = D - 2 days + 01:00` (the vintage's reference + 4 h).
- `ftr_period_actuals`: every `(day + 7, time_code)` from `warehouse.demand` with a non-null `demand_kwh`, `lag_7d_demand_kwh` as int; `available_at = day + 1 day + 05:00`.
Schemas: `area_code string, trade_date date, hour_ending int, wavg_temperature_c double, available_at timestamp`; `… hour_ending int, forecast_temperature_c double, popw_forecast_temperature_c double, popw_forecast_relative_humidity_pct double, popw_forecast_precipitation_mm double, available_at timestamp`; `… time_code int, lag_7d_demand_kwh bigint, available_at timestamp`.

- [x] **Step 1: Failing tests** — `tests/test_demand_strategies.py`: registry names (the nine, presets first); `build_strategy("lightgbm", area_code="tokyo", days=DAYS, train_start_date=…)` is a `PresetLightGbmStrategy` named `lightgbm` whose frame has `len(DAYS) * 48` rows with `wavg_temperature_c` equal to `_wavg_temperature` for one cell and `lag_7d_demand_kwh` equal to `synthetic_demand(day - 7, tc)`; `lightgbm_msm` carries `forecast_temperature_c` = `synthetic_forecast_temperature`, NaN on `FORECAST_MISSING_DAY`; `lightgbm_msm_popw_daytype` has `categorical_feature_cols == ("day_type",)` and `day_type` 2 on a `HOLIDAYS_2024_SPRING` day; a day after the hole has NaN lag at the hole's time codes; add/drop/label compose a named set; a preset needs `days`; changes need a label; a similar-day name rejects add/drop/label; the five similar-day builds keep today's assertions; unknown name → KeyError. `tests/test_demand_scripts.py`: the LightGBM tests take `feature_marts` and drop `temperature_lag_days`, `population_weight_census_year`, `day_type_levels`; `feature_preset` equals the strategy name; a test `--strategy lightgbm --add ftr_day_calendar:day_type --name lightgbm_daytype` publishes under `lightgbm_daytype` with `lgbm_categorical_feature_cols == "day_type"` and `feature_preset_base == "lightgbm"`; `--add` without `--name` is a `SystemExit` with the message.
- [x] **Step 2: Run** — `uv run pytest tests/test_demand_strategies.py tests/test_demand_scripts.py -q --no-cov`; fail.
- [x] **Step 3: Implement** — the fixture marts, the registry, the script.
- [x] **Step 4: Run** — pass.
- [x] **Step 5: Commit** — `feat(demand): the four presets run through Feast; --add, --drop and --name`.

### Task 4: The similar-day family on `PresetLightGbmStrategy`; delete the four classes

**Files:**
- Rewrite: `power_market_analytics/tasks/demand/strategies/lgbm.py` (only the family remains)
- Modify: `power_market_analytics/tasks/demand/strategies/__init__.py` (the family branch of `build_strategy`; add/drop/label accepted)
- Modify: `power_market_analytics/tasks/demand/features.py` (delete `TEMPERATURE_LAG_DAYS`, `TEMPERATURE_HALF_LIFE_DAYS`, `TEMPERATURE_FEATURE`, `FORECAST_TEMPERATURE_FEATURE`, `POPW_FORECAST_TEMPERATURE_FEATURE`, `DAY_TYPE_FEATURE`, `recency_weighted_temperature`, `join_forecast_temperature`, `join_day_type`; keep `hour_ending_of`, `DAY_TYPE_CODES`, `day_type_code`, the calendar column tuples, `join_day_calendar`)
- Modify: `power_market_analytics/tasks/demand/frames.py` (delete `AreaTemperature`, `AreaTemperatureForecast`, `DayTypeCalendar`, `DayCalendar.day_types`, `AreaWeatherForecast.temperature_forecast`)
- Modify: `power_market_analytics/tasks/demand/datasets.py` (delete `load_area_temperature`, `load_area_temperature_forecast`, `load_area_temperature_forecast_population_weighted`, `PopulationWeightedTemperatureForecast`, `load_day_types`)
- Modify: `power_market_analytics/forecasting/lgbm.py` (found while executing: the base's `_features`, which computed `month` and `day_of_week`, is unreachable once the demand classes are gone — it becomes the abstract hook, `_add_features` is the preset strategy's own and `CALENDAR_FEATURE_COLS` is deleted; `tests/test_forecasting_lgbm.py` follows)
- Modify: `power_market_analytics/features/presets.py` (found while executing: `with_changes` set `base` to the root preset, so a change from a derived preset logged the wrong `feature_preset_base`; it is now the preset the change started from)
- Test: `tests/test_demand_lgbm.py` (rewrite: the family's tests over a synthetic `FeatureFrame`), `tests/test_demand_features.py`, `tests/test_demand_frames.py`, `tests/test_demand_datasets.py` (drop the deleted units' tests), `tests/test_demand_strategies.py` (the family's build)

**Interfaces:**
```python
class LightGbmMsmPopWeightedDayTypeSimilarDayStrategy(PresetLightGbmStrategy):
    strategy_name: ClassVar[str] = "lightgbm_msm_popw_daytype_simday"
    #: The DayCalendar columns joined after the similar day's load; empty here, set by the calendar variants.
    calendar_feature_cols: ClassVar[tuple[str, ...]] = ()
    def __init__(self, preset: Preset, features: FeatureFrame, weather_forecast: AreaWeatherForecast, day_calendar: DayCalendar, weather_observed: AreaObservedWeather, hourly_load: AreaHourlyLoad, *, dtypes: dict[str, str], categorical: Sequence[str] = (), census_year: int, window_half_width_days: int = SIMILAR_DAY_WINDOW_HALF_WIDTH_DAYS, name: str | None = None, **kwargs) -> None
    # super().__init__(TASK, preset, features, dtypes=dtypes, categorical=categorical, name=name or self.strategy_name, **kwargs)
    # self.feature_cols = (*preset.feature_cols, SIMILAR_DAY_FEATURE, *self.calendar_feature_cols)
    # self.eval_set_cls = preset_eval_set_cls(TASK, preset, dtypes, extra_dtypes={SIMILAR_DAY_FEATURE: "float64", **{c: DAY_CALENDAR_FEATURE_DTYPES[c] for c in self.calendar_feature_cols}})
    # self.census_year, self.hourly_load, self.day_calendar, self.selector, self._selections as today
    def _add_features(self, featured, history):  # super()._add_features (the frame merge), join_similar_day_load, then join_day_calendar(cols=self.calendar_feature_cols) when any
    def _extra_params(self):  # {**super()._extra_params(), "population_weight_census_year": self.census_year, the similar-day params as today}
    # predict and diagnostics unchanged
class LightGbmMsmPopWeightedDayTypeSimilarDayCalendarStrategy(…SimilarDayStrategy): strategy_name = "…_calendar"; calendar_feature_cols = DAY_CALENDAR_FEATURE_COLS
class …HolidayDegreeStrategy: "…_holidaydegree"; HOLIDAY_DEGREE_FEATURE_COLS
class …HolidayDistanceStrategy: "…_holidaydistance"; HOLIDAY_DISTANCE_FEATURE_COLS
class …CalendarCountStrategy: "…_calendarcounts"; CALENDAR_COUNT_FEATURE_COLS
```
`build_strategy` for a similar-day name: `preset = LIGHTGBM_MSM_POPW_DAYTYPE` (with_changes when add/drop, label required), the same retrieval as a preset, then `cls(preset, frame, weather.forecast, load_day_calendar(spark=spark), observed.weather, load_area_hourly_load(area_code, spark=spark), dtypes=…, categorical=…, census_year=weather.census_year, name=label, train_start_date=…)` with the weather and observed loaders as today.

- [x] **Step 1: Failing tests** — `tests/test_demand_lgbm.py` rewritten: `sim_inputs` gains a `FeatureFrame` of the `lightgbm_msm_popw_daytype` columns over `SIM_DEMAND_DAYS` built from the module's synthetic series (`expected_wavg`, `forecast_temperature_at`, `day_type_at`, the D-7 lag of `demand_at`); `make_sim_strategy(inputs, cls=…)` calls the new constructor; class-attribute tests assert `strategy_name`, `feature_cols` (preset's + `similar_day_demand_kwh` + the calendar subset), `categorical_feature_cols == ("day_type",)`, `lookback_days == 0`, the eval set's schema; the predict / backtest / evaluate / diagnostics tests port unchanged in intent (the first predict fits the selector, weights fitted once, a day without pairs, a day outside the calendar, eval set and contributions carry the feature, the selector params logged, `population_weight_census_year` logged, the calendar variants add exactly their columns). `tests/test_demand_strategies.py`: the family builds carry `feature_cols`, `categorical_feature_cols`, `census_year`, `hourly_load`, the selector and the calendar; add/drop/label on a family name compose a named set. Trim the other three test files to the surviving units.
- [x] **Step 2: Run** — `uv run pytest tests/test_demand_lgbm.py tests/test_demand_strategies.py tests/test_demand_features.py tests/test_demand_frames.py tests/test_demand_datasets.py -q --no-cov`; fail.
- [x] **Step 3: Implement** — the rewrite and the deletions.
- [x] **Step 4: Run** — pass; then `just test`, `just lint`, `just mypy`.
- [x] **Step 5: Commit** — `feat(demand): the similar-day family reads its base features from Feast; delete the four strategy classes`.

### Task 5: Reproduction, docs, PR

- [x] **Step 1: Old runs** — `lightgbm_msm_popw_daytype` run `6d276210f40a4014b8141d81ae1c9306` (MAE 594,902 kWh), `lightgbm_msm_popw_daytype_simday` run `b54de31519864bcf86fcde6651314830` (MAE 583,683 kWh); 729 days, 34,954 periods, one day skipped.
- [x] **Step 2: New runs** — `f6729577324b44898115b7097635bffa` (MAE 595,721 kWh) and `b36041eccfa44e3eb5f4446044ff0a95` (MAE 586,886 kWh); the same 729 days, 34,954 periods and skipped day.
- [x] **Step 3: Compare** — Every feature value of the scored periods matches: exactly for `month`, `day_of_week`, `lag_7d_demand_kwh`, `popw_forecast_temperature_c`, `day_type` and `similar_day_demand_kwh`, to 1.4e-14 for `wavg_temperature_c`. The forecasts do not: they differ on every period (max 1,549,389 kWh and 742,646 kWh), MAE +0.14 % and +0.55 %. Over the whole training span (2023-01-01..2026-08-17, 63,562 common rows; the 38 hole periods of 2025-06-14 are absent in the old matrix and null in the new one, so both drop them) the features match the same way. The first refit's 28,512 training rows are the same rows; LightGBM fitted on the old and the new matrix predicts 2024-08-18 up to 347,197 kWh apart; with `wavg_temperature_c` and `popw_forecast_temperature_c` rounded to 12 decimals still 92,599 kWh apart, rounded to 9 decimals identical to the digit. The cause is LightGBM's histogram binning moving on the last-bit differences of the weighted mean summed in a different order, not a feature definition. Recorded as an open question in the spec (§13): round the weighted marts in dbt.
- [x] **Step 4: Docs** — done with the code commits and this entry.
- [x] **Step 5: PR** — PR #65, `feat(demand): presets over Feast replace the four LightGBM strategy classes`; one Codex finding (a calendar variant given `--add` of a column it joins itself) fixed in b1985dd; 👍 on the second round.
