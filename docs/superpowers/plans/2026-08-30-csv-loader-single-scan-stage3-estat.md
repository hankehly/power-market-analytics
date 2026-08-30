# CsvLoader Single-Scan Reads — Stage 3 (e-Stat census mesh) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `EstatCensusMeshCsvLoader` (302 files: 151 per census vintage) read each vintage's files in one header-based Spark scan and validate their rows in one grouped pass per scan, instead of one scan plus one Spark action per file.

**Architecture:** `_read_all` identifies and header-checks every file in Python (as today), groups the files by `(vintage, exact header line)` — so a file whose columns are ordered differently never gets misaligned by Spark's first-file-header rule — and reads each group with `spark.read.options(header="true", …).csv(group)` plus the hidden `SOURCE_FILE_COL`; the file's primary mesh code is joined back on the file name from a tiny lookup frame; `_check_rows` becomes one aggregation grouped by `SOURCE_FILE_COL` per group, keeping today's per-file messages; vintage attributes are literals per group; the few group frames are `unionByName`-ed. `_read_file` is deleted.

**Tech Stack:** Python 3.13 / PySpark 4.1.1, pytest with the repo's `spark` fixture, ruff, mypy, `just`, `docker compose`, beeline.

**Spec:** `docs/superpowers/specs/2026-08-30-csv-loader-single-scan-design.md` (stage 3 row)

## Global Constraints

