# JMA Climatological Normals Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load JMA's 1991–2020 climatological normals (平年値) — the daily file of every staffed station — into the warehouse as a raw table, a long standardized model and two wide facts, `fct_jma_normal_daily` and `fct_jma_normal_hourly`.

**Architecture:** One module, `power_market_analytics/ingestion/jma/normals.py`, holds the vintage config, a downloader (version off the page, `normal_surface.zip` always re-downloaded and validated, 157 daily files + a manifest) and a positional `CsvLoader` subclass (69 fields, the period, version and in-use date injected from the manifest). dbt unpivots the 31 value/flag pairs to one row per station × element × month × day (`std_jma__normal_daily`, scaled by the seed `jma_normal_elements`) and pivots the daily elements and the 24 hourly temperatures into the two facts, joined to `dim_jma_station`.

**Tech Stack:** Python 3 / PySpark 4.1 (`CsvLoader._scan_positional`), requests, pydantic contracts in `conf/schemas/`, dbt 1.11 + dbt-spark (Spark SQL `stack`, `make_date`, `last_day`), pytest with the local `spark` fixture, `uv`, `just`.

**Spec:** `docs/superpowers/specs/2026-09-27-jma-climatological-normals-design.md`

## Global Constraints

- Work in the worktree `.claude/worktrees/feature+jma-climatological-normals`, branch `feature/jma-climatological-normals`. A `cd` inside a compound command moves the session's working directory for every later command, so run dbt from `dbt/` in a subshell — `(cd dbt && uv run dbt parse)` — and never put a shell variable where a command argument goes (the worktree guard refuses it): write literal paths.
- Commits: Conventional Commits, `feat(jma): …` for code, `test(jma): …`, `docs(jma): …`; every message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Python: NumPy-style docstrings; ruff line length 100 (a PostToolUse hook runs `ruff format` + `ruff check --fix` on every edited `.py` — **it strips unused imports**, so add an import in the same edit as its first use); `just lint`, `just mypy` clean; the coverage gate is 100 % over `power_market_analytics/` + `scripts/` (`just test`).
- Tests never touch the network or real time: the downloader takes an injectable `session`; scripts are driven through `main(argv)` with the downloader/loader class swapped via `tests.support.import_script`; loader tests write files into `tmp_path` and load through the real contract with the `spark` fixture.
- dbt: every model has `config: contract: enforced: true` with a `data_type` per column and a uniqueness test on its grain (`dbt_utils.unique_combination_of_columns`); generic test args under `arguments:`; `int` where the contract says int (`div` returns bigint); singular tests live in `dbt/dbt_tests/`; seed column types in `dbt/dbt_project.yml`.
- Names are the spec's: `pma_raw.jma_normal_surface_daily`, `stg_jma__normal_surface_daily`, `std_jma__normal_daily`, `fct_jma_normal_daily`, `fct_jma_normal_hourly`, seed `jma_normal_elements`, `conf/schemas/jma_normal_surface_daily.yaml`, `data/jma/normals/<period end year>/`, scripts `download_jma_normals.py` / `load_jma_normals.py`.
- Writing style (docs, YAML descriptions, PR body): plain short sentences, one idea each; exact names and numbers; no filler.
- `available_at` of every normals row = `in_use_since` at 00:00 (2021-05-19), naive JST — the documented bound.
- The three occurrence rates (codes 3600, 4600, 4700) are percentages as published: scale denominator 1.
- Host-side `dbt parse` from `dbt/` needs no warehouse (`uv run dbt deps` once, then `uv run dbt parse`); `dbt build` and the Spark-backed scripts run in the devcontainer (`just dbt …`, `just python …`), from the worktree path.

## Review Focus

1. A truncated line (fewer than 69 fields, a broken download) — the loader must fail naming the file, not load nulls. Pinned in Task 4 (`short line` case of `TestValidationFailsBeforeWriting`).
2. A negative value (`   -32`, a Hokkaido winter) must parse and scale to −3.2. Pinned in Task 4 (`test_negative_values_parse`) and Task 7's unit test (the 7100 row has none, so the loader test carries it).
3. A page heading in full-width brackets on both sides (`（第5版）`) or a dotted version (`4.0.1`) must parse. Pinned in Task 1 (`TestParseVersions`).
4. An archive whose daily files sit at another path (no `normal_surface/` prefix) must be refused as "0 daily files", not silently loaded from elsewhere. Pinned in Task 2 (the `found 0` case).
5. A second period directory (the 2030 normals, ~2031) must load next to the first with its own manifest values, the grain including the period. Pinned in Task 4 (`test_two_periods_load_side_by_side`).

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/ingestion/jma/normals.py` | `NormalsVintage`, `VINTAGES`, `vintage_for_year`, `parse_versions`; `JmaNormalsDownloader`, `JmaNormalsDownloadError`; `JmaNormalsCsvLoader` |
| `scripts/download_jma_normals.py`, `scripts/load_jma_normals.py` | CLI entry points |
| `conf/schemas/jma_normal_surface_daily.yaml` | positional load contract (69 + 5 columns) |
| `tests/test_jma_normals.py` | vintages, page parse, downloader, loader |
| `tests/test_jma_normals_scripts.py`, `tests/test_load_scripts.py` | script wiring |
| `dbt/seeds/jma_normal_elements.csv`, `dbt/dbt_project.yml` | the 81 element codes and their types |
| `dbt/models/raw/jma.yml` | source `jma_normal_surface_daily` |
| `dbt/models/staging/stg_jma__normal_surface_daily.{sql,yml}` | as-is |
| `dbt/models/standardized/std_jma__normal_daily.{sql,yml}` | unpivot, scale, `available_at`; unit tests |
| `dbt/models/curated/fct_jma_normal_daily.{sql,yml}`, `fct_jma_normal_hourly.{sql,yml}` | the two facts |
| `dbt/dbt_tests/assert_*.sql` (5) | padding cells, derived flags, every element, 366 days, 24 hours |
| `docs/JMA-Climatological-Normals-Retrieval.md`, `docs/_sidebar.md`, `docs/README.md`, `docs/Curated-Star-Schema.md`, `CLAUDE.md`, `justfile`, `power_market_analytics/ingestion/jma/__init__.py`, `docs/superpowers/README.md` | docs and recipes |

---

### Task 1: Vintage config and the page's version heading

**Files:**
- Create: `power_market_analytics/ingestion/jma/normals.py`
- Create: `tests/test_jma_normals.py`

**Interfaces:**
- Produces: `NormalsVintage(period_start_year: int, period_end_year: int, in_use_since: datetime.date, zip_url: str, expected_station_count: int)` (frozen dataclass); `VINTAGES: tuple[NormalsVintage, ...]` (one entry, 1991–2020); `vintage_for_year(period_end_year: int) -> NormalsVintage` (KeyError otherwise); `parse_versions(html: str) -> dict[int, str]`; constants `NORMALS_PAGE_URL`, `STATION_INDEX_MEMBER`, `DAILY_MEMBER_RE`.

- [ ] **Step 1: Write the failing tests**

`tests/test_jma_normals.py`:

```python
"""Tests for the JMA climatological normals (平年値) module.

The vintage config and the page's version heading; the downloader against a fake
session serving a two-station archive built in memory; the positional loader on
files written into ``tmp_path`` in the exact 308-byte daily layout, loaded through
the real ``conf/schemas/jma_normal_surface_daily.yaml`` contract.
"""

from __future__ import annotations

import datetime

import pytest

from power_market_analytics.ingestion.jma.normals import (
    VINTAGES,
    NormalsVintage,
    parse_versions,
    vintage_for_year,
)

# --------------------------------------------------------------------------- vintages


class TestVintages:
    def test_one_configured_period(self):
        assert VINTAGES == (
            NormalsVintage(
                period_start_year=1991,
                period_end_year=2020,
                in_use_since=datetime.date(2021, 5, 19),
                zip_url=(
                    "https://www.data.jma.go.jp/stats/data/mdrr/normal/2020/data/"
                    "normal_surface.zip"
                ),
                expected_station_count=157,
            ),
        )

    def test_lookup_by_period_end_year(self):
        assert vintage_for_year(2020) is VINTAGES[0]

    def test_unknown_year_raises(self):
        with pytest.raises(KeyError, match="2030"):
            vintage_for_year(2030)

    def test_vintage_is_immutable(self):
        with pytest.raises(AttributeError):
            VINTAGES[0].zip_url = "x"  # type: ignore[misc]


# --------------------------------------------------------------------------- the page


class TestParseVersions:
    def test_the_heading_as_jma_writes_it_mixed_brackets(self):
        assert parse_versions("<h2>2020年平年値（第5版)</h2>") == {2020: "5"}

    def test_full_width_brackets_and_a_dotted_version(self):
        assert parse_versions("2020年平年値（第4.0.1版）") == {2020: "4.0.1"}

    def test_two_periods_on_one_page(self):
        html = "<h2>2030年平年値（第1版）</h2> … <h2>2020年平年値（第5版）</h2>"
        assert parse_versions(html) == {2030: "1", 2020: "5"}

    def test_news_items_do_not_match(self):
        # The notices read 2020年平年値の第5版を公開しました — no bracket, not a heading.
        assert parse_versions("2020年平年値の第5版を公開しました。") == {}

    def test_no_heading(self):
        assert parse_versions("<html>maintenance</html>") == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv sync --locked && uv run pytest tests/test_jma_normals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'power_market_analytics.ingestion.jma.normals'`

- [ ] **Step 3: Write the module's first part**

`power_market_analytics/ingestion/jma/normals.py`:

```python
"""JMA climatological normals (平年値): the vintage config, the downloader and the raw loader.

JMA publishes the normals of its staffed stations as one archive,
``normal_surface.zip``, on the 平年値ダウンロード page. Each station's daily file
holds 81 elements × 12 months, one row each, with 31 (value, flag) cells per row
and no header. The page, the archive, the record layout, the element codes, the
versions and the warehouse models are documented in
``docs/JMA-Climatological-Normals-Retrieval.md``.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

#: The 平年値ダウンロード page; its ``<year>年平年値（第<n>版）`` heading names the version.
NORMALS_PAGE_URL = "https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html"

#: The station list inside the archive, extracted for reference and never loaded.
STATION_INDEX_MEMBER = "normal_surface/surface_station_index.csv"

#: One daily file per station inside the archive.
DAILY_MEMBER_RE = re.compile(r"^normal_surface/daily/nml_sfc_d_(?P<station>\d{5})\.csv$")

#: ``2020年平年値（第5版)`` — JMA mixes full-width and ASCII brackets. The notices
#: (``2020年平年値の第5版を公開しました``) have no bracket and do not match.
_HEADING_RE = re.compile(r"(?P<year>\d{4})年平年値[（(]第(?P<version>\d+(?:\.\d+)*)版[)）]")


@dataclass(frozen=True)
class NormalsVintage:
    """One normals period as JMA publishes it.

    Attributes
    ----------
    period_start_year, period_end_year : int
        The 30 years the normals average, e.g. 1991 and 2020.
    in_use_since : datetime.date
        The day JMA put the period's normals into use (2021-05-19 for
        1991–2020); the ``available_at`` of every row of the period.
    zip_url : str
        The surface-observation archive of the period.
    expected_station_count : int
        Daily files the archive must hold; another count fails the download.
    """

    period_start_year: int
    period_end_year: int
    in_use_since: datetime.date
    zip_url: str
    expected_station_count: int


#: Configured periods, oldest first. The 2030 normals become a second entry.
VINTAGES: tuple[NormalsVintage, ...] = (
    NormalsVintage(
        period_start_year=1991,
        period_end_year=2020,
        in_use_since=datetime.date(2021, 5, 19),
        zip_url="https://www.data.jma.go.jp/stats/data/mdrr/normal/2020/data/normal_surface.zip",
        expected_station_count=157,
    ),
)


def vintage_for_year(period_end_year: int) -> NormalsVintage:
    """Return the configured vintage whose period ends in ``period_end_year``.

    Parameters
    ----------
    period_end_year : int
        The last year of the period, e.g. ``2020``.

    Returns
    -------
    NormalsVintage

    Raises
    ------
    KeyError
        If no vintage is configured for the year.
    """
    for vintage in VINTAGES:
        if vintage.period_end_year == period_end_year:
            return vintage
    raise KeyError(f"no normals vintage configured for the period ending {period_end_year}")


def parse_versions(html: str) -> dict[int, str]:
    """Every ``<year>年平年値（第<version>版）`` heading of the page, by period end year.

    Parameters
    ----------
    html : str
        The decoded page.

    Returns
    -------
    dict of int to str
        Period end year → version as written (``"5"``, ``"4.0.1"``); the last
        heading wins should a year repeat.
    """
    return {int(m["year"]): m["version"] for m in _HEADING_RE.finditer(html)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jma_normals.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/ingestion/jma/normals.py tests/test_jma_normals.py
git commit -m "feat(jma): normals vintage config and the page's version heading

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The downloader

**Files:**
- Modify: `power_market_analytics/ingestion/jma/normals.py` (append)
- Modify: `tests/test_jma_normals.py` (append; extend the import block)

**Interfaces:**
- Consumes: Task 1's names.
- Produces: `JmaNormalsDownloadError(RuntimeError)`; `JmaNormalsDownloader(data_dir=Path("data/jma/normals"), timeout=60.0, session=None, page_url=NORMALS_PAGE_URL)` with `vintage_dir`, `zip_path_for`, `daily_dir`, `station_index_path_for`, `manifest_path_for` (each `(vintage) -> Path`), `read_version(vintage) -> str`, `download_vintage(vintage) -> list[Path]` (the daily files, sorted), `download_all(years: list[int] | None = None) -> list[Path]`. The manifest JSON keys: `period_start_year`, `period_end_year`, `version`, `in_use_since`, `downloaded_at_utc`, `zip_url`, `zip_sha256`, `zip_bytes`, `daily_file_count`.

- [ ] **Step 1: Write the failing tests**

Extend the import block at the top of `tests/test_jma_normals.py` to:

```python
from __future__ import annotations

import calendar
import dataclasses
import datetime
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
import requests

from power_market_analytics.ingestion.jma.normals import (
    NORMALS_PAGE_URL,
    STATION_INDEX_MEMBER,
    VINTAGES,
    JmaNormalsDownloader,
    JmaNormalsDownloadError,
    NormalsVintage,
    parse_versions,
    vintage_for_year,
)
```

Append to the file:

```python
# --------------------------------------------------------------------------- fixtures

V2020 = vintage_for_year(2020)
#: The real vintage with the station count of the two-station test archive.
DEMO = dataclasses.replace(V2020, expected_station_count=2)
PAGE_HTML = "<h2>2020年平年値（第5版)</h2>".encode("utf-8")
STATION_INDEX_BYTES = (
    "Station Number,Station Name,Station Name,Station Name,Latitude,Latitude,Longitude,"
    "Longitude,Altitude\r\n,(Kanji),(Kana),,(degree),(minutes),(degree),(minutes),(m)\r\n"
    "47662,東京　　　　　　　　,ﾄｳｷｮｳ          ,TOKYO                         ,35,41.5,139,45.0, 0025.2\r\n"
).encode("cp932")


def daily_line(
    station: str,
    element: str,
    month: int,
    cells: list[tuple[int, int]],
    n_years: int = 30,
    start: int = 1991,
    end: int = 2020,
    kind: int = 15,
) -> str:
    """One line as JMA writes it: 7 key fields, then 31 (value, flag) pairs, values in 6."""
    assert len(cells) == 31
    head = f"{kind:>2},{station},{element},{n_years:>2},{start:>4},{end:>4},{month:>2}"
    return head + "," + ",".join(f"{value:>6},{flag}" for value, flag in cells)


def month_cells(month: int, values: list[int], flag: int = 8) -> list[tuple[int, int]]:
    """``values`` for the month's days, padded with 0,0 to 31 cells as JMA pads."""
    days = calendar.monthrange(2020, month)[1]
    assert len(values) == days
    return [(v, flag) for v in values] + [(0, 0)] * (31 - days)


