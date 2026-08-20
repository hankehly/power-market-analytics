# JMA s-Station Re-scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-scope the JMA hourly weather pipeline to staffed (s-prefixed) stations only, expand the element set to 7 elements (core 4 + 積雪の深さ/相対湿度/全天日射量), and download each station-year as ONE stitched write-once CSV built from time-windowed requests.

**Architecture:** The seed/dim shrink to s-stations via a `staffed_only` filter in the station-master downloader. `JmaHourlyDownloader` gains time-windowing: when an element set × year exceeds JMA's per-request value budget, the year is split into windows, each fetched separately, and the responses stitched (header block kept once, data rows appended) into one file per station-year. The AMeDAS leg (contract, raw table, stg model, union in std) is deleted; the staffed contract widens to 26 columns and `std`/`fct` gain 9 columns.

**Tech Stack:** Python (requests, loguru, PySpark loader framework), pytest (100% coverage gate), dbt 1.11 on Spark thriftserver, uv, just.

**Spec:** `docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md` — read it first; it records the decisions and the 均質番号 caveat this plan implements.

## Global Constraints

- Work on branch `jma-s-station-rescope` (already created; the spec is committed there).
- `just test` must pass with **100% coverage** (`fail_under` gate); every new line needs a test.
- A PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` you edit — the file may change right after your edit; re-read before the next edit if an edit fails to anchor. Line length 100.
- NumPy-style docstrings (`Parameters` / `Returns` / `Raises` with underlined headers) on every public function/method, matching the existing style in `power_market_analytics/jma.py`.
- Every dbt model keeps `contract: enforced: true` with a `data_type` for every column, and a uniqueness test on its grain (`dbt_utils.unique_combination_of_columns` for composite keys).
- dbt 1.11 generic-test args go under `arguments:` (see existing ymls).
- Anything creating a SparkSession runs in the devcontainer (`just python`, `just dbt`); plain pytest (`just test`) and host-side dbt (`cd dbt && DBT_THRIFT_HOST=localhost uv run dbt <cmd>`) work from the host.
- Element codes: 降水量=101, 気温=201, 風向・風速=301 (2 value columns), 日照時間=401, 積雪の深さ=501, 相対湿度=605, 全天日射量=610. The new file-name code string is `101-201-301-401-501-605-610`.
- Do not touch the untracked files `.codex/`, `AGENTS.md` (except the documented edit in Task 7), `scratch.md`.

---

### Task 1: Spike — validate the windowed 8-column request against live JMA

Confirms the design's one empirical assumption before any code: an 8-value-column half-year request passes JMA's data-volume cap. Also pins the real 26-column layout for Task 5's contract. **Throwaway code — never committed.**

**Files:**
- Create: `<scratchpad>/spike_jma_window.py` (scratchpad dir from the session; NOT in the repo)
- Create: `<scratchpad>/s47662_window1_2024.csv`, `<scratchpad>/s47662_window2_2024.csv` (spike outputs, referenced by Task 5)

**Interfaces:**
- Produces: confirmation that `MAX_VALUES_PER_REQUEST = 44_000` is safe (half-year × 8 columns ≈ 35k values passes); the real header rows and 2 sample data rows of the 26-column layout; observed value formats for 積雪 (int cm), 湿度 (int %), 全天日射量 (double MJ/㎡, incl. what a night hour looks like).

- [ ] **Step 1: Write the throwaway spike script**

```python
"""Throwaway spike: does an 8-value-column half-year request pass JMA's cap?

Two live requests for 東京 (s47662), 2024 in two half-year windows, all 7
re-scope elements. Saves the raw responses and prints the layout.
"""

from pathlib import Path

from power_market_analytics.jma import JmaHourlyDownloader

OUT = Path(__file__).parent
ELEMENT_CODES = ["101", "201", "301", "401", "501", "605", "610"]
WINDOWS = [(("2024", "1", "1"), ("2024", "7", "1")), (("2024", "7", "2"), ("2024", "12", "31"))]

dl = JmaHourlyDownloader()  # real session, real 5 s throttle
for i, ((y1, m1, d1), (y2, m2, d2)) in enumerate(WINDOWS, start=1):
    payload = dl._payload("s47662", ["temperature"], 2024)  # template
    payload["elementNumList"] = "[" + ",".join(f'["{c}",""]' for c in ELEMENT_CODES) + "]"
    payload["ymdList"] = f'["{y1}","{y2}","{m1}","{m2}","{d1}","{d2}"]'
    response = dl._post_with_retry(dl.SHOW_TABLE_URL, payload)
    head = response.content[:64].decode("cp932", errors="replace")
    print(f"window {i}: {len(response.content)} bytes, head={head!r}")
    assert head.startswith("ダウンロードした時刻"), f"window {i} REJECTED: {head!r}"
    (OUT / f"s47662_window{i}_2024.csv").write_bytes(response.content)

for i in (1, 2):
    lines = (OUT / f"s47662_window{i}_2024.csv").read_bytes().decode("cp932").splitlines()
    print(f"--- window {i}: {len(lines)} lines; header + first/last data rows:")
    for line in lines[:7]:
        print(repr(line))
    print(repr(lines[-1]))
    data = [ln for ln in lines if ln[:4].isdigit() and ln[4:5] == "/"]
    print(f"data rows: {len(data)}; columns: {data[0].count(',') + 1}")
```

(The `_payload` template call uses the CURRENT signature `(station_id, elements, year)` — this spike runs before Task 3 changes it.)

- [ ] **Step 2: Run it** — `just python <scratchpad>/spike_jma_window.py` (or host-side `uv run python`; no SparkSession involved). Expected: both windows print `head='ダウンロードした時刻…'`, window 1 has 4,392 data rows (Jan 1 01:00 → Jul 2 00:00 stored timestamps ending 24:00 as next-day 00:00), window 2 has 4,392, columns = **26**.

- [ ] **Step 3: Decision gate.**
  - Both pass → record in the task report: confirmed `MAX_VALUES_PER_REQUEST = 44_000`; paste the 4 header rows + 2 data rows (one winter with snow if visible, one summer day + one night hour for solar) into the report for Task 5.
  - A window is rejected (HTML head) → re-run with quarter windows (`1/1–3/31`, `4/1–6/30`, …). If quarters pass, the constant becomes `22_000` (→ 4 windows/year for 8 columns) and Task 3's window-count test expectations change accordingly (the code is parameterized; only test constants change). Report which passed.
  - Verify from the printed rows: column order matches ascending element codes (降水量 group, 気温, 風, 日照, 積雪, 湿度, 全天日射量); note the exact number formats.

- [ ] **Step 4: Do NOT commit anything.** The spike files stay in the scratchpad; Tasks 3/5 consume the findings from the task report.

---

### Task 2: Station master — `staffed_only` filter + seed script

**Files:**
- Modify: `power_market_analytics/jma.py` (JmaStationMasterDownloader `__init__` ~line 466, `download` ~line 495)
- Modify: `scripts/update_jma_stations_seed.py`
- Test: `tests/test_jma.py` (TestStationMasterDownload), `tests/test_jma_scripts.py` (make_station_master_fake, TestUpdateJmaStationsSeed)

**Interfaces:**
- Produces: `JmaStationMasterDownloader(dest, staffed_only: bool = False, **kwargs)` — when `staffed_only=True`, only rows whose `station_id` starts with `"s"` are written. Task 4's orchestrator also passes `staffed_only=True`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_jma.py`, inside `TestStationMasterDownload` (reuse the existing `station_block`/`route_areas`/`PREFECTURE_MAP`/`TOKYO`/`FUCHU`/`SHINKIBA`/`NAVIGATION_BLOCK`/`HEADER` helpers):