- Same as stages 1–2 (docstrings, 100 % coverage gate, lint, mypy, Bash edits followed by `ruff format` + `ruff check --fix`).
- Branch `fix/estat-loader-single-scan` off `fix/jma-loader-single-scan` (stage 1 — the only stage this depends on; if #19 has merged, off `main`). PR base accordingly. Never rebase/force-push.
- Conventional Commits with scope `estat`; `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; `git add` only the named files.
- Real-warehouse runs are main-session background tasks.
- Existing error messages must keep matching `tests/test_estat_loader.py::TestValidationFailsBeforeWriting` (file name present; `mesh code.*<code>`, `primary mesh 5339.*<code>`, `population.*<value>`, `HTKSYORI.*<value>`, `label row`, `no data rows`, `missing required columns.*KEY_CODE`, `population column T000847001`).
- Do not touch `data/`, the contract, dbt models, `justfile`, `VINTAGES`, `_identify`, `_resolve_files`.

---

## File structure

| File | Responsibility |
|---|---|
| `power_market_analytics/estat.py` | `_read_all` (group by vintage + header, one scan per group), `_read_group`, `_check_headers` returns the header row, `_check_rows` grouped by file, `_read_file` deleted; class + module docstrings |
| `tests/test_estat_loader.py` | + reordered-header-within-a-vintage test, duplicate-file-name guard test, per-file null report test, no-`_read_file` test |
| `CLAUDE.md` | architecture bullet (validation "per file in one grouped pass"), gotcha lists e-Stat as converted |
| `docs/eStat-Census-Population-Mesh-Retrieval.md` | load paragraph: one scan per vintage/header group, grouped validation, measured time |

---

### Task 0: Branch

- [ ] `git checkout -b fix/estat-loader-single-scan fix/jma-loader-single-scan` (or `main` if #19 merged); `git status --short` shows only the pre-existing entries.

---

### Task 1: One scan per (vintage, header) group with grouped row checks

**Files:**
- Modify: `power_market_analytics/estat.py` (imports; class `EstatCensusMeshCsvLoader` lines 616-790)
- Test: `tests/test_estat_loader.py`

**Interfaces:**
- Consumes (stage 1): `csv_loader.SOURCE_FILE_COL`, `CsvLoader._project(raw)`, `REPORT_LIMIT` (tests only).
- Produces: `EstatCensusMeshCsvLoader._read_all(files) -> DataFrame`; `_read_group(vintage, files, codes) -> DataFrame`; `_check_headers(file, vintage) -> list[str]` (the header row); `_check_rows(raw, vintage) -> None` where `raw` carries `SOURCE_FILE_COL` and `PRIMARY_MESH_CODE_SOURCE`.

- [ ] **Step 1: Write the failing tests**

Import `REPORT_LIMIT` next to `CsvTableSchema`, and append to `tests/test_estat_loader.py`:

```python
class TestSingleScan:
    def test_files_of_one_vintage_with_reordered_headers_load_correctly(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        # Same vintage, population column moved to the end: must not be read
        # through 5339's header by position.
        reordered = [
            "KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847002,T000847003,T000847001",
            ",,,,　人口総数　男,　人口総数　女,　人口総数",
            "534000054,0,,,10,20,30",
        ]
        write_vintage(tmp_path, V2015, "5340", reordered)
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.reorder", spark=spark)

        assert loader.load() == 6

        rows = rows_of(spark, "test_estat_loader.reorder")
        assert rows[(2015, "534000054")]["population_total"] == 30
        assert rows[(2015, "534000054")]["primary_mesh_code"] == "5340"
        assert rows[(2015, "533900054")]["population_total"] == 64
        assert rows[(2015, "533900054")]["primary_mesh_code"] == "5339"

    def test_two_files_with_the_same_name_are_rejected(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_cp932(tmp_path / "1999" / "txt" / V2015.member_name("5339"), LINES_2015)
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.dupnames", spark=spark)

        with pytest.raises(ValueError, match="share the file name tblT000847H5339.txt"):
            loader.load()
        assert not spark.catalog.tableExists("test_estat_loader.dupnames")

    def test_row_check_failure_names_only_the_bad_file(self, spark, tmp_path):
        write_vintage(tmp_path, V2015, "5339", LINES_2015)
        write_vintage(tmp_path, V2015, "5340", ["KEY_CODE,HTKSYORI,HTKSAKI,GASSAN,T000847001", ",,,,　人口総数", "534000054,3,,,1"])
        loader = EstatCensusMeshCsvLoader(CONTRACT, tmp_path, "test_estat_loader.onebad", spark=spark)

        with pytest.raises(ValueError, match=r"tblT000847H5340\.txt: 1 row\(s\) with HTKSYORI") as excinfo:
            loader.load()
        assert "tblT000847H5339.txt" not in str(excinfo.value)

    def test_loader_has_no_per_file_read(self):
        assert "_read_file" not in EstatCensusMeshCsvLoader.__dict__
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_estat_loader.py::TestSingleScan -q -p no:cacheprovider`: the duplicate-name and no-`_read_file` tests fail; the other two pass today (they guard the new path).

- [ ] **Step 3: Implement**

Imports: `from functools import reduce` (if absent) and `from power_market_analytics.csv_loader import SOURCE_FILE_COL, CsvLoader, CsvTableSchema`.

Replace `_read_file` with:

```python
    def _read_all(self, files: list[str]) -> DataFrame:
        names = [Path(file).name for file in files]
        if len(set(names)) != len(names):
            clash = next(name for name in names if names.count(name) > 1)
            raise ValueError(
                f"{names.count(clash)} files share the file name {clash}: the primary mesh "
                "code is joined back on the name, so every file name must be unique"
            )
        # One scan per (vintage, exact header line): Spark applies the first
        # file's header to every file of a multi-path read, so files whose
        # columns are ordered differently must not share a scan.
        groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        vintages: dict[str, CensusVintage] = {}
        codes: dict[str, str] = {}
        for file in files:
            vintage, primary_mesh_code = self._identify(file)
            header = self._check_headers(file, vintage)
            groups.setdefault((vintage.stats_id, tuple(header)), []).append(file)
            vintages[vintage.stats_id] = vintage
            codes[Path(file).name] = primary_mesh_code
        frames = [
            self._read_group(vintages[stats_id], group, codes)
            for (stats_id, _), group in groups.items()
        ]
        return reduce(DataFrame.unionByName, frames)

    def _read_group(
        self, vintage: CensusVintage, files: list[str], codes: dict[str, str]
    ) -> DataFrame:
        """Read files that share ``vintage`` and a header line in one scan.

        Parameters
        ----------
        vintage : CensusVintage
            The census the files belong to (population column, attributes).
        files : list of str
            Paths with identical header lines.
        codes : dict of str to str
            File name → primary mesh code, for every file.

        Returns
        -------
        pyspark.sql.DataFrame
            Contract columns plus ``SOURCE_FILE_COL``, rows validated.
        """
        lookup = self.spark.createDataFrame(
            [(Path(file).name, codes[Path(file).name]) for file in files],
            f"{SOURCE_FILE_COL} string, {PRIMARY_MESH_CODE_SOURCE} string",
        )
        raw = (
            self.spark.read.options(header="true", **self.schema.read_options)
            .csv(files)
            .withColumn(SOURCE_FILE_COL, F.col("_metadata.file_name"))
            .join(F.broadcast(lookup), SOURCE_FILE_COL, "inner")
        )
        self._check_rows(raw, vintage)
        data = (
            raw.filter(F.col(KEY_CODE).isNotNull())
            .withColumn(CENSUS_YEAR_SOURCE, F.lit(vintage.census_year))
            .withColumn(CENSUS_DATE_SOURCE, F.lit(vintage.census_date))
            .withColumn(GEODETIC_DATUM_SOURCE, F.lit(vintage.geodetic_datum))
            .withColumn(STATS_ID_SOURCE, F.lit(vintage.stats_id))
            .withColumn(POPULATION_SOURCE, F.col(vintage.population_source_column))
            .withColumn(SOURCE_FILE_SOURCE, F.col(SOURCE_FILE_COL))
        )
        return self._project(data)
```

`_check_headers`: signature `-> list[str]`, docstring gains a `Returns` section ("the header row"), and `return header` after the `has_data` check.

`_check_rows(self, raw, vintage)` — one aggregation grouped by file, same messages:

```python
    def _check_rows(self, raw: DataFrame, vintage: CensusVintage) -> None:
        """Validate every data row of a scan, reporting per file.

        Parameters
        ----------
        raw : pyspark.sql.DataFrame
            A header-based scan carrying ``SOURCE_FILE_COL`` and
            ``PRIMARY_MESH_CODE_SOURCE``.
        vintage : CensusVintage
            Supplies the population column to check.

        Raises
        ------
        ValueError
            Named after the first offending file: a malformed mesh code, a
            mesh code outside that file's primary mesh, a population that is
            not a non-negative integer literal (``*`` included), ``HTKSYORI``
            outside 0/1/2, or a number of rows without a ``KEY_CODE`` other
            than exactly one (the label row).
        """
        key = F.col(KEY_CODE)
        population = F.col(vintage.population_source_column)
        privacy = F.col(PRIVACY_CODE)
        checks: list[tuple[str, Column]] = [
            ("mesh code", key.isNotNull() & ~key.rlike(MESH_CODE_RE.pattern)),
            (
                "mesh code outside primary mesh {code}",
                key.isNotNull() & ~key.startswith(F.col(PRIMARY_MESH_CODE_SOURCE)),
            ),
            (
                f"population ({vintage.population_source_column}) not a non-negative integer",
                key.isNotNull() & (population.isNull() | ~population.rlike(_POPULATION_RE)),
            ),
            (
                f"{PRIVACY_CODE} not in {list(_ACCEPTED_PRIVACY_CODES)}",
                key.isNotNull() & ~privacy.isin(*_ACCEPTED_PRIVACY_CODES),
            ),
        ]
        counts = (
            raw.groupBy(SOURCE_FILE_COL, PRIMARY_MESH_CODE_SOURCE)
            .agg(
                F.count(F.when(key.isNull(), True)).alias("__label_rows"),
                *[F.count(F.when(cond, True)).alias(f"__c{i}") for i, (_, cond) in enumerate(checks)],
            )
            .orderBy(SOURCE_FILE_COL)
            .collect()
        )
        for row in counts:
            file = row[SOURCE_FILE_COL]
            if row["__label_rows"] != 1:
                raise ValueError(
                    f"{file}: expected exactly one label row without a KEY_CODE, found "
                    f"{row['__label_rows']} rows with an empty KEY_CODE"
                )
            for i, (label, cond) in enumerate(checks):
                n_bad = row[f"__c{i}"]
                if n_bad:
                    examples = [
                        (r[KEY_CODE], r[PRIVACY_CODE], r[vintage.population_source_column])
                        for r in raw.filter((F.col(SOURCE_FILE_COL) == file) & cond)
                        .select(KEY_CODE, PRIVACY_CODE, vintage.population_source_column)
                        .limit(_EXAMPLE_LIMIT)
                        .collect()
                    ]
                    raise ValueError(
                        f"{file}: {n_bad} row(s) with "
                        f"{label.format(code=row[PRIMARY_MESH_CODE_SOURCE])}; first "
                        f"(KEY_CODE, HTKSYORI, population): {examples}"
                    )
        logger.debug("{}: header and row checks passed", [r[SOURCE_FILE_COL] for r in counts])
```

(`Column` needs importing from `pyspark.sql`.) Update the class docstring ("except for how files are found and read") and the module docstring lines 24-30 to say: files are grouped by vintage and header line and read one scan per group, rows validated per file in one grouped pass.

- [ ] **Step 4: Run** `uv run pytest tests/test_estat_loader.py tests/test_load_scripts.py -q -p no:cacheprovider`, then `just test`, `just lint`, `just mypy` — all green, 100 %.

- [ ] **Step 5: Commit** — `fix(estat): read each census vintage in one scan and validate rows per file in one pass`.

---

### Task 2: Real-data verification

- [ ] Scratch load with `EstatCensusMeshCsvLoader(schema, REPO/"data/estat/census_population_mesh", "pma_scratch.estat_census_population_mesh")` (background), then per vintage: `select census_year, count(*), count(distinct source_file)` on prod vs scratch (expect 2015: 471,066 / 151; 2020: 466,156 / 151) and `EXCEPT` both ways = 0; drop `pma_scratch`; `just python scripts/load_estat_census_population_mesh.py` (record wall); `just dbt build --select stg_estat__census_population_mesh`.

---

### Task 3: Docs

- [ ] CLAUDE.md architecture bullet: "validates mesh codes / population / HTKSYORI before casting" → "…, reads each vintage's files in one scan and validates mesh codes / population / HTKSYORI per file in one grouped pass before casting"; gotcha: add "e-Stat since 2026-08-30 — 302 files in ~<MEASURED> s". e-Stat doc load paragraph: replace "reads the file with Spark (`windows-31j`), validates every row before casting (§4)" with "reads each vintage's files in a single Spark scan (`windows-31j`; files grouped by their exact header line, the primary mesh code joined back on the file name), validates every row per file in one grouped pass before casting (§4)" and add the measured time after the code block. Commit `docs(estat): describe the single-scan load`.

---

### Task 4: PR and reviews

- [ ] Push, `gh pr create --base <stage-1 branch or main> --title "fix(estat): read each census vintage in one scan instead of a per-file union"`, assign `hankehly`, labels `bug` + `documentation`; then the CLAUDE.md "Code review" loop (Codex → fix → Copilot → report).