def daily_file_text(station: str, elements: tuple[str, ...] = ("0500", "7100")) -> str:
    """A daily file: 12 months per element, value = 10 × month + day, flag 8."""
    lines = []
    for element in elements:
        for month in range(1, 13):
            days = calendar.monthrange(2020, month)[1]
            values = [10 * month + day for day in range(1, days + 1)]
            lines.append(daily_line(station, element, month, month_cells(month, values)))
    return "\n".join(lines) + "\n"


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def demo_zip(stations: tuple[str, ...] = ("47662", "47772"), index: bool = True) -> bytes:
    """A two-station archive with a monthly member the downloader must ignore."""
    members = {
        f"normal_surface/daily/nml_sfc_d_{s}.csv": daily_file_text(s).encode("ascii")
        for s in stations
    }
    members["normal_surface/monthly/nml_sfc_m_47662.csv"] = b"11,47662,0100,30,1991,2020\n"
    if index:
        members[STATION_INDEX_MEMBER] = STATION_INDEX_BYTES
    return make_zip(members)


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "application/zip"):
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Stand-in for requests.Session serving canned responses by URL."""

    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        if url not in self.responses:
            return FakeResponse(b"not found", status=404, content_type="text/html")
        return self.responses[url]


def demo_session(zip_bytes: bytes | None = None, page: bytes = PAGE_HTML) -> FakeSession:
    return FakeSession(
        {
            NORMALS_PAGE_URL: FakeResponse(page, content_type="text/html"),
            DEMO.zip_url: FakeResponse(demo_zip() if zip_bytes is None else zip_bytes),
        }
    )


def files_under(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- downloader


class TestDownloaderPaths:
    def test_defaults(self):
        dl = JmaNormalsDownloader()
        assert dl.data_dir == Path("data/jma/normals")
        assert dl.timeout == 60.0
        assert isinstance(dl.session, requests.Session)
        assert dl.page_url == NORMALS_PAGE_URL

    def test_paths_of_a_period(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path)
        assert dl.vintage_dir(V2020) == tmp_path / "2020"
        assert dl.zip_path_for(V2020) == tmp_path / "2020/zip/normal_surface.zip"
        assert dl.daily_dir(V2020) == tmp_path / "2020/csv/daily"
        assert dl.station_index_path_for(V2020) == tmp_path / "2020/csv/surface_station_index.csv"
        assert dl.manifest_path_for(V2020) == tmp_path / "2020/manifest.json"


class TestReadVersion:
    def test_returns_the_periods_version(self, tmp_path):
        session = demo_session()
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session, timeout=12.5)
        assert dl.read_version(DEMO) == "5"
        assert session.calls == [(NORMALS_PAGE_URL, 12.5)]

    def test_a_page_without_the_period_names_the_periods_it_has(self, tmp_path):
        session = demo_session(page="<h2>2030年平年値（第1版）</h2>".encode("utf-8"))
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(JmaNormalsDownloadError, match=r"1991–2020.*2030.*add a vintage"):
            dl.read_version(DEMO)

    def test_a_page_with_no_heading(self, tmp_path):
        session = demo_session(page=b"<html>maintenance</html>")
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(JmaNormalsDownloadError, match="periods on the page: none"):
            dl.read_version(DEMO)

    def test_http_errors_propagate(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(requests.HTTPError):
            dl.read_version(DEMO)


class TestDownloadVintage:
    def test_writes_the_zip_the_daily_files_the_index_and_the_manifest(self, tmp_path):
        session = demo_session()
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)

        paths = dl.download_vintage(DEMO)

        assert [url for url, _ in session.calls] == [NORMALS_PAGE_URL, DEMO.zip_url]
        assert paths == [
            tmp_path / "2020/csv/daily/nml_sfc_d_47662.csv",
            tmp_path / "2020/csv/daily/nml_sfc_d_47772.csv",
        ]
        assert (tmp_path / "2020/zip/normal_surface.zip").read_bytes() == demo_zip()
        assert paths[0].read_bytes() == daily_file_text("47662").encode("ascii")
        assert (tmp_path / "2020/csv/surface_station_index.csv").read_bytes() == STATION_INDEX_BYTES
        manifest = json.loads((tmp_path / "2020/manifest.json").read_text(encoding="utf-8"))
        assert manifest == {
            "period_start_year": 1991,
            "period_end_year": 2020,
            "version": "5",
            "in_use_since": "2021-05-19",
            "downloaded_at_utc": manifest["downloaded_at_utc"],
            "zip_url": DEMO.zip_url,
            "zip_sha256": hashlib.sha256(demo_zip()).hexdigest(),
            "zip_bytes": len(demo_zip()),
            "daily_file_count": 2,
        }
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest["downloaded_at_utc"])
        assert list(tmp_path.rglob("*.part")) == []
        assert sorted(p.name for p in (tmp_path / "2020/csv/daily").iterdir()) == [
            "nml_sfc_d_47662.csv",
            "nml_sfc_d_47772.csv",
        ]

    def test_every_call_downloads_again(self, tmp_path):
        session = demo_session()
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        dl.download_vintage(DEMO)
        dl.download_vintage(DEMO)
        assert [url for url, _ in session.calls] == [NORMALS_PAGE_URL, DEMO.zip_url] * 2

    def test_a_daily_file_the_archive_no_longer_holds_is_removed(self, tmp_path):
        stale = tmp_path / "2020/csv/daily/nml_sfc_d_99999.csv"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session())
        dl.download_vintage(DEMO)
        assert not stale.exists()

    @pytest.mark.parametrize(
        "content, message",
        [
            (b"<html>maintenance</html>", "not a zip archive"),
            (demo_zip(index=False), "surface_station_index"),
            (demo_zip(stations=("47662",)), "expected 2 daily files, found 1"),
            (demo_zip(stations=("47662", "47772", "47401")), "expected 2 daily files, found 3"),
            (
                make_zip(
                    {
                        "daily/nml_sfc_d_47662.csv": b"x",
                        "daily/nml_sfc_d_47772.csv": b"x",
                        STATION_INDEX_MEMBER: STATION_INDEX_BYTES,
                    }
                ),
                "expected 2 daily files, found 0",
            ),
        ],
    )
    def test_a_bad_archive_is_rejected_and_nothing_is_written(self, tmp_path, content, message):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session(zip_bytes=content))
        with pytest.raises(JmaNormalsDownloadError, match=message):
            dl.download_vintage(DEMO)
        assert not (tmp_path / "2020").exists()

    def test_a_bad_archive_leaves_the_previous_download_intact(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session())
        dl.download_vintage(DEMO)
        before = files_under(tmp_path / "2020")
        dl.session = demo_session(zip_bytes=b"<html>maintenance</html>")
        with pytest.raises(JmaNormalsDownloadError):
            dl.download_vintage(DEMO)
        assert files_under(tmp_path / "2020") == before

    def test_a_failure_while_writing_leaves_no_manifest(self, tmp_path, monkeypatch):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=demo_session())
        dl.download_vintage(DEMO)  # a complete first download, manifest present
        real = JmaNormalsDownloader._atomic_write

        def failing(path, write):
            if path.name == "surface_station_index.csv":
                raise OSError("disk full")
            real(path, write)

        monkeypatch.setattr(JmaNormalsDownloader, "_atomic_write", staticmethod(failing))
        with pytest.raises(OSError, match="disk full"):
            dl.download_vintage(DEMO)
        assert not (tmp_path / "2020/manifest.json").exists()
        assert (tmp_path / "2020/csv/daily/nml_sfc_d_47662.csv").exists()
        assert list(tmp_path.rglob("*.part")) == []

    def test_http_error_on_the_zip_writes_nothing(self, tmp_path):
        session = FakeSession({NORMALS_PAGE_URL: FakeResponse(PAGE_HTML, content_type="text/html")})
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=session)
        with pytest.raises(requests.HTTPError):
            dl.download_vintage(DEMO)
        assert not (tmp_path / "2020").exists()


class TestDownloadAll:
    def test_defaults_to_every_configured_vintage(self, tmp_path, monkeypatch):
        seen: list[int] = []
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        monkeypatch.setattr(
            dl,
            "download_vintage",
            lambda vintage: seen.append(vintage.period_end_year) or [tmp_path / "x.csv"],
        )
        assert dl.download_all() == [tmp_path / "x.csv"]
        assert seen == [2020]

    def test_years_select_the_vintages(self, tmp_path, monkeypatch):
        seen: list[int] = []
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        monkeypatch.setattr(
            dl, "download_vintage", lambda vintage: seen.append(vintage.period_end_year) or []
        )
        dl.download_all(years=[2020])
        assert seen == [2020]

    def test_unknown_year_raises(self, tmp_path):
        dl = JmaNormalsDownloader(data_dir=tmp_path, session=FakeSession({}))
        with pytest.raises(KeyError, match="2030"):
            dl.download_all(years=[2030])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jma_normals.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'JmaNormalsDownloader'`

- [ ] **Step 3: Append the downloader to the module**

Add to the import block of `normals.py`:

```python
import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests
from loguru import logger
```

Append after `parse_versions`:

```python
class JmaNormalsDownloadError(RuntimeError):
    """Raised when the page or the archive is not what the vintage config expects."""


def _bytes_writer(data: bytes) -> Callable[[Path], None]:
    """A ``write(partial)`` for :meth:`JmaNormalsDownloader._atomic_write` that writes ``data``."""

    def write(partial: Path) -> None:
        partial.write_bytes(data)

    return write


class JmaNormalsDownloader:
    """Download one normals period: the version off the page, the archive, the daily files.

    The archive is re-downloaded on every call — JMA replaces it under the same
    URL when a new version comes out, and nothing inside names the version — and
    validated before anything is written: a zip holding the station index and
    exactly ``expected_station_count`` daily files. Every file is written
    atomically (``.part``, then replace). The manifest is removed first and
    written last, so a run that fails midway leaves no manifest and
    :class:`JmaNormalsCsvLoader` refuses the directory.

    Parameters
    ----------
    data_dir : pathlib.Path or str, default ``"data/jma/normals"``
        Root; each period gets ``{period_end_year}/`` underneath.
    timeout : float, default 60.0
        HTTP request timeout in seconds.
    session : requests.Session, optional
        Session to issue ``get`` calls with; a plain :class:`requests.Session`
        by default. Injected by the tests.
    page_url : str, default :data:`NORMALS_PAGE_URL`
        The page whose heading names the version.
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/jma/normals"),
        timeout: float = 60.0,
        session: requests.Session | None = None,
        page_url: str = NORMALS_PAGE_URL,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self.page_url = page_url

    # ----------------------------------------------------------------- paths

    def vintage_dir(self, vintage: NormalsVintage) -> Path:
        """Directory of a period, ``data_dir/<period end year>``."""
        return self.data_dir / str(vintage.period_end_year)

    def zip_path_for(self, vintage: NormalsVintage) -> Path:
        """Where the period's archive is kept."""
        return self.vintage_dir(vintage) / "zip" / "normal_surface.zip"

    def daily_dir(self, vintage: NormalsVintage) -> Path:
        """Directory of the period's extracted daily files."""
        return self.vintage_dir(vintage) / "csv" / "daily"

    def station_index_path_for(self, vintage: NormalsVintage) -> Path:
        """Where the period's station index is extracted, for reference."""
        return self.vintage_dir(vintage) / "csv" / "surface_station_index.csv"

    def manifest_path_for(self, vintage: NormalsVintage) -> Path:
        """The period's manifest JSON, read by the loader."""
        return self.vintage_dir(vintage) / "manifest.json"

    # ----------------------------------------------------------------- the page

    def read_version(self, vintage: NormalsVintage) -> str:
        """Return the version the page names for the vintage's period.

        Parameters
        ----------
        vintage : NormalsVintage
            The period whose heading to read.

        Returns
        -------
        str
            The version as written, e.g. ``"5"``.

        Raises
        ------
        JmaNormalsDownloadError
            If the page has no heading for the period — JMA has moved on, and
            a vintage for the newer normals is the fix.
        requests.HTTPError
            If the page cannot be fetched.
        """
        response = self._get(self.page_url)
        versions = parse_versions(response.content.decode("utf-8", errors="replace"))
        if vintage.period_end_year not in versions:
            found = ", ".join(str(year) for year in sorted(versions)) or "none"
            raise JmaNormalsDownloadError(
                f"{self.page_url}: no heading for the {vintage.period_start_year}–"
                f"{vintage.period_end_year} normals (periods on the page: {found}); add a "
                "vintage for the newer normals"
            )
        return versions[vintage.period_end_year]

    # ----------------------------------------------------------------- the archive

    def download_vintage(self, vintage: NormalsVintage) -> list[Path]:
        """Fetch the period's archive and extract its daily files and station index.

        Parameters
        ----------
        vintage : NormalsVintage
            The period to fetch.

        Returns
        -------
        list of pathlib.Path
            The extracted daily files, sorted by name.

        Raises
        ------
        JmaNormalsDownloadError
            If the page has no heading for the period, or the archive is not a
            zip, lacks the station index or holds another number of daily
            files; nothing is written in that case.
        requests.HTTPError
            If the page or the archive cannot be fetched.
        """
        version = self.read_version(vintage)
        logger.info(
            "Normals {}–{}: version {} on the page; downloading {}",
            vintage.period_start_year,
            vintage.period_end_year,
            version,
            vintage.zip_url,
        )
        content = self._get(vintage.zip_url).content
        members = self._validate_archive(content, vintage)
        downloaded_at = datetime.datetime.now(datetime.timezone.utc)
        # No manifest while the files are being replaced: a run that fails
        # midway must not look complete to the loader.
        self.manifest_path_for(vintage).unlink(missing_ok=True)
        self._atomic_write(self.zip_path_for(vintage), _bytes_writer(content))
        paths: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for member in members:
                dest = self.daily_dir(vintage) / Path(member).name
                self._atomic_write(dest, _bytes_writer(archive.read(member)))
                paths.append(dest)
            index = archive.read(STATION_INDEX_MEMBER)
        self._atomic_write(self.station_index_path_for(vintage), _bytes_writer(index))
        # A daily file of a station the archive no longer holds would otherwise
        # be loaded next to the new ones.
        for stale in self.daily_dir(vintage).glob("nml_sfc_d_*.csv"):
            if stale not in paths:
                stale.unlink()
                logger.warning("Removed {}: not in the archive any more", stale)
        manifest = {
            "period_start_year": vintage.period_start_year,
            "period_end_year": vintage.period_end_year,
            "version": version,
            "in_use_since": vintage.in_use_since.isoformat(),
            "downloaded_at_utc": downloaded_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zip_url": vintage.zip_url,
            "zip_sha256": hashlib.sha256(content).hexdigest(),
            "zip_bytes": len(content),
            "daily_file_count": len(paths),
        }
        self._atomic_write(
            self.manifest_path_for(vintage),
            _bytes_writer(json.dumps(manifest, indent=2).encode("utf-8")),
        )
        logger.info(
            "Normals {}–{}: {} daily file(s) in {} ({} bytes, sha256 {})",
            vintage.period_start_year,
            vintage.period_end_year,
            len(paths),
            self.daily_dir(vintage),
            len(content),
            manifest["zip_sha256"],
        )
        return paths

    @staticmethod
    def _validate_archive(content: bytes, vintage: NormalsVintage) -> list[str]:
        """The archive's daily members, sorted, once the archive passes its checks.

        Raises
        ------
        JmaNormalsDownloadError
            If ``content`` is not a zip, lacks :data:`STATION_INDEX_MEMBER`, or
            holds a number of daily members other than the vintage's.
        """
        buffer = io.BytesIO(content)
        if not zipfile.is_zipfile(buffer):
            raise JmaNormalsDownloadError(
                f"{vintage.zip_url}: not a zip archive ({content[:60]!r})"
            )
        with zipfile.ZipFile(buffer) as archive:
            names = archive.namelist()
        if STATION_INDEX_MEMBER not in names:
            raise JmaNormalsDownloadError(
                f"{vintage.zip_url}: no member {STATION_INDEX_MEMBER} among {len(names)} members"
            )
        members = sorted(name for name in names if DAILY_MEMBER_RE.match(name))
        if len(members) != vintage.expected_station_count:
            raise JmaNormalsDownloadError(
                f"{vintage.zip_url}: expected {vintage.expected_station_count} daily files, "
                f"found {len(members)} — JMA opened or closed a station; update "
                "expected_station_count on purpose"
            )
        return members

    def download_all(self, years: list[int] | None = None) -> list[Path]:
        """Fetch every configured period, or the given period end years.

        Parameters
        ----------
        years : list of int, optional
            Period end years to fetch; every entry of :data:`VINTAGES` by default.

        Returns
        -------
        list of pathlib.Path
            The daily files of all requested periods, oldest period first.

        Raises
        ------
        KeyError
            If a year has no configured vintage.
        """
        vintages = list(VINTAGES) if years is None else [vintage_for_year(y) for y in years]
        paths: list[Path] = []
        for vintage in vintages:
            paths.extend(self.download_vintage(vintage))
        return paths

    # ----------------------------------------------------------------- HTTP and files

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response

    @staticmethod
    def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
        """Write to ``path`` atomically: ``write`` fills a ``.part`` file, then replace.

        On any exception from ``write`` or the replace, the partial file is
        deleted and the exception re-raised; a prior file at ``path`` is
        untouched until the replace. The same rule as the MSM downloader's,
        repeated here because importing that module pulls in eccodes.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        try:
            write(partial)
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jma_normals.py -v`
Expected: 26 passed

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check power_market_analytics/ingestion/jma/normals.py tests/test_jma_normals.py && uv run mypy power_market_analytics/ingestion/jma/normals.py`
Expected: no findings.

```bash
git add power_market_analytics/ingestion/jma/normals.py tests/test_jma_normals.py
git commit -m "feat(jma): normals downloader — the version off the page, the archive validated, a manifest

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The download script

**Files:**
- Create: `scripts/download_jma_normals.py`
- Create: `tests/test_jma_normals_scripts.py`

**Interfaces:**
- Consumes: `JmaNormalsDownloader(data_dir=…, timeout=…)`, `download_all(years=…)`, `VINTAGES`.
- Produces: `main(argv: list[str] | None = None) -> None`; flags `--years` (choices = the configured period end years, default all), `--data-dir` (default `data/jma/normals`), `--timeout` (default 60.0).

- [ ] **Step 1: Write the failing tests**

`tests/test_jma_normals_scripts.py`:

```python
"""CLI wiring of scripts/download_jma_normals.py.

