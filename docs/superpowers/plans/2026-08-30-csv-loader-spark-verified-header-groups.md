# CsvLoader Spark-verified header groups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `CsvLoader`'s Python header preflight with byte-level first-line grouping, Spark's own column names per group, and `enforceSchema=false` on every grouped scan — same tables, same messages, 358 → ~120 lines of header logic.

**Architecture:** `_read_all` groups files by the raw bytes of their first header line (`_first_line`, no decoding), asks Spark for each group's names and cells once (`_group_header`), judges them with the existing `_header_problem` rules, and scans each group with `enforceSchema=false` so Spark verifies every file's header positionally and names any offender. The Python dialect mirror, its deferral list and the codec map are deleted.

**Tech Stack:** PySpark 4.1.1 CSV reader (`enforceSchema`, `makeSafeHeader` naming), Python `gzip` / `bz2` / `zlib`, pytest with the local `spark` fixture.

**Spec:** `docs/superpowers/specs/2026-08-30-csv-loader-spark-verified-header-groups-design.md`

## Global Constraints

- 100 % coverage gate (`just test`), `just lint`, `just mypy` green before every commit.
- NumPy docstrings; `ruff format` line length 100 (the PostToolUse hook formats Edit/Write; Bash edits need `uv run ruff format` + `ruff check --fix`).
- Conventional Commits `refactor(loader): …` on branch `chore/csv-loader-spark-verified-header-groups`; `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Existing single-file error messages are byte-identical (tests pin them); production tables `EXCEPT` both ways = 0.

## Probe results (2026-08-30, pyspark 4.1.1) the code relies on

- Leading lines Spark skips before the header: empty after stripping `\r` and **spaces** (`   `, `\r\n`, `\n\n`); a line containing a tab is *kept* as the header (`['\t']`). The byte sniff mirrors exactly that.
- `enforceSchema=false`: a later file with a different name at a position, or a different column count, fails the scan with `CSV header does not conform to the schema … CSV file: …` / `Number of column in CSV header is not equal to number of fields in the schema … CSV file: …`; case-only differences pass under `spark.sql.caseSensitive=false`, fail under `true`; a `multiLine` header cell spanning lines that differs after the first physical line fails naming the file. **Limitation:** a header cell equal to a non-default `nullValue` fails the check even against its own file (`Expected: _c1 but found: NA`); an empty cell passes.
- `zlib.decompressobj()` streams Hadoop `.deflate` (DefaultCodec); Spark reads the same file.

---

### Task 1: `_first_line` — the byte-level grouping key

**Files:**
- Modify: `power_market_analytics/csv_loader.py` (module helpers after `_source_file_name`; method after `_option`)
- Test: `tests/test_csv_loader.py` (new class `TestFirstLine`)

**Interfaces:**
- Produces: `CsvLoader._first_line(file: str) -> bytes | None`; module helpers `_decompressed(file) -> Iterator[bytes] | None`, `_CHUNK = 65536`.

- [ ] **Step 1: failing tests**

```python
class TestFirstLine:
    def loader(self, spark, tmp_path, **read_options):
        schema = CsvTableSchema.model_validate({"read_options": read_options, "columns": [{"name": "id", "type": "int"}]})
        return CsvLoader(schema, tmp_path, "t", spark=spark)

    def test_skips_what_spark_skips_and_keeps_bytes_verbatim(self, spark, tmp_path):
        (tmp_path / "f.csv").write_bytes(b"   \r\n\r\n\xef\xbb\xbf\"id\",v\r\n1,2\r\n")
        assert self.loader(spark, tmp_path)._first_line(str(tmp_path / "f.csv")) == b'\xef\xbb\xbf"id",v'

    def test_a_tab_only_line_is_the_header_as_for_spark(self, spark, tmp_path):
        (tmp_path / "f.csv").write_bytes(b"\t\nid,v\n")
        assert self.loader(spark, tmp_path)._first_line(str(tmp_path / "f.csv")) == b"\t"

    def test_comment_lines_and_bare_cr_terminators(self, spark, tmp_path):
        (tmp_path / "f.csv").write_bytes(b"# note\r# more\rid,v\r1,2\r")
        assert self.loader(spark, tmp_path, comment="#")._first_line(str(tmp_path / "f.csv")) == b"id,v"

    def test_custom_line_separator_is_the_only_terminator(self, spark, tmp_path):
        (tmp_path / "f.csv").write_bytes(b"id,v\r|1,2|")
        assert self.loader(spark, tmp_path, lineSep="|")._first_line(str(tmp_path / "f.csv")) == b"id,v\r"

    @pytest.mark.parametrize("suffix, compress", [(".csv.gz", gzip.compress), (".csv.bz2", bz2.compress), (".csv.deflate", zlib.compress)])
    def test_hadoop_codecs_python_can_open(self, spark, tmp_path, suffix, compress):
        (tmp_path / f"f{suffix}").write_bytes(compress(b"\nid,v\n1,2\n"))
        assert self.loader(spark, tmp_path)._first_line(str(tmp_path / f"f{suffix}")) == b"id,v"

    def test_empty_file_and_only_blank_lines_give_empty_bytes(self, spark, tmp_path):
        (tmp_path / "e.csv").write_bytes(b"")
        (tmp_path / "b.csv").write_bytes(b"\n  \n")
        loader = self.loader(spark, tmp_path)
        assert loader._first_line(str(tmp_path / "e.csv")) == b""
        assert loader._first_line(str(tmp_path / "b.csv")) == b""

    def test_last_line_without_terminator_is_returned(self, spark, tmp_path):
        (tmp_path / "f.csv").write_bytes(b"\nid,v")
        assert self.loader(spark, tmp_path)._first_line(str(tmp_path / "f.csv")) == b"id,v"

    @pytest.mark.parametrize("suffix", [".csv.zst", ".csv.lz4", ".csv.snappy"])
    def test_codecs_python_cannot_open_are_none(self, spark, tmp_path, suffix):
        (tmp_path / f"f{suffix}").write_bytes(b"whatever")
        assert self.loader(spark, tmp_path)._first_line(str(tmp_path / f"f{suffix}")) is None

    @pytest.mark.parametrize("read_options", [{"lineSep": " "}, {"comment": "＃"}])
    def test_non_ascii_line_separator_or_comment_is_none(self, spark, tmp_path, read_options):
        (tmp_path / "f.csv").write_bytes(b"id,v\n")
        assert self.loader(spark, tmp_path, **read_options)._first_line(str(tmp_path / "f.csv")) is None

    def test_reads_past_the_first_chunk(self, spark, tmp_path):
        (tmp_path / "f.csv").write_bytes(b"\n" * 70000 + b"id,v\n")
        assert self.loader(spark, tmp_path)._first_line(str(tmp_path / "f.csv")) == b"id,v"
