# JMA re-scope: s-stations only, expanded elements, one stitched file

**Date:** 2026-08-20
**Status:** Approved design, not yet implemented

## Context and motivation

The JMA hourly pipeline currently ingests every station in the network (1,287 active:
~1,130 AMeDAS `a*` + 156 staffed `s*`), but the only consumer — the demand task's
representative-station temperature feature — reads staffed stations exclusively
(`dim_area.representative_jma_station_id`: 東京 s47662, 大阪 s47772). Analysis against the
2020 census 500 m population mesh confirmed the staffed network alone covers Japan's
population centers: every 政令指定都市 has an active s-station within 30 km (18 of 21
within 13 km), 90% of the population lives within 30 km and 99.1% within 50 km of one.
Staffed stations also observe 官署-only elements (humidity, solar radiation, …) that
AMeDAS lacks, and all 159 observe the full element set (`kansoku=111111`).

Re-scoping to s-stations therefore loses nothing downstream, cuts a cold scrape from
~60 h to ~14 h even with expanded elements, and removes an entire ingestion leg
(second CSV layout, second raw table, union in `std`).

## Decisions (user-approved)

1. **Remove the AMeDAS leg entirely** — downloader, contract, raw table, staging model,
   union branch, tests, docs. Git history keeps it recoverable.
2. **Filter the station-master seed to s-stations** (~159 rows incl. 3 discontinued);
   `dim_jma_station` and the download plan follow automatically. The s-prefix rule keeps
   南極昭和基地 (s89532) and mountain stations (富士山 s47639) — accepted, no special-casing.
3. **Delete ingested AMeDAS artifacts** — `data/jma/hourly/a*.csv` from disk and
   `pma_raw.jma_hourly_amedas` from the warehouse (plus the orphaned
   `pma_staging.stg_jma__hourly_amedas` relation).
4. **Expand the element set (lean)** — core 4 (気温 201, 降水量 101, 風向・風速 301,
   日照時間 401) plus 積雪の深さ 501, 相対湿度 605, 全天日射量 610. Rationale: direct
   forecasting value (winter/snow load, AC/humidity load, PV output). Excluded as least
   useful or derivable: 天気/雲量/視程, 蒸気圧/露点温度, 気圧, 降雪の深さ.
5. **One stitched file per station-year** (`s{id}_101-201-301-401-501-605-610_{year}.csv`)
   rather than two element-group file sets — permanently simpler warehouse (one contract,
   one raw table, no join) at the cost of a one-time full re-scrape (~14 h).
6. **Fallback ladder is time, never elements** — if a request window exceeds JMA's cap,
   shrink the window (half-year → quarter → month); do not split by element group.
7. **Files are write-once** — a year file is assembled in memory and written complete, or
   not at all; never appended to or patched afterward. A stale current-year file is
   replaced wholesale on refresh (as today).

## Design

### Spike (implementation step 0)

One live request: all 7 elements (8 value columns) for 東京 s47662, 2024-01-01 → 06-30
(~35k values, under the proven ~44k pass / ~61k fail bracket from
docs/JMA-Weather-Data-Retrieval.md §6.1). Outcomes:

- **Pass** → confirms `MAX_VALUES_PER_REQUEST = 40_000` and pins the real 26-column
  layout for the contract and test fixtures.
- **Fail** → lower the constant and re-spike at a smaller window (quarter → month).
  The stitched-file design is unchanged; only window count grows.

### Ingestion

- `JmaStationMasterDownloader` gains a staffed-only filter;
  `scripts/update_jma_stations_seed.py` enables it. Seed regen cost unchanged (same
  per-prefecture pages scraped).
- `JmaHourlyDownloader.download` gains time-windowing: window count =
  `ceil(value_columns × hours_in_year / MAX_VALUES_PER_REQUEST)` (8 columns → 2
  half-year windows). It fetches each window, strips the repeated header block, and
  stitches responses into one CSV, written once. The current `MAX_VALUE_COLUMNS = 5`
  hard reject becomes the windowing trigger. Window boundaries are clean because hour
  24:00 is stored as next-day 00:00: Jan–Jun ends Jul 1 00:00, Jul–Dec starts
  Jul 1 01:00 — no overlap, no gap.
- `scripts/download_jma_hourly_all.py` plans the same (station, year) pairs with the
  7-element set. Resume/caching, end-date trimming (阿蘇山 s47821 contributes 2016–2017),
  current-year staleness refresh, the 10-consecutive-failure circuit breaker, and
  `--prefecture/--limit/--dry-run` all carry over.
