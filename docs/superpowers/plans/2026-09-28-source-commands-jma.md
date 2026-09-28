# Source Commands, PR 1 (JMA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the eight JMA ingestion scripts with one, `scripts/jma.py`, a command with `download` and `load` subcommands and one subcommand per dataset, plus the load helper the other five source commands will share.

**Architecture:** `power_market_analytics/ingestion/cli.py` holds the argparse plumbing every source script repeats: `add_subcommand` (a dataset subparser whose description is its handler's docstring), `add_load_arguments` (`--schema` / `--data` / `--table` with the dataset's defaults) and `load_from_args` (contract → loader → row count). `scripts/jma.py` is one module: a handler per dataset whose body is today's script body, `build_parser()` with nested `add_subparsers`, `main(argv)` that dispatches to `args.run`. The MSM downloader is imported inside its handler so no other `jma` subcommand needs eccodes. Tests keep their seam: `import_script("jma")`, the class swapped in the script's namespace, `main([...])` with `download <dataset>` or `load <dataset>` in front of today's argv.

**Tech Stack:** Python 3.13, argparse (no click/typer), loguru, pytest with `tests.support.import_script`, `uv`, `just`; the loads run on the devcontainer's Spark session.

**Spec:** `docs/superpowers/specs/2026-09-28-source-commands-design.md`

## Global Constraints

- Work in the worktree `.claude/worktrees/chore+source-commands`, branch `chore/source-commands`; the main checkout is never touched. Run every command from the worktree path. The worktree guard refuses compound commands that mention `git` inside a heredoc or a pipeline it cannot follow: write files with the Write/Edit tools, keep `git` commands plain.
- Commits: Conventional Commits, `refactor(ingestion): …` / `refactor(scripts): …` / `docs(jma): …`; every message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Python: NumPy-style docstrings; ruff line length 100 (a PostToolUse hook runs `ruff format` + `ruff check --fix` on every edited `.py` — **it strips unused imports**, so add an import in the same edit as its first use); `just lint`, `just mypy` clean; the coverage gate is 100 % over `power_market_analytics/` + `scripts/` (`just test`).
- Tests never touch the network or Spark: downloader and loader classes are swapped for fakes; scripts are driven through `main(argv)`.
- Names are the spec's: `scripts/jma.py`, `power_market_analytics/ingestion/cli.py`, datasets `hourly`, `normals`, `msm_surface_forecast`, `stations`; `prog="jma"`.
- Every flag and default of the old scripts survives except: `download_jma_hourly.py`'s `--elements` and `--force-all` (gone; `--station ID …` on `download hourly` replaces the script) and `load_jma_hourly.py`'s `--data-dir` / `--schema-dir` (now `--schema` / `--data` / `--table`, spec decisions 6 and 7).
- `scripts/jma.py` imports `MsmDownloader` inside `download_msm_surface_forecast` only, with the reason in a comment (spec decision 8). Every other import is at the top.
- Writing style (docs, YAML descriptions, PR body): plain short sentences; exact names and numbers.
- The archive under `docs/superpowers/` keeps the old script names (spec decision 13).
- Host-side gates: `just test`, `just lint`, `just mypy`, `just docs-links`, and `(cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse)`. In-container runs go through the main checkout's compose project with the worktree as the working directory (Task 6), and are run by the main session as background tasks.

## Review Focus

1. `--station` together with `--prefecture` from another prefecture: the prefecture filter runs first, so the station is not there and the run must stop with `ValueError` naming the station, not proceed with an empty plan. Pinned in Task 2 (`TestBuildPlan.test_station_filter_runs_after_the_prefecture_filter`).
2. `--station` naming a station that ended before the window: an empty plan and the "ended before" log line, not an error — the id was valid. Pinned in Task 2 (`TestBuildPlan.test_a_discontinued_station_makes_an_empty_plan`).
3. eccodes missing from the interpreter: every `jma` subcommand but `download msm_surface_forecast` must still run, and that one must fail with `ImportError` when it runs, not earlier. Pinned in Task 2 (`TestMsmDownloaderImportIsLazy`, both tests).
4. `download hourly -h` must show the long docstring paragraph by paragraph, not re-wrapped into one block and not indented by the docstring's margin. Pinned in Task 1 (`TestAddSubcommand.test_description_is_the_handlers_docstring_dedented`) and Task 2 (`TestParserTree.test_help_at_every_level_exits_0`, the `download hourly -h` case asserts a phrase).
5. `load stations` and `download nope`: an invalid dataset must exit 2 with the parser's own list of choices, and no class may be touched. Pinned in Task 2 (`TestParserTree`).

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/ingestion/cli.py` (new) | `add_subcommand`, `add_load_arguments`, `load_from_args` |
| `tests/test_ingestion_cli.py` (new) | the three helpers |
| `scripts/jma.py` (new) | the `jma` command: four download handlers, three load handlers, `build_plan`, `build_parser`, `main` |
| `scripts/download_jma_hourly.py`, `download_jma_hourly_all.py`, `download_jma_msm_surface_forecast.py`, `download_jma_normals.py`, `load_jma_hourly.py`, `load_jma_msm_surface_forecast.py`, `load_jma_normals.py`, `update_jma_stations_seed.py` | deleted |
| `tests/test_jma_scripts.py` | the `download` subcommands, the parser tree, the lazy import |
| `tests/test_load_scripts.py` | every `load` subcommand, table-driven; the JMA rows join it |
| `tests/test_msm.py` | gains `TestDefaultEndDate` |
| `tests/test_jma_normals_scripts.py`, `tests/test_msm_scripts.py` | deleted |
| `justfile` | the `jma` recipe; `refresh-all`'s JMA lines; the `python` doc example |
| `CLAUDE.md`, `docs/Development.md`, `docs/JMA-Weather-Data-Retrieval.md`, `docs/JMA-MSM-GPV-Retrieval.md`, `docs/JMA-Climatological-Normals-Retrieval.md`, `dbt/models/raw/jma.yml`, `dbt/models/curated/dim_jma_station.yml`, `conf/schemas/jma_msm_surface_forecast.yaml`, `conf/schemas/jma_normal_surface_daily.yaml`, `power_market_analytics/ingestion/msm/vintage.py`, `power_market_analytics/ingestion/jma/normals.py`, `tests/test_jma_loader.py` | the JMA mentions rewritten |

---

### Task 1: The shared plumbing — `power_market_analytics/ingestion/cli.py`

**Files:**
- Create: `power_market_analytics/ingestion/cli.py`
- Test: `tests/test_ingestion_cli.py`

**Interfaces:**
- Consumes: `CsvLoader`, `CsvTableSchema` from `power_market_analytics.ingestion.loader`.
- Produces:
  - `add_subcommand(subparsers, name: str, run: Callable[[argparse.Namespace], None], *, help: str) -> argparse.ArgumentParser` — registers `name` under `subparsers` with `help`, `description=inspect.cleandoc(run.__doc__ or "")`, `formatter_class=argparse.RawDescriptionHelpFormatter`, and `set_defaults(run=run)`; returns the subcommand's parser.
  - `add_load_arguments(parser, *, schema: Path, data: Path, table: str, data_help: str = "A file, a directory of files or a glob pattern to load.") -> None` — adds `--schema` (type `Path`), `--data` (type `Path`), `--table` (str) with those defaults.
  - `load_from_args(args: argparse.Namespace, loader_cls: type[CsvLoader]) -> int` — `CsvTableSchema.from_yaml(args.schema)`, `loader_cls(schema=…, filepath=args.data, table=args.table).load()`, logs `Loaded {n} rows into {table}`, returns `n`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingestion_cli.py`:

```python
"""The argparse plumbing the source scripts share (``power_market_analytics.ingestion.cli``)."""

from __future__ import annotations

import argparse

import pytest
from loguru import logger

from power_market_analytics.ingestion.cli import (
    add_load_arguments,
    add_subcommand,
    load_from_args,
)
from power_market_analytics.ingestion.loader import CsvTableSchema
from tests.support import REPO_ROOT


class RecordingLoader:
    """A loader stand-in: records its constructor kwargs and whether ``load()`` ran."""

    built: list[dict] = []

    def __init__(self, schema, filepath, table):
        self.record = {"schema": schema, "filepath": filepath, "table": table, "loaded": False}
        type(self).built.append(self.record)

    def load(self) -> int:
        self.record["loaded"] = True
        return 7


@pytest.fixture(autouse=True)
def reset_recording_loader():
    RecordingLoader.built = []
    yield
    RecordingLoader.built = []


DEFAULTS = {
    "schema": REPO_ROOT / "conf/schemas/jepx_spot.yaml",
    "data": REPO_ROOT / "data/jepx/spot",
    "table": "pma_raw.jepx_spot",
}


def load_parser(**overrides) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="t")
    add_load_arguments(parser, **{**DEFAULTS, **overrides})
    return parser


class TestAddLoadArguments:
    def test_defaults_are_the_datasets(self):
        args = load_parser().parse_args([])

        assert (args.schema, args.data, args.table) == (
            DEFAULTS["schema"],
            DEFAULTS["data"],
            DEFAULTS["table"],
        )

    def test_overrides_are_paths_and_a_string(self, tmp_path):
        args = load_parser().parse_args(
            ["--schema", str(tmp_path / "s.yaml"), "--data", str(tmp_path / "x.csv"), "--table", "db.t"]
        )

        assert args.schema == tmp_path / "s.yaml"
        assert args.data == tmp_path / "x.csv"
        assert args.table == "db.t"

    def test_the_data_help_is_the_datasets_when_given(self, capsys):
        parser = load_parser(data_help="The manifest is read two levels up.")

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["-h"])

        assert exc.value.code == 0
        assert "The manifest is read two levels up." in capsys.readouterr().out

    def test_the_data_help_has_a_default(self, capsys):
        with pytest.raises(SystemExit):
            load_parser().parse_args(["-h"])

        assert "A file, a directory of files or a glob pattern to load." in capsys.readouterr().out


class TestLoadFromArgs:
    def test_reads_the_contract_runs_the_loader_and_returns_the_count(self):
        args = load_parser().parse_args([])

        assert load_from_args(args, RecordingLoader) == 7

        (built,) = RecordingLoader.built
        assert isinstance(built["schema"], CsvTableSchema)
        assert built["schema"].grain == ["trade_date", "time_code"]
        assert built["filepath"] == DEFAULTS["data"]
        assert built["table"] == "pma_raw.jepx_spot"
        assert built["loaded"] is True

    def test_a_missing_contract_fails_before_the_loader_is_built(self, tmp_path):
        args = load_parser().parse_args(["--schema", str(tmp_path / "nope.yaml")])

        with pytest.raises(FileNotFoundError):
            load_from_args(args, RecordingLoader)

        assert RecordingLoader.built == []

    def test_logs_the_row_count_and_the_table(self):
        args = load_parser().parse_args(["--table", "db.t"])
        messages: list[str] = []
        sink = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")
        try:
            load_from_args(args, RecordingLoader)
        finally:
            logger.remove(sink)

        assert "Loaded 7 rows into db.t" in messages


class TestAddSubcommand:
    @staticmethod
    def parser_with(run, help="A thing"):
        parser = argparse.ArgumentParser(prog="t")
        subparsers = parser.add_subparsers(dest="dataset", required=True)
        sub = add_subcommand(subparsers, "thing", run, help=help)
        return parser, sub

    def test_run_is_the_handler_and_the_parser_is_returned(self):
        calls = []

        def run(args):
            """Do the thing."""
            calls.append(args)

        parser, sub = self.parser_with(run)
        sub.add_argument("--n", type=int, default=1)

        args = parser.parse_args(["thing", "--n", "3"])
        args.run(args)

        assert calls == [args]
        assert args.n == 3

    def test_description_is_the_handlers_docstring_dedented(self, capsys):
        def run(args):
            """First line.

            Second paragraph, kept apart from the first.
            """

        parser, _sub = self.parser_with(run)

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["thing", "-h"])

        out = capsys.readouterr().out
        assert exc.value.code == 0
        assert "First line.\n\nSecond paragraph, kept apart from the first." in out

    def test_the_help_is_what_the_verb_lists(self, capsys):
        def run(args):
            """Do the thing."""

        parser, _sub = self.parser_with(run, help="One line about the thing")

        with pytest.raises(SystemExit):
            parser.parse_args(["-h"])

        assert "One line about the thing" in capsys.readouterr().out

    def test_a_handler_without_a_docstring_gets_an_empty_description(self, capsys):
        def run(args):
            pass

        parser, _sub = self.parser_with(run)

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["thing", "-h"])

        assert exc.value.code == 0
        assert "usage: t thing [-h]" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ingestion_cli.py -q -p no:cacheprovider --no-cov`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'power_market_analytics.ingestion.cli'`.

- [ ] **Step 3: Write the module**

Create `power_market_analytics/ingestion/cli.py`:

```python
"""The argparse plumbing the source scripts share.

Each ``scripts/<source>.py`` is one command with ``download`` and ``load`` verbs and a
subcommand per dataset (``docs/superpowers/specs/2026-09-28-source-commands-design.md``).
The bespoke parts — a download's flags and body — live in the script. What every dataset
repeats lives here: how a subcommand is registered, the three arguments every load takes,
and the run they drive.
"""

from __future__ import annotations

import argparse
import inspect
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.loader import CsvLoader, CsvTableSchema

#: A subcommand's body: ``main`` calls it with the parsed arguments.
Handler = Callable[[argparse.Namespace], None]


