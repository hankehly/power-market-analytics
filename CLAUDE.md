# CLAUDE.md

## Commands

- `just refresh-all` — every source in one go: download + reload `raw` for JEPX (+ the holidays
  seed), JMA hourly (+ the station seed), OCCTO, TEPCO (both datasets), Kansai (both datasets),
  e-Stat and MSM,
  in that order (JMA before MSM: the MSM downloader reads the station seed), each script with
  its defaults, then a single `dbt build` (models + tests). Warm caches make it ~1.5 h,
  dominated by JMA re-fetching every station's current-year file; a failing step aborts before
  the build. It is the only refresh recipe — the per-source `refresh-<source>` / `ingest-<source>`
  recipes were dropped on 2026-08-31.
- A single source is refreshed by running its download + load scripts (all under `scripts/`)
  through `just python`, then `just dbt build`:
  - JEPX: `download_jepx_spot.py`, `update_holidays_seed.py`, `load_jepx_spot.py`.
  - JMA hourly: `update_jma_stations_seed.py` (~5 min, staffed stations inside JEPX areas only),
    `download_jma_hourly_all.py` (stitched 7-element hourly CSVs; e.g. `--prefecture 44`; no
    args = all ~149 staffed stations, ~13.5 h cold; `--request-interval 3` is proven — the
    2026-08-20 full backfill, ~3,100 requests, zero 429s, ~6 h — while 2 s spacing draws 429s),
    `load_jma_hourly.py`.
  - OCCTO 翌々日, two datasets — the demand-forecast CSV (~700 KB, 3 HTTP calls) and the
    half-hourly area reserve-rate CSV (~20 MB/yr, fetched in 300-day windows because the portal
    caps a download at 150,000 rows): `download_occto_demand_forecast.py`,
    `download_occto_area_reserve_rate.py`, `load_occto_demand_forecast.py`,
    `load_occto_area_reserve_rate.py`.
  - TEPCO, two datasets — the Tokyo-area demand/generation actuals
    (`download_tepco_area_demand_generation.py` redownloads every monthly `AREA_YYYYMM.zip`,
    2022-04 → now, ~5 MB total; `load_tepco_area_demand_generation.py`) and でんき予報 hourly
    電力使用実績 (`download_tepco_power_usage.py` fetches the yearly `juyo-YYYY.csv` files 2016 …
    2022, cached — `--force-yearly` refetches them — and redownloads every monthly
    `YYYYMM_power_usage.zip`, 2022-04 → now, ~4 MB; `load_tepco_power_usage.py`).
  - Kansai, two datasets — the same as TEPCO's actuals for 関西電力送配電 (`YYYYMM_jisseki.zip`,
    2022-04 → now, ~2 MB total): `download_kansai_area_demand_generation.py`,
    `load_kansai_area_demand_generation.py`; and でんき予報 hourly 電力使用実績
    (`download_kansai_power_usage.py` redownloads every monthly `YYYYMM_jisseki.zip` under
    `…/yamasou/`, 2016-04 → now, ~8.5 MB, 126 zips; `load_kansai_power_usage.py`, ~3,800 daily
    files in seconds).
  - e-Stat: `download_estat_census_population_mesh.py` downloads every configured census vintage
    (2015 `T000847`, 2020 `T001101` JGD2000; 151 primary-mesh zips each, cached — `--years 2020`,
    `--force`; a cold run is ~50 min because e-Stat generates each archive in ~10 s),
    `load_estat_census_population_mesh.py`.
  - MSM: `download_jma_msm_surface_forecast.py` downloads + decodes, for each delivery day D, the
    three RISH GRIB2 files covering the 12 UTC D-2 run (`--start-date`, `--force`, `--keep-grib`;
    ~157 MB per delivery day, ~54 GiB/yr), `load_jma_msm_surface_forecast.py`. `--start-date`
    defaults to 2022-04-01, but the warehouse holds 2019-04-01 → since the 2026-09-05 backfill;
    2019-04-01 is the earliest the downloader accepts (the archive's `FH40-51` member starts
    with the 2019-03-05 12 UTC run), so pass it on a fresh clone. Both need a
    devcontainer image rebuild (`docker compose build devcontainer`) for the eccodes dependency
    before they can run in-container — `power_market_analytics/msm.py` imports eccodes at module
    level; see [docs/JMA-MSM-GPV-Retrieval.md](docs/JMA-MSM-GPV-Retrieval.md) §8.
- `just test [pytest args]` — Python unit tests (host-side pytest, ~1 min) with a `pytest-cov`
  term-missing report over `power_market_analytics/` + `scripts/` (config in `pyproject.toml`
  `[tool.coverage.*]`; gated at 100% via `fail_under`, so a partial suite fails locally and in
  CI — `.github/workflows/ci.yml` runs the same command on every push). Shared fixtures in
  `tests/conftest.py`: `spark` (local session, temp warehouse, no metastore),
  `curated_warehouse` (synthetic `pma_curated` star for both tasks), `feature_marts` (the seven
  `pma_features` marts from it under a UTC session — the similar-day mart picks D − 364 —
  with the Feast store in a temp registry; every test that builds a preset strategy takes
  it), an autouse temp MLflow file store, and
  a session-wide single-thread LightGBM cap. HTTP is never real: the
  downloaders take an injectable `session` (`session_factory` for OCCTO) and scripts are driven
  through `main(argv)` with their downloader/loader class swapped in the module namespace
  (`tests/support.import_script`).
- `just lint [ruff args]` — `uv run ruff check .` (rules in `pyproject.toml` `[tool.ruff]`;
  extra args append, e.g. `just lint --fix`). The `ci` workflow runs the same check as a
  `lint` job on every push (dev dependency group only, no PySpark install).
- `just mypy [mypy args]` — `uv run mypy` over `power_market_analytics/` + `scripts/` +
  `tests/` (config in `pyproject.toml` `[tool.mypy]`; untyped-function bodies are not
  checked, and plotly/shap imports are ignored for lack of stubs). Also a `ci` job on every
  push (full `uv sync` — mypy resolves types against PySpark/MLflow and the `pandas-stubs` /
  `types-PyYAML` dev dependencies).
- `just checkov [checkov args]` — checkov scan (Dockerfiles, GitHub Actions workflows, secrets
  in any committed file; config in `.checkov.yaml`, version pinned in the justfile and
  `.github/workflows/ci.yml`). Exits 1 on any failed check; the `ci` workflow runs it as a
  second job on every push.
- `just python <args>` / `just exec <cmd>` / `just shell` — run inside the devcontainer.
- `just dbt <args>` — dbt from `/workspace/dbt` (e.g. `just dbt build`, `just dbt show --inline "select ..." --limit 5`).
  `just dbt parse` needs no warehouse (parse never opens a connection) and is the one dbt step
  the `ci` workflow runs — a `dbt parse` job on every push: full locked `uv sync`, `dbt deps`
  (versions from `dbt/package-lock.yml`), then `dbt parse` from `dbt/`, ~8 s after the
  install. It catches model/source YAML, enforced-contract, ref/source, Jinja and
  test-argument errors; the data tests (`dbt build`) still need the thriftserver and do not
  run in CI.
- `just sql` — beeline shell on the thriftserver.
- `just feature-views` — regenerate `power_market_analytics/features/views.py`, the Feast feature
  views of the dbt feature marts, from the manifest (host-side `dbt parse`, then
  `scripts/generate_feature_views.py`); run it after a mart or its tags change. The `dbt parse`
  CI job runs the generator with `--check` and fails on a stale file. The file is generated
  output: never edit it, and it is excluded from `ruff format`.
- `just feast-ui` — serve the Feast UI, the browsable feature catalogue (feature views with
  their fields, descriptions and tags, entities, data sources), host-side on
  http://localhost:8888 after refreshing `data/feast/registry.db` from the package; Ctrl-C
  stops it. `just open feast` opens it. Needs the `grpcio` extra of `feast` (in the
  dependency since 2026-09-10: `feast[spark,grpcio]`) — without it `feast ui` dies on
  `import grpc`. The UI reads the registry at start: restart it after a mart changes.
- `just python scripts/spot_price_backtest.py --strategy lightgbm --area tokyo` — day-ahead
  backtest (strategies: `previous_day`, and the presets `lightgbm`, `lightgbm_occto`; areas =
  `dim_area.area_code`). Since 2026-09-11 a LightGBM strategy is a **preset**
  (`tasks/spot_price/presets.py`: a named list of `<view>:<column>` references into the
  Feast feature views; a feature is categorical when its view field carries the mart's
  `categorical` tag — `features.presets.categorical_columns`, so an added feature is
  treated as its mart declares it); `build_strategy` retrieves the
  preset's features once for the run's days through Feast (`features/retrieval.py`, each
  row as of its own 09:30 D-1 issue time) and builds a `PresetLightGbmStrategy`
  (`forecasting/preset_lgbm.py`) over that `FeatureFrame`; `time_code` is always the first
  feature. `--add VIEW:COLUMN …` / `--drop VIEW:COLUMN …` change the list for one run and
  need `--name`, which becomes the run's strategy label (`strategy` column, MLflow tag;
  params `feature_preset`, `feature_preset_base`, `feature_refs`, `lgbm_feature_cols`). A
  new preset = an entry in `PRESETS`; new features come from the marts (`just feature-views`
  after a mart changes). The spot `LightGbmStrategy` / `LightGbmOcctoStrategy` classes and
  the OCCTO loader/frame were deleted with PR 5 of the feature catalogue (reproduced at
  0 difference first). Logs to MLflow (`just open mlflow`) and publishes forecasts to the
  warehouse (and, for the LightGBM strategies, their TreeSHAP contributions to
  `pma_ml.spot_price_forecast_contribution` and their permutation feature importance to
  `pma_ml.spot_price_forecast_importance` (`--importance-repeats`, default 5) — build
  `+fct_spot_price_forecast_accuracy +fct_spot_price_forecast_contribution
  +fct_spot_price_forecast_importance` afterwards). `--start-date/--end-date` pin the
  first training row — set all three identically for a feature experiment and its matched
  baseline. Feast's retrieval needs a UTC Spark session (the devcontainer's), so the spot
  LightGBM backtests run in the devcontainer only.
