# JMA MSM GPV Point-Forecast Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download historical JMA MSM surface forecasts (GRIB2) from Kyoto University RISH, extract forecast values at the JMA staffed-station locations of `dim_jma_station`, and publish an hourly, multi-vintage forecast fact (`fct_jma_msm_weather_forecast_hourly`) usable leakage-free at the 09:30 JST D−1 demand-forecast cutoff.

**Architecture:** A new `power_market_analytics/msm.py` holds all pure logic (run/file/URL mapping, JST hour mapping, station seed loading, nearest-grid selection, unit conversions, the CSV loader subclass) with **no eccodes import**, so the load path works in the devcontainer without a venv rebuild. A sibling `power_market_analytics/msm_grib.py` holds everything that touches ecCodes (GRIB message identification by metadata, per-file station extraction) plus the `MsmDownloader` orchestrator (download → validate → extract → per-day `csv.gz` + manifest → delete GRIB). Scripts, a contract YAML, `just refresh-msm`, and dbt raw→stg→std→fct models follow the e-Stat/JMA patterns already in the repo.

**Tech Stack:** Python 3.13, `eccodes` (PyPI binary wheels bundle the native library — no Dockerfile change), requests, PySpark, dbt-spark.

**Spec:** `docs/superpowers/specs/2026-08-21-jma-msm-gpv-point-forecast-ingestion-design.md` — the binding authority for every value below. Read it before implementing.

## Global Constraints

- 100% test coverage (`just test` gates at `fail_under = 100`); every task leaves the suite green.
- `just lint` (ruff E4/E7/E9/F/I, line length 100) and `just mypy` clean after every task.
- NumPy-style docstrings (`Parameters` / `Returns` / `Raises` with underlined headers).
- No real HTTP in tests; downloaders take an injectable `session`; scripts are tested through `main(argv)` with classes swapped in the module namespace (`tests/support.import_script`).
- Tests must not download a production MSM file; GRIB fixtures are built deterministically from ecCodes samples.
- Every dbt model: enforced contract (`data_type` on every column) + a uniqueness test on its primary key; generic-test args under `arguments:`.
- Warehouse standardized/curated timestamps are JST (naive); raw keeps the UTC strings so the conversion is auditable.
- Do not hardcode the station count anywhere (seed currently has 149 staffed JEPX-area stations: 146 active + 3 discontinued — all are extracted).
- No population weighting, no TSO-area aggregation, no demand-strategy changes.
- Sequential, throttled downloads only — RISH is an academic archive (no parallel requests).
- The forecast run for delivery day D is always the 12:00 UTC run of D−2 (= 21:00 JST D−2); `forecast_reference_at` stays in the fact grain.
- The PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` you Edit/Write — re-Read a file before a follow-up Edit if it may have been reformatted.

## Key domain values (used by several tasks)

- RISH URL: `https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/{YYYY}/{MM}/{DD}/{filename}` where `YYYY/MM/DD` is the **reference date** (D−2).
- Filenames (R = D−2 as `YYYYMMDD`), leads used, JST delivery hours, JEPX time codes:

| filename | leads used | JST hour ending | time codes |
|---|---|---|---|
| `Z__C_RJTD_{R}120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin` | 28–33 | 01:00–06:00 | 1–12 |
| `Z__C_RJTD_{R}120000_MSM_GPV_Rjp_Lsurf_FH34-39_grib2.bin` | 34–39 | 07:00–12:00 | 13–24 |
| `Z__C_RJTD_{R}120000_MSM_GPV_Rjp_Lsurf_FH40-51_grib2.bin` | 40–51 | 13:00–24:00 | 25–48 |

- `valid_at_utc = reference_at_utc + lead hours`; JST = UTC + 9 h (fixed, no DST). Lead 28 = hour ending 01:00 JST on D; lead 51 = hour ending 24:00 JST on D (stored as 00:00 of D+1, exactly like `fct_jma_weather_hourly.observed_at`). `hour_ending = lead − 27`; `time_codes = (2·hour_ending − 1, 2·hour_ending)`; downstream reverses with `hour_ending = (time_code + 1) // 2`.
- Example: delivery 2026-08-19 → files initialized `20260817120000` in directory `2026/08/17`.
- `EARLIEST_DELIVERY_DATE = date(2019, 4, 1)` (12 UTC runs reach FH51 only from March 2019); `DEFAULT_BACKFILL_START = date(2022, 4, 1)`.
- Default `--end-date` = JST today + 1 day (the 12 UTC run of "yesterday JST" is distributed ~23:30 JST, so D = yesterday+2 = tomorrow is the newest safely-complete delivery day).
- Conversions: `temperature_c = K − 273.15`; `*_hpa = Pa / 100`; `wind_speed_ms = sqrt(u² + v²)`; `solar_radiation_mjm2 = W/m² × 3600 / 1e6`; precipitation `kg/m² ≡ mm` (identity).
- Instantaneous elements are valid AT the hour-ending instant; precipitation (accumulation) and shortwave radiation (mean flux) cover `[valid_at − 1h, valid_at)`.
- GRIB messages are identified ONLY by metadata (JMA does not guarantee ordering); production status (`productionStatusOfProcessedData`) must be `0` (operational) or the message is rejected.
- MSM surface grid (expected, but always read from the file): 505 rows (lat 47.6 → 22.4, step 0.05) × 481 columns (lon 120 → 150, step 0.0625) = 242,905 points, i-fastest scan, north→south.

---

### Task 1: Pure MSM core (`power_market_analytics/msm.py`)

**Files:**
- Create: `power_market_analytics/msm.py`
- Test: `tests/test_msm.py`

**Interfaces (Produces — later tasks import these exact names):**

