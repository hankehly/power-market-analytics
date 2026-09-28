# Source Commands, PR 2 (JEPX, OCCTO, TEPCO, Kansai, e-Stat) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sixteen remaining ingestion scripts with five source commands — `scripts/jepx.py`, `occto.py`, `tepco.py`, `kansai.py`, `estat.py` — each with `download` and `load` subcommands and one subcommand per dataset, completing the spec after PR 1 (#232, the `jma` command).

**Architecture:** The shape of `scripts/jma.py` (PR 1): a handler per dataset whose body is today's script body, `build_parser()` with nested `add_subparsers` registered through `power_market_analytics.ingestion.cli.add_subcommand`, the loads through `add_load_arguments` / `load_from_args`, `main(argv)` dispatching to `args.run`. Tests keep their seam: `import_script("<source>")`, the class swapped in the script's namespace, `main([...])` with `download <dataset>` or `load <dataset>` in front of today's argv.

**Tech Stack:** Python 3.13, argparse, loguru, pytest with `tests.support.import_script`, `uv`, `just`; the loads run on the devcontainer's Spark session.

**Spec:** `docs/superpowers/specs/2026-09-28-source-commands-design.md` (§3.2–§3.6 are these five commands). PR 1's plan, `docs/superpowers/plans/2026-09-28-source-commands-jma.md`, is the worked example of every step here.

## Global Constraints

- Work in the worktree `.claude/worktrees/chore+source-commands-2`, branch `chore/source-commands-2`, stacked on `chore/source-commands` (PR #232); the main checkout is never touched. Run every command from the worktree path. The worktree guard refuses compound commands that mention `git` inside a heredoc or a pipeline it cannot follow, and loops over a variable: write files with the Write/Edit tools, keep `git` commands plain, spell file lists out.
- Commits: Conventional Commits, `refactor(scripts): …` / `build(justfile): …` / `docs(scripts): …`; every message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Python: NumPy-style docstrings; ruff line length 100 (a PostToolUse hook runs `ruff format` + `ruff check --fix` on every edited `.py` — **it strips unused imports**, so add an import in the same edit as its first use); `just lint`, `just mypy` clean; the coverage gate is 100 % over `power_market_analytics/` + `scripts/` (`just test`).
- Tests never touch the network or Spark: downloader and loader classes are swapped for fakes; scripts are driven through `main(argv)`.
- Names are the spec's: `prog=<source>`; datasets `spot`; `demand_forecast_dad`, `area_reserve_rate_dad`; `area_demand_generation`, `power_usage` (TEPCO and Kansai); `census_population_mesh`. A dataset's name is its directory under `data/<source>/` (spec decision 2), and for OCCTO the key `OcctoBulkDownloader.download()` takes.
- Every flag and default of the sixteen scripts survives (spec §3.2–§3.6): download `--data-dir` defaults relative, load defaults under `REPO_ROOT`, the same help texts but for the generic `--data` line, which is now `add_load_arguments`' default.
- Every import at the top of each script; no lazy import is needed (none of these sources touches eccodes).
- Writing style (docs, YAML descriptions, PR body): plain short sentences; exact names and numbers. The archive under `docs/superpowers/` keeps the old script names (spec decision 13).
- Host-side gates: `just test`, `just lint`, `just mypy`, `just docs-links`, `(cd dbt && DBT_THRIFT_HOST=localhost uv run --no-sync dbt parse)`. In-container runs go through the main checkout's compose project with this worktree as the working directory (Task 5; `data/<source>` are symlinks into the main checkout's data), run by the main session as background tasks.

## Review Focus

