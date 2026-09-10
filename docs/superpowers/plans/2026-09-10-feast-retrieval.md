# Feast retrieval — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feast does the as-of join over the feature marts: feature views generated from the dbt manifest, entities per grain, a store opened from a checked-in config, and a retrieval helper that stamps every prediction row with its issue time. Nothing in the strategies changes yet (PR 5).

**Architecture:** The Feast definitions live in the package, `power_market_analytics/features/`, so tests and the coverage gate see them; `conf/feast/feature_store.yaml` holds the store config (Spark offline store on the active session, a file registry under `data/feast/`, no online store use). `scripts/generate_feature_views.py` reads `dbt/target/manifest.json` and writes `views.py`; a CI step fails when the file is stale. `retrieval.py` builds the entity frame and calls `get_historical_features`.

**Tech Stack:** feast[spark] 0.66.0, PySpark 4.1.1, pandas 2.3, dbt manifest JSON.

**Spec:** `docs/superpowers/specs/2026-09-10-feature-catalogue-design.md` §6, §9, §11 PR 4.

## Global Constraints

- The spike (§9) passed on 2026-09-10 before this plan ran: 17,520 rows in 6.2 s, values equal to the loaders, the one-minute instant honoured, dbt unit tests run on thrift. The façade fallback is not built.
- Time zone rule (found while executing): Feast's Spark store renders the entity timestamps as UTC string literals in its SQL while comparing rows as instants, so the session must be UTC. The devcontainer's is; the entity frame stamps the naive JST issue time as UTC (`ENTITY_TIME_ZONE`); `historical_features` refuses another session zone or a frame stamped otherwise; the Feast tests switch the Asia/Tokyo fixture session to UTC.
- Entities and join keys: `area_code` (string), `trade_date_key` (int `yyyymmdd`), `hour_ending` (int), `time_code` (int). A view's entities are the key columns of its mart's grain; `trade_date_key` is derived in the source query from `trade_date`.
- The generated file is the only Feast definition of the marts; hand edits are forbidden, the CI check enforces it.
- Feature view names are the mart names; feature references are `<mart>:<column>`.
- Coverage stays 100 %; no real network or warehouse in tests.
- The devcontainer image must be rebuilt for the new dependency: `docker compose build devcontainer` (the running container had it pip-installed for the spike).
- Branch `feature/feast-retrieval` (worktree `.claude/worktrees/feast-retrieval`); commit type `feat(features)`; PR labels `enhancement` + `documentation`.

---

### Task 1: Dependency and store config

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (done: `feast[spark]==0.66.0`)
- Create: `conf/feast/feature_store.yaml`
- Create: `power_market_analytics/features/__init__.py`

- [ ] **Step 1: `conf/feast/feature_store.yaml`**

```yaml
project: pma
provider: local
# The registry is rebuilt by open_store() from the package's definitions; data/ is gitignored.
registry: ../../data/feast/registry.db
offline_store:
  # Reuses the active SparkSession (the project's, Hive-enabled); nothing to configure.
  type: spark
online_store:
  # Never materialised: every view is online=False. Feast requires an entry.
  type: sqlite
  path: ../../data/feast/online_store.db
entity_key_serialization_version: 3
```

- [ ] **Step 2: Commit** — `feat(features): feast dependency and store config`

### Task 2: Entities and the store

**Files:**
- Create: `power_market_analytics/features/entities.py`
- Create: `power_market_analytics/features/store.py`
- Test: `tests/test_feature_store.py`

**Interfaces:**
- `entities.py`: `AREA_CODE`, `TRADE_DATE_KEY`, `HOUR_ENDING`, `TIME_CODE` (`feast.Entity`), `ENTITIES` tuple, `GRAIN_ENTITIES: dict[str, tuple[Entity, ...]]` = `{"day": (area, day), "hour": (area, day, hour), "period": (area, day, period)}`.
- `store.py`: `FEATURE_STORE_DIR = <repo>/conf/feast`; `open_store(repo_path=FEATURE_STORE_DIR, *, definitions=None) -> FeatureStore`: loads the yaml, applies `definitions` (default: `ENTITIES + views.VIEWS`), returns the store; `session_time_zone(spark) -> str`.

- [ ] **Step 1: Failing test** — `open_store(tmp repo)` applies one inline view over a Spark temp view and lists it; `session_time_zone` returns the fixture's `Asia/Tokyo`.
- [ ] **Step 2: Implement**, **Step 3: Test passes**, **Step 4: Commit** — `feat(features): entities and the store`