```python
BASE_URL = "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original"
EARLIEST_DELIVERY_DATE = datetime.date(2019, 4, 1)
DEFAULT_BACKFILL_START = datetime.date(2022, 4, 1)
JST = datetime.timezone(datetime.timedelta(hours=9))

class MsmError(RuntimeError): ...          # base for download/extract errors

@dataclass(frozen=True)
class MsmStation:
    station_id: str
    latitude: float
    longitude: float

def load_stations(stations_csv: Path | str, station_areas_csv: Path | str) -> list[MsmStation]
    # utf-8 DictReader over dbt/seeds/jma_stations.csv (+ jma_station_areas.csv);
    # keeps every station in jma_stations.csv (active AND discontinued), sorted by
    # station_id; raises MsmError naming the station ids if any station lacks a
    # mapping row in jma_station_areas.csv or has an empty latitude/longitude.
    # Extra mapping rows without a seed station are ignored (dim joins left from stations).

def reference_at_for(delivery_date: datetime.date) -> datetime.datetime
    # tz-aware UTC datetime: (delivery_date - 2 days) at 12:00 UTC.

def issue_cutoff_for(delivery_date: datetime.date) -> datetime.datetime
    # tz-aware JST datetime: 09:30 JST on delivery_date - 1 day (the demand-model cutoff).

@dataclass(frozen=True)
class MsmSourceFile:
    file_name: str        # e.g. "Z__C_RJTD_20260817120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin"
    url: str              # BASE_URL + /YYYY/MM/DD/ + file_name  (reference date)
    leads_used: range     # range(28, 34) / range(34, 40) / range(40, 52)

def source_files_for(delivery_date: datetime.date) -> tuple[MsmSourceFile, MsmSourceFile, MsmSourceFile]

def valid_at_for(delivery_date: datetime.date, lead_hours: int) -> datetime.datetime
    # tz-aware UTC datetime reference_at_for(...) + lead_hours.

def hour_ending_for(lead_hours: int) -> int          # lead 28..51 -> 1..24 (ValueError otherwise)
def time_codes_for(hour_ending: int) -> tuple[int, int]   # h -> (2h-1, 2h) (ValueError outside 1..24)

def kelvin_to_celsius(v: float) -> float
def pa_to_hpa(v: float) -> float
def wind_speed(u: float, v: float) -> float
def wm2_to_mjm2(v: float) -> float
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float   # R = 6371.0088

@dataclass(frozen=True)
class MsmGrid:
    ni: int                  # points per row (longitude)
    nj: int                  # rows (latitude)
    first_latitude: float
    first_longitude: float
    latitude_step: float     # SIGNED: negative when scanning north->south
    longitude_step: float    # SIGNED: negative when iScansNegatively

@dataclass(frozen=True)
class SelectedGridPoint:
    latitude: float
    longitude: float
    flat_index: int          # j * ni + i (i-fastest scan; the grib layer asserts jPointsAreConsecutive == 0)
    distance_km: float       # haversine, rounded to 3 decimals

def select_grid_point(grid: MsmGrid, latitude: float, longitude: float) -> SelectedGridPoint
    # j_exact = (latitude - first_latitude) / latitude_step (same for i with longitude);
    # raises MsmError("... outside the MSM domain ...") unless 0 <= j_exact <= nj-1
    # and 0 <= i_exact <= ni-1 (inclusive corner extent).
    # Nearest index with DETERMINISTIC tie-break toward the LOWER index:
    #   low = math.floor(exact); idx = low if exact - low <= 0.5 else low + 1
    # (an exact .5 fraction picks the lower index = the point encountered first in scan order).

@dataclass(frozen=True)
class MsmElement:
    key: str                   # canonical stem, drives the record field it fills
    discipline: int
    parameter_category: int
    parameter_number: int
    surface_type: int          # typeOfFirstFixedSurface
    statistical: bool          # True = 1-hour interval (accum/avg); False = instantaneous

MSM_SURFACE_ELEMENTS: tuple[MsmElement, ...]   # exactly these 12, keyed by (discipline, category, number):
#   ("surface_pressure_pa",    0, 3, 0,   1, False)
#   ("sea_level_pressure_pa",  0, 3, 1, 101, False)
#   ("u_wind_ms",              0, 2, 2, 103, False)
#   ("v_wind_ms",              0, 2, 3, 103, False)
#   ("temperature_k",          0, 0, 0, 103, False)
#   ("relative_humidity_pct",  0, 1, 1, 103, False)
#   ("precipitation_mm",       0, 1, 8,   1, True)
#   ("shortwave_radiation_wm2",0, 4, 7,   1, True)
#   ("total_cloud_cover_pct",  0, 6, 1,   1, False)
#   ("low_cloud_cover_pct",    0, 6, 3,   1, False)
#   ("middle_cloud_cover_pct", 0, 6, 4,   1, False)
#   ("high_cloud_cover_pct",   0, 6, 5,   1, False)
# (surface_type values are asserted when decoding; Task 6's real-file E2E confirms them
#  against the JMA format PDF — if one differs, fix the constant here and the fixture
#  builder inherits it.)

def element_for(discipline: int, parameter_category: int, parameter_number: int) -> MsmElement | None

RAW_CSV_COLUMNS: tuple[str, ...]   # exact header order of the extracted csv.gz — see Task 3

class MsmForecastCsvLoader(CsvLoader):
    # only override: _resolve_files() globs "*.csv.gz" when filepath is a directory
    # (mirror CsvLoader._resolve_files otherwise, incl. FileNotFoundError message
    # "No MSM forecast csv.gz files found at {filepath}").
```

Module docstring: follow `estat.py`'s style — what MSM GPV is, the RISH archive, the 12 UTC D−2 vintage choice and 09:30 JST cutoff, pointer to `docs/JMA-MSM-GPV-Retrieval.md`.