- `just python scripts/compare_spot_price_runs.py --baseline <run_id> --candidate <run_id>` —
  matched two-run comparison (MAE overall / by day part / near the OCCTO peak hour / by
  month / high-price days, plus bias) as markdown; needs
  `just dbt build --select +fct_spot_price_forecast_accuracy` after the runs.
- `just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype --area tokyo` —
  day-ahead area demand backtest. Strategies: the nine presets of `tasks/demand/presets.py`
  — `lightgbm`, `lightgbm_msm`, `lightgbm_msm_popw`, `lightgbm_msm_popw_daytype` (the
  script default and the Kansai baseline), `lightgbm_msm_popw_daytype_simday` (the Tokyo
  demand baseline, reference run `008868fe…`; Tokyo-only, because its
  `ftr_period_similar_day` mart needs the でんき予報 hourly load of
  `fct_area_power_usage_hourly` and a fit of the similar-day weights, below) and its four
  calendar variants `…_simday_calendar`, `…_simday_holidaydegree`,
  `…_simday_holidaydistance` and `…_simday_calendarcounts` (research `demand/R-005`, all
  rejected, kept as reference presets; their feature lists and numbers are in the Demand
  task bullet below). Areas: `tokyo`, `kansai` = the TSO feeds loaded into
  `fct_area_demand_generation_actual`. An area's feature marts need its representative JMA
  station's hourly weather loaded and current (`dim_area.representative_jma_station_id`:
  東京 s47662, 大阪 s47772 — both loaded and current as of the 2026-08-20 re-scope backfill;
  keep them fresh with the JMA download + load scripts, since a stale window's last days are
  skipped for lack of a temperature window), the MSM forecasts of its stations in
  `fct_jma_msm_weather_forecast_hourly` (a delivery day without a forecast is skipped) and
  `fct_census_population_jma_station`. Same flags as the spot script: `--add VIEW:COLUMN …` /
  `--drop VIEW:COLUMN …` with `--name`, `--days` (default 365), `--start-date` /
  `--end-date`, `--train-start`, `--importance-repeats`. Logs to the MLflow experiment
  `demand`, publishes to `pma_ml.demand_forecast`, then `just dbt build --select
  +fct_demand_forecast_accuracy +fct_demand_forecast_contribution
  +fct_demand_forecast_importance` (the second selector materialises the run's TreeSHAP
  contributions for the dashboard's Explanation tab, the third its permutation feature
  importance for that tab's Feature importance section — `--importance-repeats`, default 5;
  the first is what its Run filter reads). Feast's retrieval needs a UTC Spark session (the
  devcontainer's), so the demand backtests run in the devcontainer only.