### Task 3: The generator and the generated views

**Files:**
- Create: `scripts/generate_feature_views.py`
- Create: `power_market_analytics/features/views.py` (generated)
- Test: `tests/test_generate_feature_views.py`

**Interfaces:**
- `generate_feature_views.main(argv)`: `--manifest dbt/target/manifest.json`, `--output power_market_analytics/features/views.py`, `--check` (exit 1 and print a diff summary when the output differs; nothing written). `render(manifest: dict) -> str` builds the module text: for each model whose `path` starts with `features/`, sorted by name: a `SparkSource(name=<model>, query=<select keys, trade_date_key, tagged features, available_at from <schema>.<model>>, timestamp_field="available_at", description=<model description>)` and a `FeatureView(name=<model>, entities=GRAIN_ENTITIES[<grain>], schema=[Field(name, dtype, description, tags={"categorical": "true"|"false"})...], source=..., online=False, tags={"grain": <grain>})`; `VIEWS` tuple at the end. Grain from the key columns: `time_code` present → period, `hour_ending` → hour, else day. dtype map: `int`/`bigint` → `Int64`, `double` → `Float64`, `string` → `String`, `boolean` → `Bool`, `timestamp` → `UnixTimestamp`; a tagged column of another type is an error. Feature columns = those whose `config.meta.feature` is true (`ftr_hour_msm` also carries `forecast_reference_at` and `census_year` untagged: they are not selected).
- The schema in the query comes from the node's `schema` + `database` fields in the manifest (`pma_features`).

- [ ] **Step 1: Failing tests** with a synthetic manifest (two models: a period mart with one double feature and a day mart with an int categorical, one non-feature model) asserting the rendered text, the grain choice, the dtype error, and `--check` both ways.
- [ ] **Step 2: Implement** and generate `views.py` from the real manifest (`cd dbt && uv run dbt parse` host-side first).
- [ ] **Step 3: Test** that importing `views` yields six views with the expected entities and that `open_store` applies them to a temp registry.
- [ ] **Step 4: Commit** — `feat(features): feature views generated from the dbt manifest`

### Task 4: Retrieval

**Files:**
- Create: `power_market_analytics/features/retrieval.py`
- Test: `tests/test_feature_retrieval.py`

**Interfaces:**
- `entity_frame(area_code: str, days: pd.DatetimeIndex, issue_offset: pd.Timedelta) -> pd.DataFrame`: one row per day × time code 1..48 with `area_code`, `trade_date`, `time_code`, `trade_date_key`, `hour_ending`, `event_timestamp` = `trade_date + issue_offset` stamped as UTC.
- `historical_features(store, entity_df, features: Sequence[str]) -> pd.DataFrame`: `store.get_historical_features(...).to_df()`, with `event_timestamp` returned naive in the session zone and the rows in the entity frame's order.

- [ ] **Step 1: Failing tests** on the `spark` fixture: a temp view with two vintages of one row (available 01:00 and 02:00) and one unrelated key; entity rows at 00:59, 01:00, 02:00, 09:30 expecting null, v1, v2, v2; day-grain broadcast: a day feature repeated over 48 periods; the row order and naive timestamps.
- [ ] **Step 2: Implement**, **Step 3: Tests pass**, **Step 4: Commit** — `feat(features): entity frame and historical retrieval`

### Task 5: Warehouse proof, CI step, docs, PR

- [ ] **Step 1: Proof** — in the devcontainer, `retrieval` over the real store for Tokyo 2025 with the six views' every feature: row count 17,520, equality with the marts (exact), elapsed time; the spike's instant check repeated through `retrieval`.
- [ ] **Step 2: CI** — in `.github/workflows/ci.yml`'s `dbt parse` job, after `Run dbt parse`: `uv run --no-sync python scripts/generate_feature_views.py --check` from the repo root. Justfile recipe `feature-views` = host-side `dbt parse` then the generator.
- [ ] **Step 3: Docs** — CLAUDE.md (commands: `just feature-views`, the rebuild; architecture: the Feast layer and the time-zone rule); spec §6 (package layout, `conf/feast`, the session-zone rule) and §11 PR 4 done.
- [ ] **Step 4: PR** — title `feat(features): feast retrieval over the feature marts`, Why / What / Proof, labels, assignee; Codex loop.
