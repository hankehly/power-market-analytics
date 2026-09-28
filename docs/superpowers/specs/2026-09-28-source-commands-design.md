# Source commands — one script per data source, with `download` and `load` subcommands — design

Date: 2026-09-28. Status: **draft, for the researcher's review** (pattern 1 chosen in chat on
2026-09-28 out of three; nothing built). Branch `chore/source-commands`.

## 1. Goal

Replace the twenty-four ingestion scripts under `scripts/` — twelve downloads, eleven loads
and the JMA station-seed updater — with six, one per data source. Each is a command with two
verbs, `download` and `load`, and one subcommand per dataset under each verb:

```bash
just jma download hourly --prefecture 44
just jma load hourly
just jma download msm_surface_forecast --start-date 2019-04-01
just tepco download power_usage --force-yearly
```

What must hold afterwards:

- Every flag and default of today's scripts survives, but for the four named in decisions
  6 and 7.
- `just refresh-all` runs the same twenty-four steps in the same order, then `dbt build`.
- `--help` at every level lists what is under it: `just jma -h` the verbs, `just jma
  download -h` the datasets, `just jma download hourly -h` the flags.
- No new dependency. The tests keep their seam: the downloader or loader class swapped for
  a fake in the script's namespace, the script driven through `main(argv)`.
- `jma load msm_surface_forecast` never imports eccodes, as the MSM loader does not today.

Out of scope: `update_holidays_seed.py` (decision 11) and the nine modeling and tooling
scripts (`demand_backtest.py`, `spot_price_backtest.py`, the two compare scripts,
`create_forecast_dashboard.py`, `fit_similar_day.py`, `generate_feature_views.py`,
`show_preset.py`, `check_docs_links.py`). `scripts/` goes from 34 files to 16.

## 2. Decisions

1. **Pattern 1: source, then verb, then dataset.** Two others were weighed on 2026-09-28.
   Verb commands with path-like ids (`just load jma/hourly`) fit the eleven identical loads
   and not the twelve bespoke downloads. A package console script (`pma jma download
   hourly`) is the same grammar under one noun; its one advantage is room for the modeling
   scripts later, which is not this work. Pattern 1 is the consolidation asked for, with no
   new concept.
2. **A dataset's name is its directory under `data/<source>/`.** That name is already the
   downloader's key, the schema file's stem and the raw table's suffix, or their prefix.
   So OCCTO's datasets are `demand_forecast_dad` and `area_reserve_rate_dad`, the keys
   `OcctoBulkDownloader.download()` takes, not the scripts' shorter `demand_forecast` /
   `area_reserve_rate` said in chat: one rule with no exception, and the parsed dataset
   name is the key. The station master writes a seed, not a data directory; it is
   `stations`, after `dbt/seeds/jma_stations.csv`.
3. **Six files under `scripts/`, argparse.** `scripts/jma.py`, `jepx.py`, `occto.py`,
   `tepco.py`, `kansai.py`, `estat.py`. Nested `add_subparsers`, both levels
   `required=True`, `prog=<source>` so usage reads `jma download hourly [-h] …` whichever
   way the file is run. No click or typer: the other scripts are argparse, and a new
   dependency would be audited for nothing.
4. **A subcommand's help is its handler's docstring, which is today's module docstring.**
   `description=download_hourly.__doc__` with `RawDescriptionHelpFormatter`, so the
   paragraphs of the long ones (the hourly scrape, the MSM download) survive. Each script's
   own module docstring is one line per verb.
5. **One shared load helper, in the package.** `power_market_analytics/ingestion/cli.py`:
   `add_load_arguments(parser, *, schema, data, table, data_help=…)` adds the three
   arguments every load has, `--schema`, `--data` and `--table`, with the dataset's
   defaults; `load_from_args(args, loader_cls)` reads the contract, runs the loader and
   logs the row count. `scripts/` is not a package, so shared code cannot live there, and
   `loader.py` is 813 lines of CSV engine that stays that. A load handler is one line,
   `load_from_args(args, JmaNormalsCsvLoader)`, the class looked up in the script's
   namespace at call time, so the tests' swap still lands.
