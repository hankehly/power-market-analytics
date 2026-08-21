# CLAUDE.md

## Commands

- `just refresh-jepx` — JEPX refresh: download JEPX CSVs + holidays, reload `raw`, `dbt build` (models + tests).
- `just refresh-jma` — JMA weather refresh: regenerate the station seed (~5 min, staffed
  stations only), download stitched 7-element hourly CSVs (args pass through, e.g.
  `--prefecture 44`; no args = all ~159 staffed stations, ~14 h cold), reload `raw`,
  `dbt build`.
- `just refresh-occto` — OCCTO 翌々日 refresh, two datasets: the demand-forecast CSV (~700 KB,
  3 HTTP calls) and the half-hourly area reserve-rate CSV (~20 MB/yr, fetched in 300-day windows
  because the portal caps a download at 150,000 rows), reload `raw`, `dbt build`.
- `just refresh-tepco` — TEPCO Tokyo-area demand/generation actuals refresh: redownload every
  monthly archive (`AREA_YYYYMM.zip`, 2022-04 → now, ~5 MB total), reload `raw`, `dbt build`.
- `just refresh-kansai` — same for 関西電力送配電's Kansai-area actuals (`YYYYMM_jisseki.zip`,
  2022-04 → now, ~2 MB total), reload `raw`, `dbt build`.
- `just refresh-estat [args]` — e-Stat census 500 m population mesh: download every configured
  census vintage (2015 `T000847`, 2020 `T001101` JGD2000; 151 primary-mesh zips each, cached —
  args pass through, e.g. `--years 2020`, `--force`; a cold run is ~50 min because e-Stat generates
  each archive in ~10 s), reload `raw`, `dbt build`.
- `just test [pytest args]` — Python unit tests (host-side pytest, ~1 min) with a `pytest-cov`
  term-missing report over `power_market_analytics/` + `scripts/` (config in `pyproject.toml`
  `[tool.coverage.*]`; gated at 100% via `fail_under`, so a partial suite fails locally and in
  CI — `.github/workflows/ci.yml` runs the same command on every push). Shared fixtures in
  `tests/conftest.py`: `spark` (local session, temp warehouse, no metastore),
  `curated_warehouse` (synthetic `pma_curated` star for the spot-price task), an autouse temp
  MLflow file store, and a session-wide single-thread LightGBM cap. HTTP is never real: the
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
- `just sql` — beeline shell on the thriftserver.
- `just python scripts/spot_price_backtest.py --strategy lightgbm --area tokyo` — day-ahead
  backtest (strategies: `previous_day`, `lightgbm`, `lightgbm_occto`; areas =
  `dim_area.area_code`). Logs to MLflow (`just open mlflow`) and publishes forecasts to the
  warehouse. `--start-date/--end-date` pin the evaluation window and `--train-start` the
  first training row — set all three identically for a feature experiment and its matched
  baseline. New strategies subclass `ForecastStrategy`, register in `STRATEGIES`, and get
  their inputs wired in `build_strategy`
  (`power_market_analytics/tasks/spot_price/strategies/__init__.py`).
- `just python scripts/compare_spot_price_runs.py --baseline <run_id> --candidate <run_id>` —
  matched two-run comparison (MAE overall / by day part / near the OCCTO peak hour / by
  month / high-price days, plus bias) as markdown; needs
  `just dbt build --select +fct_spot_price_forecast_accuracy` after the runs.
- `just python scripts/demand_backtest.py --strategy lightgbm --area tokyo` — day-ahead area
  demand backtest (strategies: `lightgbm`; areas: `tokyo`, `kansai` = the TSO feeds loaded into
  `fct_area_demand_generation_actual`); each area also needs its representative JMA station's
  hourly weather loaded and current (`dim_area.representative_jma_station_id`: 東京 s47662,
  大阪 s47772 — both loaded and current as of the 2026-08-20 re-scope backfill; keep them fresh
  with `just refresh-jma`, since a stale window's last days are skipped for lack of a
  temperature window). Same flags as the spot script
  (`--days` defaults to 365); logs to the MLflow experiment `demand`, publishes to
  `pma_ml.demand_forecast`, then `just dbt build --select +fct_demand_forecast_accuracy`.
- `just python scripts/create_forecast_dashboard.py [--task spot_price|demand]` — (re)build the
  Superset forecast-analysis dashboards from the repo (idempotent; no `--task` = all): per task a
  `DashboardSpec` (dataset SQL, unit, formats, band/calibration columns) drives one shared set of
  chart/layout builders; charts are matched by name *within their dataset*, so both dashboards
  share chart names. Rerun after `docker compose down -v` or after editing a spec.
