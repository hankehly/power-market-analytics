# Kansai エリア需給・発電（実績） (Area Demand & Generation) Data Retrieval

How 関西電力送配電 (Kansai Transmission and Distribution) publishes the
Kansai-area 30-minute demand / generation actuals, what the files look like,
and how `power_market_analytics.kansai` brings them into the
warehouse. This is the Kansai counterpart of TEPCO's feed
([TEPCO doc](TEPCO-Area-Demand-Generation-Retrieval.md)); the two share the
downloader/loader code (`power_market_analytics/area_actuals.py`) and land in
the same curated fact.

Verified against a full capture on 2026-08-17 (every daily file 2022-04-01 →
2026-08-17).

## 1. Overview

- **Publisher**: 関西電力送配電, under the インバランス料金 information-disclosure
  rules (電力・ガス取引監視等委員会「２０２２年度以降のインバランス料金制度について
  （中間とりまとめ）」§3(2) 系統の需給に関する情報). The files are labelled with
  the disclosure item IDs: 実績値 = **A-1** エリア総需要量, **B-1** エリア総発電量,
  **B-4** エリア風力・太陽光発電量 (予測値 = A-2/B-2/B-5, BG計画値の総計 =
  A-3/B-3/B-6).
- **Page**: <https://www.kansai-td.co.jp/denkiyoho/imbalance/> (インバランス料金
  関連に関する情報公表 → エリア需給・発電（実績）); past months on `past.html`,
  whose list is served by
  `https://www.kansai-td.co.jp/interchange/denkiyoho/imbalance/past_imbalance_data.json`.
- **Content**: per 30-minute period, エリア総需要量, エリア総発電量 and
  エリア風力・太陽光(発電量), all in **30分kWh** — energy over the period. Three
  series per day in separate zips (実績 / 予測 / BG計画); **only 実績 is loaded.**
- **Coverage**: real files from 2022-03-16 (the 2022-01 and 2022-02 archives
  are 4-byte test stubs); loaded from **2022-04-01**, the first full month and
  the start of the imbalance regime, matching TEPCO. Complete through the last
  finalized day; the current month's zip also carries the running day (see §4).

## 2. Files and URLs

| What | URL | Notes |
|---|---|---|
| Monthly archive (history) | `https://www.kansai-td.co.jp/interchange/denkiyoho/imbalance/YYYYMM_jisseki.zip` | 2022-01 → current month; ~35 KB each. Members are flat. Sibling `YYYYMM_yosoku.zip` / `YYYYMM_keikaku.zip` hold 予測 / BG計画 (not used). |
| Today's live files | `…/imbalance/jukyu_jisseki_YYYYMMDD_06.csv` (+ `_yosoku_`, `_keikaku_`), listed in `…/imbalance/imbalance_csv_list.csv` | Refreshed every 30 min. Not used. |

Daily member names changed with the December 2025 archive:
`YYYYMMDD_jisseki.csv` through 2025-11, `jukyu_jisseki_YYYYMMDD_06.csv` from
2025-12 (`06` = the Kansai エリアコード). Plain `GET`, no session or token.

## 3. Daily CSV format

Two layouts, both CP932 with CRLF, both 48 data rows of 7 fields.

**Layout A — 2022-03-16 … 2025-12-24** (title line + two metadata lines +
header):

```
実績値（Ａ－１・Ｂ－１・Ｂ－４）
ファイル更新日,ファイル更新時間,対象年月日
20250702,00:13:11,20250701
日付,時間コマ,時間帯＿自,時間帯＿至,エリア総需要量,エリア総発電量,エリア風力・太陽光
20250701,1,00:00,00:30,7649810,8018589,2717
…
20250701,48,23:30,24:00,8385730,8460832,2176
```

**Layout B — 2025-12-25 onward** (two metadata lines + header; TEPCO's
shape):

```
ファイル更新日,ファイル更新時間,対象年月日
2025/12/26,00:13:12,2025/12/25
DATE,時間コマ,時間帯_自,時間帯_至,エリア総需要量(kWh),エリア総発電量(kWh),エリア風力・太陽光発電量(kWh)
2025/12/25,1,00:00,00:30,6786128,7066384,13060
```

| # | Column | Meaning | Notes |
|---|---|---|---|
| 1 | 日付 / DATE | Delivery date | `yyyymmdd` (A) or `yyyy/mm/dd` (B); the loader normalises B to `yyyymmdd` |
| 2 | 時間コマ | 30-minute period | 1 = 0:00–0:30 … 48 = 23:30–24:00 (= JEPX time code) |
| 3 | 時間帯＿自 / 時間帯_自 | Period start | `HH:MM` (40 early rows `H:MM`) |
| 4 | 時間帯＿至 / 時間帯_至 | Period end | `HH:MM`; the last period ends `24:00` (TEPCO writes `0:00`) |
| 5 | エリア総需要量 | Area total demand | kWh over the period, full precision (TEPCO rounds to 1,000 kWh) |
| 6 | エリア総発電量 | Area total generation | kWh over the period |
| 7 | エリア風力・太陽光(発電量) | Wind + solar generation | kWh over the period |