6. **`jma download hourly` absorbs the per-station script.** `download_jma_hourly_all.py`'s
   plan gains `--station ID …`, a filter like `--prefecture`; no match raises the same
   `ValueError`. `download_jma_hourly.py`'s two other flags go. `--elements`, because the
   raw table has one contract, the 7-element layout, and any other element set is
   exploration the Python API still offers (`JmaHourlyDownloader.download(station,
   elements, year)`, the retrieval doc §6.3). `--force-all`, because the cache is the resume
   mechanism and a file to refetch is a file to delete. What the per-station defaults gave
   (s47662, 2016 to this year, this year refreshed) is what the plan gives for
   `--station s47662`.
7. **`jma load hourly` takes the three shared arguments.** `--schema
   conf/schemas/jma_hourly_staffed.yaml`, `--data
   data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv`, `--table
   pma_raw.jma_hourly_staffed`, under `REPO_ROOT` as the other loads are.
   `load_jma_hourly.py`'s `--data-dir` / `--schema-dir` and its one-row `FORMATS` table
   were the shape of two station classes; there has been one since the 2026-08 re-scope.
8. **The MSM downloader is imported inside its handler.** `msm/download.py` imports
   `msm/grib.py`, which imports eccodes at module level; CLAUDE.md records that only the
   downloader reaches it. A top-level import in `scripts/jma.py` would make every jma
   subcommand need eccodes, `load` included. So `download_msm_surface_forecast` imports
   `MsmDownloader` in its body, the one lazy import of the six files, the reason in a
   comment. Its tests patch the class on `msm.download` rather than on the script, and one
   test proves the property (§6).
9. **Six justfile recipes, sugar over `just python`.** `jma *args:` runs `just python
   scripts/jma.py "$@"`, one line each, the `[doc]` naming the datasets. On 2026-08-31 the
   researcher dropped seventeen `refresh-<source>` / `ingest-<source>` recipes as clutter;
   these six are not refresh shortcuts but the commands themselves, and `just --list` is
   where a source's command is found. `refresh-all` keeps its explicit, ordered list: the
   order (JMA before MSM, since the MSM downloader reads the station seed) lives there and
   nowhere else, so there is no `refresh` verb and no `--all`. `.claude/settings.json` gains
   no allow entry, as `just python` has none. Should the researcher prefer no new recipes,
   everything below works as `just python scripts/jma.py download hourly`; only §5 changes.
10. **`build_plan` stays in the script.** It moves verbatim from
    `download_jma_hourly_all.py` with the `stations` filter added, and
    `MAX_CONSECUTIVE_FAILURES` with it. Moving the plan and the download loop into
    `ingestion/jma/hourly.py` would leave every handler a thin call; that is a change of its
    own, for another day.
11. **`update_holidays_seed.py` stays a script.** Its source is the Cabinet Office, not a
    data source of ours, and it writes a seed. It keeps its place in `refresh-all`.
12. **Two PRs.** PR 1: this spec, `scripts/jma.py`, the helper, the `jma` recipe, the tests
    and the JMA docs — eight scripts to one, the richest source and the only one with a
    fold and a lazy import. PR 2, stacked on it: the other five sources, sixteen scripts to
    five. Type `refactor(scripts)`, labels `chore` and `ingestion`.
13. **The archive keeps the old names.** The plans and specs under `docs/superpowers/` are
    history. Everything else that names a script is rewritten (§7).

## 3. The commands

One table per source: the subcommand, its flags with their defaults, the code it drives.
The flags are today's, listed so the plan and the tests can pin them; a handler's body is
today's script body. Download `--data-dir` defaults are relative paths and load defaults
are under `REPO_ROOT`, both as today.

### 3.1 `jma`

