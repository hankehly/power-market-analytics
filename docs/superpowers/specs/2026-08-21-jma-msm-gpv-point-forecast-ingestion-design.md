# JMA MSM GPV point-forecast ingestion

## Objective

Implement a pipeline that downloads historical JMA MSM surface forecasts from Kyoto University RISH, extracts the forecast values at the JMA staffed-station locations already represented by `dim_jma_station`, and publishes an hourly forecast fact in the warehouse.

The primary use case is leakage-safe day-ahead demand forecasting performed at 09:30 JST on D−1 for the 48 half-hourly periods of delivery day D.

Do not implement population weighting, TSO-area aggregation, or changes to the demand forecasting strategy in this task.

## Location scope

Use the stations already defined by the repository:

- Source station metadata from `dbt/seeds/jma_stations.csv`.
- Require the station to have a mapping in `dbt/seeds/jma_station_areas.csv`.
- Reuse `dim_jma_station`; do not create a second weather-location dimension.
- Do not introduce the previously discussed one-per-prefecture list.
- Do not hardcode the station count. At present the seed contains 149 staffed stations in JEPX areas: 146 active and 3 discontinued.

The MSM values are gridded forecasts sampled at these station coordinates. They are not station forecasts produced specifically for those observation sites.

Persist the selected MSM grid latitude and longitude with every extracted record. This preserves which grid cell was used even if the station dimension’s type-1 coordinates change later.

## Data provider and product

The underlying product is the Japan Meteorological Agency’s MSM GPV:

- MSM means Meso-Scale Model.
- Product: MSM GPV, Japan-region surface fields.
- Publisher: JMA.
- Historical archive/distributor: Kyoto University RISH.
- Format: GRIB2.
- Cost: free.
- Native temporal resolution: hourly for surface fields.
- Spatial grid:
  - Latitude: 47.6°N down to 22.4°N in 0.05° increments.
  - Longitude: 120°E to 150°E in 0.0625° increments.
  - 505 × 481 = 242,905 grid points.
- Current production frequency: eight forecast runs per day.
- Current horizons:
  - 00 and 12 UTC runs: 78 hours.
  - Other runs: 39 hours.

Authoritative references:

- [JMA MSM product catalogue](https://www.data.jma.go.jp/suishin/cgi-bin/catalogue/make_product_page.cgi?id=MesModel)
- [JMBSC MSM distribution description and history](https://www.jmbsc.or.jp/jp/online/file/f-online10200.html)
- [JMA MSM surface GRIB2 format specification](https://www.data.jma.go.jp/suishin/catalogue/format/FcdNwpGrbMsmRjpL_format.pdf)
- [RISH original GPV archive](https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/)

Use the RISH `original` GRIB2 archive. Do not use RISH’s daily `netcdf/MSM-S` products: those are analysis-oriented transformations and do not preserve the original forecast run/vintage structure needed for leakage-safe backtesting.

## Forecast run to ingest

For delivery day D, use the MSM run initialized at:

- 12:00 UTC on D−2.
- Equivalent to 21:00 JST on D−2.
- Normally distributed around 23:30 JST on D−2.

This run is safely available before the model’s 09:30 JST D−1 cutoff and provides one consistent forecast vintage covering all of D.

Do not use the 00:00 UTC D−1 run. Although initialized at 09:00 JST on D−1, MSM distribution is normally about 2 hours 30 minutes after initialization, around 11:30 JST—too late for the model cutoff.

The 21:00 UTC D−2 run should not be used as the sole vintage either. It is newer and normally available around 08:30 JST, but its 39-hour horizon does not cover the final hours of D. Mixing it with an older run would introduce a forecast-vintage discontinuity within the day.

Keep `forecast_reference_at` in the fact grain so additional runs can be ingested later without redesigning the table.

## Files required for each delivery day

For the 12 UTC run on D−2, delivery day D corresponds to forecast hours 28 through 51.

Download these three files:

```text
Z__C_RJTD_{D-2 as YYYYMMDD}120000_MSM_GPV_Rjp_Lsurf_FH16-33_grib2.bin
Z__C_RJTD_{D-2 as YYYYMMDD}120000_MSM_GPV_Rjp_Lsurf_FH34-39_grib2.bin
Z__C_RJTD_{D-2 as YYYYMMDD}120000_MSM_GPV_Rjp_Lsurf_FH40-51_grib2.bin
```

URL pattern:

```text
https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/YYYY/MM/DD/{filename}
```

Use only:

```text
*_MSM_GPV_Rjp_Lsurf_*_grib2.bin
```

Do not download pressure-level `L-pall` files or `FH52-78`.

Lead-hour mapping:

| Source file | Leads used | Delivery hours in JST | JEPX time codes |
|---|---:|---:|---:|
| FH16-33 | FH28–33 only | hour ending 01:00–06:00 | 1–12 |
| FH34-39 | FH34–39 | hour ending 07:00–12:00 | 13–24 |
| FH40-51 | FH40–51 | hour ending 13:00–24:00 | 25–48 |

For example, delivery date 2026-08-19 uses the files initialized at `20260817120000` in the RISH `2026/08/17` directory.

The first file contains unused leads 16–27; download the file but do not retain those forecasts.

Typical combined download volume is approximately 157 MB per delivery day, or about 54 GiB per year. The archive cannot provide a server-side station subset, so reducing the number of extraction locations does not reduce network transfer. Process files sequentially and delete full GRIB2 files after successful extraction unless `--keep-grib` is specified.

The retained point data should be much smaller: approximately 1.3 million hourly station rows per year at the current station count.

## Historical scope

Default the initial backfill to 2022-04-01, matching the available TSO demand-actual history in this repository. Make start and end dates configurable.

The selected 12 UTC/FH28–51 policy is also valid before the June 2022 extension to 78 hours because 12 UTC runs had already been extended through FH51 in March 2019.

Important product changes:

- Approximately 5 km MSM grid from March 2006.
- 39-hour forecasts from 2013.
- Solar radiation added in December 2017.
- 00/12 UTC runs extended through FH51 in March 2019.
- 00/12 UTC runs extended through FH78 in June 2022.

A full delivery day from one pre-cutoff vintage should therefore not be promised before the FH51 extension in March 2019.

## Weather elements

Extract the following surface values. Identifying GRIB messages must use their metadata, never their position within the file; JMA explicitly does not guarantee element ordering.

| Field | GRIB meaning | Native unit | Curated representation |
|---|---|---:|---|
| Temperature | 1.5 m air temperature | K | `temperature_c` |
| Relative humidity | 1.5 m RH | % | `relative_humidity_pct` |
| U wind | 10 m eastward component | m/s | `u_wind_ms` |
| V wind | 10 m northward component | m/s | `v_wind_ms` |
| Wind speed | Derived from U and V | m/s | `wind_speed_ms` |
| Hourly precipitation | Previous-hour accumulation | kg/m² | `precipitation_mm` |
| Surface pressure | Surface pressure | Pa | `surface_pressure_hpa` |
| Sea-level pressure | MSL pressure | Pa | `sea_level_pressure_hpa` |
| Downward shortwave radiation | Previous-hour mean flux | W/m² | retain W/m² and derive `solar_radiation_mjm2` |
| Total cloud | Instantaneous total cover | % | `total_cloud_cover_pct` |
| High cloud | Instantaneous cover | % | `high_cloud_cover_pct` |
| Middle cloud | Instantaneous cover | % | `middle_cloud_cover_pct` |
| Low cloud | Instantaneous cover | % | `low_cloud_cover_pct` |

Conversions:

```text
temperature_c = temperature_k - 273.15
pressure_hpa = pressure_pa / 100
wind_speed_ms = sqrt(u_wind_ms² + v_wind_ms²)
solar_radiation_mjm2 = shortwave_radiation_wm2 × 3600 / 1,000,000
```

Temperature, humidity, wind, pressure and cloud fields are instantaneous at the valid time.

Precipitation covers `[forecast_hour_start_at, forecast_valid_at)`. Solar radiation is the mean flux over that same preceding hour.

Reject GRIB messages whose production status is not operational status `0`.

## Grid-cell selection

For each station, deterministically select the nearest MSM grid point.

Requirements:

- Validate that the station coordinate falls inside the MSM domain.
- Use the coordinates and scan metadata from the GRIB file rather than assuming every historical file is identical.
- Use ecCodes or an equivalent GRIB2-aware decoder.
- Explicitly handle scan direction and missing-value bitmaps.
- Define deterministic tie-breaking.
- Persist:
  - station latitude and longitude used during extraction;
  - selected grid latitude and longitude;
  - great-circle distance from station to grid point.

Do not interpolate between grid cells in the first implementation. Nearest-neighbour sampling is sufficient and auditable.

## Local pipeline

Add an MSM module following the repository’s existing source patterns, likely:

```text
power_market_analytics/msm.py
scripts/download_jma_msm_surface_forecast.py
scripts/load_jma_msm_surface_forecast.py
conf/schemas/jma_msm_surface_forecast.yaml
docs/JMA-MSM-GPV-Retrieval.md
```

Add a `just refresh-msm` recipe that downloads/extracts, reloads the raw table, and runs dbt.

Recommended local flow:

```text
RISH GRIB2
    → validated temporary download
    → selected station/grid-point records
    → compressed CSV or Parquet extracts
    → pma_raw
    → staging
    → standardized
    → curated forecast fact
```

The downloader must:

- Accept `--start-date`, `--end-date`, `--force`, `--keep-grib`, and `--data-dir`.
- Use an injectable HTTP session for tests.
- Use timeouts, bounded retries and resumable caching.
- Download to a `.part` file and rename atomically after validation.
- Validate HTTP status, nonempty content, GRIB magic/signature and expected filename.
- Avoid loading an entire multi-file day into memory.
- Decode and extract one source file at a time.
- Write extracted output atomically.
- Delete temporary GRIB2 after successful extraction unless explicitly retained.
- Treat an absent archive file as a visible completeness failure, not silently as an empty forecast.
- Record source filename, source URL and, where practical, SHA-256.
- Avoid aggressive parallel requests against the academic RISH service.

Add the ecCodes native runtime and Python bindings to the development container and project dependencies. Avoid using `cfgrib`/xarray as the primary abstraction if it causes full-grid materialization or obscures the individual GRIB-message metadata.

## Warehouse design

Use an atomic, multi-vintage forecast fact.

Recommended models:

```text
pma_raw.jma_msm_surface_forecast
stg_jma__msm_surface_forecast
std_jma__msm_surface_forecast
fct_jma_msm_weather_forecast_hourly
```

Curated fact grain:

```text
station_id × forecast_reference_at × forecast_valid_at
```

Required curated columns:

```text
station_id
forecast_reference_at
forecast_valid_at
forecast_hour_start_at
forecast_lead_hours
date_key
grid_latitude
grid_longitude
grid_distance_km
temperature_c
relative_humidity_pct
u_wind_ms
v_wind_ms
wind_speed_ms
precipitation_mm
surface_pressure_hpa
sea_level_pressure_hpa
shortwave_radiation_wm2
solar_radiation_mjm2
total_cloud_cover_pct
high_cloud_cover_pct
middle_cloud_cover_pct
low_cloud_cover_pct
source_file_name
```

Use JST for standardized and curated warehouse timestamps, consistent with the existing JMA observation fact. Retain or parse UTC timestamps at the raw boundary so the conversion remains auditable.

`forecast_valid_at` is the end of the represented weather hour, and:

```text
forecast_hour_start_at = forecast_valid_at - 1 hour
date_key = date(forecast_hour_start_at)
```

This matches `fct_jma_weather_hourly`.

Do not duplicate each hourly forecast into two half-hourly fact rows. Downstream demand features should map JEPX periods to the containing weather hour using the existing convention:

```text
hour_ending = (time_code + 1) // 2
```

Reuse `dim_jma_station` as the station foreign key. Area membership remains available through `dim_jma_station.area_key`; do not copy area-level aggregates into this fact.

Every model must have an enforced dbt contract. The curated fact must have a `dbt_utils.unique_combination_of_columns` test over its grain and a relationship test from `station_id` to `dim_jma_station`.

## Testing

Follow the repository’s existing testing conventions: no real HTTP, injectable dependencies, NumPy-style docstrings, and 100% coverage.

At minimum, test:

- Delivery date → reference date/run mapping.
- Exact URL and filename construction.
- FH28–51 → JST hour-ending mapping.
- JEPX time-code alignment.
- GRIB parameter identification by metadata with messages in different orders.
- Rejection of non-operational production status.
- Temperature, pressure, wind and radiation conversions.
- Previous-hour semantics for precipitation and radiation.
- Nearest-grid selection, scan direction and deterministic ties.
- Out-of-domain station rejection.
- Missing-value handling.
- Partial-download cleanup and atomic output.
- Cache, `--force` and `--keep-grib` behavior.
- Missing archive file behavior.
- Duplicate-grain rejection.
- dbt contracts, uniqueness, relationships and physical value ranges.
- A one-delivery-day smoke fixture producing exactly `station count × 24` curated rows.
- The selected forecast reference time is earlier than the 09:30 JST D−1 cutoff.

Include a very small local GRIB2 fixture or create one deterministically using ecCodes samples. Tests must not download a production MSM file.

Verification should include:

```text
just test
just lint
just mypy
just dbt build
```

Also run a one-day end-to-end extraction and inspect representative stations from several JEPX areas.

## Acceptance criteria

The task is complete when:

1. A configurable date range can be downloaded and resumed safely.
2. Only the three required surface files per delivery day are fetched.
3. Full GRIB2 files do not have to be retained.
4. Every configured staffed JEPX-area station receives 24 hourly forecasts per complete delivery day.
5. The fact preserves forecast reference time and valid time separately.
6. Forecast values and interval semantics are documented and unit-tested.
7. The warehouse can compare forecast temperature against `fct_jma_weather_hourly` at the same station and hour.
8. The default snapshot could genuinely have been known at the model’s 09:30 JST D−1 issue time.
9. No population weighting, area aggregation, or model-strategy change is included.

## Key caveats

- RISH is a public academic archive, not a guaranteed operational API; historical gaps or delayed/missing products must be surfaced.
- RISH directory modification times are not authoritative JMA issue timestamps.
- JMBSC’s approximately 2½-hour dissemination delay is operational guidance, not an exact per-file publication record. The chosen 12 UTC D−2 run has enough margin that this uncertainty does not threaten the 09:30 cutoff.
- Station coordinates are observation-site coordinates; the extracted value represents the nearest model grid point, not a station-specific forecast.
- Forecast and observed temperature can differ partly because MSM grid elevation and terrain representation differ from the station.
- Download volume is determined by the full spatial grid, not by the number of selected stations.