The downloader class is swapped for a recording fake in the script's namespace,
so what is asserted is the argument plumbing, not the HTTP work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import import_script


def make_fake(record: dict):
    class FakeDownloader:
        def __init__(self, data_dir, timeout=60.0):
            record["data_dir"] = data_dir
            record["timeout"] = timeout

        def download_all(self, years=None):
            record["years"] = years
            return [Path(record["data_dir"]) / "2020/csv/daily/nml_sfc_d_47662.csv"]

    return FakeDownloader


class TestDownloadJmaNormals:
    def test_defaults(self, monkeypatch):
        script = import_script("download_jma_normals")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_fake(record))

        script.main([])

        assert record == {"data_dir": Path("data/jma/normals"), "timeout": 60.0, "years": [2020]}

    def test_overrides(self, tmp_path, monkeypatch):
        script = import_script("download_jma_normals")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_fake(record))

        script.main(["--data-dir", str(tmp_path), "--timeout", "5", "--years", "2020"])

        assert record == {"data_dir": tmp_path, "timeout": 5.0, "years": [2020]}

    def test_an_unconfigured_year_is_rejected_by_the_parser(self, monkeypatch):
        script = import_script("download_jma_normals")
        record: dict = {}
        monkeypatch.setattr(script, "JmaNormalsDownloader", make_fake(record))

        with pytest.raises(SystemExit) as exc:
            script.main(["--years", "2030"])

        assert exc.value.code == 2
        assert record == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jma_normals_scripts.py -v`
Expected: FAIL — `FileNotFoundError` from `import_script` (no `scripts/download_jma_normals.py`)

- [ ] **Step 3: Write the script**

`scripts/download_jma_normals.py`:

```python
"""Download JMA's climatological normals (平年値): the daily file of every staffed station.

For each configured normals period (``power_market_analytics.ingestion.jma.normals.VINTAGES``:
1991–2020, in use since 2021-05-19) the version is read off the 平年値ダウンロード
page, ``normal_surface.zip`` (20 MB, one request) is downloaded and validated —
the station index plus exactly 157 daily files — and the daily files, the index
and a manifest are written under ``{data-dir}/{period end year}/``. The zip is
always re-downloaded: JMA replaces it under the same URL when a new version
comes out, and nothing inside names the version.
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.jma.normals import VINTAGES, JmaNormalsDownloader

CONFIGURED_YEARS = [vintage.period_end_year for vintage in VINTAGES]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        choices=CONFIGURED_YEARS,
        default=CONFIGURED_YEARS,
        metavar="YEAR",
        help=f"Period end years to fetch (configured: {CONFIGURED_YEARS}); defaults to all.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/jma/normals"),
        help="Root directory; each period gets {year}/zip, {year}/csv and a manifest.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP request timeout in seconds.",
    )
    args = parser.parse_args(argv)

    downloader = JmaNormalsDownloader(data_dir=args.data_dir, timeout=args.timeout)
    paths = downloader.download_all(years=args.years)
    logger.info("Extracted {} daily file(s) under {}", len(paths), args.data_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jma_normals_scripts.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/download_jma_normals.py tests/test_jma_normals_scripts.py
git commit -m "feat(jma): download_jma_normals.py

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The contract and the positional loader

**Files:**
- Create: `conf/schemas/jma_normal_surface_daily.yaml`
- Modify: `power_market_analytics/ingestion/jma/normals.py` (append)
- Modify: `tests/test_jma_normals.py` (append; extend the import block)

**Interfaces:**
- Consumes: `CsvLoader` (`_scan_positional`, `_project`, `_validate`, `SOURCE_FILE_COL`, `REPORT_LIMIT`), `CsvTableSchema.from_yaml`.
- Produces: `JmaNormalsCsvLoader(schema, filepath, table, spark=None)` with `COLUMN_COUNT = 69`, `NORMAL_KIND_DAILY = "15"`, `ACCEPTED_FLAGS = ("0", "5", "6", "7", "8")`, `manifest_path_for(file: str) -> Path` (staticmethod: `<file>/../../manifest.json`), `_resolve_files`, `_read_all`; the contract's 74 columns in this order: `normal_kind, station_number, element_code, n_years, statistic_start_year, statistic_end_year, month, value_d01, flag_d01, …, value_d31, flag_d31, normals_period_start_year, normals_period_end_year, normals_version, in_use_since, source_file`.

- [ ] **Step 1: Write the contract**

Write `conf/schemas/jma_normal_surface_daily.yaml` with this head, then the 62 day columns generated below, then the five injected columns:

```yaml
description: >
  JMA climatological normals (平年値) of the 1991–2020 period: the daily file
  (地上気象観測 日別平年値) of every staffed station,
  normal_surface/daily/nml_sfc_d_<station>.csv of normal_surface.zip from the
  平年値ダウンロード page (https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html),
  downloaded by scripts/download_jma_normals.py and loaded by
  JmaNormalsCsvLoader (scripts/load_jma_normals.py). Format, element codes,
  flags and versions: docs/JMA-Climatological-Normals-Retrieval.md. The files
  have no header: 15 (the daily kind), the station number, the element code,
  the years of data, the first and last year the statistic covers (0 and 0
  when there is none), the month, then 31 (value, flag) pairs — 69 fields, the
  values right-aligned in 6 characters and scaled per the element's unit
  (0.1 °C, 0.1 mm, 0.1 h, 0.1 MJ/m2, 0.1 tenths of cloud, 1 cm, the three
  occurrence rates in 0.01 day, which over one day is a percentage); a day
  the month does not have is 0,0. Flags: 8 normal; 6 normal, no phenomenon (a
  true zero); 7 and 5 the same two, reference only — the observation ended or
  the series was cut; 0 no statistic. Read positionally (_c0 … _c68) in one
  scan per period; the period, the archive's version and its in-use date are
  injected from the downloader's manifest (__normals_period_start_year,
  __normals_period_end_year, __normals_version, __in_use_since) and the file
  name as __source_file.

read_options:
  # Java charset name for cp932 / Shift_JIS with Windows extensions; the daily
  # files are ASCII, the station index next to them is not.
  encoding: windows-31j
  # the values are right-aligned with spaces
  ignoreLeadingWhiteSpace: "true"

grain: [normals_period_end_year, station_number, element_code, month]

columns:
  - { name: normal_kind, source: _c0, type: int, nullable: false }
  - { name: station_number, source: _c1, type: string, nullable: false }
  - { name: element_code, source: _c2, type: string, nullable: false }
  - { name: n_years, source: _c3, type: int, nullable: false }
  - { name: statistic_start_year, source: _c4, type: int, nullable: false }
  - { name: statistic_end_year, source: _c5, type: int, nullable: false }
  - { name: month, source: _c6, type: int, nullable: false }
```

Generate the 62 day columns (day d has value `_c{5+2d}` and flag `_c{6+2d}`: `value_d01` = `_c7`, `flag_d01` = `_c8`, … `value_d31` = `_c67`, `flag_d31` = `_c68`) and append them, then the tail:

```bash
python3 -c '
for d in range(1, 32):
    print(f"  - {{ name: value_d{d:02d}, source: _c{5+2*d}, type: int, nullable: false }}")
    print(f"  - {{ name: flag_d{d:02d}, source: _c{6+2*d}, type: int, nullable: false }}")
' >> conf/schemas/jma_normal_surface_daily.yaml
cat >> conf/schemas/jma_normal_surface_daily.yaml <<'EOF'
  - { name: normals_period_start_year, source: __normals_period_start_year, type: int, nullable: false }
  - { name: normals_period_end_year, source: __normals_period_end_year, type: int, nullable: false }
  - { name: normals_version, source: __normals_version, type: string, nullable: false }
  - { name: in_use_since, source: __in_use_since, type: date, nullable: false }
  - { name: source_file, source: __source_file, type: string, nullable: false }
EOF
```

Check: `grep -c 'name: ' conf/schemas/jma_normal_surface_daily.yaml` prints `74`.

- [ ] **Step 2: Write the failing tests**

Extend the import block of `tests/test_jma_normals.py`:

```python
from power_market_analytics.ingestion.jma.normals import (
    NORMALS_PAGE_URL,
    STATION_INDEX_MEMBER,
    VINTAGES,
    JmaNormalsCsvLoader,
    JmaNormalsDownloader,
    JmaNormalsDownloadError,
    NormalsVintage,
    parse_versions,
    vintage_for_year,
)
from power_market_analytics.ingestion.loader import CsvTableSchema
from tests.support import REPO_ROOT
```

Append:

```python
# --------------------------------------------------------------------------- loader

CONTRACT = CsvTableSchema.from_yaml(REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml")

MANIFEST_2020 = {
    "period_start_year": 1991,
    "period_end_year": 2020,
    "version": "5",
    "in_use_since": "2021-05-19",
    "downloaded_at_utc": "2026-09-27T10:00:00Z",
    "zip_url": V2020.zip_url,
    "zip_sha256": "0" * 64,
    "zip_bytes": 1,
    "daily_file_count": 2,
}
MANIFEST_2030 = {**MANIFEST_2020, "period_start_year": 2001, "period_end_year": 2030,
                 "version": "1", "in_use_since": "2031-05-20"}


def write_period(root: Path, year: int, files: dict[str, str], manifest: dict | None) -> Path:
    """Write daily files under ``root/<year>/csv/daily/`` and the manifest next to them."""
    daily = root / str(year) / "csv" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (daily / name).write_bytes(text.encode("ascii"))
    if manifest is not None:
        (root / str(year) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return daily


def rows_of(spark, table: str) -> dict[tuple[int, str, str, int], dict]:
    return {
        (r["normals_period_end_year"], r["station_number"], r["element_code"], r["month"]): r.asDict()
        for r in spark.table(table).collect()
    }


class TestContract:
    def test_grain_and_read_options(self):
        assert CONTRACT.grain == [
            "normals_period_end_year",
            "station_number",
            "element_code",
            "month",
        ]
        assert CONTRACT.read_options == {
            "encoding": "windows-31j",
            "ignoreLeadingWhiteSpace": "true",
        }

    def test_columns_in_order(self):
        names = [c.name for c in CONTRACT.columns]
        assert names[:7] == [
            "normal_kind",
            "station_number",
            "element_code",
            "n_years",
            "statistic_start_year",
            "statistic_end_year",
            "month",
        ]
        assert names[7:69] == [
            f"{kind}_d{d:02d}" for d in range(1, 32) for kind in ("value", "flag")
        ]
        assert names[69:] == [
            "normals_period_start_year",
            "normals_period_end_year",
            "normals_version",
            "in_use_since",
            "source_file",
        ]
        assert [c.source_name for c in CONTRACT.columns[:69]] == [f"_c{i}" for i in range(69)]
        assert all(not c.nullable for c in CONTRACT.columns)
        types = {c.name: c.type for c in CONTRACT.columns}
        assert (types["station_number"], types["element_code"], types["in_use_since"]) == (
            "string",
            "string",
            "date",
        )


class TestLoad:
    @pytest.fixture(scope="class")
    def loaded(self, spark, tmp_path_factory):
        root = tmp_path_factory.mktemp("normals")
        write_period(
            root,
            2020,
            {
                "nml_sfc_d_47662.csv": daily_file_text("47662"),
                "nml_sfc_d_47772.csv": daily_file_text("47772"),
            },
            MANIFEST_2020,
        )
        loader = JmaNormalsCsvLoader(CONTRACT, root, "test_jma_normals.loaded", spark=spark)
        return loader.load(), rows_of(spark, "test_jma_normals.loaded")

    def test_row_count_and_grain(self, loaded):
        n_rows, rows = loaded
        assert n_rows == 48  # 2 stations × 2 elements × 12 months
        assert len(rows) == 48

    def test_cells_are_read_by_position(self, loaded):
        _, rows = loaded
        feb = rows[(2020, "47662", "0500", 2)]
        assert (feb["normal_kind"], feb["n_years"]) == (15, 30)
        assert (feb["statistic_start_year"], feb["statistic_end_year"]) == (1991, 2020)
        assert (feb["value_d01"], feb["flag_d01"]) == (21, 8)
        assert (feb["value_d29"], feb["flag_d29"]) == (49, 8)
        assert (feb["value_d30"], feb["flag_d30"], feb["value_d31"], feb["flag_d31"]) == (0, 0, 0, 0)
        jan = rows[(2020, "47772", "7100", 1)]
        assert (jan["value_d31"], jan["flag_d31"]) == (41, 8)

    def test_manifest_values_are_injected(self, loaded):
        _, rows = loaded
        r = rows[(2020, "47662", "0500", 1)]
        assert (
            r["normals_period_start_year"],
            r["normals_period_end_year"],
            r["normals_version"],
            r["in_use_since"],
            r["source_file"],
        ) == (1991, 2020, "5", datetime.date(2021, 5, 19), "nml_sfc_d_47662.csv")

    def test_table_types_follow_the_contract(self, spark, loaded):
        schema = {
            f.name: f.dataType.simpleString()
            for f in spark.table("test_jma_normals.loaded").schema
        }
        assert len(schema) == 74
        assert (schema["element_code"], schema["in_use_since"], schema["value_d31"]) == (
            "string",
            "date",
            "int",
        )

    def test_negative_values_parse(self, spark, tmp_path):
        january = month_cells(1, [-40 + d for d in range(1, 32)])
        text = "\n".join(
            daily_line("47412", "0500", m, january if m == 1 else month_cells(m, [0] * calendar.monthrange(2020, m)[1]))
            for m in range(1, 13)
        ) + "\n"
        write_period(tmp_path, 2020, {"nml_sfc_d_47412.csv": text}, MANIFEST_2020)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.negative", spark=spark)
        assert loader.load() == 12
        rows = rows_of(spark, "test_jma_normals.negative")
        assert rows[(2020, "47412", "0500", 1)]["value_d01"] == -39

    def test_two_periods_load_side_by_side(self, spark, tmp_path):
        files = {"nml_sfc_d_47662.csv": daily_file_text("47662")}
        write_period(tmp_path, 2020, files, MANIFEST_2020)
        write_period(tmp_path, 2030, files, MANIFEST_2030)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.two", spark=spark)
        assert loader.load() == 48
        rows = rows_of(spark, "test_jma_normals.two")
        assert rows[(2030, "47662", "0500", 1)]["normals_version"] == "1"
        assert rows[(2030, "47662", "0500", 1)]["in_use_since"] == datetime.date(2031, 5, 20)
        assert rows[(2020, "47662", "0500", 1)]["normals_period_start_year"] == 1991


class TestFileResolution:
    def test_directory_root_finds_every_periods_daily_files(self, spark, tmp_path):
        write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": "x"}, MANIFEST_2020)
        write_period(tmp_path, 2030, {"nml_sfc_d_47662.csv": "x"}, MANIFEST_2030)
        (tmp_path / "2020/zip").mkdir()
        (tmp_path / "2020/zip/normal_surface.zip").write_bytes(b"not a csv")
        (tmp_path / "2020/csv/surface_station_index.csv").write_bytes(b"not loaded")
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "t", spark=spark)
        assert loader._resolve_files() == [
            str(tmp_path / "2020/csv/daily/nml_sfc_d_47662.csv"),
            str(tmp_path / "2030/csv/daily/nml_sfc_d_47662.csv"),
        ]

    def test_glob_and_single_file(self, spark, tmp_path):
        daily = write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": "x"}, MANIFEST_2020)
        assert JmaNormalsCsvLoader(CONTRACT, daily / "*.csv", "t", spark=spark)._resolve_files() == [
            str(daily / "nml_sfc_d_47662.csv")
        ]
        assert JmaNormalsCsvLoader(
            CONTRACT, daily / "nml_sfc_d_47662.csv", "t", spark=spark
        )._resolve_files() == [str(daily / "nml_sfc_d_47662.csv")]

    def test_manifest_path_is_two_levels_up(self):
        assert JmaNormalsCsvLoader.manifest_path_for("/data/2020/csv/daily/nml_sfc_d_47662.csv") == Path(
            "/data/2020/manifest.json"
        )

    def test_no_files_raises(self, spark, tmp_path):
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "t", spark=spark)
        with pytest.raises(FileNotFoundError, match="No daily normals files"):
            loader.load()


def _replace_line(text: str, element: str, month: int, line: str) -> str:
    lines = text.splitlines()
    for i, existing in enumerate(lines):
        if existing.split(",")[2] == element and int(existing.split(",")[6]) == month:
            lines[i] = line
            return "\n".join(lines) + "\n"
    raise AssertionError("no such line")


GOOD = daily_file_text("47662")
JAN = month_cells(1, [10 + d for d in range(1, 32)])


class TestValidationFailsBeforeWriting:
    @pytest.mark.parametrize(
        "text, message",
        [
            (_replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 1, JAN, kind=16)), "first field not 15"),
            (_replace_line(GOOD, "0500", 1, daily_line("47663", "0500", 1, JAN)), "station number not the file's"),
            (_replace_line(GOOD, "0500", 1, daily_line("47662", "05", 1, JAN)), "element code not four digits"),
            (_replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 13, JAN)), "month not 1-12"),
            (_replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 1, JAN).replace("    11,8", "     x,8")), "value cell not an integer"),
            (_replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 1, JAN).replace("    11,8", "    11,9")), r"flag not in \['0', '5', '6', '7', '8'\]"),
            # a short line: the last four fields missing
            (_replace_line(GOOD, "0500", 1, daily_line("47662", "0500", 1, JAN)[:-18]), "value cell not an integer"),
            # December of 0500 dropped: 11 months
            (GOOD.replace(daily_line("47662", "0500", 12, month_cells(12, [120 + d for d in range(1, 32)])) + "\n", ""), "element 0500 has 11 row"),
        ],
    )
    def test_bad_rows_name_the_file(self, spark, tmp_path, text, message):
        write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": text}, MANIFEST_2020)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.bad", spark=spark)
        with pytest.raises(ValueError, match=message) as excinfo:
            loader.load()
        assert "nml_sfc_d_47662.csv" in str(excinfo.value)
        assert not spark.catalog.tableExists("test_jma_normals.bad")

    def test_files_with_different_element_sets_are_rejected(self, spark, tmp_path):
        write_period(
            tmp_path,
            2020,
            {
                "nml_sfc_d_47662.csv": daily_file_text("47662"),
                "nml_sfc_d_47772.csv": daily_file_text("47772", elements=("0500", "7100", "0600")),
            },
            MANIFEST_2020,
        )
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.sets", spark=spark)
        with pytest.raises(ValueError, match=r"nml_sfc_d_47662\.csv: 2 element\(s\) where the other files have 3"):
            loader.load()

    @pytest.mark.parametrize(
        "manifest, message",
        [
            (None, "no manifest"),
            ({**MANIFEST_2020, "period_end_year": 2030}, "is not the directory's"),
            ({k: v for k, v in MANIFEST_2020.items() if k != "version"}, r"manifest lacks \['version'\]"),
        ],
    )
    def test_manifest_problems(self, spark, tmp_path, manifest, message):
        write_period(tmp_path, 2020, {"nml_sfc_d_47662.csv": daily_file_text("47662")}, manifest)
        loader = JmaNormalsCsvLoader(CONTRACT, tmp_path, "test_jma_normals.manifest", spark=spark)
        with pytest.raises(ValueError, match=message):
            loader.load()

    def test_a_file_not_named_like_a_daily_file_is_rejected(self, spark, tmp_path):
        daily = write_period(tmp_path, 2020, {"normals_47662.csv": daily_file_text("47662")}, MANIFEST_2020)
        loader = JmaNormalsCsvLoader(CONTRACT, daily / "normals_47662.csv", "t", spark=spark)
        with pytest.raises(ValueError, match="not a daily normals file"):
            loader.load()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jma_normals.py -v -k "Contract or Load or FileResolution or Validation"`
Expected: FAIL at import — `ImportError: cannot import name 'JmaNormalsCsvLoader'`

- [ ] **Step 4: Append the loader to the module**

Add to the import block of `normals.py`:

```python
import glob
import operator
from functools import reduce
from typing import Any

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from power_market_analytics.ingestion.loader import REPORT_LIMIT, SOURCE_FILE_COL, CsvLoader
```

Append:

```python
class JmaNormalsCsvLoader(CsvLoader):
    """Positional full reload of the daily normals files into a raw table.

    The files have no header: 7 key fields, then 31 (value, flag) pairs, 69
    fields in all, which the contract addresses as ``_c0`` … ``_c68``. The files
    of one period are read in one positional scan
    (:meth:`CsvLoader._scan_positional`); the period's manifest, written by
    :class:`JmaNormalsDownloader`, supplies the injected
    ``__normals_period_start_year``, ``__normals_period_end_year``,
    ``__normals_version`` and ``__in_use_since``; ``__source_file`` is the file
    name. Row checks run in one grouped Spark pass per scan and name the first
    offending file.

    Parameters
    ----------
    schema, filepath, table, spark
        As for :class:`CsvLoader`. A directory ``filepath`` is the downloader's
        root (``{period end year}/csv/daily/*.csv`` underneath); a glob pattern
        or a single file also works, with the manifest two levels up.
    """

    COLUMN_COUNT = 69
    NORMAL_KIND_DAILY = "15"
    ACCEPTED_FLAGS = ("0", "5", "6", "7", "8")

    _FILENAME_RE = re.compile(r"nml_sfc_d_(?P<station>\d{5})\.csv$")
    _MANIFEST_KEYS = ("period_start_year", "period_end_year", "version", "in_use_since")

    def _resolve_files(self) -> list[str]:
        if self.filepath.is_dir():
            files = sorted(str(p) for p in self.filepath.glob("*/csv/daily/nml_sfc_d_*.csv"))
        else:
            files = sorted(glob.glob(str(self.filepath)))
        if not files:
            raise FileNotFoundError(f"No daily normals files found at {self.filepath}")
        return files

    @staticmethod
    def manifest_path_for(file: str) -> Path:
        """The manifest of the period a daily file belongs to: ``<period>/manifest.json``."""
        return Path(file).resolve().parents[2] / "manifest.json"

    def _read_all(self, files: list[str]) -> DataFrame:
        groups: dict[Path, list[str]] = {}
        for file in files:
            if self._FILENAME_RE.search(file) is None:
                raise ValueError(f"{file}: not a daily normals file (nml_sfc_d_<station>.csv)")
            groups.setdefault(self.manifest_path_for(file), []).append(file)
        frames = [self._read_period(manifest, members) for manifest, members in sorted(groups.items())]
        return reduce(DataFrame.unionByName, frames)

    def _read_period(self, manifest_path: Path, files: list[str]) -> DataFrame:
        """One positional scan of a period's files, the manifest's values injected."""
        manifest = self._read_manifest(manifest_path)
        raw = self._scan_positional(files, self.COLUMN_COUNT)
        self._check_rows(raw)
        data = (
            raw.withColumn("__normals_period_start_year", F.lit(int(manifest["period_start_year"])))
            .withColumn("__normals_period_end_year", F.lit(int(manifest["period_end_year"])))
            .withColumn("__normals_version", F.lit(str(manifest["version"])))
            .withColumn(
                "__in_use_since",
                F.lit(datetime.date.fromisoformat(str(manifest["in_use_since"]))),
            )
            .withColumn("__source_file", F.col(SOURCE_FILE_COL))
        )
        return self._project(data)

    def _read_manifest(self, path: Path) -> dict[str, Any]:
        """The period's manifest, checked against its directory.

        Raises
        ------
        ValueError
            If the manifest is absent (a download that never finished), lacks
            a key the loader needs, or names another period than its directory.
        """
        if not path.exists():
            raise ValueError(
                f"{path}: no manifest next to the daily files; run scripts/download_jma_normals.py"
            )
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in self._MANIFEST_KEYS if key not in manifest]
        if missing:
            raise ValueError(f"{path}: manifest lacks {missing}")
        if str(manifest["period_end_year"]) != path.parent.name:
            raise ValueError(
                f"{path}: period_end_year {manifest['period_end_year']!r} is not the "
                f"directory's {path.parent.name!r}"
            )
        return manifest

    def _check_rows(self, raw: DataFrame) -> None:
        """Validate every row of a scan, reporting per file.

        Parameters
        ----------
        raw : pyspark.sql.DataFrame
            A positional scan carrying ``SOURCE_FILE_COL``.

        Raises
        ------
        ValueError
            Named after the first offending file: a first field other than 15,
            a station number other than the file's, an element code that is not
            four digits, a month outside 1–12, a value cell that is not an
            integer (a short line reads as nulls and fails here too), a flag
            outside 0/5/6/7/8; then an element without exactly its 12 months,
            or a file whose element set is not every file's.
        """
        station_of_file = F.regexp_extract(F.col(SOURCE_FILE_COL), self._FILENAME_RE.pattern, 1)
        values = [F.col(f"_c{i}") for i in range(7, self.COLUMN_COUNT, 2)]
        flags = [F.col(f"_c{i}") for i in range(8, self.COLUMN_COUNT, 2)]

        def bad(condition: Column) -> Column:
            # A null cell (a short line) is bad too; a comparison with null is null.
            return F.coalesce(condition, F.lit(True))

        checks: list[tuple[str, Column]] = [
            (
                f"first field not {self.NORMAL_KIND_DAILY}",
                bad(F.col("_c0") != self.NORMAL_KIND_DAILY),
            ),
            ("station number not the file's", bad(F.col("_c1") != station_of_file)),
            ("element code not four digits", bad(~F.col("_c2").rlike(r"^\d{4}$"))),
            ("month not 1-12", bad(~F.col("_c6").rlike(r"^([1-9]|1[0-2])$"))),
            (
                "value cell not an integer",
                reduce(operator.or_, [bad(~c.rlike(r"^-?\d+$")) for c in values]),
            ),
            (
                f"flag not in {list(self.ACCEPTED_FLAGS)}",
                reduce(operator.or_, [bad(~c.isin(*self.ACCEPTED_FLAGS)) for c in flags]),
            ),
        ]
        counts = (
            raw.groupBy(SOURCE_FILE_COL)
            .agg(
                *[
                    F.count(F.when(condition, True)).alias(f"__c{i}")
                    for i, (_, condition) in enumerate(checks)
                ]
            )
            .orderBy(SOURCE_FILE_COL)
            .collect()
        )
        for row in counts:
            file = row[SOURCE_FILE_COL]
            for i, (label, condition) in enumerate(checks):
                n_bad = row[f"__c{i}"]
                if n_bad:
                    examples = [
                        (r["_c1"], r["_c2"], r["_c6"])
                        for r in raw.filter((F.col(SOURCE_FILE_COL) == file) & condition)
                        .select("_c1", "_c2", "_c6")
                        .limit(REPORT_LIMIT)
                        .collect()
                    ]
                    raise ValueError(
                        f"{file}: {n_bad} row(s) with {label}; first (station, element, "
                        f"month): {examples}"
                    )
        self._check_structure(raw)

    def _check_structure(self, raw: DataFrame) -> None:
        """Every element has its 12 months, and every file holds the same elements."""
        per_element = (
            raw.groupBy(SOURCE_FILE_COL, "_c2")
            .agg(F.count(F.lit(1)).alias("rows"), F.count_distinct(F.col("_c6")).alias("months"))
            .filter((F.col("rows") != 12) | (F.col("months") != 12))
            .orderBy(SOURCE_FILE_COL, "_c2")
            .limit(REPORT_LIMIT)
            .collect()
        )
        if per_element:
            r = per_element[0]
            raise ValueError(
                f"{r[SOURCE_FILE_COL]}: element {r['_c2']} has {r['rows']} row(s) over "
                f"{r['months']} month(s); expected one row per month, 12"
            )
        n_elements = raw.select("_c2").distinct().count()
        per_file = (
            raw.groupBy(SOURCE_FILE_COL)
            .agg(F.count_distinct(F.col("_c2")).alias("elements"))
            .filter(F.col("elements") != n_elements)
            .orderBy(SOURCE_FILE_COL)
            .limit(REPORT_LIMIT)
            .collect()
        )
        if per_file:
            r = per_file[0]
            raise ValueError(
                f"{r[SOURCE_FILE_COL]}: {r['elements']} element(s) where the other files have "
                f"{n_elements}; every file must hold the same elements"
            )
        logger.debug("{} file(s): row and structure checks passed", raw.select(SOURCE_FILE_COL).distinct().count())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jma_normals.py -v`
Expected: all passed (about 50 tests; the Spark ones take a minute).

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy power_market_analytics/ingestion/jma/normals.py tests/test_jma_normals.py`
Expected: no findings. If ruff reformatted a line (the hook does), re-read before editing again.

