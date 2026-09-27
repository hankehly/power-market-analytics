# JMA climatological normals (平年値 1991–2020) — design

Date: 2026-09-27. Status: **approved 2026-09-27** (in chat, sections 1–5; the researcher
chose the purpose, the daily-file scope and approach A). Branch
`feature/jma-climatological-normals`.

## 1. Goal

Load JMA's per-station climatological normals (平年値) into the warehouse, so a demand
feature can read a day's or an hour's departure from normal, and a dashboard can show an
actual next to its normal. This first ingestion is the daily file of the staffed stations:
every element, every station, the 1991–2020 period.

The source was profiled on 2026-09-27 from the current archive (version 5). The findings in
§3 are measured, not assumed.

## 2. Decisions

1. **Scope: the daily file only.** It holds mean, max and min temperature, the temperature
   at each hour 01–24, cloud cover, sunshine, radiation, precipitation, snowfall and snow
   depth per calendar day — every element our hourly facts hold, at a finer grain than the
   monthly file. The monthly file (humidity, wind, pressure, threshold-day counts) and the
   other grains come later, from the same zip, one contract and model chain each.
2. **Approach A: a long standardized model and two wide curated facts.** One long fact
   would make every reader pivot, with the hour of a temperature living in a dimension
   row. A pivot in the loader would stop raw being as published. Neither was taken.
3. **One module per dataset:** `power_market_analytics/ingestion/jma/normals.py` holds the
   vintage config, the downloader and the loader, as `tso/area_actuals.py` does. Scripts
   `scripts/download_jma_normals.py` and `scripts/load_jma_normals.py`.
4. **A vintage config, one entry today.** `NormalsVintage(period_start_year=1991,
   period_end_year=2020, in_use_since=2021-05-19, zip_url=…/2020/data/normal_surface.zip,
   expected_station_count=157)` in `VINTAGES`, like `estat.vintages`. The 2030 normals are
   a second entry in about 2031, and both periods then load side by side: every grain keeps
   `normals_period_end_year`.
5. **The version comes from the download page.** The heading `2020年平年値（第5版)` gives
   the period end year and the version. The page must carry a heading for the configured
   period; its version goes into the manifest. A page with no heading for it fails the run,
   naming the years it does carry — the signal that JMA has moved on. Both bracket forms
   are accepted. Nothing else on the page is parsed; the zip URL is config.
6. **The zip is always re-downloaded.** A new version replaces files under the same URL
   with no version inside the archive, so a cache would go stale without a sign; the zip is
   20 MB, one request. No cache, no `--force`. The response is validated before anything is
   written (§4.1).
7. **Raw is the file, positionally.** One row per file line, the 31 value/flag pairs as
   62 integer columns, plus the manifest's period, version and in-use date and the file
   name. Padding cells are loaded as the zeros they are and dropped in `std`.
8. **The statistic years are per row, not per period.** The two year columns hold the years
   each statistic covers (1991–2016 for an element whose observation ended in 2016, 0 and 0
   with `n_years` 0 for no statistic). The period is a vintage attribute with its own
   columns.
9. **`std` unpivots to one row per station, element, month and day.** A fixed leap-year
   calendar keeps Feb 29 (the normals have one) and drops the padding days. The published
   integer divided by the element's power-of-ten denominator is the value; a flag of 0 makes
   it null.
10. **An element seed, `jma_normal_elements`, is the one place the 81 codes are named**:
    the element, the statistic, the hour, the unit and the scale denominator. `std` joins
    it; an unknown code fails the build.
11. **`available_at` is the period's first in-use date, 2021-05-19 00:00.** Only the
    current version is served, each version changed a few stations' files, and the
    differences are corrections and instrument moves — so a row holds version 5's value
    under the date the 2020 normals came into use. The version's own in-use date
    (2025-05-21) would hide the normals from every backtest day before it, the whole
    pinned window, for a difference that is nil at all but a handful of stations.
12. **Two facts:** `fct_jma_normal_daily` (station × month × day, ~35 columns) and
    `fct_jma_normal_hourly` (× hour ending 1–24, the temperature). Both inner-join
    `dim_jma_station`, so the ten stations outside a JEPX area drop out. The six class
    thresholds per temperature element and `n_years` stay in `std`, queryable.
