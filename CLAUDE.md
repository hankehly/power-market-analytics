# CLAUDE.md

## Commands

- `just refresh-jepx` — JEPX refresh: download JEPX CSVs + holidays, reload `raw`, `dbt build` (models + tests).
- `just refresh-jma` — JMA weather refresh: regenerate the station seed (~5 min), download
  hourly CSVs (args pass through, e.g. `--prefecture 44`; no args = full network, ~60 h
  cold), reload `raw`, `dbt build`.
- `just refresh-occto` — OCCTO 翌々日 demand-forecast refresh: redownload the full-history CSV
  (~700 KB, 3 HTTP calls), reload `raw`, `dbt build`.
- `just refresh-tepco` — TEPCO Tokyo-area demand/generation actuals refresh: redownload every
  monthly archive (`AREA_YYYYMM.zip`, 2022-04 → now, ~5 MB total), reload `raw`, `dbt build`.
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
- Host-side dbt also works: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`.
- Anything that creates a SparkSession MUST run in the devcontainer (metastore/warehouse only
  resolve on the compose network); plain python and dbt work from the host too.

## Architecture (data flow)

- JEPX CSVs: `scripts/download_jepx_spot.py` → `data/jepx/spot/` (gitignored) →
  `scripts/load_jepx_spot.py` (`CsvLoader`, load contract in `conf/schemas/jepx_spot.yaml`) → `pma_raw.jepx_spot`.
- JMA weather CSVs: `scripts/download_jma_hourly_all.py` (per-station:
  `download_jma_hourly.py`) → `data/jma/hourly/` → `scripts/load_jma_hourly.py`
  (`JmaHourlyCsvLoader`, positional contracts in `conf/schemas/jma_hourly_*.yaml`, one per
  station-class layout) → `pma_raw.jma_hourly_amedas` / `pma_raw.jma_hourly_staffed`.
  Station master: `scripts/update_jma_stations_seed.py` → seed `jma_stations` →
  `dim_jma_station`. Protocol + CSV format:
  [docs/JMA-Weather-Data-Retrieval.md](docs/JMA-Weather-Data-Retrieval.md).
- OCCTO 翌々日 demand forecast: `scripts/download_occto_demand_forecast.py`
  (`OcctoBulkDownloader` in `power_market_analytics/occto.py`, always re-downloads the whole
  history) → `data/occto/demand_forecast_dad/` → `scripts/load_occto_demand_forecast.py`
  (`CsvLoader`, contract `conf/schemas/occto_demand_forecast_dad.yaml`) →
  `pma_raw.occto_demand_forecast_dad` → `stg/std_occto__demand_forecast_dad` →
  `fct_occto_demand_forecast_dad` (9 JEPX areas; エリア計 totals + Okinawa stay in `std`).
  Protocol + CSV format:
  [docs/OCCTO-Demand-Forecast-Retrieval.md](docs/OCCTO-Demand-Forecast-Retrieval.md).
- TEPCO エリア需要・発電情報 (Tokyo-area 30-min actuals): `scripts/download_tepco_area_demand_generation.py`
  (`TepcoAreaDownloader` in `power_market_analytics/tepco.py`, always re-downloads every monthly
  zip and extracts only the `AREA_JISEKI_*.csv` actuals) → `data/tepco/area_demand_generation/{zip,csv}/`
  → `scripts/load_tepco_area_demand_generation.py` (`TepcoAreaCsvLoader`, positional contract
  `conf/schemas/tepco_area_demand_generation_actual.yaml`) → `pma_raw.tepco_area_demand_generation_actual`
  → `stg/std_tepco__area_demand_generation_actual` → `fct_tepco_area_demand_generation_actual`
  (grain date × time_code × area, Tokyo only; joins `fct_jepx_spot_area_price` 1:1). Format +
  quirks: [docs/TEPCO-Area-Demand-Generation-Retrieval.md](docs/TEPCO-Area-Demand-Generation-Retrieval.md).
- dbt (`dbt/`): sources in `models/raw/<source>.yml` → `staging` (as-is) → `standardized`
  (typed time axis) → `curated` (Kimball star: `dim_*`, `fct_*`). Schemas: `pma_<layer>`.
- Japanese holidays: Cabinet Office CSV → `scripts/update_holidays_seed.py` → seed → `dim_date`
  (spine end derives from the seed's max year).
- Forecast write-back: `scripts/spot_price_backtest.py` logs the run to MLflow AND publishes
  row-level forecasts (`tasks/spot_price/publish.py`) to `pma_ml.spot_price_forecast`
  (parquet, partitioned by `run_id`, dynamic partition overwrite = idempotent per run) →
  `stg/std_ml__spot_price_forecast` → `fct_spot_price_forecast` →
  `fct_spot_price_forecast_accuracy` (joins actuals; the Superset-facing surface).
  `run_id` links warehouse rows to the MLflow run; the run's `warehouse_table` tag points back.
  `tasks/spot_price/compare.py` reads the accuracy fact back for run-vs-run segment tables.
- Exogenous features: `LightGbmOcctoStrategy` joins `OcctoDemandForecast`
  (`datasets.load_occto_demand_forecast`, from `fct_occto_demand_forecast_dad`) to each
  delivery day's rows via the `_join_daily_features` hook; its training set therefore
  starts 2024-04-01, so a matched `lightgbm` baseline needs `--train-start 2024-04-01`.

## Gotchas

- dbt 1.11 generic tests: put test args under `arguments:` (e.g. `dbt_utils.accepted_range`),
  else deprecation warnings.
- Spark SQL `div` returns `bigint` — cast to `int` where the model contract says `int`.
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

## Claude Code settings

- `permissions.allow` in `.claude/settings.json` is kept ASCII-sorted automatically by the
  SessionStart hook (`.claude/hooks/sort_permissions.py`) — no manual re-sorting needed; just
  keep new entries sorted when editing the file by hand.
- A PostToolUse hook runs `uv run ruff format` + `ruff check --fix` on every `.py` file you
  Edit/Write — the file may change right after your edit; re-Read before the next Edit if
  needed. Config: `pyproject.toml` (line length 100; rules E4/E7/E9/F/I only).
- Verification: there is no pytest suite. Validate data/model changes with
  `just dbt build` (contracts + tests) and Python changes with `uv run ruff check .`;
  loaders/downloaders are checked by running their `scripts/` entry point.

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