```bash
git add conf/schemas/jma_normal_surface_daily.yaml power_market_analytics/ingestion/jma/normals.py tests/test_jma_normals.py
git commit -m "feat(jma): positional loader for the daily normals files and its contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The load script

**Files:**
- Create: `scripts/load_jma_normals.py`
- Modify: `tests/test_load_scripts.py:42-107` (one entry in `GENERIC_SCRIPTS`, one in `CONTRACT_GRAINS`)

**Interfaces:**
- Consumes: `JmaNormalsCsvLoader(schema=…, filepath=…, table=…)`, `CsvTableSchema.from_yaml`.
- Produces: `main(argv)`; flags `--schema` (default `conf/schemas/jma_normal_surface_daily.yaml`), `--data` (default `data/jma/normals`), `--table` (default `pma_raw.jma_normal_surface_daily`).

- [ ] **Step 1: Add the failing test entries**

In `tests/test_load_scripts.py`, append to `GENERIC_SCRIPTS`:

```python
    (
        "load_jma_normals",
        "JmaNormalsCsvLoader",
        "conf/schemas/jma_normal_surface_daily.yaml",
        "data/jma/normals",
        "pma_raw.jma_normal_surface_daily",
    ),
```

and to `CONTRACT_GRAINS`:

```python
    "conf/schemas/jma_normal_surface_daily.yaml": [
        "normals_period_end_year",
        "station_number",
        "element_code",
        "month",
    ],
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_load_scripts.py -v -k load_jma_normals`
Expected: FAIL — `FileNotFoundError` from `import_script`

- [ ] **Step 3: Write the script**

`scripts/load_jma_normals.py`:

```python
"""Load the extracted JMA daily normals files into the warehouse (full reload).

Run inside the devcontainer so the Spark session picks up the shared Hive
metastore from ``SPARK_CONF_DIR``:

    python scripts/load_jma_normals.py
"""

import argparse
from pathlib import Path

from loguru import logger

from power_market_analytics.ingestion.jma.normals import JmaNormalsCsvLoader
from power_market_analytics.ingestion.loader import CsvTableSchema

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        default=REPO_ROOT / "conf/schemas/jma_normal_surface_daily.yaml",
        help="Path to the YAML schema definition.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "data/jma/normals",
        help=(
            "Downloader root ({period end year}/csv/daily/*.csv underneath), a single daily "
            "file, or a glob pattern to load; the manifest is read two levels up."
        ),
    )
    parser.add_argument(
        "--table",
        default="pma_raw.jma_normal_surface_daily",
        help="Destination table (database.table).",
    )
    args = parser.parse_args(argv)

    schema = CsvTableSchema.from_yaml(args.schema)
    loader = JmaNormalsCsvLoader(schema=schema, filepath=args.data, table=args.table)
    n_rows = loader.load()
    logger.info("Loaded {} rows into {}", n_rows, args.table)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_load_scripts.py -v -k load_jma_normals`
Expected: 2 passed

- [ ] **Step 5: Run the whole suite with the coverage gate, then commit**

Run: `just test`
Expected: every test passes; `TOTAL … 100%`; `normals.py`, both scripts fully covered. A missed line means a test above is missing — add it, never `# pragma: no cover`.

