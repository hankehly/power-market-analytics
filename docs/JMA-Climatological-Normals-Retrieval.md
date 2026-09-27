# JMA Climatological Normals (平年値) Data Retrieval

How JMA publishes the climatological normals of its staffed stations, what the
daily file looks like, and how `power_market_analytics.ingestion.jma.normals`
brings it into the warehouse. Verified against the version 5 archive on
2026-09-27: every one of the 157 daily files was checked for the layout,
the flags and the padding described below.

## 1. Overview

- **Publisher**: 気象庁, the 平年値ダウンロード page
  (<https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html>). The
  English tables page (`…/stats/data/en/normal/normal.html`, eleven cities,
  monthly) has no Japanese twin; the download page is the Japanese source.
- **What a normal is**: the mean (or the total) of an element over the 30
  years 1991–2020, per station and calendar day, computed by JMA under the
  WMO rules and renewed every ten years. The 2020 normals came into use on
  2021-05-19 and replaced the 1981–2010 ones.
- **Per station**: every value belongs to one observatory. Tokyo is 東京
  (47662, 北の丸公園); station moves inside the period are corrected for
  (均質化, the correction values are published next to the data).
- **Scope here**: the daily file (日別平年値) of the 地上気象観測 (staffed)
  stations, every element and every station. Not loaded: the monthly, 3-month,
  10-day, 5-day and running-window files of the same archive, the AMeDAS
  normals, the regional averages and the seasonal phenomena.
- **Vintages**: one period today, `NormalsVintage` in
  `power_market_analytics/ingestion/jma/normals.py` (`VINTAGES`):

  | Period | In use since | Archive | Daily files |
  |---|---|---|---|
  | 1991–2020 | 2021-05-19 | `…/mdrr/normal/2020/data/normal_surface.zip` | 157 |

  The 2030 normals (about 2031) are a second entry; both periods then load
  side by side, every model keyed by `normals_period_end_year`.

## 2. The page and the archive

The page's heading `2020年平年値（第5版)` (a full-width opening bracket and an
ASCII closing one, as JMA writes it) names the period and the version; the
downloader reads the version there and nothing else — the archive URL is
config. Below the heading: the explanation PDF (`2020/doc/kaisetsu.pdf`), the
element list (`koumoku_sa.pdf`), the record layouts (`format_surface.pdf`) and
the archives, one per observation network.

`normal_surface.zip` (21,615,988 bytes, 1,963 files) holds, under
`normal_surface/`:

| Directory | Files | Content |
|---|---|---|
| `daily/` | 157 | `nml_sfc_d_<station>.csv` — the daily normals, loaded |
| `daily_5day/`, `daily_7day/`, `daily_14day/`, `daily_28day/` | 157 each | the normals of 5/7/14/28-day windows ending on each day |
| `5day/`, `month_basis_5day/`, `month_basis_10day/` | 157 each | 半旬 / 旬 normals |
| `monthly/`, `3month/` | 157 each | monthly and annual, 3-month |
| `seasonal_phenomena/` | 157 | first frost, first snow … |
| `regional_average/<grain>/` | 36 each | the regional averages of the class thresholds |
| `surface_station_index.csv` | 1 | the station list (CP932): number, names, latitude and longitude in degrees and minutes, altitude |

The archive is served by plain GET, with no cache headers to rely on, and is
replaced under the same URL when a new version comes out; nothing inside
names the version. The downloader therefore re-downloads it on every run.

## 3. The daily file

### 3.1 Record layout

`nml_sfc_d_47662.csv` has 972 lines, one per element and month, sorted by
element then month; every line is 308 bytes, LF-terminated, ASCII:

```text
15,47662,0500,30,1991,2020, 1,    60,8,    59,8,    58,8, …,    54,8
15,47662,0500,30,1991,2020, 2,    54,8,    54,8, …,    74,8,     0,0,     0,0
```

| Field | Width | Content |
|---|---|---|
| 1 | 2 | 平年値種別, always `15` for the daily file |
| 2 | 5 | station number |
| 3 | 4 | element code (§3.2) |
| 4 | 2 | 資料年数, the years of data behind the statistic (0 = none) |
| 5, 6 | 4, 4 | the first and last year the statistic covers — **per row**: 1991–2020 on most, 1991–2016 for an observation that ended in 2016, 2007–2020 for one that started late, `0` and `0` with 資料年数 0 |
| 7 | 2 | month 1–12 |
| 8 … 69 | 6 + 1, 31 times | (value, flag) of day 1 … day 31, the value right-aligned in six characters |

A day the month does not have is `0,0` — 76,302 such cells in the archive,
every one of them zero. February has 29 values.

