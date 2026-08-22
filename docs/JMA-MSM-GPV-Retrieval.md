# JMA MSM GPV Retrieval

This document describes how JMA's MSM (メソ数値予報モデル, mesoscale numerical prediction
model) surface-grid GPV forecast is retrieved from Kyoto University's RISH archive, decoded
from GRIB2 into per-station hourly records, and loaded into the warehouse: the product and
its publication schedule, the RISH mirror's URL/etiquette, the single-vintage policy this
pipeline ingests and why, the GRIB2 element/grid metadata the decoder trusts (including a
multi-field-message gotcha the real data surfaced), the extract and warehouse schemas, and
how to run/operate the pipeline (`just refresh-msm`). It mirrors the depth of
[docs/OCCTO-Demand-Forecast-Retrieval.md](OCCTO-Demand-Forecast-Retrieval.md).

Source of truth for the constants and logic described here: `power_market_analytics/msm.py`
(pure logic — vintage arithmetic, grid geometry, nearest-neighbour selection, unit
conversions, no eccodes) and `power_market_analytics/msm_grib.py` (GRIB2 decoding and the
downloader; the only module in the pipeline that imports eccodes). Load contract:
`conf/schemas/jma_msm_surface_forecast.yaml`. Every GRIB2 element/grid fact in
[§5](#5-grib2-decoding) was verified empirically against a real archive member during the
pipeline's one-day end-to-end run ([§9](#9-verification-results-one-day-end-to-end-2026-08-21));
if RISH or JMA changes the product, re-verify against a live file before trusting this doc.

## 1. Product & publisher

- **Publisher**: Japan Meteorological Agency (JMA). **Product**: MSM (メソ数値予報モデル)
  GPV — a mesoscale numerical weather prediction model output on a regular Japan-region
  surface grid, distributed as GRIB2.
- **Grid**: 505 rows (latitude) × 481 columns (longitude), 0.05° latitude × 0.0625°
  longitude spacing (the ~5 km resolution JMA advertises for MSM). The grid metadata
  verified in [§5.3](#53-grid-metadata-and-scan-handling) puts the domain at roughly
  22.40°N–47.60°N, 120.00°E–150.00°E (computed from `Ni`/`Nj`/first point/increments —
  the standard published MSM domain).
- **Run schedule**: JMA runs MSM eight times a day (00/03/06/09/12/15/18/21 UTC); this
  pipeline ingests exactly one of those eight runs per delivery day
  ([§3](#3-vintage-policy)).
- **Product-change history** (bears on how far back this pipeline can safely backfill):

  | Date | Change |
  |---|---|
  | 2006-03 | ~5 km grid introduced |
  | 2013 | Forecast horizon extended to 39 h |
  | 2017-12 | Downward shortwave (solar) radiation added to the surface element set |
  | 2019-03 | 00/12 UTC runs extended to FH51 |
  | 2022-06 | 00/12 UTC runs extended further to FH78 |

  This pipeline only reads leads 28–51 ([§4](#4-file-set-and-forecast-lead-table)), so the
  2019-03 FH51 extension is the binding constraint:
  `power_market_analytics.msm.EARLIEST_DELIVERY_DATE` is **2019-04-01**, one month after the
  change, so every ingested delivery day's 12 UTC D−2 run is guaranteed to carry FH51.
  Historical backfills default to `DEFAULT_BACKFILL_START` **2022-04-01** instead, matching
  the other refresh tasks' backfill start (TEPCO/Kansai actuals, etc.).

## 2. The RISH archive

- **URL pattern**: `https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/`
  (`power_market_analytics.msm.BASE_URL`), one subdirectory per **reference run's date**
  (`YYYY/MM/DD`, always the 12 UTC run's calendar date — UTC and JST agree on the date for
  a 12:00 UTC timestamp) and one file per forecast-hour band within it, e.g.
  `.../2026/08/17/Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin` for the
  2026-08-17 12 UTC run.
- **`original` vs `netcdf`/`MSM-S`**: RISH mirrors JMA's MSM output in more than one form;
  this pipeline deliberately uses the `original` GRIB2 tree, not the `netcdf`/`MSM-S`
  derivative, because the derivative destroys the run/vintage structure (one run's issue
  time and forecast-hour banding) that this pipeline's leakage-safe vintage selection
  ([§3](#3-vintage-policy)) depends on.
- **Academic-use etiquette**: RISH is an academic mirror with no published rate limit of
  its own. `MsmDownloader` is deliberately polite: downloads are sequential (one HTTP
  request in flight at a time) and throttled to at least `request_interval` seconds apart
  (default 1.0 s), with bounded retries (`max_attempts`, default 3, backoff
  `request_interval * attempt`) on transient failures.
- **Gaps are possible and must surface as errors.** RISH's archive can have publication
  gaps or files not yet published; an HTTP 404 is treated as a **completeness failure**
  (`MsmDownloadError`, naming the URL), never as an empty/partial forecast. Downloaded
  content is also checked for the 4-byte `GRIB` magic before being trusted as a GRIB2 file
  — RISH serving an HTML error page with a 200 status is caught here rather than becoming
  a corrupt cache entry.
- **RISH mtimes are not authoritative.** File modification times on the archive do not
  reliably reflect JMA's publication schedule; this pipeline never uses them for freshness
  or resume decisions — only the reference run's issue time
  (`power_market_analytics.msm.reference_at_for`), computed purely from the delivery date,
  drives which files are fetched.

## 3. Vintage policy

This pipeline ingests **a single vintage per delivery day D**: the **12 UTC run of D−2**
(21:00 JST D−2). `power_market_analytics.msm.reference_at_for(D)` returns that instant;
`issue_cutoff_for(D)` returns the constraint it must satisfy — **09:30 JST D−1**, the same
instant the demand-forecast model issues its own forecast for D
(`docs/../CLAUDE.md`'s demand task: 09:30 JST D−1). Using a later run as a feature would let
the demand model see information it could not actually have had at forecast time.

Why the 12 UTC D−2 run and not another:

- **12 UTC D−2 is the latest run whose horizon still reaches every hour of D.** FH51 of the
  12 UTC D−2 run lands at reference + 51 h = 15:00 UTC D = **24:00 JST D** — the last
  hour-ending of the delivery day (`hour_ending_for(51) == 24`); FH28 lands at
  **01:00 JST D** (`hour_ending_for(28) == 1`), the first. The run is published (RISH
  distribution observed ~23:30 JST D−2) well before the 09:30 JST D−1 cutoff — about ten
  hours of margin.
- **The 21 UTC D−2 run cannot be used**: its horizon (FH39 pre-extension era; even
  post-extension its practical distribution timing is the same run family) reaches only
  21:00 UTC + 39 h = 12:00 UTC D = **21:00 JST D** — three hours short of the delivery day's
  last hour-ending (24:00 JST D; the missing hour-endings are 22:00, 23:00, 24:00). A
  39-hour horizon run simply cannot cover the full day.
- **The 00 UTC D−1 run cannot be used**: although its horizon (FH51 or FH78 depending on
  era) would cover D, it is not distributed until roughly **11:30 JST D−1** — *after* the
  09:30 JST D−1 cutoff. Using it would leak same-day information the demand model could not
  have seen at its own issue time.

`default_end_date()` (JST "today" + 1 day) reflects this arithmetic directly: a delivery day
of "tomorrow" only needs "yesterday's" 12 UTC run, which by the time `default_end_date` is
evaluated has already been published and is safe to fetch.

## 4. File set and forecast-lead table

Every delivery day reads exactly **three** GRIB2 archive members from the 12 UTC D−2 run
directory (`power_market_analytics.msm.source_files_for`), each covering a band of forecast
hours; only a subset of each file's leads is actually used — the rest of the file is decoded
but discarded (skipped) rather than fetched separately, since RISH bands files this way and
splitting a band mid-file is not possible over HTTP range requests here.

| Archive member (`FH<band>`) | Leads physically in the file | Leads used by this pipeline | JST hour-endings covered |
|---|---|---|---|
| `..._FH16-33_grib2.bin` | 16–33 | **28–33** | 01:00–06:00 |
| `..._FH34-39_grib2.bin` | 34–39 | **34–39** | 07:00–12:00 |
| `..._FH40-51_grib2.bin` | 40–51 | **40–51** | 13:00–24:00 |

`hour_ending_for(lead) = lead - 27` and its inverse are asserted to hold only for
28 ≤ lead ≤ 51; `time_codes_for(hour_ending)` returns the pair of JEPX 30-minute time codes
`(2h-1, 2h)` an hour-ending covers (downstream reverses this with
`hour_ending = (time_code + 1) // 2`, [§7](#7-warehouse-models)). Files are processed in the
order above; each file's messages are streamed and released one at a time
([§5](#5-grib2-decoding)), so at most one file's worth of decoded values is ever held in
memory.

Verified real file sizes for delivery day 2026-08-19 (reference 2026-08-17T12:00:00Z,
[§9](#9-verification-results-one-day-end-to-end-2026-08-21)): FH16-33 78,716,561 bytes,
FH34-39 26,238,929 bytes, FH40-51 52,477,745 bytes — **~157 MB total per delivery day**
(~54 GiB for a full year, `docs/` and `justfile` operational notes,
[§8](#8-operations)).

## 5. GRIB2 decoding

`power_market_analytics.msm_grib.extract_station_records` walks a downloaded archive
member's GRIB2 messages with ecCodes, identifies each one **by metadata, never by position
in the file**, samples the grid at the point nearest every station, and returns one record
per station and forecast hour. The decoder is deliberately strict — see the module
docstring — because a silently short or mislabeled extract would become a silently wrong
forecast feature downstream.

### 5.1 The multi-field-message gotcha

**JMA packs many fields into one GRIB2 message envelope.** A whole archive member is a
*single* envelope containing many logical (element, forecast-hour) fields — the real
FH16-33 file holds 12 elements × 18 forecast hours = **216 fields**, all inside one
envelope. ecCodes' default behaviour on such a file yields only the **first** field; its
multi-field support has to be turned on (`eccodes.codes_grib_multi_support_on()`) for the
message-iteration loop to see all 216. This is **process-global ecCodes state** that other
code (including ecCodes' own multi-field writer) can flip back off, so
`extract_station_records` re-asserts it on **every call** rather than once at import time;
the call is idempotent and costs nothing. This was found as a real defect during the
pipeline's end-to-end verification ([§9](#9-verification-results-one-day-end-to-end-2026-08-21)) — without it, extraction silently produced far fewer
records than expected and then failed the completeness check.

### 5.2 Element identification and the surface element table

Every message is identified by its `(discipline, parameterCategory, parameterNumber)`
triple, looked up against a fixed table
(`power_market_analytics.msm.MSM_SURFACE_ELEMENTS`); an unconfigured triple is skipped, not
an error (the file may carry parameters this pipeline does not use). A configured element's
`typeOfFirstFixedSurface` is then asserted against the table's expected surface type — a
mismatch raises (`MsmExtractError`), signalling a format change rather than being silently
tolerated. The real file's messages confirmed every row of this table exactly:

| Key | discipline/category/number | Surface type | Meaning | Semantics |
|---|---|---|---|---|
| `temperature_k` | 0/0/0 | 103 (1.5 m above ground) | Temperature | Instantaneous, K |
| `relative_humidity_pct` | 0/1/1 | 103 (1.5 m above ground) | Relative humidity | Instantaneous, % |
| `u_wind_ms` | 0/2/2 | 103 (10 m above ground) | U-component of wind | Instantaneous, m/s |
| `v_wind_ms` | 0/2/3 | 103 (10 m above ground) | V-component of wind | Instantaneous, m/s |
| `surface_pressure_pa` | 0/3/0 | 1 (ground/surface) | Surface pressure | Instantaneous, Pa |
| `sea_level_pressure_pa` | 0/3/1 | 101 (mean sea level) | Pressure reduced to MSL | Instantaneous, Pa |
| `precipitation_mm` | 0/1/8 | 1 (ground/surface) | Total precipitation | **Statistical**: PDT 8, 1-hour accumulation, mm |
| `shortwave_radiation_wm2` | 0/4/7 | 1 (ground/surface) | Downward shortwave radiation flux | **Statistical**: PDT 8, 1-hour mean flux, W/m² |
| `total_cloud_cover_pct` | 0/6/1 | 1 (ground/surface) | Total cloud cover | Instantaneous, % |
| `low_cloud_cover_pct` | 0/6/3 | 1 (ground/surface) | Low cloud cover | Instantaneous, % |
| `middle_cloud_cover_pct` | 0/6/4 | 1 (ground/surface) | Middle cloud cover | Instantaneous, % |
| `high_cloud_cover_pct` | 0/6/5 | 1 (ground/surface) | High cloud cover | Instantaneous, % |

The height annotations in the "Surface type" column (1.5 m, 10 m) come from JMA's MSM
format specification, not from a decode-time assertion: the decoder checks only
`typeOfFirstFixedSurface` (103) against `MsmElement.surface_type`, never the height value
itself (the height is carried but not checked by the decoder — a level-103 message's
`scaledValueOfFirstFixedSurface`/ecCodes `level` key does hold the 1.5 m / 10 m figure
independently, only `typeOfFirstFixedSurface` is asserted here) — the heights are
documentation of what 103 means for each element, not something this pipeline re-verifies
per message.

Two other checks apply to every message before it is used
(`_check_message_identity`): `editionNumber` must be **2** (GRIB2 throughout the archive)
and `productionStatusOfProcessedData` must be **0** (operational data — the archive can in
principle carry test/research runs, and this pipeline must never load one), and its
`dataDate`/`dataTime` must equal the reference run being fetched (guards against a
misnamed or stale cached file).

The two **statistical** elements (precipitation, shortwave radiation) use GRIB2 Product
Definition Template 8 (a statistically-processed field over a time interval) with a 1-hour
accumulation/mean window ending at the message's forecast hour; the ten instantaneous
elements are valid *at* that hour. Both land on the same `StationHourRecord` — the record is
read downstream as "the hour ending at `forecast_valid_at`" regardless of which semantics an
individual value column carries (documented per-column in the std/fct model descriptions,
[§7](#7-warehouse-models)).

A completed extract requires **every** (element, used lead) pair to have been decoded; any
absent (element, lead) pair — a genuinely missing message, not a bitmap hole — raises
`MsmExtractError` naming every absent pair, rather than yielding a record with a silent hole.

### 5.3 Grid metadata and scan handling

Every message repeats the grid's geometry (`_read_grid`), read fresh per message rather than
assumed constant, and cached by grid identity so the (expensive) nearest-neighbour selection
for all stations is computed once per distinct grid, not once per message. Verified fields
for the real file:

| Field | Value | Meaning |
|---|---|---|
| `Ni` | 481 | Points per row (longitude direction) |
| `Nj` | 505 | Number of rows (latitude direction) |
| `latitudeOfFirstGridPointInDegrees` | 47.6 | First point's latitude (grid's north edge) |
| `longitudeOfFirstGridPointInDegrees` | 120.0 | First point's longitude (grid's west edge) |
| `jDirectionIncrementInDegrees` | 0.05 | Row spacing |
| `iDirectionIncrementInDegrees` | 0.0625 | Column spacing |
| `iScansNegatively` | 0 | Columns scan eastward (i increases → longitude increases) |
| `jScansPositively` | 0 | Rows scan **southward** (j increases → latitude decreases) |
| `jPointsAreConsecutive` | 0 | **i-fastest** (row-major): flat index = `j * Ni + i` |

`iScansNegatively`/`jScansPositively` set the *sign* of `MsmGrid.longitude_step`/
`latitude_step` (`_read_grid`) so grid-index arithmetic always follows the values' storage
order; `jPointsAreConsecutive != 0` (j-fastest / column-major) is rejected outright — the
pipeline's flat indexing assumes row-major and would silently misread a column-major grid
otherwise.

**Bitmap handling.** A message's `bitmapPresent` flag is checked once; when set, the
message's `missingValue` sentinel is read and any sampled grid value equal to it is decoded
as `None` rather than as a real number. This is the **only** source of a `None` value column
in a completed record — a whole missing message is a hard failure ([§5.2](#52-element-identification-and-the-surface-element-table)), not a bitmap hole.

### 5.4 Nearest-neighbour grid selection

`power_market_analytics.msm.select_grid_point` maps a station's (latitude, longitude) to the
nearest grid index on each axis independently, then converts to the flat row-major index the
GRIB values array uses:

- The query point must fall inside the grid's extent, **inclusive of its four corners**; a
  1e-9 floating-point tolerance is applied on the boundary check so an exact corner is never
  rejected merely because a step size like `0.05` is not exactly representable in binary
  floating point. A station strictly outside the domain raises `MsmError`.
- **Ties resolve toward the lower index** on each axis — the point encountered first in the
  grid's scan order (`_nearest_index`: `floor(x)` unless the fractional part exceeds exactly
  `0.5`).
- The selected grid point's own coordinates, its flat index, and the **great-circle
  (haversine) distance** to the query station (rounded to 3 decimals,
  `EARTH_RADIUS_KM = 6371.0088`) are all persisted on every record
  (`grid_latitude`, `grid_longitude`, `grid_distance_km`), so every downstream consumer can
  see exactly how far the sampled point is from the station it's attributed to — a station's
  own coordinates and its nearest grid point's coordinates are never conflated.

## 6. Extract format

`MsmDownloader.extract_day` writes one gzip CSV extract and one JSON manifest per delivery
day (`data/jma/msm_surface_forecast/csv/msm_surface_YYYYMMDD.csv.gz` /
`.json` by default), both written atomically (`.part` file, then
`Path.replace`) so an interrupted run never leaves a truncated file at its final path.

### 6.1 CSV columns

Header is the fixed `power_market_analytics.msm.RAW_CSV_COLUMNS` tuple, 24 columns, one row
per station × used forecast lead:

| # | Column | Nullable | Notes |
|---|---|---|---|
| 1 | `station_id` | No | JMA station id, e.g. `s47662` |
| 2 | `station_latitude` | No | Station's own coordinate |
| 3 | `station_longitude` | No | Station's own coordinate |
| 4 | `grid_latitude` | No | Nearest MSM grid point ([§5.4](#54-nearest-neighbour-grid-selection)) |
| 5 | `grid_longitude` | No | Nearest MSM grid point |
| 6 | `grid_distance_km` | No | Haversine distance, station ↔ grid point |
| 7 | `forecast_reference_at_utc` | No | ISO 8601 UTC string, `"...Z"` — the run's issue time |
| 8 | `forecast_valid_at_utc` | No | ISO 8601 UTC string — END of the represented hour |
| 9 | `forecast_lead_hours` | No | 28–51 |
| 10–23 | 14 weather value columns (`VALUE_COLUMNS`) | **Yes** | `temperature_c`, `relative_humidity_pct`, `u_wind_ms`, `v_wind_ms`, `wind_speed_ms`, `precipitation_mm`, `surface_pressure_hpa`, `sea_level_pressure_hpa`, `shortwave_radiation_wm2`, `solar_radiation_mjm2`, `total_cloud_cover_pct`, `high_cloud_cover_pct`, `middle_cloud_cover_pct`, `low_cloud_cover_pct` |
| 24 | `source_file_name` | No | Archive member the row was decoded from |

The two timestamp columns are kept as **UTC ISO 8601 strings**, not parsed values, deliberately —
this avoids any session-timezone parsing ambiguity at the raw-load boundary; the standardized
layer ([§7](#7-warehouse-models)) does the JST conversion explicitly and auditably. Every
weather value is rounded to 6 decimal places (`VALUE_PRECISION`) and rendered as `''` (empty
cell) for `None` rather than any sentinel string.

### 6.2 Manifest

One JSON file per delivery day records exactly what was downloaded, for provenance and
integrity checking:

```json
{
  "delivery_date": "2026-08-19",
  "reference_at_utc": "2026-08-17T12:00:00Z",
  "files": [
    {"file_name": "Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin",
     "url": "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/2026/08/17/...",
     "sha256": "...", "size_bytes": 78716561},
    {"file_name": "...FH34-39_grib2.bin", "url": "...", "sha256": "...", "size_bytes": 26238929},
    {"file_name": "...FH40-51_grib2.bin", "url": "...", "sha256": "...", "size_bytes": 52477745}
  ]
}
```

The manifest is written **before** the csv.gz, and the csv.gz commit is the sole "this day is
done" signal a later non-`--force` run trusts — see [§8.3](#83-resume-behavior) for why the
write order matters.

## 7. Warehouse models

`MsmForecastCsvLoader` (`power_market_analytics/msm.py`, a `CsvLoader` subclass) performs a
**full reload** (overwrite) of every `csv.gz` extract under a directory (or a single
file/glob) into `pma_raw.jma_msm_surface_forecast`, enforcing the load contract
`conf/schemas/jma_msm_surface_forecast.yaml` — grain
`(station_id, forecast_reference_at_utc, forecast_valid_at_utc)`.

| Model | Layer | What it adds |
|---|---|---|
| `pma_raw.jma_msm_surface_forecast` | raw | As extracted; the nine identifying columns + `source_file_name` `not null`, all 14 weather values nullable; grain enforced by the load contract |
| `stg_jma__msm_surface_forecast` | staging | As-is view with an enforced dbt contract + grain uniqueness test; column documentation lives on the source (`models/raw/jma.yml`) |
| `std_jma__msm_surface_forecast` | standardized | Typed JST time axis: `forecast_reference_at`/`forecast_valid_at` parsed from the UTC strings (`to_timestamp` with an explicit format) and shifted +9 h (`timestampadd`); `forecast_hour_start_at` = `forecast_valid_at` − 1 h (shifted +8 h directly, same result, one fewer subtraction); `forecast_date` = the hour-*start* date |
| `fct_jma_msm_weather_forecast_hourly` | curated | A **multi-vintage atomic forecast fact** — grain `(station_id, forecast_reference_at, forecast_valid_at)` allows more than one reference run per station/valid-hour, even though this pipeline currently loads exactly one vintage per delivery day. `date_key` (FK `dim_date`) is the forecast-valid day (hour-start date). `station_id` is a FK to `dim_jma_station`. No half-hour duplication is applied at this layer. |

**JST conversion**: JST is a fixed UTC+9 offset (no daylight saving), so
`timestampadd(hour, 9, to_timestamp(..., "yyyy-MM-dd'T'HH:mm:ss'Z'"))` is exact and auditable
— no timezone-database lookup, matching the fixed-offset `JST` constant in `msm.py`.

**`forecast_valid_at` marks the END of the represented weather hour** — the same convention
`fct_jma_weather_hourly.observed_at` uses (`std_jma__hourly`/`jma_hourly_staffed`, hour 24:00
stored as next-day 00:00). This is deliberate: it makes the **forecast-vs-observed join**
(used for the accuracy analysis in [§9.4](#94-forecast-vs-observed-comparison)) a plain
equi-join on `station_id` **and** `forecast_valid_at = observed_at`, with no hour-boundary
arithmetic needed on either side.

`precipitation_mm`, `shortwave_radiation_wm2` and `solar_radiation_mjm2` cover
`[forecast_hour_start_at, forecast_valid_at)`; every other weather value is instantaneous
*at* `forecast_valid_at` — the same statistical/instantaneous split established at GRIB
decode time ([§5.2](#52-element-identification-and-the-surface-element-table)).

**Downstream join convention**: JEPX's 48 half-hourly `time_code`s map onto this hourly grain
via `hour_ending = (time_code + 1) // 2` (the same convention the demand task's temperature
feature uses, `docs/../CLAUDE.md`'s demand-task bullet) — this fact has no half-hourly grain
of its own and is not duplicated across the two half-hour periods of an hour; a consumer
resolves the hour first, then reads one row.

Every weather value column is the **nearest MSM grid point's** value
(`grid_latitude`/`grid_longitude`/`grid_distance_km`), not a station-specific forecast — the
grid point's elevation and terrain can differ materially from the station's own
([§9.4](#94-forecast-vs-observed-comparison) shows this in the verified numbers). `dbt_utils.accepted_range`
tests on the fct model encode the physically-derived plausibility bounds (e.g.
`relative_humidity_pct` 0–100, `grid_distance_km` 0–5). One deliberate allowance:
`total_cloud_cover_pct` is tested against 0–100.1, because the GRIB2 packing of that field
overshoots 100 by up to ~0.011 on rare rows (157 of 5.7 M in the full backfill); values are
kept as decoded rather than clamped, and the three layer covers stay within 0–100.

## 8. Operations

### 8.1 `just refresh-msm`

```
just refresh-msm [args]
# = just python scripts/download_jma_msm_surface_forecast.py [args]
#   just python scripts/load_jma_msm_surface_forecast.py
#   just dbt build
```

`scripts/download_jma_msm_surface_forecast.py` flags (all forwarded through `just refresh-msm`):

| Flag | Default | Meaning |
|---|---|---|
| `--start-date` | `DEFAULT_BACKFILL_START` (2022-04-01) | First delivery day, inclusive |
| `--end-date` | `default_end_date()` (JST today + 1) | Last delivery day, inclusive |
| `--data-dir` | `data/jma/msm_surface_forecast` | Root for `grib/` downloads and `csv/` extracts |
| `--force` | off | Re-download every GRIB2 file and rebuild the extract even for a cached delivery day |
| `--keep-grib` | off | Keep the three GRIB2 files after a successful extract (deleted by default to bound disk use across a backfill) |

Stations to extract are loaded once per invocation from `dbt/seeds/jma_stations.csv` +
`dbt/seeds/jma_station_areas.csv` (`power_market_analytics.msm.load_stations`) — every
staffed station mapped to a JEPX area, active or discontinued, sorted by `station_id`; a
station missing its area mapping or its coordinates fails the whole run rather than being
silently skipped.

### 8.2 Volumes and the devcontainer rebuild

~157 MB per delivery day ([§4](#4-file-set-and-forecast-lead-table)) ≈ **54 GiB for a full
year** of backfill — comparable in shape to the other GRIB2/zip-archive pipelines in this
repo but the single largest per-day volume of any of them; a full historical backfill from
`DEFAULT_BACKFILL_START` should be run detached, and GRIBs left un-deleted (`--keep-grib`)
only when actively debugging a decode issue, given the disk cost.

**The devcontainer image must be rebuilt** (`docker compose build devcontainer`) before
`just refresh-msm` can run end-to-end inside it — the baked venv predates the `eccodes` /
`eccodeslib` dependency this pipeline added (`pyproject.toml`, `uv.lock`). Until that rebuild
happens, the download+extract step can still run **host-side**
(`uv run python scripts/download_jma_msm_surface_forecast.py ...`, no Spark/metastore
needed) because `msm_grib.py`'s eccodes import only matters there; the load step
(`just python scripts/load_jma_msm_surface_forecast.py`) works regardless of the
devcontainer's eccodes state, because the load path (`power_market_analytics/msm.py`,
`MsmForecastCsvLoader`) is **eccodes-free by design** — all eccodes code lives in
`msm_grib.py`, imported only by the extraction/download path, never by the raw loader.

### 8.3 Resume behavior

Both `download_file` (per GRIB2 file) and `extract_day` (per delivery day) are idempotent
caches by default: an already-downloaded GRIB2 file or an already-extracted `csv.gz` is
reused unless `--force`. On any failure mid-day — a download error, a decode error, or the
record-count sanity check (`len(stations) * 24`) failing — **nothing is ever left at the
day's `csv_path`**: the manifest is written first and the `csv.gz` committed last
([§6.2](#62-manifest)), so the `csv.gz`'s existence is the sole "this day is done" signal the
cache check trusts, and a half-written day is always re-attempted by a later non-`--force`
call rather than silently treated as complete. GRIB2 files already downloaded before a
mid-day failure are left in place (for inspection, and to avoid re-downloading them on
retry) even though the day itself isn't marked done.

### 8.4 RISH TLS chain workaround

Since a server-side leaf certificate renewal on **2026-05-28**,
`database.rish.kyoto-u.ac.jp` serves a **stale intermediate** ("NII Open Domain CA - G7
RSA") for a leaf now issued by "NII Open Domain CA - G8 RSA" — an incomplete chain. Browsers
and macOS `curl` tolerate this (they chase the correct intermediate via the leaf's Authority
Information Access extension); Python's `requests`/`certifi` does not, and download attempts
fail with `unable to get local issuer certificate`.

**Workaround** (used for this pipeline's verification run and documented here for future
operators, until RISH fixes their chain): fetch the missing G8 intermediate certificate from
the leaf's AIA URL, append it in PEM form to a copy of the `certifi` bundle, and point
`REQUESTS_CA_BUNDLE` at the combined file for the download step:

```bash
curl -s http://repo1.secomtrust.net/sppca/nii/odca4/nii-odca4g8rsa.cer | \
  openssl x509 -inform der -outform pem >> combined-ca-bundle.pem
cat "$(python -c 'import certifi; print(certifi.where())')" >> combined-ca-bundle.pem
REQUESTS_CA_BUNDLE=combined-ca-bundle.pem uv run python scripts/download_jma_msm_surface_forecast.py ...
```

No code change was needed or made — `MsmDownloader`'s injectable `session` parameter is the
code-level seam available if a permanent fix (e.g. a custom `requests.Session` with the
bundle baked in) is ever wanted. This may resolve itself whenever RISH corrects their
server's certificate chain; re-check without the workaround periodically.

## 9. Verification results (one-day end-to-end, 2026-08-21)

Verified by running the full pipeline — download, decode, load, `dbt build` — for delivery
day **2026-08-19** (reference run **2026-08-17T12:00:00Z**, the 12 UTC D−2 run
[§3](#3-vintage-policy) selects).

### 9.1 Extraction

- **3,576 rows extracted** = 149 stations × 24 hours, exactly the expected
  `len(stations) * HOURS_PER_DELIVERY_DAY` record count.
- `forecast_valid_at` spans JST **01:00–24:00** of the delivery day (24:00 stored as
  next-day 00:00, per convention).
- File sizes: FH16-33 **78,716,561 bytes**, FH34-39 **26,238,929 bytes**, FH40-51
  **52,477,745 bytes** — **~157 MB** total for the day's three GRIB2 downloads. The
  resulting `csv.gz` extract is **~205 KB**.
- GRIB2 files were deleted after the successful extract (the default; `--keep-grib` was not
  used). The manifest recorded `file_name`/`url`/`sha256`/`size_bytes` for all three source
  files.
- The real file's element and grid metadata matched **every** entry in
  [§5.2](#52-element-identification-and-the-surface-element-table)'s table exactly, including
  `productionStatusOfProcessedData = 0` on every message and the grid geometry in
  [§5.3](#53-grid-metadata-and-scan-handling) (`Ni=481`, `Nj=505`, first point 47.6°N/120.0°E,
  increments 0.0625°/0.05°, scan order i+ j− north-to-south, `jPointsAreConsecutive = 0`).
- The multi-field-message defect ([§5.1](#51-the-multi-field-message-gotcha)) was found and
  fixed during this run: without `codes_grib_multi_support_on()`, the real FH16-33 file
  yielded 1 field instead of the expected 216 (12 elements × 18 leads it physically
  contains), and extraction failed the completeness check. After the fix, extraction
  succeeded cleanly.

### 9.2 dbt build

Full `just dbt build`: **624/624 PASS** — every contract, every grain-uniqueness test, and
every relationship test (`dim_jma_station`, `dim_date`) held, including every physical-range
test on real data (notably `relative_humidity_pct` staying within its asserted 0–100 bound).

The full backfill (2022-04-01 → 2026-08-23, run 2026-08-21/22: 1,606 delivery days,
252.8 GB of GRIB2 fetched sequentially at ~80 days/hour, 320 MB of csv.gz extracts, every
file exactly 3,576 rows, 5,743,056 raw rows loaded) surfaced one real-data refinement: the
`total_cloud_cover_pct` packing overshoot described in [§7](#7-warehouse-models), after
which the build is again fully green. A dozen transient RISH connection resets during the
run were absorbed by the per-file retry; two laptop-sleep network outages exhausted the
retries, and the resumable cache picked up at the first missing day on relaunch.

### 9.3 Downloader/loader

The 100%-coverage unit test suites for `msm.py`/`msm_grib.py`
(`tests/test_msm.py`, `tests/test_msm_grib.py`, `tests/test_msm_downloader.py`,
`tests/test_msm_loader.py`, `tests/test_msm_scripts.py`) all remained green through this
verification, alongside the real-file run.

### 9.4 Forecast-vs-observed comparison

Joined `fct_jma_msm_weather_forecast_hourly` to `fct_jma_weather_hourly` on `station_id` and
`forecast_valid_at = observed_at` ([§7](#7-warehouse-models)'s join convention) — 146 of the
149 extracted stations had an observation to compare against for this delivery day:

- **Median temperature MAE: 1.66 °C. p90: 2.57 °C.**
- Outliers are **physically explained by grid-vs-station elevation**, not a pipeline defect:
  - `s47639` 富士山 (Mt. Fuji, station elevation 3,775 m) has a **+8.28 °C warm bias** — the
    nearest ~5 km grid cell's terrain elevation is far below the summit's, so the model's
    surface temperature at that grid point is naturally warmer than what a station on the
    actual peak observes.
  - Basin stations — 松本 (Matsumoto, 610 m) and 諏訪 (Suwa, 760 m) — run **~−3 °C** cold,
    consistent with basin cold-pooling the ~5 km grid cannot resolve.
  - `s47662` 東京 (Tokyo) maps to grid point 35.70°N/139.75°E, **0.923 km** away from the
    station — a representative case of the typical small grid-to-station offset.

These numbers, together with `grid_distance_km`, are the evidence that "nearest grid point"
is a deliberate and disclosed approximation ([§7](#7-warehouse-models)), not a hidden source
of error — any consumer joining on `grid_distance_km` can filter or weight by how far a
station is from its sampled point.