- [ ] **Step 1:** Write `tests/test_msm.py` first (TDD; class-per-area layout like `tests/test_estat.py`), covering at minimum:
  - `reference_at_for(date(2026, 8, 19)) == datetime(2026, 8, 17, 12, tzinfo=UTC)` and its JST rendering is 21:00 D−2.
  - `reference_at_for(D)` is strictly before `issue_cutoff_for(D)` (the 09:30 JST D−1 leakage gate), for several D.
  - `source_files_for(date(2026, 8, 19))`: exact three file names, exact URLs (directory `2026/08/17`), exact `leads_used` ranges; order FH16-33, FH34-39, FH40-51.
  - `valid_at_for`: lead 28 → 2026-08-18 16:00 UTC (= 01:00 JST on D), lead 51 → 2026-08-19 15:00 UTC (= 24:00 JST of D stored as next-day 00:00).
  - `hour_ending_for`: 28→1, 33→6, 34→7, 39→12, 40→13, 51→24; 27 and 52 raise ValueError.
  - `time_codes_for`: 1→(1,2), 24→(47,48); round-trips with `(time_code + 1) // 2`; 0 and 25 raise.
  - Conversions: `kelvin_to_celsius(273.15) == 0.0`; `pa_to_hpa(101325.0) == 1013.25`; `wind_speed(3.0, 4.0) == 5.0`; `wm2_to_mjm2(250.0) == pytest.approx(0.9)`.
  - `haversine_km`: 0 at identical points; a known pair (e.g. (35.0, 139.0) → (35.05, 139.0) ≈ 5.56 km, abs=0.01).
  - `select_grid_point`: interior nearest on a synthetic north→south grid (`MsmGrid(ni=5, nj=5, first_latitude=36.0, first_longitude=139.0, latitude_step=-0.05, longitude_step=0.0625)`); exact-tie at a half-step midpoint picks the LOWER index on each axis; a south→north grid (`latitude_step=+0.05`) selects the same geographic point; `flat_index == j * ni + i`; out-of-domain raises `MsmError` on all four edges (just outside each corner); the four corner points themselves are in-domain.
  - `load_stations`: tmp CSVs shaped like the seeds (same headers) — happy path sorted by station_id; a discontinued station (non-empty `observation_ended_on`) is KEPT; missing mapping row raises `MsmError` naming the station; empty latitude raises; extra mapping-only row ignored.
  - Real-seed sanity: `load_stations("dbt/seeds/jma_stations.csv", "dbt/seeds/jma_station_areas.csv")` returns a non-empty list (no count assertion) and every station is inside lat 22.4–47.6 / lon 120–150.
  - `EARLIEST_DELIVERY_DATE == date(2019, 4, 1)`; `DEFAULT_BACKFILL_START == date(2022, 4, 1)`.
- [ ] **Step 2:** Run `uv run pytest tests/test_msm.py -x -q` — all fail (module absent).
- [ ] **Step 3:** Implement `power_market_analytics/msm.py`.
- [ ] **Step 4:** `uv run pytest tests/test_msm.py -q` passes; then `just test` (full suite, 100% gate), `just lint`, `just mypy`.
- [ ] **Step 5:** Commit `feat: add MSM GPV pure core (run mapping, grid selection, conversions)`.

---

### Task 2: GRIB decode layer (`power_market_analytics/msm_grib.py`) + eccodes dependency

**Files:**
- Modify: `pyproject.toml` (add `"eccodes>=2.47"` to `[project] dependencies`, alphabetical; add `module = ["eccodes.*", "gribapi.*"]` / `ignore_missing_imports = true` to the mypy overrides block — merge with the existing plotly/shap override list if cleaner)
- Modify: `uv.lock` (via `uv sync`)
- Create: `power_market_analytics/msm_grib.py` (decode part; `MsmDownloader` is Task 3 in the same file)
- Create: `tests/msm_grib_support.py` (deterministic GRIB2 fixture builder — plain helpers like `tests/support.py`, importable, not fixtures)
- Test: `tests/test_msm_grib.py`

**Interfaces:**
- Consumes (Task 1): `MsmElement`, `MSM_SURFACE_ELEMENTS`, `element_for`, `MsmGrid`, `select_grid_point`, `MsmStation`, `MsmError`, `source_files_for`, `reference_at_for`, `valid_at_for`, conversions.
- Produces:

```python
class MsmExtractError(MsmError): ...

@dataclass(frozen=True)
class StationHourRecord:
    station_id: str
    station_latitude: float
    station_longitude: float
    grid_latitude: float
    grid_longitude: float
    grid_distance_km: float
    forecast_reference_at: datetime.datetime   # tz-aware UTC
    forecast_valid_at: datetime.datetime       # tz-aware UTC
    forecast_lead_hours: int
    values: dict[str, float | None]            # keyed by MsmElement.key + derived
                                               # "wind_speed_ms", "solar_radiation_mjm2"
    source_file_name: str

def extract_station_records(
    grib_path: Path,
    source_file: MsmSourceFile,
    reference_at: datetime.datetime,
    stations: Sequence[MsmStation],
) -> list[StationHourRecord]
```