```bash
git add scripts/load_jma_normals.py tests/test_load_scripts.py
git commit -m "feat(jma): load_jma_normals.py

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The element seed, the raw source and the staging model

**Files:**
- Create: `dbt/seeds/jma_normal_elements.csv`
- Modify: `dbt/dbt_project.yml:26-52` (seed column types)
- Modify: `dbt/models/raw/jma.yml` (append a table to the `jma` source)
- Create: `dbt/models/staging/stg_jma__normal_surface_daily.sql`, `.yml`

**Interfaces:**
- Consumes: `pma_raw.jma_normal_surface_daily` (Task 4's contract columns).
- Produces: seed `jma_normal_elements` (`element_code` string, `element_name_ja`, `element`, `statistic`, `hour_ending` int or null, `unit`, `scale_denominator` int; 81 rows); `stg_jma__normal_surface_daily` with the raw table's 74 columns.

- [ ] **Step 1: Write the seed**

`dbt/seeds/jma_normal_elements.csv` — the header and 81 rows, exactly:

```csv
element_code,element_name_ja,element,statistic,hour_ending,unit,scale_denominator
0500,日平均気温,mean_temperature,normal,,°C,10
0510,日平均気温【標準偏差】,mean_temperature,std,,°C,10
0521,日平均気温【階級区分1】,mean_temperature,class_1,,°C,10
0522,日平均気温【階級区分2】,mean_temperature,class_2,,°C,10
0523,日平均気温【階級区分3】,mean_temperature,class_3,,°C,10
0524,日平均気温【階級区分4】,mean_temperature,class_4,,°C,10
0525,日平均気温【階級区分5】,mean_temperature,class_5,,°C,10
0526,日平均気温【階級区分6】,mean_temperature,class_6,,°C,10
0600,日最高気温,max_temperature,normal,,°C,10
0610,日最高気温【標準偏差】,max_temperature,std,,°C,10
0621,日最高気温【階級区分1】,max_temperature,class_1,,°C,10
0622,日最高気温【階級区分2】,max_temperature,class_2,,°C,10
0623,日最高気温【階級区分3】,max_temperature,class_3,,°C,10
0624,日最高気温【階級区分4】,max_temperature,class_4,,°C,10
0625,日最高気温【階級区分5】,max_temperature,class_5,,°C,10
0626,日最高気温【階級区分6】,max_temperature,class_6,,°C,10
0700,日最低気温,min_temperature,normal,,°C,10
0710,日最低気温【標準偏差】,min_temperature,std,,°C,10
0721,日最低気温【階級区分1】,min_temperature,class_1,,°C,10
0722,日最低気温【階級区分2】,min_temperature,class_2,,°C,10
0723,日最低気温【階級区分3】,min_temperature,class_3,,°C,10
0724,日最低気温【階級区分4】,min_temperature,class_4,,°C,10
0725,日最低気温【階級区分5】,min_temperature,class_5,,°C,10
0726,日最低気温【階級区分6】,min_temperature,class_6,,°C,10
3000,雲量 日平均,cloud_cover,normal,,tenths,10
3500,日照時間 日合計,sunshine_duration,normal,,h,10
3600,日照率≧40% 日数（出現率）,sunshine_rate_ge_40pct,normal,,%,1
3800,全天日射量 日合計,solar_radiation,normal,,MJ/m2,10
4000,降水量 日合計,precipitation,normal,,mm,10
4600,日降水量≧1.0mm 日数（出現率）,precipitation_ge_1mm,normal,,%,1
4700,日降水量≧10.0mm 日数（出現率）,precipitation_ge_10mm,normal,,%,1
6000,降雪の深さ 日合計,snowfall,normal,,cm,1
6200,積雪の深さ 日最大,max_snow_depth,normal,,cm,1
```

then, for h = 1 … 24, two rows each — code `7100 + 100 (h − 1)` for the normal and `+ 10` for the std, `hour_ending` = h:

```bash
python3 -c '
for h in range(1, 25):
    code = 7100 + 100 * (h - 1)
    print(f"{code:04d},{h:02d}時の気温,hourly_temperature,normal,{h},°C,10")
    print(f"{code + 10:04d},{h:02d}時の気温【標準偏差】,hourly_temperature,std,{h},°C,10")
' >> dbt/seeds/jma_normal_elements.csv
```

Check: `wc -l dbt/seeds/jma_normal_elements.csv` prints `82` (a header and 81 rows); the last row is `9410,24時の気温【標準偏差】,hourly_temperature,std,24,°C,10`; the file is UTF-8 with LF endings and no BOM.

- [ ] **Step 2: Type the seed's columns**

In `dbt/dbt_project.yml`, after the `jma_stations` block (line 52), add:

```yaml
    jma_normal_elements:
      +column_types:
        # keep as string: the code has a leading zero (0500)
        element_code: string
        hour_ending: int
        scale_denominator: int
```

- [ ] **Step 3: Add the raw source table**

Append to `dbt/models/raw/jma.yml`, as the last entry of the `jma` source's `tables:` list (six-space indent, like `jma_hourly_staffed`):

```yaml
      - name: jma_normal_surface_daily
        description: >
          JMA climatological normals (平年値) of the 1991–2020 period: the
          daily file (地上気象観測 日別平年値) of every staffed station in
          normal_surface.zip from the 平年値ダウンロード page, one row per
          station, element code and month as published — 152,604 rows, 157
          stations × 81 elements × 12 months — with the row's 31 (value, flag)
          cells as 62 integer columns. Values are scaled integers per the
          element's unit (0.1 °C, 0.1 mm, 0.1 h, 0.1 MJ/m2, cloud tenths, 1 cm;
          the three occurrence rates in hundredths of a day, which over one day
          is a percentage); a day the month does not have is 0,0. Flags: 8
          normal; 6 normal, no phenomenon (a true zero); 7 and 5 the same two,
          reference only — the observation ended or the series was cut, not
          for anomalies; 0 no statistic. The two year columns are the years the
          row's statistic covers, not the period. The period, the archive's
          version and its in-use date come from the downloader's manifest;
          only the current version is served
          (docs/JMA-Climatological-Normals-Retrieval.md §5). Grain enforced at
          load time. Load contract: conf/schemas/jma_normal_surface_daily.yaml.
        columns:
          - name: normal_kind
            description: JMA's 平年値種別; 15 = the daily file.
            data_tests:
              - not_null
              - accepted_values:
                  arguments:
                    values: [15]
                    quote: false
          - name: station_number
            description: >
              Five-digit station number as published (47662 = 東京);
              station_id = 's' || station_number.
            data_tests:
              - not_null
          - name: element_code
            description: >
              Four-digit element code (0500 = 日平均気温), a string so the zero
              stays; the seed jma_normal_elements names every code.
            data_tests:
              - not_null
          - name: n_years
            description: Years of data behind the statistic (資料年数); 0 when there is none.
            data_tests:
              - not_null
          - name: statistic_start_year
            description: First year the row's statistic covers; 0 with n_years 0.
            data_tests:
              - not_null
          - name: statistic_end_year
            description: >
              Last year the row's statistic covers — 2016 for an observation
              that ended that year; 0 with n_years 0.
            data_tests:
              - not_null
          - name: month
            description: Calendar month 1–12.
            data_tests:
              - not_null
          - name: value_d01
            description: >
              The published integer of day 1 (value_d02 … value_d31 the other
              days), in the element's scaled unit; 0 where the flag is 0 and in
              the padding cells of a day the month does not have.
          - name: flag_d01
            description: >
              Quality flag of day 1 (flag_d02 … flag_d31 the other days): 8, 6,
              7, 5 or 0.
          - name: normals_period_start_year
            description: First year of the normals period (1991), from the manifest.
            data_tests:
              - not_null
          - name: normals_period_end_year
            description: Last year of the normals period (2020), from the manifest.
            data_tests:
              - not_null
          - name: normals_version
            description: The archive's version as the page named it when downloaded ("5").
            data_tests:
              - not_null
          - name: in_use_since
            description: The day JMA put the period's normals into use (2021-05-19).
            data_tests:
              - not_null
          - name: source_file
            description: The daily file the row came from, e.g. nml_sfc_d_47662.csv.
            data_tests:
              - not_null
```

- [ ] **Step 4: Write the staging model**

`dbt/models/staging/stg_jma__normal_surface_daily.sql`:

```sql
with
  source as (
  select
    normal_kind,
    station_number,
    element_code,
    n_years,
    statistic_start_year,
    statistic_end_year,
    month,
    {% for d in range(1, 32) -%}
    value_d{{ '%02d' % d }},
    flag_d{{ '%02d' % d }},
    {% endfor -%}
    normals_period_start_year,
    normals_period_end_year,
    normals_version,
    in_use_since,
    source_file
  from
    {{ source('jma', 'jma_normal_surface_daily') }}
  )

select * from source
```

`dbt/models/staging/stg_jma__normal_surface_daily.yml` — the head, then the 62 day columns generated below, then the tail:

```yaml
models:
  - name: stg_jma__normal_surface_daily
    config:
      contract:
        enforced: true
    description: >
      As-is representation of pma_raw.jma_normal_surface_daily (JMA daily
      climatological normals, 1991–2020, staffed stations). One row per
      period, station number, element code and month; 31 (value, flag) pairs.
      Column documentation lives on the source (models/raw/jma.yml).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - normals_period_end_year
              - station_number
              - element_code
              - month
    columns:
      - name: normal_kind
        data_type: int
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [15]
                quote: false
      - name: station_number
        data_type: string
        data_tests:
          - not_null
      - name: element_code
        data_type: string
        data_tests:
          - not_null
      - name: n_years
        data_type: int
        data_tests:
          - not_null
      - name: statistic_start_year
        data_type: int
        data_tests:
          - not_null
      - name: statistic_end_year
        data_type: int
        data_tests:
          - not_null
      - name: month
        data_type: int
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 12
```

```bash
python3 -c '
for d in range(1, 32):
    for kind in ("value", "flag"):
        print(f"      - name: {kind}_d{d:02d}\n        data_type: int")
' >> dbt/models/staging/stg_jma__normal_surface_daily.yml
cat >> dbt/models/staging/stg_jma__normal_surface_daily.yml <<'EOF'
      - name: normals_period_start_year
        data_type: int
        data_tests:
          - not_null
      - name: normals_period_end_year
        data_type: int
        data_tests:
          - not_null
      - name: normals_version
        data_type: string
        data_tests:
          - not_null
      - name: in_use_since
        data_type: date
        data_tests:
          - not_null
      - name: source_file
        data_type: string
        data_tests:
          - not_null
EOF
```

Check: `grep -c '      - name: ' dbt/models/staging/stg_jma__normal_surface_daily.yml` prints `74`.

- [ ] **Step 5: Parse**

Run (from `dbt/`, host-side, no warehouse): `uv run dbt deps && uv run dbt parse`
Expected: `Performance info` and no error; a contract or YAML mistake stops here.

- [ ] **Step 6: Commit**

```bash
git add dbt/seeds/jma_normal_elements.csv dbt/dbt_project.yml dbt/models/raw/jma.yml dbt/models/staging/stg_jma__normal_surface_daily.sql dbt/models/staging/stg_jma__normal_surface_daily.yml
git commit -m "feat(dbt): jma_normal_surface_daily source, staging model and the element seed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The standardized model, its unit tests and three singular tests

**Files:**
- Create: `dbt/models/standardized/std_jma__normal_daily.sql`, `.yml`
- Create: `dbt/dbt_tests/assert_stg_jma__normal_surface_daily_padding_cells_are_zero.sql`
- Create: `dbt/dbt_tests/assert_std_jma__normal_daily_derived_statistics_share_base_flags.sql`
- Create: `dbt/dbt_tests/assert_std_jma__normal_daily_every_station_has_every_element.sql`

**Interfaces:**
- Consumes: `stg_jma__normal_surface_daily`, seed `jma_normal_elements`.
- Produces: `std_jma__normal_daily` — grain `normals_period_end_year × station_id × element_code × month × day_of_month`; columns `normals_period_start_year`, `normals_period_end_year`, `normals_version`, `station_id`, `element_code`, `element`, `statistic`, `hour_ending`, `element_name_ja`, `unit`, `month`, `day_of_month`, `value`, `quality_flag`, `is_reference_only`, `n_years`, `statistic_start_year`, `statistic_end_year`, `available_at`.

- [ ] **Step 1: Write the model**

`dbt/models/standardized/std_jma__normal_daily.sql`:

```sql
-- std_jma__normal_daily: the daily normals unpivoted to one row per station,
-- element, month and day, scaled to the element's unit through the seed
-- jma_normal_elements. The padding cells (days a month does not have) are
-- dropped by a fixed leap-year calendar, so Feb 29 stays whatever the period's
-- end year is; a flag of 0 (no statistic) makes the value null. available_at
-- is the period's first in-use date, the documented bound: only the current
-- version of the archive is served, and each version changed a few stations'
-- files (docs/JMA-Climatological-Normals-Retrieval.md §5).
with
  source as (
  select * from {{ ref('stg_jma__normal_surface_daily') }}
  ),

  elements as (
  select * from {{ ref('jma_normal_elements') }}
  ),

  unpivoted as (
  select
    normals_period_start_year,
    normals_period_end_year,
    normals_version,
    in_use_since,
    station_number,
    element_code,
    n_years,
    statistic_start_year,
    statistic_end_year,
    month,
    stack(
      31,
      {% for d in range(1, 32) -%}
      {{ d }}, value_d{{ '%02d' % d }}, flag_d{{ '%02d' % d }}{{ ',' if not loop.last }}
      {% endfor -%}
    ) as (day_of_month, published_value, quality_flag)
  from
    source
  ),

  calendar_days as (
  -- 2000 is a leap year: the normals carry a Feb 29.
  select
    *
  from
    unpivoted
  where
    day_of_month <= day(last_day(make_date(2000, month, 1)))
  ),

  final as (
  select
    days.normals_period_start_year,
    days.normals_period_end_year,
    days.normals_version,
    concat('s', days.station_number) as station_id,
    days.element_code,
    elements.element,
    elements.statistic,
    elements.hour_ending,
    elements.element_name_ja,
    elements.unit,
    days.month,
    days.day_of_month,
    case
      when days.quality_flag = 0 then null
      else cast(days.published_value as double) / cast(elements.scale_denominator as double)
    end as value,
    days.quality_flag,
    days.quality_flag in (5, 7) as is_reference_only,
    days.n_years,
    nullif(days.statistic_start_year, 0) as statistic_start_year,
    nullif(days.statistic_end_year, 0) as statistic_end_year,
    cast(days.in_use_since as timestamp) as available_at
  from
    calendar_days as days
    left join elements
      on elements.element_code = days.element_code
  )

select * from final
```

- [ ] **Step 2: Write the YAML with the contract, the tests and the two unit tests**

`dbt/models/standardized/std_jma__normal_daily.yml`:

```yaml
models:
  - name: std_jma__normal_daily
    config:
      contract:
        enforced: true
    description: >
      JMA daily climatological normals (平年値, the 1991–2020 period) of the
      staffed stations, one row per period, station, element code, month and
      day of month — 4,654,422 rows: 157 stations × 81 elements × 366 days.
      value is the published integer divided by the element's scale
      denominator (jma_normal_elements): °C, cloud tenths, h, MJ/m2, mm, cm,
      or % for the three occurrence rates, which JMA publishes in hundredths
      of a day; null where quality_flag is 0. Flags: 8 normal; 6 normal, no
      phenomenon (a true zero — snowfall at 那覇); 7 and 5 the same two,
      reference only (is_reference_only): the observation ended or the series
      was cut, statistic_end_year says when, and JMA says not to use them for
      anomalies. A day a month does not have is not a row; Feb 29 is.
      available_at is the period's first in-use date at 00:00 (2021-05-19 for
      1991–2020) — a documented bound: JMA served the 2020 normals from
      2021-03-24 and put them in use 2021-05-19; only the current version of
      the archive is served (5 since 2025-05-21), and each version changed a
      few stations' files, so a row holds the current version's value under
      the period's in-use date. A normal has no date; a dated row finds its
      normal through the month and day of its date.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - normals_period_end_year
              - station_id
              - element_code
              - month
              - day_of_month
      - dbt_utils.unique_combination_of_columns:
          name: std_jma__normal_daily_one_row_per_element_statistic_and_hour
          arguments:
            combination_of_columns:
              - normals_period_end_year
              - station_id
              - element
              - statistic
              - hour_ending
              - month
              - day_of_month
      - dbt_utils.expression_is_true:
          name: std_jma__normal_daily_value_is_null_exactly_when_flag_is_0
          arguments:
            expression: "(quality_flag = 0) = (value is null)"
      - dbt_utils.expression_is_true:
          name: std_jma__normal_daily_hour_ending_exactly_on_hourly_temperature
          arguments:
            expression: "(element = 'hourly_temperature') = (hour_ending is not null)"
    columns:
      - name: normals_period_start_year
        data_type: int
        description: First year of the normals period (1991).
        data_tests:
          - not_null
      - name: normals_period_end_year
        data_type: int
        description: Last year of the normals period (2020); part of the grain.
        data_tests:
          - not_null
      - name: normals_version
        data_type: string
        description: The archive's version when downloaded ("5").
        data_tests:
          - not_null
      - name: station_id
        data_type: string
        description: JMA station id, 's' + the station number (s47662 = 東京); every station of the archive, the ten outside a JEPX area included.
        data_tests:
          - not_null
      - name: element_code
        data_type: string
        description: Four-digit JMA element code (0500 = 日平均気温).
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('jma_normal_elements')
                field: element_code
      - name: element
        data_type: string
        description: >
          The element in English (jma_normal_elements): mean_temperature,
          max_temperature, min_temperature, cloud_cover, sunshine_duration,
          sunshine_rate_ge_40pct, solar_radiation, precipitation,
          precipitation_ge_1mm, precipitation_ge_10mm, snowfall,
          max_snow_depth, hourly_temperature.
        data_tests:
          - not_null
      - name: statistic
        data_type: string
        description: >
          normal (the mean or total), std (its standard deviation) or class_1
          … class_6 (JMA's class thresholds: 1 the smallest of the "low"
          class, 2 at or below "much lower than normal", 3 at or below
          "lower", 4 above "higher", 5 above "much higher", 6 the largest of
          the "high" class).
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [normal, std, class_1, class_2, class_3, class_4, class_5, class_6]
      - name: hour_ending
        data_type: int
        description: 1–24 for hourly_temperature (24 = the value at 24:00 of the day), null for the daily elements.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 24
      - name: element_name_ja
        data_type: string
        description: JMA's name of the element code, e.g. 日平均気温【標準偏差】, 01時の気温.
        data_tests:
          - not_null
      - name: unit
        data_type: string
        description: "°C, tenths, h, %, MJ/m2, mm or cm."
        data_tests:
          - not_null
      - name: month
        data_type: int
        description: Calendar month 1–12.
        data_tests:
          - not_null
      - name: day_of_month
        data_type: int
        description: Day of the month, 1 to the month's length in a leap year.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 31
      - name: value
        data_type: double
        description: The normal in unit; null where quality_flag is 0.
      - name: quality_flag
        data_type: int
        description: 8 normal, 6 normal with no phenomenon, 7 and 5 reference only, 0 no statistic.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 5, 6, 7, 8]
                quote: false
      - name: is_reference_only
        data_type: boolean
        description: quality_flag 5 or 7 — not for anomalies.
        data_tests:
          - not_null
      - name: n_years
        data_type: int
        description: Years of data behind the statistic; 0 with quality_flag 0.
        data_tests:
          - not_null
      - name: statistic_start_year
        data_type: int
        description: First year the statistic covers; null with n_years 0.
      - name: statistic_end_year
        data_type: int
        description: Last year the statistic covers; null with n_years 0.
      - name: available_at
        data_type: timestamp
        description: The period's first in-use date at 00:00, naive JST (2021-05-19 00:00 for 1991–2020).
        data_tests:
          - not_null

unit_tests:
  - name: std_jma__normal_daily_unpivots_scales_and_drops_padding
    description: >
      Tokyo's February row of 日平均気温 (0500), day 2 replaced by a 0,0 cell:
      29 rows, 5.4 on the 1st and 7.4 on the 29th, a null value with flag 0 on
      the 2nd, and the two padding cells gone.
    model: std_jma__normal_daily
    given:
      - input: ref('jma_normal_elements')
        rows:
          - {element_code: "0500", element_name_ja: 日平均気温, element: mean_temperature, statistic: normal, hour_ending: null, unit: °C, scale_denominator: 10}
      - input: ref('stg_jma__normal_surface_daily')
        format: csv
        rows: |
          normal_kind,station_number,element_code,n_years,statistic_start_year,statistic_end_year,month,value_d01,flag_d01,value_d02,flag_d02,value_d03,flag_d03,value_d04,flag_d04,value_d05,flag_d05,value_d06,flag_d06,value_d07,flag_d07,value_d08,flag_d08,value_d09,flag_d09,value_d10,flag_d10,value_d11,flag_d11,value_d12,flag_d12,value_d13,flag_d13,value_d14,flag_d14,value_d15,flag_d15,value_d16,flag_d16,value_d17,flag_d17,value_d18,flag_d18,value_d19,flag_d19,value_d20,flag_d20,value_d21,flag_d21,value_d22,flag_d22,value_d23,flag_d23,value_d24,flag_d24,value_d25,flag_d25,value_d26,flag_d26,value_d27,flag_d27,value_d28,flag_d28,value_d29,flag_d29,value_d30,flag_d30,value_d31,flag_d31,normals_period_start_year,normals_period_end_year,normals_version,in_use_since,source_file
          15,47662,0500,30,1991,2020,2,54,8,0,0,55,8,55,8,55,8,56,8,56,8,56,8,57,8,57,8,58,8,59,8,59,8,60,8,61,8,61,8,62,8,63,8,64,8,64,8,65,8,66,8,67,8,69,8,70,8,71,8,72,8,73,8,74,8,0,0,0,0,1991,2020,5,2021-05-19,nml_sfc_d_47662.csv
    expect:
      format: csv
      rows: |
        station_id,element_code,element,statistic,hour_ending,element_name_ja,unit,month,day_of_month,value,quality_flag,is_reference_only,n_years,statistic_start_year,statistic_end_year,available_at
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,1,5.4,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,2,,0,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,3,5.5,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,4,5.5,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,5,5.5,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,6,5.6,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,7,5.6,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,8,5.6,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,9,5.7,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,10,5.7,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,11,5.8,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,12,5.9,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,13,5.9,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,14,6.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,15,6.1,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,16,6.1,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,17,6.2,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,18,6.3,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,19,6.4,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,20,6.4,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,21,6.5,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,22,6.6,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,23,6.7,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,24,6.9,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,25,7.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,26,7.1,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,27,7.2,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,28,7.3,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,0500,mean_temperature,normal,,日平均気温,°C,2,29,7.4,8,false,30,1991,2020,2021-05-19 00:00:00

  - name: std_jma__normal_daily_rates_no_phenomenon_reference_only_and_no_statistic
    description: >
      Four February rows: 3600's 78 is 78.0 (a percentage as published,
      denominator 1); 6000's 0 with flag 6 is 0.0 (no phenomenon); 7100 lands
      on hour_ending 1, its flag 7 is reference only, its years 1991–2016 are
      kept as they are; 9410 (24時の気温【標準偏差】) with n_years 0, years 0
      and flags 0 gives null values and null years on hour_ending 24.
    model: std_jma__normal_daily
    given:
      - input: ref('jma_normal_elements')
        rows:
          - {element_code: "3600", element_name_ja: 日照率≧40% 日数（出現率）, element: sunshine_rate_ge_40pct, statistic: normal, hour_ending: null, unit: "%", scale_denominator: 1}
          - {element_code: "6000", element_name_ja: 降雪の深さ 日合計, element: snowfall, statistic: normal, hour_ending: null, unit: cm, scale_denominator: 1}
          - {element_code: "7100", element_name_ja: 01時の気温, element: hourly_temperature, statistic: normal, hour_ending: 1, unit: °C, scale_denominator: 10}
          - {element_code: "9410", element_name_ja: 24時の気温【標準偏差】, element: hourly_temperature, statistic: std, hour_ending: 24, unit: °C, scale_denominator: 10}
      - input: ref('stg_jma__normal_surface_daily')
        format: csv
        rows: |
          normal_kind,station_number,element_code,n_years,statistic_start_year,statistic_end_year,month,value_d01,flag_d01,value_d02,flag_d02,value_d03,flag_d03,value_d04,flag_d04,value_d05,flag_d05,value_d06,flag_d06,value_d07,flag_d07,value_d08,flag_d08,value_d09,flag_d09,value_d10,flag_d10,value_d11,flag_d11,value_d12,flag_d12,value_d13,flag_d13,value_d14,flag_d14,value_d15,flag_d15,value_d16,flag_d16,value_d17,flag_d17,value_d18,flag_d18,value_d19,flag_d19,value_d20,flag_d20,value_d21,flag_d21,value_d22,flag_d22,value_d23,flag_d23,value_d24,flag_d24,value_d25,flag_d25,value_d26,flag_d26,value_d27,flag_d27,value_d28,flag_d28,value_d29,flag_d29,value_d30,flag_d30,value_d31,flag_d31,normals_period_start_year,normals_period_end_year,normals_version,in_use_since,source_file
          15,47662,3600,30,1991,2020,2,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,78,8,0,0,0,0,1991,2020,5,2021-05-19,nml_sfc_d_47662.csv
          15,47662,6000,30,1991,2020,2,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,6,0,0,0,0,1991,2020,5,2021-05-19,nml_sfc_d_47662.csv
          15,47662,7100,25,1991,2016,2,10,7,20,7,30,7,40,7,50,7,60,7,70,7,80,7,90,7,100,7,110,7,120,7,130,7,140,7,150,7,160,7,170,7,180,7,190,7,200,7,210,7,220,7,230,7,240,7,250,7,260,7,270,7,280,7,290,7,0,0,0,0,1991,2020,5,2021-05-19,nml_sfc_d_47662.csv
          15,47662,9410,0,0,0,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1991,2020,5,2021-05-19,nml_sfc_d_47662.csv
    expect:
      format: csv
      rows: |
        station_id,element_code,element,statistic,hour_ending,element_name_ja,unit,month,day_of_month,value,quality_flag,is_reference_only,n_years,statistic_start_year,statistic_end_year,available_at
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,1,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,2,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,3,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,4,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,5,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,6,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,7,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,8,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,9,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,10,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,11,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,12,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,13,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,14,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,15,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,16,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,17,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,18,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,19,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,20,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,21,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,22,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,23,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,24,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,25,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,26,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,27,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,28,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,3600,sunshine_rate_ge_40pct,normal,,日照率≧40% 日数（出現率）,%,2,29,78.0,8,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,1,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,2,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,3,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,4,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,5,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,6,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,7,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,8,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,9,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,10,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,11,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,12,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,13,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,14,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,15,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,16,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,17,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,18,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,19,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,20,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,21,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,22,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,23,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,24,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,25,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,26,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,27,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,28,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,6000,snowfall,normal,,降雪の深さ 日合計,cm,2,29,0.0,6,false,30,1991,2020,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,1,1.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,2,2.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,3,3.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,4,4.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,5,5.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,6,6.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,7,7.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,8,8.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,9,9.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,10,10.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,11,11.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,12,12.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,13,13.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,14,14.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,15,15.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,16,16.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,17,17.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,18,18.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,19,19.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,20,20.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,21,21.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,22,22.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,23,23.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,24,24.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,25,25.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,26,26.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,27,27.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,28,28.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,7100,hourly_temperature,normal,1,01時の気温,°C,2,29,29.0,7,true,25,1991,2016,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,1,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,2,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,3,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,4,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,5,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,6,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,7,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,8,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,9,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,10,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,11,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,12,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,13,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,14,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,15,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,16,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,17,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,18,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,19,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,20,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,21,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,22,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,23,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,24,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,25,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,26,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,27,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,28,,0,false,0,,,2021-05-19 00:00:00
        s47662,9410,hourly_temperature,std,24,24時の気温【標準偏差】,°C,2,29,,0,false,0,,,2021-05-19 00:00:00
```

- [ ] **Step 3: Write the three singular tests**

`dbt/dbt_tests/assert_stg_jma__normal_surface_daily_padding_cells_are_zero.sql`:

```sql
-- std_jma__normal_daily drops the days a month does not have (the 30th and
-- 31st of February, the 31st of April, June, September and November) by a
-- fixed leap-year calendar. That is safe only while JMA writes 0,0 in those
-- cells, as every file did on 2026-09-27; a value there would be lost with
-- nothing saying so.
with
  source as (
  select * from {{ ref('stg_jma__normal_surface_daily') }}
  )

select
  normals_period_end_year,
  station_number,
  element_code,
  month
from
  source
where
  (month in (4, 6, 9, 11) and (value_d31 <> 0 or flag_d31 <> 0))
  or (
    month = 2
    and (value_d30 <> 0 or flag_d30 <> 0 or value_d31 <> 0 or flag_d31 <> 0)
  )
```

`dbt/dbt_tests/assert_std_jma__normal_daily_derived_statistics_share_base_flags.sql`:

```sql
-- A standard-deviation or class-threshold row carries its base element's
-- flag, year count and statistic years on every cell of every file (measured
-- on 2026-09-27: 0 mismatches over 4.7 M cells), which is why the facts carry
-- one flag per measure. A derived row without a base row, or one whose flag
-- or years differ, fails here.
with
  normals as (
  select * from {{ ref('std_jma__normal_daily') }}
  ),

  derived as (
  select * from normals where statistic <> 'normal'
  ),

  base as (
  select * from normals where statistic = 'normal'
  )

select
  derived.normals_period_end_year,
  derived.station_id,
  derived.element_code,
  derived.month,
  derived.day_of_month
from
  derived
  left join base
    on base.normals_period_end_year = derived.normals_period_end_year
    and base.station_id = derived.station_id
    and base.element = derived.element
    and base.hour_ending <=> derived.hour_ending
    and base.month = derived.month
    and base.day_of_month = derived.day_of_month
where
  base.element_code is null
  or base.quality_flag <> derived.quality_flag
  or base.n_years <> derived.n_years
  or not (base.statistic_start_year <=> derived.statistic_start_year)
  or not (base.statistic_end_year <=> derived.statistic_end_year)
```

`dbt/dbt_tests/assert_std_jma__normal_daily_every_station_has_every_element.sql`:

```sql
-- JMA ships every element for every station (81 today). A station missing
-- one would leave a fact column null with nothing saying so; a code the seed
-- lacks fails the relationships test instead.
with
  expected as (
  select count(*) as n_elements from {{ ref('jma_normal_elements') }}
  ),

  per_station as (
  select
    normals_period_end_year,
    station_id,
    count(distinct element_code) as n_elements
  from
    {{ ref('std_jma__normal_daily') }}
  group by
    normals_period_end_year,
    station_id
  )

select
  per_station.normals_period_end_year,
  per_station.station_id,
  per_station.n_elements,
  expected.n_elements as expected_n_elements
from
  per_station
  cross join expected
where
  per_station.n_elements <> expected.n_elements
```

- [ ] **Step 4: Parse**

Run (from `dbt/`): `uv run dbt parse`
Expected: no error. A `stack` or Jinja slip shows up only in the devcontainer build (Task 10), so read the compiled SQL once: `uv run dbt compile --select std_jma__normal_daily` and open `target/compiled/pma/models/standardized/std_jma__normal_daily.sql` — the `stack(31, 1, value_d01, flag_d01, … 31, value_d31, flag_d31)` call must have no trailing comma.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/standardized/std_jma__normal_daily.sql dbt/models/standardized/std_jma__normal_daily.yml dbt/dbt_tests/assert_stg_jma__normal_surface_daily_padding_cells_are_zero.sql dbt/dbt_tests/assert_std_jma__normal_daily_derived_statistics_share_base_flags.sql dbt/dbt_tests/assert_std_jma__normal_daily_every_station_has_every_element.sql
git commit -m "feat(dbt): std_jma__normal_daily — the daily normals unpivoted and scaled, with unit and singular tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The two facts and their singular tests

**Files:**
- Create: `dbt/models/curated/fct_jma_normal_daily.sql`, `.yml`
- Create: `dbt/models/curated/fct_jma_normal_hourly.sql`, `.yml`
- Create: `dbt/dbt_tests/assert_fct_jma_normal_daily_has_366_days_per_station.sql`
- Create: `dbt/dbt_tests/assert_fct_jma_normal_hourly_has_24_hours_per_day.sql`

**Interfaces:**
- Consumes: `std_jma__normal_daily`, `dim_jma_station.station_id`.
- Produces: `fct_jma_normal_daily` (grain `normals_period_end_year × station_id × month × day_of_month`, 34 columns) and `fct_jma_normal_hourly` (grain `… × hour_ending`, 14 columns), columns as in the YAML below.

- [ ] **Step 1: Write the daily fact**

`dbt/models/curated/fct_jma_normal_daily.sql`:

```sql
-- fct_jma_normal_daily: the daily climatological normals of a period, one row
-- per station and calendar day (month, day_of_month; 366 per station), wide
-- over the daily elements — the normal and, for the three temperatures, its
-- standard deviation, with one quality flag per element. The hourly
-- temperatures are fct_jma_normal_hourly; the class thresholds and n_years
-- stay in std_jma__normal_daily. A normal has no date: a dated row finds its
-- normal through the month and day of its date. The inner join to
-- dim_jma_station drops the ten stations outside a JEPX area.
with
  normals as (
  select
    *
  from
    {{ ref('std_jma__normal_daily') }}
  where
    element <> 'hourly_temperature'
    and statistic in ('normal', 'std')
  ),

  stations as (
  select station_id from {{ ref('dim_jma_station') }}
  ),

  final as (
  select
    normals.normals_period_end_year,
    normals.station_id,
    normals.month,
    normals.day_of_month,
    -- one row per key and element in `normals`, so each max() sees one value
    max(case when normals.element = 'mean_temperature' and normals.statistic = 'normal' then normals.value end) as mean_temperature_c,
    max(case when normals.element = 'mean_temperature' and normals.statistic = 'std' then normals.value end) as mean_temperature_std_c,
    max(case when normals.element = 'mean_temperature' and normals.statistic = 'normal' then normals.quality_flag end) as mean_temperature_quality_flag,
    max(case when normals.element = 'max_temperature' and normals.statistic = 'normal' then normals.value end) as max_temperature_c,
    max(case when normals.element = 'max_temperature' and normals.statistic = 'std' then normals.value end) as max_temperature_std_c,
    max(case when normals.element = 'max_temperature' and normals.statistic = 'normal' then normals.quality_flag end) as max_temperature_quality_flag,
    max(case when normals.element = 'min_temperature' and normals.statistic = 'normal' then normals.value end) as min_temperature_c,
    max(case when normals.element = 'min_temperature' and normals.statistic = 'std' then normals.value end) as min_temperature_std_c,
    max(case when normals.element = 'min_temperature' and normals.statistic = 'normal' then normals.quality_flag end) as min_temperature_quality_flag,
    max(case when normals.element = 'cloud_cover' then normals.value end) as cloud_cover_tenths,
    max(case when normals.element = 'cloud_cover' then normals.quality_flag end) as cloud_cover_quality_flag,
    max(case when normals.element = 'sunshine_duration' then normals.value end) as sunshine_duration_h,
    max(case when normals.element = 'sunshine_duration' then normals.quality_flag end) as sunshine_duration_quality_flag,
    max(case when normals.element = 'sunshine_rate_ge_40pct' then normals.value end) as prob_sunshine_rate_ge_40pct_pct,
    max(case when normals.element = 'sunshine_rate_ge_40pct' then normals.quality_flag end) as sunshine_rate_ge_40pct_quality_flag,
    max(case when normals.element = 'solar_radiation' then normals.value end) as solar_radiation_mjm2,
    max(case when normals.element = 'solar_radiation' then normals.quality_flag end) as solar_radiation_quality_flag,
    max(case when normals.element = 'precipitation' then normals.value end) as precipitation_mm,
    max(case when normals.element = 'precipitation' then normals.quality_flag end) as precipitation_quality_flag,
    max(case when normals.element = 'precipitation_ge_1mm' then normals.value end) as prob_precipitation_ge_1mm_pct,
    max(case when normals.element = 'precipitation_ge_1mm' then normals.quality_flag end) as precipitation_ge_1mm_quality_flag,
    max(case when normals.element = 'precipitation_ge_10mm' then normals.value end) as prob_precipitation_ge_10mm_pct,
    max(case when normals.element = 'precipitation_ge_10mm' then normals.quality_flag end) as precipitation_ge_10mm_quality_flag,
    max(case when normals.element = 'snowfall' then normals.value end) as snowfall_cm,
    max(case when normals.element = 'snowfall' then normals.quality_flag end) as snowfall_quality_flag,
    max(case when normals.element = 'max_snow_depth' then normals.value end) as max_snow_depth_cm,
    max(case when normals.element = 'max_snow_depth' then normals.quality_flag end) as max_snow_depth_quality_flag,
    max(normals.normals_period_start_year) as normals_period_start_year,
    max(normals.normals_version) as normals_version,
    max(normals.available_at) as available_at
  from
    normals
    inner join stations
      on stations.station_id = normals.station_id
  group by
    normals.normals_period_end_year,
    normals.station_id,
    normals.month,
    normals.day_of_month
  )

select * from final
```