def add_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    run: Handler,
    *,
    help: str,
) -> argparse.ArgumentParser:
    """Register a dataset subcommand whose ``--help`` description is its handler's docstring.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The ``download`` or ``load`` verb's subparsers.
    name : str
        The dataset name — the directory under ``data/<source>/``.
    run : callable
        The handler. Its docstring, dedented, is the subcommand's description, kept
        paragraph by paragraph (``RawDescriptionHelpFormatter``).
    help : str
        The one-line summary the verb's ``--help`` lists.

    Returns
    -------
    argparse.ArgumentParser
        The subcommand's parser, for its own arguments.
    """
    parser = subparsers.add_parser(
        name,
        help=help,
        description=inspect.cleandoc(run.__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(run=run)
    return parser


def add_load_arguments(
    parser: argparse.ArgumentParser,
    *,
    schema: Path,
    data: Path,
    table: str,
    data_help: str = "A file, a directory of files or a glob pattern to load.",
) -> None:
    """Add ``--schema``, ``--data`` and ``--table``, the three arguments every load takes.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The load subcommand's parser.
    schema : pathlib.Path
        Default contract, ``conf/schemas/<table>.yaml``.
    data : pathlib.Path
        Default input: the downloader's output directory, a file or a glob pattern.
    table : str
        Default destination, ``pma_raw.<table>``.
    data_help : str, optional
        The ``--data`` help, when the dataset has more to say than the default.
    """
    parser.add_argument(
        "--schema", type=Path, default=schema, help="Path to the YAML schema definition."
    )
    parser.add_argument("--data", type=Path, default=data, help=data_help)
    parser.add_argument("--table", default=table, help="Destination table (database.table).")


def load_from_args(args: argparse.Namespace, loader_cls: type[CsvLoader]) -> int:
    """Read the contract, run the loader and log the row count.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments carrying ``schema``, ``data`` and ``table``
        (:func:`add_load_arguments`).
    loader_cls : type
        The loader class, built as ``loader_cls(schema=…, filepath=args.data, table=args.table)``.
        Looked up by the caller at call time, so a test can swap it in the script's namespace.

    Returns
    -------
    int
        The number of rows written.

    Raises
    ------
    FileNotFoundError
        If the contract file does not exist — before any loader is built.
    """
    schema = CsvTableSchema.from_yaml(args.schema)
    loader = loader_cls(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)
    return n_rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ingestion_cli.py -q -p no:cacheprovider --no-cov`
Expected: 11 passed.

- [ ] **Step 5: Lint and type-check, then commit**

Run: `just lint` and `just mypy` — both clean (`argparse._SubParsersAction[argparse.ArgumentParser]` is the typeshed generic; if mypy objects, annotate the parameter `argparse._SubParsersAction  # type: ignore[type-arg]` and say so in the PR).

```bash
git add power_market_analytics/ingestion/cli.py tests/test_ingestion_cli.py
git commit -m "refactor(ingestion): the argparse plumbing the source scripts share

add_subcommand, add_load_arguments and load_from_args, for the one-command-
per-source scripts of docs/superpowers/specs/2026-09-28-source-commands-design.md.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `scripts/jma.py` with its tests; the eight scripts and two test files go

**Files:**
- Create: `scripts/jma.py`
- Modify: `tests/test_jma_scripts.py` (rewritten around the new script), `tests/test_load_scripts.py` (the table), `tests/test_msm.py` (gains `TestDefaultEndDate`), `tests/test_jma_loader.py:5` (one docstring phrase)
- Delete: `scripts/download_jma_hourly.py`, `scripts/download_jma_hourly_all.py`, `scripts/download_jma_msm_surface_forecast.py`, `scripts/download_jma_normals.py`, `scripts/load_jma_hourly.py`, `scripts/load_jma_msm_surface_forecast.py`, `scripts/load_jma_normals.py`, `scripts/update_jma_stations_seed.py`, `tests/test_jma_normals_scripts.py`, `tests/test_msm_scripts.py`

**Interfaces:**
- Consumes: Task 1's `add_subcommand`, `add_load_arguments`, `load_from_args`; `JmaHourlyDownloader` (`EARLIEST_YEAR`, `path_for`, `window_count`, `download(station_id, elements, year, force)`), `SCRAPE_ELEMENTS`, `JmaStationMasterDownloader(dest, staffed_only, jepx_areas_only).download(force)`, `JmaNormalsDownloader(data_dir, timeout).download_all(years)`, `VINTAGES`, `MsmDownloader(data_dir).download_range(start, end, stations, force, keep_grib)`, `load_stations(stations_csv, station_areas_csv)`, `DEFAULT_BACKFILL_START`, `default_end_date()`, the three loader classes.
- Produces: `scripts/jma.py` with module names the tests patch — `JmaHourlyDownloader`, `JmaStationMasterDownloader`, `JmaNormalsDownloader`, `JmaHourlyCsvLoader`, `JmaNormalsCsvLoader`, `MsmForecastCsvLoader`, `load_stations`, `default_end_date` — plus `REPO_ROOT`, `SEED_PATH`, `MAX_CONSECUTIVE_FAILURES`, `build_plan(stations_csv, start_year, end_year, limit, prefectures=None, stations=None)`, `build_parser()`, `main(argv=None)`.

- [ ] **Step 1: Rewrite `tests/test_jma_scripts.py`**

Apply these edits; the fakes `write_stations`, `scrape_path`, `make_hourly_fake`, `make_station_master_fake`, `capture_logs` and every existing assertion stay as they are.

1. Replace the module docstring and add imports:

```python
"""CLI wiring of ``scripts/jma.py``: the ``download`` subcommands (hourly, normals,
msm_surface_forecast, stations), the parser tree, and the one import that must stay lazy.

The downloader classes are swapped for recording fakes in the script's namespace, so what
is asserted is the argument plumbing, not the HTTP work. The ``load`` subcommands are in
``test_load_scripts.py`` with every other load.
"""

from __future__ import annotations

import csv
import datetime
import importlib
import os
import re
import sys
import time
from collections.abc import Collection
from pathlib import Path

import pytest
from loguru import logger

from power_market_analytics.ingestion.jma.hourly import SCRAPE_ELEMENTS, JmaHourlyDownloader
from power_market_analytics.ingestion.jma.stations import JmaStationMasterDownloader
from power_market_analytics.ingestion.msm import download as msm_download
from power_market_analytics.ingestion.msm import stations as msm_stations
from power_market_analytics.ingestion.msm import vintage as msm_vintage
from tests.support import REPO_ROOT, import_script

TODAY = datetime.date.today()
```

2. Everywhere: `import_script("download_jma_hourly_all")` → `import_script("jma")`; `import_script("update_jma_stations_seed")` → `import_script("jma")`.

3. Delete the whole `class TestDownloadJmaHourly:` (the five per-station tests) and the `# ---- download_jma_hourly` banner above `make_hourly_fake`; keep `make_hourly_fake` itself under a banner `# ---- download hourly`.

4. In `TestBuildPlan`, append four tests:

```python
    def test_station_filter_keeps_only_those_ids(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "s47772", "prefecture_code": "62"},
                {"station_id": "s47401", "prefecture_code": "11"},
            ],
        )
        assert script.build_plan(stations, 2016, 2016, None, stations=["s47772"]) == [
            ("s47772", 2016)
        ]
        assert script.build_plan(stations, 2016, 2016, None, stations=["s47401", "s47662"]) == [
            ("s47662", 2016),
            ("s47401", 2016),
        ]

    def test_unmatched_station_filter_raises(self, tmp_path):
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv", [{"station_id": "s47662", "prefecture_code": "44"}]
        )
        with pytest.raises(ValueError, match=r"No stations with ids \['nope'\]"):
            script.build_plan(stations, 2016, 2016, None, stations=["nope"])

    def test_station_filter_runs_after_the_prefecture_filter(self, tmp_path):
        # 大阪 (s47772, pd 62) is not among the 東京 stations, so asking for both is empty
        # and stops here, rather than planning nothing and reporting 0/0 done.
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "s47772", "prefecture_code": "62"},
            ],
        )
        with pytest.raises(ValueError, match=r"No stations with ids \['s47772'\]"):
            script.build_plan(stations, 2016, 2016, None, prefectures=[44], stations=["s47772"])

    def test_a_discontinued_station_makes_an_empty_plan(self, tmp_path):
        # A valid id whose observations ended before the window is not a typo: no error,
        # an empty plan, and the "ended before" line in the log.
        script = import_script("jma")
        stations = write_stations(
            tmp_path / "stations.csv",
            [
                {
                    "station_id": "s47401",
                    "prefecture_code": "11",
                    "observation_ended_on": "2003-10-16",
                }
            ],
        )
        assert script.build_plan(stations, 2016, 2016, None, stations=["s47401"]) == []
```

5. Rename `class TestDownloadJmaHourlyAll` → `class TestDownloadHourly`. In every `script.main([...])` of that class insert `"download", "hourly",` as the first two items (the dry-run, full-run, prefecture-and-limit, unmatched-prefecture, failing-station, ten-failures, reset, current-year and progress tests). Append two tests:

```python
    def test_station_filter_flows_into_the_plan(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(
            tmp_path / "stations.csv",
            [
                {"station_id": "s47401", "prefecture_code": "11"},
                {"station_id": "s47662", "prefecture_code": "44"},
                {"station_id": "s47772", "prefecture_code": "62"},
            ],
        )
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        script.main(
            [
                "download",
                "hourly",
                "--stations-csv",
                str(stations_csv),
                "--data-dir",
                str(tmp_path / "hourly"),
                "--start-year",
                "2016",
                "--end-year",
                "2017",
                "--station",
                "s47662",
            ]
        )

        assert hourly["calls"] == [
            ("s47662", SCRAPE_ELEMENTS, 2016, False),
            ("s47662", SCRAPE_ELEMENTS, 2017, False),
        ]

    def test_unmatched_station_aborts_before_downloading(self, tmp_path, monkeypatch):
        script = import_script("jma")
        hourly: dict = {}
        stations_csv = write_stations(tmp_path / "stations.csv", self.TWO_STATIONS)
        monkeypatch.setattr(script, "JmaStationMasterDownloader", make_station_master_fake({}))
        monkeypatch.setattr(
            script, "JmaHourlyDownloader", make_hourly_fake(hourly, write_root=tmp_path)
        )

        with pytest.raises(ValueError, match="No stations with ids"):
            script.main(
                ["download", "hourly", "--stations-csv", str(stations_csv), "--station", "nope"]
            )

        assert hourly.get("calls", []) == []
```

6. Rename `class TestUpdateJmaStationsSeed` → `class TestDownloadStations`, banner `# ---- download stations`; `script.main([])` → `script.main(["download", "stations"])`; `script.main(["--dest", …])` → `script.main(["download", "stations", "--dest", …])`. The `SEED_PATH` assertion stays.

7. Append, after `TestDownloadStations`, the normals tests (moved from `tests/test_jma_normals_scripts.py`):

```python
# --------------------------------------------------------------------------- download normals


def make_normals_fake(record: dict):
    class FakeNormals:
        def __init__(self, data_dir, timeout=60.0):
            record["data_dir"] = data_dir
            record["timeout"] = timeout

        def download_all(self, years=None):
            record["years"] = years
            return [Path(record["data_dir"]) / "2020/csv/daily/nml_sfc_d_47662.csv"]

    return FakeNormals


class TestDownloadNormals:
    def test_defaults(self, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_normals_fake(record))

        script.main(["download", "normals"])

        assert record == {"data_dir": Path("data/jma/normals"), "timeout": 60.0, "years": [2020]}

    def test_overrides(self, tmp_path, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_normals_fake(record))

        script.main(
            [
                "download",
                "normals",
                "--data-dir",
                str(tmp_path),
                "--timeout",
                "5",
                "--years",
                "2020",
            ]
        )

        assert record == {"data_dir": tmp_path, "timeout": 5.0, "years": [2020]}

    def test_an_unconfigured_year_is_rejected_by_the_parser(self, monkeypatch):
        script = import_script("jma")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_normals_fake(record))

        with pytest.raises(SystemExit) as exc:
            script.main(["download", "normals", "--years", "2030"])

        assert exc.value.code == 2
        assert record == {}
```

8. Append the MSM download tests (moved from `tests/test_msm_scripts.py`; the fake goes on the `msm.download` module, which is what the handler's call-time import reads):

```python
# --------------------------------------------------------------------------- download msm_surface_forecast


class TestDownloadMsmSurfaceForecast:
    @pytest.fixture
    def fake(self, monkeypatch):
        module = import_script("jma")
        seen: dict = {}
        stations = [
            msm_stations.MsmStation(station_id="s47662", latitude=35.6, longitude=139.7),
            msm_stations.MsmStation(station_id="s47772", latitude=34.6, longitude=135.5),
        ]

        def fake_load_stations(stations_csv, station_areas_csv):
            seen["stations_csv"] = stations_csv
            seen["station_areas_csv"] = station_areas_csv
            return stations

        class FakeDownloader:
            def __init__(self, data_dir):
                seen["data_dir"] = data_dir

            def download_range(self, start_date, end_date, stations, force=False, keep_grib=False):
                seen["start_date"] = start_date
                seen["end_date"] = end_date
                seen["stations"] = stations
                seen["force"] = force
                seen["keep_grib"] = keep_grib
                return [Path("data/jma/msm_surface_forecast/csv/msm_surface_20260821.csv.gz")]

        monkeypatch.setattr(module, "load_stations", fake_load_stations)
        # The handler imports MsmDownloader when it runs (spec decision 8), so the fake
        # goes on the msm.download module, not on the script.
        monkeypatch.setattr(msm_download, "MsmDownloader", FakeDownloader)
        monkeypatch.setattr(module, "default_end_date", lambda: datetime.date(2026, 8, 22))
        return module, seen, stations

    def test_defaults(self, fake):
        module, seen, stations = fake

        module.main(["download", "msm_surface_forecast"])

        assert seen["stations_csv"] == REPO_ROOT / "dbt/seeds/jma_stations.csv"
        assert seen["station_areas_csv"] == REPO_ROOT / "dbt/seeds/jma_station_areas.csv"
        assert seen["data_dir"] == Path("data/jma/msm_surface_forecast")
        assert seen["start_date"] == msm_vintage.DEFAULT_BACKFILL_START
        assert seen["end_date"] == datetime.date(2026, 8, 22)
        assert seen["stations"] == stations
        assert seen["force"] is False
        assert seen["keep_grib"] is False

    def test_start_end_data_dir_overrides(self, fake, tmp_path):
        module, seen, _stations = fake

        module.main(
            [
                "download",
                "msm_surface_forecast",
                "--start-date",
                "2026-08-01",
                "--end-date",
                "2026-08-03",
                "--data-dir",
                str(tmp_path),
            ]
        )

        assert seen["start_date"] == datetime.date(2026, 8, 1)
        assert seen["end_date"] == datetime.date(2026, 8, 3)
        assert seen["data_dir"] == tmp_path
        assert seen["force"] is False
        assert seen["keep_grib"] is False

    def test_force_and_keep_grib_flags_forwarded(self, fake):
        module, seen, _stations = fake

        module.main(["download", "msm_surface_forecast", "--force", "--keep-grib"])

        assert seen["force"] is True
        assert seen["keep_grib"] is True
```

9. Append the parser-tree tests and the lazy-import tests:

```python
# --------------------------------------------------------------------------- the parser tree


def choices_in(error: str) -> list[str]:
    """The names argparse lists in an invalid-choice message, in its order."""
    listed = re.search(r"\(choose from (.*)\)", error)
    assert listed is not None, error
    return [name.strip("'") for name in listed.group(1).split(", ")]


CLASSES = (
    "JmaHourlyDownloader",
    "JmaNormalsDownloader",
    "JmaStationMasterDownloader",
    "JmaHourlyCsvLoader",
    "JmaNormalsCsvLoader",
    "MsmForecastCsvLoader",
)


class TestParserTree:
    def test_download_lists_exactly_the_four_datasets(self, capsys):
        script = import_script("jma")

        with pytest.raises(SystemExit) as exc:
            script.main(["download", "nope"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == [
            "hourly",
            "normals",
            "msm_surface_forecast",
            "stations",
        ]

    def test_load_lists_exactly_the_three_datasets(self, capsys):
        script = import_script("jma")

        with pytest.raises(SystemExit) as exc:
            script.main(["load", "stations"])

        assert exc.value.code == 2
        assert choices_in(capsys.readouterr().err) == ["hourly", "normals", "msm_surface_forecast"]

    @pytest.mark.parametrize("argv", [[], ["download"], ["load"], ["refresh", "hourly"]])
    def test_a_missing_or_unknown_verb_or_dataset_exits_2(self, argv, monkeypatch):
        script = import_script("jma")
        for name in CLASSES:
            monkeypatch.setattr(script, name, None)  # calling None would raise TypeError

        with pytest.raises(SystemExit) as exc:
            script.main(argv)

        assert exc.value.code == 2

    @pytest.mark.parametrize(
        "argv, phrase",
        [
            (["-h"], "{download,load}"),
            (["download", "-h"], "{hourly,normals,msm_surface_forecast,stations}"),
            (["load", "-h"], "{hourly,normals,msm_surface_forecast}"),
            (["download", "hourly", "-h"], "The scrape is resumable"),
            # One word: argparse wraps a flag's help at the column, a description never.
            (["load", "normals", "-h"], "manifest"),
        ],
    )
    def test_help_at_every_level_exits_0(self, argv, phrase, capsys):
        script = import_script("jma")

        with pytest.raises(SystemExit) as exc:
            script.main(argv)

        out = capsys.readouterr().out
        assert exc.value.code == 0
        assert out.startswith("usage: jma")
        assert phrase in out


# --------------------------------------------------------------------------- the lazy import

GRIB = "power_market_analytics.ingestion.msm.grib"
DOWNLOAD = "power_market_analytics.ingestion.msm.download"


def block_eccodes(monkeypatch):
    """Make ``import eccodes`` fail and forget the two msm modules that reach it.

    ``None`` in ``sys.modules`` makes the import raise ``ImportError``; the two entries are
    removed so a fresh import runs their module code under the block. monkeypatch restores
    all three afterwards.
    """
    monkeypatch.setitem(sys.modules, "eccodes", None)
    for name in (GRIB, DOWNLOAD):
        monkeypatch.delitem(sys.modules, name, raising=False)


class TestMsmDownloaderImportIsLazy:
    def test_the_script_imports_without_eccodes(self, monkeypatch):
        block_eccodes(monkeypatch)
        with pytest.raises(ImportError):  # the control: the block is live
            importlib.import_module(GRIB)

        script = import_script("jma")  # must not raise: msm.download is not imported here

        assert not hasattr(script, "MsmDownloader")

    def test_only_the_msm_download_handler_needs_eccodes(self, monkeypatch):
        block_eccodes(monkeypatch)
        script = import_script("jma")

        with pytest.raises(ImportError):
            script.main(["download", "msm_surface_forecast"])
```

- [ ] **Step 2: Update `tests/test_load_scripts.py`**

Replace the module docstring, the table and the parametrized class; keep `RecordingLoader` and its fixture.

```python
"""CLI wiring tests for every ``load`` subcommand of the source scripts, and the
``scripts/load_*.py`` entry points not yet folded into one.

Each is exercised through ``main(argv)`` with the loader class swapped for a fake that
records its constructor arguments, so the tests pin the default contract / data path /
table literals and the CLI overrides without touching Spark.
"""
```

```python
#: (script stem, the argv before the load flags, loader attribute in the script namespace,
#:  default contract, default data path, default table)
LOAD_COMMANDS = [
    (
        "load_jepx_spot",
        [],
        "CsvLoader",
        "conf/schemas/jepx_spot.yaml",
        "data/jepx/spot",
        "pma_raw.jepx_spot",
    ),
    (
        "load_occto_area_reserve_rate",
        [],
        "CsvLoader",
        "conf/schemas/occto_area_reserve_rate_dad.yaml",
        "data/occto/area_reserve_rate_dad",
        "pma_raw.occto_area_reserve_rate_dad",
    ),
    (
        "load_occto_demand_forecast",
        [],
        "CsvLoader",
        "conf/schemas/occto_demand_forecast_dad.yaml",
        "data/occto/demand_forecast_dad",
        "pma_raw.occto_demand_forecast_dad",
    ),
    (
        "load_tepco_area_demand_generation",
        [],
        "TepcoAreaCsvLoader",
        "conf/schemas/tepco_area_demand_generation_actual.yaml",
        "data/tepco/area_demand_generation/csv",
        "pma_raw.tepco_area_demand_generation_actual",
    ),
    (
        "load_estat_census_population_mesh",
        [],
        "EstatCensusMeshCsvLoader",
        "conf/schemas/estat_census_population_mesh.yaml",
        "data/estat/census_population_mesh",
        "pma_raw.estat_census_population_mesh",
    ),
    (
        "load_tepco_power_usage",
        [],
        "TepcoPowerUsageCsvLoader",
        "conf/schemas/tepco_power_usage_hourly.yaml",
        "data/tepco/power_usage/csv",
        "pma_raw.tepco_power_usage_hourly",
    ),
    (
        "load_kansai_power_usage",
        [],
        "KansaiPowerUsageCsvLoader",
        "conf/schemas/kansai_power_usage_hourly.yaml",
        "data/kansai/power_usage/csv",
        "pma_raw.kansai_power_usage_hourly",
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

#: The grain of each default contract, proving the script read the right file.
CONTRACT_GRAINS = {
    "conf/schemas/jepx_spot.yaml": ["trade_date", "time_code"],
    "conf/schemas/occto_area_reserve_rate_dad.yaml": [
        "target_date",
        "period_end_time",
        "area_name_ja",
    ],
    "conf/schemas/occto_demand_forecast_dad.yaml": ["target_date", "area_name_ja"],
    "conf/schemas/tepco_area_demand_generation_actual.yaml": ["target_date", "time_code"],
    "conf/schemas/estat_census_population_mesh.yaml": ["census_year", "mesh_code"],
    "conf/schemas/tepco_power_usage_hourly.yaml": ["target_date", "hour_start"],
    "conf/schemas/kansai_power_usage_hourly.yaml": ["target_date", "hour_start"],
    "conf/schemas/jma_hourly_staffed.yaml": ["station_id", "observed_at"],
    "conf/schemas/jma_normal_surface_daily.yaml": [
        "normals_period_end_year",
        "station_number",
        "element_code",
        "month",
    ],
    "conf/schemas/jma_msm_surface_forecast.yaml": [
        "station_id",
        "forecast_reference_at_utc",
        "forecast_valid_at_utc",
    ],
}


@pytest.mark.parametrize(
    "stem, command, loader_attr, contract, data, table",
    LOAD_COMMANDS,
    ids=[" ".join([stem, *command]) for stem, command, *_ in LOAD_COMMANDS],
)
class TestLoadCommands:
    def test_defaults(self, monkeypatch, stem, command, loader_attr, contract, data, table):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)

        script.main([*command])

        assert len(RecordingLoader.built) == 1
        built = RecordingLoader.built[0]
        assert isinstance(built["schema"], CsvTableSchema)
        assert built["schema"].grain == CONTRACT_GRAINS[contract]
        assert built["filepath"] == REPO_ROOT / data
        assert built["table"] == table
        assert built["loaded"] is True

    def test_overrides(
        self, tmp_path, monkeypatch, stem, command, loader_attr, contract, data, table
    ):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)
        # A minimal contract file so --schema is proven to be honoured.
        schema_file = tmp_path / "alt.yaml"
        schema_file.write_text("grain: [k]\ncolumns:\n  - {name: k, type: int}\n", encoding="utf-8")

        script.main(
            [
                *command,
                "--schema",
                str(schema_file),
                "--data",
                str(tmp_path / "x.csv"),
                "--table",
                "db.t",
            ]
        )

        built = RecordingLoader.built[0]
        assert built["schema"].grain == ["k"]
        assert [c.name for c in built["schema"].columns] == ["k"]
        assert built["filepath"] == tmp_path / "x.csv"
        assert built["table"] == "db.t"
        assert built["loaded"] is True

    def test_missing_schema_file_fails_before_loading(
        self, tmp_path, monkeypatch, stem, command, loader_attr, contract, data, table
    ):
        script = import_script(stem)
        monkeypatch.setattr(script, loader_attr, RecordingLoader)
        with pytest.raises(FileNotFoundError):
            script.main([*command, "--schema", str(tmp_path / "nope.yaml")])
        assert RecordingLoader.built == []


def test_repo_root_constant_points_at_the_checkout():
    for stem in ("load_jepx_spot", "jma"):
        assert import_script(stem).REPO_ROOT == REPO_ROOT
        assert isinstance(import_script(stem).REPO_ROOT, Path)
```

Delete `class TestLoadJmaHourly` (its FORMATS test and the `--data-dir` / `--schema-dir` test go with the flags).

- [ ] **Step 3: Move `TestDefaultEndDate` into `tests/test_msm.py`**

Add the import `from power_market_analytics.ingestion.msm import vintage as msm_vintage` next to the existing `from power_market_analytics.ingestion.msm.vintage import (` block, and append the class verbatim from `tests/test_msm_scripts.py` lines 24–48:

```python
class TestDefaultEndDate:
    def test_is_jst_today_plus_one_day(self, monkeypatch):
        frozen = datetime.datetime(2026, 8, 21, 23, 59, tzinfo=msm_vintage.JST)
        monkeypatch.setattr(msm_vintage, "_now", lambda: frozen)

        assert msm_vintage.default_end_date() == datetime.date(2026, 8, 22)

    def test_uses_jst_not_the_naive_calendar_date(self, monkeypatch):
        # 2026-08-21 15:30 UTC == 2026-08-22 00:30 JST, so "today" is already
        # the 22nd in JST even though a naive UTC read would still say 21st.
        frozen = datetime.datetime(2026, 8, 22, 0, 30, tzinfo=msm_vintage.JST)
        monkeypatch.setattr(msm_vintage, "_now", lambda: frozen)

        assert msm_vintage.default_end_date() == datetime.date(2026, 8, 23)

    def test_real_now_returns_a_date_in_the_future(self):
        # No monkeypatch: exercises the real _now() seam. The reference date
        # is read once, strictly before default_end_date() makes its own
        # (possibly later) _now() call — so the result is always at least
        # one full day ahead of this reference, even if midnight JST falls
        # between the two reads; comparing against a *second*, later now()
        # read would be flaky right at that boundary.
        reference_date = datetime.datetime.now(msm_vintage.JST).date()

        assert msm_vintage.default_end_date() > reference_date
```

In `tests/test_jma_loader.py` line 5, replace ``named the way ``scripts/download_jma_hourly.py`` names them`` with ``named the way ``JmaHourlyDownloader.path_for`` names them``.

- [ ] **Step 4: Run the changed tests to verify they fail**

Run: `uv run pytest tests/test_jma_scripts.py tests/test_load_scripts.py tests/test_msm.py -q -p no:cacheprovider --no-cov`
Expected: the `jma` tests FAIL with `FileNotFoundError: … scripts/jma.py` (from `import_script`); the old `load_*` rows and `TestDefaultEndDate` pass.

- [ ] **Step 5: Write `scripts/jma.py`**

```python
"""JMA: download the hourly observations, the normals, the MSM forecast and the station
master, and load the first three into pma_raw.

    just jma download hourly|normals|msm_surface_forecast|stations [flags]
    just jma load hourly|normals|msm_surface_forecast [flags]

``-h`` at any level lists what is under it. The loads need the devcontainer's Spark
session; the downloads run host-side too (``uv run python scripts/jma.py download …``).
"""

import argparse
import csv
import datetime
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.cli import add_load_arguments, add_subcommand, load_from_args
from power_market_analytics.ingestion.jma.hourly import SCRAPE_ELEMENTS, JmaHourlyDownloader
from power_market_analytics.ingestion.jma.load import JmaHourlyCsvLoader
from power_market_analytics.ingestion.jma.normals import (
    VINTAGES,
    JmaNormalsCsvLoader,
    JmaNormalsDownloader,
)
from power_market_analytics.ingestion.jma.stations import JmaStationMasterDownloader
from power_market_analytics.ingestion.msm.load import MsmForecastCsvLoader
from power_market_analytics.ingestion.msm.stations import load_stations
from power_market_analytics.ingestion.msm.vintage import DEFAULT_BACKFILL_START, default_end_date

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "dbt/seeds/jma_stations.csv"
CONFIGURED_NORMALS_YEARS = [vintage.period_end_year for vintage in VINTAGES]

#: Consecutive failures after which the hourly download aborts (server refusing us).
MAX_CONSECUTIVE_FAILURES = 10


# --------------------------------------------------------------------------- download hourly


def build_plan(
    stations_csv: Path,
    start_year: int,
    end_year: int,
    limit: int | None,
    prefectures: list[int] | None = None,
    stations: list[str] | None = None,
) -> list[tuple[str, int]]:
    """Plan the (station_id, year) downloads from the station master.

    Parameters
    ----------
    stations_csv : pathlib.Path
        Station master CSV written by ``JmaStationMasterDownloader``.
    start_year : int
        First calendar year to download.
    end_year : int
        Last calendar year to download.
    limit : int, optional
        Keep only the first ``limit`` stations (in file order, after the
        prefecture and station filters) — for test runs.
    prefectures : list of int, optional
        Keep only stations in these prefecture (``pd``) codes, e.g. ``[44]``
        for 東京 (docs/JMA-Weather-Data-Retrieval.md Appendix A). ``None``
        keeps every station.
    stations : list of str, optional
        Keep only these station ids, e.g. ``["s47662"]``, applied after
        ``prefectures``. ``None`` keeps every station.

    Returns
    -------
    list of (str, int)
        One entry per station and year, station-major. Stations whose
        observations ended before ``start_year`` are excluded; discontinued
        stations only contribute years up to their end date.

    Raises
    ------
    ValueError
        If ``prefectures`` or ``stations`` matches no station (e.g. a typo'd code
        or id).
    """
    with open(stations_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if prefectures is not None:
        rows = [s for s in rows if int(s["prefecture_code"]) in prefectures]
        if not rows:
            raise ValueError(
                f"No stations in prefecture codes {prefectures}; see "
                "docs/JMA-Weather-Data-Retrieval.md Appendix A for valid codes"
            )
    if stations is not None:
        rows = [s for s in rows if s["station_id"] in stations]
        if not rows:
            raise ValueError(
                f"No stations with ids {stations}; ids are the station_id column of {stations_csv}"
            )
    if limit is not None:
        rows = rows[:limit]

    plan: list[tuple[str, int]] = []
    skipped = 0
    for station in rows:
        last_year = end_year
        if station["observation_ended_on"]:
            ended = datetime.date.fromisoformat(station["observation_ended_on"])
            if ended.year < start_year:
                skipped += 1
                continue
            last_year = min(last_year, ended.year)
        plan.extend((station["station_id"], year) for year in range(start_year, last_year + 1))
    logger.info(
        "Planned {} station-years across {} stations ({} stations ended "
        "before {} and were skipped)",
        len(plan),
        len(rows) - skipped,
        skipped,
        start_year,
    )
    return plan


def download_hourly(args: argparse.Namespace) -> None:
    """Download JMA hourly weather CSVs for every staffed station in the master.

    Walks the station master (downloading it first if absent, restricted to
    staffed stations only — 気象官署, ``s``-prefixed ids; the 2026-08 re-scope,
    docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md — inside
    a JEPX area, i.e. excluding Okinawa, Antarctica and 南鳥島), plans
    one request-set per station and calendar year for the 7-element scrape set
    (``SCRAPE_ELEMENTS``: 気温・降水量・風向風速・日照時間・積雪の深さ・相対湿
    度・全天日射量, docs/JMA-Weather-Data-Retrieval.md §6.3) — 8 value columns,
    which exceeds JMA's per-request data-volume budget, so each station-year is
    fetched as 2 request windows stitched into one file — and downloads each
    missing file. ``--prefecture`` and ``--station`` narrow the plan. Stations
    whose observations ended before the window are skipped, and discontinued
    stations only get years up to their end date.

    The scrape is resumable: existing year files are served from the cache, so
    re-running after an interruption continues where it left off. A current-year
    file is re-downloaded only when it was written before today; to refetch any
    other file, delete it. Failures are logged and skipped (the next run retries
    them, since no file is written), but ten consecutive failures abort the run —
    that pattern means JMA is refusing us, and hammering on regardless would be
    impolite.

    The full staffed network (~149 stations x 11 years x 2 windows/station-year
    ≈ 3,450 requests) takes roughly 14 hours cold at the observed ~15-second
    per-request pace (server response time, ~10 s per file, dominates the 5-second
    spacing floor), so run it detached, e.g. host-side:

        nohup uv run python scripts/jma.py download hourly > jma_scrape.log 2>&1 &
    """
    JmaStationMasterDownloader(
        dest=args.stations_csv, staffed_only=True, jepx_areas_only=True
    ).download()
    plan = build_plan(
        args.stations_csv,
        args.start_year,
        args.end_year,
        args.limit,
        prefectures=args.prefecture,
        stations=args.station,
    )

    downloader = JmaHourlyDownloader(data_dir=args.data_dir, request_interval=args.request_interval)
    today = datetime.date.today()
    to_fetch = sum(
        1
        for station_id, year in plan
        if not downloader.path_for(station_id, SCRAPE_ELEMENTS, year).exists()
    )
    # Server response time (~10 s per file) usually dominates the request
    # interval, so the spacing-based figure is a lower bound.
    requests_per_year = downloader.window_count(SCRAPE_ELEMENTS, today.year)
    logger.info(
        "{} of {} station-years not yet downloaded (~{} requests); at least "
        "{:.1f} h at {:.0f} s spacing (~{:.0f} h at the observed ~15 s/request)",
        to_fetch,
        len(plan),
        to_fetch * requests_per_year,
        to_fetch * requests_per_year * args.request_interval / 3600,
        args.request_interval,
        to_fetch * requests_per_year * 15 / 3600,
    )
    if args.dry_run:
        logger.info("Dry run: would download {} of {} station-years", to_fetch, len(plan))
        return

    failures: list[tuple[str, int, str]] = []
    consecutive_failures = 0
    for i, (station_id, year) in enumerate(plan, start=1):
        dest = downloader.path_for(station_id, SCRAPE_ELEMENTS, year)
        # Refresh a current-year file only if it predates today; past years
        # are immutable and always served from the cache.
        force = (
            year == today.year
            and dest.exists()
            and datetime.date.fromtimestamp(dest.stat().st_mtime) < today
        )
        try:
            downloader.download(station_id, SCRAPE_ELEMENTS, year, force=force)
            consecutive_failures = 0
        except Exception as exc:
            failures.append((station_id, year, str(exc)))
            consecutive_failures += 1
            logger.error("FAILED {} {}: {}", station_id, year, exc)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "{} consecutive failures — aborting; JMA appears to be "
                    "refusing requests. Re-run later to resume.",
                    consecutive_failures,
                )
                break
        if i % 100 == 0:
            logger.info("Progress: {}/{} station-years", i, len(plan))

    logger.info("Done: {}/{} station-years ok", len(plan) - len(failures), len(plan))
    if failures:
        logger.error("{} failures (re-run to retry):", len(failures))
        for station_id, year, message in failures[:20]:
            logger.error("  {} {}: {}", station_id, year, message)
        raise SystemExit(1)


# --------------------------------------------------------------------------- download normals


def download_normals(args: argparse.Namespace) -> None:
    """Download JMA's climatological normals (平年値): the daily file of every staffed station.

    For each configured normals period (``power_market_analytics.ingestion.jma.normals.VINTAGES``:
    1991–2020, in use since 2021-05-19) the version is read off the 平年値ダウンロード
    page, ``normal_surface.zip`` (20 MB, one request) is downloaded and validated —
    the station index plus exactly 157 daily files — and the daily files, the index
    and a manifest are written under ``{data-dir}/{period end year}/``. The zip is
    always re-downloaded: JMA replaces it under the same URL when a new version
    comes out, and nothing inside names the version.
    """
    downloader = JmaNormalsDownloader(data_dir=args.data_dir, timeout=args.timeout)
    paths = downloader.download_all(years=args.years)
    logger.info("Extracted {} daily file(s) under {}", len(paths), args.data_dir)


# --------------------------------------------------------------------------- download msm_surface_forecast


def download_msm_surface_forecast(args: argparse.Namespace) -> None:
    """Download JMA MSM GPV surface forecasts from the Kyoto University RISH GRIB2 mirror.

    Each delivery day costs one archive of three GRIB2 files, roughly 157 MB
    total (~54 GiB for a full year); downloads are sequential and throttled
    (``power_market_analytics.ingestion.msm.download.MsmDownloader``) out of politeness toward
    RISH, an academic mirror with no published rate limit of its own — a full
    historical backfill is correspondingly slow and should be run detached.

    For every delivery day D in ``[--start-date, --end-date]`` (default:
    ``DEFAULT_BACKFILL_START`` through ``default_end_date()``, JST "today" + 1
    day), downloads and decodes the three GRIB2 files covering D
    (``power_market_analytics.ingestion.msm.vintage.source_files_for``) into one gzip CSV extract
    under ``--data-dir/csv/``, reusing an already-cached extract unless
    ``--force``. The three GRIB2 files are deleted after a successful extract
    unless ``--keep-grib``.

    TLS needs no setup: RISH has sent an incomplete certificate chain since
    2026-05-28, and the downloader's default session trusts the missing
    intermediate CA directly (``power_market_analytics.ingestion.msm.download.default_session``).
    """
    # Imported here, not at the top: msm.download reaches eccodes through msm.grib,
    # and no other jma subcommand needs it — the loader least of all (spec decision 8;
    # tests/test_jma_scripts.py::TestMsmDownloaderImportIsLazy pins it).
    from power_market_analytics.ingestion.msm.download import MsmDownloader

    stations = load_stations(
        REPO_ROOT / "dbt/seeds/jma_stations.csv",
        REPO_ROOT / "dbt/seeds/jma_station_areas.csv",
    )
    downloader = MsmDownloader(data_dir=args.data_dir)
    paths = downloader.download_range(
        args.start_date,
        args.end_date,
        stations,
        force=args.force,
        keep_grib=args.keep_grib,
    )
    logger.info("Extracted {} delivery day(s) under {}", len(paths), args.data_dir)


# --------------------------------------------------------------------------- download stations


def download_stations(args: argparse.Namespace) -> None:
    """Regenerate the JMA station master dbt seed (staffed stations only).

    Scrapes the station master (id, name, kana, prefecture, coordinates,
    elevation, observed-element mask, end-of-observation date) from the JMA
    obsdl per-prefecture station pages, keeps only staffed stations (気象官署,
    s-prefixed ids — the 2026-08 re-scope; see
    docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md) inside a
    JEPX area (dropping Okinawa, Antarctica and 南鳥島, so every station maps to
    a dim_area row) and rewrites dbt/seeds/jma_stations.csv as UTF-8 with ISO
    dates. Roughly 60 requests at polite spacing, so expect ~5 minutes.
    dim_jma_station is built from this seed joined to the jma_station_areas
    seed, which must have a row for every station id written here.
    """
    downloader = JmaStationMasterDownloader(dest=args.dest, staffed_only=True, jepx_areas_only=True)
    # Always refresh: the point of this command is to pick up new stations and
    # discontinuations, so the cached copy must never be served.
    path = downloader.download(force=True)
    logger.info("Station master seed written to {}", path)


# --------------------------------------------------------------------------- load


def load_hourly(args: argparse.Namespace) -> None:
    """Load the downloaded JMA hourly CSVs into the warehouse (full reload).

    The 7-element staffed-station scrape set (降水量+気温+風向・風速+日照時間+
    積雪の深さ+相対湿度+全天日射量, codes 101-201-301-401-501-605-610) is a
    single fixed 27-column layout, loaded through one contract into one raw
    table. Files are matched by name:
    ``s{station}_101-201-301-401-501-605-610_{year}.csv``. The loader's
    column-count check (contract vs. first data row) guards against JMA
    layout drift.

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jma load hourly``.
    """
    load_from_args(args, JmaHourlyCsvLoader)


def load_normals(args: argparse.Namespace) -> None:
    """Load the extracted JMA daily normals files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jma load normals``.
    """
    load_from_args(args, JmaNormalsCsvLoader)


def load_msm_surface_forecast(args: argparse.Namespace) -> None:
    """Load the extracted MSM GPV surface-forecast csv.gz files into the warehouse (full reload).

    Needs the devcontainer's Spark session (the shared Hive metastore from
    ``SPARK_CONF_DIR``): ``just jma load msm_surface_forecast``. eccodes is not
    needed: the loader imports ``ingestion.loader`` alone.
    """
    load_from_args(args, MsmForecastCsvLoader)


# --------------------------------------------------------------------------- the parser


def build_parser() -> argparse.ArgumentParser:
    """Build the ``jma`` parser: ``download`` and ``load``, a subcommand per dataset under each."""
    parser = argparse.ArgumentParser(
        prog="jma", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    download = verbs.add_parser(
        "download", help="Fetch a dataset's files from JMA (RISH's mirror for the MSM forecast)"
    )
    downloads = download.add_subparsers(dest="dataset", required=True)

    hourly = add_subcommand(
        downloads,
        "hourly",
        download_hourly,
        help="Hourly observations of every staffed station, one stitched file per station-year",
    )
    hourly.add_argument(
        "--stations-csv",
        type=Path,
        default=Path("dbt/seeds/jma_stations.csv"),
        help=(
            "Station master CSV (the dbt seed; downloaded automatically if "
            "absent, refreshed by `jma download stations`)."
        ),
    )
    hourly.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/hourly"),
        help="Directory where hourly CSV files are stored.",
    )
    hourly.add_argument(
        "--start-year",
        type=int,
        default=JmaHourlyDownloader.EARLIEST_YEAR,
        help="First calendar year to download.",
    )
    hourly.add_argument(
        "--end-year",
        type=int,
        default=datetime.date.today().year,
        help="Last calendar year to download.",
    )
    hourly.add_argument(
        "--prefecture",
        type=int,
        nargs="+",
        default=None,
        metavar="PD",
        help=(
            "Only stations in these prefecture codes, e.g. --prefecture 44 "
            "for 東京 (codes: docs/JMA-Weather-Data-Retrieval.md Appendix A)."
        ),
    )
    hourly.add_argument(
        "--station",
        nargs="+",
        default=None,
        metavar="ID",
        help="Only these station ids, e.g. --station s47662 for 東京; applied after --prefecture.",
    )
    hourly.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N stations of the master (for testing).",
    )
    hourly.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and report what would be downloaded, without downloading.",
    )
    hourly.add_argument(
        "--request-interval",
        type=float,
        default=5.0,
        help="Minimum seconds between consecutive HTTP requests.",
    )

    normals = add_subcommand(
        downloads,
        "normals",
        download_normals,
        help="The 1991–2020 climatological normals (平年値): the daily file of every staffed station",
    )
    normals.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_NORMALS_YEARS,
        default=CONFIGURED_NORMALS_YEARS,
        metavar="YEAR",
        help=f"Period end years to fetch (configured: {CONFIGURED_NORMALS_YEARS}); defaults to all.",
    )
    normals.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/normals"),
        help="Root directory; each period gets {year}/zip, {year}/csv and a manifest.",
    )
    normals.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP request timeout in seconds.",
    )

    msm = add_subcommand(
        downloads,
        "msm_surface_forecast",
        download_msm_surface_forecast,
        help="The MSM GPV surface forecast per delivery day, decoded from RISH's GRIB2 mirror",
    )
    msm.add_argument(
        "--start-date",
        type=datetime.date.fromisoformat,
        default=DEFAULT_BACKFILL_START,
        help="First delivery day to extract, inclusive (YYYY-MM-DD).",
    )
    msm.add_argument(
        "--end-date",
        type=datetime.date.fromisoformat,
        default=default_end_date(),
        help="Last delivery day to extract, inclusive (YYYY-MM-DD); defaults to JST today + 1 day.",
    )
    msm.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/msm_surface_forecast"),
        help="Root directory for GRIB2 downloads (grib/) and csv.gz extracts (csv/).",
    )
    msm.add_argument(
        "--force",
        action="store_true",
        help="Re-download every GRIB2 file and rebuild the extract, even for a cached day.",
    )
    msm.add_argument(
        "--keep-grib",
        action="store_true",
        help="Keep the downloaded GRIB2 files after extraction (deleted by default).",
    )

    stations = add_subcommand(
        downloads,
        "stations",
        download_stations,
        help="The station master, rewritten as the dbt seed jma_stations.csv (~60 requests, ~5 min)",
    )
    stations.add_argument(
        "--dest",
        type=Path,
        default=SEED_PATH,
        help="Where to write the station master CSV (default: the dbt seed).",
    )

    load = verbs.add_parser(
        "load", help="Load a dataset's files into pma_raw (full reload; the devcontainer's Spark)"
    )
    loads = load.add_subparsers(dest="dataset", required=True)

    load_hourly_parser = add_subcommand(
        loads, "hourly", load_hourly, help="The stitched 7-element files → pma_raw.jma_hourly_staffed"
    )
    add_load_arguments(
        load_hourly_parser,
        schema=REPO_ROOT / "conf/schemas/jma_hourly_staffed.yaml",
        data=REPO_ROOT / "data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv",
        table="pma_raw.jma_hourly_staffed",
        data_help="The downloaded hourly files: a glob pattern, a single file or a directory.",
    )

    load_normals_parser = add_subcommand(
        loads,
        "normals",
        load_normals,
        help="The extracted daily normals files → pma_raw.jma_normal_surface_daily",
    )
    add_load_arguments(
        load_normals_parser,
        schema=REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml",
        data=REPO_ROOT / "data/jma/normals",
        table="pma_raw.jma_normal_surface_daily",
        data_help=(
            "Downloader root ({period end year}/csv/daily/*.csv underneath), a single daily "
            "file, or a glob pattern to load; the manifest is read two levels up."
        ),
    )

    load_msm_parser = add_subcommand(
        loads,
        "msm_surface_forecast",
        load_msm_surface_forecast,
        help="The csv.gz extracts → pma_raw.jma_msm_surface_forecast",
    )
    add_load_arguments(
        load_msm_parser,
        schema=REPO_ROOT / "conf/schemas/jma_msm_surface_forecast.yaml",
        data=REPO_ROOT / "data/jma/msm_surface_forecast/csv",
        table="pma_raw.jma_msm_surface_forecast",
        data_help="Downloader csv/ directory, a single csv.gz file, or a glob pattern to load.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the changed tests to verify they pass**

Run: `uv run pytest tests/test_jma_scripts.py tests/test_load_scripts.py tests/test_msm.py tests/test_ingestion_cli.py -q -p no:cacheprovider --no-cov`
Expected: all pass. If `test_help_at_every_level_exits_0[download hourly -h]` fails on the phrase, the docstring lost "The scrape is resumable"; if `[load normals -h]` fails, the `data_help` lost the word "manifest".

- [ ] **Step 7: Delete the eight scripts and the two test files**

```bash
git rm -q scripts/download_jma_hourly.py scripts/download_jma_hourly_all.py scripts/download_jma_msm_surface_forecast.py scripts/download_jma_normals.py scripts/load_jma_hourly.py scripts/load_jma_msm_surface_forecast.py scripts/load_jma_normals.py scripts/update_jma_stations_seed.py tests/test_jma_normals_scripts.py tests/test_msm_scripts.py
```

- [ ] **Step 8: Run the whole suite for the coverage gate, then lint and mypy**

Run: `just test -q -p no:cacheprovider`
Expected: every test passes and the coverage report ends `TOTAL … 100%`; `scripts/jma.py` shows no missing lines but the `if __name__` guard, which is excluded. A missing line in `scripts/jma.py` means a handler or branch no test reaches — add the test, do not exclude the line.

Run: `just lint` and `just mypy` — clean.

- [ ] **Step 9: Commit**

```bash
git add scripts/jma.py tests/test_jma_scripts.py tests/test_load_scripts.py tests/test_msm.py tests/test_jma_loader.py
git commit -m "refactor(scripts): one jma command with download and load subcommands

scripts/jma.py replaces the eight JMA scripts: download hourly (the
all-stations scrape, with --station in place of the per-station script),
normals, msm_surface_forecast (MsmDownloader imported in the handler, so
no other subcommand needs eccodes) and stations; load hourly, normals and
msm_surface_forecast through the shared load helper. Tests keep their seam
with the verb and dataset in front of the argv.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The justfile

**Files:**
- Modify: `justfile` (the `python` recipe's doc, a new `jma` recipe after it, the JMA lines of `refresh-all` and its comment)

- [ ] **Step 1: Edit the recipes**

Replace the `python` recipe's doc line:

```
[doc("Run python inside the devcontainer (e.g. just python scripts/demand_backtest.py --area tokyo)")]
```

Insert after the `python` recipe:

```
[doc("JMA, inside the devcontainer: just jma download hourly|normals|msm_surface_forecast|stations … / just jma load hourly|normals|msm_surface_forecast …; -h at any level lists what is under it")]
jma *args:
    @just python scripts/jma.py "$@"
```

Replace the comment above `refresh-all`:

```
# One refresh recipe covers every source. A single source is refreshed by its source command's
# download and load subcommands (JMA: `just jma …`; the other sources' download + load scripts
# through `just python` until they are folded the same way) and then `just dbt build`.
# JMA runs before MSM because the MSM downloader reads the station seed.
```

In `refresh-all`, replace the JMA lines:

```
    just jma download stations
    just jma download hourly
    just jma load hourly

    just jma download normals
    just jma load normals
```

and the MSM lines:

```
    just jma download msm_surface_forecast
    just jma load msm_surface_forecast
```

- [ ] **Step 2: Check the recipe parses and reads the tree**

Run: `just --list` — `jma` appears with its doc. Run: `just --dry-run jma download hourly --dry-run` — prints `just python scripts/jma.py download hourly --dry-run` (the recipe's one line) without running it. The in-container run itself is Task 6.

- [ ] **Step 3: Commit**

```bash
git add justfile
git commit -m "build(justfile): the jma recipe, and refresh-all through it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The docs, the YAML descriptions and the two docstrings

**Files:**
- Modify: `CLAUDE.md`, `docs/Development.md`, `docs/JMA-Weather-Data-Retrieval.md`, `docs/JMA-MSM-GPV-Retrieval.md`, `docs/JMA-Climatological-Normals-Retrieval.md`, `dbt/models/raw/jma.yml`, `dbt/models/curated/dim_jma_station.yml`, `conf/schemas/jma_msm_surface_forecast.yaml`, `conf/schemas/jma_normal_surface_daily.yaml`, `power_market_analytics/ingestion/msm/vintage.py`, `power_market_analytics/ingestion/jma/normals.py`, `docs/superpowers/README.md`

The rule: where a document tells the reader to run something, the command is `just jma <verb> <dataset> [flags]` (host-side: `uv run python scripts/jma.py <verb> <dataset>`); where it names the code, `scripts/jma.py`. Line numbers are those of `main` at `caeb027`.

- [ ] **Step 1: `CLAUDE.md`**

Commands section:

1. Lines 12–13, the intro of the single-source bullet, become:

```
- A single source is refreshed by its download and load commands, then `just dbt build`. JMA
  is one command since 2026-09-28 — `scripts/jma.py`, `just jma <download|load> <dataset>`,
  `-h` at any level (spec `docs/superpowers/specs/2026-09-28-source-commands-design.md`); the
  other five sources still run their download + load scripts (under `scripts/`) through
  `just python` until they are folded the same way:
```

2. Lines 15–19, the JMA hourly bullet, become:

```
  - JMA hourly: `just jma download stations` (~5 min, staffed stations inside JEPX areas only;
    rewrites the seed), `just jma download hourly` (stitched 7-element hourly CSVs; e.g.
    `--prefecture 44` or `--station s47662`; no args = all ~149 staffed stations, ~13.5 h cold;
    `--request-interval 3` is proven — the 2026-08-20 full backfill, ~3,100 requests, zero
    429s, ~6 h — while 2 s spacing draws 429s), `just jma load hourly`. The per-station
    `download_jma_hourly.py` and its `--elements` went with the consolidation; another element
    set is `JmaHourlyDownloader.download` in Python.
```

3. Lines 20–24, the normals bullet: `download_jma_normals.py` → `just jma download normals`; `load_jma_normals.py` → `just jma load normals`.

4. Lines 45–56, the MSM bullet: `download_jma_msm_surface_forecast.py` → `just jma download msm_surface_forecast`; `load_jma_msm_surface_forecast.py` → `just jma load msm_surface_forecast`; and the sentence ending `the loader imports `ingestion.loader` alone.` gains: ` `scripts/jma.py` imports `MsmDownloader` inside that one handler, so no other `jma` subcommand needs eccodes (a test blocks eccodes and imports the script).`

5. After the bullet `- `just python <args>` / `just exec <cmd>` / `just shell` — run inside the devcontainer.` add:

```
- `just jma <verb> <dataset> [flags]` (since 2026-09-28) — the JMA source command in the
  devcontainer, `scripts/jma.py`: `download hourly|normals|msm_surface_forecast|stations`,
  `load hourly|normals|msm_surface_forecast`; sugar over `just python scripts/jma.py`.
```

Architecture section:

6. Line 562: `` `scripts/download_jma_hourly_all.py` (per-station: `download_jma_hourly.py`; `` → `` `just jma download hourly` (`scripts/jma.py`, `--station` for one station; ``.
7. Line 565: `` `scripts/load_jma_hourly.py` `` → `` `just jma load hourly` ``.
8. Line 570: `` `scripts/update_jma_stations_seed.py` `` → `` `just jma download stations` ``.
9. Line 578: `` `scripts/download_jma_msm_surface_forecast.py` `` → `` `just jma download msm_surface_forecast` ``.
10. Line 585: `` `scripts/load_jma_msm_surface_forecast.py` `` → `` `just jma load msm_surface_forecast` ``.
11. Line 596: `` `scripts/download_jma_normals.py` `` → `` `just jma download normals` ``.
12. Line 602: `` `scripts/load_jma_normals.py` `` → `` `just jma load normals` ``.

- [ ] **Step 2: `docs/Development.md` lines 35–36**

```
just jma download hourly --prefecture 44   # one source's download + load commands (scripts/<source>.py; the pairs in CLAUDE.md), then...
just jma load hourly && just dbt build     # ...rebuild + test dbt
```

- [ ] **Step 3: `docs/JMA-Weather-Data-Retrieval.md`**

1. Line 201–202: `` (the default in `scripts/update_jma_stations_seed.py`) `` → `` (the default in `just jma download stations`) ``.
2. Lines 213–214: `` passed by both `scripts/update_jma_stations_seed.py` and `scripts/download_jma_hourly_all.py`. `` → `` passed by both `jma download stations` and `jma download hourly` (`scripts/jma.py`). ``.
3. Lines 266–267: `` (SCD1, regenerated by `scripts/update_jma_stations_seed.py`) `` → `` (SCD1, regenerated by `just jma download stations`) ``.
4. Lines 577–585 become:

````
The CLI downloads a station's full history of the scrape set (past years cached, the
current year refreshed when its file predates today); another element set is a Python
call as above:

```bash
# Host (no SparkSession involved):
uv run python scripts/jma.py download hourly --station s47662
# Or in the devcontainer:
just jma download hourly --station s47662
```
````

5. Line 593: `uv run python scripts/update_jma_stations_seed.py` → `uv run python scripts/jma.py download stations`.
6. Line 604: `` `scripts/download_jma_hourly_all.py` orchestrates `` → `` `just jma download hourly` (`scripts/jma.py`) orchestrates ``.
7. Lines 616–620 become:

```
nohup uv run python scripts/jma.py download hourly > jma_scrape.log 2>&1 &
# --prefecture 44        only 東京 stations (codes: Appendix A; several codes allowed)
# --station s47662       only these station ids (applied after --prefecture)
# --limit N              only the first N stations (test runs)
# --start-year/--end-year  narrow the year range
# --dry-run              plan and report without downloading
```

8. Line 625: `` `scripts/load_jma_hourly.py` performs `` → `` `just jma load hourly` performs ``.
9. Line 651: `just python scripts/load_jma_hourly.py` → `just jma load hourly`.

- [ ] **Step 4: `docs/JMA-MSM-GPV-Retrieval.md`**

1. Lines 426–427:

```
just jma download msm_surface_forecast [args]
just jma load msm_surface_forecast
```

2. Line 434: `` `scripts/load_jma_msm_surface_forecast.py` reads `` → `` `just jma load msm_surface_forecast` reads ``.
3. Line 440: `` `scripts/download_jma_msm_surface_forecast.py` flags: `` → `` `just jma download msm_surface_forecast` flags: ``.
4. Line 470: `` (`uv run python scripts/download_jma_msm_surface_forecast.py ...`, `` → `` (`uv run python scripts/jma.py download msm_surface_forecast ...`, ``.
5. Lines 476–478, the sentence `Verified by importing each script with `eccodes` made unimportable: the load script imports, the download script raises.` becomes: `` `scripts/jma.py` imports `MsmDownloader` inside the download handler only, so `jma load msm_surface_forecast` never reaches `msm.grib` either. Verified by a test that makes `eccodes` unimportable: the script imports, and `download msm_surface_forecast` raises when it runs (`tests/test_jma_scripts.py::TestMsmDownloaderImportIsLazy`). ``

- [ ] **Step 5: `docs/JMA-Climatological-Normals-Retrieval.md` lines 179–186**

```bash
# The page (version), the zip (20 MB), the 157 daily files. Host-side:
uv run python scripts/jma.py download normals
# or in the devcontainer:
just jma download normals                                  # every configured period
just jma download normals --years 2020 --data-dir data/jma/normals

# Devcontainer: 152,604 rows into pma_raw.jma_normal_surface_daily, seconds.
just jma load normals
just dbt build --select jma_normal_elements stg_jma__normal_surface_daily+
```

- [ ] **Step 6: The YAML descriptions and the two docstrings**

1. `dbt/models/raw/jma.yml` line 7: `scripts/download_jma_hourly.py and loaded by scripts/load_jma_hourly.py` → `` `just jma download hourly` and loaded by `just jma load hourly` (scripts/jma.py) ``; line 134: `(scripts/download_jma_msm_surface_forecast.py)` → `(just jma download msm_surface_forecast)`; line 137: `(scripts/load_jma_msm_surface_forecast.py)` → `(just jma load msm_surface_forecast)`.
2. `dbt/models/curated/dim_jma_station.yml` line 14: `(regenerated by scripts/update_jma_stations_seed.py)` → `(regenerated by just jma download stations)`.
3. `conf/schemas/jma_msm_surface_forecast.yaml` line 11: `(scripts/download_jma_msm_surface_forecast.py)` → `(just jma download msm_surface_forecast)`; line 14: `(scripts/load_jma_msm_surface_forecast.py)` → `(just jma load msm_surface_forecast)`.
4. `conf/schemas/jma_normal_surface_daily.yaml` lines 6–7: `downloaded by scripts/download_jma_normals.py and loaded by JmaNormalsCsvLoader (scripts/load_jma_normals.py).` → `downloaded by just jma download normals and loaded by JmaNormalsCsvLoader (just jma load normals; both scripts/jma.py).`
5. `power_market_analytics/ingestion/msm/vintage.py` line 48: ``` ``--end-date`` of ``scripts/download_jma_msm_surface_forecast.py``. ``` → ``` ``--end-date`` of ``jma download msm_surface_forecast`` (``scripts/jma.py``). ```
6. `power_market_analytics/ingestion/jma/normals.py` line 471: `run scripts/download_jma_normals.py` → `run `jma download normals``. No test asserts that message text (checked: `grep -rn "no manifest next to" tests/` is empty).

- [ ] **Step 7: The design-history row**

In `docs/superpowers/README.md`, the 2026-09-28 row's Plan cell `—` becomes `[plan](superpowers/plans/2026-09-28-source-commands-jma.md)`.

- [ ] **Step 8: Check**

Run: `just docs-links` — every link resolves. Run: `(cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse)` — the YAML still parses. Run the stale-name check of the spec §7 from the worktree (`git ls-files` into a file first if the guard refuses the pipeline):

```bash
git ls-files -- . ':!docs/superpowers' \
  | xargs grep -IhoE "\b(download|load|update)_[a-z_]+\.py\b|scripts/[a-z_]+\.py" \
  | sed 's#^scripts/##' | sort -u \
  | while read -r f; do [ -f "scripts/$f" ] || echo "stale: $f"; done
```

Expected: no output. Any `stale:` line is a mention Step 1–6 missed — fix it, do not add an exception.

- [ ] **Step 9: Commit**

```bash
git add CLAUDE.md docs/Development.md docs/JMA-Weather-Data-Retrieval.md docs/JMA-MSM-GPV-Retrieval.md docs/JMA-Climatological-Normals-Retrieval.md dbt/models/raw/jma.yml dbt/models/curated/dim_jma_station.yml conf/schemas/jma_msm_surface_forecast.yaml conf/schemas/jma_normal_surface_daily.yaml power_market_analytics/ingestion/msm/vintage.py power_market_analytics/ingestion/jma/normals.py docs/superpowers/README.md
git commit -m "docs(jma): the jma command in CLAUDE.md, the retrieval docs and the source YAML

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Whole-branch review

- [ ] **Step 1: Independent reviewers**, each on the diff `origin/main...HEAD`, with one lens each, findings verified adversarially before any is acted on:
  1. Spec conformance: every row of spec §3.1 against `scripts/jma.py` — flag names, types, defaults, `nargs`, `choices`, the handler bodies against the deleted scripts (`git show origin/main:scripts/<old>.py`); decision 8's lazy import; `prog`; the order of the subcommands.
  2. Tests: the seam (the fakes land where the handlers look), every deleted test's assertion accounted for (moved, or its flag gone), the mutation checks (`test_the_script_imports_without_eccodes` fails when the import moves to the top — try it), the coverage of every branch of `build_plan`.
  3. Docs: every rewritten line reads as a command where it says to run something and as code where it names the file; the stale-name check; the CLAUDE.md bullets' facts unchanged but for the spellings.
- [ ] **Step 2: Fix what is confirmed**, one commit per finding class, then `just test`, `just lint`, `just mypy`, `just docs-links` again.

---

### Task 6: In-container verification (main session, background tasks)

The worktree has no `.env`, so compose runs from the main checkout with the worktree as the working directory. Define the prefix once per command (write it out; the guard refuses shell variables):

```
docker compose -f /Users/hankehly/Projects/power-market-analytics/docker-compose.yaml --project-directory /Users/hankehly/Projects/power-market-analytics exec -T -w /workspace/.claude/worktrees/chore+source-commands -e PYTHONPATH=/workspace/.claude/worktrees/chore+source-commands devcontainer python scripts/jma.py <verb> <dataset> [flags]
```

- [ ] **Step 1: Row counts before**, on `main`'s tables (the warehouse is shared; the commands reload the same tables): `just dbt show --inline "select count(*) as n from pma_raw.jma_hourly_staffed" --limit 1`, the same for `pma_raw.jma_normal_surface_daily` and `pma_raw.jma_msm_surface_forecast`, from the main checkout's `just` (its `.env`). Record the three numbers.
- [ ] **Step 2: The plan counts**: `… scripts/jma.py download hourly --dry-run` against `git show origin/main:scripts/download_jma_hourly_all.py > scratch/download_jma_hourly_all_main.py` run the same way — the "Planned … station-years" and "Dry run: would download X of Y" lines must be equal. Then `… download hourly --station s47662 --dry-run` (one station, 2016 to this year) and `… download hourly --station nope --dry-run` (`ValueError: No stations with ids ['nope']`, no request).
- [ ] **Step 3: The loads**: `… load normals` → `Loaded 152604 rows`; `… load hourly` → the Step 1 count, ~50 s warm; `… load msm_surface_forecast` → the Step 1 count. Re-read the three counts after.
- [ ] **Step 4: The downloads that cost nothing**: `… download normals` (20 MB, seconds; "Extracted 157 daily file(s)"); `… download msm_surface_forecast --start-date D --end-date D` for a D already under `data/jma/msm_surface_forecast/csv/` ("Extracted 1 delivery day(s)", nothing fetched); `… download stations` (~5 min; then `git diff --stat dbt/seeds/jma_stations.csv` in the worktree is empty).
- [ ] **Step 5: The eccodes property in the container**: `… python -c "import sys; sys.modules['eccodes'] = None; import runpy; sys.argv = ['jma', 'load', 'msm_surface_forecast', '-h']; runpy.run_path('scripts/jma.py', run_name='__main__')"` → the help text, exit 0.
- [ ] **Step 6: The help tree**: `… scripts/jma.py`, `… scripts/jma.py download`, `… scripts/jma.py download hourly -h` — usage naming the verbs; the datasets; the flags.

Keep every command's output lines that carry a number for the PR's Evidence table.

---

### Task 7: The pull request

- [ ] **Step 1: Push and open**: `git push -u origin chore/source-commands`; `gh pr create --title "refactor(scripts): one jma command with download and load subcommands" --body-file scratch/pr-body.md`; `gh pr edit <n> --add-assignee hankehly --add-label chore --add-label ingestion`. The body follows `.github/pull_request_template.md`: Summary (the spec, PR 2 to follow), Changes (one line per file group), Effect on what exists (spec §8's JMA rows), Checks (the gate commands and the stale-name check), folded Decisions (the ones the review changed, if any) and Evidence (Task 6's table: the counts before and after, the plan counts, the timings). Never spell out the Codex mention in the body.
- [ ] **Step 2: Watch the review** — the CLAUDE.md loop: poll `pulls/<n>/reviews`, `issues/<n>/reactions`, `pulls/<n>/comments`, `issues/<n>/comments` every 60 s with `--method GET`; address every finding (a fix, or a reply with evidence), resolve the threads, push, repeat until 👍; CI green on the current head. Then report the PR as ready; the researcher merges.