```

- [ ] **Step 2: run** `uv run pytest tests/test_csv_loader.py -k TestFirstLine -q` → FAIL (`AttributeError: _first_line`).
- [ ] **Step 3: implement**

```python
_CHUNK = 65536


def _decompressed(file: str) -> Iterator[bytes] | None:
    """``file`` in 64 KiB chunks as Spark would see it, or ``None`` for a codec Python cannot open."""
    suffix = Path(file).suffix.lower()
    if suffix in (".zst", ".lz4", ".snappy"):
        return None
    if suffix == ".deflate":
        return _inflated(file)
    opener = {".gz": gzip.open, ".bz2": bz2.open}.get(suffix, open)
    return _chunks(opener(file, "rb"))


def _chunks(f: IO[bytes]) -> Iterator[bytes]:
    with f:
        while chunk := f.read(_CHUNK):
            yield chunk


def _inflated(file: str) -> Iterator[bytes]:
    inflater = zlib.decompressobj()
    with open(file, "rb") as f:
        while chunk := f.read(_CHUNK):
            yield inflater.decompress(chunk)
```

```python
    def _first_line(self, file: str) -> bytes | None:
        charset = self._option("encoding", self._option("charset", "UTF-8"))
        if self._option("multiLine", "false").lower() == "true" or not _ascii_compatible(charset):
            return None  # grouped alone: Spark reads this file's header by itself
        try:
            line_sep = self._option("lineSep", "").encode("ascii")
            comment = self._option("comment", "").encode("ascii")
        except UnicodeEncodeError:
            return None
        chunks = _decompressed(file)
        if chunks is None:
            return None
        terminator = re.compile(re.escape(line_sep) if line_sep else rb"\r\n|\n|\r")
        buffer = b""
        for chunk in chunks:
            buffer += chunk
            while (end := terminator.search(buffer)) is not None:
                line, buffer = buffer[: end.start()], buffer[end.end() :]
                if _is_header_line(line, comment):
                    return line
        return buffer if _is_header_line(buffer, comment) else b""


def _is_header_line(line: bytes, comment: bytes) -> bool:
    return bool(line.strip(b" ")) and not (comment and line.startswith(comment))
