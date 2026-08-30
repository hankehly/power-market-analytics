# CsvLoader Single-Scan Reads — Stage 4 (header-based default: JEPX, OCCTO, MSM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `CsvLoader._read_all`'s per-file union with a header-grouped single scan so the loaders that still use the default — JEPX (11 files), OCCTO (1 file each) and MSM (1,606 `csv.gz` files) — read each header layout once; delete the now-unused `_read_file` from the base class and the でんき予報 shim.

**Architecture:** `_read_all` sniffs every file's header row in Python (contract encoding via a Java-charset → Python-codec map, contract `sep`, gzip-aware, `errors="replace"` so a wrongly decoded header still surfaces as "missing required columns"), checks the required columns per file (same message as today), groups files by their exact header tuple, reads each group once with `spark.read.options(header="true", …).csv(group)` + `SOURCE_FILE_COL`, projects each with `_project`, and `unionByName`s the handful of groups. JEPX and MSM measured as one layout each, OCCTO is single-file, so every default-path loader becomes one scan.

**Tech Stack:** Python 3.13 / PySpark 4.1.1, pytest, ruff, mypy, `just`, `docker compose` (eccodes 2.48.0 is in the devcontainer, so the MSM load runs there), beeline.

**Spec:** `docs/superpowers/specs/2026-08-30-csv-loader-single-scan-design.md` (stage 4 row)

## Global Constraints

