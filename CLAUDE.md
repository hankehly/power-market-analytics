# CLAUDE.md

## Commands

- `just refresh-jepx` — JEPX refresh: download JEPX CSVs + holidays, reload `raw`, `dbt build` (models + tests).
- `just refresh-jma` — JMA weather refresh: regenerate the station seed (~5 min), download
  hourly CSVs (args pass through, e.g. `--prefecture 44`; no args = full network, ~60 h
  cold), reload `raw`, `dbt build`.
- `just python <args>` / `just exec <cmd>` / `just shell` — run inside the devcontainer.
- `just dbt <args>` — dbt from `/workspace/dbt` (e.g. `just dbt build`, `just dbt show --inline "select ..." --limit 5`).
- `just sql` — beeline shell on the thriftserver.
- Host-side dbt also works: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`.
- Anything that creates a SparkSession MUST run in the devcontainer (metastore/warehouse only
  resolve on the compose network); plain python and dbt work from the host too.

## Architecture (data flow)

- JEPX CSVs: `scripts/download_jepx_spot.py` → `data/jepx/spot/` (gitignored) →
  `scripts/load_jepx_spot.py` (`CsvLoader`, load contract in `conf/schemas/jepx_spot.yaml`) → `raw.jepx_spot`.
- JMA weather CSVs: `scripts/download_jma_hourly_all.py` (per-station:
  `download_jma_hourly.py`) → `data/jma/hourly/` → `scripts/load_jma_hourly.py`
  (`JmaHourlyCsvLoader`, positional contracts in `conf/schemas/jma_hourly_*.yaml`, one per
  station-class layout) → `raw.jma_hourly_amedas` / `raw.jma_hourly_staffed`.
  Station master: `scripts/update_jma_stations_seed.py` → seed `jma_stations` →
  `dim_jma_station`. Protocol + CSV format:
  [docs/JMA-Weather-Data-Retrieval.md](docs/JMA-Weather-Data-Retrieval.md).
- dbt (`dbt/`): sources in `models/raw/<source>.yml` → `staging` (as-is) → `standardized`
  (typed time axis) → `curated` (Kimball star: `dim_*`, `fct_*`). Schemas: `pma_<layer>`.
- Japanese holidays: Cabinet Office CSV → `scripts/update_holidays_seed.py` → seed → `dim_date`
  (spine end derives from the seed's max year).

## Gotchas

- dbt 1.11 generic tests: put test args under `arguments:` (e.g. `dbt_utils.accepted_range`),
  else deprecation warnings.
- Spark SQL `div` returns `bigint` — cast to `int` where the model contract says `int`.
- `dbt show --inline`: use the `--limit` flag; a `limit` clause inside the SQL breaks dbt's wrapper.
- JEPX data history constrains tests: FY2016 has genuine 0.00 area prices (no 0.01 floor yet),
  Hokkaido area prices are null 2018-09-07..26 (earthquake suspension), block/FIP columns are
  null before ~FY2022. Check `conf/schemas/jepx_spot.yaml` + model descriptions before
  tightening constraints.

## Dimensional Modeling

- For anything dimensional-modeling related (fact/dimension table design, grain declarations,
  star schemas, SCDs, etc.), abide by the guidelines in
  [docs/Kimball-Dimensional-Modeling-Techniques.md](docs/Kimball-Dimensional-Modeling-Techniques.md).

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