| Subcommand | Flags (default) | Drives |
|---|---|---|
| `download hourly` | `--stations-csv dbt/seeds/jma_stations.csv`, `--data-dir data/jma/hourly`, `--start-year 2016`, `--end-year <this year>`, `--prefecture PD …`, `--station ID …` (new), `--limit N`, `--dry-run`, `--request-interval 5.0` | `JmaStationMasterDownloader` (the master, if absent), `build_plan`, `JmaHourlyDownloader.download` per station-year; ten consecutive failures abort; any failure exits 1 |
| `download normals` | `--years 2020 …` (choices from `VINTAGES`), `--data-dir data/jma/normals`, `--timeout 60` | `JmaNormalsDownloader.download_all` |
| `download msm_surface_forecast` | `--start-date 2022-04-01` (`DEFAULT_BACKFILL_START`), `--end-date <JST today + 1>`, `--data-dir data/jma/msm_surface_forecast`, `--force`, `--keep-grib` | `load_stations` on the two seeds under `REPO_ROOT`, `MsmDownloader.download_range` (imported in the handler) |
| `download stations` | `--dest dbt/seeds/jma_stations.csv` | `JmaStationMasterDownloader(staffed_only=True, jepx_areas_only=True).download(force=True)` |
| `load hourly` | `--schema conf/schemas/jma_hourly_staffed.yaml`, `--data data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv`, `--table pma_raw.jma_hourly_staffed` | `JmaHourlyCsvLoader` |
| `load normals` | `jma_normal_surface_daily.yaml`, `data/jma/normals`, `pma_raw.jma_normal_surface_daily` | `JmaNormalsCsvLoader` |
| `load msm_surface_forecast` | `jma_msm_surface_forecast.yaml`, `data/jma/msm_surface_forecast/csv`, `pma_raw.jma_msm_surface_forecast` | `MsmForecastCsvLoader` |

### 3.2 `jepx`

| Subcommand | Flags (default) | Drives |
|---|---|---|
| `download spot` | `--data-dir data/jepx/spot`, `--force-all` | `JepxSpotDownloader.download` per fiscal year, `EARLIEST_FISCAL_YEAR` to the current one, the last two forced |
| `load spot` | `jepx_spot.yaml`, `data/jepx/spot`, `pma_raw.jepx_spot` | `CsvLoader` |

### 3.3 `occto`

| Subcommand | Flags (default) | Drives |
|---|---|---|
| `download demand_forecast_dad` / `download area_reserve_rate_dad` | `--data-dir data/occto` | `OcctoBulkDownloader(data_dir).download(args.dataset)`, one handler for both |
| `load demand_forecast_dad` | `occto_demand_forecast_dad.yaml`, `data/occto/demand_forecast_dad`, `pma_raw.occto_demand_forecast_dad` | `CsvLoader` |
| `load area_reserve_rate_dad` | `occto_area_reserve_rate_dad.yaml`, `data/occto/area_reserve_rate_dad`, `pma_raw.occto_area_reserve_rate_dad` | `CsvLoader` |

### 3.4 `tepco`

| Subcommand | Flags (default) | Drives |
|---|---|---|
| `download area_demand_generation` | `--data-dir data/tepco/area_demand_generation` | `TepcoAreaDownloader.download_all` |
| `download power_usage` | `--data-dir data/tepco/power_usage`, `--force-yearly` | `TepcoPowerUsageDownloader.download_all(force_yearly=…)` |
| `load area_demand_generation` | `tepco_area_demand_generation_actual.yaml`, `data/tepco/area_demand_generation/csv`, `pma_raw.tepco_area_demand_generation_actual` | `TepcoAreaCsvLoader` |
| `load power_usage` | `tepco_power_usage_hourly.yaml`, `data/tepco/power_usage/csv`, `pma_raw.tepco_power_usage_hourly` | `TepcoPowerUsageCsvLoader` |

### 3.5 `kansai`

| Subcommand | Flags (default) | Drives |
|---|---|---|
| `download area_demand_generation` | `--data-dir data/kansai/area_demand_generation` | `KansaiAreaDownloader.download_all` |
| `download power_usage` | `--data-dir data/kansai/power_usage` | `KansaiPowerUsageDownloader.download_all` |
| `load area_demand_generation` | `kansai_area_demand_generation_actual.yaml`, `data/kansai/area_demand_generation/csv`, `pma_raw.kansai_area_demand_generation_actual` | `KansaiAreaCsvLoader` |
| `load power_usage` | `kansai_power_usage_hourly.yaml`, `data/kansai/power_usage/csv`, `pma_raw.kansai_power_usage_hourly` | `KansaiPowerUsageCsvLoader` |

### 3.6 `estat`

| Subcommand | Flags (default) | Drives |
|---|---|---|
| `download census_population_mesh` | `--years 2015 2020 …` (choices from `VINTAGES`), `--data-dir data/estat/census_population_mesh`, `--force` | `EstatCensusMeshDownloader.download_all(years, force)` |
| `load census_population_mesh` | `estat_census_population_mesh.yaml`, `data/estat/census_population_mesh`, `pma_raw.estat_census_population_mesh` | `EstatCensusMeshCsvLoader` |

## 4. Code