13. **The calendar-day key is (`month`, `day_of_month`); the facts reference no
    `dim_date`.** A normal has no date. A dated row finds its normal through the month and
    day of its date — `dim_date` carries both, and `month()` / `day()` on the timestamp give
    the same. No `calendar_day_key` was added to `dim_date` for one join.
14. **One quality flag per measure.** A standard-deviation or class-threshold element
    carries its base element's flag, year count and statistic years on every cell of every
    file (0 mismatches over 4.7 M cells). A singular test guards it.
15. **The three occurrence rates are percentages as published.** JMA's unit `0.01日（%）`
    means hundredths of a day; over one day that integer is the percentage (Tokyo Jan 1:
    78 % of years had a sunshine rate ≥ 40 %). Their denominator is 1, and the columns end
    in `_pct`.
16. **Names without a precedent**, accepted by the researcher: `prob_sunshine_rate_ge_40pct_pct`,
    `prob_precipitation_ge_1mm_pct`, `prob_precipitation_ge_10mm_pct`, `cloud_cover_tenths`.
17. **Loader checks are the file's invariants**, not its size: a test file can be short.
    The 81-element set is asserted in dbt, where a trimmed fixture is no obstacle.
18. **Out of scope**: the feature columns (a feature candidate issue and its own PR under
    the build-out rule), dashboard changes, the monthly file and other grains, AMeDAS
    normals, regional averages, seasonal phenomena.

## 3. The source, as measured

| Item | Finding |
|---|---|
| Page | `https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html` (平年値ダウンロード); heading `2020年平年値（第5版)`; the 2020 normals in use since 2021-05-19; the English tables page (`…/stats/data/en/normal/normal.html`) has no Japanese twin |
| Archive | `…/mdrr/normal/2020/data/normal_surface.zip`, 21,615,988 bytes, 1,963 files, plain GET; also `normal_surface_ver5.zip` with the files version 5 changed (延岡 47822 only) |
| Layout | `normal_surface/<grain>/nml_sfc_<code>_<station>.csv`; per-station grains `daily` (`d`), `daily_5day`, `daily_7day`, `daily_14day`, `daily_28day`, `5day`, `month_basis_5day`, `month_basis_10day`, `monthly` (`m`), `3month`, `seasonal_phenomena`, 157 files each; `regional_average/<grain>/`, 36 files each; `surface_station_index.csv` |
| Daily file | 972 rows = 81 elements × 12 months, sorted by element then month; every line 308 bytes, LF; ASCII (the station index is CP932) |
| Daily row | `15, station, element, n_years, start_year, end_year, month,` then 31 × `value, flag`, values right-aligned in 6 characters; 69 fields |
| Padding | A day the month does not have is `0,0`, on every file (checked); Feb has 29 values |
| Values | Scaled integers per the element's unit: 0.1 °C, 0.1 (cloud tenths), 0.1 h, 0.1 MJ/m², 0.1 mm, 1 cm; the three rates in 0.01 day = % |
| Flags | 8 normal; 6 normal, no phenomenon (a true zero); 7 and 5 the same two, reference only — the observation ended or the series was cut, JMA says not to use them for anomalies; 0 no statistic. Counts over all daily files: 8 = 4,438,021, 0 = 120,368 (76,302 of them padding), 7 = 86,964, 6 = 54,466, 5 = 30,905 |
| Reference-only | Per element: at most stations one element's 366 days (an observation that ended, cloud cover after automation); 阿蘇山 47821 entirely (closed 2017-12-11) |
| Years | `n_years` 0, 5–20, 22, 23, 26–30; the statistic years per row, 1991–2020 on most, e.g. 1991–2016, 2007–2020, 0–0 with `n_years` 0 |
| Derived statistics | A `std` or `class_1…6` row carries its base element's flag, `n_years` and years, every cell, every file |
| Stations | 157. 147 of the seed's 149 (伊吹山 s47751 and 剣山 s47894, closed 2001-03-31, have no file). Not in the seed: the eight Okinawa stations (47912, 47917, 47918, 47927, 47929, 47936, 47940, 47945), 南鳥島 47991, 昭和 89532 |
| Versions | v1 published 2021-03-24, in use 2021-05-19; v2 2022-04-04 / 2022-05-18; v2.1 2023-02-03 / 2023-03-02 (高知 instruments moved); v3 2023-03-16 / 2023-05-17; v3.0.1 2023-05-18 (files v3 updated by mistake restored); v3.1 2023-11-07 / 2023-12-06 (銚子 anemometer moved); v4 2024-02-09 / 2024-03-26; v4.0.1 2024-07-11 (v4 errors corrected); v4.1 2024-10-29 / 2024-11-27 (名瀬 instruments moved); v5 2025-03-25 / 2025-05-21 (延岡). Only the current full zip is served |
| Tokyo checks | 0500 Jan 1 = 6.0 °C, 0510 = 1.9 °C, 9400 (24:00) = 5.5 °C, 3000 = 3.7 tenths, 3500 = 6.3 h, 3800 = 9.0 MJ/m², 3600 = 78 %, 4600 = 10 %, 4700 = 4 %, 6000 = 0 cm flag 6; 札幌 6000 Jan 1 = 4 cm |
| Reference page | `https://www.data.jma.go.jp/stats/etrn/view/nml_sfc_d.php?prec_no=44&block_no=47662&year=&month=1&day=&view=` — JMA's own daily normals of a station and month, for the verification step |