### 3.2 Elements (81 codes)

The published integer is scaled: divide by the denominator for the value in
the unit. The seed `dbt/seeds/jma_normal_elements.csv` holds this table.

| Codes | Element | Statistic | Unit | Denominator |
|---|---|---|---|---|
| 0500, 0510, 0521–0526 | 日平均気温 `mean_temperature` | normal, std, class 1–6 | °C | 10 |
| 0600, 0610, 0621–0626 | 日最高気温 `max_temperature` | the same | °C | 10 |
| 0700, 0710, 0721–0726 | 日最低気温 `min_temperature` | the same | °C | 10 |
| 3000 | 雲量 日平均 `cloud_cover` | normal | tenths of the sky | 10 |
| 3500 | 日照時間 日合計 `sunshine_duration` | normal | h | 10 |
| 3600 | 日照率≧40% 日数（出現率） `sunshine_rate_ge_40pct` | normal | % | 1 |
| 3800 | 全天日射量 日合計 `solar_radiation` | normal | MJ/m² | 10 |
| 4000 | 降水量 日合計 `precipitation` | normal | mm | 10 |
| 4600 | 日降水量≧1.0mm 日数（出現率） `precipitation_ge_1mm` | normal | % | 1 |
| 4700 | 日降水量≧10.0mm 日数（出現率） `precipitation_ge_10mm` | normal | % | 1 |
| 6000 | 降雪の深さ 日合計 `snowfall` | normal | cm | 1 |
| 6200 | 積雪の深さ 日最大 `max_snow_depth` | normal | cm | 1 |
| 7100 + 100 (h − 1), + 10 for the std, h = 1 … 24 | h時の気温 `hourly_temperature` | normal, std | °C | 10 |

The three occurrence rates are published in `0.01日`; over one day that
integer is the percentage of the period's years in which the day qualified
(Tokyo, Jan 1: 78 % had a sunshine rate ≥ 40 %, 10 % had ≥ 1 mm of rain).
The class thresholds are JMA's three-class boundaries: 1 the smallest of the
"low" class, 2 at or below "much lower than normal" (かなり低い), 3 at or below
"lower" (低い), 4 above "higher" (高い), 5 above "much higher" (かなり高い), 6
the largest of the "high" class. The hourly temperatures (時別気温) are new in
the 2020 normals.

### 3.3 Flags

| Flag | Meaning |
|---|---|
| 8 | 正常値 — normal |
| 6 | 正常値（現象なし） — normal, no phenomenon: a true zero (snowfall at 那覇) |
| 7 | ［参考］正常値 — reference only |
| 5 | ［参考］正常値（現象なし） — reference only, no phenomenon |
| 0 | 統計値なし — no statistic; the value cell holds 0 |

［参考］ means the observation has ended or the series was cut
(統計を切断); JMA says such a value cannot be used for a departure from
normal (平年差・平年比). The flag is per element: at most stations one element's
366 days are reference only (an observation that ended — cloud cover after the
station was automated); 阿蘇山 47821, closed 2017-12-11, is reference only
throughout. Counts over the archive: 8 — 4,438,021; 0 — 120,368 (76,302 of
them padding); 7 — 86,964; 6 — 54,466; 5 — 30,905.

A standard-deviation or class-threshold row carries its base element's flag,
資料年数 and statistic years on every cell of every file (0 mismatches over
4.7 M cells), which is why the facts carry one flag per measure; the singular
test `assert_std_jma__normal_daily_derived_statistics_share_base_flags` keeps
it so.

## 4. Stations

157 stations. 147 of the seed `jma_stations`'s 149 have a file — 伊吹山
(s47751) and 剣山 (s47894), both closed 2001-03-31, do not. Ten stations of
the archive are not in the seed and drop out of the facts at the
`dim_jma_station` join: the eight Okinawa stations (47912 与那国島, 47917
西表島, 47918 石垣島, 47927, 47929, 47936 那覇, 47940, 47945 南大東島), 南鳥島
47991 and 昭和 89532. The raw and standardized models keep all 157.

## 5. Versions and `available_at`

| Version | Published | In use since | What changed |
|---|---|---|---|
| 1 | 2021-03-24 | 2021-05-19 | the 2020 normals |
| 2 | 2022-04-04 | 2022-05-18 | annual update |
| 2.1 | 2023-02-03 | 2023-03-02 | 高知: wind and sunshine instruments moved |
| 3 | 2023-03-16 | 2023-05-17 | annual update |
| 3.0.1 | 2023-05-18 | — | files updated by mistake in v3 restored |
| 3.1 | 2023-11-07 | 2023-12-06 | 銚子: anemometer moved |
| 4 | 2024-02-09 | 2024-03-26 | annual update |
| 4.0.1 | 2024-07-11 | — | errors in v4 corrected |
| 4.1 | 2024-10-29 | 2024-11-27 | 名瀬: instruments moved |
| 5 | 2025-03-25 | 2025-05-21 | 延岡 47822 (the only file in `normal_surface_ver5.zip`) |