- Same as stages 1–3 (docstrings, 100 % coverage gate, lint, mypy, Bash edits followed by `ruff format` + `ruff check --fix`).
- Branch `fix/csv-loader-header-grouped-scan` off `fix/estat-loader-single-scan` (the top of the stacked chain #19 → #21 → #23, so the CLAUDE.md gotcha edits never conflict); PR base = that branch. Never rebase/force-push.
- Conventional Commits with scope `loader` (base class) / `tepco` (shim removal); `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; `git add` only the named files.
- Real-warehouse runs are main-session background tasks. Do not touch `data/`, contracts, dbt models, `justfile`.
- Existing messages stay: `"{file} is missing required columns: [...]"`.

---

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/csv_loader.py` | `_read_all` = header-grouped scans; `_read_header(file)`, `_read_group(files)`, `python_codec(java_charset)`; `_read_file` deleted; docstrings |
| `power_market_analytics/tepco/power_usage.py` | `_read_file` shim deleted |
| `tests/test_csv_loader.py` | + grouping / gzip / delimiter / codec tests, no-`_read_file` test; `PositionalLoader` stays |
| `tests/test_tepco_power_usage.py` | `test_read_file_reads_a_single_file` → replaced by a no-`_read_file` assertion |
| `CLAUDE.md` | gotcha rewritten (all loaders single-scan; MSM measured; 20g no longer required by any loader) |
| `docs/JMA-MSM-GPV-Retrieval.md` | §8: load time of the 1,606 gz files |

---

### Task 0: Branch

- [ ] `git checkout -b fix/csv-loader-header-grouped-scan fix/estat-loader-single-scan`

---

### Task 1: Header-grouped default read

**Files:** `power_market_analytics/csv_loader.py`, `tests/test_csv_loader.py`

**Interfaces:**
- Produces: `python_codec(java_charset: str) -> str` (module function: `windows-31j`/`ms932` → `cp932`, `utf-8`/`utf8` → `utf-8-sig`, else the lower-cased name); `CsvLoader._read_header(self, file: str) -> list[str]`; `CsvLoader._read_group(self, files: list[str]) -> DataFrame`; `CsvLoader._read_all` as described. `_read_file` removed from the base class.

- [ ] **Step 1: Failing tests** — append to `tests/test_csv_loader.py` (import `python_codec` from `power_market_analytics.csv_loader`; `import gzip`):

```python
class TestHeaderGroupedRead:
    def test_files_with_one_header_share_a_scan_and_layouts_are_unioned(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "a2.csv", FILE_A)
        write_utf8(tmp_path / "b.csv", FILE_B)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.groups", spark=spark)

        df = loader._read_all(loader._resolve_files())

        plan = df._jdf.queryExecution().analyzed().toString()
        # Two layouts → exactly one Union of two scans, not a union per file.
        assert plan.count("Union") == 1
        assert sorted((r.id, r[SOURCE_FILE_COL]) for r in df.collect()) == [
            (1, "a.csv"), (1, "a2.csv"), (2, "a.csv"), (2, "a2.csv"), (3, "b.csv")
        ]

    def test_single_layout_is_one_scan_without_a_union(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        write_utf8(tmp_path / "a2.csv", FILE_A)
        loader = CsvLoader(SCHEMA, tmp_path, "t", spark=spark)
        plan = loader._read_all(loader._resolve_files())._jdf.queryExecution().analyzed().toString()
        assert "Union" not in plan

    def test_missing_required_column_is_reported_for_its_own_file(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", FILE_A)
        lines = [",".join(v for i, v in enumerate(ln.split(",")) if i != 8) for ln in FILE_A]
        write_utf8(tmp_path / "bad.csv", lines)
        loader = CsvLoader(SCHEMA, tmp_path, "test_csv_loader.badfile", spark=spark)
        with pytest.raises(ValueError, match=re.escape("bad.csv is missing required columns: ['value(kWh)']")):
            loader.load()

    def test_gzip_files_are_sniffed_and_read(self, spark, tmp_path):
        with gzip.open(tmp_path / "a.csv.gz", "wt", encoding="utf-8", newline="") as f:
            f.write("\n".join(FILE_A) + "\n")
        loader = CsvLoader(SCHEMA, tmp_path / "*.csv.gz", "test_csv_loader.gz", spark=spark)
        assert loader.load() == 2

    def test_header_sniff_honours_encoding_and_separator(self, spark, tmp_path):
        schema = CsvTableSchema.model_validate(
            {"read_options": {"encoding": "windows-31j", "sep": ";"},
             "columns": [{"name": "v", "source": "値", "type": "int"}, {"name": "k", "source": "キー", "type": "string"}]}
        )
        write_cp932(tmp_path / "j.csv", ["キー;値", "a;1"])
        loader = CsvLoader(schema, tmp_path, "test_csv_loader.sep", spark=spark)
        assert loader._read_header(str(tmp_path / "j.csv")) == ["キー", "値"]
        assert loader.load() == 1

    def test_loader_has_no_per_file_read(self):
        assert "_read_file" not in CsvLoader.__dict__


class TestPythonCodec:
    @pytest.mark.parametrize(
        "java, python",
        [("windows-31j", "cp932"), ("MS932", "cp932"), ("UTF-8", "utf-8-sig"), ("utf8", "utf-8-sig"), ("Shift_JIS", "shift_jis"), ("EUC-JP", "euc-jp")],
    )
    def test_maps_java_charset_names(self, java, python):
        assert python_codec(java) == python
```

- [ ] **Step 2: Run** `uv run pytest tests/test_csv_loader.py -q -p no:cacheprovider -k "HeaderGrouped or PythonCodec"` → fails (`ImportError: python_codec`).

- [ ] **Step 3: Implement** in `csv_loader.py` (`import csv`, `import gzip` added):

```python
#: Java charset names (as Spark's CSV reader takes them) that Python spells differently.
_JAVA_TO_PYTHON_CODEC = {"windows-31j": "cp932", "ms932": "cp932", "utf-8": "utf-8-sig", "utf8": "utf-8-sig"}


def python_codec(java_charset: str) -> str:
    """Python codec for a Java charset name from a contract's ``read_options``.

    Parameters
    ----------
    java_charset : str
        E.g. ``windows-31j``, ``UTF-8``, ``Shift_JIS``.

    Returns
    -------
    str
        ``cp932`` for the Windows Shift_JIS names, ``utf-8-sig`` for UTF-8 (a
        BOM, if any, is dropped from the first header), otherwise the
        lower-cased name, which Python accepts for the remaining charsets.
    """
    name = java_charset.lower()
    return _JAVA_TO_PYTHON_CODEC.get(name, name)
```

and, replacing `_read_all` + `_read_file`:

```python
    def _read_all(self, files: list[str]) -> DataFrame:
        """Read every file into one DataFrame in the contract's column order.

        Files are grouped by their exact header row and every group is read
        in one scan: Spark applies the first file's header to all files of a
        multi-path read, so differently laid-out files must not share one,
        while one frame per file re-analyses the whole plan on every
        ``unionByName`` and ships a task binary that grows with the file
        count (1,600 files ≈ 8 min of planning and a 45 MiB binary). Loaders
        whose files carry no usable header override this —
        :meth:`_scan_positional` + :meth:`_project` for positional layouts,
        one ``createDataFrame`` over Python-parsed rows.

        Parameters
        ----------
        files : list of str
            Resolved file paths, as returned by :meth:`_resolve_files`.

        Returns
        -------
        pyspark.sql.DataFrame
            Contract columns plus ``SOURCE_FILE_COL``.

        Raises
        ------
        ValueError
            If a file's header lacks a required column.
        """
        groups: dict[tuple[str, ...], list[str]] = {}
        for file in files:
            header = self._read_header(file)
            missing = [
                c.source_name for c in self.schema.columns if c.required and c.source_name not in header
            ]
            if missing:
                raise ValueError(f"{file} is missing required columns: {missing}")
            groups.setdefault(tuple(header), []).append(file)
        return reduce(DataFrame.unionByName, (self._read_group(g) for g in groups.values()))

    def _read_header(self, file: str) -> list[str]:
        """The header row of ``file``, decoded like Spark will decode it.

        Parameters
        ----------
        file : str
            CSV path; ``.gz`` files are opened through gzip.

        Returns
        -------
        list of str
            Header cells split on the contract's ``sep`` (default ``,``),
            quotes removed; ``[]`` for an empty file. Undecodable bytes are
            replaced rather than raised, so a header in the wrong encoding
            fails the required-column check instead of the read.
        """
        encoding = python_codec(self.schema.read_options.get("encoding", "UTF-8"))
        sep = self.schema.read_options.get("sep", ",")
        opener = gzip.open if file.endswith(".gz") else open
        with opener(file, "rt", encoding=encoding, errors="replace", newline="") as f:
            return next(csv.reader(f, delimiter=sep), [])

    def _read_group(self, files: list[str]) -> DataFrame:
        """One header-based scan of files that share a header row."""
        raw = (
            self.spark.read.options(header="true", **self.schema.read_options)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, F.col("_metadata.file_name"))
        )
        return self._project(raw)
```

- [ ] **Step 4: Run** the module, then `just test` / `just lint` / `just mypy` (the pre-existing `test_read_options_encoding_decodes_cp932_headers_and_values` and `test_missing_encoding_option_makes_cp932_headers_unresolvable` must still pass).

- [ ] **Step 5: Commit** `fix(loader): read header-based files one scan per header layout`.

---

### Task 2: Drop the でんき予報 `_read_file` shim

- [ ] Replace `tests/test_tepco_power_usage.py::test_read_file_reads_a_single_file` with `def test_loader_has_no_per_file_read(self): assert "_read_file" not in TepcoPowerUsageCsvLoader.__dict__`; delete `TepcoPowerUsageCsvLoader._read_file` (lines 434-435). Run `uv run pytest tests/test_tepco_power_usage.py -q`; `just test`. Commit `refactor(tepco): drop the unused per-file read of the でんき予報 loader`.

---

### Task 3: Real-data verification (JEPX, OCCTO ×2, MSM)

- [ ] Scratch loads (one background script): `CsvLoader` with `conf/schemas/jepx_spot.yaml` on `data/jepx/spot` → `pma_scratch.jepx_spot`; `occto_demand_forecast_dad.yaml` / `occto_area_reserve_rate_dad.yaml` on their dirs; `MsmForecastCsvLoader` with `jma_msm_surface_forecast.yaml` on `data/jma/msm_surface_forecast/csv` → `pma_scratch.jma_msm_surface_forecast` (record each load time). `EXCEPT` both ways vs the `pma_raw` tables (MSM: also `count(distinct source_file_name)` = 1,606) → all 0. Drop `pma_scratch`. Real scripts: `load_jepx_spot.py`, `load_occto_demand_forecast.py`, `load_occto_area_reserve_rate.py`, `load_jma_msm_surface_forecast.py` (record wall). `just dbt build --select <staging models>` (names from `ls dbt/models/staging | grep -E 'jepx|occto|msm'`). Note the MSM output file count (gz → one partition per file); record it for the coalesce decision in the PR body rather than changing the write.

---

### Task 4: Docs

- [ ] CLAUDE.md gotcha → "Many-file raw reloads: every `CsvLoader` reads its files in a handful of scans since 2026-08-30 (positional: `_scan_positional`; header-based: one scan per header layout; Python-parsed: one `createDataFrame`) — JMA 1,608 files ~50 s, area actuals ~15 s / ~8 s, e-Stat ~19 s, MSM 1,606 `csv.gz` ~<MEASURED> s, JEPX/OCCTO seconds. Before that the per-file union cost ~8 min of planning and a 45 MiB task binary per 1,600 files, which is why the compose default is `SPARK_DRIVER_MEMORY=20g`; no loader needs it any more (JMA verified at 4g) — the default is kept as headroom. An aborted overwrite leaves the old table intact." MSM doc §8: one sentence with the measured load time and file count. Commit `docs(loader): all loaders single-scan; retire the 20g requirement`.

---

### Task 5: PR and reviews

- [ ] Push; `gh pr create --base fix/estat-loader-single-scan --title "fix(loader): read header-based files one scan per header layout"` (Why / What / Proof; no Codex mention in the body); assign `hankehly`, labels `bug` + `documentation`; CLAUDE.md "Code review" loop.
