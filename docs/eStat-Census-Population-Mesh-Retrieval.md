# e-Stat Census Population Mesh (国勢調査 500 m メッシュ) Data Retrieval

How the Statistics Bureau publishes the Population Census on the 500 m mesh
through e-Stat 統計GIS, what the files look like, how the privacy processing
works, and how `power_market_analytics.estat` / `estat_loader` bring the total
population per mesh into the warehouse for every configured census vintage.
The output feeds a later population-weighted weather aggregation; the
weather-grid crosswalk and the weights themselves are **not** part of this
pipeline.

Verified against full captures of the 2015 and 2020 listings and archives on
2026-08-19 (151 primary-mesh archives per vintage; the loaded totals equal
the official census counts, 127,094,745 and 126,146,099) and the official
2015 table definition (`T000847` 定義書) for the privacy rules.

## 1. Overview

- **Publisher**: 総務省統計局 via e-Stat 統計GIS
  (<https://www.e-stat.go.jp/gis>) — 統計データダウンロード → 国勢調査 →
  4次メッシュ（500ｍメッシュ）.
- **Content**: one row per nine-digit 500 m mesh (`KEY_CODE`) with the
  demographic tabulation その１ 人口等基本集計に関する事項 — total population,
  by sex, age bands, households, etc. **Only total population is loaded.**
- **Vintages**: each census is a separate e-Stat statistics table
  (`statsId`); the differences live in `power_market_analytics.estat.VINTAGES`:

  | Census | Census date | Datum | `statsId` | Population column | Files |
  |---|---|---|---|---|---|
  | 2015 | 2015-10-01 | JGD2000 (世界測地系) | `T000847` | `T000847001` 人口総数 | 151 |
  | 2020 | 2020-10-01 | JGD2000 | `T001101` | `T001101001` 人口（総数） | 151 |

  e-Stat also publishes a JGD2011 duplicate of the 2020 tables; the JGD2000
  product is used so mesh geography is consistent across the initial vintages
  (`dim_population_mesh_500m` asserts that every mesh code carries a single
  set of geographic attributes — a JGD2011 vintage would need its own
  decision at that time).
- **Packaging**: the 第１次地域区画 (primary mesh, four digits) packages, one
  zip per primary mesh, listed on the filtered listing pages
  ([2015](https://www.e-stat.go.jp/gis/statmap-search?page=1&type=1&toukeiCode=00200521&toukeiYear=2015&aggregateUnit=H&serveyId=H002005112015&statsId=T000847),
  [2020 JGD2000](https://www.e-stat.go.jp/gis/statmap-search?page=1&type=1&toukeiCode=00200521&toukeiYear=2020&aggregateUnit=H&serveyId=H002005112020&statsId=T001101));
  the 都道府県 packages are not used.

## 2. Listing pages and download URLs

The listing page (`/gis/statmap-search?…`) is a Drupal JavaScript shell: the
HTML contains the filter menus but **no result rows**. The rows are fetched by
the page's script (`gis_download-main.js`) from a JSON endpoint with the same
query string plus two flags:

```text
GET https://www.e-stat.go.jp/gis/statmap-search/search_detail
    ?page={N}&type=1&toukeiCode=00200521&toukeiYear=2015&aggregateUnit=H
    &serveyId=H002005112015&statsId=T000847&mesh_data_flg=1&download_disp_flg=1
```

The response is JSON whose fields are HTML fragments:

| Field | Content |
|---|---|
| `detail` | The 20 result rows of the page: `<article class="stat-resorce_list-item">` … `<a class="stat-dl_icon stat-statistics-table_icon" href="/gis/statmap-search/data?statsId=T000847&code=3622&downloadType=2">` |
| `paginate` | The pager, including `<div class="stat-paginate-index …">1/8ページ</div>` on every page (a page past the end returns `9/8ページ` and no rows) |
| `side_mega` | The hit count (`<span class="… js-total_resource">151</span>件のデータ`) |

`CensusVintage.listing_detail_url(page)` derives that URL from the vintage's
`listing_url` (path swap + the two flags), the downloader walks pages
`1..M` using the `N/Mページ` index, collects the `code=` values of the
download links (order preserved, duplicates dropped), and requires that
every link carry the vintage's `statsId`, that every code be four digits, and
that the de-duplicated count equal `expected_file_count` (151) — a changed
publication fails loudly instead of loading a partial country.

Individual archives:

```text
GET https://www.e-stat.go.jp/gis/statmap-search/data
    ?statsId={stats_id}&code={primary_mesh_code}&downloadType=2
→ application/octet-stream, Content-Disposition: attachment; filename*=UTF-8''tbl{stats_id}H{code}.zip
```

Plain `GET`, no session, cookie or user agent required. The archives are
generated on request and take **~10 s each** server-side regardless of size
(1 KB for an offshore islet up to 1.2 MB for 5339 Tokyo; ~36 MB for both
vintages, ~120 MB extracted), so a cold download of one vintage is ~25 min
and both vintages ~50 min; the downloader spaces requests 0.5 s apart on top
of that. A rerun with everything cached takes ~10 s (listing pages only).

## 3. Archive and text-file format

Each zip holds exactly one member, `tbl{stats_id}H{primary_mesh_code}.txt`
(e.g. `tblT000847H5339.txt`, 16,734 meshes for the Tokyo primary mesh; the
151 files of a vintage hold 471,066 populated meshes in 2015 and 466,156 in
2020, 481,799 distinct across both); any other structure fails the download. The member is CP932 (Shift_JIS with
Windows extensions), comma-separated, CRLF, unquoted, with **two header
rows** — the source codes, then the Japanese labels (empty over the code
columns, each label prefixed with a full-width space):

```text
KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847001,T000847002,T000847003,T000847004,…
,,,,　人口総数,　人口総数　男,　人口総数　女,　０～１４歳人口総数,…
533900054,0,,,64,33,31,6,5,1,58,28,30,35,19,16,55,26,29,23,9,14,9,1,8,0,0,0,21,21,…
533900064,2,533900073,,3,1,2,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,*,2,2,*,…
533900073,1,,533900064,57,27,30,6,2,4,54,26,28,24,13,11,54,26,28,30,13,17,…
533900341,1,,533900342;533900343,24,12,12,0,0,0,30,15,15,11,6,5,29,15,14,…
```

| Column | Meaning | Notes |
|---|---|---|
| `KEY_CODE` | Nine-digit 500 m mesh code | Always starts with the file's primary mesh code; final digit 1–4 (§5) |
| `HTKSYORI` | 秘匿処理 privacy-processing code | 0 none; 1 this mesh absorbed another mesh's suppressed detail statistics; 2 this mesh's detail statistics were folded into another mesh |
| `HTKSAKI` | 秘匿先情報 | For `HTKSYORI = 2`: the mesh that received the detail statistics |
| `GASSAN` | 合算情報 | For `HTKSYORI = 1`: the mesh(es) folded in, **semicolon-delimited** when several |
| `T000847001` / `T001101001` | Total population | Never suppressed |
| other `T…` columns | Detail measures | `*` where suppressed (rows with `HTKSYORI = 2`) |

2015 has 45 columns (its second label is 人口総数); 2020 has 54 (人口（総数）,
extra 18+/85+ age bands). The 5339 file has 16,734 rows in 2015 (15,295 ×
code 0, 644 × 1, 795 × 2) and 16,671 in 2020; ~115 rows per vintage carry a
semicolon-delimited `GASSAN`.

## 4. Privacy processing — what is (not) done with it

The 秘匿処理 exists to protect small-count detail cells: for a mesh with very
few residents, the sex / age / household breakdown is replaced by `*`
(`HTKSYORI = 2`) and added to a neighbouring mesh (`HTKSYORI = 1`), whose
detail columns therefore cover several meshes. **Total population is
explicitly excluded** from this — the table definition marks it 秘匿対象外 —
so every mesh reports its own headcount, and the pipeline:

- keeps `population_total` exactly as published at every mesh, whatever
  `HTKSYORI` says (in the example above 533900064 keeps its 3 residents and
  533900073 its 57);
- does **not** add, subtract or reallocate population along `HTKSAKI` /
  `GASSAN` — those relationships describe the folded detail statistics, and
  applying them to the total would double-count;
- carries `HTKSYORI`, `HTKSAKI` and `GASSAN` verbatim (`privacy_processing_code`,
  `aggregation_destination_mesh_code`, `aggregation_source_mesh_codes`) for
  traceability, and fails the load if population contains `*`, is not a
  non-negative integer, or a mesh code is malformed / outside its file's
  primary mesh.

Summing `population_total` across meshes is therefore correct for any
geography; the detail columns are not loaded because they would not be.

## 5. Mesh codes and coordinates

`KEY_CODE` is a JIS X 0410 4次メッシュ code `AABB C D E F G`:

| Part | Meaning | Size |
|---|---|---|
| `AA` | latitude band, `AA × 2/3` degrees N | 40′ |
| `BB` | longitude band, `100 + BB` degrees E | 1° |
| `C`, `D` | second-level row / column (0–7) | 5′ × 7′30″ |
| `E`, `F` | third-level row / column (0–9) | 30″ × 45″ (the "1 km" mesh) |
| `G` | 500 m quadrant of the third-level mesh: 1 SW, 2 SE, 3 NW, 4 NE | 15″ × 22.5″ |

Lower-left corner, then the quadrant shift and the box:

```text
south = AA × 2/3 + C/12 + E/120        west = 100 + BB + D/8 + F/80
if G in (3, 4): south += 1/240          if G in (2, 4): west += 1/160
north = south + 1/240                   east = west + 1/160
centroid = ((south + north)/2, (west + east)/2)
```

`power_market_analytics.estat.decode_mesh_code("533946114")` (東京駅) →
centroid `35.681250 N, 139.771875 E`; `std_estat__census_population_mesh`
applies the same formula in SQL and `dim_population_mesh_500m` carries the
result. Structural validity is `^\d{4}[0-7]{2}\d{2}[1-4]$` (checked by the
loader and by a dbt test). Coordinates are on the vintage's datum (JGD2000);
no boundary polygons are stored.

## 6. Downloading and loading with `power_market_analytics.estat`

`power_market_analytics/estat.py` holds the vintage configuration
(`CensusVintage`, `VINTAGES`), the mesh decoder and the downloader;
`estat_loader.py` the vintage-aware `EstatCensusMeshCsvLoader`.

```python
from power_market_analytics.estat import EstatCensusMeshDownloader, vintage_for_year

downloader = EstatCensusMeshDownloader()            # data/estat/census_population_mesh
downloader.discover_primary_mesh_codes(vintage_for_year(2020))   # ['3622', '3623', ..., '6848']
downloader.download_all()                            # both vintages, cached
downloader.download_all(years=[2020], force=True)    # re-fetch one vintage
# zips -> data/estat/census_population_mesh/{year}/zip/tbl{statsId}H{code}.zip
# txts -> data/estat/census_population_mesh/{year}/txt/tbl{statsId}H{code}.txt
```

Every archive is validated (real zip, exactly the expected member) **before**
it is moved into place (`.part` → rename), so an interrupted or bad response
never leaves a cached artefact; the member is written out byte-for-byte
(CP932, CRLF, no re-encoding). A rerun without `--force` re-reads the
listing pages (8 cheap JSON calls per vintage — this is what enforces the
expected file count) and reuses every cached archive; census tables never
change once published, so `--force` is only for a corrupted cache.

The loader takes the downloader root, finds `*/txt/*.txt`, identifies each
file's vintage from its name (`statsId` → `VINTAGES`), sniffs the two header
rows in Python (all four privacy columns and the vintage's population column
must be present, line 2 must be the label row), reads the file with Spark
(`windows-31j`), validates every row before casting (§4), injects
`census_year`, `census_date`, `geodetic_datum`, `stats_id`,
`primary_mesh_code`, `population_total` (from the vintage's column) and
`source_file`, and full-reloads `pma_raw.estat_census_population_mesh`
(contract `conf/schemas/estat_census_population_mesh.yaml`, grain
`(census_year, mesh_code)`). End to end:

```bash
just refresh-estat                    # ~50 min cold (server-side archive generation), ~2 min when cached
# = just python scripts/download_estat_census_population_mesh.py   [--years 2015 2020] [--force]
#   just python scripts/load_estat_census_population_mesh.py
#   just dbt build
```

Warehouse path: `pma_raw.estat_census_population_mesh` →
`stg_estat__census_population_mesh` → `std_estat__census_population_mesh`
(+ decoded bounding box / centroid) → `dim_population_mesh_500m` (one row
per mesh, distinct across vintages) and `fct_census_population_mesh`
(`census_year × mesh_code`, `population_total`; a periodic snapshot —
additive across meshes, not across census years). No `population_weight`
column: its denominator depends on the later target geography or weather
grid.

Unit tests: `tests/test_estat.py` (vintages, decoder, listing pagination and
link discovery from fixture JSON, expected-count / dedup / cache / force /
atomic write / malformed-archive behaviour), `tests/test_estat_loader.py`
(CP932 parsing, both population-column mappings, privacy metadata, every
load-time validation), plus the CLI registries in
`tests/test_download_scripts.py` / `tests/test_load_scripts.py` — `just test`.

## 7. Adding a census vintage

1. Find the table on e-Stat: 統計データダウンロード → 国勢調査 → the census
   year → 4次メッシュ（500ｍメッシュ）→ その１ 人口等基本集計に関する事項, choose
   the datum product (JGD2000 to stay consistent), and copy the listing URL
   (it carries `toukeiYear`, `serveyId` and `statsId`).
2. Open one archive and note the total-population column header
   (`{statsId}001` so far) and the number of 第１次地域区画 downloads shown as
   the hit count.
3. Append a `CensusVintage(...)` to `VINTAGES` in
   `power_market_analytics/estat.py` (`census_year`, `census_date`,
   `geodetic_datum`, `stats_id`, `population_source_column`, `listing_url`,
   `expected_file_count`) and add the year to the tests' vintage assertions
   and the singular dbt test `assert_fct_census_population_mesh_has_every_vintage`.
4. `just refresh-estat` (or `--years <year>` first). No schema change is
   needed: the raw contract, models and tests are vintage-agnostic; the
   `dim_population_mesh_500m` unique test will flag any mesh whose
   attributes (datum) differ from the loaded vintages.
