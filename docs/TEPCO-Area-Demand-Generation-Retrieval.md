# TEPCO エリア需要・発電情報 (Area Demand & Generation) Data Retrieval

How TEPCO Power Grid publishes the Tokyo-area 30-minute demand / generation
actuals, what the files look like, and how `power_market_analytics.tepco`
brings them into the warehouse.

Verified against a full capture on 2026-08-16 (every daily file 2022-04-01 →
2026-08-15).

## 1. Overview

- **Publisher**: 東京電力パワーグリッド (TEPCO Power Grid), under METI's
  「系統情報の公表の考え方」.
- **Page**: <https://www.tepco.co.jp/forecast/html/area-download-j.html>
  (エリア需要・発電情報のダウンロード). A different page,
  `area_data-j.html` (エリア需給実績, per-fuel generation), exists but is not
  used here.
- **Content**: per 30-minute period, エリア総需要量 (area total demand),
  エリア総発電量 (area total generation) and エリア風力・太陽光発電量 (wind +
  solar generation), all in **30分kWh** — energy over the period. Three
  series per day: 実績 (actuals), 予測 (TEPCO's forecast) and BG計画総計
  (balancing-group plan total). **Only 実績 is loaded.**
- **Coverage**: 2022-04-01 → yesterday, complete (no missing days).

## 2. Files and URLs

| What | URL | Notes |
|---|---|---|
| Monthly archive (history) | `https://www4.tepco.co.jp/forecast/html/images/AREA_YYYYMM.zip` | 2022-04 → current month; ~100 KB each, 53 zips ≈ 5 MB (2026-08). Current month's zip is regenerated daily and contains all finalized days through yesterday. |
| Today's actuals (live) | `…/images/AREA_JISEKI.csv` | Partial: periods not yet observed are 0. Not used. |
| Today's forecast / BG plan (live) | `…/images/AREA_YOSOKU.csv`, `…/images/AREA_BGKEI.csv` | Revised during the day. Not used. |
| Tomorrow's forecast / BG plan | `…/images/AREA_ONCE_YOSOKU.csv`, `…/images/AREA_ONCE_BGKEI.csv` | Published in the evening; header only before that. Not used. |

Each zip holds three files per day — `AREA_JISEKI_YYYYMMDD.csv`,
`AREA_YOSOKU_YYYYMMDD.csv`, `AREA_BGKEI_YYYYMMDD.csv` — as flat members,
**except `AREA_202403.zip`, whose members sit under an `AREA_202403/`
subfolder**. The downloader flattens on extraction. Plain `GET`, no session
or token; the response is `application/zip`.

## 3. `AREA_JISEKI_YYYYMMDD.csv` format

- **Encoding**: CP932 (Shift_JIS). **Line endings**: CRLF.
- **Layout** (identical in all 1,598 files captured):

```
ファイル更新日,ファイル更新時間,対象年月日                      ← line 1: metadata header
20250716,00:05:04,20250715                                   ← line 2: file update date/time, target date
日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光発電量   ← line 3: column header
20250715,1,0:00,0:30,15059000,12421000,141000                ← lines 4-51: 48 data rows
…
20250715,48,23:30,0:00,15531000,12507000,131000
```

| # | Column | Meaning | Notes |
|---|---|---|---|
| 1 | 日付 | Delivery date | `yyyymmdd`; equals the file-name date on every row |
| 2 | 時間コマ | 30-minute period | 1 = 0:00–0:30 … 48 = 23:30–24:00 (= JEPX time code) |
| 3 | 時間帯＿自 | Period start | `H:MM` (full-width underscore in the header) |
| 4 | 時間帯＿至 | Period end | `H:MM`; 24:00 is written `0:00` |
| 5 | エリア総需要量 | Area total demand | kWh over the period (30分kWh) |
| 6 | エリア総発電量 | Area total generation | kWh over the period |
| 7 | エリア風力・太陽光発電量 | Wind + solar generation | kWh over the period; ≤ column 6 on every row |

Exactly 48 rows per file, 7 fields per row, no blank cells, no negatives.
The 予測 / BG計画 files use a slightly different header
(`時間帯_自(HH:MI)`, unit suffixes `[30分kWh]`, times as `HHMM`) — another
reason the loader checks the header text of every file it reads.

**Grain**: one row per (日付, 時間コマ).

## 4. Data quirks (verified)

1. **Scientific notation** — 13 files, 2022-04-01 … 2022-04-13, contain 47
   cells like `1.66919e+07` (precision lost to ~10 kWh). Spark's ANSI
   `cast(string as bigint)` throws on these, so the load contract types the
   three measures as `double`; `std_tepco__area_demand_generation_actual`
   rounds back to `bigint`.
2. **"Not yet observed" zeros** — TEPCO writes 0 for future periods in the
   live file. The archived `AREA_JISEKI_20250614.csv` froze mid-day: time
   codes 11–48 are 0/0/0. `std` nulls all three measures on rows where all
   three are 0 (Tokyo demand is never 0); the rows are kept.
3. **Isolated zero** — 2023-09-17 time code 11 has wind+solar = 0 with normal
   demand/generation. Left as published.
4. **Revisions** — actuals files are normally created at ~00:05 on
   target date + 1 (`ファイル更新日/時間`), but a few were re-issued later
   (2022-12-01 and 12-02 on 2022-12-14 10:17; 2024-03-11 on 2024-04-19).
   Because past months are therefore not immutable and the whole history is
   ~5 MB, the downloader re-fetches every zip on every run.

## 5. Publication timing

- 実績: 「対象となる時間帯が終了後、すみやかに公表」 — the live file updates
  every 30 minutes; the day's file is finalized at ~00:05 the next morning
  and appears in that month's zip the same day.
- 予測: 「前日夕方に公表」 (next-day file in the evening), then revised
  intraday; the archived copy is the **last revision (~23:40 on the target
  day)**, not the day-ahead version — which is why 予測 / BG計画 are out of
  scope as day-ahead features.
- BG計画総計: published after the next-day and same-day plans are fixed;
  archived copy = last revision, same caveat.

## 6. Downloading and loading with `power_market_analytics.tepco`

The download/extract and the positional load are the shared
`AreaActualsDownloader` / `AreaActualsCsvLoader` in
`power_market_analytics/area_actuals.py`, driven by a per-TSO
`AreaActualsSource` spec; `power_market_analytics/tepco.py` supplies the
`TEPCO` spec (URL template, 2022-04, `AREA_JISEKI_*` member regex, the one
accepted column-header line) plus thin `TepcoAreaDownloader` and
`TepcoAreaCsvLoader` subclasses. The Kansai feed reuses the
same classes ([Kansai doc](Kansai-Area-Demand-Generation-Retrieval.md)).

```python
from power_market_analytics.tepco import TepcoAreaDownloader

downloader = TepcoAreaDownloader()          # data/tepco/area_demand_generation
downloader.download(2025, 7)                 # one month -> 31 csv/ files
downloader.download_all()                    # 2022-04 .. current month
# zips  -> data/tepco/area_demand_generation/zip/AREA_YYYYMM.zip
# csvs  -> data/tepco/area_demand_generation/csv/AREA_JISEKI_YYYYMMDD.csv
```

The loader reads the CSVs positionally (`_c0`..`_c6`, contract
`conf/schemas/tepco_area_demand_generation_actual.yaml`), verifies each
file's column-header line against the spec, filters to the 48 data rows,
injects `file_updated_at` from the metadata line, and full-reloads
`pma_raw.tepco_area_demand_generation_actual`. End to end:

```bash
just refresh-tepco
# = just python scripts/download_tepco_area_demand_generation.py
#   just python scripts/load_tepco_area_demand_generation.py
#   just dbt build
```

Warehouse path: `pma_raw.tepco_area_demand_generation_actual` →
`stg_tepco__area_demand_generation_actual` →
`std_tepco__area_demand_generation_actual` (typed time axis, bigint kWh,
sentinel rows nulled) → `fct_area_demand_generation_actual`
(date_key × time_code × area_key; Tokyo rows come from this feed, Kansai rows
from `std_kansai__area_demand_generation_actual`).

Unit tests: `tests/test_area_actuals.py` (shared classes) and
`tests/test_tepco.py` (the TEPCO spec) — `just test`.

## 7. Extending

- **予測 / BG計画**: add `AREA_YOSOKU_` / `AREA_BGKEI_` to the extraction
  regex (`TEPCO.member_re`), give each its own contract (their header text
  and `HHMM` time labels differ) and raw table
  (`tepco_area_demand_generation_forecast` / `_bg_plan`), and remember the
  archived copies are last-intraday revisions.
- **Live files**: `AREA_JISEKI.csv` (today, partial) could feed an intraday
  view; it uses the same layout as the archived actuals.