### 4.1 The shape of a source script

```python
"""JMA: download the hourly observations, the normals, the MSM forecast and the station
master; load the first three into pma_raw."""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, load_from_args
from power_market_analytics.ingestion.jma.hourly import SCRAPE_ELEMENTS, JmaHourlyDownloader
...  # every other import at the top, as today; msm.download is the one exception

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_CONSECUTIVE_FAILURES = 10


def build_plan(...):  # verbatim from download_jma_hourly_all.py, plus `stations`
    ...


def download_hourly(args: argparse.Namespace) -> None:
    """<today's download_jma_hourly_all.py docstring>"""
    ...


def download_msm_surface_forecast(args: argparse.Namespace) -> None:
    """<today's download_jma_msm_surface_forecast.py docstring>"""
    # Imported here, not at the top: msm.download reaches eccodes through msm.grib,
    # and no other jma subcommand needs it — the loader least of all.
    from power_market_analytics.ingestion.msm.download import MsmDownloader
    ...


def load_normals(args: argparse.Namespace) -> None:
    """<today's load_jma_normals.py docstring>"""
    load_from_args(args, JmaNormalsCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jma", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    download = verbs.add_parser("download", help="Fetch a dataset's files from JMA")
    datasets = download.add_subparsers(dest="dataset", required=True)
    hourly = datasets.add_parser(
        "hourly",
        help="Hourly observations of every staffed station, one file per station-year",
        description=download_hourly.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    hourly.add_argument("--stations-csv", ...)
    ...
    hourly.set_defaults(run=download_hourly)
    ...
    load = verbs.add_parser("load", help="Load a dataset into pma_raw (full reload; devcontainer)")
    datasets = load.add_subparsers(dest="dataset", required=True)
    normals = datasets.add_parser("normals", help=..., description=load_normals.__doc__, ...)
    add_load_arguments(
        normals,
        schema=REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml",
        data=REPO_ROOT / "data/jma/normals",
        table="pma_raw.jma_normal_surface_daily",
        data_help="Downloader root, a single daily file or a glob; the manifest is read two levels up.",
    )
    normals.set_defaults(run=load_normals)
    ...
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

Handlers return `None`; `download_hourly` raises `SystemExit(1)` on failures as today. The
dataset subparsers are added in the order of §3's tables, which is the order `--help`
lists them. `build_parser` is a function so a test can read the tree without running it.

### 4.2 `power_market_analytics/ingestion/cli.py`

```python
"""The argparse plumbing the six source scripts share."""

def add_load_arguments(
    parser: argparse.ArgumentParser,
    *,
    schema: Path,
    data: Path,
    table: str,
    data_help: str = "A file, a directory of files or a glob pattern to load.",
) -> None:
    """Add --schema, --data and --table with the dataset's defaults."""

def load_from_args(args: argparse.Namespace, loader_cls: type[CsvLoader]) -> int:
    """Read the contract, run the loader, log and return the row count."""
    schema = CsvTableSchema.from_yaml(args.schema)
    n_rows = loader_cls(schema=schema, filepath=args.data, table=args.table).load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)
    return n_rows
```

It imports `ingestion.loader` only, so it costs a load nothing it did not already pay.

## 5. Justfile

```
[doc("JMA: just jma download hourly|normals|msm_surface_forecast|stations … and just jma load hourly|normals|msm_surface_forecast …, inside the devcontainer; `just jma download -h` lists the flags")]
jma *args:
    @just python scripts/jma.py "$@"
```

One such recipe per source, after `python` and before `dbt`. The `python` recipe's `[doc]`
example becomes `just python scripts/demand_backtest.py`. `refresh-all`:

```
    just jepx download spot
    just python scripts/update_holidays_seed.py
    just jepx load spot

    just jma download stations
    just jma download hourly
    just jma load hourly

    just jma download normals
    just jma load normals

    just occto download demand_forecast_dad
    just occto download area_reserve_rate_dad
    just occto load demand_forecast_dad
    just occto load area_reserve_rate_dad

    just tepco download area_demand_generation
    just tepco load area_demand_generation
    just tepco download power_usage
    just tepco load power_usage

    just kansai download area_demand_generation
    just kansai load area_demand_generation
    just kansai download power_usage
    just kansai load power_usage

    just estat download census_population_mesh
    just estat load census_population_mesh

    just jma download msm_surface_forecast
    just jma load msm_surface_forecast

    just dbt build