The 81 daily element codes (format PDF `2020/doc/format_surface.pdf`, pages 14–16):

| Codes | `element` | `statistic` | Unit | Denominator |
|---|---|---|---|---|
| 0500, 0510, 0521–0526 | `mean_temperature` | `normal`, `std`, `class_1` … `class_6` | °C | 10 |
| 0600, 0610, 0621–0626 | `max_temperature` | the same | °C | 10 |
| 0700, 0710, 0721–0726 | `min_temperature` | the same | °C | 10 |
| 3000 | `cloud_cover` | `normal` | tenths | 10 |
| 3500 | `sunshine_duration` | `normal` | h | 10 |
| 3600 | `sunshine_rate_ge_40pct` | `normal` | % | 1 |
| 3800 | `solar_radiation` | `normal` | MJ/m2 | 10 |
| 4000 | `precipitation` | `normal` | mm | 10 |
| 4600 | `precipitation_ge_1mm` | `normal` | % | 1 |
| 4700 | `precipitation_ge_10mm` | `normal` | % | 1 |
| 6000 | `snowfall` | `normal` | cm | 1 |
| 6200 | `max_snow_depth` | `normal` | cm | 1 |
| 7100 + 100 (h − 1), and + 10 for the std, h = 1 … 24 | `hourly_temperature` | `normal`, `std`; `hour_ending` = h | °C | 10 |

Class thresholds: 1 the smallest of the "low" class, 2 at or below is "much lower than
normal", 3 at or below "lower", 4 above "higher", 5 above "much higher", 6 the largest of
the "high" class.

## 4. Code

### 4.1 Downloader — `JmaNormalsDownloader`

- `JmaNormalsDownloader(data_dir=Path("data/jma/normals"), timeout=60.0, session=None)`;
  `session` is injectable, the default a plain `requests.Session` (no TLS quirk, unlike
  RISH). Module constant `NORMALS_PAGE_URL`.
- `parse_versions(html) -> dict[int, str]`: every `<year>年平年値（第<version>版）`
  heading, either bracket form; `version` keeps its dots (`"4.0.1"`).
- `read_version(vintage) -> str`: fetch the page; return the version of
  `vintage.period_end_year`; raise `JmaNormalsDownloadError` naming the years found when the
  period is not on the page.
