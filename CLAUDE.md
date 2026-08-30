# CLAUDE.md

## Commands

- `just refresh-jepx` — JEPX refresh: download JEPX CSVs + holidays, reload `raw`, `dbt build` (models + tests).
- `just refresh-jma` — JMA weather refresh: regenerate the station seed (~5 min, staffed
  stations inside JEPX areas only), download stitched 7-element hourly CSVs (args pass
  through, e.g. `--prefecture 44`; no args = all ~149 staffed stations, ~13.5 h cold),
  reload `raw`, `dbt build`. `--request-interval 3` is proven (2026-08-20 full backfill,
  ~3,100 requests, zero 429s, ~6 h); 2 s spacing draws 429s.
- `just refresh-occto` — OCCTO 翌々日 refresh, two datasets: the demand-forecast CSV (~700 KB,
  3 HTTP calls) and the half-hourly area reserve-rate CSV (~20 MB/yr, fetched in 300-day windows
  because the portal caps a download at 150,000 rows), reload `raw`, `dbt build`.
- `just refresh-tepco-area-demand-generation` — TEPCO Tokyo-area demand/generation actuals
  refresh: redownload every monthly archive (`AREA_YYYYMM.zip`, 2022-04 → now, ~5 MB total),
  reload `raw`, `dbt build`. `just refresh-tepco` is the umbrella: both TEPCO datasets (this one
  and `refresh-tepco-power-usage` below) reloaded, then one `dbt build`.
- `just refresh-kansai` — same for 関西電力送配電's Kansai-area actuals (`YYYYMM_jisseki.zip`,
  2022-04 → now, ~2 MB total), reload `raw`, `dbt build`.
- `just refresh-tepco-power-usage` — TEPCO でんき予報 hourly 電力使用実績 refresh: fetch the
  yearly `juyo-YYYY.csv` files (2016 … 2022, cached; `--force-yearly` passes through) and
  redownload every monthly `YYYYMM_power_usage.zip` (2022-04 → now, ~4 MB), reload `raw`,
  `dbt build`.
- `just refresh-estat [args]` — e-Stat census 500 m population mesh: download every configured
  census vintage (2015 `T000847`, 2020 `T001101` JGD2000; 151 primary-mesh zips each, cached —
  args pass through, e.g. `--years 2020`, `--force`; a cold run is ~50 min because e-Stat generates
  each archive in ~10 s), reload `raw`, `dbt build`.
- `just refresh-msm [args]` — JMA MSM GPV surface-forecast refresh: for each delivery day D,
  download + decode the three RISH GRIB2 files covering the 12 UTC D-2 run (args pass through,
  e.g. `--start-date`, `--force`, `--keep-grib`; ~157 MB per delivery day, ~54 GiB/yr), reload
  `raw`, `dbt build`. Needs a devcontainer image rebuild (`docker compose build devcontainer`)
  for the eccodes dependency before it can run in-container — the load step too, since
  `power_market_analytics/msm.py` imports eccodes at module level; see
  [docs/JMA-MSM-GPV-Retrieval.md](docs/JMA-MSM-GPV-Retrieval.md) §8.
- `just refresh-all` — every source in one go: runs each `ingest-<source>` (the private
  download + reload-`raw` half of the matching `refresh-<source>`, with its defaults — no args
  forwarded) in the order JEPX, JMA, OCCTO, TEPCO, Kansai, e-Stat, MSM (JMA before MSM: the MSM
  downloader reads the station seed), then a single `dbt build`. Warm caches make it ~1.5 h,
  dominated by JMA re-fetching every station's current-year file; a failing step aborts before
  the build. `just ingest-<source>` also works on its own for a build-free reload.
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
  warehouse (and, for the LightGBM strategies, their TreeSHAP contributions to
  `pma_ml.spot_price_forecast_contribution` — build `+fct_spot_price_forecast_accuracy
  +fct_spot_price_forecast_contribution` afterwards). `--start-date/--end-date` pin the
  first training row — set all three identically for a feature experiment and its matched
  baseline. New strategies subclass `ForecastStrategy`, register in `STRATEGIES`, and get
  their inputs wired in `build_strategy`
  (`power_market_analytics/tasks/spot_price/strategies/__init__.py`).