Only the current full archive is served; a "changed files" zip exists for some
versions, and no full older version. So a reload after a new version silently
replaces those stations' rows, and the manifest records which version the
warehouse holds.

`available_at` of every row is the period's first in-use date, 2021-05-19
00:00 — a documented bound. The version's own in-use date (2025-05-21) would
hide the normals from every backtest day before it, the whole pinned
evaluation window, for a difference that is nil at all but a handful of
stations.

## 6. Downloading and loading

```bash
# The page (version), the zip (20 MB), the 157 daily files. Host-side:
uv run python scripts/download_jma_normals.py
# or in the devcontainer:
just python scripts/download_jma_normals.py            # every configured period
just python scripts/download_jma_normals.py --years 2020 --data-dir data/jma/normals

# Devcontainer: 152,604 rows into pma_raw.jma_normal_surface_daily, seconds.
just python scripts/load_jma_normals.py
just dbt build --select jma_normal_elements stg_jma__normal_surface_daily+
```

Files, written atomically (`.part`, then replace):

```text
data/jma/normals/2020/zip/normal_surface.zip           the archive, replaced on every run
data/jma/normals/2020/csv/daily/nml_sfc_d_47662.csv     157 daily files, byte for byte
data/jma/normals/2020/csv/surface_station_index.csv     for reference; not loaded
data/jma/normals/2020/manifest.json                     period_start_year, period_end_year,
                                                         version, in_use_since, downloaded_at_utc,
                                                         zip_url, zip_sha256, zip_bytes, daily_file_count
```

The downloader validates the archive before writing anything: a zip, the
station index present, exactly `expected_station_count` daily members. It
removes the manifest first and writes it last, so a run that fails midway
leaves no manifest, and the loader refuses a directory without one. A page
without a heading for the configured period fails the run naming the periods
it carries — JMA has moved on, and a `VINTAGES` entry for the newer normals is
the fix.

The loader (`JmaNormalsCsvLoader`, contract
`conf/schemas/jma_normal_surface_daily.yaml`) reads each period's files in one
positional scan (`_c0` … `_c68`), injects the manifest's period, version and
in-use date, and checks every row in one grouped Spark pass, naming the first
offending file: the first field is 15, the station number is the file's, the
element code is four digits, the month is 1–12, every value cell is an
integer, every flag is 0/5/6/7/8, every element has its 12 months, and every
file holds the same elements.

## 7. Warehouse models

| Model | Grain | Content |
|---|---|---|
| `pma_raw.jma_normal_surface_daily` | period × station × element × month | the file's rows, 31 value/flag pairs as 62 columns |
| `stg_jma__normal_surface_daily` | the same | as-is |
| `std_jma__normal_daily` | period × station × element × month × day | one row per day, the value scaled by the seed, null with flag 0, padding days dropped, Feb 29 kept, `is_reference_only`, the statistic years null when 0, `available_at` |
| `fct_jma_normal_daily` | period × station × month × day | the daily elements wide (the normal, the std of the three temperatures, a flag per element), 147 stations |
| `fct_jma_normal_hourly` | … × hour ending | the temperature at each hour with its std and flag |

The seed `jma_normal_elements` names the 81 codes; the class thresholds and
資料年数 stay in `std_jma__normal_daily`.

## 8. Reading a normal from a dated row

A normal has no date. Join on `station_id`, the month and the day of the
row's date, and for the hourly fact the hour ending 1–24 — hour 24 belongs to
the day it ends, as `fct_jma_weather_hourly` places it (`date_key` is the
hour-start date, and `hour(observed_hour_start_at) + 1` is the hour ending).
`dim_date` carries `month` and `day_of_month`; `month()` and `day()` on a
timestamp give the same.

```sql
select o.station_id, o.observed_at, o.temperature_c, n.temperature_c as normal_temperature_c
from pma_curated.fct_jma_weather_hourly o
join pma_curated.fct_jma_normal_hourly n
  on n.station_id = o.station_id
  and n.month = month(o.date_key)
  and n.day_of_month = day(o.date_key)
  and n.hour_ending = hour(o.observed_hour_start_at) + 1
where o.station_id = 's47662' and o.date_key = date '2025-08-01'
```