Metadata line: `<update date>,<HH:MM:SS>,<target date>` in the same date
style as the rows; one 2022-03 file has an unpadded hour and trailing commas
(`20220328,0:13:11,20220327,,,,`), which the loader tolerates.

**Grain**: one row per (日付, 時間コマ).

## 4. Data quirks (verified)

1. **Running day in the archive** — unlike TEPCO, the current month's zip
   includes today's file, refreshed intraday (e.g. `2026/08/18,06:43:12` for
   target 2026/08/18) with **blank cells** for periods not yet observed. A
   file is final only when its ファイル更新日 is later than its 対象年月日, so
   the loader (`archive_includes_current_day=True` on the `KANSAI` spec)
   skips files that fail that test; nothing else in the pipeline needs to
   know about the running day.
2. **Blank cells in a finalized day** — 2025-10-12 has 22 periods (time codes
   10–31) with empty measures; that date is on Kansai's 広域需給調整
   中断・分断 list. Loaded as null (the contract's measures are nullable) and
   kept so the grain stays dense.
3. **Layout change 2025-12-25** — see §3; the December 2025 zip mixes both
   layouts (01–24 A, 25–31 B) under the new member names.
4. **Revisions** — files are normally created at ~00:13 on target date + 1;
   all of April 2022 was re-issued on 2023-09-11 12:31. Past months are not
   immutable, so every zip is re-fetched on every run.
5. **No sentinel zeros, no scientific notation** — hence `bigint` measures
   straight from raw and no zero-nulling in `std` (contrast TEPCO).

## 5. Publication timing

実績 files are written ~00:13 the morning after the target day and appear in
that month's zip immediately; the live intraday copy updates every 30 min
(コマ終了後速やかに公表, 遅くとも30分後まで, per the disclosure rule).

## 6. Downloading and loading with `power_market_analytics.kansai`

`power_market_analytics/kansai.py` supplies the `KANSAI` `AreaActualsSource`
(URL template, 2022-04, both member-name generations, both accepted headers,
`archive_includes_current_day`), a thin `KansaiAreaDownloader`, and
`KansaiAreaCsvLoader`, which binds the shared positional loader to the spec.

```python
from power_market_analytics.kansai import KansaiAreaDownloader

downloader = KansaiAreaDownloader()          # data/kansai/area_demand_generation
downloader.download(2025, 7)                  # one month -> 31 csv/ files
downloader.download_all()                     # 2022-04 .. current month
# zips  -> data/kansai/area_demand_generation/zip/YYYYMM_jisseki.zip
# csvs  -> data/kansai/area_demand_generation/csv/<daily member name>
```

The loader reads positionally (`_c0`..`_c6`, contract
`conf/schemas/kansai_area_demand_generation_actual.yaml`), checks each file's
header against the two accepted layouts, skips not-yet-final files, keeps
the 48 data rows, normalises dates, injects `file_updated_at`, and
full-reloads `pma_raw.kansai_area_demand_generation_actual`. End to end:

```bash
just refresh-kansai
# = just python scripts/download_kansai_area_demand_generation.py
#   just python scripts/load_kansai_area_demand_generation.py
#   just dbt build
```

The loader reads all ~1,600 daily files in a **single Spark scan** (`CsvLoader._scan_positional`),
sniffing each file's `ファイル更新日` line in Python and joining the stamp back on the file name —
a full reload takes about 8 s (before 2026-08-30 the per-file union spent ~3 min planning
and ~40 s per Spark action).

Warehouse path: `pma_raw.kansai_area_demand_generation_actual` →
`stg_kansai__area_demand_generation_actual` →
`std_kansai__area_demand_generation_actual` (typed time axis) →
`fct_area_demand_generation_actual` (date_key × time_code × area_key,
`area_code = 'kansai'` alongside TEPCO's `'tokyo'`).

Unit tests: `tests/test_area_actuals.py` (shared classes, both Kansai
layouts, running-day skipping), `tests/test_kansai.py` (the spec),
`tests/test_kansai_scripts.py` (CLI + contract) — `just test`.

## 7. Extending

- **予測 / BG計画**: separate archives (`YYYYMM_yosoku.zip`,
  `YYYYMM_keikaku.zip`; members `*_yosoku.csv` / `*_bgkeikaku.csv`, titles
  A-2/B-2/B-5 and A-3/B-3/B-6) with the same column layout — a second
  `AreaActualsSource` per series plus its own contract and raw table.
- **Other TSOs**: Chubu (`YYYYMM_keito.zip`, nested CP932 folder names,
  `yyyy/mm/dd` dates), Tohoku, etc. publish the same A-1/B-1/B-4 files;
  each is a new spec + contract + `std_<tso>__…` model and one more `union
  all` branch in `fct_area_demand_generation_actual`.
