# CsvLoader Single-Scan Reads — Stage 1 (base protocol + JMA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/load_jma_hourly.py` read its ~1,600 station-year files in one Spark scan (≈1 min) instead of a 1,600-branch union (≈1 h 45 min, 20 GB driver), by adding the single-scan protocol to `CsvLoader` and switching `JmaHourlyCsvLoader` to it.

**Architecture:** `CsvLoader` gains a hidden per-row `SOURCE_FILE_COL` (`_source_file`, from Spark's `_metadata.file_name`), `_scan_positional(files, column_count)` (one headerless multi-path scan), `_project(raw)` (the contract cast keeping the file column), per-file suffixes on the two validation errors, and `load()` drops the hidden column before writing. `JmaHourlyCsvLoader` overrides `_read_all`: check every file in Python first (`_check_file`), then one scan → data-row filter → station id from the file name → `_project`. Its `_read_file` is deleted. The base `_read_all` default is untouched in this stage (docstring corrected).

**Tech Stack:** Python 3.13 / PySpark 4.1.1 (local session in tests; Hive-enabled session in the devcontainer), pytest with the repo's `spark` fixture (`TZ=Asia/Tokyo`), ruff, mypy, `just`, `docker compose`, beeline on the thriftserver.

**Spec:** `docs/superpowers/specs/2026-08-30-csv-loader-single-scan-design.md`

## Global Constraints

- NumPy-style docstrings (`Parameters` / `Returns` / `Raises`, underlined headers) on every new public or protected method.
- `just test` has a **100 % coverage gate** — every new line must be exercised; `just lint` and `just mypy` must pass. A PostToolUse hook runs `ruff format` + `ruff check --fix` on every `.py` you edit and **strips unused imports** — re-read a file before editing it again.
- Existing error messages stay byte-identical up to the new suffix: `"Non-nullable columns contain nulls after casting (null count per column): {…}"` and `"Grain [...] is not unique: N rows but M distinct keys"`; the suffixes are `"; by file (first 10): {…}"` and `"; first 10 duplicated keys (key, rows, files): […]"`.
- Names fixed by the spec: `SOURCE_FILE_COL = "_source_file"`, `REPORT_LIMIT = 10`, methods `_scan_positional`, `_project`, `_nulls_by_file`, `_duplicates_by_file`, `JmaHourlyCsvLoader._check_file`.
- Anything that opens a SparkSession against the real warehouse runs in the devcontainer (`just python …`, `docker compose exec -T … python - < snippet`); such runs and `dbt build` are **main-session background Bash tasks** — never backgrounded by a subagent. Stopping a `just python` client does not stop the in-container process (`just exec pkill -f <script>`).
- Git: branch `fix/jma-loader-single-scan` off `main`; commit after every task with Conventional Commits (`fix(loader)`, `fix(jma)`, `test(...)`, `docs(...)`); every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. `git add` only the files named in the task — the working tree carries unrelated uncommitted research-doc edits (`docs/research/…`, `.codex/`) that must stay out.
- Do not touch `data/`, `.env`, `docker-compose.yaml`, contracts under `conf/schemas/`, dbt models or `justfile`.

---

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/csv_loader.py` | + `SOURCE_FILE_COL`, `REPORT_LIMIT`, `_scan_positional`, `_project`, `_nulls_by_file`, `_duplicates_by_file`; `_validate` suffixes; `load()` drops the hidden column; `_read_all` docstring corrected |
| `power_market_analytics/jma.py` | `JmaHourlyCsvLoader._read_all` (single scan) + `_check_file`; `_read_file` deleted; module/class docstrings updated |
| `tests/test_csv_loader.py` | + positional test loader, `_scan_positional` / `_project` / per-file report / hidden-column-drop tests; two existing messages anchored with `$` |
| `tests/test_jma_loader.py` | + every-file-checked test, per-file null report test; duplicate report assertion extended |
| `CLAUDE.md` | "Large raw reloads" gotcha rewritten |
| `docs/JMA-Weather-Data-Retrieval.md` | "Loading into the warehouse" paragraph: single scan + measured timing, 20g paragraph replaced |

---

### Task 0: Branch

**Files:** none

- [ ] **Step 1: Create the branch off `main`**

```bash
git checkout -b fix/jma-loader-single-scan main
git status --short   # expect only the pre-existing docs/research + .codex entries
```

---

### Task 1: `_scan_positional` + `SOURCE_FILE_COL` on `CsvLoader`

**Files:**
- Modify: `power_market_analytics/csv_loader.py` (imports at lines 9-21; new constants after the imports; new method after `_read_all`, i.e. after line 200)
- Test: `tests/test_csv_loader.py`

**Interfaces:**
- Produces: `SOURCE_FILE_COL: str = "_source_file"`; `CsvLoader._scan_positional(self, files: list[str], column_count: int) -> DataFrame` returning columns `_c0 … _c<column_count-1>` (all `string`) plus `_source_file` (the row's file base name).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_csv_loader.py` — import line becomes
`from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvColumn, CsvLoader, CsvTableSchema`
and, after the `table_rows` helper (line ~132), append:

```python
POSITIONAL_SCHEMA = CsvTableSchema.model_validate(
    {
        "grain": ["id"],
        "columns": [
            {"name": "id", "source": "_c0", "type": "int", "nullable": False},
            {"name": "label", "source": "_c1", "type": "string", "nullable": False},
        ],
    }
)


class TestScanPositional:
    def test_one_scan_reads_every_file_headerless_with_its_file_name(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha", "2,beta"])
        write_utf8(tmp_path / "b.csv", ["id,label", "3,gamma"])
        loader = CsvLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.scan", spark=spark)

        df = loader._scan_positional([str(tmp_path / "a.csv"), str(tmp_path / "b.csv")], 2)

        assert df.columns == ["_c0", "_c1", SOURCE_FILE_COL]
        assert {f.dataType.simpleString() for f in df.schema} == {"string"}
        # Header lines are ordinary rows; every row names the file it came from.
        assert sorted((r._c0, r._c1, r[SOURCE_FILE_COL]) for r in df.collect()) == [
            ("1", "alpha", "a.csv"),
            ("2", "beta", "a.csv"),
            ("3", "gamma", "b.csv"),
            ("id", "label", "a.csv"),
            ("id", "label", "b.csv"),
        ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_csv_loader.py::TestScanPositional -v`
Expected: FAIL — `ImportError: cannot import name 'SOURCE_FILE_COL'`.

- [ ] **Step 3: Implement**

In `power_market_analytics/csv_loader.py`, extend the imports:

```python
from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType
```

After the imports (before `class CsvColumn`), add:

```python
#: Hidden per-row column naming the file a row came from (base name only).
#: :meth:`CsvLoader._scan_positional` attaches it, :meth:`CsvLoader._validate`
#: reports problems per file with it and :meth:`CsvLoader.load` drops it
#: before the write. Never a contract column — but a contract may *source*
#: a column from it (``source: _source_file``) to expose the file name.
SOURCE_FILE_COL = "_source_file"
```

After `_read_all` (after line 200), add:

```python
    def _scan_positional(self, files: list[str], column_count: int) -> DataFrame:
        """Read ``files`` headerless, in one scan, as ``_c0`` .. ``_c<n-1>`` strings.

        One ``FileScan`` over every path (Spark packs the files into
        partitions by size) rather than one frame per file: a union of
        per-file frames re-analyses the whole plan on every ``unionByName``
        and ships a task binary proportional to the file count (~8 min of
        planning and a 45 MiB binary for the 1,608 JMA files). Header and
        metadata lines come back as ordinary rows — filter them out with a
        pattern on ``_c0``. The hidden ``SOURCE_FILE_COL`` holds each row's
        file name so a subclass can derive per-file values from it and
        :meth:`_validate` can report per file.

        Parameters
        ----------
        files : list of str
            Paths to read, all in the contract's ``read_options`` encoding.
        column_count : int
            Number of physical columns to expose; extra columns are ignored,
            missing ones read as null.

        Returns
        -------
        pyspark.sql.DataFrame
            ``_c0`` .. ``_c<column_count-1>`` (string) plus ``SOURCE_FILE_COL``.
        """
        spark_schema = StructType(
            [StructField(f"_c{i}", StringType()) for i in range(column_count)]
        )
        return (
            self.spark.read.options(**self.schema.read_options)
            .schema(spark_schema)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, F.col("_metadata.file_name"))
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_csv_loader.py::TestScanPositional -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/csv_loader.py tests/test_csv_loader.py
git commit -m "$(cat <<'EOF'
fix(loader): scan positional files in one FileScan with a per-row source file

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `_project` and `load()` dropping the hidden column

**Files:**
- Modify: `power_market_analytics/csv_loader.py` (`load()` lines 149-171; new `_project` after `_scan_positional`)
- Test: `tests/test_csv_loader.py`

**Interfaces:**
- Consumes: `_scan_positional`, `SOURCE_FILE_COL` (Task 1).
- Produces: `CsvLoader._project(self, raw: DataFrame) -> DataFrame` = contract columns in order + `_source_file`; `load()` writes the table **without** `_source_file`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_csv_loader.py` add `from pyspark.sql import functions as F` to the imports, and after `TestScanPositional` append:

```python
class PositionalLoader(CsvLoader):
    """Test double: ``id,label`` files with a one-line header, read positionally."""

    def _read_all(self, files: list[str]):
        raw = self._scan_positional(files, 2).filter(F.col("_c0") != "id")
        return self._project(raw)


class TestProject:
    def test_casts_the_contract_in_order_and_keeps_the_file_name(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha"])
        loader = CsvLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.project", spark=spark)
        raw = loader._scan_positional([str(tmp_path / "a.csv")], 2).filter(F.col("_c0") != "id")

        df = loader._project(raw)

        assert [(f.name, f.dataType.simpleString()) for f in df.schema] == [
            ("id", "int"),
            ("label", "string"),
            (SOURCE_FILE_COL, "string"),
        ]
        assert [tuple(r) for r in df.collect()] == [(1, "alpha", "a.csv")]


class TestPositionalLoad:
    def test_loads_through_the_contract_without_the_file_column(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha", "2,beta"])
        write_utf8(tmp_path / "b.csv", ["id,label", "3,gamma"])
        loader = PositionalLoader(
            POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.positional", spark=spark
        )

        assert loader.load() == 3

        table = spark.table("test_csv_loader.positional")
        assert table.columns == ["id", "label"]
        assert sorted(tuple(r) for r in table.collect()) == [
            (1, "alpha"),
            (2, "beta"),
            (3, "gamma"),
        ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_csv_loader.py::TestProject tests/test_csv_loader.py::TestPositionalLoad -v`
Expected: FAIL — `AttributeError: 'CsvLoader' object has no attribute '_project'`.

- [ ] **Step 3: Implement**

In `csv_loader.py`, after `_scan_positional` add:

```python
    def _project(self, raw: DataFrame) -> DataFrame:
        """Cast ``raw`` to the contract's columns, keeping ``SOURCE_FILE_COL``.

        Parameters
        ----------
        raw : pyspark.sql.DataFrame
            Source-named columns (``_c<n>`` positions or header names, plus
            any injected ``__``-prefixed sources) and ``SOURCE_FILE_COL``.

        Returns
        -------
        pyspark.sql.DataFrame
            The contract columns in contract order, then ``SOURCE_FILE_COL``.
        """
        return raw.select(
            [self._cast(raw, c) for c in self.schema.columns] + [F.col(SOURCE_FILE_COL)]
        )
```

Rewrite the body of `load()` (keep the docstring) as:

```python
        files = self._resolve_files()
        logger.info("Loading {} file(s) into {}: {}", len(files), self.table, files)
        df = self._read_all(files)
        df.cache()
        try:
            # The hidden source-file column serves validation only.
            out = df.drop(SOURCE_FILE_COL)
            n_rows = df.count()
            logger.info(
                "Read shape=({}, {}); schema: {}",
                n_rows,
                len(out.columns),
                ", ".join(f"{f.name}:{f.dataType.simpleString()}" for f in out.schema),
            )
            self._validate(df)
            self._write(out)
        finally:
            df.unpersist()
        logger.info("Loaded {} rows into {}", n_rows, self.table)
        return n_rows
```

(`DataFrame.drop` of an absent column is a no-op, so the header-based path is unchanged.)

- [ ] **Step 4: Run the whole loader test module**

Run: `uv run pytest tests/test_csv_loader.py -v`
Expected: all PASS (the pre-existing header-based tests still pass).

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/csv_loader.py tests/test_csv_loader.py
git commit -m "$(cat <<'EOF'
fix(loader): project the contract over a scan and drop the source-file column at write

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Per-file validation reports

**Files:**
- Modify: `power_market_analytics/csv_loader.py` (`_validate` lines 227-248; two new methods after it; `import operator` at the top; `REPORT_LIMIT` next to `SOURCE_FILE_COL`)
- Test: `tests/test_csv_loader.py`

**Interfaces:**
- Consumes: `SOURCE_FILE_COL`, `PositionalLoader` (tests).
- Produces: `REPORT_LIMIT = 10`; `CsvLoader._nulls_by_file(self, df, columns: list[str]) -> str` and `CsvLoader._duplicates_by_file(self, df) -> str`, each `""` when `df` has no `SOURCE_FILE_COL`, otherwise the suffixes quoted in *Global Constraints*.

- [ ] **Step 1: Write the failing tests**

Import line: `from power_market_analytics.csv_loader import REPORT_LIMIT, SOURCE_FILE_COL, CsvColumn, CsvLoader, CsvTableSchema`.

Anchor the two existing messages (header-based path, no file column → no suffix). In `test_non_nullable_column_with_nulls_after_cast_raises` (line ~272) change the `match=` to

```python
            match=re.escape("nulls after casting (null count per column): {'id': 1, 'd': 2}") + "$",
```

and in `test_duplicate_grain_raises` (line ~293) to

```python
            match=re.escape("Grain ['id'] is not unique: 3 rows but 2 distinct keys") + "$",
```

Then add to `TestPositionalLoad`:

```python
    def test_null_report_names_the_offending_files_only(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha"])
        write_utf8(tmp_path / "b.csv", ["id,label", "2,", "3,"])
        loader = PositionalLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.nulls", spark=spark)

        with pytest.raises(ValueError) as exc:
            loader.load()

        assert str(exc.value) == (
            "Non-nullable columns contain nulls after casting (null count per column): "
            f"{{'label': 2}}; by file (first {REPORT_LIMIT}): {{'b.csv': {{'label': 2}}}}"
        )
        assert not spark.catalog.tableExists("test_csv_loader.nulls")

    def test_duplicate_report_lists_keys_with_their_files(self, spark, tmp_path):
        write_utf8(tmp_path / "a.csv", ["id,label", "1,alpha", "2,beta"])
        write_utf8(tmp_path / "b.csv", ["id,label", "2,beta-again", "3,gamma"])
        loader = PositionalLoader(POSITIONAL_SCHEMA, tmp_path, "test_csv_loader.dups", spark=spark)

        with pytest.raises(ValueError) as exc:
            loader.load()

        assert str(exc.value) == (
            "Grain ['id'] is not unique: 4 rows but 3 distinct keys; "
            f"first {REPORT_LIMIT} duplicated keys (key, rows, files): [((2,), 2, ['a.csv', 'b.csv'])]"
        )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_csv_loader.py -v -k "report or nulls_after_cast or duplicate_grain"`
Expected: the two new tests FAIL (`ImportError` for `REPORT_LIMIT`, then message mismatch); the two anchored tests PASS.

- [ ] **Step 3: Implement**

Top of `csv_loader.py`: add `import operator` (keep imports alphabetical: `glob`, `operator`, then `from functools import reduce`). Next to `SOURCE_FILE_COL`:

```python
#: Files / duplicated keys named in a validation error message.
REPORT_LIMIT = 10
```

Replace `_validate` and add the two helpers:

```python
    def _validate(self, df: DataFrame) -> None:
        non_nullable = [c.name for c in self.schema.columns if not c.nullable]
        if non_nullable:
            # collect()[0] over first(): an aggregation always yields one row,
            # and unlike first() the element is not Optional.
            null_counts = df.select(
                [F.count(F.when(F.col(name).isNull(), True)).alias(name) for name in non_nullable]
            ).collect()[0]
            bad = {name: null_counts[name] for name in non_nullable if null_counts[name]}
            if bad:
                raise ValueError(
                    "Non-nullable columns contain nulls after casting "
                    f"(null count per column): {bad}{self._nulls_by_file(df, list(bad))}"
                )
        if self.schema.grain:
            total = df.count()
            distinct = df.select(self.schema.grain).distinct().count()
            if distinct != total:
                raise ValueError(
                    f"Grain {self.schema.grain} is not unique: "
                    f"{total} rows but {distinct} distinct keys{self._duplicates_by_file(df)}"
                )

    def _nulls_by_file(self, df: DataFrame, columns: list[str]) -> str:
        """Null counts of ``columns`` per source file, as an error-message suffix.

        Parameters
        ----------
        df : pyspark.sql.DataFrame
            The loaded frame; reported per file only if it carries
            ``SOURCE_FILE_COL``.
        columns : list of str
            Non-nullable contract columns that contain nulls.

        Returns
        -------
        str
            ``"; by file (first 10): {file: {column: nulls}}"`` for the first
            ``REPORT_LIMIT`` offending files (by name), or ``""``.
        """
        if SOURCE_FILE_COL not in df.columns:
            return ""
        rows = (
            df.groupBy(SOURCE_FILE_COL)
            .agg(*[F.count(F.when(F.col(c).isNull(), True)).alias(c) for c in columns])
            .filter(reduce(operator.or_, (F.col(c) > 0 for c in columns)))
            .orderBy(SOURCE_FILE_COL)
            .limit(REPORT_LIMIT)
            .collect()
        )
        by_file = {r[SOURCE_FILE_COL]: {c: r[c] for c in columns if r[c]} for r in rows}
        return f"; by file (first {REPORT_LIMIT}): {by_file}"

    def _duplicates_by_file(self, df: DataFrame) -> str:
        """The first duplicated grain keys and their files, as an error-message suffix.

        Parameters
        ----------
        df : pyspark.sql.DataFrame
            The loaded frame; reported only if it carries ``SOURCE_FILE_COL``.

        Returns
        -------
        str
            ``"; first 10 duplicated keys (key, rows, files): [...]"`` — one
            ``(key tuple, row count, sorted file names)`` per duplicated key,
            the first ``REPORT_LIMIT`` in key order — or ``""``.
        """
        if SOURCE_FILE_COL not in df.columns:
            return ""
        rows = (
            df.groupBy(self.schema.grain)
            .agg(
                F.count(F.lit(1)).alias("_rows"),
                F.sort_array(F.collect_set(SOURCE_FILE_COL)).alias("_files"),
            )
            .filter(F.col("_rows") > 1)
            .orderBy(self.schema.grain)
            .limit(REPORT_LIMIT)
            .collect()
        )
        duplicates = [
            (tuple(r[k] for k in self.schema.grain), r["_rows"], r["_files"]) for r in rows
        ]
        return f"; first {REPORT_LIMIT} duplicated keys (key, rows, files): {duplicates}"
```

- [ ] **Step 4: Run the module**

Run: `uv run pytest tests/test_csv_loader.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/csv_loader.py tests/test_csv_loader.py
git commit -m "$(cat <<'EOF'
fix(loader): name the offending files in null and duplicate-grain errors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `JmaHourlyCsvLoader` reads everything in one scan

**Files:**
- Modify: `power_market_analytics/jma.py` (import line 56; class `JmaHourlyCsvLoader` lines 866-902: docstring, `_read_file` → `_read_all` + `_check_file`; module docstring lines 23-37)
- Test: `tests/test_jma_loader.py`

**Interfaces:**
- Consumes: `SOURCE_FILE_COL`, `_scan_positional`, `_project` (Tasks 1-2).
- Produces: `JmaHourlyCsvLoader._read_all(self, files: list[str]) -> DataFrame`; `JmaHourlyCsvLoader._check_file(self, file: str, expected: int) -> None` (raises `ValueError` with the two existing messages). `_read_file` no longer exists.

- [ ] **Step 1: Write the failing tests**

In `tests/test_jma_loader.py`, add `from power_market_analytics.csv_loader import REPORT_LIMIT, CsvTableSchema` (replacing the existing `CsvTableSchema` import) and add to `TestJmaHourlyCsvLoaderLoad`:

```python
    def test_every_file_is_checked_before_any_is_read(self, spark, tmp_path):
        # The bad file sorts *after* the good one ("-" < "_"), so a guard that
        # only inspected the first file would let it through.
        staffed_file(tmp_path, "s47662_101-201-301-401-501-605-610_2024.csv")
        write_cp932(tmp_path / "s47662_101-201-301-401_2016.csv", OLD_CORE_HEADER + OLD_CORE_ROWS)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.mixed", spark=spark)

        with pytest.raises(ValueError, match="first data row has 17 columns, contract expects 27"):
            loader.load()
        assert not spark.catalog.tableExists("test_jma.mixed")

    def test_null_report_names_the_offending_station_file(self, spark, tmp_path):
        staffed_file(tmp_path, "s47662_101-201-301-401-501-605-610_2024.csv")
        rows = list(STAFFED_ROWS)
        rows[0] = "2024/2/5 19:00:00,3.0,0,,1,0.6,8,1,3.9,8,北北西,8,1,0,1,8,1,3,0,8,1,98,8,1,0,8,1"
        write_cp932(tmp_path / "s47772_101-201-301-401-501-605-610_2024.csv", STAFFED_HEADER + rows)
        loader = JmaHourlyCsvLoader(STAFFED_CONTRACT, tmp_path, "test_jma.nullfile", spark=spark)

        with pytest.raises(ValueError) as exc:
            loader.load()

        assert str(exc.value).endswith(
            f"; by file (first {REPORT_LIMIT}): "
            "{'s47772_101-201-301-401-501-605-610_2024.csv': {'precipitation_quality_flag': 1}}"
        )

    def test_loader_has_no_per_file_read(self):
        # The base class keeps its header-based ``_read_file`` in this stage,
        # so check the JMA class body, not attribute lookup through the MRO.
        assert "_read_file" not in JmaHourlyCsvLoader.__dict__
```

And extend `test_overlapping_year_files_of_one_station_violate_the_grain` — replace its `with pytest.raises(...)` block with:

```python
        with pytest.raises(ValueError) as exc:
            loader.load()
        message = str(exc.value)
        assert message.startswith(
            "Grain ['station_id', 'observed_at'] is not unique: 8 rows but 4 distinct keys; "
            f"first {REPORT_LIMIT} duplicated keys (key, rows, files): "
        )
        # Keys in grain order: the earliest hour first, both year files named.
        assert message.endswith(
            "[(('s47662', datetime.datetime(2017, 12, 12, 1, 0)), 2, "
            "['s47662_101-201-301-401-501-605-610_2024.csv', "
            "'s47662_101-201-301-401-501-605-610_2025.csv']), "
            "(('s47662', datetime.datetime(2024, 2, 5, 19, 0)), 2, "
            "['s47662_101-201-301-401-501-605-610_2024.csv', "
            "'s47662_101-201-301-401-501-605-610_2025.csv']), "
            "(('s47662', datetime.datetime(2024, 7, 1, 13, 0)), 2, "
            "['s47662_101-201-301-401-501-605-610_2024.csv', "
            "'s47662_101-201-301-401-501-605-610_2025.csv']), "
            "(('s47662', datetime.datetime(2024, 11, 7, 16, 0)), 2, "
            "['s47662_101-201-301-401-501-605-610_2024.csv', "
            "'s47662_101-201-301-401-501-605-610_2025.csv'])]"
        )
```

(`STAFFED_ROWS` has four hours: 2017-12-12 01:00, 2024-02-05 19:00, 2024-07-01 13:00, 2024-11-07 16:00; the test process runs in `TZ=Asia/Tokyo`, so the naive datetimes match the parsed CSV values.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_jma_loader.py -v`
Expected: `test_null_report_names_the_offending_station_file`, `test_loader_has_no_per_file_read` and the extended overlap test FAIL (no suffix yet / `_read_file` still exists); `test_every_file_is_checked_before_any_is_read` passes already (the union path also checked per file) — that is fine, it guards the new loop.

- [ ] **Step 3: Implement**

`power_market_analytics/jma.py` line 56 → `from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvLoader`. (The `StringType, StructField, StructType` import on line 54 becomes unused; the ruff hook removes it — verify it is gone.)

Replace the class (lines 866-902, i.e. the docstring and `_read_file`; keep `_expected_column_count` and `_sniff_column_count` as they are) with:

```python
class JmaHourlyCsvLoader(CsvLoader):
    """Positional full reload of JMA hourly CSVs into a warehouse table.

    Works exactly like :class:`CsvLoader` (same constructor, validation and
    write behavior) except for how the files are read: every file is checked
    in Python first (column count against the contract, station id in the
    name), then all of them are read in one positional scan
    (:meth:`CsvLoader._scan_positional`) and the station id is taken from
    each row's file name — one frame per file unioned together made Spark
    re-analyse the plan 1,600 times and ship a 45 MiB task binary. The
    contract's ``source`` fields must be ``_c<n>`` positions plus
    ``__station_id`` for the injected station id.
    """

    #: Contract ``source`` name for the station id parsed from the file name.
    STATION_ID_SOURCE = "__station_id"

    _FILENAME_RE = re.compile(r"([sa]\d+)_[\d-]+_\d{4}\.csv$")
    _DATA_ROW_PATTERN = r"^\d{4}/"

    def _read_all(self, files: list[str]) -> DataFrame:
        expected = self._expected_column_count()
        for file in files:
            self._check_file(file, expected)
        raw = (
            self._scan_positional(files, expected)
            .filter(F.col("_c0").rlike(self._DATA_ROW_PATTERN))
            .withColumn(
                self.STATION_ID_SOURCE,
                F.regexp_extract(F.col(SOURCE_FILE_COL), self._FILENAME_RE.pattern, 1),
            )
        )
        return self._project(raw)

    def _check_file(self, file: str, expected: int) -> None:
        """Fail on a file the contract cannot read, before any Spark scan.

        Parameters
        ----------
        file : str
            Path to a JMA hourly CSV file.
        expected : int
            Physical column count implied by the contract.

        Raises
        ------
        ValueError
            If the first data row's column count differs from ``expected``
            (wrong station class or JMA changed the layout) or the file name
            carries no station id.
        """
        actual = self._sniff_column_count(file)
        if actual != expected:
            raise ValueError(
                f"{file}: first data row has {actual} columns, contract "
                f"expects {expected} — file does not match this format "
                "(wrong station class or JMA changed the layout)"
            )
        if self._FILENAME_RE.search(file) is None:
            raise ValueError(f"{file}: cannot parse a station id from the file name")
```

Module docstring, lines 23-37 — replace the two paragraphs with:

```
``JmaHourlyCsvLoader`` brings the downloaded hourly CSVs into a raw
warehouse table. They cannot go through the generic header-name mapping of
:class:`~power_market_analytics.csv_loader.CsvLoader`: they open with a
download-timestamp line, a blank line and multiple header rows whose labels
repeat per element (e.g. ``気温(℃)`` three times), and the station id
appears only in the file name. The loader therefore reads all files
headerless in a single Spark scan — the load contract addresses columns
positionally via ``source: _c0``, ``_c1``, … — keeps only data rows (first
field is a timestamp), and injects a ``station_id`` column parsed from each
row's file name (contract ``source: __station_id``).

Every file's column count and name are checked in Python before the scan;
a mismatch fails the load rather than silently truncating, guarding against
JMA layout drift (or a stale pre-re-scope file) rather than a station-class
mixup.
```

- [ ] **Step 4: Run the JMA tests, then the full suite**

Run: `uv run pytest tests/test_jma_loader.py tests/test_load_scripts.py -v`
Expected: all PASS.
Run: `just test`
Expected: all PASS, coverage 100 % (`_check_file` both raises, `_nulls_by_file` / `_duplicates_by_file` both branches are exercised).

- [ ] **Step 5: Lint and types**

Run: `just lint` and `just mypy`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add power_market_analytics/jma.py tests/test_jma_loader.py
git commit -m "$(cat <<'EOF'
fix(jma): read the hourly files in one scan instead of a per-file union

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Correct the `_read_all` default's docstring

**Files:**
- Modify: `power_market_analytics/csv_loader.py` (`_read_all` docstring, lines ~183-199)

- [ ] **Step 1: Replace the docstring**

```python
    def _read_all(self, files: list[str]) -> DataFrame:
        """Read every file into one DataFrame in the contract's column order.

        The default unions one :meth:`_read_file` frame per file, which is
        only fit for a handful of files: every ``unionByName`` re-analyses
        the whole growing plan and each task deserialises a binary that grows
        with the file count (1,600 files ≈ 8 min of planning, a 45 MiB task
        binary and hours per load). Loaders with hundreds of files override
        this to build a single frame — :meth:`_scan_positional` +
        :meth:`_project` for positional layouts (JMA), one ``createDataFrame``
        over Python-parsed rows (TEPCO でんき予報).

        Parameters
        ----------
        files : list of str
            Resolved file paths, as returned by :meth:`_resolve_files`.

        Returns
        -------
        pyspark.sql.DataFrame
        """
        return reduce(DataFrame.unionByName, (self._read_file(f) for f in files))
```

- [ ] **Step 2: Lint, commit**

Run: `just lint`
Expected: clean.

```bash
git add power_market_analytics/csv_loader.py
git commit -m "$(cat <<'EOF'
docs(loader): say what the per-file union default is fit for

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Real-data verification (main session, devcontainer)

**Files:** none in the repo; scratch scripts under the session scratchpad.

**Prerequisite:** Docker Desktop up (`docker compose ps` lists the stack).

- [ ] **Step 1: Scratch load with the new loader (background task)**

Write `scratchpad/jma_stage1_scratch_load.py`:

```python
"""Load the JMA hourly files with the branch's loader into a scratch table."""

import time
from pathlib import Path

from loguru import logger

from power_market_analytics.csv_loader import CsvTableSchema
from power_market_analytics.jma import JmaHourlyCsvLoader

REPO = Path("/workspace")
schema = CsvTableSchema.from_yaml(REPO / "conf/schemas/jma_hourly_staffed.yaml")
loader = JmaHourlyCsvLoader(
    schema=schema,
    filepath=REPO / "data/jma/hourly/s*_101-201-301-401-501-605-610_*.csv",
    table="pma_scratch.jma_hourly_staffed",
)
t0 = time.perf_counter()
n = loader.load()
logger.info("scratch load: {} rows in {:.1f}s", n, time.perf_counter() - t0)
```

Run (background, `timeout` 600000):

```bash
docker compose exec -T -e PYTHONPATH=/workspace devcontainer python - < scratchpad/jma_stage1_scratch_load.py > scratchpad/jma_stage1_scratch_load.log 2>&1; echo "EXIT=$?" >> scratchpad/jma_stage1_scratch_load.log
```

Expected: `EXIT=0`, ~1 min, no `Broadcasting large task binary` warning.

- [ ] **Step 2: Diff against production**

```bash
docker compose exec thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin --silent=true --outputformat=tsv2 -e "
select 'prod' t, count(*) n, count(distinct station_id) stations, min(observed_at) min_at, max(observed_at) max_at from pma_raw.jma_hourly_staffed
union all
select 'scratch', count(*), count(distinct station_id), min(observed_at), max(observed_at) from pma_scratch.jma_hourly_staffed;
select count(*) only_in_prod from (select * from pma_raw.jma_hourly_staffed except select * from pma_scratch.jma_hourly_staffed);
select count(*) only_in_scratch from (select * from pma_scratch.jma_hourly_staffed except select * from pma_raw.jma_hourly_staffed);"
```

Expected: identical counts/spans; both `EXCEPT` counts 0. (If the production table is older than the files on disk, compare against a scratch load made with the `main` loader instead — `git stash` is not needed: run the same snippet with `PYTHONPATH` pointing at a `git worktree add /tmp/pma-main main` checkout mounted… simpler: note the difference and re-run after Step 4 refreshes production.)

- [ ] **Step 3: Memory check at 4g**

Re-run Step 1 with `-e SPARK_DRIVER_MEMORY=4g` added to the `docker compose exec` command; expected `EXIT=0` in about the same time. Record the wall time for the docs.

- [ ] **Step 4: Drop the scratch database, run the real entry point (background), then dbt**

```bash
docker compose exec thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin --silent=true -e "drop database if exists pma_scratch cascade"
```

Background task (`timeout` 600000): `just python scripts/load_jma_hourly.py > scratchpad/load_jma_hourly_stage1.log 2>&1; echo "EXIT=$?" >> scratchpad/load_jma_hourly_stage1.log` — expected `Loaded 13658616 rows` (or the current file total) and `EXIT=0` in ~1 min.

Then: `just dbt build --select stg_jma__hourly_staffed` — expected PASS.

---

### Task 7: Docs

**Files:**
- Modify: `CLAUDE.md:381-383`
- Modify: `docs/JMA-Weather-Data-Retrieval.md:636-640`

- [ ] **Step 1: CLAUDE.md gotcha** — replace the three lines

```
- Large raw reloads (JMA hourly ≈1.7k files / 1.2 GB; MSM) need the compose default
  `SPARK_DRIVER_MEMORY=20g` — a 1g driver stalls ("no recent heartbeats"), 8g OOMs in the
  parquet write; size per `.env.template`. An aborted overwrite leaves the old table intact.
```

with

```
- Many-file raw reloads: `CsvLoader._read_all`'s default unions one frame per file, which is
  only fit for a handful of files — every `unionByName` re-analyses the whole plan and each
  task deserialises a binary proportional to the file count (1,600 files ≈ 8 min of planning,
  a 45 MiB binary, ~1 h 45 min per load and a 20g driver). Loaders with hundreds of files read
  them in one scan instead (`_scan_positional` + `_project`; JMA hourly since 2026-08-30 —
  1,608 files in ~<MEASURED> s at `SPARK_DRIVER_MEMORY=4g`). MSM (≈1.6k `csv.gz`) still takes
  the union path and needs the compose default `SPARK_DRIVER_MEMORY=20g` (a 1g driver stalls,
  8g OOMs in the parquet write; size per `.env.template`) until it is converted. An aborted
  overwrite leaves the old table intact.
```

filling `<MEASURED>` from Task 6.

- [ ] **Step 2: JMA doc** — replace lines 636-640

```
`fct_jma_weather_hourly`) carries it downstream. Loading needs Spark, so run inside the
devcontainer. A full reload reads all ~1,718 station-year files at once, so it needs a
large Spark driver heap: the compose environment defaults `SPARK_DRIVER_MEMORY` to `20g`
(`docker-compose.yaml`, overridable per-host in `.env`); smaller overrides stall (`1g`) or
OOM during the parquet write (`8g`).
```

with

```
`fct_jma_weather_hourly`) carries it downstream. Loading needs Spark, so run inside the
devcontainer. The loader checks every file in Python (column count, station id in the name)
and then reads all of them in a **single Spark scan** (`CsvLoader._scan_positional`; the
station id comes from each row's file name), so a full reload of ~1,600 station-year files
takes about <MEASURED> s and runs at any driver size (verified at `SPARK_DRIVER_MEMORY=4g`).
Before 2026-08-30 it unioned one frame per file, which cost ~8 min of planning, a 45 MiB task
binary and ~1 h 45 min per load on a 20g driver — the reason the compose default is still 20g
(the MSM loader has not been converted yet).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/JMA-Weather-Data-Retrieval.md
git commit -m "$(cat <<'EOF'
docs(jma): describe the single-scan load and retire the 20g requirement for JMA

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: PR and reviews

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin fix/jma-loader-single-scan
gh pr create --title "fix(jma): read the hourly files in one scan instead of a per-file union" --body-file - <<'EOF'
## Why
`just python scripts/load_jma_hourly.py` took ~1 h 45 min and a 20g driver (8 min of planning + a 45 MiB task binary from a 1,608-branch `unionByName` plan) and looked hung from any client that did not live that long.

## What
- `CsvLoader`: `SOURCE_FILE_COL`, `_scan_positional` (one FileScan over all files), `_project`, per-file suffixes on the null / duplicate-grain errors, hidden column dropped at write.
- `JmaHourlyCsvLoader`: every file checked in Python first, then one scan; station id from each row's file name; `_read_file` removed.
- Docs: CLAUDE.md gotcha, JMA retrieval doc.

Stage 1 of `docs/superpowers/specs/2026-08-30-csv-loader-single-scan-design.md` (area actuals, e-Stat and the header-based default follow in their own PRs).

## Proof
- Scratch load vs production: <rows> rows / <stations> stations, `EXCEPT` both ways = 0.
- `just python scripts/load_jma_hourly.py`: <MEASURED> s (was ~1 h 45 min); also completes at `SPARK_DRIVER_MEMORY=4g`.
- `just test` (100 % coverage), `just lint`, `just mypy`, `just dbt build --select stg_jma__hourly_staffed` green.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 2: Wait for Codex's review** (it posts within ~5–15 min of each push): poll `gh pr view --comments` / review threads, address every item with a commit, push, and repeat until a push draws no new findings.

- [ ] **Step 3: Request a Copilot review** (GitHub MCP `request_copilot_review` on the PR), address its findings the same way, then report the PR as ready for the researcher to merge.

---

## Self-review notes

- Spec coverage (stage 1): protocol constants/methods ✔ (Tasks 1-3), JMA read + `_check_file` + `_read_file` removal ✔ (Task 4), `_read_all` docstring ✔ (Task 5), verification protocol ✔ (Task 6), docs ✔ (Task 7), git/PR/review process ✔ (Task 8). Stages 2-4 are deliberately out of this plan.
- Names used consistently: `SOURCE_FILE_COL`, `REPORT_LIMIT`, `_scan_positional(files, column_count)`, `_project(raw)`, `_nulls_by_file(df, columns)`, `_duplicates_by_file(df)`, `_check_file(file, expected)`.
- The `<MEASURED>` placeholders in Task 7 / Task 8 are filled from Task 6's numbers — they are the only values not knowable before the run.