```

PR 1 changes the JMA lines and leaves the rest as `just python scripts/…`; PR 2 finishes it.

## 6. Tests (pytest, the 100 % gate)

The seam is unchanged: `import_script("jma")`, the class swapped in the script's namespace,
`main([...])` with the verb and the dataset in front of today's argv. The assertions stay.

PR 1:

- `tests/test_jma_scripts.py` keeps `TestBuildPlan`, `TestDownloadJmaHourlyAll` (now
  `download hourly`) and `TestUpdateJmaStationsSeed` (now `download stations`), and gains
  the normals download tests from `test_jma_normals_scripts.py` and the MSM download tests
  from `test_msm_scripts.py`. Those two files go; `TestDefaultEndDate` moves to
  `test_msm.py`, where the vintage module's tests belong. `TestDownloadJmaHourly`, the
  per-station script's five tests, is replaced by two on `download hourly --station`: the
  plan holds that station's years only, and an unknown station raises before any request.
- `tests/test_load_scripts.py`'s parametrized table gains the script and the dataset per
  row, `("jma", "hourly", "JmaHourlyCsvLoader", …)` and so on, so the three JMA loads run
  the three generic tests: the defaults land, the overrides land, a missing contract fails
  before loading. `TestLoadJmaHourly` and the `FORMATS` test go.
- `tests/test_ingestion_cli.py`, new: `add_load_arguments` and `load_from_args` with a
  recording loader — the defaults, the overrides, the returned count.
- New in `test_jma_scripts.py`: the vocabulary, pinned through the parser's own message and
  not its private attributes — `main(["download", "nope"])` exits 2 and the invalid-choice
  error on stderr names exactly `hourly`, `normals`, `msm_surface_forecast`, `stations`, in
  that order, and `main(["load", "nope"])` the first three; `main([])` and
  `main(["download"])` exit 2 without touching a class; `-h` at each of the three levels
  exits 0.
- The eccodes property: with `sys.modules["eccodes"]` set to `None` and the `msm.download`
  and `msm.grib` entries removed (monkeypatch, so all three are restored after),
  `import_script("jma")` succeeds, and, as the control that the block is live, importing
  `power_market_analytics.ingestion.msm.grib` under it raises `ImportError`. Move the
  `MsmDownloader` import to the top of the script and the test fails.
- The MSM download tests patch `MsmDownloader` on `power_market_analytics.ingestion.msm.download`,
  which the handler binds at call time, and `load_stations` and `default_end_date` on the
  script as today.

PR 2: `tests/test_download_scripts.py` and `tests/test_kansai_scripts.py` change stems and
argv; `test_load_scripts.py`'s table gains the eight other rows; each of the five scripts
gets its vocabulary test and its exit-2 test; `test_repo_root_constant_points_at_the_checkout`
runs over the six scripts.

Coverage stays at 100 %: every handler is reached through `main`, and the six
`if __name__ == "__main__":` lines are the excluded ones, as today.

## 7. Docs

The rule: where a document tells the reader to run something, the command is `just <source>
<verb> <dataset> [flags]`; where it names the code, `scripts/<source>.py`. Every mention
outside `docs/superpowers/` is rewritten. Today the twenty-four names are mentioned in 26
tracked files outside the archive.

PR 1, the JMA mentions: `CLAUDE.md` (the Commands bullets for JMA hourly, JMA normals and
MSM, the Architecture bullets for the same three, the `refresh-all` bullet), `justfile`,
`docs/Development.md`, `docs/JMA-Weather-Data-Retrieval.md` (the `--elements` example
becomes the Python call above it), `docs/JMA-MSM-GPV-Retrieval.md`,
`docs/JMA-Climatological-Normals-Retrieval.md`, `dbt/models/raw/jma.yml`,
`dbt/models/curated/dim_jma_station.yml`, `conf/schemas/jma_msm_surface_forecast.yaml`,
`conf/schemas/jma_normal_surface_daily.yaml`, the docstrings in `ingestion/msm/vintage.py`
and `ingestion/jma/normals.py`, and `tests/test_jma_loader.py`'s docstring. PR 2: the
remaining `CLAUDE.md` bullets, `docs/OCCTO-Demand-Forecast-Retrieval.md`, the two TEPCO and
the two Kansai retrieval docs, `docs/eStat-Census-Population-Mesh-Retrieval.md`,
`dbt/models/raw/{jepx,tepco,kansai,estat}.yml`, `conf/schemas/estat_census_population_mesh.yaml`.

The sweep is checked, not grepped for the names we remember: every script name a tracked
file outside the archive mentions, with or without the `scripts/` prefix, must exist —

```bash
git ls-files -- . ':!docs/superpowers' \
  | xargs grep -IhoE "\b(download|load|update)_[a-z_]+\.py\b|scripts/[a-z_]+\.py" \
  | sed 's#^scripts/##' | sort -u \
  | while read -r f; do [ -f "scripts/$f" ] || echo "stale: $f"; done