- Volumes: backfill ≈ 159 stations × ~11 years × 2 requests ≈ 3,450 requests ≈ 14 h at
  the observed ~15 s/request; future cold run the same; routine current-year refresh
  ≈ 320 requests ≈ 1.5 h. Existing `s*_101-201-301-401_*.csv` files become dead cache
  (deleted in cleanup).

### 均質番号 caveat introduced by time slicing

均質番号 restarts from 1 in every server response (doc §7.3). In a stitched year file the
numbering therefore resets at each window boundary (e.g. Jul 1): homogeneity breaks are
only meaningful *within* a window, and a real break falling exactly on a window boundary
is invisible in the CSV alone. This is the existing cross-file caveat at finer grain. The
columns are carried through unchanged; the caveat is documented in the JMA doc and in the
model descriptions wherever `*_homogeneity_no` is surfaced.

### Warehouse

- `scripts/load_jma_hourly.py` `FORMATS` shrinks to one entry:
  `("jma_hourly_staffed", "s*_101-201-301-401-501-605-610_*.csv", "pma_raw.jma_hourly_staffed")`.
- `conf/schemas/jma_hourly_staffed.yaml` is rewritten for the 26-column stitched layout —
  timestamp + 降水量 (value, 現象なし, 品質, 均質) + 気温 (value, 品質, 均質) + 風
  (風速 value+品質, 風向 value+品質, shared 均質) + 日照時間 (value, 現象なし, 品質, 均質) +
  積雪の深さ (value, 品質, 均質) + 相対湿度 (value, 品質, 均質) + 全天日射量 (value, 品質, 均質).
  None of the three new elements is a phenomenon element, so no new 現象なし columns.
  Exact column order is pinned from the spike's real response.
  `conf/schemas/jma_hourly_amedas.yaml` is deleted. `JmaHourlyCsvLoader` is schema-driven
  and should need no code change.
- dbt: delete `stg_jma__hourly_amedas.{sql,yml}`; widen `stg_jma__hourly_staffed`; update
  `models/raw/jma.yml` (drop the amedas table, extend staffed columns);
  `std_jma__hourly` becomes a straight select (no union, no join, no null-padding);
  `fct_jma_weather_hourly` gains 9 columns — `snow_depth_cm`, `humidity_pct`,
  `solar_radiation_mjm2`, each with `_quality_flag` and `_homogeneity_no`. Grain unchanged
  (station × hour). Enforced contracts and uniqueness tests updated per repo dbt rules.
- `dim_jma_station`: no SQL change (seed-driven); descriptions/tests updated where they
  mention AMeDAS. Downstream demand task untouched — new columns are available for future
  features only.

### Cleanup (ordered — warehouse never goes dark)

1. Implement + merge.
2. Backfill the new stitched files (~14 h, resumable).
3. Reload raw + `dbt build`.
4. Delete `data/jma/hourly/a*.csv` and the obsolete `s*_101-201-301-401_*.csv`.
5. Drop orphaned relations: `pma_raw.jma_hourly_amedas`,
   `pma_staging.stg_jma__hourly_amedas`.

Side effect: once backfilled, 大阪 s47772 is loaded and current, unblocking
`scripts/demand_backtest.py --area kansai`.

### Testing

TDD throughout; the 100% coverage gate stays. Unit tests: station-master s-filter;
downloader windowing (boundary math incl. the 24:00 → next-day-00:00 handoff, header
dedup, value-budget arithmetic, cache/current-year behavior, window-failure ladder);
loader against a 26-column fixture trimmed from the spike response; `FORMATS`/plan
assertions; AMeDAS tests removed. End-to-end: script entry points in the devcontainer
(`--prefecture 44 --limit 1` smoke), then full `dbt build`.

### Documentation

- `docs/JMA-Weather-Data-Retrieval.md`: §4 station scope, §5 chosen element set, §6.3
  windowed packing math, §7.3 均質番号 window-boundary caveat, §7.5 single-format table,
  §8 usage examples.
- `CLAUDE.md` / `AGENTS.md`: `refresh-jma` command description and JMA architecture
  bullets (s-only, 7 elements, one raw table; cold ~14 h, was ~60 h).
- `justfile` `refresh-jma` docstring timings.

## Out of scope

- Using the new elements (humidity, solar radiation, snow depth) as model features — a
  separate research task once the data is loaded.
- Adding further elements (気圧, 天気, …) — would add a window per year; revisit if needed.
- Any change to the demand/spot-price task code.