```python
    def test_staffed_only_writes_only_s_stations(self, tmp_path):
        pages = {"00": PREFECTURE_MAP, "44": TOKYO + FUCHU + SHINKIBA, "45": ""}
        session = FakeSession(route_areas(pages))
        dest = tmp_path / "stations.csv"
        dl = JmaStationMasterDownloader(
            dest=dest, staffed_only=True, request_interval=0.0, session=session
        )

        dl.download()

        assert dest.read_bytes().decode("utf-8") == (
            HEADER + "\r\ns47662,44,東京,トウキヨウ,35.6917,139.75,25.2,111111,1,1,1,1,1,1,\r\n"
        )

    def test_staffed_only_defaults_off(self, tmp_path):
        pages = {"00": PREFECTURE_MAP, "44": TOKYO + FUCHU, "45": ""}
        session = FakeSession(route_areas(pages))
        dest = tmp_path / "stations.csv"
        JmaStationMasterDownloader(dest=dest, request_interval=0.0, session=session).download()
        station_ids = [line.split(",")[0] for line in dest.read_text().splitlines()[1:]]
        assert station_ids == ["a1133", "s47662"]
```

- [ ] **Step 2: Run to verify failure** — `just test tests/test_jma.py::TestStationMasterDownload -x`. Expected: `TypeError: ... unexpected keyword argument 'staffed_only'`.

- [ ] **Step 3: Implement.** In `JmaStationMasterDownloader.__init__`, add the parameter (document it in the class docstring's Parameters section):

```python
    def __init__(
        self,
        dest: Path | str = Path("data/jma/stations.csv"),
        staffed_only: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.dest = Path(dest)
        self.staffed_only = staffed_only
```

Docstring addition under Parameters:

```
    staffed_only : bool, default False
        Write only staffed stations (気象官署, ``s``-prefixed ids) and drop
        every AMeDAS row. The scrape itself is unchanged — the same
        per-prefecture pages are fetched — only the output is filtered.
```

In `download()`, right before the `ordered = sorted(...)` line:

```python
        if self.staffed_only:
            stations = {sid: row for sid, row in stations.items() if sid.startswith("s")}
```

- [ ] **Step 4: Run the tests** — `just test tests/test_jma.py -x`. Expected: PASS.

- [ ] **Step 5: Update the seed script and its tests.** In `scripts/update_jma_stations_seed.py`, change the construction and module docstring:

```python
    downloader = JmaStationMasterDownloader(dest=args.dest, staffed_only=True)
```

Docstring first paragraph becomes:

```python
"""Regenerate the JMA station master dbt seed (staffed stations only).

Scrapes the station master (id, name, kana, prefecture, coordinates,
elevation, observed-element mask, end-of-observation date) from the JMA
obsdl per-prefecture station pages, keeps only staffed stations (気象官署,
s-prefixed ids — the 2026-08 re-scope; see
docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md) and
rewrites dbt/seeds/jma_stations.csv as UTF-8 with ISO dates. Roughly 60
requests at polite spacing, so expect ~5 minutes. dim_jma_station is built
from this seed.
"""
```

In `tests/test_jma_scripts.py`, update the fake to record and pass through the new kwarg (full replacement):

```python
def make_station_master_fake(record: dict, rows: list[dict] | None = None):
    """A ``JmaStationMasterDownloader`` stand-in that writes ``rows`` to ``dest`` if absent."""

    class FakeStationMaster:
        def __init__(self, dest, staffed_only=False):
            record["dest"] = Path(dest)
            record["staffed_only"] = staffed_only

        def download(self, force=False):
            record["download"] = {"force": force}
            if rows is not None and not record["dest"].exists():
                write_stations(record["dest"], rows)
            return record["dest"]

    return FakeStationMaster
```

Update both `TestUpdateJmaStationsSeed` assertions to include the flag, e.g.:

```python
        assert record == {
            "dest": script.SEED_PATH,
            "staffed_only": True,
            "download": {"force": True},
        }
```

(and the `--dest` override test analogously with `tmp_path / "stations.csv"`).

The `TestDownloadJmaHourlyAll` tests that assert on a `record` from `make_station_master_fake` will gain a `staffed_only` key once Task 4 passes it from the orchestrator — those tests don't assert full-dict equality on this record, so they stay green here.

- [ ] **Step 6: Run the affected suites** — `just test tests/test_jma.py tests/test_jma_scripts.py`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add power_market_analytics/jma.py scripts/update_jma_stations_seed.py tests/test_jma.py tests/test_jma_scripts.py
git commit -m "Filter the JMA station master seed to staffed stations"
```

---

### Task 3: `JmaHourlyDownloader` — time-windowed, stitched, write-once downloads

The core change. The per-request cap becomes a value budget; the year splits into windows; responses are stitched into one file.

**Files:**
- Modify: `power_market_analytics/jma.py` (JmaHourlyDownloader; module constants)
- Test: `tests/test_jma.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 4 and Task 5):
  - `SCRAPE_ELEMENTS: list[str]` module constant in `power_market_analytics/jma.py` = `["precipitation", "temperature", "wind", "sunshine", "snow_depth", "humidity", "solar_radiation"]` (8 value columns; file code string `101-201-301-401-501-605-610`).
  - `JmaHourlyDownloader.MAX_VALUES_PER_REQUEST: int = 44_000` (replaces `MAX_VALUE_COLUMNS`; adjust only if the Task 1 spike said so).
  - `JmaHourlyDownloader.window_count(elements: list[str], year: int) -> int`.
  - `JmaHourlyDownloader.download(station_id, elements, year, force=False, today=None) -> Path` — same signature, now transparently multi-request.
  - `_payload(station_id, elements, start: datetime.date, end: datetime.date) -> dict` — takes a date range, no `today` parameter, no clamping (clamping happens in `_windows`).

- [ ] **Step 1: Write the failing window-math tests.** Add to `tests/test_jma.py` (after `TestHourlyPaths`; `SCRAPE_ELEMENTS` is imported from `power_market_analytics.jma` in the existing import block):

```python
class TestWindows:
    def test_scrape_elements_is_the_rescope_set(self):
        assert SCRAPE_ELEMENTS == [
            "precipitation",
            "temperature",
            "wind",
            "sunshine",
            "snow_depth",
            "humidity",
            "solar_radiation",
        ]
        assert sum(ELEMENT_VALUE_COLUMNS[e] for e in SCRAPE_ELEMENTS) == 8

    def test_small_sets_fit_one_full_year_window(self):
        dl = JmaHourlyDownloader()
        core = ["precipitation", "temperature", "wind", "sunshine"]  # 5 cols, 43,920 leap values
        assert dl.window_count(core, 2024) == 1
        assert dl.window_count(["temperature"], 2016) == 1
        assert dl._windows(core, 2016, today=TODAY) == [
            (datetime.date(2016, 1, 1), datetime.date(2016, 12, 31))
        ]

    def test_scrape_set_needs_two_windows_split_at_midyear(self):
        dl = JmaHourlyDownloader()
        assert dl.window_count(SCRAPE_ELEMENTS, 2024) == 2  # leap: 70,272 values
        assert dl.window_count(SCRAPE_ELEMENTS, 2023) == 2  # 70,080 values
        assert dl._windows(SCRAPE_ELEMENTS, 2024, today=TODAY) == [
            (datetime.date(2024, 1, 1), datetime.date(2024, 7, 1)),
            (datetime.date(2024, 7, 2), datetime.date(2024, 12, 31)),
        ]

    def test_current_year_windows_are_clamped_to_yesterday(self):
        dl = JmaHourlyDownloader()
        # TODAY = 2026-08-18: window 2 starts Jul 2 and is cut at Aug 17.
        assert dl._windows(SCRAPE_ELEMENTS, 2026, today=TODAY) == [
            (datetime.date(2026, 1, 1), datetime.date(2026, 7, 1)),
            (datetime.date(2026, 7, 2), datetime.date(2026, 8, 17)),
        ]
        # Early in the year only the first (clamped) window remains.
        assert dl._windows(SCRAPE_ELEMENTS, 2026, today=datetime.date(2026, 3, 1)) == [
            (datetime.date(2026, 1, 1), datetime.date(2026, 2, 28)),
        ]

    def test_january_first_has_no_observable_days_yet(self):
        dl = JmaHourlyDownloader()
        assert dl._windows(["temperature"], 2026, today=datetime.date(2026, 1, 1)) == []
```