1. `occto download` with the dataset name misspelt (`demand_forecast`, the old script's name): exit 2 with the two `_dad` names listed, no request. Pinned in Task 1 (`TestParserTrees.test_download_lists_exactly_the_datasets[occto]`).
2. `jepx download spot` with `current_fiscal_year` pinned: the two newest fiscal years forced, the rest cached — the old script's policy, now behind a verb and a dataset. Pinned in Task 1 (`TestDownloadJepxSpot`, carried over).
3. `tepco download power_usage --force-yearly`: the flag reaches `download_all(force_yearly=True)`; `tepco download area_demand_generation --force-yearly` is refused (exit 2), since only the power-usage downloader has yearly files. Pinned in Task 1 (`TestDownloadTepcoPowerUsage` carried over; `TestParserTrees.test_a_flag_of_another_dataset_is_refused`).
4. `estat download census_population_mesh --years 2010`: exit 2 from the parser's `choices`, no request. Pinned in Task 1 (`TestDownloadEstatCensusPopulationMesh.test_unconfigured_year_is_rejected_by_the_parser`, carried over).
5. `kansai load area_demand_generation` reads the Kansai contract, not TEPCO's: the grain and the `windows-31j` encoding come from `conf/schemas/kansai_area_demand_generation_actual.yaml`. Pinned in Task 1 (`TestLoadCommands[kansai load area_demand_generation]::test_defaults` through `CONTRACT_GRAINS`, and `TestKansaiContract`, unchanged).

## File structure

| File | Responsibility |
|---|---|
| `scripts/jepx.py`, `scripts/occto.py`, `scripts/tepco.py`, `scripts/kansai.py`, `scripts/estat.py` (new) | the five source commands |
| `scripts/download_jepx_spot.py`, `load_jepx_spot.py`, `download_occto_demand_forecast.py`, `download_occto_area_reserve_rate.py`, `load_occto_demand_forecast.py`, `load_occto_area_reserve_rate.py`, `download_tepco_area_demand_generation.py`, `load_tepco_area_demand_generation.py`, `download_tepco_power_usage.py`, `load_tepco_power_usage.py`, `download_kansai_area_demand_generation.py`, `load_kansai_area_demand_generation.py`, `download_kansai_power_usage.py`, `load_kansai_power_usage.py`, `download_estat_census_population_mesh.py`, `load_estat_census_population_mesh.py` | deleted |
| `tests/test_download_scripts.py` | the five scripts' `download` subcommands and their parser trees |
| `tests/test_kansai_scripts.py` | the Kansai `download area_demand_generation` wiring and the Kansai contract; its load test moves to the table |
| `tests/test_load_scripts.py` | every `load` subcommand of the six scripts, table-driven |
| `justfile` | five recipes; `refresh-all` through them; the comment and the doc |
| `CLAUDE.md`, `docs/Development.md`, `docs/OCCTO-Demand-Forecast-Retrieval.md`, `docs/TEPCO-Area-Demand-Generation-Retrieval.md`, `docs/TEPCO-Power-Usage-Retrieval.md`, `docs/Kansai-Area-Demand-Generation-Retrieval.md`, `docs/Kansai-Power-Usage-Retrieval.md`, `docs/eStat-Census-Population-Mesh-Retrieval.md`, `dbt/models/raw/{jepx,occto,tepco,kansai,estat}.yml`, `conf/schemas/estat_census_population_mesh.yaml`, `docs/superpowers/README.md` | the mentions rewritten; the design-history row gains this plan |

---

### Task 1: The five scripts with their tests; the sixteen scripts go

**Files:**
- Create: `scripts/jepx.py`, `scripts/occto.py`, `scripts/tepco.py`, `scripts/kansai.py`, `scripts/estat.py`
- Modify: `tests/test_download_scripts.py`, `tests/test_kansai_scripts.py`, `tests/test_load_scripts.py`
- Delete: the sixteen scripts listed in the file structure

**Interfaces:**
- Consumes: `add_subcommand(subparsers, name, run, *, help)`, `add_load_arguments(parser, *, schema, data, table, data_help=…)`, `load_from_args(args, loader_cls)` from `power_market_analytics.ingestion.cli`; `JepxSpotDownloader` (`EARLIEST_FISCAL_YEAR`, `download(fiscal_year, force)`), `current_fiscal_year()`, `CsvLoader`; `OcctoBulkDownloader(data_dir).download(dataset)`; `TepcoAreaDownloader` / `KansaiAreaDownloader` (`download_all()`, `csv_dir`), `TepcoPowerUsageDownloader.download_all(force_yearly)`, `KansaiPowerUsageDownloader.download_all()`; `TepcoAreaCsvLoader`, `TepcoPowerUsageCsvLoader`, `KansaiAreaCsvLoader`, `KansaiPowerUsageCsvLoader`; `EstatCensusMeshDownloader.download_all(years, force)`, `EstatCensusMeshCsvLoader`, `estat.vintages.VINTAGES` (`census_year`).
- Produces: five scripts with the names the tests patch on each — `jepx`: `JepxSpotDownloader`, `current_fiscal_year`, `CsvLoader`; `occto`: `OcctoBulkDownloader`, `CsvLoader`; `tepco`: `TepcoAreaDownloader`, `TepcoPowerUsageDownloader`, `TepcoAreaCsvLoader`, `TepcoPowerUsageCsvLoader`; `kansai`: `KansaiAreaDownloader`, `KansaiPowerUsageDownloader`, `KansaiAreaCsvLoader`, `KansaiPowerUsageCsvLoader`; `estat`: `EstatCensusMeshDownloader`, `EstatCensusMeshCsvLoader`, `CONFIGURED_YEARS` — each with `REPO_ROOT`, `build_parser()`, `main(argv=None)`.

- [ ] **Step 1: Rewrite `tests/test_download_scripts.py`**

Keep every fake and assertion. Edits:

1. The docstring and imports:

```python
"""CLI wiring of the ``download`` subcommands of ``scripts/jepx.py``, ``occto.py``,
``tepco.py``, ``kansai.py`` (power usage; the area actuals are in ``test_kansai_scripts.py``)
and ``estat.py``, and the five parser trees.

Each handler builds one downloader and drives it; the downloader class is swapped for a
recording fake in the script's namespace, so what is asserted is the argument plumbing
(data dir, dataset key, force policy), not the HTTP work. The ``load`` subcommands are in
``test_load_scripts.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support import import_script
```

2. `TestDownloadJepxSpot`: `import_script("download_jepx_spot")` → `import_script("jepx")`; every `script.main([...])` gains `"download", "spot",` in front (`script.main(["download", "spot", "--data-dir", str(tmp_path)])`, `script.main(["download", "spot"])`, and so on).
3. `_OcctoScriptCase`: `import_script(self.script_name)` → `import_script("occto")`; `module.main(["--data-dir", str(tmp_path)])` → `module.main(["download", self.dataset, "--data-dir", str(tmp_path)])`; `module.main([])` → `module.main(["download", self.dataset])`; the two subclasses drop `script_name` and keep `dataset`.
4. `TestDownloadTepcoAreaDemandGeneration`: `import_script("tepco")`; argv `["download", "area_demand_generation", …]`. `TestDownloadTepcoPowerUsage`: `import_script("tepco")`; argv `["download", "power_usage", …]`.
5. `TestDownloadKansaiPowerUsage`: `import_script("kansai")`; argv `["download", "power_usage", …]`.
6. `TestDownloadEstatCensusPopulationMesh`: `import_script("estat")`; argv `["download", "census_population_mesh", …]`.
7. Append the parser-tree tests:

```python
# --------------------------------------------------------------------------- the parser trees


def choices_in(error: str) -> list[str]:
    """The names argparse lists in an invalid-choice message, in its order."""
    listed = re.search(r"\(choose from (.*)\)", error)
    assert listed is not None, error
    return [name.strip("'") for name in listed.group(1).split(", ")]


#: script → (its download datasets, its load datasets), in --help order.
TREES = {
    "jepx": (["spot"], ["spot"]),
    "occto": (
        ["demand_forecast_dad", "area_reserve_rate_dad"],
        ["demand_forecast_dad", "area_reserve_rate_dad"],
    ),
    "tepco": (["area_demand_generation", "power_usage"], ["area_demand_generation", "power_usage"]),
    "kansai": (["area_demand_generation", "power_usage"], ["area_demand_generation", "power_usage"]),
    "estat": (["census_population_mesh"], ["census_population_mesh"]),
}


@pytest.mark.parametrize("stem", sorted(TREES))
class TestParserTrees:
    def test_download_lists_exactly_the_datasets(self, stem, capsys):
        script = import_script(stem)

        with pytest.raises(SystemExit) as exc:
            script.main(["download", "nope"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == TREES[stem][0]

    def test_load_lists_exactly_the_datasets(self, stem, capsys):
        script = import_script(stem)

        with pytest.raises(SystemExit) as exc:
            script.main(["load", "nope"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == TREES[stem][1]

    @pytest.mark.parametrize("argv", [[], ["download"], ["load"]])
    def test_a_missing_verb_or_dataset_exits_2(self, stem, argv):
        script = import_script(stem)

        with pytest.raises(SystemExit) as exc:
            script.main(argv)

        assert exc.value.code == 2

    def test_help_at_every_level_exits_0(self, stem, capsys):
        script = import_script(stem)
        downloads, loads = TREES[stem]
        for argv in ([], ["download"], ["load"], ["download", downloads[0]], ["load", loads[0]]):
            with pytest.raises(SystemExit) as exc:
                script.main([*argv, "-h"])
            assert exc.value.code == 0, argv
            assert capsys.readouterr().out.startswith(f"usage: {stem}"), argv


def test_a_flag_of_another_dataset_is_refused(capsys):
    # --force-yearly belongs to tepco download power_usage alone: the area actuals have no
    # yearly files, so the flag is not shared across the source's datasets.
    script = import_script("tepco")

    with pytest.raises(SystemExit) as exc:
        script.main(["download", "area_demand_generation", "--force-yearly"])

    assert exc.value.code == 2
    assert "unrecognized arguments: --force-yearly" in capsys.readouterr().err
```

- [ ] **Step 2: Update `tests/test_kansai_scripts.py`**

Replace the module docstring, the imports and the local `import_script` helper with:

```python
"""CLI wiring of ``scripts/kansai.py download area_demand_generation``, and the Kansai
area-actuals contract. The ``load`` subcommands are in ``test_load_scripts.py`` with every
other load; ``download power_usage`` is in ``test_download_scripts.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from power_market_analytics.ingestion.loader import CsvTableSchema
from tests.support import REPO_ROOT, import_script
```

In `TestDownloadScript`: `import_script("download_kansai_area_demand_generation")` → `import_script("kansai")`; `script.main(["--data-dir", str(tmp_path)])` → `script.main(["download", "area_demand_generation", "--data-dir", str(tmp_path)])`; `script.main([])` → `script.main(["download", "area_demand_generation"])`. Delete `class TestLoadScript` (its two assertions are the table's `test_defaults` and `test_overrides` for `kansai load area_demand_generation`, Step 3). `TestKansaiContract` stays as it is.

- [ ] **Step 3: Update `tests/test_load_scripts.py`**

The docstring becomes:

```python
"""CLI wiring tests for every ``load`` subcommand of the six source scripts.

Each is exercised through ``main(argv)`` with the loader class swapped for a fake that
records its constructor arguments, so the tests pin the default contract / data path /
table literals and the CLI overrides without touching Spark.
"""
```

`LOAD_COMMANDS` becomes (the three `jma` rows unchanged at the end):

```python
LOAD_COMMANDS = [
    (
        "jepx",
        ["load", "spot"],
        "CsvLoader",
        "conf/schemas/jepx_spot.yaml",
        "data/jepx/spot",
        "pma_raw.jepx_spot",
    ),
    (
        "occto",
        ["load", "area_reserve_rate_dad"],
        "CsvLoader",
        "conf/schemas/occto_area_reserve_rate_dad.yaml",
        "data/occto/area_reserve_rate_dad",
        "pma_raw.occto_area_reserve_rate_dad",
    ),
    (
        "occto",
        ["load", "demand_forecast_dad"],
        "CsvLoader",
        "conf/schemas/occto_demand_forecast_dad.yaml",
        "data/occto/demand_forecast_dad",
        "pma_raw.occto_demand_forecast_dad",
    ),
    (
        "tepco",
        ["load", "area_demand_generation"],
        "TepcoAreaCsvLoader",
        "conf/schemas/tepco_area_demand_generation_actual.yaml",
        "data/tepco/area_demand_generation/csv",
        "pma_raw.tepco_area_demand_generation_actual",
    ),
    (
        "tepco",
        ["load", "power_usage"],
        "TepcoPowerUsageCsvLoader",
        "conf/schemas/tepco_power_usage_hourly.yaml",
        "data/tepco/power_usage/csv",
        "pma_raw.tepco_power_usage_hourly",
    ),
    (
        "kansai",
        ["load", "area_demand_generation"],
        "KansaiAreaCsvLoader",
        "conf/schemas/kansai_area_demand_generation_actual.yaml",
        "data/kansai/area_demand_generation/csv",
        "pma_raw.kansai_area_demand_generation_actual",
    ),
    (
        "kansai",
        ["load", "power_usage"],
        "KansaiPowerUsageCsvLoader",
        "conf/schemas/kansai_power_usage_hourly.yaml",
        "data/kansai/power_usage/csv",
        "pma_raw.kansai_power_usage_hourly",
    ),
    (
        "estat",
        ["load", "census_population_mesh"],
        "EstatCensusMeshCsvLoader",
        "conf/schemas/estat_census_population_mesh.yaml",
        "data/estat/census_population_mesh",
        "pma_raw.estat_census_population_mesh",
    ),
    (
        "jma",
        ["load", "hourly"],
        "JmaHourlyCsvLoader",
        "conf/schemas/jma_hourly_staffed.yaml",
        "data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv",
        "pma_raw.jma_hourly_staffed",
    ),
    (
        "jma",
        ["load", "normals"],
        "JmaNormalsCsvLoader",
        "conf/schemas/jma_normal_surface_daily.yaml",
        "data/jma/normals",
        "pma_raw.jma_normal_surface_daily",
    ),
    (
        "jma",
        ["load", "msm_surface_forecast"],
        "MsmForecastCsvLoader",
        "conf/schemas/jma_msm_surface_forecast.yaml",
        "data/jma/msm_surface_forecast/csv",
        "pma_raw.jma_msm_surface_forecast",
    ),
]
```

`CONTRACT_GRAINS` gains `"conf/schemas/kansai_area_demand_generation_actual.yaml": ["target_date", "time_code"]`. The three test methods are unchanged. The last test becomes:

```python
def test_repo_root_constant_points_at_the_checkout():
    for stem in ("jepx", "occto", "tepco", "kansai", "estat", "jma"):
        assert import_script(stem).REPO_ROOT == REPO_ROOT
        assert isinstance(import_script(stem).REPO_ROOT, Path)
```

- [ ] **Step 4: Run the changed tests to verify they fail**

Run: `uv run --no-sync pytest tests/test_download_scripts.py tests/test_kansai_scripts.py tests/test_load_scripts.py -q -p no:cacheprovider --no-cov`
Expected: every test of the five new stems FAILS with `FileNotFoundError: … scripts/<stem>.py`; the `jma` rows and `TestKansaiContract` pass.

- [ ] **Step 5: Write the five scripts**

`scripts/jepx.py`:

```python
"""JEPX: download the spot market CSVs and load them into pma_raw.

    just jepx download spot [--data-dir DIR] [--force-all]
    just jepx load spot [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The load needs the devcontainer's Spark
session; the download runs host-side too (``uv run python scripts/jepx.py download spot``).
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.jepx import JepxSpotDownloader, current_fiscal_year
from power_market_analytics.ingestion.loader import CsvLoader

REPO_ROOT = Path(__file__).resolve().parents[1]


def download_spot(args: argparse.Namespace) -> None:
    """Download JEPX spot price CSVs for every available fiscal year.

    Past fiscal years are immutable and served from the local cache; the two
    most recent fiscal years are always re-downloaded because JEPX appends rows
    to the current file daily (and a file cached mid-year would otherwise stay
    partial after the fiscal year rolls over).
    """
    downloader = JepxSpotDownloader(data_dir=args.data_dir)
    latest = current_fiscal_year()
    for fiscal_year in range(downloader.EARLIEST_FISCAL_YEAR, latest + 1):
        force = args.force_all or fiscal_year >= latest - 1
        downloader.download(fiscal_year, force=force)
    logger.info("Downloaded fiscal years {}..{}", downloader.EARLIEST_FISCAL_YEAR, latest)


def load_spot(args: argparse.Namespace) -> None:
    """Load the downloaded JEPX spot price CSVs into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jepx load spot``.
    """
    load_from_args(args, CsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``jepx`` parser: ``download`` and ``load``, the ``spot`` dataset under each."""
    parser = argparse.ArgumentParser(
        prog="jepx", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    download = verbs.add_parser("download", help="Fetch a dataset's files from JEPX")
    downloads = download.add_subparsers(dest="dataset", required=True)
    spot = add_subcommand(
        downloads, "spot", download_spot, help="The spot market CSVs, one per fiscal year"
    )
    spot.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jepx/spot"),
        help="Directory where CSV files are stored.",
    )
    spot.add_argument(
        "--force-all",
        action="store_true",
        help="Re-download every fiscal year, ignoring the cache.",
    )

    load = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    )
    loads = load.add_subparsers(dest="dataset", required=True)
    load_spot_parser = add_subcommand(loads, "spot", load_spot, help="The spot CSVs → pma_raw.jepx_spot")
    add_load_arguments(
        load_spot_parser,
        schema=REPO_ROOT / "conf/schemas/jepx_spot.yaml",
        data=REPO_ROOT / "data/jepx/spot",
        table="pma_raw.jepx_spot",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

`scripts/occto.py`:

```python
"""OCCTO: download the two 翌々日 datasets from the 情報ダウンロード portal and load them into
pma_raw.

    just occto download demand_forecast_dad|area_reserve_rate_dad [--data-dir DIR]
    just occto load demand_forecast_dad|area_reserve_rate_dad [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark session;
the downloads run host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.loader import CsvLoader
from power_market_analytics.ingestion.occto import OcctoBulkDownloader

REPO_ROOT = Path(__file__).resolve().parents[1]


def download(args: argparse.Namespace) -> None:
    """Download an OCCTO day-after-next (翌々日) dataset's CSV: the whole history, every time.

    ``demand_forecast_dad``, the demand forecast: OCCTO serves the whole dataset
    (2024-03-13 onward, ~700 KB) in one file, so a refresh is a single
    three-request handshake.

    ``area_reserve_rate_dad``, the エリア・広域ブロック情報 (reserve rate) from
    2025-04-01: the bulk-download screen caps one download at 150,000 rows and
    this dataset has 480 rows per day (48 half-hours × 10 areas), so the
    downloader fetches it in 300-day windows and concatenates them into one file
    (~20 MB per year of history).
    """
    downloader = OcctoBulkDownloader(data_dir=args.data_dir)
    path = downloader.download(args.dataset)
    logger.info("Downloaded OCCTO {} to {}", args.dataset, path)


def load(args: argparse.Namespace) -> None:
    """Load a downloaded OCCTO 翌々日 CSV into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just occto load demand_forecast_dad`` or
    ``just occto load area_reserve_rate_dad``.
    """
    load_from_args(args, CsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``occto`` parser: ``download`` and ``load``, the two datasets under each."""
    parser = argparse.ArgumentParser(
        prog="occto", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's CSV from the OCCTO 情報ダウンロード portal"
    ).add_subparsers(dest="dataset", required=True)
    for name, summary in (
        ("demand_forecast_dad", "The 翌々日 demand forecast, one CSV of the whole history"),
        ("area_reserve_rate_dad", "The 翌々日 エリア・広域ブロック情報 reserve rate, in 300-day windows"),
    ):
        dataset = add_subcommand(downloads, name, download, help=summary)
        dataset.add_argument(
            "--data-dir",
            type=Path,
            default=Path("data/occto"),
            help="Root directory where OCCTO CSV files are stored (one subdirectory per dataset).",
        )

    loads = verbs.add_parser(
        "load", help="Load a dataset's CSV into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    for name in ("demand_forecast_dad", "area_reserve_rate_dad"):
        dataset = add_subcommand(loads, name, load, help=f"The {name} CSV → pma_raw.occto_{name}")
        add_load_arguments(
            dataset,
            schema=REPO_ROOT / f"conf/schemas/occto_{name}.yaml",
            data=REPO_ROOT / f"data/occto/{name}",
            table=f"pma_raw.occto_{name}",
        )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

`scripts/tepco.py`:

```python
"""TEPCO: download the two Tokyo-area datasets and load them into pma_raw.

    just tepco download area_demand_generation [--data-dir DIR]
    just tepco download power_usage [--data-dir DIR] [--force-yearly]
    just tepco load area_demand_generation|power_usage [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark session;
the downloads run host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.tso.tepco.area_demand_generation import (
    TepcoAreaCsvLoader,
    TepcoAreaDownloader,
)
from power_market_analytics.ingestion.tso.tepco.power_usage import (
    TepcoPowerUsageCsvLoader,
    TepcoPowerUsageDownloader,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def download_area_demand_generation(args: argparse.Namespace) -> None:
    """Download the TEPCO エリア需要・発電情報 monthly archives and extract the actuals CSVs.

    Always re-downloads every month from 2022-04 to the current month (~53 zips,
    ~5 MB in total): TEPCO revises past days occasionally and refreshes the current
    month's archive daily, and re-fetching everything is the simplest way to stay
    consistent with the published history.
    """
    downloader = TepcoAreaDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} actuals file(s) into {}", len(paths), downloader.csv_dir)


def download_power_usage(args: argparse.Namespace) -> None:
    """Download the TEPCO でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

    Fetches the yearly ``juyo-YYYY.csv`` files (2016 … 2022, immutable — cached
    after the first run unless ``--force-yearly``) and always re-downloads every
    monthly ``YYYYMM_power_usage.zip`` from 2022-04 to the current month (~53
    zips, ~4 MB), extracting the daily ``YYYYMMDD_power_usage.csv`` members next
    to the yearly files under ``csv/``.
    """
    downloader = TepcoPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all(force_yearly=args.force_yearly)
    logger.info("Downloaded {} source file(s) into {}", len(paths), downloader.csv_dir)


def load_area_demand_generation(args: argparse.Namespace) -> None:
    """Load the extracted TEPCO area actuals CSVs into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just tepco load area_demand_generation``.
    """
    load_from_args(args, TepcoAreaCsvLoader)


def load_power_usage(args: argparse.Namespace) -> None:
    """Load the TEPCO でんき予報 hourly 電力使用実績 files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just tepco load power_usage``.
    """
    load_from_args(args, TepcoPowerUsageCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``tepco`` parser: ``download`` and ``load``, the two datasets under each."""
    parser = argparse.ArgumentParser(
        prog="tepco", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's archives from TEPCO and extract them"
    ).add_subparsers(dest="dataset", required=True)
    area = add_subcommand(
        downloads,
        "area_demand_generation",
        download_area_demand_generation,
        help="The エリア需要・発電情報 30-minute actuals, every monthly zip since 2022-04",
    )
    area.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/tepco/area_demand_generation"),
        help="Root directory for TEPCO area files (zip/ archives, csv/ extracted actuals).",
    )
    usage = add_subcommand(
        downloads,
        "power_usage",
        download_power_usage,
        help="The でんき予報 hourly 電力使用実績: yearly files 2016 … 2022, monthly zips since 2022-04",
    )
    usage.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/tepco/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ yearly + extracted daily files).",
    )
    usage.add_argument(
        "--force-yearly",
        action="store_true",
        help="Re-download the yearly juyo-YYYY.csv files even when cached.",
    )

    loads = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    load_area = add_subcommand(
        loads,
        "area_demand_generation",
        load_area_demand_generation,
        help="The extracted actuals → pma_raw.tepco_area_demand_generation_actual",
    )
    add_load_arguments(
        load_area,
        schema=REPO_ROOT / "conf/schemas/tepco_area_demand_generation_actual.yaml",
        data=REPO_ROOT / "data/tepco/area_demand_generation/csv",
        table="pma_raw.tepco_area_demand_generation_actual",
    )
    load_usage = add_subcommand(
        loads,
        "power_usage",
        load_power_usage,
        help="The yearly and daily files → pma_raw.tepco_power_usage_hourly",
    )
    add_load_arguments(
        load_usage,
        schema=REPO_ROOT / "conf/schemas/tepco_power_usage_hourly.yaml",
        data=REPO_ROOT / "data/tepco/power_usage/csv",
        table="pma_raw.tepco_power_usage_hourly",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

`scripts/kansai.py`:

```python
"""Kansai (関西電力送配電): download the two datasets and load them into pma_raw.

    just kansai download area_demand_generation|power_usage [--data-dir DIR]
    just kansai load area_demand_generation|power_usage [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark session;
the downloads run host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.tso.kansai.area_demand_generation import (
    KansaiAreaCsvLoader,
    KansaiAreaDownloader,
)
from power_market_analytics.ingestion.tso.kansai.power_usage import (
    KansaiPowerUsageCsvLoader,
    KansaiPowerUsageDownloader,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def download_area_demand_generation(args: argparse.Namespace) -> None:
    """Download the 関西電力送配電 エリア需給・発電（実績） monthly archives and extract the daily CSVs.

    Always re-downloads every month from 2022-04 to the current month (~53 zips,
    ~2 MB in total): Kansai revises past days occasionally and refreshes the
    current month's archive daily, and re-fetching everything is the simplest way
    to stay consistent with the published history.
    """
    downloader = KansaiAreaDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} actuals file(s) into {}", len(paths), downloader.csv_dir)


def download_power_usage(args: argparse.Namespace) -> None:
    """Download the 関西電力送配電 でんき予報 過去の電力使用実績 history (hourly 電力使用状況).

    Always re-downloads every monthly ``YYYYMM_jisseki.zip`` from 2016-04 to the
    current month (~126 zips, ~8.5 MB) — Kansai revises past days without notice
    — and extracts the daily members (``YYYYMMDD_juyo1_kansai.csv`` through
    2025-11, ``juyo_06_YYYYMMDD.csv`` from 2025-12) under ``csv/``. A settled
    month must hold every day except 2024-03-31, which Kansai never published.
    """
    downloader = KansaiPowerUsageDownloader(data_dir=args.data_dir)
    paths = downloader.download_all()
    logger.info("Extracted {} daily file(s) into {}", len(paths), downloader.csv_dir)


def load_area_demand_generation(args: argparse.Namespace) -> None:
    """Load the extracted Kansai area actuals CSVs into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just kansai load area_demand_generation``.
    """
    load_from_args(args, KansaiAreaCsvLoader)


def load_power_usage(args: argparse.Namespace) -> None:
    """Load the Kansai でんき予報 hourly 電力使用実績 files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just kansai load power_usage``.
    """
    load_from_args(args, KansaiPowerUsageCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``kansai`` parser: ``download`` and ``load``, the two datasets under each."""
    parser = argparse.ArgumentParser(
        prog="kansai", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's archives from 関西電力送配電 and extract them"
    ).add_subparsers(dest="dataset", required=True)
    area = add_subcommand(
        downloads,
        "area_demand_generation",
        download_area_demand_generation,
        help="The エリア需給・発電（実績） 30-minute actuals, every monthly zip since 2022-04",
    )
    area.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/area_demand_generation"),
        help="Root directory for Kansai area files (zip/ archives, csv/ extracted actuals).",
    )
    usage = add_subcommand(
        downloads,
        "power_usage",
        download_power_usage,
        help="The でんき予報 hourly 電力使用実績, every monthly zip since 2016-04",
    )
    usage.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/kansai/power_usage"),
        help="Root directory (zip/ monthly archives, csv/ extracted daily files).",
    )

    loads = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    load_area = add_subcommand(
        loads,
        "area_demand_generation",
        load_area_demand_generation,
        help="The extracted actuals → pma_raw.kansai_area_demand_generation_actual",
    )
    add_load_arguments(
        load_area,
        schema=REPO_ROOT / "conf/schemas/kansai_area_demand_generation_actual.yaml",
        data=REPO_ROOT / "data/kansai/area_demand_generation/csv",
        table="pma_raw.kansai_area_demand_generation_actual",
    )
    load_usage = add_subcommand(
        loads,
        "power_usage",
        load_power_usage,
        help="The daily files → pma_raw.kansai_power_usage_hourly",
    )
    add_load_arguments(
        load_usage,
        schema=REPO_ROOT / "conf/schemas/kansai_power_usage_hourly.yaml",
        data=REPO_ROOT / "data/kansai/power_usage/csv",
        table="pma_raw.kansai_power_usage_hourly",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

`scripts/estat.py`:

```python
"""e-Stat: download the census 500 m population-mesh archives and load them into pma_raw.

    just estat download census_population_mesh [--years YEAR …] [--data-dir DIR] [--force]
    just estat load census_population_mesh [--schema PATH] [--data PATH] [--table NAME]

``-h`` at any level lists what is under it. The load needs the devcontainer's Spark session;
the download runs host-side too.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.estat.download import EstatCensusMeshDownloader
from power_market_analytics.ingestion.estat.load import EstatCensusMeshCsvLoader
from power_market_analytics.ingestion.estat.vintages import VINTAGES

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGURED_YEARS = [vintage.census_year for vintage in VINTAGES]


def download_census_population_mesh(args: argparse.Namespace) -> None:
    """Download the e-Stat census 500 m population-mesh archives and extract the text files.

    For every configured census vintage (power_market_analytics.ingestion.estat.vintages.VINTAGES:
    2015 = T000847, 2020 = T001101 JGD2000) the 第１次地域区画 listing is read
    (151 primary-mesh archives each, ~1 MB apiece), every archive is downloaded
    into ``{data-dir}/{year}/zip/`` unless it is already cached, and its single
    text member is extracted unmodified into ``{data-dir}/{year}/txt/``. Census
    tables never change once published, so a plain rerun only re-reads the listing
    pages; pass ``--force`` to re-download the archives.
    """
    downloader = EstatCensusMeshDownloader(data_dir=args.data_dir)
    paths = downloader.download_all(years=args.years, force=args.force)
    logger.info("Extracted {} text file(s) under {}", len(paths), args.data_dir)


def load_census_population_mesh(args: argparse.Namespace) -> None:
    """Load the extracted e-Stat census population-mesh text files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just estat load census_population_mesh``.
    """
    load_from_args(args, EstatCensusMeshCsvLoader)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``estat`` parser: ``download`` and ``load``, the one dataset under each."""
    parser = argparse.ArgumentParser(
        prog="estat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    downloads = verbs.add_parser(
        "download", help="Fetch a dataset's archives from e-Stat and extract them"
    ).add_subparsers(dest="dataset", required=True)
    mesh = add_subcommand(
        downloads,
        "census_population_mesh",
        download_census_population_mesh,
        help="The census 4次メッシュ population tables, 151 primary-mesh archives per vintage",
    )
    mesh.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_YEARS,
        default=CONFIGURED_YEARS,
        metavar="YEAR",
        help=f"Census years to fetch (configured: {CONFIGURED_YEARS}); defaults to all.",
    )
    mesh.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/estat/census_population_mesh"),
        help="Root directory for the per-year zip/ archives and txt/ extracts.",
    )
    mesh.add_argument(
        "--force",
        action="store_true",
        help="Re-download archives that are already cached.",
    )

    loads = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    ).add_subparsers(dest="dataset", required=True)
    load_mesh = add_subcommand(
        loads,
        "census_population_mesh",
        load_census_population_mesh,
        help="The extracted text files → pma_raw.estat_census_population_mesh",
    )
    add_load_arguments(
        load_mesh,
        schema=REPO_ROOT / "conf/schemas/estat_census_population_mesh.yaml",
        data=REPO_ROOT / "data/estat/census_population_mesh",
        table="pma_raw.estat_census_population_mesh",
        data_help=(
            "Downloader root ({year}/txt/*.txt underneath), a single text file, "
            "or a glob pattern to load."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the changed tests to verify they pass**

Run: `uv run --no-sync pytest tests/test_download_scripts.py tests/test_kansai_scripts.py tests/test_load_scripts.py -q -p no:cacheprovider --no-cov`
Expected: all pass.

- [ ] **Step 7: Delete the sixteen scripts**

```bash
git rm -q scripts/download_jepx_spot.py scripts/load_jepx_spot.py scripts/download_occto_demand_forecast.py scripts/download_occto_area_reserve_rate.py scripts/load_occto_demand_forecast.py scripts/load_occto_area_reserve_rate.py scripts/download_tepco_area_demand_generation.py scripts/load_tepco_area_demand_generation.py scripts/download_tepco_power_usage.py scripts/load_tepco_power_usage.py scripts/download_kansai_area_demand_generation.py scripts/load_kansai_area_demand_generation.py scripts/download_kansai_power_usage.py scripts/load_kansai_power_usage.py scripts/download_estat_census_population_mesh.py scripts/load_estat_census_population_mesh.py
```

- [ ] **Step 8: The whole suite, lint, mypy**

Run: `just test -q -p no:cacheprovider` — every test passes, `TOTAL … 100%`; a missing line in one of the five scripts means a handler or branch no test reaches — add the test, not an exclusion. Then `just lint` and `just mypy`, clean.

- [ ] **Step 9: Commit**

```bash
git add scripts/jepx.py scripts/occto.py scripts/tepco.py scripts/kansai.py scripts/estat.py tests/test_download_scripts.py tests/test_kansai_scripts.py tests/test_load_scripts.py
git commit -m "refactor(scripts): jepx, occto, tepco, kansai and estat commands

The sixteen remaining ingestion scripts become five source commands with
download and load subcommands, the shape of scripts/jma.py; OCCTO's datasets
carry the _dad suffix of their data directories and downloader keys.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The justfile, the docs, the YAML descriptions

**Files:**
- Modify: `justfile`, `CLAUDE.md`, `docs/Development.md`, `docs/OCCTO-Demand-Forecast-Retrieval.md`, `docs/TEPCO-Area-Demand-Generation-Retrieval.md`, `docs/TEPCO-Power-Usage-Retrieval.md`, `docs/Kansai-Area-Demand-Generation-Retrieval.md`, `docs/Kansai-Power-Usage-Retrieval.md`, `docs/eStat-Census-Population-Mesh-Retrieval.md`, `dbt/models/raw/jepx.yml`, `dbt/models/raw/occto.yml`, `dbt/models/raw/tepco.yml`, `dbt/models/raw/kansai.yml`, `dbt/models/raw/estat.yml`, `conf/schemas/estat_census_population_mesh.yaml`, `docs/superpowers/README.md`

The rule: where a document tells the reader to run something, the command is `just <source> <verb> <dataset> [flags]`; where it names the code, `scripts/<source>.py`. Line numbers are those of the branch `chore/source-commands` at `2f3ce58`.

- [ ] **Step 1: `justfile`**

After the `jma` recipe insert, in this order:

```
[doc("JEPX, inside the devcontainer: just jepx download spot [--force-all] / just jepx load spot; -h at any level lists what is under it")]
jepx *args:
    @just python scripts/jepx.py "$@"

[doc("OCCTO, inside the devcontainer: just occto download|load demand_forecast_dad|area_reserve_rate_dad; -h at any level lists what is under it")]
occto *args:
    @just python scripts/occto.py "$@"

[doc("TEPCO, inside the devcontainer: just tepco download|load area_demand_generation|power_usage; -h at any level lists what is under it")]
tepco *args:
    @just python scripts/tepco.py "$@"

[doc("Kansai (関西電力送配電), inside the devcontainer: just kansai download|load area_demand_generation|power_usage; -h at any level lists what is under it")]
kansai *args:
    @just python scripts/kansai.py "$@"

[doc("e-Stat, inside the devcontainer: just estat download census_population_mesh [--years …] [--force] / just estat load census_population_mesh; -h at any level lists what is under it")]
estat *args:
    @just python scripts/estat.py "$@"
```

The comment above `refresh-all` becomes:

```
# One refresh recipe covers every source. A single source is refreshed by its command's
# download and load subcommands (`just <source> …`, the six recipes above) and then
# `just dbt build`. JMA runs before MSM because the MSM downloader reads the station seed.
```

In `refresh-all`'s `[doc]`, `with each script's defaults` → `with each command's defaults`. The remaining `just python scripts/…` lines become:

```
    just jepx download spot
    just python scripts/update_holidays_seed.py
    just jepx load spot
```

```
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
```

Check: `just --list` shows the six source recipes; `just --dry-run occto download demand_forecast_dad` prints `just python scripts/occto.py "$@"`.

- [ ] **Step 2: `CLAUDE.md`**

Commands section:

1. Lines 13–17, the single-source intro, become:

```
- A single source is refreshed by its command's `download` and `load` subcommands, then
  `just dbt build`: `scripts/<source>.py`, run as `just <source> <download|load> <dataset>
  [flags]`, `-h` at any level (since 2026-09-28; spec
  `docs/superpowers/specs/2026-09-28-source-commands-design.md`). A dataset is named after
  its directory under `data/<source>/`:
```

2. Line 18: `` - JEPX: `download_jepx_spot.py`, `update_holidays_seed.py`, `load_jepx_spot.py`. `` → `` - JEPX: `just jepx download spot` (`--force-all` refetches every fiscal year), `just python scripts/update_holidays_seed.py`, `just jepx load spot`. ``
3. Lines 33–35, the OCCTO command names: `` `download_occto_demand_forecast.py`, `download_occto_area_reserve_rate.py`, `load_occto_demand_forecast.py`, `load_occto_area_reserve_rate.py`. `` → `` `just occto download demand_forecast_dad`, `just occto download area_reserve_rate_dad`, `just occto load demand_forecast_dad`, `just occto load area_reserve_rate_dad`. ``
4. Lines 37–41, TEPCO: `` (`download_tepco_area_demand_generation.py` redownloads `` → `` (`just tepco download area_demand_generation` redownloads ``; `` `load_tepco_area_demand_generation.py`) `` → `` `just tepco load area_demand_generation`) ``; `` (`download_tepco_power_usage.py` fetches `` → `` (`just tepco download power_usage` fetches ``; `` `load_tepco_power_usage.py`). `` → `` `just tepco load power_usage`). ``
5. Lines 43–47, Kansai: `` `download_kansai_area_demand_generation.py`, `load_kansai_area_demand_generation.py`; `` → `` `just kansai download area_demand_generation`, `just kansai load area_demand_generation`; ``; `` (`download_kansai_power_usage.py` redownloads `` → `` (`just kansai download power_usage` redownloads ``; `` `load_kansai_power_usage.py`, ~3,800 daily files in seconds) `` → `` `just kansai load power_usage`, ~3,800 daily files in seconds) ``.
6. Lines 48–51, e-Stat: `` `download_estat_census_population_mesh.py` downloads `` → `` `just estat download census_population_mesh` downloads ``; `` `load_estat_census_population_mesh.py`. `` → `` `just estat load census_population_mesh`. ``
7. The `just jma <verb> <dataset> [flags]` bullet (after the `just python` bullet) becomes:

```
- `just <source> <verb> <dataset> [flags]` (since 2026-09-28) — the six source commands in the
  devcontainer, `scripts/<source>.py` for `jepx`, `jma`, `occto`, `tepco`, `kansai` and
  `estat`: `download` or `load`, then the dataset (the directory under `data/<source>/`);
  sugar over `just python scripts/<source>.py`. `-h` at any level lists what is under it.
```

Architecture section:

8. Lines 566–567 (JEPX): `` `scripts/download_jepx_spot.py` → `data/jepx/spot/` (gitignored) → `scripts/load_jepx_spot.py` `` → `` `just jepx download spot` (`scripts/jepx.py`) → `data/jepx/spot/` (gitignored) → `just jepx load spot` ``.
9. Line 632: `` `scripts/download_occto_demand_forecast.py` `` → `` `just occto download demand_forecast_dad` (`scripts/occto.py`; ``; line 635: `` `scripts/load_occto_demand_forecast.py` `` → `` `just occto load demand_forecast_dad` ``. (Line 632's opening parenthesis now closes after "history)" as before.)
10. Line 642: `` `scripts/download_occto_area_reserve_rate.py` `` → `` `just occto download area_reserve_rate_dad` ``; line 646: `` `scripts/load_occto_area_reserve_rate.py` `` → `` `just occto load area_reserve_rate_dad` ``.
11. Lines 667–668: `` `scripts/download_tepco_area_demand_generation.py` `` → `` `just tepco download area_demand_generation` (`scripts/tepco.py`) ``; `` `scripts/load_tepco_area_demand_generation.py` `` → `` `just tepco load area_demand_generation` ``.
12. Lines 676–677: `` `scripts/download_kansai_area_demand_generation.py` `` → `` `just kansai download area_demand_generation` (`scripts/kansai.py`) ``; `` `scripts/load_kansai_area_demand_generation.py` `` → `` `just kansai load area_demand_generation` ``.
13. Lines 701–702: `` `scripts/download_tepco_power_usage.py` `` → `` `just tepco download power_usage` ``; `` `scripts/load_tepco_power_usage.py` `` → `` `just tepco load power_usage` ``.
14. Lines 718–719: `` `scripts/download_kansai_power_usage.py` `` → `` `just kansai download power_usage` ``; `` `scripts/load_kansai_power_usage.py` `` → `` `just kansai load power_usage` ``.
15. Line 737: `` `scripts/download_estat_census_population_mesh.py` `` → `` `just estat download census_population_mesh` (`scripts/estat.py`; ``; line 743: `` `scripts/load_estat_census_population_mesh.py` `` → `` `just estat load census_population_mesh` ``. (The parenthesis opened on 737 closes where the old one did.)

- [ ] **Step 3: `docs/Development.md` line 34**

`# every source: each download/load script with its defaults, one dbt build at the end` → `# every source: each command's download and load with its defaults, one dbt build at the end`. Line 35's comment (`scripts/<source>.py`) is now literally true; leave it.

- [ ] **Step 4: The retrieval docs**

1. `docs/OCCTO-Demand-Forecast-Retrieval.md` lines 413–416:

```
just occto download demand_forecast_dad
just occto download area_reserve_rate_dad    # §9
just occto load demand_forecast_dad
just occto load area_reserve_rate_dad        # §9
```

and line 420: `` `scripts/load_occto_demand_forecast.py` performs a full reload into `` → `` `just occto load demand_forecast_dad` performs a full reload into ``.

2. `docs/TEPCO-Area-Demand-Generation-Retrieval.md` lines 166–167: `just tepco download area_demand_generation` / `just tepco load area_demand_generation`.
3. `docs/TEPCO-Power-Usage-Retrieval.md` lines 149–150: `` `scripts/download_tepco_power_usage.py` (`--force-yearly`) and `scripts/load_tepco_power_usage.py`, then `just dbt build` `` → `` `just tepco download power_usage` (`--force-yearly`) and `just tepco load power_usage`, then `just dbt build` ``.
4. `docs/Kansai-Area-Demand-Generation-Retrieval.md` lines 146–147: `just kansai download area_demand_generation` / `just kansai load area_demand_generation`.
5. `docs/Kansai-Power-Usage-Retrieval.md` lines 159–160: `just kansai download power_usage   # 126 zips, ~8.5 MB` / `just kansai load power_usage       # 3,809 files, 91,416 rows, ~10 s`.
6. `docs/eStat-Census-Population-Mesh-Retrieval.md` lines 218–219: `just estat download census_population_mesh   # [--years 2015 2020] [--force]; ~50 min cold (server-side archive generation), ~2 min when cached` / `just estat load census_population_mesh`; line 257: `` 4. Download (`scripts/download_estat_census_population_mesh.py`, `--years <year>` `` → `` 4. Download (`just estat download census_population_mesh`, `--years <year>` ``.

- [ ] **Step 5: The YAML descriptions**

1. `dbt/models/raw/jepx.yml` line 6: `CSV files by scripts/load_jepx_spot.py (full` → `CSV files by \`just jepx load spot\` (scripts/jepx.py; full`.
2. `dbt/models/raw/occto.yml` lines 7–8: `scripts/download_occto_*.py and loaded by scripts/load_occto_*.py (full reload, one script pair per dataset).` → `` `just occto download <dataset>` and loaded by `just occto load <dataset>` (scripts/occto.py; full reload, one subcommand pair per dataset). ``
3. `dbt/models/raw/tepco.yml` lines 9–10: `scripts/download_tepco_area_demand_generation.py and loaded by scripts/load_tepco_area_demand_generation.py (full reload;` → `` `just tepco download area_demand_generation` and loaded by `just tepco load area_demand_generation` (scripts/tepco.py; full reload; ``; line 15: `(scripts/download_tepco_power_usage.py / load_tepco_power_usage.py;` → `` (`just tepco download power_usage` / `just tepco load power_usage`; ``; lines 75–76: `by scripts/download_tepco_power_usage.py, loaded by scripts/load_tepco_power_usage.py, full reload)` → `` by `just tepco download power_usage`, loaded by `just tepco load power_usage`, full reload) ``.
4. `dbt/models/raw/kansai.yml` lines 10–11, 15, 72–73: the same four edits with `kansai` in place of `tepco` and `scripts/kansai.py`.
5. `dbt/models/raw/estat.yml` lines 8–9: `scripts/download_estat_census_population_mesh.py and loaded by scripts/load_estat_census_population_mesh.py (full reload,` → `` `just estat download census_population_mesh` and loaded by `just estat load census_population_mesh` (scripts/estat.py; full reload, ``.
6. `conf/schemas/estat_census_population_mesh.yaml` lines 7–8: `downloaded by scripts/download_estat_census_population_mesh.py and loaded by EstatCensusMeshCsvLoader (scripts/load_estat_census_population_mesh.py).` → `downloaded by just estat download census_population_mesh and loaded by EstatCensusMeshCsvLoader (just estat load census_population_mesh; both scripts/estat.py).`

- [ ] **Step 6: The design-history row**

In `docs/superpowers/README.md`, the 2026-09-28 row's Plan cell becomes `[plan 1](superpowers/plans/2026-09-28-source-commands-jma.md) · [plan 2](superpowers/plans/2026-09-28-source-commands-sources.md)`.

- [ ] **Step 7: Check**

`just docs-links`; `(cd dbt && DBT_THRIFT_HOST=localhost uv run --no-sync dbt parse)`; the stale-name sweep of the spec §7 (`git ls-files -- . ':!docs/superpowers' > scratch/…/tracked.txt` first, then the `xargs grep … | sed … | sort -u | while …` over that file), which must print nothing — and, since PR 2 ends the migration, a plain `git grep -nE "(download|load)_(jepx|occto|tepco|kansai|estat)[a-z_]*\.py" -- ':!docs/superpowers'` must be empty too.

- [ ] **Step 8: Commit**

```bash
git add justfile
git commit -m "build(justfile): the five source recipes, and refresh-all through them

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git add CLAUDE.md docs/Development.md docs/OCCTO-Demand-Forecast-Retrieval.md docs/TEPCO-Area-Demand-Generation-Retrieval.md docs/TEPCO-Power-Usage-Retrieval.md docs/Kansai-Area-Demand-Generation-Retrieval.md docs/Kansai-Power-Usage-Retrieval.md docs/eStat-Census-Population-Mesh-Retrieval.md dbt/models/raw/jepx.yml dbt/models/raw/occto.yml dbt/models/raw/tepco.yml dbt/models/raw/kansai.yml dbt/models/raw/estat.yml conf/schemas/estat_census_population_mesh.yaml docs/superpowers/README.md
git commit -m "docs(scripts): the five source commands in CLAUDE.md, the retrieval docs and the source YAML

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Whole-branch review

As PR 1's Task 5: four lenses (spec §3.2–§3.6 conformance flag by flag against the deleted scripts; the tests' seam, accounting and mutation-proofness; the docs and the sweep; behaviour as a user meets it, host-side and network-free), every finding verified by three refuters, the confirmed ones fixed in one commit per finding class, the gates run again.

### Task 4: In-container verification (main session, background tasks)

The prefix, written out each time:

```
docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics exec -T -w /workspace/.claude/worktrees/chore+source-commands-2 -e PYTHONPATH=/workspace/.claude/worktrees/chore+source-commands-2 devcontainer python scripts/<source>.py <verb> <dataset> [flags]
```

- [ ] **Step 1: The counts before**, read on 2026-09-28 before any run: `jepx_spot` 183,600; `occto_demand_forecast_dad` 11,076; `occto_area_reserve_rate_dad` 258,720; `tepco_area_demand_generation_actual` 78,384; `tepco_power_usage_hourly` 91,776; `kansai_area_demand_generation_actual` 78,384; `kansai_power_usage_hourly` 91,752; `estat_census_population_mesh` 937,222.
- [ ] **Step 2: The eight loads first** (the downloads below can add a day's rows, so the loads run against the files as they are): `jepx load spot`, `occto load demand_forecast_dad`, `occto load area_reserve_rate_dad`, `tepco load area_demand_generation`, `tepco load power_usage`, `kansai load area_demand_generation`, `kansai load power_usage`, `estat load census_population_mesh` — each `Loaded N rows` equal to Step 1's count.
- [ ] **Step 3: The eight downloads with their defaults**: `jepx download spot` (the two newest fiscal years refetched, the rest cached), `occto download demand_forecast_dad` and `occto download area_reserve_rate_dad` (seconds; a portal HTTP-200 error screen is the known transient — retry once), `tepco download area_demand_generation` (~5 MB), `tepco download power_usage` (~4 MB, yearly files cached), `kansai download area_demand_generation` (~2 MB), `kansai download power_usage` (~8.5 MB, 126 zips), `estat download census_population_mesh` (cached: the listing pages only, ~2 min). Keep each run's last log line (file counts) for the Evidence table.
- [ ] **Step 4: The help trees**: `<source>` bare → exit 2 naming `{download,load}`; `<source> download` bare → the datasets; one `-h` per source.

### Task 5: The pull request

- [ ] **Step 1**: `git push -u origin chore/source-commands-2`; `gh pr create --base chore/source-commands --title "refactor(scripts): jepx, occto, tepco, kansai and estat commands" --body-file scratch/…/pr-body.md`; `gh pr edit <n> --add-assignee hankehly --add-label chore --add-label ingestion`. The body: Summary (PR 2 of the spec, stacked on #232, completes the migration), Changes, Effect on what exists (spec §8's rows for these sources; the eight counts), Checks, Decisions (any the review changed), Evidence (Task 4's table). Never spell out the Codex mention.
- [ ] **Step 2**: watch the review as PR 1's; address every finding; CI green on the current head; report ready. The researcher merges #232 first, then this one (GitHub retargets it to `main`).