- [ ] **Step 2: Write its YAML**

`dbt/models/curated/fct_jma_normal_daily.yml`:

```yaml
models:
  - name: fct_jma_normal_daily
    config:
      contract:
        enforced: true
    description: >
      JMA daily climatological normals (平年値) of the 1991–2020 period, one
      row per station and calendar day — 147 stations × 366 days, Feb 29
      included — for the stations of dim_jma_station: the normal of each daily
      element and, for the three temperatures, its standard deviation, with a
      quality flag per element (8 normal; 6 normal, no phenomenon; 7 and 5
      reference only — the observation ended or the series was cut, not for
      anomalies; 0 no statistic, value null). The three prob_* columns are
      the share of the period's years in which the day qualified, in %, as
      JMA publishes them. A normal has no date: join a dated row on
      station_id and the month and day of its date (dim_date carries both;
      month() and day() on a timestamp give the same). The hourly
      temperatures are fct_jma_normal_hourly; the class thresholds and the
      years behind each statistic stay in std_jma__normal_daily. available_at
      is 2021-05-19 00:00, the day the period's normals came into use — a
      documented bound, since only the current version of the archive is
      served (docs/JMA-Climatological-Normals-Retrieval.md §5).
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - normals_period_end_year
              - station_id
              - month
              - day_of_month
    columns:
      - name: normals_period_end_year
        data_type: int
        description: Last year of the normals period (2020); part of the grain.
        data_tests:
          - not_null
      - name: station_id
        data_type: string
        description: Station foreign key to dim_jma_station, e.g. s47662 = 東京.
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_jma_station')
                field: station_id
      - name: month
        data_type: int
        description: Calendar month 1–12 of the normal's day.
        data_tests:
          - not_null
      - name: day_of_month
        data_type: int
        description: Day of the month of the normal's day, 1–31; Feb 29 exists.
        data_tests:
          - not_null
      - name: mean_temperature_c
        data_type: double
        description: 日平均気温 normal, °C (富士山, 3,775 m, is near −19 in January).
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: -30
                max_value: 35
      - name: mean_temperature_std_c
        data_type: double
        description: Standard deviation of the daily mean temperature over the period's years, °C.
      - name: mean_temperature_quality_flag
        data_type: int
        description: Flag of the mean temperature (and its std, which shares it).
      - name: max_temperature_c
        data_type: double
        description: 日最高気温 normal, °C.
      - name: max_temperature_std_c
        data_type: double
        description: Its standard deviation, °C.
      - name: max_temperature_quality_flag
        data_type: int
        description: Flag of the maximum temperature.
      - name: min_temperature_c
        data_type: double
        description: 日最低気温 normal, °C.
      - name: min_temperature_std_c
        data_type: double
        description: Its standard deviation, °C.
      - name: min_temperature_quality_flag
        data_type: int
        description: Flag of the minimum temperature.
      - name: cloud_cover_tenths
        data_type: double
        description: 雲量 日平均 normal, tenths of the sky (0–10).
      - name: cloud_cover_quality_flag
        data_type: int
        description: Flag of the cloud cover.
      - name: sunshine_duration_h
        data_type: double
        description: 日照時間 日合計 normal, hours.
      - name: sunshine_duration_quality_flag
        data_type: int
        description: Flag of the sunshine duration.
      - name: prob_sunshine_rate_ge_40pct_pct
        data_type: double
        description: Share of the period's years in which the day's sunshine rate was ≥ 40 %, in %.
      - name: sunshine_rate_ge_40pct_quality_flag
        data_type: int
        description: Flag of that share.
      - name: solar_radiation_mjm2
        data_type: double
        description: 全天日射量 日合計 normal, MJ/m².
      - name: solar_radiation_quality_flag
        data_type: int
        description: Flag of the solar radiation.
      - name: precipitation_mm
        data_type: double
        description: 降水量 日合計 normal, mm.
      - name: precipitation_quality_flag
        data_type: int
        description: Flag of the precipitation.
      - name: prob_precipitation_ge_1mm_pct
        data_type: double
        description: Share of the period's years in which the day had ≥ 1.0 mm of precipitation, in %.
      - name: precipitation_ge_1mm_quality_flag
        data_type: int
        description: Flag of that share.
      - name: prob_precipitation_ge_10mm_pct
        data_type: double
        description: Share of the period's years in which the day had ≥ 10.0 mm of precipitation, in %.
      - name: precipitation_ge_10mm_quality_flag
        data_type: int
        description: Flag of that share.
      - name: snowfall_cm
        data_type: double
        description: 降雪の深さ 日合計 normal, cm; 0.0 with flag 6 where it never snows.
      - name: snowfall_quality_flag
        data_type: int
        description: Flag of the snowfall.
      - name: max_snow_depth_cm
        data_type: double
        description: 積雪の深さ 日最大 normal, cm.
      - name: max_snow_depth_quality_flag
        data_type: int
        description: Flag of the snow depth.
      - name: normals_period_start_year
        data_type: int
        description: First year of the normals period (1991).
        data_tests:
          - not_null
      - name: normals_version
        data_type: string
        description: The archive's version when downloaded ("5").
        data_tests:
          - not_null
      - name: available_at
        data_type: timestamp
        description: The period's first in-use date at 00:00, naive JST.
        data_tests:
          - not_null
```

- [ ] **Step 3: Write the hourly fact and its YAML**

`dbt/models/curated/fct_jma_normal_hourly.sql`:

```sql
-- fct_jma_normal_hourly: the climatological normal of the temperature at each
-- hour 01–24 of each calendar day, one row per station, day and hour ending —
-- the same hour axis as fct_jma_weather_hourly (hour 24 belongs to the day it
-- ends) and the MSM forecast fact. The normal and the std rows of std share
-- the flag and the years, so each max() sees one value.
with
  normals as (
  select
    *
  from
    {{ ref('std_jma__normal_daily') }}
  where
    element = 'hourly_temperature'
  ),

  stations as (
  select station_id from {{ ref('dim_jma_station') }}
  ),

  final as (
  select
    normals.normals_period_end_year,
    normals.station_id,
    normals.month,
    normals.day_of_month,
    normals.hour_ending,
    max(case when normals.statistic = 'normal' then normals.value end) as temperature_c,
    max(case when normals.statistic = 'std' then normals.value end) as temperature_std_c,
    max(case when normals.statistic = 'normal' then normals.quality_flag end) as temperature_quality_flag,
    max(normals.n_years) as n_years,
    max(normals.statistic_start_year) as statistic_start_year,
    max(normals.statistic_end_year) as statistic_end_year,
    max(normals.normals_period_start_year) as normals_period_start_year,
    max(normals.normals_version) as normals_version,
    max(normals.available_at) as available_at
  from
    normals
    inner join stations
      on stations.station_id = normals.station_id
  group by
    normals.normals_period_end_year,
    normals.station_id,
    normals.month,
    normals.day_of_month,
    normals.hour_ending
  )

select * from final
```

`dbt/models/curated/fct_jma_normal_hourly.yml`:

```yaml
models:
  - name: fct_jma_normal_hourly
    config:
      contract:
        enforced: true
    description: >
      JMA climatological normal of the temperature at each hour of each
      calendar day (時別気温 平年値, new in the 2020 normals), one row per
      station, day and hour ending 1–24 — 147 stations × 366 days × 24 hours —
      for the stations of dim_jma_station, with its standard deviation over
      the period's years and one quality flag (8 normal, 7 reference only, 0
      no statistic). hour_ending 24 is the value at 24:00 of the day, which
      fct_jma_weather_hourly stores as 00:00 of the next day under the day's
      date_key (observed_date), so both join on station_id, the month and day
      of the date and hour(observed_hour_start_at) + 1. available_at is
      2021-05-19 00:00, the day the period's normals came into use.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns:
              - normals_period_end_year
              - station_id
              - month
              - day_of_month
              - hour_ending
    columns:
      - name: normals_period_end_year
        data_type: int
        description: Last year of the normals period (2020); part of the grain.
        data_tests:
          - not_null
      - name: station_id
        data_type: string
        description: Station foreign key to dim_jma_station, e.g. s47662 = 東京.
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_jma_station')
                field: station_id
      - name: month
        data_type: int
        description: Calendar month 1–12.
        data_tests:
          - not_null
      - name: day_of_month
        data_type: int
        description: Day of the month, 1–31; Feb 29 exists.
        data_tests:
          - not_null
      - name: hour_ending
        data_type: int
        description: Hour ending 1–24 (JST); 24 = the value at 24:00 of the day.
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                max_value: 24
      - name: temperature_c
        data_type: double
        description: The temperature normal at the hour, °C; null with flag 0 (富士山 reaches about −21 at night in January).
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: -30
                max_value: 40
      - name: temperature_std_c
        data_type: double
        description: Its standard deviation over the period's years, °C.
      - name: temperature_quality_flag
        data_type: int
        description: 8 normal, 7 reference only (the observation ended or the series was cut), 0 no statistic.
        data_tests:
          - not_null
          - accepted_values:
              arguments:
                values: [0, 7, 8]
                quote: false
      - name: n_years
        data_type: int
        description: Years of data behind the statistic; 0 with flag 0.
        data_tests:
          - not_null
      - name: statistic_start_year
        data_type: int
        description: First year the statistic covers; null with n_years 0.
      - name: statistic_end_year
        data_type: int
        description: Last year the statistic covers; null with n_years 0.
      - name: normals_period_start_year
        data_type: int
        description: First year of the normals period (1991).
        data_tests:
          - not_null
      - name: normals_version
        data_type: string
        description: The archive's version when downloaded ("5").
        data_tests:
          - not_null
      - name: available_at
        data_type: timestamp
        description: The period's first in-use date at 00:00, naive JST.
        data_tests:
          - not_null
```

- [ ] **Step 4: Write the two singular tests**

`dbt/dbt_tests/assert_fct_jma_normal_daily_has_366_days_per_station.sql`:

```sql
-- Every station and period has one row per calendar day of a leap year, Feb 29
-- among them: a padding day that slipped through, or a day the unpivot lost,
-- shows here as a count other than 366.
select
  normals_period_end_year,
  station_id,
  count(*) as n_days,
  sum(case when month = 2 and day_of_month = 29 then 1 else 0 end) as n_feb_29
from
  {{ ref('fct_jma_normal_daily') }}
group by
  normals_period_end_year,
  station_id
having
  count(*) <> 366
  or sum(case when month = 2 and day_of_month = 29 then 1 else 0 end) <> 1
```

`dbt/dbt_tests/assert_fct_jma_normal_hourly_has_24_hours_per_day.sql`:

```sql
-- Every station-day of the hourly fact has its 24 hour endings exactly once.
select
  normals_period_end_year,
  station_id,
  month,
  day_of_month,
  count(*) as n_rows,
  count(distinct hour_ending) as n_hours
from
  {{ ref('fct_jma_normal_hourly') }}
group by
  normals_period_end_year,
  station_id,
  month,
  day_of_month
having
  count(*) <> 24
  or count(distinct hour_ending) <> 24
```

- [ ] **Step 5: Parse and check the generated files are untouched**

Run (from `dbt/`): `uv run dbt parse`
Expected: no error.

Run (from the worktree root): `uv run python scripts/generate_feature_views.py --check`
Expected: passes unchanged — no feature mart was added, so `views.py`, `fct_feature_value.sql` and `dim_feature.sql` stay as they are.

- [ ] **Step 6: Commit**

```bash
git add dbt/models/curated/fct_jma_normal_daily.sql dbt/models/curated/fct_jma_normal_daily.yml dbt/models/curated/fct_jma_normal_hourly.sql dbt/models/curated/fct_jma_normal_hourly.yml dbt/dbt_tests/assert_fct_jma_normal_daily_has_366_days_per_station.sql dbt/dbt_tests/assert_fct_jma_normal_hourly_has_24_hours_per_day.sql
git commit -m "feat(dbt): fct_jma_normal_daily and fct_jma_normal_hourly

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Docs, recipes and the index rows

**Files:**
- Create: `docs/JMA-Climatological-Normals-Retrieval.md`
- Modify: `docs/_sidebar.md:3-11`, `docs/README.md:11,48-50,97`, `docs/Curated-Star-Schema.md:3,11,29-30,183,307,319`, `CLAUDE.md` (three places), `justfile:73-75`, `power_market_analytics/ingestion/jma/__init__.py`, `docs/superpowers/README.md:16`

- [ ] **Step 1: Write the retrieval doc**

`docs/JMA-Climatological-Normals-Retrieval.md`:

````markdown
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
# Host or devcontainer: the page (version), the zip (20 MB), the 157 daily files.
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
````

- [ ] **Step 2: The sidebar, the home page, the star-schema doc**

`docs/_sidebar.md` — after the `JMA — MSM GPV 地上予報` line add:

```markdown
  - [JMA — 平年値 (climatological normals)](JMA-Climatological-Normals-Retrieval.md)
```

`docs/README.md`:

- line 11: `the sixteen fact tables` → `the eighteen fact tables`.
- in the gantt, after the `MSM GPV 地上予報` line (line 50):

```text
    平年値 1991–2020 (daily, in use 2021-05-19)      :milestone, jmanml, 2021-05-19, 0d