- `download_vintage(vintage) -> list[Path]`: the version, then the zip. Validation before
  any write: `zipfile.is_zipfile`; the member `normal_surface/surface_station_index.csv`
  is present; the members matching `normal_surface/daily/nml_sfc_d_(\d{5})\.csv` number
  exactly `expected_station_count`. A failure raises `JmaNormalsDownloadError` quoting the
  count or the missing member, with nothing on disk. Then, atomically (`.part`, then
  replace, the MSM pattern): the zip, the 157 daily files byte for byte, the station index,
  and last the manifest. Returns the daily files, sorted.
- `download_all(years=None) -> list[Path]`: every configured vintage, or the given period
  end years.
- Files:

  | Path | Content |
  |---|---|
  | `data/jma/normals/2020/zip/normal_surface.zip` | the archive, replaced on every run |
  | `data/jma/normals/2020/csv/daily/nml_sfc_d_<station>.csv` | 157 daily files, as archived |
  | `data/jma/normals/2020/csv/surface_station_index.csv` | for reference; not loaded, the seed has every station |
  | `data/jma/normals/2020/manifest.json` | `period_start_year`, `period_end_year`, `version`, `in_use_since`, `downloaded_at_utc`, `zip_url`, `zip_sha256`, `zip_bytes`, `daily_file_count` |

- Script `scripts/download_jma_normals.py`: `--data-dir` (default `data/jma/normals`),
  `--years` (period end years, default every configured vintage), `--timeout`. Logs the
  version and the file count.

### 4.2 Loader — `JmaNormalsCsvLoader(CsvLoader)`

- `_resolve_files`: a directory is the downloader root, `*/csv/daily/nml_sfc_d_*.csv`
  underneath; a file or a glob as the other loaders. A file's manifest is
  `file.parents[2] / "manifest.json"`; missing, or its `period_end_year` not the directory's
  year, fails the load naming the file.
- `_read_all`: in Python, each file name matches `nml_sfc_d_(\d{5})\.csv$`; the files of one
  manifest are read in one `_scan_positional(files, 69)`, the manifest's four values and
  `SOURCE_FILE_COL` are injected as `__normals_period_start_year`,
  `__normals_period_end_year`, `__normals_version`, `__in_use_since`, `__source_file`;
  `_check_rows` on the scan; `_project` through the contract; scans unioned.
- `_check_rows`, one grouped Spark pass per scan, the first offending file named with
  examples, as `EstatCensusMeshCsvLoader._check_rows` does: `_c0` is `15`; `_c1` equals the
  file's station number; `_c2` matches `^\d{4}$`; `_c6` is 1–12; every value cell matches
  `^-?\d+$`; every flag cell is one of `0 5 6 7 8`; every (file, element) has exactly 12
  rows with 12 distinct months; every file's element set is the whole set (each file's
  distinct element count equals the scan's).
- Contract `conf/schemas/jma_normal_surface_daily.yaml`: `read_options`
  `encoding: windows-31j`, `ignoreLeadingWhiteSpace: "true"`; grain
  `[normals_period_end_year, station_number, element_code, month]`; columns, all
  non-nullable:

  | Column | Source | Type |
  |---|---|---|
  | `normal_kind` | `_c0` | int (always 15) |
  | `station_number` | `_c1` | string (`47662`) |
  | `element_code` | `_c2` | string (`0500`; a string keeps the zero) |
  | `n_years` | `_c3` | int |
  | `statistic_start_year`, `statistic_end_year` | `_c4`, `_c5` | int (0 = none) |
  | `month` | `_c6` | int |
  | `value_d01`, `flag_d01`, … `value_d31`, `flag_d31` | `_c7` … `_c68` | int |
  | `normals_period_start_year`, `normals_period_end_year` | injected | int |
  | `normals_version` | injected | string (`5`) |
  | `in_use_since` | injected | date |
  | `source_file` | injected | string |

- Table `pma_raw.jma_normal_surface_daily`, full overwrite; 152,604 rows; seconds.
- Script `scripts/load_jma_normals.py`: `--schema`, `--data` (the downloader root, a file
  or a glob), `--table` (default `pma_raw.jma_normal_surface_daily`).

## 5. dbt

### 5.1 Source and seed

- `dbt/models/raw/jma.yml` gains `jma_normal_surface_daily`: descriptions, `not_null` on
  the grain columns, `accepted_values` 15 on `normal_kind`.
