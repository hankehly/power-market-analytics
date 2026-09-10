# Spot-price presets — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One LightGBM strategy for the spot-price task, parametrised by a preset (a named list of feature references), fed by Feast retrieval; the two spot LightGBM classes and their eval sets go away, and `--add` / `--drop` / `--name` compose ad hoc feature sets on the command line.

**Architecture:** A `Preset` names features as `<view>:<column>` references; `feature_dtypes` reads their types off the generated views. `build_strategy` builds the entity frame for the run's days, retrieves the preset's features once through Feast, wraps them in a `FeatureFrame` (grain trade_date × time_code, float64 columns, NaN = unavailable) and hands it to `PresetLightGbmStrategy`, a `SlidingWindowLightGbmStrategy` whose feature path is a merge with that frame instead of pandas builders. Everything else in the base (window, refit cadence, SHAP, importance, evaluation) is unchanged. The demand strategies keep the old hooks until PR 6.

**Tech Stack:** feast 0.66 (FeatureService), pandas, LightGBM, the existing `forecasting` framework.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §6 (presets), §7 (the strategy), §11 PR 5.

## Global Constraints

- Feature column names in the design matrix are the Feast feature names (`month`, `lag_1d_price`, …), the same names as today, so SHAP components, the contribution fact and the dashboards keep their labels.
- `time_code` is always the first feature of a preset strategy, as `CALENDAR_FEATURE_COLS` made it today.
- Reproduction: the new `lightgbm_occto` and `lightgbm` runs over 2025-04-01 to 2026-08-16 with `--train-start 2024-04-01` must match the old code's runs made on the same warehouse: feature values exact (the contribution fact's `feature_value`), forecasts within 1e-6.
- The Spark session for retrieval is UTC (PR 4's rule); the local tests switch the fixture session to UTC while the feature marts exist.
- Coverage 100 %; `just lint`, `just mypy` clean.
- Branch `feature/spot-price-presets` (worktree `.claude/worktrees/spot-price-presets`); commit type `feat(spot-price)` / `feat(features)` / `feat(forecasting)`; PR labels `enhancement` + `documentation`.

## Presets

| Preset | Features (`view:column`) | Categorical |
|---|---|---|
| `lightgbm` | `ftr_day_calendar:month`, `ftr_day_calendar:day_of_week`, `ftr_period_jepx:lag_1d_price` | none |
| `lightgbm_occto` | the above + `ftr_day_occto:max_demand_hour_ending`, `ftr_day_occto:max_demand_mw`, `ftr_day_occto:max_supply_capacity_mw` | none |

`previous_day` stays a strategy without features.

---

### Task 1: `Preset` and feature services

**Files:**
- Create: `power_market_analytics/features/presets.py`
- Modify: `power_market_analytics/features/store.py` (services in the default definitions and in the reconciliation)
- Test: `tests/test_features_presets.py`

**Interfaces:**
```python
@dataclasses.dataclass(frozen=True)
class Preset:
    task: str                      # "spot_price"
    name: str                      # the strategy label, e.g. "lightgbm_occto"
    features: tuple[str, ...]      # "view:column" references, in feature order
    categorical: tuple[str, ...] = ()   # column names among features
    @property
    def columns(self) -> tuple[str, ...]      # the column names, in order
    @property
    def feature_cols(self) -> tuple[str, ...] # ("time_code", *columns)
    def with_changes(self, *, add=(), drop=(), name: str) -> Preset
def feature_column(ref: str) -> str           # "view:column" -> "column"
def feature_dtypes(preset: Preset) -> dict[str, str]   # column -> pandas dtype off the views (Int64 -> int64, Float64 -> float64; else ValueError)
def feature_service(preset: Preset) -> feast.FeatureService  # name f"{task}__{name}", one projection per view
```
`Preset.__post_init__` rejects a duplicate column, a categorical that is not a feature, and a reference without a colon. `with_changes` rejects dropping an absent reference and adding a present one. `store.open_store()` applies `ENTITIES + VIEWS + feature_services()` by default, where `features/catalogue.py` collects the services of every task's presets (importing `tasks.spot_price.presets` inside the function to keep the package layering one-way), and deletes stale services like stale views.

- [ ] Steps: failing tests for the dataclass rules, `feature_dtypes` on the real views (`lag_1d_price` float64, `month` int64, an unknown reference, a `String` feature rejected), `feature_service` shape, `open_store` listing the services and dropping a stale one; implement; commit `feat(features): presets and feature services`.

### Task 2: `FeatureFrame`

**Files:**
- Create: `power_market_analytics/features/frame.py`
- Test: `tests/test_features_frame.py`

**Interfaces:**
```python
class FeatureFrame(DomainFrame):         # grain (trade_date, time_code); subclasses per preset
def feature_frame_class(columns: Sequence[str]) -> type[FeatureFrame]   # schema GRAIN + {col: float64}
def feature_frame(retrieved: pd.DataFrame, columns: Sequence[str]) -> FeatureFrame  # from historical_features' output: keeps trade_date, time_code, the columns cast to float64
```

- [ ] Steps: failing tests (NaN kept, ints cast, a missing column rejected, duplicate grain rejected); implement; commit `feat(features): FeatureFrame`.

### Task 3: `PresetLightGbmStrategy`

**Files:**
- Create: `power_market_analytics/forecasting/preset_lgbm.py`
- Modify: `power_market_analytics/forecasting/strategy.py` (`name`, `task` become plain attributes), `power_market_analytics/forecasting/lgbm.py` (`feature_cols`, `eval_set_cls`, `lookback_days`, `categorical_feature_cols` become plain attributes; `DEFAULT_TRAIN_WINDOW_DAYS = 730`)
- Test: `tests/test_forecasting_preset_lgbm.py` (the behaviour tests of `tests/test_spot_price_lgbm.py` ported to a synthetic `FeatureFrame`: predict, unavailable feature, refit cadence, window bounds, eval set, contributions, importance, `_extra_params`)

**Interfaces:**
```python
class PresetLightGbmStrategy(SlidingWindowLightGbmStrategy):
    def __init__(self, task: TaskSpec, preset: Preset, features: FeatureFrame, *, dtypes: dict[str, str], name: str | None = None, train_window_days=DEFAULT_TRAIN_WINDOW_DAYS, refit_every_days=7, train_start_date=None)
    # sets self.task, self.name (= name or preset.name), self.preset, self.feature_cols, self.categorical_feature_cols, self.lookback_days = 0, self.eval_set_cls = preset_eval_set_cls(task, preset, dtypes)
    def _features(self, points, history): points.merge(self._features_df, how="left", on=GRAIN_COLS, validate="one_to_one")
    def _add_features(self, featured, history): return featured   # the base's hook, unused
    def _extra_params(self): {"feature_preset": preset.name, "feature_refs": ",".join(preset.features)}
def preset_eval_set_cls(task: TaskSpec, preset: Preset, dtypes: dict[str, str]) -> type[LightGbmEvalSetBase]
```

- [ ] Steps: failing tests; implement; commit `feat(forecasting): PresetLightGbmStrategy over a FeatureFrame`.

### Task 4: Spot-price presets, registry and script

**Files:**
- Create: `power_market_analytics/tasks/spot_price/presets.py` (`PRESETS`)
- Modify: `power_market_analytics/tasks/spot_price/strategies/__init__.py`: `STRATEGIES: tuple[str, ...] = ("previous_day", *PRESETS)`; `build_strategy(name, *, area_code, days: pd.DatetimeIndex | None = None, train_start_date=None, spark=None, add=(), drop=(), label=None)` — `previous_day` as before (rejects `train_start_date`, `add`, `drop`); a preset name: `preset = PRESETS[name]` then `with_changes` when `add`/`drop`/`label` given (a label is required with `add` or `drop`), `store = open_store()`, `frame = entity_frame(area_code, days, TASK.issue_offset)`, `retrieved = historical_features(store, frame, preset.features)`, `PresetLightGbmStrategy(TASK, preset, feature_frame(retrieved, preset.columns), dtypes=feature_dtypes(preset), name=label, train_start_date=...)`; `days` is required for a preset.
- Delete: `power_market_analytics/tasks/spot_price/strategies/lgbm.py`, `tests/test_spot_price_lgbm.py`
- Modify: `scripts/spot_price_backtest.py`: `--add REF [REF …]`, `--drop REF [REF …]`, `--name`; `parser.error` when add/drop without name or with `previous_day`; `label = args.name or args.strategy` used for the run name, tags, params, records; `days = pd.date_range(max(prices.first, start_date - (DEFAULT_TRAIN_WINDOW_DAYS + 1) days), end_date)` passed to `build_strategy`.
- Modify: `tests/conftest.py`: `feature_marts` fixture — switches the session to UTC for the test, creates `pma_features.ftr_day_calendar` (13 feature columns from the synthetic calendar helpers, every day of `CALENDAR_DAYS`, areas tokyo and kansai, `available_at` 1900-01-01), `ftr_day_occto` (`OCCTO_DAYS`, `available_at` D−2 18:00) and `ftr_period_jepx` (`PRICE_DAYS` shifted by one day, `available_at` D−2 12:00) once per session, restores the zone after.
- Modify: `tests/test_spot_price_strategies.py` (registry names; `build_strategy` for a preset through the marts: the frame's columns and values, `with_changes` through `add`/`drop`/`label`, `previous_day` rejects them), `tests/test_spot_price_scripts.py` (LightGBM tests take `feature_marts`; a test of `--add`/`--drop` with `--name`, one of the missing `--name`).

- [ ] Steps: failing tests; implement; `just test`; commit `feat(spot-price): presets over Feast replace the LightGBM strategy classes`.

### Task 5: Reproduction, docs, PR

- [ ] **Step 1: Reproduction** — old code on `main` ran `lightgbm_occto` and `lightgbm` over 2025-04-01..2026-08-16 (`--train-start 2024-04-01`, `--importance-repeats 1`) in the devcontainer; run the same two with the new code from the worktree; compare per run pair via the warehouse: `max(abs(new.forecast - old.forecast))` over the joined periods and `max(abs(new.feature_value - old.feature_value))` per component from the contribution table; row counts equal.
- [ ] **Step 2: Docs** — CLAUDE.md (the spot backtest command: presets, `--add/--drop/--name`; the architecture bullets naming `LightGbmOcctoStrategy` / `_join_daily_features`; the Feast bullet's "nothing reads it"); spec §11 PR 5 done; this plan.
- [ ] **Step 3: PR** — `feat(spot-price): presets over Feast replace the LightGBM strategy classes`, Why / What / Proof, labels, assignee; Codex loop.