- Host-side dbt also works: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`.
- Anything that creates a SparkSession MUST run in the devcontainer (metastore/warehouse only
  resolve on the compose network); plain python and dbt work from the host too.

## Architecture (data flow)

- JEPX CSVs: `scripts/download_jepx_spot.py` → `data/jepx/spot/` (gitignored) →
  `scripts/load_jepx_spot.py` (`CsvLoader`, load contract in `conf/schemas/jepx_spot.yaml`) → `pma_raw.jepx_spot`.
- JMA weather CSVs (staffed stations only, since the 2026-08 re-scope):
  `scripts/download_jma_hourly_all.py` (per-station: `download_jma_hourly.py`) →
  `data/jma/hourly/` → `scripts/load_jma_hourly.py` (`JmaHourlyCsvLoader`, positional
  contract `conf/schemas/jma_hourly_staffed.yaml`, 27 columns) →
  `pma_raw.jma_hourly_staffed` only (over-budget station-years are fetched as 2 request
  windows and stitched into one file; 均質番号 resets per window, so a stitched year file
  resets it at the mid-year boundary). Station master:
  `scripts/update_jma_stations_seed.py` (`staffed_only=True`) → seed `jma_stations` →
  `dim_jma_station`. Protocol + CSV format:
  [docs/JMA-Weather-Data-Retrieval.md](docs/JMA-Weather-Data-Retrieval.md).
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
  accepted header lines, `archive_includes_current_day`) — always re-downloads every monthly zip
  and extracts only the daily 実績 members; the loader reads positionally, sniffs the metadata
  line for `file_updated_at`, normalises `yyyy/mm/dd` dates and skips not-yet-final files.
  - TEPCO / Tokyo: `power_market_analytics/tepco.py` (`TEPCO`, `TepcoAreaDownloader`) →
    `scripts/download_tepco_area_demand_generation.py` → `data/tepco/area_demand_generation/{zip,csv}/`
    → `scripts/load_tepco_area_demand_generation.py` (`TepcoAreaCsvLoader`, contract
    `conf/schemas/tepco_area_demand_generation_actual.yaml`) → `pma_raw.tepco_area_demand_generation_actual`
    → `stg/std_tepco__area_demand_generation_actual`. Format + quirks:
    [docs/TEPCO-Area-Demand-Generation-Retrieval.md](docs/TEPCO-Area-Demand-Generation-Retrieval.md).
  - 関西電力送配電 / Kansai: `power_market_analytics/kansai.py` (`KANSAI`, `KansaiAreaDownloader`) →
    `scripts/download_kansai_area_demand_generation.py` → `data/kansai/area_demand_generation/{zip,csv}/`
    → `scripts/load_kansai_area_demand_generation.py` (`KansaiAreaCsvLoader`, contract
    `conf/schemas/kansai_area_demand_generation_actual.yaml`, nullable bigint measures) →
    `pma_raw.kansai_area_demand_generation_actual` → `stg/std_kansai__area_demand_generation_actual`.
    Format (two layouts, switch 2025-12-25) + quirks:
    [docs/Kansai-Area-Demand-Generation-Retrieval.md](docs/Kansai-Area-Demand-Generation-Retrieval.md).
  - Curated: `fct_area_demand_generation_actual` = `union all` of the `std_<tso>__…` models joined
    to `dim_area` (grain date × time_code × area; joins `fct_jepx_spot_area_price` 1:1). Adding a
    TSO = new spec + contract + stg/std models + one union branch.
- e-Stat census 500 m population mesh (国勢調査 4次メッシュ, one CP932 text file per 第１次地域区画):
  `scripts/download_estat_census_population_mesh.py` (`EstatCensusMeshDownloader` in
  `power_market_analytics/estat.py`; per-vintage `CensusVintage` config in `VINTAGES` — stats id,
  population column, census date, datum, listing URL, expected file count; the listing rows come
  from the `search_detail` JSON endpoint, not the HTML page; zips validated before caching, member
  extracted byte-for-byte) → `data/estat/census_population_mesh/{year}/{zip,txt}/` →
  `scripts/load_estat_census_population_mesh.py` (`EstatCensusMeshCsvLoader` in `estat_loader.py`:
  vintage from the file name, selects that vintage's population column, injects vintage attributes,
  validates mesh codes / population / HTKSYORI before casting; contract
  `conf/schemas/estat_census_population_mesh.yaml`) → `pma_raw.estat_census_population_mesh` →
  `stg_estat__census_population_mesh` → `std_estat__census_population_mesh` (+ bounding box /
  centroid decoded from the mesh code; Python reference `estat.decode_mesh_code`) →
  `dim_population_mesh_500m` (one row per mesh across vintages) + `fct_census_population_mesh`
  (`census_year × mesh_code`, `population_total` as published at every mesh — the 秘匿処理 folds only
  the `*`-suppressed detail columns, never the total; additive across meshes, not across years; no
  weights). Adding a census = one `VINTAGES` entry + fixtures + the singular dbt test's year list.
  Protocol + format: [docs/eStat-Census-Population-Mesh-Retrieval.md](docs/eStat-Census-Population-Mesh-Retrieval.md).
- dbt (`dbt/`): sources in `models/raw/<source>.yml` → `staging` (as-is) → `standardized`
  (typed time axis) → `curated` (Kimball star: `dim_*`, `fct_*`). Schemas: `pma_<layer>`.
- Japanese holidays: Cabinet Office CSV → `scripts/update_holidays_seed.py` → seed → `dim_date`
  (spine end derives from the seed's max year).
- Forecast write-back: `scripts/spot_price_backtest.py` logs the run to MLflow AND publishes
  row-level forecasts (`forecasting/publish.py`) to `pma_ml.spot_price_forecast`
  (parquet, partitioned by `run_id`, dynamic partition overwrite = idempotent per run) →
  `stg/std_ml__spot_price_forecast` → `fct_spot_price_forecast` →
  `fct_spot_price_forecast_accuracy` (joins actuals; the Superset-facing surface, read by the
  **Spot Price Forecast Analysis** dashboard via the `spot_price_forecast_analysis` dataset).
  `run_id` links warehouse rows to the MLflow run; the run's `warehouse_table` tag points back.
  `tasks/spot_price/compare.py` reads the accuracy fact back for run-vs-run segment tables.
- Exogenous features: `LightGbmOcctoStrategy` joins `OcctoDemandForecast`
  (`datasets.load_occto_demand_forecast`, from `fct_occto_demand_supply_forecast_daily`) to each
  delivery day's rows via the `_join_daily_features` hook; its training set therefore
  starts 2024-04-01, so a matched `lightgbm` baseline needs `--train-start 2024-04-01`.
- Modeling tasks live under `power_market_analytics/tasks/<task>/` (`spot_price`, `demand`),
  each a thin configuration of the shared framework `power_market_analytics/forecasting/`:
  a frozen `TaskSpec` in the task's `__init__.py` (name = MLflow experiment, unit,
  `history_lead_days`, `issue_offset`, `forecast_table`, the task's four frame classes),
  frames as two-line subclasses of `forecasting.frames` (`HalfHourlySeries` / `DayAheadForecast`
  / `BacktestResult` / `ForecastRecords`, schema assembled from `value_col` /
  `forecast_col` / `actual_col`), `forecasting.backtest.run_backtest` (history the strategy
  sees = days ≤ `task.history_cutoff(D)`; a `ForecastUnavailableError` skips the day and is
  reported on `BacktestRun.skipped_days`; forecast points without an actual are dropped),
  `forecasting.lgbm.SlidingWindowLightGbmStrategy` (subclass sets `task`, `feature_cols`,
  `eval_set_cls`, `lookback_days`, implements `_add_features`), `forecasting.publish`
  and `forecasting.plots`. Adding a task = TaskSpec + frames + datasets + strategies +
  script + `pma_ml.<task>_forecast` dbt models.
- Demand task (`tasks/demand/`): at 09:30 JST on D-1 forecast the 48 half-hourly `demand_kwh`
  of `fct_area_demand_generation_actual` for D; usable history = days ≤ D-2
  (`history_lead_days = 2`, TSO files finalise after midnight). `lightgbm` features =
  `time_code, month, day_of_week, wavg_temperature_c, lag_7d_demand_kwh`;
  `wavg_temperature_c` = same-hour temperature at the area's representative JMA station
  (`dim_area.representative_jma_station_id`, seed `jepx_areas`; hour containing the period =
  `(time_code + 1) // 2`) over D-8..D-2, weights halving per day back (`demand/features.py`).
  Null-demand rows (TSO holes) are dropped at load; a target day whose D-7 lag falls in a hole
  is skipped. Write-back: `pma_ml.demand_forecast` → `stg/std_ml__demand_forecast` →
  `fct_demand_forecast` → `fct_demand_forecast_accuracy` → Superset **Demand Forecast Analysis**
  dashboard (dataset `demand_forecast_analysis`; kWh with SI number formats, 2-GWh actual-demand
  bands, calibration x = actual rounded to 1 GWh).

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
  (contracts + tests) and Python changes with `just lint` + `just mypy` (both also CI
  jobs); loaders/downloaders are
  also checked end-to-end by running their `scripts/` entry point in the devcontainer.

## Dimensional Modeling

- For anything dimensional-modeling related (fact/dimension table design, grain declarations,
  star schemas, SCDs, etc.), abide by the guidelines in
  [docs/Kimball-Dimensional-Modeling-Techniques.md](docs/Kimball-Dimensional-Modeling-Techniques.md).

## Forecasting Research

- Use [docs/research/observations.md](docs/research/observations.md) to record notable forecast
  behavior and [docs/research/investigation-template.md](docs/research/investigation-template.md)
  for coherent forecasting questions and their experiments.
- Do not generate hypotheses, explanations, or initial ideas for the research log unless the
  researcher explicitly asks; record the researcher's thinking faithfully.
- Update the investigation index in [docs/research/README.md](docs/research/README.md).
- Keep reasoning, interpretations, and decisions in the research documents; keep run-level
  parameters, metrics, code versions, and detailed artifacts in MLflow.

## dbt

- Every dbt model must have an enforced contract
  (`config: contract: enforced: true` with a `data_type` for every column).
- Every dbt model must have a uniqueness test on its primary key column(s):
  `unique` for a single column, `dbt_utils.unique_combination_of_columns` for
  composite keys.

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
