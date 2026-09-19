# Presets as YAML files — design

Date: 2026-09-19. Status: **approved** on 2026-09-19; implemented on branch `feature/yaml-presets`
(plan `docs/superpowers/plans/2026-09-19-yaml-presets.md`).

## 1. Goal

Move every preset, the named feature list a LightGBM strategy runs on, out of
Python and into one YAML file each under `conf/presets/<task>/<name>.yaml`. The
researcher's reason (2026-09-19): a YAML file is easier to read and to reference
from an experiment issue than a Python constant. After this change:

1. **A preset is a file.** Its name is the file's stem, the strategy label its
   runs are published under.
2. **A file is a full feature list, or a base preset plus what it adds and
   drops.** That is the shape `Preset.with_changes` gives today, so the
   `feature_preset_base` param keeps its meaning.
3. **Everything downstream is unchanged:** `build_strategy`, `--add` / `--drop`
   / `--name`, the MLflow params, the Feast feature services, the warehouse
   `strategy` column, the dashboards.
4. **Every current preset resolves to exactly today's tuple**, pinned by a
   test, so no published run's feature set moves.

Out of scope: the research-on-issues change (its own spec), a layer of named
feature families, the `--strategy` default, deleting the rejected reference
presets.

## 2. Decisions

1. **One kind of file.** ChatGPT's proposal (share `6aadd169`, 2026-09-16) had a
   "model config" for the accepted set and an "experiment config" for a delta
   under trial. Here they are the same file: a preset with `base` and `add` /
   `drop` is what an experiment runs, and if the experiment is kept, that file
   becomes the baseline by being named as such in the task README and, when it
   runs for every area, as the script default. No `experiments/` directory. What
   separates an accepted preset from a trial is the experiment issue's decision,
   not the file's shape.
2. **The word stays "preset".** The class, the `feature_preset` and
   `feature_preset_base` params on every run since 2026-09-11, the Feast service
   tag and CLAUDE.md all say preset. One name, one directory: `conf/presets/`.
   "Model config" in the researcher's message of 2026-09-19 means this file.
3. **Flat, ordered `view:column` references**, not a mapping by view. Feature
   order is part of a preset: LightGBM walks features in column order and breaks
   gain ties by position, and today's presets interleave views
   (`ftr_day_calendar:day_type` comes after
   `ftr_hour_msm:popw_forecast_temperature_c`). A mapping keyed by view cannot
   say that order. The flat string is also the form `--add`, the `feature_refs`
   param and `fct_feature_value.feature_ref` already use.
4. **The name is the file stem.** No `name` key: two places for one name would
   drift. `description` is required.
5. **A published preset's file is never edited.** A change to its features is
   a new file. The pin test (§6) enforces it for the thirteen migrated presets;
   for later ones it is the rule, like a feature column never being renamed.
6. **Families are not files.** The Python tuples (`RECENT_LOAD_FEATURES`,
   `DAY_CALENDAR_FEATURES`, …) go. A preset that adds a family lists the
   references, and two presets that share a family repeat the list. Which
   family a feature belongs to is a Project field on its issue (the
   research-on-issues spec), and an experiment issue names its batch.
   *Superseded on 2026-09-19 (PR #185): the Project field was removed; a
   feature's family belongs on its mart column, in the repository.*
7. **The task `presets.py` modules go.** The registry is the directory.
   `features/catalogue.py` stops importing the tasks, so its import-cycle
   workaround goes with it.
8. **Every current preset migrates**, the rejected R-005 references included.
   Deleting one is a separate decision.

## 3. The file

```
conf/presets/
├── demand/
│   ├── lightgbm.yaml
│   ├── lightgbm_msm.yaml
│   ├── …                                   (eleven files)
│   └── lightgbm_msm_popw_daytype_simday_lags_weather.yaml
└── spot_price/
    ├── lightgbm.yaml
    └── lightgbm_occto.yaml
```

A preset from scratch:

```yaml
# conf/presets/demand/lightgbm.yaml
description: >-
  Calendar, the recency-weighted same-hour temperature over D-8..D-2 at the
  representative station and the D-7 demand lag.
features:
  - ftr_day_calendar:month
  - ftr_day_calendar:day_of_week
  - ftr_hour_jma_obs:wavg_temperature_c
  - ftr_period_actuals:lag_7d_demand_kwh
```

A preset changed from another:

```yaml
# conf/presets/demand/lightgbm_msm_popw_daytype_simday_lags_weather.yaml
description: >-
  Plus the three other MSM forecast elements, population-weighted like the
  forecast temperature the base already carries (demand/R-007 E-001).
base: lightgbm_msm_popw_daytype_simday_lags
add:
  - ftr_hour_msm:popw_forecast_relative_humidity_pct
  - ftr_hour_msm:popw_forecast_precipitation_mm
  - ftr_hour_msm:popw_forecast_solar_radiation_mjm2
```

| Key | Required | Meaning |
|---|---|---|
| `description` | yes | One to three sentences: what the preset is and the investigation or experiment it came from. Becomes the Feast service's description. |
| `features` | one of the two | The full list, in feature order. |
| `base` | one of the two | Another preset of the same task, by name. |
| `add` | with `base` | References appended after the base's, in order. |
| `drop` | with `base` | References removed from the base's. |

Rules, each a `ValueError` naming the file:

- an unknown key, or a `description` that is empty;
- neither or both of `features` and `base`;
- `add` or `drop` without `base`; `base` with neither `add` nor `drop`, or both
  empty;
- a `base` that is not a file of the task, or a chain that loops;
- a reference that is not a `view:column` string;
- today's `Preset` rules: a duplicate column, or `time_code` (every preset's
  first feature already);
- today's `with_changes` rules: dropping a reference the base lacks, adding one
  it has.