- Seed `dbt/seeds/jma_normal_elements.csv`, 81 rows: `element_code`, `element_name_ja`
  (the PDF's name, e.g. `日平均気温【標準偏差】`, `01時の気温`), `element`, `statistic`,
  `hour_ending` (1–24 for `hourly_temperature`, else empty), `unit`, `scale_denominator`
  (§3 table). `dbt_project.yml` types `element_code: string`, `hour_ending: int`,
  `scale_denominator: int`, as the other seeds are typed there. Seeds carry no tests here;
  the seed is guarded on `std` by two uniqueness tests: the grain, which a repeated
  `element_code` would break, and (`normals_period_end_year`, `station_id`, `element`,
  `statistic`, `hour_ending`, `month`, `day_of_month`), which two codes mapped to the same
  element would break — and which is what makes the facts' pivot exact.

### 5.2 `stg_jma__normal_surface_daily`

As-is, contract enforced, the same tests as the source.

### 5.3 `std_jma__normal_daily`

Grain `normals_period_end_year × station_id × element_code × month × day_of_month`;
4,654,422 rows today.

- A Jinja loop writes `stack(31, 1, value_d01, flag_d01, …, 31, value_d31, flag_d31) as
  (day_of_month, published_value, quality_flag)`; keep
  `day_of_month <= day(last_day(make_date(2000, month, 1)))` — 2000 is a leap year, so
  Feb 29 stays whatever the period's end year is.
- Columns: the period and version, `station_id` = `'s' || station_number`, `element_code`,
  the seed's `element`, `statistic`, `hour_ending`, `element_name_ja`, `unit`, `month`,
  `day_of_month`, `value` double = `published_value / scale_denominator` and null when
  `quality_flag = 0`, `quality_flag`, `is_reference_only` = flag in (5, 7), `n_years`,
  `statistic_start_year` and `statistic_end_year` null when 0, `available_at` =
  `in_use_since` at 00:00, naive JST.
- The YAML states the flags once (§3) and the `available_at` bound (decision 11).
- Tests: the grain unique; `not_null` on the keys, `quality_flag`, `n_years`;
  `accepted_values` on `quality_flag` (0, 5, 6, 7, 8) and `statistic`; `relationships`
  from `element_code` to the seed.
- Unit test, one Tokyo file's worth of staging rows: element 0500 February (29 values, two
  `0,0` cells) gives 29 rows, 6.0 on the 1st; a flag-0 cell gives a null value; a flag-6
  zero gives 0.0; element 3600's 78 gives 78.0; element 7100 lands on `hour_ending` 1 and
  9410 on 24 with `statistic` `std`; years 0 become null; `available_at` is
  2021-05-19 00:00.