- [ ] **Step 2: Run to verify failure** — `just test tests/test_jma.py::TestWindows -x`. Expected: `ImportError: cannot import name 'SCRAPE_ELEMENTS'`.

- [ ] **Step 3: Implement the window math.** In `power_market_analytics/jma.py`: add `import calendar` and `import math` to the imports; after the `ELEMENT_VALUE_COLUMNS` definition add:

```python
#: The element set scraped for every staffed station: the core four plus the
#: 官署 additions chosen in the 2026-08 re-scope (spec:
#: docs/superpowers/specs/2026-08-20-jma-s-station-rescope-design.md).
#: 8 value columns -> 2 requests (windows) per station-year.
SCRAPE_ELEMENTS = [
    "precipitation",
    "temperature",
    "wind",
    "sunshine",
    "snow_depth",
    "humidity",
    "solar_radiation",
]
```

In `JmaHourlyDownloader`, replace the `MAX_VALUE_COLUMNS` constant with:

```python
    #: Empirical per-request cap on the number of values (value columns x
    #: hours). The largest proven-passing request is the core set over a
    #: full leap year (5 x 8784 = 43,920); the smallest proven-failing is
    #: ~61k (7 columns x a full year); 8-column half-years (~35k) were
    #: confirmed by the 2026-08-20 spike. Requests over the budget are
    #: split into windows and stitched, never rejected.
    MAX_VALUES_PER_REQUEST = 44_000
```

Add the three methods (full NumPy docstrings; shown compact here — write them out):

```python
    def window_count(self, elements: list[str], year: int) -> int:
        """Number of requests needed for one station-year of ``elements``.

        Parameters
        ----------
        elements : list of str
            Element names; keys of ``HOURLY_ELEMENTS``.
        year : int
            Calendar year (leap years have more hours).

        Returns
        -------
        int
            ``ceil(value columns x hours / MAX_VALUES_PER_REQUEST)``, >= 1.
        """
        hours = 24 * (366 if calendar.isleap(year) else 365)
        values = hours * sum(ELEMENT_VALUE_COLUMNS[e] for e in elements)
        return max(1, math.ceil(values / self.MAX_VALUES_PER_REQUEST))

    def _window_bounds(self, elements: list[str], year: int) -> list[tuple]:
        """Split ``year`` into the request windows for ``elements``.

        Returns
        -------
        list of (datetime.date, datetime.date)
            ``window_count`` contiguous, non-overlapping [start, end] spans
            covering Jan 1 .. Dec 31.
        """
        n = self.window_count(elements, year)
        first = datetime.date(year, 1, 1)
        days = (datetime.date(year, 12, 31) - first).days + 1
        return [
            (
                first + datetime.timedelta(days=i * days // n),
                first + datetime.timedelta(days=(i + 1) * days // n - 1),
            )
            for i in range(n)
        ]

    def _windows(self, elements: list[str], year: int, today: datetime.date) -> list[tuple]:
        """The windows to actually request: bounds clamped to yesterday.

        An end date later than yesterday makes the endpoint return an HTML
        error page, so current-year windows are cut at yesterday and
        entirely-future windows are dropped.

        Returns
        -------
        list of (datetime.date, datetime.date)
            May be empty (e.g. on January 1, when the year has no
            observable days yet).
        """
        yesterday = today - datetime.timedelta(days=1)
        return [
            (start, min(end, yesterday))
            for start, end in self._window_bounds(elements, year)
            if start <= yesterday
        ]
```

- [ ] **Step 4: Run the window tests** — `just test tests/test_jma.py::TestWindows -x`. Expected: PASS.

- [ ] **Step 5: Write the failing payload/stitch/download tests.** Changes in `tests/test_jma.py`:

(a) `TestHourlyPayload` — `_payload` now takes dates; replace the four tests with:

```python
class TestHourlyPayload:
    def test_full_year_span(self):
        dl = JmaHourlyDownloader()
        payload = dl._payload(
            "s47662",
            ["temperature", "precipitation"],
            datetime.date(2016, 1, 1),
            datetime.date(2016, 12, 31),
        )
        assert payload == PAST_YEAR_PAYLOAD

    def test_partial_span_uses_the_given_dates_verbatim(self):
        dl = JmaHourlyDownloader()
        payload = dl._payload(
            "a0368", ["wind"], datetime.date(2026, 7, 2), datetime.date(2026, 8, 17)
        )
        assert payload["ymdList"] == '["2026","2026","7","8","2","17"]'
        assert payload["stationNumList"] == '["a0368"]'
        assert payload["elementNumList"] == '[["301",""]]'
```

(b) `TestValidateElements` — delete `test_over_the_value_column_cap` and `test_exactly_at_the_cap_is_allowed` (the cap no longer rejects; TestWindows covers the budget). Keep empty/unknown/duplicate tests.

(c) New stitch + multi-window download tests. Add module-level fixtures near `CSV_BYTES`:

```python
WINDOW1_BYTES = (CSV_HEAD + "2024/1/1 1:00,5.1,8,1,0.0,8,1\r\n2024/1/1 2:00,4.8,8,1,0.0,8,1\r\n").encode("cp932")
WINDOW2_BYTES = (CSV_HEAD + "2024/7/2 1:00,25.3,8,1,0.0,8,1\r\n").encode("cp932")
STITCHED_BYTES = WINDOW1_BYTES + "2024/7/2 1:00,25.3,8,1,0.0,8,1\r\n".encode("cp932")
```

and the tests:

```python
class TestStitch:
    def test_single_part_is_returned_as_is(self):
        assert JmaHourlyDownloader._stitch([WINDOW1_BYTES]) == WINDOW1_BYTES

    def test_later_parts_contribute_only_data_rows(self):
        assert JmaHourlyDownloader._stitch([WINDOW1_BYTES, WINDOW2_BYTES]) == STITCHED_BYTES

    def test_first_part_without_trailing_newline_still_stitches_cleanly(self):
        stitched = JmaHourlyDownloader._stitch([WINDOW1_BYTES.rstrip(b"\r\n"), WINDOW2_BYTES])
        assert stitched == STITCHED_BYTES
```

In `TestHourlyDownload` add:

```python
    def test_multi_window_set_posts_per_window_and_writes_one_stitched_file(self, tmp_path):
        session = FakeSession([FakeResponse(WINDOW1_BYTES), FakeResponse(WINDOW2_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)

        path = dl.download("s47662", SCRAPE_ELEMENTS, 2024, today=TODAY)

        assert path == tmp_path / "s47662_101-201-301-401-501-605-610_2024.csv"
        assert path.read_bytes() == STITCHED_BYTES
        assert [c["data"]["ymdList"] for c in session.calls] == [
            '["2024","2024","1","7","1","1"]',
            '["2024","2024","7","12","2","31"]',
        ]
        codes = '[["101",""],["201",""],["301",""],["401",""],["501",""],["605",""],["610",""]]'
        assert {c["data"]["elementNumList"] for c in session.calls} == {codes}

    def test_html_error_on_a_later_window_writes_nothing(self, tmp_path):
        session = FakeSession([FakeResponse(WINDOW1_BYTES), FakeResponse(HTML_ERROR)])
        data_dir = tmp_path / "hourly"
        dl = JmaHourlyDownloader(data_dir=data_dir, request_interval=0.0, session=session)

        with pytest.raises(ValueError, match=r"2024-07-02\.\.2024-12-31 \(not a JMA CSV\)"):
            dl.download("s47662", SCRAPE_ELEMENTS, 2024, today=TODAY)

        assert not data_dir.exists()

    def test_year_with_no_observable_days_raises_before_any_http(self, tmp_path):
        session = FakeSession([FakeResponse(CSV_BYTES)])
        dl = JmaHourlyDownloader(data_dir=tmp_path, request_interval=0.0, session=session)
        with pytest.raises(ValueError, match="no observable days"):
            dl.download("s47662", ["temperature"], 2026, today=datetime.date(2026, 1, 1))
        assert session.calls == []
```

(d) Update the existing single-window tests to the new error-message shape: in `test_html_response_raises_and_writes_nothing`, the match becomes `r"s47662/\['temperature'\]/2016-01-01\.\.2016-12-31 \(not a JMA CSV\)"`.

- [ ] **Step 6: Run to verify failure** — `just test tests/test_jma.py -x`. Expected: failures on `_stitch` (no attribute) and payload signature.

- [ ] **Step 7: Implement download/stitch/payload.** In `JmaHourlyDownloader`:

Replace `_payload`'s signature and period handling (drop the `today` parameter and the yesterday clamp — `_windows` owns that now); the only body changes:

```python
    def _payload(
        self,
        station_id: str,
        elements: list[str],
        start: datetime.date,
        end: datetime.date,
    ) -> dict:
```

and

```python
            "ymdList": (
                f'["{start.year}","{end.year}","{start.month}","{end.month}",'
                f'"{start.day}","{end.day}"]'
            ),
```

(update the docstring: Parameters now describe `start`/`end`, noting the caller must keep `end` <= yesterday).

Add the stitcher (class-level regex next to it):

```python
    _DATA_ROW = re.compile(rb"^\d{4}/")

    @classmethod
    def _stitch(cls, parts: list[bytes]) -> bytes:
        """Concatenate window responses into one CSV.

        The first part is kept whole (download-timestamp line, blank line,
        header rows, data); subsequent parts contribute only their data
        rows, so the header block appears exactly once. Note: 均質番号
        restarts at 1 in every server response, so in the stitched file the
        numbering resets at each window boundary — breaks are only
        meaningful within a window.

        Parameters
        ----------
        parts : list of bytes
            One cp932 response body per window, in chronological order.

        Returns
        -------
        bytes
            The stitched file content, CRLF line endings throughout.
        """
        stitched = bytearray(parts[0])
        if not stitched.endswith(b"\r\n"):
            stitched += b"\r\n"
        for part in parts[1:]:
            for line in part.split(b"\r\n"):
                if cls._DATA_ROW.match(line):
                    stitched += line + b"\r\n"
        return bytes(stitched)
```

Rewrite `download`'s body after the cache check (the docstring's element description changes to "any subset of ``HOURLY_ELEMENTS``; sets over the per-request value budget are fetched in multiple time windows and stitched into one file", and Raises drops the over-cap ValueError in favor of "the year has no observable days yet, or a response is not a CSV"):

```python
        windows = self._windows(elements, year, today)
        if not windows:
            raise ValueError(f"Year {year} has no observable days before {today}")
        parts = []
        for start, end in windows:
            logger.info("Downloading {} {} {}..{}", station_id, sorted(elements), start, end)
            response = self._post_with_retry(
                self.SHOW_TABLE_URL, self._payload(station_id, elements, start, end)
            )
            head = response.content[:64].decode(self.ENCODING, errors="replace")
            if not head.startswith("ダウンロードした時刻"):
                raise ValueError(
                    f"Unexpected response for {station_id}/{sorted(elements)}/"
                    f"{start}..{end} (not a JMA CSV): {head!r}"
                )
            parts.append(response.content)

        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Write-once: assemble in memory, land via temp-file + rename, and
        # never append to or patch an existing file (stale current-year
        # files are replaced wholesale via force=True).
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(self._stitch(parts))
        partial.replace(dest)
        logger.info("Saved {} ({} bytes)", dest, dest.stat().st_size)
        return dest
```

In `_validate_elements`, delete the value-column branch (the last `if columns > ...` block and the `columns = ...` line) and drop the budget sentence from its docstring. Update the class docstring's Examples if they mention the cap, and the module docstring comment near `HOURLY_ELEMENTS` referencing `MAX_VALUE_COLUMNS` if present (grep for `MAX_VALUE_COLUMNS` — no references may remain anywhere; `docs/` references are handled in Task 7).

- [ ] **Step 8: Run the whole file** — `just test tests/test_jma.py`. Expected: PASS. Then `just test` (full suite) — expected: FAIL only if something outside `tests/test_jma.py` referenced `MAX_VALUE_COLUMNS` or `_payload`'s old signature; fix those call sites the same way (there should be none — `scripts/` still uses `download(...)`).

- [ ] **Step 9: Commit**

```bash
git add power_market_analytics/jma.py tests/test_jma.py
git commit -m "Add time-windowed, stitched downloads to JmaHourlyDownloader"
```

---

### Task 4: Point both download scripts at `SCRAPE_ELEMENTS`

**Files:**
- Modify: `scripts/download_jma_hourly_all.py`
- Modify: `scripts/download_jma_hourly.py`
- Test: `tests/test_jma_scripts.py`

**Interfaces:**
- Consumes: `SCRAPE_ELEMENTS`, `window_count`, `staffed_only` from Tasks 2–3.
- Produces: `refresh-jma`'s download step now plans s-stations × years × the 7-element set; file names use `101-201-301-401-501-605-610`.

- [ ] **Step 1: Update the failing tests first.** In `tests/test_jma_scripts.py`:
  - Replace the module constant: `CORE_ELEMENTS = [...]` becomes `from power_market_analytics.jma import SCRAPE_ELEMENTS` (adjust the existing import line) and every use of the local `CORE_ELEMENTS` / inline `elements = ["temperature", ...]` list in assertions becomes `SCRAPE_ELEMENTS`.
  - Replace the `core_path` helper:

```python
def scrape_path(data_dir: Path, station_id: str, year: int) -> Path:
    """Where the orchestrator expects a stitched file (hand-derived name)."""
    return data_dir / f"{station_id}_101-201-301-401-501-605-610_{year}.csv"
```

  and update its call sites (they assert cache behavior of `TestDownloadJmaHourlyAll`).
  - In `TestDownloadJmaHourly.test_defaults_cover_tokyo_core_set_from_2016_forcing_only_this_year`, rename to `test_defaults_cover_tokyo_scrape_set_from_2016_forcing_only_this_year` and expect `SCRAPE_ELEMENTS` as the default element list.
  - In `TestDownloadJmaHourlyAll`, add one assertion to an existing full-run test that the station-master fake was constructed with `staffed_only=True` (the `record` dict from `make_station_master_fake` now carries the key):