```
(full NumPy docstrings as in the spec; imports `re`, `zlib`, `Iterator` from `collections.abc`.)

- [ ] **Step 4: run** the class → PASS. **Step 5: commit** `refactor(loader): byte-level first-line sniff for header grouping`.

### Task 2: group → Spark names → verified scan; delete the preflight

**Files:**
- Modify: `power_market_analytics/csv_loader.py`
- Test: `tests/test_csv_loader.py` (`TestHeaderGroupedRead` rewritten; `TestPythonCodec` deleted)

**Interfaces:**
- Consumes: `_first_line` (Task 1), `_resolve`, `_fold`, `_spark_options`, `_option`, `_project`.
- Produces: `_group_header(file) -> tuple[list[str], list[str]]`, `_group_label(members) -> str`, `_header_problem(names, cells) -> str | None`, `_read_layout` with `enforceSchema="false"`.

- [ ] **Step 1: rewrite `TestHeaderGroupedRead`** — keep the outcome tests listed in the spec (drop assertions on removed helpers), one parametrised "dialect is Spark's business" test for the former deferral cases, delete the misparse-verified and port-fidelity tests and `TestPythonCodec`, add: tab-only leading line → `missing required columns` naming the file; group error names the first file `(+N files with the same header)`; a forced wrong grouping (monkeypatched `_first_line`) shows the scan's check refusing a file whose contract columns are not at the group's positions, naming it; `multiLine` files are grouped alone, so an optional column present in only one of two files sharing a first physical line is kept; a non-default `nullValue` header cell is refused as missing only for a contract that sources it and disturbs nothing otherwise; a file `_first_line` cannot open forms its own group and still loads; `_spark_options` for the layout read carries `enforceSchema="false"`.
- [ ] **Step 2: run** → many FAIL.
- [ ] **Step 3: implement** `_group_header`, `_group_label`, the new `_read_all`, `_header_problem(names, cells)` (reserved-name check on the raw cells), `_read_layout(+enforceSchema)`; delete `_header_line`, `_parse_header`, `_read_header`, `_spark_header`, `_safe_header`, `python_codec`, `_JAVA_TO_PYTHON_CODEC`, `_python_knows`, `_SPARK_ONLY_SUFFIXES`, `_BOM_DEPENDENT_CODECS`, the `csv` import (`codecs` stays for `_ascii_compatible`); update the `_read_all` / `_spark_options` docstrings and the module docstring's mechanism sentence.

```python
    def _read_all(self, files: list[str]) -> DataFrame:
        groups: dict[bytes | str, list[str]] = {}
        for file in files:
            line = self._first_line(file)
            groups.setdefault(file if line is None else line, []).append(file)
        frames = []
        for members in groups.values():
            names, cells = self._group_header(members[0])
            problem = self._header_problem(names, cells)
            if problem is not None:
                raise ValueError(f"{self._group_label(members)} {problem}")
            frames.append(self._read_layout(members))
        return reduce(DataFrame.unionByName, frames)

    def _group_header(self, file: str) -> tuple[list[str], list[str]]:
        names = self.spark.read.options(**self._spark_options(header="true", inferSchema="false")).csv(file).columns
        rows = self.spark.read.options(**self._spark_options(header="false", inferSchema="false")).csv(file).head(1)
        return names, (["" if cell is None else str(cell) for cell in rows[0]] if rows else [])

    @staticmethod
    def _group_label(members: list[str]) -> str:
        return members[0] if len(members) == 1 else f"{members[0]} (+{len(members) - 1} files with the same header)"
```
- [ ] **Step 4: run** `tests/test_csv_loader.py`, then `just test`, `just lint`, `just mypy` → PASS, 100 %.
- [ ] **Step 5: commit** `refactor(loader): group by first line and let Spark verify every header`.

### Task 3: docs

- [ ] CLAUDE.md "Many-file raw reloads" gotcha: header-based loaders are grouped by their files' first line (bytes) with every file's header verified by Spark at scan time (`enforceSchema=false`); a header cell equal to a non-default `nullValue` is refused by that check.
- [ ] CLAUDE.md "Address every finding": the third finding of the same defect class on a PR is a signal to restate the design as a closed rule (or ask whether the class is in scope) instead of patching the corner — #24's eleven rounds on the header preflight.
- [ ] Spec status line → "Implemented …, PR #n".
- [ ] commit `docs(claude): Spark-verified header groups; the third finding of a class restates the design`.

### Task 4: verification (container, background tasks)

- [ ] Scratch loads of JEPX, OCCTO ×2, MSM into `pma_scratch.*` with the new loader; beeline `EXCEPT` both ways vs the production tables = 0; group counts + wall times; `drop database pma_scratch cascade`.
- [ ] `just python scripts/load_jepx_spot.py`, `load_occto_demand_forecast.py`, `load_occto_area_reserve_rate.py`, `load_jma_msm_surface_forecast.py`; `just dbt build --select stg_jepx__spot stg_occto__demand_forecast_dad stg_occto__area_reserve_rate_dad stg_jma__msm_surface_forecast` (model names per `dbt/models/staging`).

### Amendments made during review (#27)

- `multiLine` files and files of a charset Python cannot confirm ASCII-compatible (`UTF-16`/`UTF-32`, EBCDIC, a Java-only name) form singleton groups: the first physical line does not determine their header, or the sniff's ASCII comparisons do not apply (Copilot, Codex).
- The scan's `enforceSchema=false` check is positional over the contract columns the scan's schema resolves; an optional source the schema lacks is read as null, not checked — the grouping rule, not the check, keeps layouts apart (Copilot). A `nullValue` header cell is therefore refused only for a contract that sources it.
- The reserved `_source_file` check runs on the raw cells, since Spark suffixes a duplicated cell (Copilot).
- `codecs` stays (for `_ascii_compatible`); the byte sniff skips lines empty after stripping spaces, as Spark 4.1.1 does (a tab-only line is a header to it).

### Task 5: PR and review loop

- [ ] `gh pr create` (title `refactor(loader): group by first line and let Spark verify every header`, body Why / What / Proof), assignee hankehly, label `enhancement`; Codex → Copilot per CLAUDE.md; report ready / merge on the researcher's standing instruction.