- Singular tests: `assert_stg_jma__normal_surface_daily_padding_cells_are_zero` (in
  staging, the value and flag of every day a month does not have are 0, so the dropped rows
  carried nothing); `assert_std_jma__normal_daily_derived_statistics_share_base_flags`
  (every `std` and `class_*` row has its base element's `quality_flag`, `n_years`,
  `statistic_start_year` and `statistic_end_year` — decision 14);
  `assert_std_jma__normal_daily_every_station_has_every_element` (per period and station,
  the distinct element codes are the seed's 81).

### 5.4 `fct_jma_normal_daily`

Grain `normals_period_end_year × station_id × month × day_of_month`; 366 rows per station
and period; 53,802 rows today. A conditional-aggregate pivot of the `normal` and `std` rows
(one row per key and element, so `max(case …)` is exact), inner-joined to `dim_jma_station`
on `station_id`.

| Codes | Columns | Unit |
|---|---|---|
| 0500 / 0510 | `mean_temperature_c`, `mean_temperature_std_c` | °C |
| 0600 / 0610 | `max_temperature_c`, `max_temperature_std_c` | °C |
| 0700 / 0710 | `min_temperature_c`, `min_temperature_std_c` | °C |
| 3000 | `cloud_cover_tenths` | 0–10 |
| 3500 | `sunshine_duration_h` | h |
| 3600 | `prob_sunshine_rate_ge_40pct_pct` | % of years the day qualified |
| 3800 | `solar_radiation_mjm2` | MJ/m² |
| 4000 | `precipitation_mm` | mm |
| 4600 / 4700 | `prob_precipitation_ge_1mm_pct`, `prob_precipitation_ge_10mm_pct` | % |
| 6000 | `snowfall_cm` | cm |
| 6200 | `max_snow_depth_cm` | cm |

Plus one `<measure>_quality_flag` per row of the table (12: `mean_temperature_quality_flag`
… `max_snow_depth_quality_flag`), `normals_period_start_year`, `normals_version`,
`available_at`. Tests: the grain unique; `relationships` to `dim_jma_station`; the singular
test `assert_fct_jma_normal_daily_has_366_days_per_station` (every station and period has
366 rows, Feb 29 among them).

### 5.5 `fct_jma_normal_hourly`

Grain `… × hour_ending` (1–24); 24 rows per station and day; 1,291,248 rows today. Columns:
`temperature_c`, `temperature_std_c`, `temperature_quality_flag`, `n_years`,
`statistic_start_year`, `statistic_end_year`, the period columns, `normals_version`,
`available_at`. The same join and tests, with
`assert_fct_jma_normal_hourly_has_24_hours_per_day`.

### 5.6 Reading a normal from a dated row

Join on `station_id`, the month and the day of the row's date, and for the hourly fact the
hour ending 1–24 of the observation or forecast hour — hour 24 belongs to the day it ends,
as `std_jma__hourly.observed_date` already places it. `dim_date` carries `month` and
`day_of_month`; `month()` and `day()` on the timestamp give the same.

## 6. Tests (pytest, the 100 % gate)

- `tests/test_jma_normals.py`: the vintage config; `parse_versions` on both bracket forms,
  several headings, none; `read_version` on a page without the period; the downloader
  against a fake session — a zip built in memory with two daily files and the index; each
  rejection (not a zip, no index, a wrong daily count, a bad member name) leaves nothing on
  disk; the atomic write; the manifest's fields and sha256; the loader on files written to
  `tmp_path` in the exact 308-byte layout under `<year>/csv/daily/` with a manifest,
  through the real contract — a good load, and every check of §4.2 failing on a named file;
  a missing or mismatched manifest.
- `tests/test_jma_normals_scripts.py`: both scripts through `tests.support.import_script`
  with the downloader and loader classes swapped.

## 7. Verification before the PR

In the devcontainer: the real download (version 5, the manifest's sha256 and 21,615,988
bytes), the load (152,604 rows), `dbt build` of the new models, the row counts of §5
(4,654,422 / 53,802 / 1,291,248), Tokyo's values of §3 against JMA's daily-normals page,
and the hourly normals against the 2016–2025 mean of `fct_jma_weather_hourly` per month, day
and hour for Tokyo — expected a few tenths of a degree warmer than the normal on average; a
difference of degrees would mean a wrong hour or scale. Numbers go in the PR body's
Evidence block.

## 8. Docs, recipes, PR

- `docs/JMA-Climatological-Normals-Retrieval.md`: overview; page and URLs; the archive
  layout; the daily record (§3); the element table; stations; versions and the
  `available_at` bound; commands, data dir and manifest; the models and §5.6. Sidebar entry
  under Data sources: `JMA — 平年値 (climatological normals)`. `docs/README.md`: a row in
  the sources table (keys station, calendar day, hour; measures per §5.4; coverage the
  1991–2020 period, in use since 2021-05-19; table `pma_raw.jma_normal_surface_daily`) and
  a milestone at 2021-05-19 on the timeline, as the census vintages are drawn.
  `Curated-Star-Schema.md`: the two facts.
- `CLAUDE.md`: the single-source refresh list, an architecture bullet with the data flow, a
  gotcha (the statistic years are per row; only the current version is served).
- `justfile`: `refresh-all` runs `download_jma_normals.py` then `load_jma_normals.py`
  after the JMA hourly block.
- Commits `feat(jma): …`, labels `enhancement` + `ingestion`, the Codex loop.