A reference that names an unknown view or column is not the loader's to catch:
`feature_dtypes` raises on it when the strategy is built, as today, and a test
(§6) runs that check over every file.

**The name** is the stem: lowercase `a-z0-9_`. It is the `strategy` column of
the forecast facts, the MLflow `strategy` tag and `feature_preset` param, and
the Feast service `<task>__<name>`. Since 2026-09-19 (the researcher's decision)
a new preset is named after the experiment issue that tests it, `e<issue number>`,
with an optional short slug for the batch under test: `e185`, `e185_recent_load`.
The number points at the hypothesis, the diff, the runs and the decision, and
makes the name unique; the slug names the batch, never the ancestry, which is the
file's `base`, and never the algorithm, which is the same for every preset. No
task prefix (the directory is the task) and no baseline alias file (the task README
names the baseline). The thirteen presets of 2026-09-19 keep their chain names
(`lightgbm_msm_popw_daytype_simday_lags`), because their runs carry them.

## 4. Loading

`power_market_analytics/features/presets.py` gains:

- `PRESETS_DIR`: `<repo>/conf/presets`, resolved from the package like
  `store.FEATURE_STORE_DIR`.
- `load_presets(task, presets_dir=PRESETS_DIR) -> dict[str, Preset]`: reads
  every `*.yaml` under `presets_dir/task`, resolves each `base` through the
  other files (a visited set catches a loop), and returns the presets by name,
  sorted by name (which is today's registration order for all thirteen). A `features` file becomes `Preset(task, name, features,
  description)`; a `base` file becomes
  `base_preset.with_changes(add=…, drop=…, name=…)` with the file's
  description, so today's validation runs unchanged and `Preset.base` is the
  base's name as before.
- `Preset.description`, a string, empty by default so a preset built in code
  (a test, a `--add` run) needs none. `with_changes` gives the copy an empty
  description: a run changed on the command line is not the base's preset.

The registries follow the directory:

- `tasks/demand/strategies/__init__.py` and `tasks/spot_price/strategies/__init__.py`
  set `PRESETS = load_presets(TASK.name)` at import, since `STRATEGIES` feeds
  the scripts' `--strategy` choices. `tasks/demand/presets.py` and
  `tasks/spot_price/presets.py` are deleted.
- `features/catalogue.feature_services()` registers the services of
  `load_presets(task)` for every directory under `PRESETS_DIR`, sorted. The
  service is named and tagged as today; its description is the file's.

Thirteen small files load once per import. No cache.

## 5. What does not change

`build_strategy` apart from its import; `PresetLightGbmStrategy`; the scripts'
flags and the `--strategy` default; the MLflow params (`feature_preset` = the
stem, `feature_preset_base` = the base's stem or `none`, `feature_refs`,
`lgbm_feature_cols`, `lgbm_categorical_feature_cols`); the Feast service names,
so `just feast-ui` lists the same thirteen; the `pma_ml` tables, the dbt models,
the dashboards and the compare scripts. `categorical_columns`, `feature_dtypes`
and `feature_expressions` keep reading the views: a preset file says nothing
about a feature's type or categorical status, because the mart declares them.

## 6. Tests and proof

- **The loader**, on a temporary directory: both forms, a two-step base chain,
  every rule of §3 as a parametrised case naming the file, the sorted order, the
  description on the service, and that the preset directories are exactly the
  task names.
- **The migration pin.** A frozen copy of today's thirteen presets (name, base,
  features) in the tests, asserted equal to `load_presets("demand")` and
  `load_presets("spot_price")`. This is the proof for the PR: LightGBM sees the
  same columns in the same order, so a rerun of any preset reproduces its runs to
  the digit, the CLAUDE.md rule that a rebuild must not move a model. No backtest
  is run for this change.
- **Every file resolves against the views**: `feature_dtypes` over every preset
  of every task, so a typo in a file fails `just test` and CI rather than the
  first backtest.
- Coverage stays at 100 %; `just lint` and `just mypy` clean.

## 7. Docs

CLAUDE.md: the spot and demand backtest bullets (a strategy is a preset
"(`tasks/spot_price/presets.py` …)", "a new preset = an entry in `PRESETS`",
"the eleven presets of `tasks/demand/presets.py`"), the Demand task bullet's
tuple names (`DAY_CALENDAR_FEATURES`, `HOLIDAY_DEGREE_FEATURES`,
`HOLIDAY_DISTANCE_FEATURES`, `CALENDAR_COUNT_FEATURES`, `RECENT_LOAD_FEATURES`,
`MSM_ELEMENT_FEATURES`) and the Architecture bullet on tasks. Each becomes "a
file under `conf/presets/<task>/`". The task READMEs name presets by name, not
by module, and need no change here; the demand baseline moves to
`lightgbm_msm_popw_daytype_simday_lags_weather` in the research-on-issues PR,
with the three decisions it records.

## 8. Rollout

One PR on `feature/yaml-presets`: the thirteen files, the loader, the two
deleted modules, the tests, the docs. No warehouse or registry migration: the
next `open_store()` re-applies the services with their new descriptions. The
first use is the fresh baseline run of
`lightgbm_msm_popw_daytype_simday_lags_weather`, which the first experiment
issue references by its file.

## 9. Alternatives not taken

- **`conf/models/` and "model config".** Two names for one thing (decision 2).
- **A mapping by view**, ChatGPT's form. It loses feature order (decision 3).
- **A `families.yaml` of named lists a preset includes by name.** One more
  file to read before a preset says what it runs on. It can come later if the
  repetition of decision 6 becomes a burden.
- **A `name` key in the file.** Drift (decision 4).
- **Keeping `tasks/<task>/presets.py` as one line.** Nothing left to say there.