```

Empty output is the pass (today it checks 34 names, the modeling scripts among them, and
passes), and it catches a stale mention of any script, not only of the twenty-four. The
`\b` matters: without it `test_load_scripts.py` reads as a mention of `load_scripts.py`. It goes on each PR's Checks line next
to `just docs-links`. This spec gets its row in `docs/superpowers/README.md`; the plan's
link is added when the plan exists.

## 8. Effect on what exists

| What existed | After |
|---|---|
| 24 scripts: 12 download, 11 load, the station seed | 6, one per source; PR 1 replaces the 8 JMA ones, PR 2 the other 16 |
| `download_jma_hourly.py` with `--elements` and `--force-all` | Gone. `jma download hourly --station ID …` covers one station; other element sets through `JmaHourlyDownloader.download`; a file to refetch is deleted |
| `load_jma_hourly.py --data-dir / --schema-dir`, `FORMATS` | `--schema / --data / --table`, the same defaults once resolved |
| OCCTO scripts named `demand_forecast` / `area_reserve_rate` | Datasets `demand_forecast_dad` / `area_reserve_rate_dad`, the downloader's keys |
| `just refresh-all` | The same 24 steps in the same order, then `dbt build`; the spellings change |
| Raw tables, data directories, contracts, defaults | Unchanged: the same loader gets the same schema, path and table, the same downloader the same arguments |
| `tests/test_jma_normals_scripts.py`, `tests/test_msm_scripts.py` | Folded into `test_jma_scripts.py`, `test_load_scripts.py` and `test_msm.py`; removed |
| `.claude/settings.json` | Unchanged |
| The plans and specs under `docs/superpowers/` | Unchanged; they name the old scripts |
| `update_holidays_seed.py` and the nine modeling and tooling scripts | Unchanged |

## 9. Verification before each PR

PR 1, host-side: `just test` (100 %), `just lint`, `just mypy`, `just docs-links`, `just dbt
parse` (YAML descriptions change), the stale-name check of §7. In the devcontainer, each new
command once, against the numbers on `main`, read before and after with `just dbt show
--inline "select count(*) from pma_raw.<table>" --limit 1`:

| Command | Expected |
|---|---|
| `just jma download hourly --dry-run` | The planned and to-fetch counts of `download_jma_hourly_all.py --dry-run` on `main` |
| `just jma download hourly --station s47662 --dry-run` | A plan of one station, 2016 to this year |
| `just jma download hourly --station nope --dry-run` | `ValueError` naming the station, no request |
| `just jma download normals`, then `just jma load normals` | 157 daily files; 152,604 rows, as PR #231 |
| `just jma load hourly` | `pma_raw.jma_hourly_staffed`'s row count on `main`, about 50 s warm |
| `just jma load msm_surface_forecast` | `pma_raw.jma_msm_surface_forecast`'s row count on `main` |
| `just jma download msm_surface_forecast --start-date D --end-date D`, D a cached day | "Extracted 1 delivery day(s)", nothing fetched |
| `just jma download stations` | The seed rewritten with an empty `git diff --stat dbt/seeds/jma_stations.csv`, about 5 min |
| `just python -c "import sys; sys.modules['eccodes'] = None; import runpy; sys.argv = ['jma', 'load', 'msm_surface_forecast', '-h']; runpy.run_path('scripts/jma.py', run_name='__main__')"` | The help text, exit 0 |
| `just jma`, `just jma download`, `just jma download hourly -h` | Usage listing the verbs; the datasets; the flags |

PR 2 does the same for the sixteen commands: each download with its defaults (all cheap;
e-Stat's archives are cached), each load against `main`'s count. The counts before and after
go into each PR's Evidence.
