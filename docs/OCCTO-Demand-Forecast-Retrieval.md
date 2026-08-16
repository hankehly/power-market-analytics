# OCCTO Demand Forecast (翌々日) Data Retrieval

This document describes how to obtain 電力需要予想・ピーク時供給力 (demand forecast /
peak supply capacity) data — in particular the 翌々日 (day-after-next) series — from
OCCTO's public portal, programmatically and without a browser: the portal's request
framework, the bulk-download protocol that returns the **entire history in a single CSV**,
the file format, the catalog of other datasets reachable through the same endpoint, and
how to use the downloader/loader in `power_market_analytics/occto.py` (`just refresh-occto`).

All protocol details were established empirically on 2026-08-16 by driving the portal in
a browser, capturing its network traffic, and replaying the requests with plain HTTP
clients (`curl`). OCCTO may change the system at any time; if downloads start failing,
re-verify against the live site.

## 1. Overview

- **Source**: OCCTO 広域機関システム public portal —
  `https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/LOGIN_login`. The 公表
  (public disclosure) section requires **no account**: a plain GET of `LOGIN_login`
  creates an anonymous session and lands on the menu.
- **Two retrieval paths** exist for the demand-forecast data:
  1. **Interactive screens** (`CC01S030C`…`CC01S035C`): per-tab search UIs (長期 / 年間 /
     月間 / 週間 / 翌々日 / 翌日・当日) with paged tables and a per-screen CSV button.
  2. **情報ダウンロード bulk download** (`CF01S010C`, menu section ダウンロード情報):
     one request returns the **full history of a whole dataset as one CSV** — for
     需要予想・ピーク時供給力(翌々日) that is every 対象日 since 2024/03/13, all areas,
     ~700 KB. It also supports a date-range filter for incremental pulls.