- `just python scripts/fit_similar_day.py --area tokyo` — score the demand similar day walking
  forward (since 2026-09-11, feature catalogue PR 7): every `--refit-every-days` (default 7,
  the LightGBM strategies' refit cadence) a fit of the seven weights of the similar-day
  distance runs at a cutoff instant on the (target, candidate) pairs of the
  `--fit-window-days` days before it (default 730, the LightGBM strategies' training window;
  a sliding window since 2026-09-12 — the first version took every pair back to 2019, so
  the fit grew without bound) whose target load was public by then
  (`tasks/demand/similar_day.py`'s selector; the loads' `available_at`, which
  `AreaHourlyLoad` carries: the daily files' update time from 2022-04, two days after the
  day for the yearly files before) and scores the days whose 09:30 D-1 issue time follows the
  cutoff until the next one, so no day is scored with weights that saw a load that was not
  yet public — and writes the feature to `pma_ml.similar_day`
  (`tasks/demand/similar_day_feature.py`; 48 rows per day, partitioned by `run_id` like the
  forecast tables); `--window-half-width-days` (default 30). Logs to the MLflow experiment
  `similar_day` (`refit_every_days`, `n_fits`, `first_fit_cutoff`, `last_fit_cutoff`,
  `n_days_scored`, the last fit's `similar_day_*` params; every fit's weights as
  `similar_day_fits.csv`, the selection of every scored day as `similar_day_selection.csv`,
  the retrieval check of every scored day with a known load as `similar_day_retrieval.csv`
  and the four `similar_day_*` metrics over them; tag `feature_table`). Then
  `just dbt build --select stg_ml__similar_day ftr_period_similar_day` passes the rows to
  Feast; a re-run is a new run whose rows win wherever they overlap. The first run backfills
  every day from 2019 (the first fit runs when eight pairs are public — with 61 candidates a
  day, when the first scorable day's load is — so scoring starts 2019-04-04); scoring only
  new days is the live-path spec's. Needs the devcontainer.
- `just python scripts/compare_demand_runs.py --baseline <run_id> --candidate <run_id>` — the
  demand task's matched two-run comparison (`tasks/demand/compare.py`): MAE overall / MAPE /
  bias / by day part, day type, month, season, 2,000-MWh actual-demand band, top-10 % demand
  days, plus the daily paired comparison (share of days lower, seeded percentile-bootstrap CI
  over days of the mean daily-MAE difference, share of the gain from the k most-improved days)
  as markdown; `--mae-by-month-png` also writes the research figure. Reads
  `fct_demand_forecast_accuracy` (+ `dim_delivery_period`, `dim_date`), so run
  `just dbt build --select +fct_demand_forecast_accuracy` after the runs. Options:
  `--high-demand-quantile`, `--band-mwh`, `--resamples`, `--seed`, `--top-days`.
- `just python scripts/create_forecast_dashboard.py [--task spot_price|demand]` — (re)build the
  Superset forecast-analysis dashboards from the repo (idempotent; no `--task` = all): per task a
  `DashboardSpec` (dataset SQL, unit, formats, band/calibration columns) drives one shared set of
  chart/layout builders; charts are matched by name *within their dataset*, so both dashboards
  share chart names. Rerun after `docker compose down -v` or after editing a spec.
  Each dashboard has five virtual datasets — `<task>_forecast_analysis` (the accuracy mart),
  `<task>_forecast_explanation` (`fct_<task>_forecast_contribution` joined to the accuracy mart:
  one row per period × component, so AVG-only metrics), `<task>_forecast_comparison` and
  `<task>_forecast_explanation_comparison` (both for the Compare tab) and
  `<task>_forecast_importance` (`fct_<task>_forecast_importance` joined to `dim_area`: one row
  per feature × repeat, run grain, `main_dttm_col` = `published_at`) — and three top-level tabs:
  **Accuracy** (KPI tiles, error structure, calibration & distribution, runs & drilldown),
  **Explanation** and **Compare**, each built by its own `build_<tab>_tab` function
  returning a `DashboardTab` (charts by name in creation order + layout sections) that
  `build_dashboard` wires into filters and cross-filters. In **Explanation**, a **Day** native
  filter (scoped to that tab's per-day charts and to the Compare tab's
  explanation-vs-baseline section; cascades from Run,
  defaults to the default run's last day; empty = the run's mean decomposition; every value is
  a mean per period) drives base / forecast / actual / net-effect tiles, a `waterfall` of the
  mean per-period feature contributions (the base is a tile, not a bar: Superset's value axis
  always includes zero; bars sort by label, hence the `00 base`, `01 time_code`…
  `component_label` prefix), the component table and **Contributions by period**, a Mixed Chart:
  the features' contributions stacked per `time_code` (base row filtered out, so the bars sit at
  the contributions' scale around zero) with two lines on the same axis — `Forecast − base`, the
  signed sum of the bars (a stack of mixed signs has no visible edge for it), and
  `Actual − base`; the gap between the lines is the period's error. Both line metrics read the
  base off the period's base row, so the chart's query B is unfiltered. At the bottom of the
  tab, the **Feature importance** section (the run, not the Day: outside the Day filter and the
  day tables' cross-filters — `RUN_LEVEL_CHART_NAMES`) shows **Permutation importance** —
  horizontal bars of ΔMAE per feature, the MAE increase when that feature's column is shuffled
  across the run's scored periods (`avg(permuted_mae) − avg(mae)`, mean over the repeats) —
  next to **Mean |SHAP| by feature** on the explanation dataset (base row excluded), and the
  **Feature importance table** (MAE, permuted MAE, ΔMAE, std over repeats, importance %).
  Correlated features share importance; the section header says so.
  **Compare** — a third virtual dataset `<task>_forecast_comparison` (the accuracy mart self-joined
  on day × time code × area: the Run filter's run as the candidate against a **Baseline** native
  filter's run, both pinned inside the dataset SQL with Superset Jinja `filter_values()`, so
  `conf/superset/superset_config.py` sets `ENABLE_TEMPLATE_PROCESSING`; inner join, so only
  periods both runs scored count) drives delta tiles coloured by sign (ΔMAE, ΔMAE %, Δ|bias|,
  ΔWAPE; blue = candidate better, orange = worse), matched coverage / days / share of days lower /
  median daily ΔMAE, diverging Better / Worse bars of ΔMAE % by time code, day part, day type, day
  of week, actual band and year, ΔMAE % heatmaps (blue-white-yellow, ±30 %), daily ΔMAE bars, the
  cumulative error reduction, Most improved / Most worsened days tables (full width; cross-filtering
  the detail charts, the Explanation tab and the explanation-vs-baseline section; the holiday name
  is blank on non-holidays rather than `dim_date`'s "Not Applicable"), an **Explanation vs
  baseline** section and a three-line 30-minute detail.
  That section reads the fourth dataset, `<task>_forecast_explanation_comparison`: the
  contribution fact self-joined the same way on the periods both runs explained (one base row per
  period per run), one row per period × component of either run — a component one run lacks
  contributes 0 on that side, so its whole contribution is the delta, and baseline-only components
  sort after the candidate's (`component_order` + 100, three-digit label prefix) — and shows Δ base
  value / Δ net feature effect / Δ forecast tiles, a `waterfall` of per-component contribution
  deltas (candidate − baseline, mean per period) and its table; the Day filter applies to it
  (a day's mean per period; empty = the run's). The Baseline filter is scoped
  to that tab, reads its options from the analysis dataset's `baseline_run_label` alias, and opens
  on the newest other run with the same area and window as the newest run (`--baseline-run
  <run_id or prefix>` overrides); the bootstrap CI over days stays in `compare_<task>_runs.py`.
  Run labels are `published_at | area | strategy | run_id prefix` (`RUN_LABEL_SQL`, one
  definition); the leaderboard shows each run's first / last day and day count. Runs
  published before 2026-08-26 have no contributions and show an empty tab until re-run. After a
  backtest run, the three marts must be rebuilt before the dashboards make sense — `just dbt
  build --select +fct_<task>_forecast_accuracy +fct_<task>_forecast_contribution
  +fct_<task>_forecast_importance`; `+fct_<task>_forecast_contribution` alone does not refresh
  the accuracy mart (which the Run filter reads) nor the forecast fact the additivity test joins
  to, and the Run filter then never lists the new run. Clicking a date in **Worst days** (Accuracy tab) or in **Most improved days** / **Most worsened days** (Compare tab) cross-filters the Explanation tab (and the 30-minute detail charts) to that day — cross-filters persist across tabs, and it combines with the Day filter, so clear Day (or pick the same day) first.
- Host-side dbt also works: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`.
- Anything that creates a SparkSession MUST run in the devcontainer (metastore/warehouse only
  resolve on the compose network); plain python and dbt work from the host too.

## Architecture (data flow)

- JEPX CSVs: `scripts/download_jepx_spot.py` → `data/jepx/spot/` (gitignored) →
  `scripts/load_jepx_spot.py` (`CsvLoader`, load contract in `conf/schemas/jepx_spot.yaml`) → `pma_raw.jepx_spot`.
- JMA weather CSVs (staffed stations only since the 2026-08 re-scope, and only stations
  inside a JEPX area — Okinawa, Antarctica and 南鳥島 are excluded, so
  `dim_jma_station.area_key` is a required FK to `dim_area`):
  `scripts/download_jma_hourly_all.py` (per-station: `download_jma_hourly.py`) →
  `data/jma/hourly/` → `scripts/load_jma_hourly.py` (`JmaHourlyCsvLoader`, positional
  contract `conf/schemas/jma_hourly_staffed.yaml`, 27 columns) →
  `pma_raw.jma_hourly_staffed` only (over-budget station-years are fetched as 2 request
  windows and stitched into one file; 均質番号 resets per window, so a stitched year file
  resets it at the mid-year boundary). Station master:
  `scripts/update_jma_stations_seed.py` (`staffed_only=True`, `jepx_areas_only=True`) →
  seed `jma_stations` → `dim_jma_station`, which joins the hand-curated seed
  `jma_station_areas` (station → JEPX area per the TSO 供給区域 definitions;
  prefecture-level except 静岡, split at the 富士川) for its `area_key`/`area_code`
  columns — every station must have a mapping row. Protocol + CSV format:
  [docs/JMA-Weather-Data-Retrieval.md](docs/JMA-Weather-Data-Retrieval.md).
- JMA MSM GPV surface forecast (one vintage per delivery day D — the 12 UTC D-2 run, leads
  28-51 = JST hour-endings 01:00-24:00 of D, safely before the demand model's 09:30 JST D-1
  cutoff): `scripts/download_jma_msm_surface_forecast.py` (`MsmDownloader` in
  `power_market_analytics/msm.py` — the single MSM module: vintage/grid logic, the eccodes
  GRIB2 decoder with per-call `codes_grib_multi_support_on()` — JMA packs many fields per
  message — the downloader and the raw loader; three RISH GRIB2 files/day, deleted after a
  successful extract by default) → `data/jma/msm_surface_forecast/` (one `csv.gz` extract +
  manifest per delivery day) → `scripts/load_jma_msm_surface_forecast.py`
  (`MsmForecastCsvLoader`, same module, so it needs eccodes installed too; contract
  `conf/schemas/jma_msm_surface_forecast.yaml`) → `pma_raw.jma_msm_surface_forecast` →
  `stg/std_jma__msm_surface_forecast` (JST conversion, raw UTC kept as ISO strings) →
  `fct_jma_msm_weather_forecast_hourly` (grain station_id × forecast_reference_at ×
  forecast_valid_at; nearest-grid-point values, not station-specific; joins
  `fct_jma_weather_hourly` on station_id + forecast_valid_at = observed_at for
  forecast-vs-observed comparisons). Protocol, GRIB2 element table and verification results:
  [docs/JMA-MSM-GPV-Retrieval.md](docs/JMA-MSM-GPV-Retrieval.md).
- OCCTO 翌々日 demand forecast: `scripts/download_occto_demand_forecast.py`
  (`OcctoBulkDownloader` in `power_market_analytics/occto.py`, always re-downloads the whole
  history) → `data/occto/demand_forecast_dad/` → `scripts/load_occto_demand_forecast.py`
  (`CsvLoader`, contract `conf/schemas/occto_demand_forecast_dad.yaml`) →
  `pma_raw.occto_demand_forecast_dad` → `stg/std_occto__demand_forecast_dad` →
  `fct_occto_demand_supply_forecast_daily` (9 JEPX areas; エリア計 totals + Okinawa stay in `std`).
  Protocol + CSV format:
  [docs/OCCTO-Demand-Forecast-Retrieval.md](docs/OCCTO-Demand-Forecast-Retrieval.md).
- OCCTO 広域予備率 エリア・広域ブロック情報 (翌々日, half-hourly area demand/supply-capacity
  forecast, 480 rows/day from 2025-04-01): `scripts/download_occto_area_reserve_rate.py`
  (same `OcctoBulkDownloader`, dataset `area_reserve_rate_dad` = `areaDataKnd=31`; the
  downloader splits chunked datasets into `max_days_per_download` windows and concatenates
  them into one CSV) → `data/occto/area_reserve_rate_dad/` →
  `scripts/load_occto_area_reserve_rate.py` (`CsvLoader`, contract
  `conf/schemas/occto_area_reserve_rate_dad.yaml`) → `pma_raw.occto_area_reserve_rate_dad` →
  `stg/std_occto__area_reserve_rate_dad` (時刻 "00:30".."24:00" → JEPX `time_code` 1–48,
  block columns kept) → `fct_occto_demand_supply_forecast_30m` (grain date × time_code × area,
  9 JEPX areas, `demand_mw` + `supply_capacity_mw` only; joins `fct_jepx_spot_area_price` 1:1).
  Same numbers as the 広域予備率Web公表システム (`web-kohyo.occto.or.jp`, 31-day / rolling
  前年度4月 window) — verified identical; format + both portals in the OCCTO doc §9.
- TSO エリア需要・発電情報 実績 (30-min area demand / generation actuals; the インバランス料金
  「系統の需給に関する情報」 items A-1/B-1/B-4, one feed per TSO): shared
  `AreaActualsDownloader` / `AreaActualsCsvLoader` in `power_market_analytics/area_actuals.py`,
  driven by a per-TSO `AreaActualsSource` spec (URL template, earliest month, member regex,
  accepted header lines, `archive_includes_current_day`, `known_missing_days` — days the TSO never
  published, which a settled month may lack; a listed day that is published is logged) — always
  re-downloads every monthly zip
  and extracts only the daily 実績 members; the loader reads every daily file positionally in one
  scan, sniffs each file's metadata line for `file_updated_at` (joined back on the file name),
  normalises `yyyy/mm/dd` dates and skips not-yet-final files.
  - TEPCO / Tokyo: `power_market_analytics/tepco/area_demand_generation.py` (`TEPCO`,
    `TepcoAreaDownloader`; the `tepco/` package holds one module per TEPCO dataset and
    re-exports these names) →
    `scripts/download_tepco_area_demand_generation.py` → `data/tepco/area_demand_generation/{zip,csv}/`
    → `scripts/load_tepco_area_demand_generation.py` (`TepcoAreaCsvLoader`, contract
    `conf/schemas/tepco_area_demand_generation_actual.yaml`) → `pma_raw.tepco_area_demand_generation_actual`
    → `stg/std_tepco__area_demand_generation_actual`. Format + quirks:
    [docs/TEPCO-Area-Demand-Generation-Retrieval.md](docs/TEPCO-Area-Demand-Generation-Retrieval.md).
  - 関西電力送配電 / Kansai: `power_market_analytics/kansai/area_demand_generation.py` (`KANSAI`,
    `KansaiAreaDownloader`; the `kansai/` package holds one module per Kansai dataset and
    re-exports these names) →
    `scripts/download_kansai_area_demand_generation.py` → `data/kansai/area_demand_generation/{zip,csv}/`
    → `scripts/load_kansai_area_demand_generation.py` (`KansaiAreaCsvLoader`, contract
    `conf/schemas/kansai_area_demand_generation_actual.yaml`, nullable bigint measures) →
    `pma_raw.kansai_area_demand_generation_actual` → `stg/std_kansai__area_demand_generation_actual`.
    Format (two layouts, switch 2025-12-25) + quirks:
    [docs/Kansai-Area-Demand-Generation-Retrieval.md](docs/Kansai-Area-Demand-Generation-Retrieval.md).
  - Curated: `fct_area_demand_generation_actual` = `union all` of the `std_<tso>__…` models joined
    to `dim_area` (grain date × time_code × area; joins `fct_jepx_spot_area_price` 1:1). Adding a
    TSO = new spec + contract + stg/std models + one union branch.
- TSO でんき予報 過去の電力使用実績 (hourly area 電力使用状況, 1時間平均 in 万kW — the only
  public area demand before 2022-04; a different display series from A-1, one feed per TSO):
  the parser and loader are the shared `power_market_analytics/power_usage.py` —
  `PowerUsageSource` (= `AreaActualsSource` + `multi_day_headers`, the headers whose files may
  hold many dates; every other file must hold one), `parse_hourly(file, source)` (the hourly
  table under the first accepted header, ending at the first blank line; every line is read
  with trailing commas removed — Excel-padded files — and a `修正後` row corrects the row
  above it: its non-blank measures replace the original's) and `PowerUsageCsvLoader` (one
  `createDataFrame` over the parsed rows, a per-file `_file_rows` hook, the `__`-prefixed
  contract sources). The daily files also carry a 5-minute table that is parsed past, not
  loaded.
  - TEPCO / Tokyo: `power_market_analytics/tepco/power_usage.py` (`TEPCO_POWER_USAGE` spec,
    `TepcoPowerUsageDownloader` = yearly `juyo-YYYY.csv` 2016 … 2022 cached + monthly
    `YYYYMM_power_usage.zip` 2022-04 → now via the shared downloader, `parse_hourly(file)` bound
    to the spec, `TepcoPowerUsageCsvLoader` — `_file_rows` drops yearly rows ≥ 2022-04-01 so
    the daily files win) → `scripts/download_tepco_power_usage.py` →
    `data/tepco/power_usage/{zip,csv}/` → `scripts/load_tepco_power_usage.py` (contract
    `conf/schemas/tepco_power_usage_hourly.yaml`, grain date × hour_start 0–23) →
    `pma_raw.tepco_power_usage_hourly` → `stg_tepco__power_usage_hourly` →
    `std_tepco__power_usage_hourly` (typed hour axis: `hour_start` 0–23 as published +
    `hour_ending` 1–24, `delivery_datetime` = hour start, integer 万kW; all four published
    measures kept, the daily-file 予測値 / 使用率 / 供給力 null before 2022-04-01;
    `demand_mankw` tested ≥ 1 — no sentinel, TEPCO never re-issues a day; singular test
    `assert_std_tepco__power_usage_hourly_calendar_complete` = gapless from 2016-04-01). Format,
    quirks and the 4.4-year comparison with A-1 (incl. A-1's 18:00–19:00 defect since
    mid-2025): [docs/TEPCO-Power-Usage-Retrieval.md](docs/TEPCO-Power-Usage-Retrieval.md).
  - 関西電力送配電 / Kansai: `power_market_analytics/kansai/power_usage.py` (`KANSAI_POWER_USAGE`
    spec: monthly `…/yamasou/YYYYMM_jisseki.zip` 2016-04 → now — same archive name as the A-1
    feed's, different data dir —, members `YYYYMMDD_juyo1_kansai.csv` → `juyo_06_YYYYMMDD.csv`
    from 2025-12, three hourly headers — `供給力想定値` added 2019-09-12, renamed `供給力`
    2025-12-25 —, `known_missing_days = {2024-03-31}`; `KansaiPowerUsageDownloader`,
    `KansaiPowerUsageCsvLoader`) → `scripts/download_kansai_power_usage.py` →
    `data/kansai/power_usage/{zip,csv}/` → `scripts/load_kansai_power_usage.py` (contract
    `conf/schemas/kansai_power_usage_hourly.yaml`, TEPCO's columns) →
    `pma_raw.kansai_power_usage_hourly` → `stg_kansai__power_usage_hourly` →
    `std_kansai__power_usage_hourly` (same shape as TEPCO's; `forecast_mankw` / `usage_rate_pct`
    tested not null, `supply_capacity_mankw` null exactly before 2019-09-12 and ≥ demand;
    singular test `assert_std_kansai__power_usage_hourly_calendar_complete` = gapless from
    2016-04-01 except 2024-03-31). Format, quirks (60 Excel-padded files, the 2016-04-24
    `修正後` row, the 2020-11-16 使用率 redefinition) and the comparison with the Kansai A-1
    series: [docs/Kansai-Power-Usage-Retrieval.md](docs/Kansai-Power-Usage-Retrieval.md).
  - Curated: `fct_area_power_usage_hourly` = `union all` of the `std_<tso>__power_usage_hourly`
    models joined to `dim_area` (grain `date_key × hour_of_day × area_key`, `demand_kwh` =
    万kW × 10,000 only — energy over the hour, the A-1 fact's unit; this series alone, not
    stitched with A-1). `hour_of_day` references `dim_delivery_hour`, the 24-row shrunken rollup
    of `dim_delivery_period` (built from it — `group by hour_of_day, is_daytime, day_part` —
    so `day_part` cannot diverge; `dim_delivery_period.hour_of_day` is the rollup FK), which is
    how the hourly fact and the 30-minute fact drill across: sum the 30-minute `demand_kwh`
    per `hour_of_day`. Adding a TSO = new spec + contract + stg/std models + one union branch.
- e-Stat census 500 m population mesh (国勢調査 4次メッシュ, one CP932 text file per 第１次地域区画):
  `scripts/download_estat_census_population_mesh.py` (`EstatCensusMeshDownloader` in
  `power_market_analytics/estat.py`; per-vintage `CensusVintage` config in `VINTAGES` — stats id,
  population column, census date, datum, listing URL, expected file count; the listing rows come
  from the `search_detail` JSON endpoint, not the HTML page; zips validated before caching, member
  extracted byte-for-byte) → `data/estat/census_population_mesh/{year}/{zip,txt}/` →
  `scripts/load_estat_census_population_mesh.py` (`EstatCensusMeshCsvLoader`, also in `estat.py`:
  vintage from the file name, reads each vintage's files in one scan (grouped by exact header line),
  selects that vintage's population column, injects vintage attributes, validates mesh codes /
  population / HTKSYORI per file in one grouped pass before casting; contract
  `conf/schemas/estat_census_population_mesh.yaml`) → `pma_raw.estat_census_population_mesh` →
  `stg_estat__census_population_mesh` → `std_estat__census_population_mesh` (+ bounding box /
  centroid decoded from the mesh code; Python reference `estat.decode_mesh_code`) →
  `dim_population_mesh_500m` (one row per mesh across vintages) + `fct_census_population_mesh`
  (`census_year × mesh_code`, `population_total` as published at every mesh — the 秘匿処理 folds only
  the `*`-suppressed detail columns, never the total; additive across meshes, not across years) →
  `fct_census_population_jma_station` (`census_year × station_id`: each mesh assigned to the
  nearest staffed station ≤ 1,000 m elevation with a JEPX area — haversine `min_by` over a
  ~0.9 M × 144 cross join, ~80 s per build — `population_total`, `area_population_total`,
  `area_population_weight` = share within the station's area, summing to 1 per area; the five
  summit/high-altitude stations 富士山・剣山・伊吹山・奥日光・阿蘇山 are excluded because their MSM
  grid-point forecast misrepresents the lowland towns nearest to them). Adding a census = one
  `VINTAGES` entry + fixtures + the singular dbt test's year list.
  Protocol + format: [docs/eStat-Census-Population-Mesh-Retrieval.md](docs/eStat-Census-Population-Mesh-Retrieval.md).
- dbt (`dbt/`): sources in `models/raw/<source>.yml` → `staging` (as-is) → `standardized`
  (typed time axis) → `curated` (Kimball star: `dim_*`, `fct_*`) → `features` (feature marts
  `ftr_<grain>_<family>`, since 2026-09-10: one model per source family at its grain — day
  = `area_code × trade_date`, hour = `… × hour_ending`, period = `… × time_code` — every
  feature column tagged `config.meta.feature` / `categorical`, plus `available_at` carried
  from the facts through the `available_at()` macro; the singular test
  `assert_feature_marts_declare_available_at` lists any feature model without the column.
  Today's seven: `ftr_day_calendar`, `ftr_day_occto`, `ftr_hour_jma_obs`, `ftr_hour_msm`,
  `ftr_period_actuals`, `ftr_period_jepx` (each proven equal to the Python builder it
  mirrors for Tokyo 2025) and `ftr_period_similar_day` (since 2026-09-11: the demand
  similar day as the fit-and-score job wrote it to `pma_ml.similar_day`, passed through
  the guarded `stg_ml__similar_day`, one row per scoring run, with `published_at` next to
  `available_at`); every strategy reads them through Feast; design
  `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md`).
  Schemas: `pma_<layer>`.
- Feature retrieval (Feast, since 2026-09-10; both tasks' presets read it since 2026-09-11):
  `power_market_analytics/features/` — `entities.py` (join keys `area_code`, `trade_date_key`
  = int yyyymmdd, `hour_ending`, `time_code`; `GRAIN_ENTITIES` per mart grain), `views.py`
  (generated: one `SparkSource(query=…)` + `FeatureView` per mart, tagged columns as fields,
  `timestamp_field = available_at`, `created_timestamp_column = published_at` for a mart
  that has the column — several vintages per key, the newest published wins among rows
  tied on `available_at` — `online=False`), `store.py` (`open_store()` reads
  `conf/feast/feature_store.yaml` — Spark offline store on the active SparkSession, file
  registry `data/feast/registry.db`, gitignored — and applies the entities and views, so
  every caller's registry matches the package; `session_time_zone`) and `retrieval.py`
  (`entity_frame(area_code, days, issue_offset)`: one row per delivery period stamped with
  its issue time; `historical_features(store, entity_df, features)`: Feast's point-in-time
  join, the newest row per key with `available_at <=` the row's issue time, in the frame's
  row order). Proven on Tokyo 2025 against the marts inside the devcontainer. `feast[spark]`
  is a dependency since 2026-09-10 (`docker compose build devcontainer` after pulling the
  lock).
- Japanese holidays: Cabinet Office CSV → `scripts/update_holidays_seed.py` → seed → `dim_date`
  (spine end derives from the seed's max year). `dim_date.is_holiday` is the seed's 国民の祝日
  **plus** the customary non-working days computed in SQL — 年末年始 12/30–1/3, ゴールデンウィーク
  4/30–5/2 (the 休日 set every family-A TSO 託送供給等約款 uses; 東北/北陸/中国/沖縄 differ) and
  お盆 8/13–16 (convention only) — so `is_business_day` means "working day", not "banks open".
  `holiday_degree` (double, since 2026-09-05) is the graded 休日度合い of patent JP 4448226 B2
  (新日本製鐵; `docs/research/papers.md`): the largest of 1.0 on a Sunday / 祝日, 0.8 on a
  Saturday, 0.8 on the first day of 年末年始 (12/30) / ゴールデンウィーク (4/29) / お盆 (8/13) and
  1.0 on their other days, 0.5 on one working day sandwiched between off days
  (`not is_business_day` on both sides) and 0.3 on each of two, else 0; design
  `docs/superpowers/specs/2026-09-05-dim-date-holiday-degree-design.md`.
  `dim_date` also carries the standard calendar features of Azure AutoML forecasting at daily
  grain (since 2026-09-06: `half`, `day_of_quarter`, `day_of_year` next to the existing `year` /
  `quarter` / `month` / `day_of_month` / `day_of_week_iso` — Monday = 1, Azure's `wday` is 0 — /
  `day_name` / `month_name`); `year_iso`, `week` and the sub-daily ones were left out.
  A `prior_year_reference_date` (+ rule) column — the same weekday / same-named holiday one
  year earlier, for the demand task's year-ago load feature — lived on `dim_date` from
  2026-08-31 to 2026-09-05 and was removed with that feature (research `demand/R-004`, Not
  supported).
- Forecast write-back: `scripts/spot_price_backtest.py` logs the run to MLflow AND publishes
  row-level forecasts (`forecasting/publish.py`) to `pma_ml.spot_price_forecast`
  (parquet, partitioned by `run_id`, dynamic partition overwrite = idempotent per run) →
  `stg/std_ml__spot_price_forecast` → `fct_spot_price_forecast` →
  `fct_spot_price_forecast_accuracy` (joins actuals; the Superset-facing surface, read by the
  **Spot Price Forecast Analysis** dashboard via the `spot_price_forecast_analysis` dataset).
  `run_id` links warehouse rows to the MLflow run; the run's `warehouse_table` tag points back.
  `tasks/spot_price/compare.py` reads the accuracy fact back for run-vs-run segment tables.
- Explanations: every `SlidingWindowLightGbmStrategy` records exact TreeSHAP values per predicted
  row; `strategy.contributions()` (`None` for strategies with nothing to attribute — spot
  `previous_day`) melts them into `ForecastContributions` (one row per period × component: `base`
  = the expected value, `component_order` 0, then the features in `feature_cols` order; per
  period `base + Σ features = forecast`; `feature_value` = the feature as the model saw it, null
  on the base row) and the backtest scripts publish them right after the forecasts
  (`publish.build_contribution_records` aligned to the scored periods, the forecast rows'
  `published_at` reused so `run_label` matches; `publish_contribution_records` →
  `pma_ml.<task>_forecast_contribution` = `TaskSpec.contribution_table`, column
  `TaskSpec.contribution_col` = `contribution_demand_kwh` / `contribution_price_jpy_kwh`; MLflow
  tag `contribution_table`) → `stg/std_ml__<task>_forecast_contribution` (+ `trade_datetime`,
  `is_base`) → `fct_<task>_forecast_contribution` (grain run × period × area × component;
  singular tests: one base row per period, Σ contributions = the forecast within 1e-6) → Superset
  dataset `<task>_forecast_explanation`.
- Diagnostics: `ForecastStrategy.diagnostics(history, run)` (default `{}`) returns per-run frames
  keyed by artifact stem; both backtest scripts call it after publishing and log each frame as
  `<stem>.csv`, and an implementation may log metrics inside it. No registered strategy
  implements it since 2026-09-11: the similar-day selection and retrieval check moved to
  the fit script's run.
- Importance: `ForecastStrategy.permutation_importance(run, n_repeats=5, seed=0)` (default
  `None`) returns a `PermutationImportance` frame (grain feature × repeat: `n_periods`, `mae` =
  the run's MAE on its scored periods, `permuted_mae`); the LightGBM base keeps every refit
  (`_models`, `_model_of_day`) and `forecasting/importance.py` wraps them in
  `WalkForwardPredictor` (a scikit-learn estimator routing each row to the model that forecast
  its day) for `sklearn.inspection.permutation_importance` (`neg_mean_absolute_error`; the same
  shuffles for every feature), so the importance is walk-forward and out of sample. The scripts
  publish it (`publish.build_importance_records` / `publish_importance_records` →
  `pma_ml.<task>_forecast_importance` = `TaskSpec.importance_table`, columns `TaskSpec.mae_col` /
  `permuted_mae_col` = `mae_demand_kwh` / `permuted_mae_demand_kwh`; tag `importance_table`;
  params `permutation_repeats`, `permutation_seed`) and log `permutation_importance.csv`
  (`PermutationImportance.summary()`: mean / population std over repeats, importance %),
  `permutation_importance_repeats.csv` and `permutation_importance_plot.png`
  (`plots.permutation_importance_plot`) → `stg_ml__<task>_forecast_importance` →
  `fct_<task>_forecast_importance` (no `std`: no time axis; singular test: `n_periods` and the
  MAE reconcile with the accuracy mart per run) → Superset dataset `<task>_forecast_importance`
  (the Explanation tab's Feature importance section). The two `stg_ml__<task>_forecast_importance`
  models guard their source with `load_relation` (pre-hook macro `refresh_ml_source_if_exists`,
  `dbt/macros/`): until the first backtest creates `pma_ml.<task>_forecast_importance` they build
  as an empty frame of the contract's types, so an unqualified `dbt build` (`refresh-all`) does
  not abort on a warehouse that has never published importance. The rule that keeps that true:
  the guarded staging model is the only dbt node that reads an importance source — the source
  entries in `models/raw/ml.yml` carry no data tests (a source test queries the table directly);
  the staging contract and tests check every column instead.
- Exogenous features (spot): the `lightgbm_occto` preset adds the three `ftr_day_occto`
  columns (from `fct_occto_demand_supply_forecast_daily`, 2024-04-01 on); a row without
  them is dropped from training, so a matched `lightgbm` baseline needs
  `--train-start 2024-04-01`.
- Modeling tasks live under `power_market_analytics/tasks/<task>/` (`spot_price`, `demand`),
  each a thin configuration of the shared framework `power_market_analytics/forecasting/`:
  a frozen `TaskSpec` in the task's `__init__.py` (name = MLflow experiment, unit,
  `history_lead_days`, `issue_offset`, `forecast_table`, the task's four frame classes),
  frames as two-line subclasses of `forecasting.frames` (`HalfHourlySeries` / `DayAheadForecast`
  / `BacktestResult` / `ForecastRecords`, schema assembled from `value_col` /
  `forecast_col` / `actual_col`), `forecasting.backtest.run_backtest` (history the strategy
  sees = days ≤ `task.history_cutoff(D)`; a `ForecastUnavailableError` skips the day and is
  reported on `BacktestRun.skipped_days`; forecast points without an actual are dropped),
  `forecasting.lgbm.SlidingWindowLightGbmStrategy` (the window, refit cadence, TreeSHAP,
  importance and evaluation; a subclass sets `task`, `feature_cols`, `eval_set_cls`,
  `lookback_days`, optionally `categorical_feature_cols` — passed to
  `LGBMRegressor.fit(categorical_feature=…)` and logged as `lgbm_categorical_feature_cols` —
  and implements the one hook `_features`, all plain attributes, not `ClassVar`s; since
  2026-09-11 `forecasting.preset_lgbm.PresetLightGbmStrategy` is that subclass for every
  preset: it sets them per instance from a `Preset`, reads its features off a retrieved
  `FeatureFrame` in `_features`; no strategy builds a feature in Python since PR 7),
  `forecasting.publish` and `forecasting.plots`. Adding a task = TaskSpec + frames + datasets + strategies +
  script + `pma_ml.<task>_forecast` dbt models.
- Demand task (`tasks/demand/`): at 09:30 JST on D-1 forecast the 48 half-hourly `demand_kwh`
  of `fct_area_demand_generation_actual` for D; usable history = days ≤ D-2
  (`history_lead_days = 2`, TSO files finalise after midnight). Null-demand rows (TSO holes)
  are dropped at load; a target day whose D-7 lag falls in a hole is skipped. Since 2026-09-11
  (feature catalogue PRs 6 and 7) every strategy is a **preset** (`tasks/demand/presets.py`,
  `<view>:<column>` references into the Feast views of the feature marts, retrieved by
  `build_strategy` and run by `PresetLightGbmStrategy`; the nine strategy classes, their
  eval sets, `demand/features.py`, the `AreaTemperature`, `AreaTemperatureForecast` and
  `DayTypeCalendar` frames and their loaders were deleted, each run reproduced first — the
  one known divergence from the class-based code is two December-2022 delivery days, see
  Gotchas):
  `lightgbm` = `ftr_day_calendar:month`, `ftr_day_calendar:day_of_week`,
  `ftr_hour_jma_obs:wavg_temperature_c` (the same-hour temperature at the area's
  representative JMA station — `dim_area.representative_jma_station_id`, seed `jepx_areas`;
  hour containing the period = `(time_code + 1) // 2` — over D-8..D-2, weights halving per
  day back) and `ftr_period_actuals:lag_7d_demand_kwh`. `lightgbm_msm` (research
  `demand/R-001`) = that + `ftr_hour_msm:forecast_temperature_c`, the MSM point forecast for D
  at the same station (the D-2 12 UTC vintage); a training row without it is dropped, and MSM
  covers 2019-04-01 → (since the 2026-09-05 backfill), earlier than the demand history's
  2022-04-01 start, so a matched `lightgbm` baseline needs no `--train-start` today.
  `lightgbm_msm_popw` (research `demand/R-002`) = `lightgbm` +
  `ftr_hour_msm:popw_forecast_temperature_c` instead: the same forecast averaged over the
  area's staffed stations with `fct_census_population_jma_station` weights of the latest
  census vintage, renormalised over the stations that have a value for the hour; the observed
  `wavg_temperature_c` stays single-station. `lightgbm_msm_popw_daytype` (research
  `demand/R-003`; the demand baseline 2026-08-26 → 2026-09-06, still the script default and
  the Kansai baseline) = that + `ftr_day_calendar:day_type`: 0 Weekday / 1 Weekend / 2 Holiday
  from `dim_date` (`is_holiday` wins over `is_weekend`, the compare script's day-type
  precedence), categorical by the mart's tag; a delivery day outside `dim_date` is skipped.
  A fifth strategy, `lightgbm_msm_popw_daytype_lag1y` = that + `lag_1y_demand_kwh` (the
  でんき予報 hourly load of `fct_area_power_usage_hourly` on the delivery day's `dim_date`
  prior-year reference date; research `demand/R-004`), was run on 2026-08-31 and removed with
  the column on 2026-09-05 — Not supported, the reasons in the investigation.
  `lightgbm_msm_popw_daytype_simday` (research `demand/R-004` E-002, kept 2026-09-06: the
  Tokyo demand baseline, reference run `008868fe59274abfb49f128e29aa28fe`) = the
  `lightgbm_msm_popw_daytype` preset + `ftr_period_similar_day:similar_day_demand_kwh`, the
  load of a learned similar day one year earlier, halved per period. Since 2026-09-11
  (feature catalogue PR 7) a walk-forward job builds it: `scripts/fit_similar_day.py` refits
  the seven softmax weights of `tasks/demand/similar_day.py`'s distance every 7 days
  (`scipy.optimize.least_squares` on the pairs of the 730 days before the fit's cutoff —
  the LightGBM training window, `--fit-window-days` — whose target load was public by the
  cutoff, Park, Song and Kwon 2020 Eq. 1–3) and scores the days that follow with them — for a
  delivery day D the nearest day in D − 364 ± 30 under seven parts: days from D − 364; the
  24-h RMSE of D's population-weighted MSM forecast against the candidate's
  population-weighted observation for temperature, humidity and rain; |Δ| of `dim_date`'s
  days since / until a named holiday and of `holiday_degree`; a candidate needs all 24
  hourly loads of `fct_area_power_usage_hourly`, a full observed profile and a calendar
  row, D a full forecast profile and a window on or after the first candidate day (first
  scorable day 2019-04-01, the MSM start; the first fit runs when eight pairs are public,
  with its load, so scoring starts 2019-04-04) — and writes the chosen day's hourly load over the period's
  hour ÷ 2 to `pma_ml.similar_day` (`tasks/demand/similar_day_feature.py`: 48 rows per day
  with the chosen day, lag, distance, candidate count and the fit's cutoff next to the
  feature; `available_at` = the latest of the day's MSM forecast vintage's, from
  `AreaWeatherForecast`, the fit's cutoff and the chosen day's load availability; under
  the default window every candidate is at least 334 days older, so the first two decide).
  A fit at cutoff C uses only the pairs of the 730 days before C whose target load was
  public by C (`AreaHourlyLoad` carries the fact's `available_at`;
  `SimilarDaySelector.fit(available_by)`, `fit_window_days`), and a day is
  scored by the latest fit whose cutoff is on or before its issue time, so no day is scored
  with weights that saw a load that was not yet public — the similar-day spec's decision 7
  (one fit per backtest, frozen) replaced by its deferred follow-up, on Codex's findings in
  PR #67 that a separate fit through today would otherwise score the history in sample and
  that the yearly-file loads are public only two days after their day. The window slides
  since 2026-09-12 (the researcher's call on the first run's fits table: every pair back to
  2019 made the fit grow without bound, and weights that settle by averaging say nothing
  about drift); the matched comparison is in the plan's follow-up section.
  `stg_ml__similar_day` (guarded like the importance tables) and
  `ftr_period_similar_day` pass the rows to Feast, one row per scoring run
  (`similar_day_run_id`): the join takes the newest run usable at the issue time and, among
  rows tied on `available_at`, the newest published (the view's `created_timestamp_column`),
  so a re-run replaces the feature wherever it scored. The job's run (MLflow experiment
  `similar_day`) logs every fit's weights (the weight-stability follow-up of E-002), the
  selection and the retrieval check (selected vs D − 364 vs oracle load difference) that the
  deleted strategy's `diagnostics` used to log per backtest. Reproduced 2026-09-11 (PR 7,
  before the walk-forward): the one fit through 2024-08-16 equalled run `008868fe…`'s and
  the old and new code were identical period by period; with the walk-forward the run is a
  matched comparison instead — numbers in
  `docs/superpowers/plans/2026-09-11-similar-day-feature.md`.
  `lightgbm_msm_popw_daytype_simday_calendar` (research `demand/R-005` E-001, run 2026-09-06
  `e3e3bd61…`: MAE +7.3 % on the matched window, rejected by the researcher, Not supported;
  kept as a reference preset) = that + the ten `ftr_day_calendar` columns `half`, `quarter`,
  `day_of_month`, `day_of_quarter`, `day_of_year`, `holiday_degree`, `is_business_day` (1/0),
  `fiscal_quarter`, `days_since_holiday`, `days_until_holiday` (`DAY_CALENDAR_FEATURES` in
  `presets.py`, the old join order); no new categorical. Three subsets of it (research
  `demand/R-005` E-002 / E-003 / E-004, run 2026-09-06, all rejected by the researcher the
  same day, kept as reference presets): `lightgbm_msm_popw_daytype_simday_holidaydegree`
  (`HOLIDAY_DEGREE_FEATURES` = `holiday_degree`; run `a8da46c5…`: MAE +0.3 %, CI over days
  includes zero, holidays +1.8 %, 4.8 % of the SHAP mass mostly from `day_type`),
  `lightgbm_msm_popw_daytype_simday_holidaydistance` (`HOLIDAY_DISTANCE_FEATURES` =
  `days_since_holiday`, `days_until_holiday`; run `f7153839…`: MAE +6.5 %, CI excludes zero,
  every day part / day type / season worse) and `lightgbm_msm_popw_daytype_simday_calendarcounts`
  (`CALENDAR_COUNT_FEATURES` = `half`, `quarter`, `day_of_month`, `day_of_quarter`,
  `day_of_year`, `fiscal_quarter`; run `9182d469…`: MAE +4.2 %, CI excludes zero, weekdays
  +7.7 % but holidays −13.4 % — E-001's holiday gain comes with this subset; `half` /
  `quarter` never split on).
  Write-back: `pma_ml.demand_forecast` →
  `stg/std_ml__demand_forecast` →
  `fct_demand_forecast` → `fct_demand_forecast_accuracy` → Superset **Demand Forecast Analysis**
  dashboard (dataset `demand_forecast_analysis`; the mart's kWh rescaled to MWh in the dataset
  SQL — `forecast_demand_mwh`, `error_mwh`, … — with plain `,.1f`/`,.0f` formats, 2,000-MWh
  actual-demand bands (`10000-12000`), calibration x = actual rounded to 1,000 MWh);
  contributions to `pma_ml.demand_forecast_contribution` → `fct_demand_forecast_contribution` →
  the dashboard's Explanation tab.

## Gotchas

- dbt 1.11 generic tests: put test args under `arguments:` (e.g. `dbt_utils.accepted_range`),
  else deprecation warnings.
- Spark SQL `div` returns `bigint` — cast to `int` where the model contract says `int`.
- Spark SQL numeric literals with a decimal point are `decimal`, not `double`: `1.0 / 240` is
  decimal division truncated to 6 places (0.004167). For double arithmetic (mesh coordinates,
  tolerances) write `cast(1 as double) / 240`, as the estat models and their tests do.
- `dbt show --inline`: use the `--limit` flag; a `limit` clause inside the SQL breaks dbt's wrapper.
- JEPX data history constrains tests: FY2016 has genuine 0.00 area prices (no 0.01 floor yet),
  Hokkaido area prices are null 2018-09-07..26 (earthquake suspension), block/FIP columns are
  null before ~FY2022. Check `conf/schemas/jepx_spot.yaml` + model descriptions before
  tightening constraints.
- OCCTO 翌々日: the two 時刻 columns are hour-ending labels `01:00`..`24:00` (24:00 is not a
  valid Spark time → kept as strings in raw, ints 1–24 in `std`); `min_demand_mw` changed
  meaning on 2025-04-01 (was demand at the min-reserve-rate hour). Details in the doc's §4/§7.
- OCCTO portal failures come back as HTTP 200 HTML — its error screen reads
  不正なリクエストです (the one-shot key/token pair was rejected) or the session-timeout
  message, in the first `<p>`. `OcctoBulkDownloader` retries such a window (also the
  session-timeout JSON, a login without a cookie and HTTP 5xx at any step) up to 3 attempts,
  5 s apart — an attempt = login (first window, every retry) + `ok` + `download`, each retry
  from a fresh session and key pair — then raises `OcctoTransientError` with the page's
  message; validation errors, 4xx and header mismatches are raised at once. Seen once,
  2026-09-06, never reproduced (doc §3.4).
- TEPCO actuals: 13 April-2022 files hold scientific-notation values (`1.66919e+07`) that Spark's
  ANSI `cast(... as bigint)` rejects, so the raw measures are `double` and `std` rounds to
  `bigint`; TEPCO writes 0 for not-yet-observed periods and the archived 2025-06-14 file froze
  mid-day (time codes 11–48 all-zero) → those measures are null from `std` onward. Past days are
  occasionally re-issued, hence the always-re-download policy.
- Timestamps in tests: PySpark's `collect()` renders `TimestampType` as a naive datetime in the
  *process's* local time zone, while the `spark` fixture parses CSV strings in
  `spark.sql.session.timeZone=Asia/Tokyo`; `tests/conftest.py` therefore pins `TZ=Asia/Tokyo`
  for the test process (CI runners are UTC). Don't assume the host TZ in new tests.
- Kansai actuals: the current month's zip includes the *running day* (blank cells for future
  periods) — the loader drops files whose ファイル更新日 is not after their 対象年月日; a finalized
  day can also have blank cells (2025-10-12, 22 periods) → null measures; two CSV layouts
  (title line + `yyyymmdd` until 2025-12-24, TEPCO-shaped `yyyy/mm/dd` from 2025-12-25) and two
  member-name generations (`YYYYMMDD_jisseki.csv` → `jukyu_jisseki_YYYYMMDD_06.csv` from 2025-12).
- JMA MSM GRIB2: JMA packs many (element, forecast-hour) fields into one message envelope per
  archive file (216 fields in a single FH16-33 file) — ecCodes needs
  `codes_grib_multi_support_on()` (process-global, re-asserted per call) or it yields only the
  first field. RISH's TLS chain has served a stale intermediate since its leaf cert's
  2026-05-28 renewal (still so on 2026-09-06; the leaf runs to 2026-12-12); `requests`/certifi
  rejects it (browsers/curl tolerate it via AIA chasing). Since 2026-09-06 the missing G8
  intermediate is vendored at `power_market_analytics/certs/nii-open-domain-ca-g8-rsa.pem` and
  `msm.default_session()` — `MsmDownloader`'s default session — trusts it on top of certifi
  (partial-chain trust; `certifi` and `urllib3` are declared direct dependencies for it), so the
  download verifies with nothing to configure: in the devcontainer, host-side and under
  `just refresh-all`, which until then could not pass the MSM step because the manual
  `REQUESTS_CA_BUNDLE` workaround never reached the container. Drop both once RISH fixes the
  chain — the check is in `docs/JMA-MSM-GPV-Retrieval.md` §8.4 (§5.1 has the GRIB2 gotcha).
- Many-file raw reloads: every `CsvLoader` reads its files in a handful of Spark scans since
  2026-08-30 — positional layouts through `_scan_positional` (JMA hourly, TSO area actuals),
  header-based ones one scan per layout (JEPX, OCCTO, MSM: files grouped by the raw bytes of
  their first header line, each group's header judged once by Spark's own column names, and
  the scan reading with `enforceSchema=false`, under which Spark checks for every file that
  the contract's columns the scan resolves sit at the same positions under the same names as
  in the scan's schema and refuses, naming it, a file where they do not — nothing about the
  CSV dialect is judged in Python; the grouping (same first-line bytes; `multiLine` and
  non-ASCII-compatible charsets alone) is what keeps layouts apart, the check rules out
  misalignment should it ever fail; e-Stat groups by its known header lines), Python-parsed
  ones as one
  `createDataFrame` (でんき予報) — and validation errors name the offending files.
  Measured full reloads: JMA 1,608 files / 13.7 M rows ~50 s warm (~100 s cold, fine at
  `SPARK_DRIVER_MEMORY=4g`), TEPCO / Kansai ~1,600 daily files ~15 s / ~8 s, e-Stat 302 files
  / 0.94 M rows ~19 s, MSM 2,716 `csv.gz` / 9.7 M rows ~46 s (88 parquet files — Spark bin-packs
  the unsplittable gz files), JEPX 11 files ~13 s, OCCTO seconds. Before that the per-file
  `unionByName` default cost ~8 min of planning and a 45 MiB task binary per 1,600 files
  (~1 h 45 min per JMA load on a 20g driver), which is why the compose default is still
  `SPARK_DRIVER_MEMORY=20g` — no loader needs it any more; it is kept as headroom, sized per
  `.env.template`. An aborted overwrite leaves the old table intact.
- Stopping a host-side `just python <script>` (Ctrl-C / background-task stop) only kills the
  `docker exec` client — the script keeps running in the devcontainer. `just exec pkill -f
  <script>` and confirm with `just exec pgrep -fl <script>` before relaunching.
- JMA hourly: 積雪の深さ carries 現象なし情報 at staffed stations and is blank (not 0) with
  quality 1 when snow is untracked off-season; `wind_direction_quality_flag` is the one
  nullable flag (阿蘇山 s47821 post-closure padding rows, 2017-12-12..31) — don't tighten either.
- Feast's Spark offline store needs a **UTC** Spark session: the SQL it generates renders the
  entity timestamps as UTC string literals (`where available_at <= '2025-03-09T00:30:00'`) while
  comparing rows as instants, so any other session zone shifts the cutoff. The devcontainer's
  session is UTC (the warehouse's naive-JST convention is wall-clock values stored under it);
  `entity_frame` stamps the naive JST issue time as UTC and `historical_features` refuses a
  session or a frame in another zone. The test fixture runs in Asia/Tokyo, so the Feast tests
  switch the session to UTC for their duration (`utc_session`).
- Feast's as-of join hides a fact row published after the issue time, by design. In the
  TEPCO actuals two delivery days, 2022-12-08 and 2022-12-09, have a D-7 file re-published
  after 09:30 D-1 (the 2022-12-01 / 12-02 files, re-issued 2022-12-14), so `ftr_period_actuals`
  gives them no lag while the deleted class-based demand code read the final value: a run
  whose 730-day training window reaches December 2022 differs from it by those 96 training
  rows. The PR 6 reproduction therefore used `--train-start 2023-01-01`; no other row in the
  demand marts is published after its issue time (checked 2026-09-11).
- LightGBM's histogram bins move on last-bit feature differences. The weighted-mean marts
  (`wavg_temperature_c`, the `popw_*` forecast columns) equal the old pandas builders only to
  1.4e-14 (a different summation order), and PR 6's reproduction of the demand strategies had
  every feature value equal and every forecast different (MAE +0.14 % / +0.55 %); the same
  refit with both sides rounded to 9 decimals was identical to the digit, to 12 decimals
  not (a 1e-14 pair straddles a rounding boundary about once per 10^(d-14) values at d
  decimals). So two runs whose features differ at 1e-14 are not comparable period by
  period. Since 2026-09-11 the two marts add their terms in a fixed order (the
  `ordered_weighted_mean` macro, the dbt rule below), so a rebuild cannot move a model; the
  values still differ from the deleted pandas builders at 1e-14, which no longer matters.
- The `pma_ml` write-back tables (`forecasting.publish.create_run_partitioned_table`: the
  forecast, contribution, importance and similar-day tables) are created if absent and
  never altered, so a job that gains a column fails on an existing table with
  `INSERT_COLUMN_ARITY_MISMATCH`; drop the table (its rows are re-published by the next
  run) before the first run with the new column. Hit on 2026-09-11 when
  `pma_ml.similar_day` gained `similar_day_fit_through`.
- `scipy` is a declared dependency since 2026-09-05 (the similar-day weight fit uses
  `scipy.optimize.least_squares`); `scipy.*` is mypy-ignored like `shap.*`.
- `scikit-learn` is a declared dependency since 2026-09-08 (`sklearn.inspection.permutation_importance`;
  it was already installed through lightgbm / mlflow / shap); `sklearn.*` is mypy-ignored. Its
  `permutation_importance` needs a real estimator (`BaseEstimator` with `fit`), keeps row order
  on every scoring call (so positional routing to the refits is safe) and returns run-level
  scores only.

## Claude Code settings

- `permissions.allow` in `.claude/settings.json` is kept ASCII-sorted automatically by the
  SessionStart hook (`.claude/hooks/sort_permissions.py`) — no manual re-sorting needed; just
  keep new entries sorted when editing the file by hand.
- A PostToolUse hook runs `uv run ruff format` + `ruff check --fix` on every `.py` file you
  Edit/Write — the file may change right after your edit; re-Read before the next Edit if
  needed. Config: `pyproject.toml` (line length 100; rules E4/E7/E9/F/I only).
- Verification: `just test` runs the pytest suite (`tests/`; a local-Spark fixture, host-side —
  new Python should come with tests; the coverage gate is 100% locally and in the GitHub
  Actions `ci` workflow, and the `if __name__ == "__main__":` guard is the only excluded
  line). Validate data/model changes with `just dbt build`
  (contracts + tests; CI only runs `dbt parse`) and Python changes with `just lint` +
  `just mypy` (both also CI jobs); loaders/downloaders are
  also checked end-to-end by running their `scripts/` entry point in the devcontainer.
- Long-running ops (scrapes, raw reloads, `dbt build`) must run as main-session background
  Bash tasks — a subagent that backgrounds a job and ends its turn gets reaped with the job.

## Git conventions

- Branches follow [Conventional Branch](https://conventionalbranch.org/): `<type>/<description>`
  with `feature/` (new functionality), `fix/` (bug corrections), `hotfix/`, `release/vX.Y.Z`,
  `chore/` (docs, dependencies, config). Description = lowercase `a-z0-9` and single hyphens,
  issue number first when one exists (`feature/issue-123-…`). Use these purpose prefixes, not
  the spec's `claude/` AI-source prefix. Branches from before 2026-08-30 are bare kebab-case
  (`tepco-power-usage-curated`) — leave them.
- Commits follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):
  `type(scope): description`. Types `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`,
  `build`, `perf`, `style`; scope = the repo area (`dbt`, `dashboard`, `forecasting`, `demand`,
  `spot-price`, a source — `jma`, `msm`, `tepco`, `kansai`, `occto`, `estat`, `jepx` —
  `justfile`, `docs`); description lowercase, imperative, no trailing period, Japanese dataset
  names welcome (`feat(tepco): std + curated models for the でんき予報 hourly series`).
  `!` after the type/scope plus a `BREAKING CHANGE:` footer when a curated model's contract or
  grain changes incompatibly. Footers are git trailers (`Co-Authored-By: …` last). Commit type
  and branch type agree: `feature/` ↔ `feat`, `fix/` ↔ `fix`, `chore/` ↔ `chore`/`docs`/`ci`/
  `build`. PR titles use the same `type(scope): description` form (PRs are merged with merge
  commits, so the title is not itself a commit).

## Code review (pull requests)

- Every PR — docs-only ones included — is reviewed by **Codex** before it is merged. Codex is
  the only reviewer since 2026-09-06, when the researcher dropped the Copilot step that used
  to follow it: never request a Copilot review. Claude drives the loop and never merges on its
  own initiative — the researcher merges, or explicitly asks Claude to (then through
  `merge-async`, below). Open it with `gh pr create`
  (title `type(scope): description`; body sections *Why* / *What* / *Proof* with the measured
  numbers), then `gh pr edit <n> --add-assignee hankehly --add-label <labels>`. Labels are
  GitHub's defaults, mapped from the branch type: `fix/` and `hotfix/` → `bug`, `feature/` →
  `enhancement`, `chore/` → `documentation`, `release/` → no label; add `documentation` next
  to `bug`/`enhancement` when the PR also changes docs. A stage that depends on an unmerged PR
  is stacked on that branch (`--base <branch>`); GitHub retargets it to `main` when the base
  merges.
- **Never spell out the Codex mention** — the bot's handle followed by `review` — in a PR
  body, a commit message, a review reply or a file that will show up in a diff: Codex acts on
  that literal text wherever it appears on the PR and, anywhere but a plain PR comment,
  answers "To use Codex here, create an environment for this repo" instead of reviewing
  (#20 lost its creation-time review to a mention in the PR body). This file therefore only
  describes the trigger.
- **Codex** (`chatgpt-codex-connector[bot]`,
  [docs](https://learn.chatgpt.com/docs/third-party/github)) reviews automatically when a PR is
  opened and when a commit is pushed to an open PR: it reacts 👀 on the PR when it starts, then
  either posts a PR review whose body contains `Codex Review` (it begins with a newline, so
  match anywhere, never with a prefix test) and carries inline findings, or — nothing to
  flag — reacts 👍 on the PR and posts nothing. A bot review with an
  empty body (its only comment being the "create an environment" text) is a mention response,
  not a review — ignore it. Wait for one of the two real outcomes **with no timeout**: a
  main-session background poll every 60 s of
  `gh api --method GET -F per_page=100 repos/hankehly/power-market-analytics/pulls/<n>/reviews`
  (a `Codex Review` by the bot with `submitted_at` after the push you are waiting on),
  `… issues/<n>/reactions` (`content == "+1"` by the bot with `created_at` after it — Codex
  withdraws its 👍 and reacts afresh on every push, as seen on #23, so a clean re-run does get a
  newer timestamp; should a stale one ever linger, the 20-min fallback below hands the run a
  fresh subject — the trigger comment — whose reactions are per-run; a 👍 never counts for a
  later push),
  `… pulls/<n>/comments` (the inline findings themselves — id, path, line, body — which is
  what the replies endpoint needs) and `… issues/<n>/comments` (bot comments). `--method GET`
  is mandatory: a `-F` field alone turns
  `gh api` into a POST that tries to *create* a review / reaction / comment (a `body`-less
  attempt fails with 422, but it is still the wrong request). Use `--paginate` should a PR ever
  outgrow 100 items. Only when 20 min pass with neither 👀 nor a review, post a PR comment
  consisting solely of the manual trigger and keep waiting (a mention-response comment alone is
  never a reason — on #20 one arrived while an automatic run was already 👀); its reactions
  land on that comment
  (`… issues/comments/<id>/reactions`), so poll it too. Never post it while an automatic run
  may still be in flight: two runs of the same SHA race, and a 👍 from one would advance the
  loop before the other posts findings. Never conclude "no findings" from silence. A bot
  *issue comment* reading "You have reached your Codex usage limits for code reviews" is the
  third, terminal outcome of a run: no review is coming for that SHA. Stop the poll and tell the
  researcher — waiting for the reset, adding credits (the Codex usage dashboard) or merging
  the PR unreviewed is their call, not Claude's. Once credits are back, that SHA's
  automatic run is spent, so post the manual trigger and wait as above (#24, 2026-08-30).
- **Address every finding**: fix it in a commit, or reply with the reason it is not being
  changed — check a finding's premise against the *installed* versions before coding for it
  (`strings` on the Spark jar, a local-session probe, a measurement: #24's `skipRows` finding
  named an option pyspark 4.1.1 does not have, and its "per-file fallback" cost measured at
  56 ms per file), and put that evidence in the reply. The **third finding of the same defect
  class** on a PR is a signal to stop patching corners and restate the design as a closed rule
  (or ask the researcher whether the class is in scope): #24 took eleven rounds, one CSV-dialect
  corner each, on a Python header preflight that a Spark-verified design made unnecessary
  (spec `docs/superpowers/specs/2026-08-30-csv-loader-spark-verified-header-groups-design.md`);
  reply in the thread (`gh api repos/hankehly/power-market-analytics/pulls/<n>/comments/<id>/replies
  -f body='…'`, without the mention) with what changed, then **resolve the thread** —
  `gh api graphql` mutation `resolveReviewThread(input: {threadId: "…"})`, thread ids from the
  PR's `reviewThreads(first: 100) { nodes { id isResolved comments(first: 1) { nodes {
  databaseId } } } pageInfo { hasNextPage endCursor } }` query (page with `after:` beyond
  100 — GraphQL connections need a bound). Push if anything changed and wait for the automatic re-review as above; a round whose
  findings were all rebutted has nothing to push and is terminal once every thread is resolved
  (the reviewed SHA is unchanged). Repeat until a round ends with 👍 or with only rebutted,
  resolved findings.
- Then report the PR as ready — CI green, Codex clean, Proof filled in — and stop; the
  researcher merges unless they have explicitly asked Claude to. The repository's required
  checks must pass on the PR's *current* head, so a branch that has fallen behind `main` is
  brought up to date first — merge `main` into it (never rebase a reviewed branch), push, and
  take that new head through the whole loop again (Codex, CI green) before
  declaring it ready: every push is a new SHA to review. Stacked PRs are
  merged bottom-up through `PUT …/pulls/<n>/merge-async` (GitHub refuses the plain merge for a
  stack); deleting each merged branch retargets the next PR to `main`.

## Dimensional Modeling

- For anything dimensional-modeling related (fact/dimension table design, grain declarations,
  star schemas, SCDs, etc.), abide by the guidelines in
  [docs/Kimball-Dimensional-Modeling-Techniques.md](docs/Kimball-Dimensional-Modeling-Techniques.md).

## Forecasting Research

- Research is organised per task under `docs/research/<task>/` (`spot_price`, `demand` —
  same names as `tasks/<task>/` and the MLflow experiments), each with `README.md` (task index
  + scope defaults), `observations.md` and `assets/`; shared conventions live in
  [docs/research/README.md](docs/research/README.md). Record notable forecast behavior in the
  task's `observations.md`; copy [docs/research/investigation-template.md](docs/research/investigation-template.md)
  into the task folder for coherent forecasting questions and their experiments.
- IDs (`O-XXX`, `R-XXX`) are numbered per task — qualify them outside their folder
  (`spot_price/R-001`, `docs/research/spot_price/R-001-…md`).
- Do not generate hypotheses, explanations, or initial ideas for the research log unless the
  researcher explicitly asks; record the researcher's thinking faithfully.
- Update the investigation index in the task's `README.md`.
- Docs links are docsify site-root-relative (`research/spot_price/observations.md#o-001-…`);
  image paths are page-relative (`assets/…`).
- Keep reasoning, interpretations, and decisions in the research documents; keep run-level
  parameters, metrics, code versions, and detailed artifacts in MLflow.

## dbt

- Every dbt model must have an enforced contract
  (`config: contract: enforced: true` with a `data_type` for every column).
- Every dbt model must have a uniqueness test on its primary key column(s):
  `unique` for a single column, `dbt_utils.unique_combination_of_columns` for
  composite keys.
- Every standardized model of a source that feeds features carries `available_at`
  (naive JST): when the row became public, computed there once from the source's
  publication column or a documented bound (the rule and its evidence are in the
  model's YAML; `docs/superpowers/plans/2026-09-10-available-at-standardized.md`
  lists them with the measured lags). The curated facts built from those models pass
  the column through, and a model that joins several inputs takes `greatest()`.
  Forecast write-backs expose `forecast_issued_ts` under the same name.
- A weighted mean in a feature mart is added in a fixed order through the
  `ordered_weighted_mean` macro (`dbt/macros/`: an `array_sort`ed `collect_list` of
  `named_struct(key, weight, value)` folded with `aggregate()`), never with a plain `sum()`,
  so a rebuild gives the same value to the bit (since 2026-09-11; `ftr_hour_jma_obs` in lag
  order, `ftr_hour_msm` in station order).
- A feature a Python job fits and scores (spec §5 Form B; the similar day) is written
  back to `pma_ml.<feature>` (partitioned by `run_id`, with `available_at` = the latest of
  every input of the row and the cutoff of the fit that scored it, and `published_at`),
  read by a guarded staging model, and passed through by a mart with one row per scoring
  run and `published_at` next to `available_at` (the view generator turns that column into
  Feast's `created_timestamp_column`, so the newest published run wins among rows tied on
  `available_at`). The job walks forward, each fit at a cutoff on a trailing window of the
  data public by then,
  so no row is scored with a fit that saw anything published after the row's issue time; a
  backtest can then start anywhere.

## Writing style (specs, research docs, PR bodies, replies)

The researcher's preferences, stated 2026-09-05 while reviewing the R-005 spec.

- Plain language: short sentences, one idea each; everyday words where the meaning survives
  ("no gaps" over "gapless", "can be scored" over "selectable"); an aside gets its own
  sentence or is cut.
- Short: a decision is one to three lines; a justification appears once, where it belongs.
  Shortening never drops a decision, number, name or formula — plainer, not vaguer.

## Docstrings

- Always use NumPy-style docstrings
  (`Parameters` / `Returns` / `Raises` sections with the underlined-header format).

## Pandas DataFrame Core Rules

### Use domain wrappers

**Pattern**
- One wrapper class per DF "type" (e.g., `Orders`, `Entries`, `DailyKpis`).
- The wrapper class owns the contract: **schema + grain + guarantees**.
- Construct wrappers only via a validated `from_df(df)` (strict) constructor.
- Wrapper surface area:
  - `.df` (underlying DataFrame; treat as read-only in shared code)
  - metadata: `.grain`, `.keys`, `.schema_name`
  - domain methods for common transforms (avoid free-form mutation outside)

**Rules**
- Functions should accept/return wrappers (not raw `pd.DataFrame`) for domain concepts.
- Inside functions, it's OK to unwrap to `.df` for pandas ops—return a wrapper again.
- Avoid in-place mutation of `.df` in shared/app code; prefer returning a new wrapper.

### Validate at boundaries (ingress/egress + major transforms)
Validate schema + guarantees:
- after reading external data (DB/files/APIs)
- before/after joins
- at entry to business-critical functions (unless wrapper construction guarantees it)
- before writing/publishing

Validation must check (at minimum):
- required columns present
- dtypes as expected
- key columns: no unexpected nulls
- grain key uniqueness (if required)
- category/value constraints (when relevant)

Fail fast with clear error messages.

### Prefer explicit, small transforms (predictable outputs)
- Keep transforms small, named, and single-purpose.
- Functions must return predictable DF “types” (wrappers), not “whatever columns happen to exist.”
- Avoid in-place mutation in application/shared code; prefer `.assign(...)`, `.pipe(...)`, and returning new objects.

### Standardize joins (schema drift hot-spot)
- Every `merge` must specify `how=` and join keys explicitly (`on=` or `left_on/right_on`).
- Set `validate=` (`one_to_one`, `one_to_many`, etc.) whenever possible.
- Control suffixes explicitly and rename columns back to canonical names.
- After merge, validate:
  - row count sanity (if expected)
  - grain key uniqueness (if required)
  - no unexpected nulls in keys

### Use column sets instead of ad-hoc strings
- Use predefined `KEY_COLS`, `DIM_COLS`, `FACT_COLS` (and other groups) for selects, merges, and outputs.
- Avoid copying/pasting raw column name lists across modules.

### Log compact schema diagnostics at key points
At major pipeline steps, log:
- `df.shape`
- key column null counts
- a compact schema summary (columns + dtypes)