```

- in the Loaded table, after the `MSM GPV 地上予報` row (line 97):

```markdown
| JMA | [平年値 (climatological normals, 1991–2020)](https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html) (平年値ダウンロード, `normal_surface.zip`, the daily file of each staffed station) | <ul><li>station (157; 147 in the seed)</li><li>calendar day (month, day; Feb 29 included)</li><li>element (81 codes)</li></ul> | daily mean / max / min temperature with std and class thresholds, the temperature at each hour 01–24 with std, cloud cover, sunshine, radiation, precipitation, snowfall, snow depth and three occurrence rates, each with a quality flag (8/6 normal, 7/5 reference only, 0 none) and the years the statistic covers ([doc](JMA-Climatological-Normals-Retrieval.md)) | the 1991–2020 period, version 5, in use since 2021-05-19 | `pma_raw.jma_normal_surface_daily` |
```

`docs/Curated-Star-Schema.md`:

- line 3: `sixteen fact tables` → `eighteen fact tables`.
- after the `fct_jma_weather_hourly` row of the table (line 11):

```markdown
| `fct_jma_normal_daily` | station × calendar day (`month`, `day_of_month`) | JMA 1991–2020 climatological normals of the daily elements — mean / max / min temperature with their standard deviations, cloud cover, sunshine, radiation, precipitation, snowfall, snow depth, three occurrence rates in % — one quality flag per element; 366 rows per station, Feb 29 included. A normal has no date: join a dated row on the month and day of its date. |
| `fct_jma_normal_hourly` | station × calendar day × hour ending | The normal of the temperature at each hour 01–24 with its standard deviation and flag; the hour axis of `fct_jma_weather_hourly` (hour 24 belongs to the day it ends). |
```

- in the `erDiagram`, after `dim_jma_station ||--o{ fct_jma_weather_hourly : "station_id"` (line 30):

```text
    dim_jma_station ||--o{ fct_jma_normal_daily : "station_id"
    dim_jma_station ||--o{ fct_jma_normal_hourly : "station_id"
```

- after the `fct_jma_weather_hourly { … }` block (ends line 183):

```text
    fct_jma_normal_daily {
        int normals_period_end_year PK
        string station_id PK, FK
        int month PK
        int day_of_month PK
        double mean_temperature_c
        double mean_temperature_std_c
        int mean_temperature_quality_flag
        double max_temperature_c
        double max_temperature_std_c
        int max_temperature_quality_flag
        double min_temperature_c
        double min_temperature_std_c
        int min_temperature_quality_flag
        double cloud_cover_tenths
        int cloud_cover_quality_flag
        double sunshine_duration_h
        int sunshine_duration_quality_flag
        double prob_sunshine_rate_ge_40pct_pct
        int sunshine_rate_ge_40pct_quality_flag
        double solar_radiation_mjm2
        int solar_radiation_quality_flag
        double precipitation_mm
        int precipitation_quality_flag
        double prob_precipitation_ge_1mm_pct
        int precipitation_ge_1mm_quality_flag
        double prob_precipitation_ge_10mm_pct
        int precipitation_ge_10mm_quality_flag
        double snowfall_cm
        int snowfall_quality_flag
        double max_snow_depth_cm
        int max_snow_depth_quality_flag
        int normals_period_start_year
        string normals_version
        timestamp available_at
    }

    fct_jma_normal_hourly {
        int normals_period_end_year PK
        string station_id PK, FK
        int month PK
        int day_of_month PK
        int hour_ending PK
        double temperature_c
        double temperature_std_c
        int temperature_quality_flag
        int n_years
        int statistic_start_year
        int statistic_end_year
        int normals_period_start_year
        string normals_version
        timestamp available_at
    }
```

- line 307, the `class … fact` list: add `fct_jma_normal_daily,fct_jma_normal_hourly` after `fct_jma_weather_hourly`.
- in the Notes table, after the `fct_jma_weather_hourly` row (line 319):

```markdown
| `fct_jma_normal_daily`, `fct_jma_normal_hourly` | Normals are per station and calendar day, not dated: no `date_key`, no `dim_date` reference; a dated row joins on `month` and `day_of_month` of its date (and `hour_ending` for the hourly fact). `_quality_flag` 7 or 5 is reference only — the observation ended or the series was cut — and JMA says not to use it for a departure from normal. `available_at` is 2021-05-19 00:00 for the whole 1991–2020 period, a documented bound (only the current version of JMA's archive is served). The class thresholds and the years behind each statistic stay in `std_jma__normal_daily`. |
```

- [ ] **Step 3: CLAUDE.md, the justfile, the package docstring**

`CLAUDE.md`:

- In the `just refresh-all` bullet, the source list `JEPX (+ the holidays seed), JMA hourly (+ the station seed), OCCTO, …` → `JEPX (+ the holidays seed), JMA hourly (+ the station seed), JMA normals, OCCTO, …`.
- In the "A single source is refreshed by …" list, after the JMA hourly item:

```markdown
  - JMA normals (平年値, since 2026-09-27): `download_jma_normals.py` (the version off the
    平年値ダウンロード page, `normal_surface.zip` — 20 MB, always re-downloaded — validated and
    its 157 daily files extracted under `data/jma/normals/2020/` with a manifest; no `--force`),
    `load_jma_normals.py` (152,604 rows, seconds). Host or devcontainer for the download,
    devcontainer for the load.
```

- In Architecture, after the "JMA MSM GPV surface forecast" bullet:

```markdown
- JMA climatological normals (平年値, the 1991–2020 period, in use since 2021-05-19; since
  2026-09-27): `scripts/download_jma_normals.py` (`JmaNormalsDownloader` in
  `power_market_analytics/ingestion/jma/normals.py`, which holds the vintage config
  `VINTAGES` — one entry, the 2030 normals become a second —, the downloader and the loader:
  the version is read off the page heading `2020年平年値（第5版)`, the archive is validated —
  the station index plus exactly 157 daily files — and the daily files, the index and
  `manifest.json` written under `data/jma/normals/2020/`, the manifest removed first and
  written last so a failed run leaves none) → `scripts/load_jma_normals.py`
  (`JmaNormalsCsvLoader`, positional contract `conf/schemas/jma_normal_surface_daily.yaml`,
  69 fields — 7 keys, then 31 value/flag pairs —, the period, version and in-use date
  injected from the manifest, row checks in one grouped Spark pass) →
  `pma_raw.jma_normal_surface_daily` (station × element × month, 152,604 rows) →
  `stg_jma__normal_surface_daily` → `std_jma__normal_daily` (one row per station × element ×
  month × day, 4,654,422 rows: the value scaled by the seed `jma_normal_elements` — 81 codes,
  the three occurrence rates percentages as published —, null with flag 0, the padding days
  dropped by a fixed leap-year calendar so Feb 29 stays, `is_reference_only` = flag 5 or 7,
  `available_at` = 2021-05-19 00:00, the period's first in-use date — the documented bound,
  since only the current version of the archive is served) → `fct_jma_normal_daily` (station ×
  month × day_of_month, 147 stations × 366 days: mean/max/min temperature with std, cloud
  cover, sunshine, radiation, precipitation, snowfall, snow depth, three `prob_*_pct` rates,
  one flag per element; the ten stations outside a JEPX area drop at the `dim_jma_station`
  join) and `fct_jma_normal_hourly` (× hour_ending 1–24, 1,291,248 rows: the temperature at
  each hour with its std, flag and statistic years). A normal has no date: join a dated row
  on `station_id`, the month and day of its date and, for the hourly fact, the hour ending
  (hour 24 belongs to the day it ends, as `fct_jma_weather_hourly` places it). In no preset;
  departure-from-normal features are a feature candidate of their own. Protocol, record
  layout, element codes, flags and versions:
  [docs/JMA-Climatological-Normals-Retrieval.md](docs/JMA-Climatological-Normals-Retrieval.md).
```

- In Gotchas, after the "JMA hourly: 積雪の深さ …" bullet:

```markdown
- JMA normals: the two year columns of a daily row are the years *that statistic* covers
  (1991–2016 for an observation that ended in 2016, 0 and 0 with `n_years` 0 for none), not
  the period — the period is the vintage's. Only the current version of the archive is served
  (5 since 2025-05-21; each version changed a few stations' files — 延岡 in v5), so a reload
  after a new version silently changes those rows; the manifest records the version loaded.
  A standard-deviation or class-threshold row shares its base element's flag and years on
  every cell (measured), so the facts carry one flag per measure; a singular test keeps it so.
```

`justfile` — after `just python scripts/load_jma_hourly.py` (line 75), a new block:

```text

    just python scripts/download_jma_normals.py
    just python scripts/load_jma_normals.py
```

`power_market_analytics/ingestion/jma/__init__.py` — in the module list of the docstring, after the `load` bullet:

```python
* :mod:`~power_market_analytics.ingestion.jma.normals` — the climatological
  normals (平年値): the vintage config, the ``normal_surface.zip`` downloader
  and the positional loader of the daily files into
  ``pma_raw.jma_normal_surface_daily``.
```

and replace the last paragraph's scope sentence with: `Only staffed stations (気象官署, ``s``-prefixed ids) inside a JEPX area are in scope for the observations since the 2026-08 re-scope; the normals archive holds every staffed station, and the ten outside a JEPX area drop out at the curated join.`

`docs/superpowers/README.md` — the 2026-09-27 row already links this plan (done with the plan's commit); nothing to change.

- [ ] **Step 4: Check the links and the prose**

Run: `uv run --no-sync python scripts/check_docs_links.py`
Expected: `every relative Markdown link resolves`.

Run: `uv run ruff check . && uv run mypy` (the `__init__.py` docstring edit) — no findings.

- [ ] **Step 5: Commit**

```bash
git add docs/JMA-Climatological-Normals-Retrieval.md docs/_sidebar.md docs/README.md docs/Curated-Star-Schema.md CLAUDE.md justfile power_market_analytics/ingestion/jma/__init__.py docs/superpowers/README.md
git commit -m "docs(jma): the normals retrieval doc, the source rows, CLAUDE.md and the refresh recipe

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Verification in the devcontainer

**Files:** none changed; the numbers go into the PR body (Task 11).

Everything below runs from the worktree root; `just python` and `just dbt` run in the devcontainer, which sees the worktree at its own path (see the memory `candidates-201-204-buildout` for the in-container recipes from a worktree).

- [ ] **Step 1: Download and load**

Run: `just python scripts/download_jma_normals.py`
Expected: `Normals 1991–2020: version 5 on the page; downloading …`, then `157 daily file(s) in data/jma/normals/2020/csv/daily (21615988 bytes, sha256 …)`. Record the sha256 from `data/jma/normals/2020/manifest.json`.

Run: `just python scripts/load_jma_normals.py`
Expected: `Loaded 152604 rows into pma_raw.jma_normal_surface_daily`.

- [ ] **Step 2: Build the models and their tests**

Run: `just dbt build --select jma_normal_elements stg_jma__normal_surface_daily+`
Expected: the seed, the three models, the two unit tests, every generic test and the five singular tests PASS. `std_jma__normal_daily` is the slow one (a 4.6 M-row unpivot; about a minute).

- [ ] **Step 3: Row counts**

```bash
just dbt show --inline "select count(*) as n from {{ ref('std_jma__normal_daily') }}" --limit 5
just dbt show --inline "select count(*) as n, count(distinct station_id) as stations from {{ ref('fct_jma_normal_daily') }}" --limit 5
just dbt show --inline "select count(*) as n from {{ ref('fct_jma_normal_hourly') }}" --limit 5
```

Expected: 4,654,422; 53,802 and 147; 1,291,248.

- [ ] **Step 4: Tokyo's values against JMA's own page**

```bash
just dbt show --inline "select mean_temperature_c, mean_temperature_std_c, cloud_cover_tenths, sunshine_duration_h, prob_sunshine_rate_ge_40pct_pct, solar_radiation_mjm2, prob_precipitation_ge_1mm_pct, prob_precipitation_ge_10mm_pct, snowfall_cm, snowfall_quality_flag from {{ ref('fct_jma_normal_daily') }} where station_id = 's47662' and month = 1 and day_of_month = 1" --limit 5
just dbt show --inline "select hour_ending, temperature_c, temperature_std_c from {{ ref('fct_jma_normal_hourly') }} where station_id = 's47662' and month = 1 and day_of_month = 1 order by hour_ending" --limit 24
just dbt show --inline "select snowfall_cm from {{ ref('fct_jma_normal_daily') }} where station_id = 's47412' and month = 1 and day_of_month = 1" --limit 5
```

Expected: 6.0, 1.9, 3.7, 6.3, 78.0, 9.0, 10.0, 4.0, 0.0, 6; hour 24 = 5.5; 札幌 4.0. Open
<https://www.data.jma.go.jp/stats/etrn/view/nml_sfc_d.php?prec_no=44&block_no=47662&year=&month=1&day=&view=>
and compare January 1's 平均気温, 最高気温, 最低気温, 降水量, 日照時間 with the fact's row (add `max_temperature_c`, `min_temperature_c`, `precipitation_mm` to the first query) — they must be equal to the digit.

- [ ] **Step 5: Sanity: the hourly normals against ten years of observations**

```bash
just dbt show --inline "
with obs as (
  select station_id, month(date_key) as month, day(date_key) as day_of_month,
         hour(observed_hour_start_at) + 1 as hour_ending, avg(temperature_c) as mean_temperature_c
  from {{ ref('fct_jma_weather_hourly') }}
  where station_id = 's47662' and date_key between date '2016-01-01' and date '2025-12-31'
    and temperature_quality_flag = 8
  group by 1, 2, 3, 4
)
select count(*) as n, round(avg(obs.mean_temperature_c - n.temperature_c), 3) as mean_diff_c,
       round(stddev(obs.mean_temperature_c - n.temperature_c), 3) as sd_diff_c
from obs
join {{ ref('fct_jma_normal_hourly') }} n
  on n.station_id = obs.station_id and n.month = obs.month
  and n.day_of_month = obs.day_of_month and n.hour_ending = obs.hour_ending
" --limit 5
```

Expected: n = 8,784 (366 × 24); `mean_diff_c` a few tenths of a degree positive (2016–2025 is warmer than 1991–2020); `sd_diff_c` about 1 °C. A mean difference of degrees means a wrong hour or scale — stop and look.

- [ ] **Step 6: The full suite and the lint jobs**

Run: `just test && just lint && just mypy && just docs-links`
Expected: all green, coverage `TOTAL … 100%`.

Run: `(cd dbt && uv run dbt parse) && uv run python scripts/generate_feature_views.py --check`
Expected: clean (what the CI `dbt parse` job runs).

Record in a scratch note (`scratch/2026-09-27-jma-normals/verification.md`) every number above for the PR body.

---

### Task 11: The pull request and the review loop

**Files:** a PR body file under `scratch/2026-09-27-jma-normals/pr-body.md` (not committed).

- [ ] **Step 1: Push the branch**

Run: `git log --oneline origin/main..HEAD` — the spec, the plan and the nine feature commits, nothing else. Then `git push -u origin feature/jma-climatological-normals`.

- [ ] **Step 2: Write the PR body**

The shape of `.github/pull_request_template.md`, without its comments:

```markdown
## Summary

JMA's 1991–2020 climatological normals (平年値), the daily file of every staffed station, as a raw table, a long standardized model and two wide facts — for departure-from-normal features and dashboard reference. Spec `docs/superpowers/specs/2026-09-27-jma-climatological-normals-design.md`; nothing closes on merge.

## Changes

- `power_market_analytics/ingestion/jma/normals.py`: `NormalsVintage` / `VINTAGES`, `parse_versions`, `JmaNormalsDownloader`, `JmaNormalsCsvLoader`.
- `scripts/download_jma_normals.py`, `scripts/load_jma_normals.py`; `conf/schemas/jma_normal_surface_daily.yaml`.
- Seed `jma_normal_elements` (81 codes); source `jma_normal_surface_daily`; `stg_jma__normal_surface_daily`, `std_jma__normal_daily` (two unit tests), `fct_jma_normal_daily`, `fct_jma_normal_hourly`; five singular tests.
- `docs/JMA-Climatological-Normals-Retrieval.md`, the sidebar and home-page rows, `Curated-Star-Schema.md`, CLAUDE.md, `refresh-all`.
- Tests: `tests/test_jma_normals.py`, `tests/test_jma_normals_scripts.py`, a `test_load_scripts.py` entry.

## Effect on what exists

| What | Effect |
|---|---|
| Existing models, seeds, marts, presets | None: new models only; `generate_feature_views.py --check` unchanged |
| `just refresh-all` | Two new steps after the JMA hourly block, ~10 s |
| `dbt_project.yml` | Seed column types for `jma_normal_elements` |
| `dim_jma_station` | Unchanged; the facts inner-join it, so 147 of 157 stations appear |

## Checks

`just test` 100 % · `just lint` · `just mypy` · `just docs-links` · `dbt parse` · `generate_feature_views.py --check` · devcontainer: download (version 5, sha256 <from the manifest>), load 152,604 rows, `dbt build --select jma_normal_elements stg_jma__normal_surface_daily+` all PASS.

<details>
<summary>Decisions (5)</summary>

1. **The daily file only.** The hourly temperatures and every daily element are in it; the monthly file is a later chain from the same zip.
2. **A long `std`, two wide facts.** One long fact makes every reader pivot; a pivot in the loader stops raw being as published.
3. **`available_at` = 2021-05-19 00:00, the period's first in-use date.** Only the current archive version is served, and each version changed a few stations; the version's own date would hide the normals from the whole pinned window.
4. **The version off the page, the zip always re-downloaded.** Nothing inside the archive names the version, and JMA replaces it under the same URL.
5. **One flag per measure.** A std or class-threshold row shares its base element's flag and years on every cell of every file; a singular test guards it.

</details>

<details>
<summary>Evidence</summary>

| Check | Result |
|---|---|
| Rows: raw / std / daily fact / hourly fact | 152,604 / 4,654,422 / 53,802 (147 stations) / 1,291,248 |
| Tokyo Jan 1 (mean, std, cloud, sunshine, sunshine ≥ 40 %, radiation, ≥ 1 mm, ≥ 10 mm, snowfall) | 6.0 °C, 1.9, 3.7, 6.3 h, 78 %, 9.0 MJ/m², 10 %, 4 %, 0.0 cm flag 6 — equal to JMA's 日ごとの値 page |
| Tokyo hour 24, Jan 1; 札幌 snowfall Jan 1 | 5.5 °C; 4 cm |
| Hourly normals vs 2016–2025 observed mean, Tokyo | n 8,784; mean difference <x.xx> °C, sd <x.xx> °C |
| Padding cells, derived flags, every element, 366 days, 24 hours | the five singular tests PASS |

</details>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Fill the `<…>` cells from Task 10's note before creating the PR.

- [ ] **Step 3: Open the PR and label it**

```bash
gh pr create --title "feat(jma): ingest the climatological normals (平年値 1991–2020) of the staffed stations" --body-file scratch/2026-09-27-jma-normals/pr-body.md
gh pr edit <n> --add-assignee hankehly --add-label enhancement --add-label ingestion
```

- [ ] **Step 4: The review loop**

Follow CLAUDE.md "Code review (pull requests)": poll every 60 s for Codex's review (a `Codex Review` by `chatgpt-codex-connector[bot]` after the push) or its 👍, with no timeout; address every finding — a fix commit, or a reply with the evidence — resolve the threads, push, and wait for the next round; if Codex is out of credits, tell the researcher (Copilot is the fallback, their call). Report the PR as ready — CI green, the reviewer clean, the body current — and stop; the researcher merges.