- `just python scripts/compare_spot_price_runs.py --baseline <run_id> --candidate <run_id>` —
  matched two-run comparison (MAE overall / by day part / near the OCCTO peak hour / by
  month / high-price days, plus bias) as markdown; needs
  `just dbt build --select +fct_spot_price_forecast_accuracy` after the runs.
- `just python scripts/demand_backtest.py --strategy lightgbm_msm_popw_daytype --area tokyo` —
  day-ahead area demand backtest (strategies: `lightgbm`, `lightgbm_msm`, `lightgbm_msm_popw`,
  `lightgbm_msm_popw_daytype` = `lightgbm_msm_popw` + the `dim_date` day-type categorical, the
  default and the kept demand baseline since demand/R-003, 2026-08-26; areas: `tokyo`,
  `kansai` = the TSO feeds loaded into `fct_area_demand_generation_actual`); each area also needs its
  representative JMA station's hourly weather loaded and current
  (`dim_area.representative_jma_station_id`: 東京 s47662, 大阪 s47772 — both loaded and current
  as of the 2026-08-20 re-scope backfill; keep them fresh with `just refresh-jma`, since a
  stale window's last days are skipped for lack of a temperature window), and `lightgbm_msm`
  needs that station's MSM forecast in `fct_jma_msm_weather_forecast_hourly` (`just
  refresh-msm`; a delivery day without a forecast is skipped) and `lightgbm_msm_popw` the MSM
  forecasts of all the area's weighted stations plus `fct_census_population_jma_station`. Same
  flags as the spot script
  (`--days` defaults to 365); logs to the MLflow experiment `demand`, publishes to
  `pma_ml.demand_forecast`, then `just dbt build --select +fct_demand_forecast_accuracy
  +fct_demand_forecast_contribution` (the second selector materialises the run's TreeSHAP
  contributions for the dashboard's Explanation (SHAP) tab; the first is what its Run filter
  reads).
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
  Each dashboard has two virtual datasets — `<task>_forecast_analysis` (the accuracy mart) and
  `<task>_forecast_explanation` (`fct_<task>_forecast_contribution` joined to the accuracy mart:
  one row per period × component, so AVG-only metrics) — and two top-level tabs: **Accuracy**
  (KPI tiles, error structure, calibration & distribution, runs & drilldown) and
  **Explanation (SHAP)**, where a **Day** native filter (scoped to that tab; cascades from Run,
  defaults to the default run's last day; empty = the run's mean decomposition; every value is
  a mean per period) drives base / forecast / actual / net-effect tiles, a `waterfall` of the
  mean per-period feature contributions (the base is a tile, not a bar: Superset's value axis
  always includes zero; bars sort by label, hence the `00 base`, `01 time_code`…
  `component_label` prefix), the component table and stacked contributions by period. Runs
  published before 2026-08-26 have no contributions and show an empty tab until re-run. After a
  backtest run, both marts must be rebuilt before the dashboards make sense — `just dbt build
  --select +fct_<task>_forecast_accuracy +fct_<task>_forecast_contribution`;
  `+fct_<task>_forecast_contribution` alone does not refresh the accuracy mart (which the Run
  filter reads) nor the forecast fact the additivity test joins to, and the Run filter then never
  lists the new run. Clicking a date in **Worst days** (Accuracy tab) cross-filters the
  Explanation tab (and the 30-min detail chart) to that day — cross-filters persist across tabs,
  and it combines with the Day filter, so clear Day (or pick the same day) first.
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
  accepted header lines, `archive_includes_current_day`) — always re-downloads every monthly zip
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
- TEPCO でんき予報 過去の電力使用実績 (hourly Tokyo-area 電力使用状況, 1時間平均 in 万kW — the
  only public area demand before 2022-04; a different, unrevised display series from A-1):
  `power_market_analytics/tepco/power_usage.py` (`TEPCO_POWER_USAGE` spec,
  `TepcoPowerUsageDownloader` = yearly `juyo-YYYY.csv` 2016 … 2022 cached + monthly
  `YYYYMM_power_usage.zip` 2022-04 → now via the shared downloader, `parse_hourly`,
  `TepcoPowerUsageCsvLoader` — Python pre-parse of the multi-section daily files, hourly table
  only, yearly rows ≥ 2022-04-01 dropped so the daily files win) →
  `scripts/download_tepco_power_usage.py` → `data/tepco/power_usage/{zip,csv}/` →
  `scripts/load_tepco_power_usage.py` (contract `conf/schemas/tepco_power_usage_hourly.yaml`,
  grain date × hour_start 0–23) → `pma_raw.tepco_power_usage_hourly` →
  `stg_tepco__power_usage_hourly` → `std_tepco__power_usage_hourly` (typed hour axis:
  `hour_start` 0–23 as published + `hour_ending` 1–24, `delivery_datetime` = hour start, integer
  万kW; all four published measures kept, the daily-file 予測値 / 使用率 / 供給力 null before
  2022-04-01; `demand_mankw` tested ≥ 1 — no sentinel, TEPCO never re-issues a day; singular
  test `assert_std_tepco__power_usage_hourly_calendar_complete` = gapless from 2016-04-01) →
  `fct_area_power_usage_hourly` (grain `date_key × hour_of_day × area_key`, `demand_kwh` =
  万kW × 10,000 only — energy over the hour, the A-1 fact's unit; this series alone, not
  stitched with A-1). `hour_of_day` references `dim_delivery_hour`, the 24-row shrunken rollup
  of `dim_delivery_period` (built from it — `group by hour_of_day, is_daytime, day_part` —
  so `day_part` cannot diverge; `dim_delivery_period.hour_of_day` is the rollup FK), which is
  how the hourly fact and the 30-minute fact drill across: sum the 30-minute `demand_kwh`
  per `hour_of_day`. The daily files also carry a
  5-minute table (当日実績 + 太陽光, 2022-04 →) that is parsed past, not loaded. Format, quirks
  and the 4.4-year comparison with A-1 (incl. A-1's 18:00–19:00 defect since mid-2025):
  [docs/TEPCO-Power-Usage-Retrieval.md](docs/TEPCO-Power-Usage-Retrieval.md).
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
  (typed time axis) → `curated` (Kimball star: `dim_*`, `fct_*`). Schemas: `pma_<layer>`.
- Japanese holidays: Cabinet Office CSV → `scripts/update_holidays_seed.py` → seed → `dim_date`
  (spine end derives from the seed's max year). `dim_date.is_holiday` is the seed's 国民の祝日
  **plus** the customary non-working days computed in SQL — 年末年始 12/30–1/3, ゴールデンウィーク
  4/30–5/2 (the 休日 set every family-A TSO 託送供給等約款 uses; 東北/北陸/中国/沖縄 differ) and
  お盆 8/13–16 (convention only) — so `is_business_day` means "working day", not "banks open".
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
  `eval_set_cls`, `lookback_days`, optionally
  `categorical_feature_cols` — passed to `LGBMRegressor.fit(categorical_feature=…)` and logged as
  `lgbm_categorical_feature_cols` — implements `_add_features`), `forecasting.publish`
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
  is skipped. `lightgbm_msm` (`LightGbmMsmStrategy`, research `demand/R-001`) = `lightgbm` +
  `forecast_temperature_c`: the MSM point forecast for D at the same station
  (`fct_jma_msm_weather_forecast_hourly`, the D-2 12 UTC vintage, `AreaTemperatureForecast` frame
  keyed `trade_date × hour_ending`, joined at the hour containing the period); training rows
  without a forecast are dropped, so its training set starts on the first MSM day (2022-04-01 —
  the start of the demand history too, so a matched `lightgbm` baseline needs no `--train-start`
  today). `lightgbm_msm_popw` (`LightGbmMsmPopWeightedStrategy`, research `demand/R-002`) swaps
  that feature for `popw_forecast_temperature_c`: the same MSM forecast averaged over the area's
  staffed stations with `fct_census_population_jma_station` weights (latest census vintage,
  logged as `population_weight_census_year`; `load_area_temperature_forecast_population_weighted`
  renormalises over the stations that have a value for the hour). The observed
  `wavg_temperature_c` stays single-station in both.
  `lightgbm_msm_popw_daytype` (`LightGbmMsmPopWeightedDayTypeStrategy`, research `demand/R-003`; the
  demand baseline and script default since 2026-08-26) =
  `lightgbm_msm_popw` + `day_type`: 0 Weekday / 1 Weekend / 2 Holiday from `dim_date`
  (`is_holiday` wins over `is_weekend`, the compare script's day-type precedence; `load_day_types` →
  `DayTypeCalendar`, `join_day_type`), declared categorical via `categorical_feature_cols`; a delivery
  day outside `dim_date` is skipped. Write-back: `pma_ml.demand_forecast` →
  `stg/std_ml__demand_forecast` →
  `fct_demand_forecast` → `fct_demand_forecast_accuracy` → Superset **Demand Forecast Analysis**
  dashboard (dataset `demand_forecast_analysis`; the mart's kWh rescaled to MWh in the dataset
  SQL — `forecast_demand_mwh`, `error_mwh`, … — with plain `,.1f`/`,.0f` formats, 2,000-MWh
  actual-demand bands (`10000-12000`), calibration x = actual rounded to 1,000 MWh);
  contributions to `pma_ml.demand_forecast_contribution` → `fct_demand_forecast_contribution` →
  the dashboard's Explanation (SHAP) tab.

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
- JMA MSM GRIB2: JMA packs many (element, forecast-hour) fields into one message envelope per
  archive file (216 fields in a single FH16-33 file) — ecCodes needs
  `codes_grib_multi_support_on()` (process-global, re-asserted per call) or it yields only the
  first field. RISH's TLS chain has served a stale intermediate since its leaf cert's
  2026-05-28 renewal; `requests`/certifi rejects it (browsers/curl tolerate it via AIA chasing)
  — fetch the correct intermediate and pass a combined bundle via `REQUESTS_CA_BUNDLE` until
  RISH fixes it. Details: `docs/JMA-MSM-GPV-Retrieval.md` §5.1/§8.4.
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
  / 0.94 M rows ~19 s, MSM 1,606 `csv.gz` / 5.7 M rows ~61 s (52 parquet files — Spark bin-packs
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

- Every PR — docs-only ones included — is reviewed by **Codex, then Copilot**, before it is
  merged; Claude drives the loop and never merges on its own initiative — the researcher
  merges, or explicitly asks Claude to (then through `merge-async`, below). Open it with `gh pr create`
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
  researcher — waiting for the reset, adding credits (the Codex usage dashboard) or accepting
  the PR on Copilot alone is their call, not Claude's. Once credits are back, that SHA's
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
- **Copilot** only after Codex is clean: GitHub MCP `request_copilot_review` (CLI: `gh api -X
  POST repos/hankehly/power-market-analytics/pulls/<n>/requested_reviewers -f
  'reviewers[]=copilot-pull-request-reviewer[bot]'`). It posts a review as
  `copilot-pull-request-reviewer[bot]` within minutes — `APPROVED` (as on #19 and #21) or
  `COMMENTED`; either is clean once every inline thread it opened is resolved. Otherwise
  handle its comments like Codex's — fix or rebut, reply, resolve the thread with the same
  mutation. A pushed fix starts a new Codex run, so go back through the Codex wait for the
  new SHA first and only then re-request Copilot; a round in which every finding was rebutted
  has nothing to push and is terminal once its threads are resolved — do not re-request.
- Then report the PR as ready — CI green, both reviewers clean, Proof filled in — and stop; the
  researcher merges unless they have explicitly asked Claude to. The repository's required
  checks must pass on the PR's *current* head, so a branch that has fallen behind `main` is
  brought up to date first — merge `main` into it (never rebase a reviewed branch), push, and
  take that new head through the whole loop again (Codex, then Copilot, CI green) before
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