- **The bulk path is the recommended one.** Three HTTP requests, no HTML parsing, no
  pagination. The interactive screens are documented in [§6](#6-interactive-screens) only
  for completeness.
- **翌々日 dataset key facts**: available from **2024/03/13** (対象日); 12 rows per day
  (10 areas + 9エリア計 + 10エリア計); one row per (対象日, エリア); 策定日 is always
  対象日 − 2 (verified across all rows). The series exists because the 翌々日 publication
  was introduced with 広域予備率 Phase 3 in 2023–24; the older 翌日/当日 series go back
  to 2016/04/01 (see [§5](#5-dataset-catalog)).

## 2. The portal framework

The portal is a 2010s-era JSP framework ("gem2"/"sd") where everything is a POST to
`https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/<SCREEN_ID>` and hidden
`fwExtention.*` form fields select the behavior:

| Field | Meaning |
|---|---|
| `fwExtention.actionType` / `actionSubType` | The action, e.g. `reference`/`initDisplay` (load defaults), `reference`/`initTable` (search), `reference`/`ok` (issue download key), `reference`/`download` (fetch file) |
| `fwExtention.pathInfo` | Screen id — **must match the screen being posted to** (see pitfall below) |
| `fwExtention.formId` | The screen's form id (screen id with trailing `C` → `P`, e.g. `CF01S010P`) |
| `fwExtention.prgbrh` | Program branch, always `0` here |
| `requestToken` / `downloadKey` | Empty everywhere **except** the download handshake ([§3.1](#31-the-three-request-flow)) |

Request styles:

- **HTML navigation**: normal form POST; returns a full screen page. Opening a screen:
  `POST /SD/<SCR>?fwExtention.pathInfo=<SCR>&fwExtention.prgbrh=0` with an **empty
  body**.
- **AJAX**: same URL, plus header `sdReqType: AJAX`; returns a JSON envelope
  `{"root": {"actionResult": {...}, "bizRoot": {"header": {...}, "table": {...}},
  "errMessage": ..., "returnCode": ...}}`.

Empirically verified properties:

- **Session cookies are required** (`JSESSIONID` + `HSERVERID`, both scoped to
  `/public/dfw/RP11/OCCTO`, issued by the `LOGIN_login` GET). Without them every call
  returns a session-timeout error.
- **No User-Agent, Referer, or Origin checks** — none of them affect any response.
- **Pitfall**: posting a body that contains `fwExtention.pathInfo=<some other screen>`
  (e.g. a stale `MENU`) yields 利用権限がないため、利用出来ません ("no permission") even
  when the URL's query string is correct — the body value wins. Send an empty body when
  opening screens, or make the body's `pathInfo` match the target screen.
- Error pages (不正なリクエストです, 利用権限がない…) come back with **HTTP 200**, so
  status codes alone prove nothing; check content (see [§3.4](#34-failure-modes)).
- The menu tree (screen ids) can be enumerated with
  `POST /SD/MENU_show` (AJAX): the JSON lists every public screen's `prgId`.

## 3. Bulk download (情報ダウンロード, `CF01S010C`)

### 3.1 The three-request flow

The UI flow is CSV保存 → `reference/print` (validation) → confirm dialog →
`reference/ok` → `reference/download`. Headless, the `print` step can be **skipped**
(verified); opening the screen beforehand is also unnecessary. The minimal flow:

1. `GET /public/dfw/RP11/OCCTO/SD/LOGIN_login` — establishes the session cookies. The
   response is the menu page; its content is not needed.
2. `POST /public/dfw/RP11/OCCTO/SD/CF01S010C` with `sdReqType: AJAX` and
   `fwExtention.actionSubType=ok` plus the dataset selection ([§3.2](#32-request-parameters)).
   The JSON response carries the two values needed next:

   ```json
   {"root": {"bizRoot": {"header": {
     "downloadKey":  {"value": "20260816115100_CF01S010C"},
     "requestToken": {"value": "100e362f27a06177dc573f..."}}}}}
   ```

3. `POST /public/dfw/RP11/OCCTO/SD/CF01S010C` (no AJAX header) with
   `fwExtention.actionSubType=download`, the **same selection**, and the `downloadKey` +
   `requestToken` from step 2. The response is the CSV
   (`Content-Disposition: attachment;filename=<timestamp>_電力需要予想ピーク時供給力翌々日.csv`).

Both `downloadKey` and `requestToken` are required — a download with a blank or reused
token returns the 不正なリクエスト page. Each download therefore needs its own
`ok` → `download` pair, but one session can issue many pairs.

### 3.2 Request parameters

Body fields for both the `ok` and `download` POSTs
(`Content-Type: application/x-www-form-urlencoded`). This is the verified working set;
fields not listed (the UI posts many more) are unnecessary.

| Field | Value | Meaning |
|---|---|---|
| `fwExtention.actionType` | `reference` | |
| `fwExtention.actionSubType` | `ok`, then `download` | |
| `fwExtention.pathInfo` | `CF01S010C` | |
| `fwExtention.formId` | `CF01S010P` | |
| `fwExtention.prgbrh` | `0` | |
| `fwExtention.pagingTargetTable`, `fwExtention.jsonString`, `ajaxToken`, `requestTokenBk` | *(empty)* | |
| `transitionContextKey` | `DEFAULT` | |
| `requestToken` | empty for `ok`; the issued token for `download` | |
| `downloadKey` | empty for `ok`; the issued key for `download` | |
| `tabSntk` | `1` | Active tab: `0` = 連系線, `1` = エリア・広域ブロック情報. Validation follows the tab — with `0` you get 対象連系線区間が入力されていません |
| `areaDataKnd` | `32` | Dataset: 需要予想・ピーク時供給力(翌々日). Catalog in [§5](#5-dataset-catalog) |
| `areaAllTermDwld` | `Y` | すべての期間 — the full history. **Omit** to use the date range instead |
| `areaNngpFrom` / `areaNngpTo` | `2026/08/01` | Date range (filters on **対象日付**), used when `areaAllTermDwld` is omitted |
| `allAreaSectDwld` | `11` | "All areas" checkbox |
| `hkd` `thk` `tko` `chb` `hkr` `kns` `cgk` `skk` `kys` `oki` | `01`–`10` | Area checkboxes: 北海道 01, 東北 02, 東京 03, 中部 04, 北陸 05, 関西 06, 中国 07, 四国 08, 九州 09, 沖縄 10. Omitting all of them → 対象エリアが入力されていません |
| `areaSum` | `11` | エリア計 rows (9エリア計 + 10エリア計) |

Note the area checkbox values differ from the interactive screens (`Y` there, `01`–`10`
here).

### 3.3 `curl` reproduction

Downloads the complete 翌々日 history (all areas + totals) into `dad.csv`:

```bash
H='https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD'
FW='fwExtention.actionType=reference&fwExtention.pagingTargetTable=&fwExtention.pathInfo=CF01S010C&fwExtention.prgbrh=0&fwExtention.formId=CF01S010P&fwExtention.jsonString=&ajaxToken=&requestTokenBk=&transitionContextKey=DEFAULT'
SEL='tabSntk=1&areaDataKnd=32&areaAllTermDwld=Y&allAreaSectDwld=11&hkd=01&thk=02&tko=03&chb=04&hkr=05&kns=06&cgk=07&skk=08&kys=09&oki=10&areaSum=11'

# 1. session
curl -s -c cookies.txt "$H/LOGIN_login" -o /dev/null

# 2. reference/ok -> downloadKey + requestToken
curl -s -b cookies.txt -c cookies.txt -H 'sdReqType: AJAX' \
  -X POST "$H/CF01S010C" \
  --data "$FW&fwExtention.actionSubType=ok&requestToken=&downloadKey=&$SEL" -o ok.json
KEY=$(python3 -c "import json;print(json.load(open('ok.json'))['root']['bizRoot']['header']['downloadKey']['value'])")
TOK=$(python3 -c "import json;print(json.load(open('ok.json'))['root']['bizRoot']['header']['requestToken']['value'])")

# 3. reference/download -> the CSV
curl -s -b cookies.txt -c cookies.txt \
  -X POST "$H/CF01S010C" \
  --data "$FW&fwExtention.actionSubType=download&requestToken=$TOK&downloadKey=$KEY&$SEL" \
  -o dad.csv

iconv -f CP932 -t UTF-8 dad.csv | head -3
```

For an incremental pull, replace `areaAllTermDwld=Y` in `SEL` with
`areaNngpFrom=2026%2F08%2F01&areaNngpTo=2026%2F08%2F05` (the range filters on 対象日付)
in **both** the `ok` and `download` requests.

### 3.4 Failure modes

All with HTTP 200:

- **不正なリクエストです (HTML page)** — missing/blank/reused `requestToken` or
  `downloadKey` on the `download` step, or a malformed transition. Re-run `ok` to get a
  fresh pair.
- **利用権限がないため、利用出来ません (HTML page)** — the body's
  `fwExtention.pathInfo` names a different screen than the URL ([§2](#2-the-portal-framework)).
- **Session-timeout JSON** (`interceptorErr`: 一定時間操作が行われなかったため…) —
  missing or expired cookies; redo the `LOGIN_login` GET.
- **Validation errors as JSON `errMessage`** — e.g. no areas selected, or `tabSntk`
  pointing at the wrong tab.
- **Detection**: a successful download has a `Content-Disposition: attachment` header
  and the body decodes as CP932 with the expected header row; anything else is an error
  page (error HTML is UTF-8).

## 4. The 翌々日 CSV format

- **Encoding**: Shift_JIS (`cp932`). **Line endings**: LF. One header row, no
  preamble; data rows immediately follow.
- **Filename** (from `Content-Disposition`):
  `<YYYYMMDDhhmmssSSS>_電力需要予想ピーク時供給力翌々日.csv`.
- **Sort order**: newest 対象日付 first.

Columns:

| # | Column | Meaning | Notes |
|---|---|---|---|
| 1 | 策定日 | Forecast formulation date | Always 対象日付 − 2 days (verified on every row) |
| 2 | 対象日付 | Target date | `YYYY/MM/DD` |
| 3 | 対象エリア | Area | 北海道, 東北, 東京, 中部, 北陸, 関西, 中国, 四国, 九州, 沖縄, 9エリア計, 10エリア計 |
| 4 | 最小総需要予想時刻 | Time of min total demand | `HH:MM`; nullable (see below) |
| 5 | 最小総需要予想（MW） | Min total demand forecast | |
| 6 | 最大総需要予想時刻 | Time of max total demand | `HH:MM`; nullable |
| 7 | 最大総需要予想（MW） | Max total demand forecast | |
| 8 | 最大供給力予想（MW） | Max supply capacity forecast | |
| 9 | 予想使用率 | Forecast usage rate (%) | max demand ÷ max supply |
| 10 | 予想予備率 | Forecast reserve margin (%) | |

**Grain**: one row per (対象日付, 対象エリア). Since 策定日 is functionally dependent
(対象日付 − 2), the natural key is (対象日付, 対象エリア).

**Verified completeness** (capture of 2026-08-16): 対象日付 2024/03/13 – 2026/08/17,
888 days × 12 areas = 10,656 rows, perfectly rectangular — no missing days or areas.

**Nullability**: the two 時刻 columns are empty for every 10エリア計 row through
2025/03/31 (384 days) and populated from 2025/04/01. All other cells were non-null.

**Official data caveats** (shown in the CC01S035C screen's message area):

- 翌々日 広域予備率・エリア予備率 values dated **2024/03/31 and earlier are test data**
  (試験データ).
- 最小総需要予想（MW）values dated **2025/03/31 and earlier are actually
  最小予備率時総需要予想** (total demand at the minimum-reserve-rate time, not the
  minimum-demand time).
- For dates 2025/03/31 and earlier, times of N時30分 are **rounded up to the full hour**
  in the display.

## 5. Dataset catalog

The same three-request flow serves every dataset on the 情報ダウンロード screen — change
only `tabSntk` + the radio value (and note the 連系線 tab has its own selection fields,
`rkl*`, not documented here). Radio values and availability as captured 2026-08-16
(ranges from the screen's `initDisplay` response):

**エリア・広域ブロック情報 tab (`tabSntk=1`, radio `areaDataKnd`):**

| Value | Dataset | Available range |
|---|---|---|
| `01`–`06` | 需要予想・ピーク時供給力: 長期 01, 年間 02, 月間 03, 週間 04, 翌日 05, 当日 06 | 年間 2016〜2027, 月間 2016/05〜, 週間 2016/04/09〜, 翌日・当日 2016/04/01〜 |
| `32` | **需要予想・ピーク時供給力: 翌々日** | **2024/03/13〜** |
| `22`–`24`, `30` | 広域予備率（広域ブロック情報）: 週間 22, 翌日 23, 当日 24, 翌々日 30 | 2025/04/01〜 |
| `25`–`27`, `31` | 広域予備率（エリア・広域ブロック情報）: 週間 25, 翌日 26, 当日 27, 翌々日 31 | 2025/04/01〜 |
| `28`, `29` | 補正料金算定インデックス: 翌日 28, 当日 29 | 当日分のみ |
| `07` | 電力使用状況（でんき予報） | 2016/04/09〜 |
| `08`, `09` | 周波数 50Hz系統 08 / 60Hz系統 09 | 2016/04/07〜 |
| `10`–`12` | 需要実績: 年間 10, 月間 11, 日別 12 | 2016〜 |
| `13`–`16` | 地内基幹送電線 運用容量・予想潮流: 長期 13, 年間 14, 当日 15, 実績 16 | 長期 2016〜2030 |
| `17` | 地内基幹送電線潮流実績 | 2025/04/01〜 |
| `18` | 作業停止計画・実績 | 2016/03/19〜 |
| `19` | 故障情報 | 2016/07/29〜 |
| `20`, `21` | 再生可能エネルギー出力抑制実績: 年度 20, 年月 21 | 2015/05〜 |

**連系線 tab (`tabSntk=0`, radio `rklDataKnd`):** 空容量 長期 01 / 年間 02 / 月間 03 /
週間 04 / 翌々日 05 / 翌日 06 / 当日 07 (2016/06〜), 計画変更賦課金 09 / 通告変更賦課金
10 (2016/09〜2018/09), 連系線潮流実績 11 (2025/04/01〜), 1時間前取引受付停止情報 12
(2016/09/08〜).

Several 実績 datasets (連系線潮流実績, 地内基幹送電線潮流実績, 広域予備率) start
2025/04/01 — the occtonet3 system's cutover date; older history, where it exists, lives
in the previous system's archives, not this portal.

## 6. Interactive screens

Documented for completeness; the bulk path above supersedes them for data retrieval.

- Menu entry: 公表 → 需給関連情報 → 電力需要予想・ピーク時供給力参照 →
  `CC01S034C` (翌日・当日). The other horizons are sibling screens reached by the tab
  bar: 長期 `CC01S030C`, 年間 `CC01S031C`, 月間 `CC01S032C`, 週間 `CC01S033C`,
  **翌々日 `CC01S035C`**. Tab switches are plain form POSTs to the sibling screen with
  body `fwExtention.actionType=reference&fwExtention.actionSubType=init`.
- Screen fields (`CC01S035C`): 策定日 `dvlDayFrom`/`dvlDayTo` (readonly in the UI,
  auto-derived as 対象日 − 2), 対象日 `tgtDayFrom`/`tgtDayTo`, area checkboxes
  `hkd`…`oki`/`areaSum` (value `Y` here), dates formatted `YYYY/MM/DD`.
- Search = AJAX `reference`/`initTable`; the JSON's `bizRoot.table.table1` carries the
  rows (10/page, paging via `table1.*` fields). The screen's own CSV button runs
  `reference`/`print` → `reference`/`printOK` → `reference`/`download` — same handshake
  shape as bulk but with subtype `printOK` instead of `ok`, and it only covers the
  searched range.
- **Unresolved**: headless `initTable` replays echoed the search criteria back but
  returned 0 rows even for dates that display in the browser; some screen-state
  precondition (likely an `initDisplay`-seeded server-side context) was not
  reproduced. Not investigated further — the bulk download made it moot.

## 7. Publication timing and operational notes

### 7.1 When the 翌々日 data becomes available

For a target day **D**, the 翌々日 forecast is published on **D−2 at or after 17:30 JST,
in practice ~17:35–18:05** (all times JST):

| Series | Rule (本機関が公表する系統情報の項目等, 2026-04) | OCCTO guidance | Observed |
|---|---|---|---|
| **翌々日** | 「翌々日：毎日（※４）１７時３０分以降速やかに」 | 「翌々日計画 48点 毎日17:40頃の公表時」; timeline: 「18時頃 広域予備率公表」 | Portal update notices for target 8/17: 17:45–17:48 on 8/15; screen footer 「2026年08月15日 17時47分更新」. Web公表 `todayLastUpdate` over 124 sampled target dates (2025-04..2026-08): **always D−2**, 17:33–18:29, median 17:47, p90 18:04, one outlier at 23:38 (2025-06-17). |
| 翌日 | 「翌日：毎日（※４）１７時３０分以降速やかに」 | 「翌日計画 48点 毎日17:35頃」 | 17:45 on D−1 |
| 当日 | 「当日：都度（３０分周期）」 | 「当日 48点 30分ごとの更新時」 | rolling, e.g. 11:59 |
| 週間 | 「週間：毎週木曜日」 | 「毎週木曜日17:40頃」 | Thu ~17:25 |

(※４) 「公表の当日が休業日のときも、本表に定める公表時期のとおりとする」 — weekends and
holidays included, which the data confirms: every one of the 888 consecutive target dates
since 2024-03-13 is present.

Why 17:30: the upstream planning chain in 送配電等業務指針 別表８ fixes it. Balancing
groups submit 翌々日計画 by **D−2 10:00**; the TSOs' 需給バランス計画 (翌々日/翌日) is
due **D−2 17:30**; OCCTO then computes and publishes the 広域予備率 (業務規程 第108条２).
The 2023-03-29 briefing timeline reads: D−2 10時 BG計画提出期限 → 17時頃 一送需給バランス
計画提出期限 → **18時頃 広域予備率公表**; D−1 10時頃 スポット約定 → 12時 BG翌日計画提出期限.
So the 翌々日 forecast for D is available roughly **16 hours before the JEPX day-ahead
auction for D closes** (D−1 10:00 gate closure), which is what makes it usable as a
spot-price feature; the 翌日 forecast (D−1 ~17:35) is **not** — it lands after the
auction.

History: 翌々日 publication began 2024-03-11 (values through 2024-03-31 published as
参考値); FY2024 had two daily points (最大需要時・最小予備率時), FY2025 onward 48
half-hourly points; the 2025-03-05 screen change renamed 「最小予備率時（MW）」 to
「最小総需要予想（MW）」 — the semantic break in [§4](#4-the-翌々日-csv-format).

**Revisions after first publication.** The rules allow recalculation in principle
(業務規程 第108条２ bases the calculation on plans 「当該計画を変更する計画を含む」, and
OCCTO says it re-assesses 「必要に応じて」 when supply-demand changes unexpectedly), but
empirically the 翌々日 series behaves as a **single D−2 snapshot**: the CSV has exactly one
策定日 per (対象日, エリア), always D−2, and all 124 sampled update timestamps fall on D−2
(the lone 23:38 case may be a same-evening re-publication; the field only shows the latest
update). The 翌々日 view is superseded by the separate 翌日 dataset, not overwritten.

**Scheduling**: pull once daily **after ~18:15 JST** to catch the p90 case; a second pull
next morning covers rare late updates. Sources:
[本機関が公表する系統情報の項目等 (2026-04)](https://www.occto.or.jp/assets/occto/article/kikan_kouhyou_koumoku2604.pdf),
[送配電等業務指針 (2026-08)](https://www.occto.or.jp/assets/occto/article/index/shishin2608.pdf),
[業務規程 (2026-08)](https://www.occto.or.jp/assets/occto/article/index/gyoumukitei2608.pdf),
[翌々日計画 事業者説明会 2023-03-29](https://www.occto.or.jp/assets/occtosystem2/oshirase/2022/files/20230323_setumeikai01.pdf) (pp. 10, 13, 15),
[容量市場 説明会 2025-10](https://www.occto.or.jp/assets/market-board/market/oshirase/2025/files/251010_youryou_jitsujukyu_setsumeikai_requirement_r2.pdf) (pp. 15–17),
[公表画面改修のお知らせ 2024-03](https://www.occto.or.jp/assets/occtosystem2/oshirase/2023/files/20240308_kohyogamenkaishu.pdf) /
[2025-03](https://www.occto.or.jp/assets/occtosystem2/oshirase/2024/files/20250303_kohyogamenkaishu.pdf).

### 7.2 Operational notes

- A **daily refresh is 3 HTTP calls** for the full-history file (~700 KB) — cheap enough
  that incremental range pulls are an optimization, not a necessity. Prefer re-downloading
  the whole file and reloading idempotently (same pattern as the JEPX loaders).
- This is OCCTO's operational portal; keep access minimal (no polling loops, no
  per-day request storms — the bulk file makes them unnecessary anyway).
- The data lives server-side per session-issued `downloadKey`; keys are cheap but
  single-use. Do not cache them.

## 8. Downloading and loading with `power_market_analytics.occto`

`OcctoBulkDownloader` (`power_market_analytics/occto.py`) implements the three-request
handshake of [§3](#3-bulk-download-情報ダウンロード-cf01s010c): fresh anonymous session,
`reference/ok` for the key/token pair, `reference/download` for the file, then a
header-row check so an error page can never be saved as data. It always re-downloads
(there is no cache — the file is the whole dataset and OCCTO appends to it daily) and
writes atomically via a `.part` rename.

```python
from power_market_analytics.occto import OcctoBulkDownloader

downloader = OcctoBulkDownloader()  # data_dir="data/occto"
path = downloader.download("demand_forecast_dad")
# -> data/occto/demand_forecast_dad/demand_forecast_dad.csv  (~700 KB, ~4 s)

# Optional 対象日付 range instead of the full history:
import datetime as dt
downloader.download("demand_forecast_dad", dt.date(2026, 8, 1), dt.date(2026, 8, 5))
```

The end-to-end refresh is one `just` recipe (downloader and loader run in the
devcontainer; the loader needs Spark):

```bash
just refresh-occto
# = just python scripts/download_occto_demand_forecast.py
#   just python scripts/load_occto_demand_forecast.py
#   just dbt build
```

`scripts/load_occto_demand_forecast.py` performs a full reload through the generic
`CsvLoader` with the contract in `conf/schemas/occto_demand_forecast_dad.yaml`
(`windows-31j`, dates parsed from `yyyy/MM/dd`, grain `(target_date, area_name_ja)`
enforced, the two 時刻 columns kept as strings because `24:00` is not a valid Spark
time) into `pma_raw.occto_demand_forecast_dad`. dbt then builds:

| Model | Layer | What it adds |
|---|---|---|
| `stg_occto__demand_forecast_dad` | staging | As-is view of the raw table with an enforced contract and accepted-values test on the area names |
| `std_occto__demand_forecast_dad` | standardized | `area_code` (snake-case, matching `dim_area`; `okinawa`, `total_9_areas`, `total_10_areas` for the rest), `is_area_total`, `forecast_horizon_days` (asserted = 2), the `HH:00` labels parsed to `*_hour_ending` ints 1–24, and the published percentages converted to fractions (`usage_rate`, `reserve_rate`: 92.4 → 0.924) |
| `fct_occto_demand_forecast` | curated | Periodic snapshot at (`date_key`, `area_key`) for the **9 JEPX areas only, from 2024-04-01** — the エリア計 roll-ups are excluded so the grain stays atomic (Kimball), Okinawa because it has no `dim_area` row, and the pre-FY2024 trial publication (2024-03-13..31, OCCTO's 試験データ) because the source disowns it. All of it remains queryable in the standardized model. |

Grain check on the first load (2026-08-16): 10,656 raw rows → 7,821 fact rows =
869 days × 9 areas (10,656 − 228 trial rows − エリア計/沖縄 rows), all tests green.

Data-caveat handling:

- **2024/03/31 試験データ** ([§4](#4-the-翌々日-csv-format)): filtered out of the fact
  table. The whole row is dropped, not just the rate columns, because the rates are derived
  from the demand/supply forecasts in the same row — a trial-run reserve rate implies a
  trial-run demand forecast. 19 days; the fact's `date_key` carries an `accepted_range`
  test with `min_value = 2024-04-01` so the rule is asserted, not just applied.
- **2025/03/31 semantic break in 最小総需要予想** ([§4](#4-the-翌々日-csv-format)): documented
  on the models but **not** filtered — the other facts are unaffected across that boundary.
  Consumers building features from the minimum-demand columns should restrict to
  `date_key >= 2025-04-01` or treat the two eras separately.