```python
        assert master_record["staffed_only"] is True
```

  (name the record dict passed to `make_station_master_fake` in that test `master_record` if it's currently `{}` inline.)

- [ ] **Step 2: Run to verify failure** — `just test tests/test_jma_scripts.py -x`. Expected: assertions fail on the old core element list / file name.

- [ ] **Step 3: Implement.** In `scripts/download_jma_hourly_all.py`:
  - Import: `from power_market_analytics.jma import JmaHourlyDownloader, JmaStationMasterDownloader, SCRAPE_ELEMENTS`; delete the local `CORE_ELEMENTS` and replace its three uses with `SCRAPE_ELEMENTS`.
  - `JmaStationMasterDownloader(dest=args.stations_csv, staffed_only=True).download()`.
  - Replace the estimate log block with:

```python
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
```

  - Rewrite the module docstring: staffed stations only, 7-element set, 2 windows per station-year stitched into one file, ~159 stations × 11 years ≈ 3,450 requests ≈ 14 h cold (~7 GB → smaller now), same nohup advice. Mention the spec path.

In `scripts/download_jma_hourly.py`: change the `--elements` default to `SCRAPE_ELEMENTS` (import it), and extend the flag help + module docstring: "large sets are fetched in multiple windows per year and stitched into one file (bounded by JMA's per-request data-volume budget; wind counts as two value columns)".

- [ ] **Step 4: Run** — `just test tests/test_jma_scripts.py`. Expected: PASS. Then `just test` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/download_jma_hourly_all.py scripts/download_jma_hourly.py tests/test_jma_scripts.py
git commit -m "Scrape the 7-element staffed set in both JMA download scripts"
```

---

### Task 5: Load contract (26 columns) + loader wiring

**Files:**
- Modify: `conf/schemas/jma_hourly_staffed.yaml` (full rewrite)
- Delete: `conf/schemas/jma_hourly_amedas.yaml`
- Modify: `scripts/load_jma_hourly.py`
- Test: `tests/test_jma_loader.py` (major rewrite), `tests/test_load_scripts.py` (TestLoadJmaHourly)

**Interfaces:**
- Consumes: the Task 1 spike's real header/data rows (verify the fixture below against them; fix the fixture, not the spike).
- Produces: `pma_raw.jma_hourly_staffed` with 27 contract columns (station_id + 26 physical); `FORMATS = [("jma_hourly_staffed", "s*_101-201-301-401-501-605-610_*.csv", "pma_raw.jma_hourly_staffed")]`. Column names consumed by Task 6: `snow_depth_cm`, `snow_depth_quality_flag`, `snow_depth_homogeneity_no`, `humidity_pct`, `humidity_quality_flag`, `humidity_homogeneity_no`, `solar_radiation_mjm2`, `solar_radiation_quality_flag`, `solar_radiation_homogeneity_no`.

- [ ] **Step 1: Rewrite the test fixtures and tests (failing first).** In `tests/test_jma_loader.py`:
  - Delete `AMEDAS_CONTRACT`, `AMEDAS_HEADER`, `AMEDAS_ROWS`, `amedas_file`, and every `TestJmaHourlyCsvLoaderLoad` test that exercises the AMeDAS layout (`test_amedas_file_loads_positionally...`, `test_staffed_layout_against_amedas_contract_fails_loudly`, `test_amedas_layout_against_staffed_contract_fails_loudly`). Port their generic behaviors (grain violation, unparseable name, headers-only, missing non-nullable flag, two stations share a table, wrong column count fails loudly) to staffed fixtures.
  - New 26-column staffed fixture (element groups in ascending code order; **verify labels/orders against the spike output**):

```python
#: 26-column staffed layout: precip(4) temp(3) wind(5) sunshine(4) snow(3)
#: humidity(3) solar(3) after the timestamp. Shaped like the 2026-08-20
#: spike response for 東京.
STAFFED_HEADER = [
    "ダウンロードした時刻：2026/08/20 12:49:53",
    "",
    "," + ",".join(["東京"] * 25),
    "年月日時,降水量(mm),降水量(mm),降水量(mm),降水量(mm),気温(℃),気温(℃),気温(℃),"
    "風速(m/s),風速(m/s),風速(m/s),風速(m/s),風速(m/s),"
    "日照時間(時間),日照時間(時間),日照時間(時間),日照時間(時間),"
    "積雪(cm),積雪(cm),積雪(cm),相対湿度(％),相対湿度(％),相対湿度(％),"
    "全天日射量(MJ/㎡),全天日射量(MJ/㎡),全天日射量(MJ/㎡)",
    ",,,,,,,,,,風向,風向,,,,,,,,,,,,,,",
    ",,現象なし情報,品質情報,均質番号,,品質情報,均質番号,,品質情報,,品質情報,均質番号,"
    ",現象なし情報,品質情報,均質番号,,品質情報,均質番号,,品質情報,均質番号,,品質情報,均質番号",
]
STAFFED_ROWS = [
    # Daytime winter hour: snow on the ground, some sun.
    "2024/1/1 10:00:00,0,1,8,1,5.2,8,1,2.4,8,北西,8,1,0.4,0,8,1,3,8,1,45,8,1,1.25,8,1",
    # Night hour: no sunshine (true zero), zero solar radiation.
    "2024/1/1 23:00:00,1.5,0,8,1,4.0,8,1,2.0,8,北,8,1,0,1,8,1,3,8,1,60,8,1,0.0,8,1",
]
```

    Verify the value formats against the spike output before finalizing (e.g. whether 積雪/湿度 print as ints and how a missing hour renders); the contract's `nullable: false` flags must match reality — if the spike shows an empty flag cell anywhere, relax that flag in the Step 3 contract and document it. For the ported missing-flag test, construct the bad row by editing row 1 (blank out `precipitation_quality_flag`, position `_c3`), mirroring the existing `test_missing_non_nullable_flag_fails_the_load` pattern.
  - Update `STAFFED_CONTRACT` expected column count test to 26, `_sniff_column_count` test to 26, and file names to `s47662_101-201-301-401-501-605-610_2024.csv`.
  - Load test asserting the new columns land typed, in the spirit of the existing one:

```python
    def test_staffed_file_loads_through_the_contract(self, spark, tmp_path):
        write_cp932(
            tmp_path / "s47662_101-201-301-401-501-605-610_2024.csv",
            STAFFED_HEADER + STAFFED_ROWS[:2],
        )
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.staffed", spark=spark)

        assert loader.load() == 2

        rows = {r.observed_at: r for r in spark.table("test_jma.staffed").collect()}
        r1 = rows[datetime.datetime(2024, 1, 1, 10, 0)]
        assert r1.station_id == "s47662"
        assert (r1.snow_depth_cm, r1.snow_depth_quality_flag) == (3, 8)
        assert (r1.humidity_pct, r1.humidity_quality_flag) == (45, 8)
        assert (r1.solar_radiation_mjm2, r1.solar_radiation_quality_flag) == (1.25, 8)
        r2 = rows[datetime.datetime(2024, 1, 1, 23, 0)]
        assert r2.solar_radiation_mjm2 == 0.0
        assert (r2.sunshine_duration_h, r2.sunshine_phenomenon_absent) == (0.0, 1)
```

  - Keep a wrong-layout guard: write an old 17-column core file and assert `"first data row has 17 columns, contract expects 26"`.

- [ ] **Step 2: Run to verify failure** — `just test tests/test_jma_loader.py -x`. Expected: contract mismatch (yaml still 17 columns).

- [ ] **Step 3: Rewrite the contract.** `conf/schemas/jma_hourly_staffed.yaml` (adjust `type`s for snow/humidity/solar only if the spike contradicts int/int/double):

```yaml
description: >
  JMA hourly observations from staffed stations (気象官署, s-prefixed station
  ids — the network was re-scoped to staffed stations only in 2026-08), the
  7-element scrape set 降水量+気温+風向・風速+日照時間+積雪の深さ+相対湿度+
  全天日射量 (element codes 101-201-301-401-501-605-610): a 26-column layout
  per docs/JMA-Weather-Data-Retrieval.md §7. The phenomenon-recording
  elements 降水量 and 日照時間 each carry a 現象なし情報 column: 1 = no
  phenomenon that hour (the 0 value is a true zero), 0 = phenomenon
  occurred, empty when the quality flag is 2/1/0. Files are stitched from
  two half-year requests per station-year by JmaHourlyDownloader (the
  8-value-column set exceeds the per-request budget) and read positionally
  (source _c0.._c25) by JmaHourlyCsvLoader; station_id is injected from the
  file name. One row per station and hour; hours run 01:00-24:00 JST with
  24:00 stored as 00:00 of the next day. Quality flags: 8 normal,
  5 quasi-normal, 4 insufficient, 2 questionable, 1 missing, 0 not
  observed; value cells are empty when the flag is 2/1/0. Wind direction is
  a 16-point compass word (北西 etc.) or 静穏 (calm). Homogeneity numbers
  restart at 1 in every REQUEST WINDOW (half-year), not just every file, so
  in a stitched year file the numbering resets at the window boundary —
  breaks are only meaningful within a window.

read_options:
  # Java charset name for cp932 / Shift_JIS with Windows extensions
  encoding: windows-31j

grain: [station_id, observed_at]

columns:
  - { name: station_id, source: __station_id, type: string, nullable: false }
  - { name: observed_at, source: _c0, type: timestamp, format: yyyy/M/d H:mm:ss, nullable: false }
  - { name: precipitation_mm, source: _c1, type: double }
  - { name: precipitation_phenomenon_absent, source: _c2, type: int }
  - { name: precipitation_quality_flag, source: _c3, type: int, nullable: false }
  - { name: precipitation_homogeneity_no, source: _c4, type: int, nullable: false }
  - { name: temperature_c, source: _c5, type: double }
  - { name: temperature_quality_flag, source: _c6, type: int, nullable: false }
  - { name: temperature_homogeneity_no, source: _c7, type: int, nullable: false }
  - { name: wind_speed_ms, source: _c8, type: double }
  - { name: wind_speed_quality_flag, source: _c9, type: int, nullable: false }
  - { name: wind_direction, source: _c10, type: string }
  - { name: wind_direction_quality_flag, source: _c11, type: int, nullable: false }
  - { name: wind_homogeneity_no, source: _c12, type: int, nullable: false }
  - { name: sunshine_duration_h, source: _c13, type: double }
  - { name: sunshine_phenomenon_absent, source: _c14, type: int }
  - { name: sunshine_quality_flag, source: _c15, type: int, nullable: false }
  - { name: sunshine_homogeneity_no, source: _c16, type: int, nullable: false }
  - { name: snow_depth_cm, source: _c17, type: int }
  - { name: snow_depth_quality_flag, source: _c18, type: int, nullable: false }
  - { name: snow_depth_homogeneity_no, source: _c19, type: int, nullable: false }
  - { name: humidity_pct, source: _c20, type: int }
  - { name: humidity_quality_flag, source: _c21, type: int, nullable: false }
  - { name: humidity_homogeneity_no, source: _c22, type: int, nullable: false }
  - { name: solar_radiation_mjm2, source: _c23, type: double }
  - { name: solar_radiation_quality_flag, source: _c24, type: int, nullable: false }
  - { name: solar_radiation_homogeneity_no, source: _c25, type: int, nullable: false }
```

Delete `conf/schemas/jma_hourly_amedas.yaml` (`git rm`). If a fixture row legitimately shows an empty flag cell in the spike data (like the AMeDAS wind quirk), relax that one flag to nullable and document it in the description — evidence over plan.

- [ ] **Step 4: Update the load script.** `scripts/load_jma_hourly.py` — `FORMATS` becomes:

```python
#: (schema file stem, file glob, destination table).
FORMATS = [
    (
        "jma_hourly_staffed",
        "s*_101-201-301-401-501-605-610_*.csv",
        "pma_raw.jma_hourly_staffed",
    ),
]
```

Module docstring: drop the two-layout paragraph; state one 26-column staffed layout, stitched files, and that the loader's column-count check now guards JMA layout drift rather than station-class mixups.

- [ ] **Step 5: Update `tests/test_load_scripts.py` `TestLoadJmaHourly`** — single-entry expectations: `test_loads_both_layouts_with_defaults` → `test_loads_the_staffed_layout_with_defaults` (one build: glob `s*_101-201-301-401-501-605-610_*.csv`, table `pma_raw.jma_hourly_staffed`, `schema.columns[-1].source == "_c25"`); the schema-dir override test writes only the `jma_hourly_staffed` stub; `test_formats_table_is_the_source_of_truth` asserts the new single-entry list.

- [ ] **Step 6: Run** — `just test tests/test_jma_loader.py tests/test_load_scripts.py`, then the full `just test` (coverage gate must hold — the deleted AMeDAS tests must not leave uncovered lines; `grep -rn "amedas" power_market_analytics/ scripts/ tests/` should return nothing). Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A conf/schemas scripts/load_jma_hourly.py tests/test_jma_loader.py tests/test_load_scripts.py
git commit -m "Widen the staffed load contract to 26 columns and drop the AMeDAS leg"
```

---

### Task 6: dbt — single-source models with 9 new columns

**Files:**
- Modify: `dbt/models/raw/jma.yml`
- Delete: `dbt/models/staging/stg_jma__hourly_amedas.sql`, `dbt/models/staging/stg_jma__hourly_amedas.yml`
- Modify: `dbt/models/staging/stg_jma__hourly_staffed.sql`, `.yml`
- Modify: `dbt/models/standardized/std_jma__hourly.sql`, `.yml`
- Modify: `dbt/models/curated/fct_jma_weather_hourly.sql`, `.yml`
- Modify: `dbt/models/curated/dim_jma_station.yml`

**Interfaces:**
- Consumes: raw column names from Task 5.
- Produces: `fct_jma_weather_hourly` with 9 additional columns (same names as raw). Grain everywhere unchanged: (station_id, observed_at).

The 9-column block, used verbatim in stg/std/fct SQL selects (append after `sunshine_homogeneity_no` in each select list):

```sql
    snow_depth_cm,
    snow_depth_quality_flag,
    snow_depth_homogeneity_no,
    humidity_pct,
    humidity_quality_flag,
    humidity_homogeneity_no,
    solar_radiation_mjm2,
    solar_radiation_quality_flag,
    solar_radiation_homogeneity_no
```

- [ ] **Step 1: `dbt/models/raw/jma.yml`.** Delete the entire `jma_hourly_amedas` table block. Rewrite the source `description` (drop "two fixed layouts", state: staffed stations only since the 2026-08 re-scope, 7-element set, one 26-column layout, files stitched from two half-year requests, and: "Homogeneity numbers restart at 1 per REQUEST WINDOW (half-year), so in a stitched year file the numbering resets at the window boundary — only within-window changes mark observation-environment breaks, and a real break exactly on the boundary is invisible in the CSV alone."). In the `jma_hourly_staffed` table block, update its description the same way and append column docs:

```yaml
          - name: snow_depth_cm
            description: Snow depth at observed_at in cm (積雪の深さ).
          - name: snow_depth_quality_flag
            description: Quality flag for snow depth (品質情報).
          - name: snow_depth_homogeneity_no
            description: Homogeneity number for snow depth (均質番号; resets per stitched half-year window).
          - name: humidity_pct
            description: Relative humidity at observed_at in percent (相対湿度).
          - name: humidity_quality_flag
            description: Quality flag for humidity (品質情報).
          - name: humidity_homogeneity_no
            description: Homogeneity number for humidity (均質番号; resets per stitched half-year window).
          - name: solar_radiation_mjm2
            description: Global solar radiation over the observation hour in MJ/m² (全天日射量(前1時間)).
          - name: solar_radiation_quality_flag
            description: Quality flag for solar radiation (品質情報).
          - name: solar_radiation_homogeneity_no
            description: Homogeneity number for solar radiation (均質番号; resets per stitched half-year window).
```

- [ ] **Step 2: staging.** `git rm dbt/models/staging/stg_jma__hourly_amedas.sql dbt/models/staging/stg_jma__hourly_amedas.yml`. In `stg_jma__hourly_staffed.sql`, append the 9-column block to the select. In its `.yml`, append after `sunshine_homogeneity_no` (mirroring the existing quality-flag test style):

```yaml
      - name: snow_depth_cm
        data_type: int
      - name: snow_depth_quality_flag
        data_type: int
        data_tests:
          - accepted_values:
              arguments:
                values: [0, 1, 2, 4, 5, 8]
      - name: snow_depth_homogeneity_no
        data_type: int
      - name: humidity_pct
        data_type: int
      - name: humidity_quality_flag
        data_type: int
        data_tests:
          - accepted_values:
              arguments:
                values: [0, 1, 2, 4, 5, 8]
      - name: humidity_homogeneity_no
        data_type: int
      - name: solar_radiation_mjm2
        data_type: double
      - name: solar_radiation_quality_flag
        data_type: int
        data_tests:
          - accepted_values:
              arguments:
                values: [0, 1, 2, 4, 5, 8]
      - name: solar_radiation_homogeneity_no
        data_type: int
```

Also update the stg model description ("core-element" → "7-element scrape set").

- [ ] **Step 3: `std_jma__hourly.sql`.** Remove the `staffed`/`amedas`/`unioned` CTEs entirely; the model becomes source → final:

```sql
with
  source as (
  select * from {{ ref('stg_jma__hourly_staffed') }}
  ),

  final as (
  select
    station_id,
    observed_at,
    timestampadd(hour, -1, observed_at) as observed_hour_start_at,
    cast(timestampadd(hour, -1, observed_at) as date) as observed_date,
    case
      when month(timestampadd(hour, -1, observed_at)) >= 4
      then year(timestampadd(hour, -1, observed_at))
      else year(timestampadd(hour, -1, observed_at)) - 1
    end as fiscal_year,
    precipitation_mm,
    precipitation_phenomenon_absent,
    precipitation_quality_flag,
    precipitation_homogeneity_no,
    temperature_c,
    temperature_quality_flag,
    temperature_homogeneity_no,
    wind_speed_ms,
    wind_speed_quality_flag,
    wind_direction,
    wind_direction_quality_flag,
    wind_homogeneity_no,
    sunshine_duration_h,
    sunshine_phenomenon_absent,
    sunshine_quality_flag,
    sunshine_homogeneity_no,
    snow_depth_cm,
    snow_depth_quality_flag,
    snow_depth_homogeneity_no,
    humidity_pct,
    humidity_quality_flag,
    humidity_homogeneity_no,
    solar_radiation_mjm2,
    solar_radiation_quality_flag,
    solar_radiation_homogeneity_no
  from
    source
  )

select * from final
```

In `std_jma__hourly.yml`: rewrite the description — no union/AMeDAS mention; keep the time-axis semantics; the phenomenon_absent sentence becomes "The phenomenon_absent columns are null only when the quality flag is 2/1/0"; replace the homogeneity sentence with "Homogeneity numbers are only comparable within one request window (half-year) of one stitched station-year file." Append the same 9 column entries as staging (data_type only + the three quality-flag accepted_values tests).

- [ ] **Step 4: fct.** In `fct_jma_weather_hourly.sql`, append the 9-column block to the final select. In `.yml`: update the description (drop the AMeDAS/null sentence as in std; add the window-reset homogeneity note) and append:

```yaml
      - name: snow_depth_cm
        data_type: int
        description: Snow depth at observed_at in cm (積雪の深さ).
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 800
      - name: snow_depth_quality_flag
        data_type: int
        description: Quality flag for snow depth.
      - name: snow_depth_homogeneity_no
        data_type: int
        description: Homogeneity number for snow depth (resets per stitched half-year window).
      - name: humidity_pct
        data_type: int
        description: Relative humidity at observed_at in percent.
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 100
      - name: humidity_quality_flag
        data_type: int
        description: Quality flag for humidity.
      - name: humidity_homogeneity_no
        data_type: int
        description: Homogeneity number for humidity (resets per stitched half-year window).
      - name: solar_radiation_mjm2
        data_type: double
        description: Global solar radiation over the observation hour in MJ/m².
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 5
      - name: solar_radiation_quality_flag
        data_type: int
        description: Quality flag for solar radiation.
      - name: solar_radiation_homogeneity_no
        data_type: int
        description: Homogeneity number for solar radiation (resets per stitched half-year window).
```

- [ ] **Step 5: `dim_jma_station.yml`.** Update the model description (station master now staffed-only by re-scope; keep the Antarctica note) and tighten `station_type`'s test to `values: [staffed]`, its description to "always staffed (気象官署) since the 2026-08 re-scope; the amedas value would reappear only if AMeDAS ingestion returns". Leave `dim_jma_station.sql` unchanged (the case expression is harmless and self-documenting).

- [ ] **Step 6: Validate compile-time.** From the host: `cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse`. Expected: success, no contract errors, no deprecation warnings. (**Full `dbt build` is deliberately deferred to Task 8** — the warehouse's raw table still has the old 17-column shape until the backfill lands; building now would fail on the new columns. Do not "fix" that by building here.)

- [ ] **Step 7: Commit**

```bash
git add -A dbt/models
git commit -m "Single-source JMA dbt models with snow, humidity and solar columns"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/JMA-Weather-Data-Retrieval.md`
- Modify: `CLAUDE.md`, `AGENTS.md` (AGENTS.md mirrors CLAUDE.md — apply the same JMA edits if its JMA sections match; it is untracked, so it will not be committed)
- Modify: `justfile` (refresh-jma doc comment)

**Interfaces:** none — prose only. Keep every claim consistent with the code committed in Tasks 2–6.

- [ ] **Step 1: `docs/JMA-Weather-Data-Retrieval.md`.**
  - §4.1/§4.2 (Stations): add a short paragraph that the pipeline scrapes staffed stations only since 2026-08 (spec path), the seed is filtered via `staffed_only`, and AMeDAS remains documented for reference.
  - §5 (elements table): mark the seven scraped elements, e.g. add a "Scraped" column with ✓ on 101/201/301/401/501/605/610, and note beneath: "The scrape set is `SCRAPE_ELEMENTS` in `power_market_analytics/jma.py` (8 value columns → 2 windows per station-year)."
  - §6.1: append: "The downloader models the cap as `MAX_VALUES_PER_REQUEST = 44_000` values (columns × hours) and splits over-budget station-years into time windows instead of rejecting them; the 2026-08-20 spike confirmed an 8-column half-year (~35k values) passes."
  - §6.3 (packing math): rewrite for the current scrape: 159 staffed stations (156 active; 阿蘇山 contributes 2016–2017), 7 elements = 8 columns → 2 requests/station-year ≈ 3,450 requests ≈ 14 h cold at ~15 s/request; a current-year refresh is ~320 requests ≈ 1.5 h. Keep the historical AMeDAS math as a struck-through or "historical" note if useful, otherwise delete.
  - §7.1: add after the layout bullet list: "Files on disk are stitched: `JmaHourlyDownloader` fetches over-budget years in windows and keeps the header block of the first window only, appending later windows' data rows. Files are write-once — assembled in memory, landed atomically, never appended to; a stale current-year file is replaced wholesale."
  - §7.3 (均質番号 paragraph): append: "**Stitched files sharpen this caveat**: numbering restarts per request *window* (half-year), not just per file, so a stitched year file resets 均質番号 at the mid-year boundary and a real break exactly on that boundary is invisible in the CSV alone."
  - §7.5: replace the two-format/five-format consequences with the current floor: one format — all staffed stations, the 7-element set, 26 columns; the format table shrinks to that single row; keep the "layout is a pure function of (element set) × (station class)" finding.
  - §8: update the example to the new reality:

```python
# One station-year, the full 7-element scrape set — two requests, one file.
downloader.download("s47662", SCRAPE_ELEMENTS, 2016)
# -> data/jma/hourly/s47662_101-201-301-401-501-605-610_2016.csv
```

  and delete the `ValueError: needs 6 value columns` example (the cap now windows instead of rejecting). Update "The full-network scrape" numbers (159 stations, ~14 h).
- [ ] **Step 2: `CLAUDE.md`.**
  - `just refresh-jma` bullet: "regenerate the station seed (~5 min, staffed stations only), download stitched 7-element hourly CSVs (args pass through, e.g. `--prefecture 44`; no args = all ~159 staffed stations, ~14 h cold), reload `raw`, `dbt build`."
  - Architecture JMA bullet: one loader contract (`conf/schemas/jma_hourly_staffed.yaml`, 26 columns) → `pma_raw.jma_hourly_staffed` only; note the stitched-file + 均質番号-per-window caveat in one clause.
  - Update the demand-task bullet's parenthetical about s47772/s47662 staleness only if Task 8 has already refreshed the data (otherwise leave for Task 8's memory/doc sweep).
  - Apply the same edits to `AGENTS.md` if it contains the same text.
- [ ] **Step 3: `justfile`.** Update the `refresh-jma` doc attribute: `[doc("Refresh JMA weather data: update staffed-station seed, download stitched 7-element hourly files (args pass through, e.g. --prefecture 44; ~14 h cold), reload raw, rebuild + test dbt")]`.
- [ ] **Step 4: Sanity pass** — `grep -rn "amedas\|AMeDAS" CLAUDE.md justfile docs/JMA-Weather-Data-Retrieval.md` and confirm every remaining mention is intentional (historical/reference context, e.g. §4's station-type taxonomy stays).
- [ ] **Step 5: Commit**

```bash
git add docs/JMA-Weather-Data-Retrieval.md CLAUDE.md justfile
git commit -m "Document the s-station re-scope, stitched downloads and 均質番号 caveat"
```

---

### Task 8: Ops — seed regen, backfill, reload, verify, clean up

Live, long-running, and stateful — run in this order; the warehouse must never go dark.

**Files:**
- Modify: `dbt/seeds/jma_stations.csv` (regenerated, committed)
- No other repo changes; warehouse + `data/` operations.

**Interfaces:**
- Consumes: everything above, merged to the branch.
- Produces: fully backfilled `pma_raw.jma_hourly_staffed` (26 columns, ~159 stations × 2016+), green `dbt build`, deleted AMeDAS artifacts, dropped orphan relations.

- [ ] **Step 1: Regenerate the seed** — `just python scripts/update_jma_stations_seed.py` (~5 min live). Verify: `awk -F, 'NR>1 && $1 !~ /^s/' dbt/seeds/jma_stations.csv | wc -l` → 0, and `wc -l` → 160 (159 stations + header). Commit:

```bash
git add dbt/seeds/jma_stations.csv
git commit -m "Regenerate the jma_stations seed as staffed-only"
```

- [ ] **Step 2: Smoke the live pipeline** — `just python scripts/download_jma_hourly_all.py --prefecture 83 --limit 1 --start-year 2024 --end-year 2024` (大分県; first station after the filter is s47814 日田) (one station-year, 2 requests). Inspect the produced `data/jma/hourly/s*_101-201-301-401-501-605-610_2024.csv`: one header block, ~8,784 data rows, 26 columns.
- [ ] **Step 3: Launch the backfill detached** (~3,450 requests ≈ 14 h, resumable — rerun the same command to resume after any interruption):

```bash
nohup just python scripts/download_jma_hourly_all.py > jma_scrape.log 2>&1 &
```

  Monitor via `tail -f jma_scrape.log` (progress every 100 station-years; 10 consecutive failures aborts).
- [ ] **Step 4: After completion** — `just python scripts/load_jma_hourly.py` (full reload overwrites the old 17-column table), then `just dbt build`. Expected: contracts + all tests green, including the new accepted_range tests. If a range test fails on real data (e.g. a snow depth over 800 cm), widen the bound to the observed maximum + headroom and commit that as a data-driven fix.
- [ ] **Step 5: Verification queries** (host-side dbt):

```bash
cd dbt && DBT_THRIFT_HOST=localhost uv run dbt show --inline "select count(distinct station_id) stations, count(*) rows, min(observed_at) from pma_curated.fct_jma_weather_hourly" --limit 5
cd dbt && DBT_THRIFT_HOST=localhost uv run dbt show --inline "select round(avg(case when humidity_pct is not null then 1.0 else 0 end),3) humidity_fill, round(avg(case when solar_radiation_mjm2 is not null then 1.0 else 0 end),3) solar_fill from pma_curated.fct_jma_weather_hourly where station_id = 's47662'" --limit 5
```

  Expected: ~157 stations (156 active + 阿蘇山), history from 2016-01-01 01:00; near-1.0 humidity fill at 東京; solar fill high (0 at night is a value, not a null). Record actuals in the task report.
- [ ] **Step 6: Delete the obsolete files** (only after Step 4 is green):

```bash
find data/jma/hourly -name 'a*_101-201-301-401_*.csv' -delete
find data/jma/hourly -name 's*_101-201-301-401_*.csv' -delete
```

- [ ] **Step 7: Drop the orphaned relations** (`just sql`, then):

```sql
DROP TABLE IF EXISTS pma_raw.jma_hourly_amedas;
DROP TABLE IF EXISTS pma_staging.stg_jma__hourly_amedas;
DROP VIEW IF EXISTS pma_staging.stg_jma__hourly_amedas;
```

  (one of the last two errors harmlessly depending on the staging materialization — run both).
- [ ] **Step 8: Follow-ups.** Update the CLAUDE.md demand-task parenthetical (s47772 now loaded; s47662 current) left pending in Task 7; update the memory file `jma-weather-scrape.md` status (re-scope implemented + backfilled, date, request counts); then hand back to the user for PR creation (`/commit-push-pr`) — do not push without their go-ahead.

---

## Self-review notes

- Spec coverage: decisions 1–7 map to Tasks 2 (seed filter), 3 (windowing/write-once/time-ladder constant), 4 (element set), 5 (single contract, AMeDAS deletion), 6 (models + 均質番号 docs), 7 (docs), 8 (cleanup order, verification, side effects). The spike is Task 1 with the quarter-window fallback in its decision gate.
- The 均質番号 window caveat is asserted in code (`_stitch` docstring), contract yaml, raw/std/fct ymls, and the retrieval doc.
- Type consistency: `SCRAPE_ELEMENTS`, `window_count`, `_windows`, `_stitch`, `staffed_only`, and the 9 column names are used identically across tasks; file code string `101-201-301-401-501-605-610` everywhere.
- Fixture caveat: Task 5's fixture rows are format-faithful constructions — the Task 1 spike output is the authority; adjust the fixture (and, if needed, flag nullability) to match reality, never the reverse.