`extract_station_records` behavior (all metadata via `eccodes` — `codes_grib_new_from_file` loop over the multi-message file, `codes_release` in `finally`):
- Per message read: `editionNumber` (must be 2), `productionStatusOfProcessedData` (must be 0 → else `MsmExtractError` naming the file and status — spec: reject non-operational), `discipline`, `parameterCategory`, `parameterNumber`, `typeOfFirstFixedSurface`, `endStep`, `dataDate`, `dataTime`.
- `dataDate`/`dataTime` must equal `reference_at` (guards a mislabeled archive member); mismatch → `MsmExtractError`.
- Unmatched parameter triples (`element_for(...) is None`) are skipped silently; matched messages with `endStep` outside `source_file.leads_used` are skipped (the FH16-33 file's leads 16–27 fall here).
- Matched + wanted: assert `typeOfFirstFixedSurface == element.surface_type` (else `MsmExtractError` — the format changed); read grid keys `Ni`, `Nj`, `latitudeOfFirstGridPointInDegrees`, `longitudeOfFirstGridPointInDegrees`, `iDirectionIncrementInDegrees`, `jDirectionIncrementInDegrees`, `iScansNegatively`, `jScansPositively`, `jPointsAreConsecutive` (must be 0) and build `MsmGrid` with SIGNED steps (`latitude_step = +j_inc if jScansPositively else -j_inc`; `longitude_step = -i_inc if iScansNegatively else +i_inc`).
- Grid-point selection per station is cached per distinct `MsmGrid` within the call (dict keyed by the frozen dataclass).
- Values: `codes_get_values(msg)` (one full grid, ~243k doubles — one message at a time, never the whole file's grids at once); if `codes_get(msg, "bitmapPresent")` is 1, compare against `codes_get(msg, "missingValue")` — a missing value at a station's flat index yields `None` for that element.
- Statistical elements (`element.statistical`): the represented hour is `(endStep − 1, endStep]`; instantaneous: the value AT `endStep`. Both land on the record with `forecast_lead_hours = endStep` — document this in the function docstring.
- After the message loop: every (station × lead in `leads_used`) must have ALL 12 element values present (present = key set, possibly None from bitmap); any absent element/lead → `MsmExtractError` listing the missing (element, lead) pairs — an absent message is a completeness failure, never an empty forecast.
- Derived values per record: `temperature_c` (from `temperature_k`), `surface_pressure_hpa`, `sea_level_pressure_hpa` (from `*_pa`), `wind_speed_ms` (None if u or v is None), `solar_radiation_mjm2` (None if wm2 None); numeric values rounded to 6 decimals, `grid_distance_km` to 3. The record's `values` dict holds exactly the value-column names of `RAW_CSV_COLUMNS` (Task 3 writes them by name).
- Records sorted by `(station_id, forecast_lead_hours)`.

`tests/msm_grib_support.py` — deterministic fixture builder:

```python
def build_message(
    element: MsmElement,
    *,
    lead_hours: int,
    reference_at: datetime.datetime,
    grid: MsmGrid,
    values: Sequence[float],
    production_status: int = 0,
    missing_indices: Sequence[int] = (),
    surface_type: int | None = None,   # override to test the mismatch error
) -> bytes
    # codes_grib_new_from_samples("regular_ll_sfc_grib2"); set keys:
    # dataDate/dataTime from reference_at; discipline/parameterCategory/parameterNumber;
    # typeOfFirstFixedSurface (surface_type or element.surface_type);
    # productionStatusOfProcessedData; Ni/Nj/first/last lat-lon/increments/scan flags
    # from grid; for statistical elements set the product-definition template to a
    # statistical one (codes_set(msg, "productDefinitionTemplateNumber", 8)) and the
    # interval so endStep == lead_hours (startStep = lead_hours - 1), else set
    # forecastTime = lead_hours; when missing_indices: codes_set(msg, "missingValue", ...),
    # codes_set(msg, "bitmapPresent", 1), then codes_set_values with the sentinel at
    # those indices; codes_get_message -> bytes; codes_release in finally.

def build_file(path: Path, messages: Iterable[bytes]) -> Path   # concatenates (GRIB files are concatenations)
def build_day_file(path, source_file, reference_at, grid, value_for) -> Path
    # all 12 elements x source_file.leads_used, value_for(element_key, lead, flat_index) -> float
```

- [ ] **Step 1:** `uv add eccodes` (updates `pyproject.toml` + `uv.lock`; then hand-adjust the constraint to `>=2.47` if uv pinned differently) and add the mypy override. Run `uv run python -c "import eccodes; print(eccodes.codes_get_api_version())"` to prove the wheel works.
- [ ] **Step 2:** Write `tests/msm_grib_support.py`, then `tests/test_msm_grib.py` (TDD) covering:
  - Identification by metadata with messages written in SHUFFLED order (fixed seed / reversed list) — extraction result identical to sorted order.
  - `production_status=1` on any message → `MsmExtractError` mentioning the status.
  - An extra message with an unmatched parameter triple (e.g. discipline 0, category 19, number 0) is ignored.
  - Leads outside `leads_used` (e.g. lead 16 in the FH16-33 file) are skipped, and their absence from output is verified.
  - A missing expected element for one wanted lead → `MsmExtractError` naming it.
  - `dataDate` mismatch → `MsmExtractError`.
  - `surface_type` mismatch (via the override) → `MsmExtractError`.
  - `jPointsAreConsecutive == 1` → `MsmExtractError` (set via builder override or a dedicated builder arg).
  - Statistical vs instantaneous: precipitation built with interval (start ℓ−1, end ℓ) lands on `forecast_lead_hours == ℓ`.
  - Bitmap: a station whose flat index is in `missing_indices` gets `None` for that element; wind_speed None when u missing; solar None when radiation missing; other stations unaffected.
  - Values land on the right station: two stations at different grid points get each point's value (scan-direction handling through real eccodes keys — build one file with `jScansPositively=1` too, if the sample template permits setting it; if the template rejects the key, assert the builder raises and drop that variant — document which).
  - A station outside the synthetic grid's extent → `MsmError` (from `select_grid_point`, surfaced through `extract_station_records`).
  - Conversions surface in `values`: temperature_k 288.15 → temperature_c 15.0, pressures /100, wind speed from u/v, radiation W/m² → MJ/m².
  - Records sorted by (station_id, lead); reference/valid datetimes are tz-aware UTC and `valid == reference + lead`.
- [ ] **Step 3:** Run the new tests — fail (module absent).
- [ ] **Step 4:** Implement the decode part of `power_market_analytics/msm_grib.py`.
- [ ] **Step 5:** `uv run pytest tests/test_msm_grib.py -q`, then `just test`, `just lint`, `just mypy` — all green.
- [ ] **Step 6:** Commit `feat: add ecCodes GRIB2 decode layer for MSM surface files`.

---

### Task 3: Downloader + per-day pipeline + loader contract

**Files:**
- Modify: `power_market_analytics/msm_grib.py` (add `MsmDownloadError`, `MsmDownloader`)
- Create: `conf/schemas/jma_msm_surface_forecast.yaml`
- Test: `tests/test_msm_downloader.py`, `tests/test_msm_loader.py`

**Interfaces:**
- Consumes: Task 1 (`source_files_for`, `reference_at_for`, `RAW_CSV_COLUMNS`, `MsmForecastCsvLoader`, `EARLIEST_DELIVERY_DATE`), Task 2 (`extract_station_records`, builder helpers in tests).
- Produces:

```python
class MsmDownloadError(MsmError): ...

class MsmDownloader:
    def __init__(
        self,
        data_dir: Path | str = Path("data/jma/msm_surface_forecast"),
        timeout: float = 60.0,
        session: requests.Session | None = None,
        request_interval: float = 1.0,
        max_attempts: int = 3,
    ) -> None
    # grib_dir = data_dir/"grib"; csv_dir = data_dir/"csv"
    def csv_path_for(self, delivery_date: datetime.date) -> Path      # csv_dir/msm_surface_YYYYMMDD.csv.gz
    def manifest_path_for(self, delivery_date: datetime.date) -> Path # csv_dir/msm_surface_YYYYMMDD.json
    def grib_path_for(self, source_file: MsmSourceFile) -> Path       # grib_dir/<file_name>
    def download_file(self, source_file: MsmSourceFile, force: bool = False) -> tuple[Path, str]
    #   returns (path, sha256-hex)
    def extract_day(self, delivery_date, stations, force=False, keep_grib=False) -> Path
    def download_range(self, start_date, end_date, stations, force=False, keep_grib=False) -> list[Path]
```

Behavior (each point tested):
- `download_file`: cached GRIB (path exists, not force) → recompute sha256 from the file, no HTTP. Otherwise GET with `stream=True`, `timeout`; throttle like `EstatCensusMeshDownloader._throttle` (monotonic clock, `request_interval`); stream 1 MiB chunks to `<name>.part` while updating `hashlib.sha256`; on HTTP 404 raise `MsmDownloadError` naming the URL and saying the archive file is absent (RISH gap / not yet published — a completeness failure, never an empty forecast); other HTTP errors and `requests.RequestException` retry up to `max_attempts` with `request_interval * attempt` sleep between attempts, then re-raise; after streaming: content non-empty and starts with `b"GRIB"` else `MsmDownloadError` (and the `.part` is deleted); atomic `Path.replace` to the final path.
- `extract_day`: if `csv_path_for(d)` exists and not force → log + return it (no HTTP, no decode). Else for each of the three `source_files_for(d)` sequentially: `download_file` then `extract_station_records` (one file decoded at a time; rows accumulated per file — never all three GRIBs in memory). Sanity: total records == `len(stations) * 24` else `MsmExtractError`. Write the csv.gz atomically (`.part` → `replace`): `gzip.open(partial, "wt", newline="")` + `csv.writer`, header = `RAW_CSV_COLUMNS`, rows sorted by (station_id, lead); timestamps rendered `YYYY-MM-DDTHH:MM:SSZ` (`strftime("%Y-%m-%dT%H:%M:%SZ")` on the tz-aware UTC values); floats via `str(round(v, 6))` (`str(v)` where already rounded), `None` → empty string. Write the manifest json atomically next to it: `{"delivery_date": "...", "reference_at_utc": "...Z", "files": [{"file_name", "url", "sha256", "size_bytes"}...]}` (`json.dumps(..., indent=2)`). Delete the three GRIBs after success unless `keep_grib`. On any extraction error: no file at the final csv path (partial cleaned up in a `finally`/except), GRIBs left for inspection.
- `download_range`: inclusive date walk; `start > end` or `start < EARLIEST_DELIVERY_DATE` → `ValueError`; logs per day; returns csv paths in date order.

`RAW_CSV_COLUMNS` (defined in Task 1, written here, mirrored by the contract YAML — exact order):

```text
station_id, station_latitude, station_longitude, grid_latitude, grid_longitude,
grid_distance_km, forecast_reference_at_utc, forecast_valid_at_utc, forecast_lead_hours,
temperature_c, relative_humidity_pct, u_wind_ms, v_wind_ms, wind_speed_ms,
precipitation_mm, surface_pressure_hpa, sea_level_pressure_hpa, shortwave_radiation_wm2,
solar_radiation_mjm2, total_cloud_cover_pct, high_cloud_cover_pct, middle_cloud_cover_pct,
low_cloud_cover_pct, source_file_name
```

`conf/schemas/jma_msm_surface_forecast.yaml` (style of `estat_census_population_mesh.yaml`; description explains the product, the 12 UTC D−2 vintage, UTC-string timestamps and the doc pointer):

```yaml
grain: [station_id, forecast_reference_at_utc, forecast_valid_at_utc]
columns:
  - { name: station_id, type: string, nullable: false }
  - { name: station_latitude, type: double, nullable: false }
  - { name: station_longitude, type: double, nullable: false }
  - { name: grid_latitude, type: double, nullable: false }
  - { name: grid_longitude, type: double, nullable: false }
  - { name: grid_distance_km, type: double, nullable: false }
  - { name: forecast_reference_at_utc, type: string, nullable: false }
  - { name: forecast_valid_at_utc, type: string, nullable: false }
  - { name: forecast_lead_hours, type: int, nullable: false }
  - { name: temperature_c, type: double }
  - { name: relative_humidity_pct, type: double }
  - { name: u_wind_ms, type: double }
  - { name: v_wind_ms, type: double }
  - { name: wind_speed_ms, type: double }
  - { name: precipitation_mm, type: double }
  - { name: surface_pressure_hpa, type: double }
  - { name: sea_level_pressure_hpa, type: double }
  - { name: shortwave_radiation_wm2, type: double }
  - { name: solar_radiation_mjm2, type: double }
  - { name: total_cloud_cover_pct, type: double }
  - { name: high_cloud_cover_pct, type: double }
  - { name: middle_cloud_cover_pct, type: double }
  - { name: low_cloud_cover_pct, type: double }
  - { name: source_file_name, type: string, nullable: false }
```

(No `read_options` needed — the files are UTF-8 ASCII; Spark reads `.csv.gz` transparently.)

- [ ] **Step 1:** Write `tests/test_msm_downloader.py` (TDD) with an estat-style fake session (`get(url, timeout, stream=...)` returning canned responses; responses expose `status_code`, `raise_for_status`, `iter_content`) covering: happy path (3 files → csv.gz + manifest, GRIBs deleted, sha256 in the manifest equals `hashlib.sha256(content).hexdigest()`, csv has `len(stations) * 24` data rows with the exact header); `keep_grib=True` keeps GRIBs; cached csv → zero session calls; `force=True` re-downloads; cached GRIB reused (no HTTP) when csv absent; 404 → `MsmDownloadError` with URL, nothing at the csv path, no `.part` left; non-GRIB bytes → `MsmDownloadError`, no file at final path; empty body → error; one `requests.ConnectionError` then success → succeeds after retry (assert call count); `max_attempts` exhausted → raises; extraction failure (e.g. builder omits an element) → no csv, GRIBs kept; `download_range` inclusive walk + `ValueError` on reversed range and pre-2019-04-01 start; throttling sleeps `request_interval` between requests (monkeypatch `time.sleep`/`time.monotonic` like the estat tests, if they do — else assert via recorded monotonic calls). Timestamp strings in the csv end with `Z` and parse back to the expected UTC instants.
- [ ] **Step 2:** Write `tests/test_msm_loader.py` (TDD, `spark` fixture): loads a written csv.gz into a temp table (types per contract: `forecast_lead_hours` int, values double, timestamps as strings); empty-string cells → NULL doubles; duplicate grain (load the same day file twice via a glob of two copies) → `ValueError` from the grain check; a file with a missing required column fails; `_resolve_files` on a directory finds only `*.csv.gz`.
- [ ] **Step 3:** Run new tests — fail. Implement `MsmDownloader` + the YAML. Re-run — pass.
- [ ] **Step 4:** `just test`, `just lint`, `just mypy` green.
- [ ] **Step 5:** Commit `feat: add MSM downloader pipeline (csv.gz extracts + manifest) and raw load contract`.

---

### Task 4: Scripts + justfile recipe

**Files:**
- Create: `scripts/download_jma_msm_surface_forecast.py`
- Create: `scripts/load_jma_msm_surface_forecast.py`
- Modify: `justfile` (add `refresh-msm` after `refresh-estat`)
- Test: `tests/test_msm_scripts.py`

**Interfaces:**
- Consumes: `MsmDownloader`, `load_stations`, `DEFAULT_BACKFILL_START`, `JST`, `MsmForecastCsvLoader`, `CsvTableSchema`.
- Produces: `default_end_date() -> datetime.date` in `power_market_analytics/msm.py` (JST today + 1 day; added here so the script default is monkeypatchable — one function, documented in Task 1's module if implemented there instead: EITHER location is fine, but it must live in `msm.py`, not the script).

`download_jma_msm_surface_forecast.py` (docstring first line explains ~157 MB per delivery day / ~54 GiB per year and the sequential, throttled policy; pattern: `download_estat_census_population_mesh.py`):
- `--start-date` (`datetime.date.fromisoformat`, default `DEFAULT_BACKFILL_START`), `--end-date` (default `default_end_date()`), `--data-dir` (default `data/jma/msm_surface_forecast`), `--force`, `--keep-grib`.
- Loads stations from `REPO_ROOT / "dbt/seeds/jma_stations.csv"` / `jma_station_areas.csv`, builds `MsmDownloader(data_dir=args.data_dir)`, calls `download_range(...)`, logs the count.

`load_jma_msm_surface_forecast.py`: mirror `load_estat_census_population_mesh.py` — `--schema` default `conf/schemas/jma_msm_surface_forecast.yaml`, `--data` default `data/jma/msm_surface_forecast/csv`, `--table` default `pma_raw.jma_msm_surface_forecast`; `CsvTableSchema.from_yaml` + `MsmForecastCsvLoader(...).load()`.

justfile:

```just
[doc("Refresh JMA MSM surface forecasts: download RISH GRIB2 runs (~157 MB per delivery day; args pass through, e.g. --start-date 2026-08-01 --keep-grib), extract station points, reload raw, rebuild + test dbt")]
refresh-msm *args:
    just python scripts/download_jma_msm_surface_forecast.py {{ args }}
    just python scripts/load_jma_msm_surface_forecast.py
    just dbt build
```

- [ ] **Step 1:** Write `tests/test_msm_scripts.py` (TDD) using `tests/support.import_script`, mirroring `tests/test_download_scripts.py` / `test_load_scripts.py`: defaults wired through (data_dir, start/end, station seeds resolved under the repo root, table/schema defaults); flags `--force` / `--keep-grib` forwarded; `--start-date 2026-08-01 --end-date 2026-08-03` parsed to dates; downloader/loader classes swapped via `monkeypatch.setattr(module, ...)`; `default_end_date()` returns JST today + 1 (freeze by monkeypatching the date source it uses).
- [ ] **Step 2:** Run — fail. Implement both scripts + justfile recipe. Re-run — pass.
- [ ] **Step 3:** `just test`, `just lint`, `just mypy` green. `just --list` shows `refresh-msm`.
- [ ] **Step 4:** Commit `feat: add MSM download/load scripts and refresh-msm recipe`.

---

### Task 5: dbt models (raw source → stg → std → fct)

**Files:**
- Modify: `dbt/models/raw/jma.yml` (add table `jma_msm_surface_forecast` to the existing `jma` source)
- Create: `dbt/models/staging/stg_jma__msm_surface_forecast.sql` + `.yml`
- Create: `dbt/models/standardized/std_jma__msm_surface_forecast.sql` + `.yml`
- Create: `dbt/models/curated/fct_jma_msm_weather_forecast_hourly.sql` + `.yml`

**Interfaces:** consumes the raw table written by Task 3/4; produces the curated fact the spec names.

Raw source yml: description covers the product (MSM GPV Rjp Lsurf via RISH), the single ingested vintage (12 UTC D−2), UTC-string timestamps, grain, doc pointer (`docs/JMA-MSM-GPV-Retrieval.md`); column list = the 24 raw columns with descriptions; `not_null` data_tests on the three grain columns.

`stg_jma__msm_surface_forecast.sql`: explicit-column `select` from `{{ source('jma', 'jma_msm_surface_forecast') }}` (style of `stg_estat__census_population_mesh.sql`). YML: enforced contract (string/double/int per the load contract), `dbt_utils.unique_combination_of_columns` over `[station_id, forecast_reference_at_utc, forecast_valid_at_utc]` (args under `arguments:`), `not_null` on the grain + station/grid coordinates + lead + source_file_name.

`std_jma__msm_surface_forecast.sql` — typed JST time axis (JST = UTC + 9 h, fixed offset; `timestampadd` keeps the wall-clock arithmetic explicit and auditable from the retained raw strings):

```sql
with
  source as (
  select * from {{ ref('stg_jma__msm_surface_forecast') }}
  ),

  final as (
  select
    station_id,
    timestampadd(hour, 9, to_timestamp(forecast_reference_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
      as forecast_reference_at,
    timestampadd(hour, 9, to_timestamp(forecast_valid_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
      as forecast_valid_at,
    timestampadd(hour, 8, to_timestamp(forecast_valid_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'"))
      as forecast_hour_start_at,
    cast(timestampadd(hour, 8, to_timestamp(forecast_valid_at_utc, "yyyy-MM-dd'T'HH:mm:ss'Z'")) as date)
      as forecast_date,
    forecast_lead_hours,
    station_latitude,
    station_longitude,
    grid_latitude,
    grid_longitude,
    grid_distance_km,
    temperature_c,
    relative_humidity_pct,
    u_wind_ms,
    v_wind_ms,
    wind_speed_ms,
    precipitation_mm,
    surface_pressure_hpa,
    sea_level_pressure_hpa,
    shortwave_radiation_wm2,
    solar_radiation_mjm2,
    total_cloud_cover_pct,
    high_cloud_cover_pct,
    middle_cloud_cover_pct,
    low_cloud_cover_pct,
    source_file_name
  from
    source
  )

select * from final
```

(`to_timestamp` parses the literal wall-clock; the session TZ is JST but the pattern carries no zone, so the +9/+8 `timestampadd` produces the JST wall-clock instant — same convention as every other std model; hour 24:00 lands on next-day 00:00 exactly like `std_jma__hourly`.) YML: contract (timestamps/date/int/double), uniqueness over `[station_id, forecast_reference_at, forecast_valid_at]`, `not_null` on keys/time axis/coordinates/lead.

`fct_jma_msm_weather_forecast_hourly.sql` — the spec's exact curated column list:

```sql
with
  standardized as (
  select * from {{ ref('std_jma__msm_surface_forecast') }}
  ),

  final as (
  select
    -- grain: one row per station, forecast run and forecast-valid hour
    station_id,
    forecast_reference_at,
    forecast_valid_at,
    forecast_hour_start_at,
    forecast_lead_hours,
    forecast_date as date_key,
    grid_latitude,
    grid_longitude,
    grid_distance_km,
    temperature_c,
    relative_humidity_pct,
    u_wind_ms,
    v_wind_ms,
    wind_speed_ms,
    precipitation_mm,
    surface_pressure_hpa,
    sea_level_pressure_hpa,
    shortwave_radiation_wm2,
    solar_radiation_mjm2,
    total_cloud_cover_pct,
    high_cloud_cover_pct,
    middle_cloud_cover_pct,
    low_cloud_cover_pct,
    source_file_name
  from
    standardized
  )

select * from final
```

YML (model description: multi-vintage atomic forecast fact; `forecast_valid_at` = END of the represented weather hour matching `fct_jma_weather_hourly.observed_at`, so forecast vs observed joins on `station_id` + timestamp equality; precipitation/radiation cover `[forecast_hour_start_at, forecast_valid_at)`; the MSM value is the nearest grid point, not a station forecast; grid elevation differs from station elevation; downstream maps JEPX periods via `hour_ending = (time_code + 1) // 2`; no half-hour duplication):
- contract enforced; `dbt_utils.unique_combination_of_columns` over `[station_id, forecast_reference_at, forecast_valid_at]`.
- `station_id`: `not_null` + `relationships` to `ref('dim_jma_station')` field `station_id`.
- `date_key`: `not_null` + `relationships` to `ref('dim_date')` field `date_key`.
- `not_null` on the time axis, lead, grid columns, `source_file_name`.
- `forecast_lead_hours`: `dbt_utils.accepted_range` 28–51.
- Physical ranges (`dbt_utils.accepted_range`, args under `arguments:`): `temperature_c` −50..50; `relative_humidity_pct` 0..100; `u_wind_ms`/`v_wind_ms` −80..80; `wind_speed_ms` 0..90; `precipitation_mm` 0..200; `surface_pressure_hpa` 500..1100; `sea_level_pressure_hpa` 870..1090; `shortwave_radiation_wm2` 0..1500; `solar_radiation_mjm2` 0..5.4; cloud covers 0..100; `grid_distance_km` 0..5.

- [ ] **Step 1:** Write all 7 files.
- [ ] **Step 2:** Host-side parse/compile gate: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse` succeeds (full `dbt build` needs the raw table and runs in Task 6).
- [ ] **Step 3:** Commit `feat: add MSM surface forecast dbt models (raw source, stg, std, curated fact)`.

---### Task 6: One-day end-to-end verification (controller-led)

No new code. Run on a real day and against the real warehouse:

- [ ] **Step 1:** Host-side: `uv run python scripts/download_jma_msm_surface_forecast.py --start-date 2026-08-19 --end-date 2026-08-19` (~157 MB from RISH, sequential). Verify `data/jma/msm_surface_forecast/csv/msm_surface_20260819.csv.gz` + manifest; row count = station count × 24.
- [ ] **Step 2:** Devcontainer: `just python scripts/load_jma_msm_surface_forecast.py` (the load path imports `msm.py` only — no eccodes needed in the container), then `just dbt build` (or at least `--select +fct_jma_msm_weather_forecast_hourly` plus its tests) — contracts, uniqueness, relationships, ranges all pass.
- [ ] **Step 3:** Inspect representative stations from several JEPX areas (e.g. s47662 東京, s47772 大阪, s47412 札幌, s47807 福岡) via `just dbt show --inline` — plausible diurnal temperature shape, and compare forecast vs `fct_jma_weather_hourly` at the same station/hour (join on `station_id` and `forecast_valid_at = observed_at`) — differences of a few °C, no systematic garbage. Verify the FH28–51 → JST hour mapping lines up (01:00..24:00 hour-endings on 2026-08-19).
- [ ] **Step 4:** If the real file's GRIB metadata differs from `MSM_SURFACE_ELEMENTS` (surface types) or a range test fails on real values: fix the constants/ranges (a fix-dispatch, reviewed), re-run.
- [ ] **Step 5:** Ledger the observed numbers (row counts, example temperature deltas) for the doc task.

---

### Task 7: Documentation + CLAUDE.md

**Files:**
- Create: `docs/JMA-MSM-GPV-Retrieval.md`
- Modify: `docs/_sidebar.md` (add `- [JMA MSM GPV Retrieval](JMA-MSM-GPV-Retrieval.md)` after the JMA weather entry)
- Modify: `CLAUDE.md` (Commands: `just refresh-msm`; Architecture: the MSM data-flow bullet mirroring the others; Gotchas if E2E surfaced any)

`docs/JMA-MSM-GPV-Retrieval.md` structure (mirror `docs/OCCTO-Demand-Forecast-Retrieval.md`'s depth): product & publisher (MSM GPV, Japan-region surface, grid geometry, run schedule/horizons, product-change history incl. 2019-03 FH51 / 2022-06 FH78 / 2017-12 radiation); the RISH archive (URL pattern, `original` vs `netcdf/MSM-S` and why original; academic-use etiquette, gaps possible, mtime not authoritative); the vintage policy (12 UTC D−2, FH28–51, 09:30 JST D−1 cutoff arithmetic, why not 00 UTC D−1 / 21 UTC D−2); file set + lead table (the table from this plan's header); GRIB2 decoding (element table with discipline/category/number/surface type/statistical semantics, production-status rule, bitmap handling, grid metadata & scan handling, nearest-neighbour + tie-break + persisted grid coords/distance); extract format (`RAW_CSV_COLUMNS`, UTC strings, manifest with sha256); warehouse models (raw → stg → std → fct, grain, JST conversion, `date_key`, join conventions incl. `(time_code + 1) // 2` and forecast-vs-observed join); operations (`just refresh-msm`, `--start-date/--end-date/--force/--keep-grib`, volumes ~157 MB/day ≈ 54 GiB/yr, devcontainer image rebuild note for eccodes, resume behavior); verification results from Task 6.

- [ ] **Step 1:** Write the doc with the Task 6 numbers; update `_sidebar.md` and `CLAUDE.md`.
- [ ] **Step 2:** `just test`, `just lint`, `just mypy` still green (docs-only, but cheap).
- [ ] **Step 3:** Commit `docs: add JMA MSM GPV retrieval doc; wire refresh-msm into CLAUDE.md`.

---

## Self-review notes (spec → task coverage)

- Location scope (seeds, mapping requirement, no station-count hardcoding, grid coords persisted): Tasks 1, 3, 5.
- Run choice + cutoff (12 UTC D−2, cutoff test): Task 1; grain keeps `forecast_reference_at`: Tasks 3, 5.
- Three files only, FH28–51, unused leads dropped: Tasks 1, 2, 3.
- Elements, metadata-only identification, status-0 rule, conversions, interval semantics: Tasks 1, 2 (+5 docs/ranges).
- Grid selection (domain validation, file metadata, scan, bitmap, ties, persisted distance): Tasks 1, 2.
- Downloader requirements (flags, injectable session, timeouts/retries/caching, atomic writes, GRIB validation, memory bounds, deletion policy, absent-file failure, sha256/URL/filename record, politeness): Task 3 (+4 flags).
- Warehouse design (model names, grain, required columns, JST + auditable UTC, hour-ending convention, no half-hour duplication, dim reuse, contracts/uniqueness/relationships): Task 5.
- Testing list: distributed across Tasks 1–4; smoke station-count×24: Task 3; dbt-side: Tasks 5–6; one-day E2E + representative stations: Task 6.
- Acceptance criteria 1–9: 1→T3/T4, 2→T1/T3, 3→T3, 4→T3/T6, 5→T3/T5, 6→T1/T2/T7, 7→T5/T6, 8→T1 (cutoff test), 9→global constraint.
