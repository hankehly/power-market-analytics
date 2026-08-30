# CsvLoader header groups: group by first line, let Spark verify every header

**Date:** 2026-08-30
**Status:** Implemented 2026-08-30 (PR #27); supersedes the header-based half of
[2026-08-30-csv-loader-single-scan-design.md](2026-08-30-csv-loader-single-scan-design.md)
(stage 4, merged as #24)

## Context and motivation

Stage 4 gave `CsvLoader._read_all` (the default for header-based contracts — JEPX, OCCTO ×2,
MSM) one Spark scan per header layout instead of one frame per file. To group files by layout
without a Spark job per file, it sniffs each file's header **in Python, with the contract's CSV
dialect**, and judges it against the contract; Spark's own parse is consulted only when the
Python parse fails or is rejected. That preflight is where the cost went: eleven review rounds
on #24 (2026-08-30) each found one more way the Python parse could accept a header that Spark
names differently — case-only duplicates, trimming options, `emptyValue`, disabled or unquoted
escapes, doubled quotes, `unescapedQuoteHandling`, BOM-dependent charsets, … — because
accepting such a header silently misaligns columns (a contract source resolves to nothing and
`_cast` reads null). Every corner was fixed by mirroring more of Spark's dialect in Python or by
deferring that dialect to Spark, and today **358 of the file's 810 lines** (12 methods) and
**41 of the loader's tests** exist to judge headers. None of it affects the five production
headers, which all take the fast path; it exists to make grouping *exact* for inputs no source
has.

Two Spark facts make the exactness unnecessary (both probed on pyspark 4.1.1, 2026-08-30):

- With `enforceSchema=false` and `header=true`, Spark checks **every file's** header against
  the scan's schema and fails the read naming the file (`CSV header does not conform to the
  schema … Expected: id but found: v … CSV file: file:///…/c.csv`). Probed through the
  loader's own scan (which selects the contract's columns, so Spark's CSV column pruning
  applies), the check is **positional over the contract's columns**: each file must carry
  them at the same positions under the same names (folded per `spark.sql.caseSensitive`) as
  the scan's schema — which Spark infers from whichever file its listing puts first, the
  largest — and a file with fewer or more columns passes as long as those positions line up;
  unselected columns (an empty or `nullValue`-named header cell, an extra provider column)
  are never compared. Positional agreement is exactly the condition under which a shared scan
  reads a file correctly, so a wrongly grouped file is either read correctly or refused by
  name. With the default `enforceSchema=true` the same input is read silently misaligned
  (`('5','6')` under `id,v` from a `v,id` file) — the failure mode the whole preflight
  defends against.
- `spark.read.options(header="true", inferSchema="false").csv(file).columns` is Spark's own
  naming of a file's header (one small job): `makeSafeHeader` applied — empty / `nullValue`
  cells `_c<i>`, duplicates suffixed — with no Python port needed.

So grouping only has to satisfy *same bytes → same header*; Spark verifies each file at scan
time and names the offender if the grouping was ever wrong. The Python dialect mirror can go.

## Design

### `_read_all` (header-based default)

```python
groups: dict[bytes | str, list[str]] = {}
for file in files:
    line = self._first_line(file)            # bytes; None = only Spark can open it
    groups.setdefault(line if line is not None else file, []).append(file)
frames = []
for key, members in groups.items():
    names, cells = self._group_header(members[0])
    problem = self._header_problem(names, cells)
    if problem is not None:
        raise ValueError(f"{self._group_label(members)} {problem}")
    frames.append(self._read_layout(members))
return reduce(DataFrame.unionByName, frames)
```

- **`_first_line(file) -> bytes | None`** — the first line Spark will treat as the header, as
  raw bytes: read (plain / `gzip` / `bz2` / `zlib` for `.deflate`, Hadoop's DefaultCodec) up to
  the line terminator — `\n`, `\r`, `\r\n` (Hadoop's line reader) or the contract's `lineSep`
  encoded as ASCII — skipping lines that are empty after trimming bytes ≤ 0x20 (Spark's
  `filterCommentAndEmpty` uses Java `trim`) and, when the contract sets `comment`, lines
  starting with that ASCII byte. No decoding, no dialect: a BOM, quotes, escapes, separators
  are just bytes in the key. Returns `b""` for a file with no such line (empty file) and
  `None` for a codec Python cannot open (`.zst`, `.lz4`, `.snappy`) or a `lineSep`/`comment`
  that is not ASCII — such a file forms its own group (per-file cost, only for inputs no
  source has). ASCII-compatible charsets are assumed, which is Spark's own assumption for
  line-mode CSV (it splits on the 0x0A byte before decoding).
- **`_group_header(file) -> tuple[list[str], list[str]]`** — Spark's names for the group's
  first file (`header="true"`, `.columns`) and its raw header cells (`header="false"`,
  `.head(1)`, nulls as `""`); both reads under `_spark_options(inferSchema="false")` (and the
  respective `header`). Two tiny jobs **per group**, not per file.
- **`_header_problem(names, cells) -> str | None`** — unchanged rules, on Spark's names:
  reserved `_source_file` cell (folded), a contract source that recurs among `cells` (folded,
  so `id,ID` by default) and is absent from `names` → `has duplicated header columns`, a
  required source (other than `_source_file`) that `_resolve`s to nothing → `is missing
  required columns`. `_resolve` / `_fold` stay as they are (used by `_cast` too).
- **`_group_label(members)`** — `members[0]` for a singleton group, else
  `"{members[0]} (+{n-1} files with the same header)"`; existing messages for single files
  are byte-identical (tests pin them).
- **`_read_layout(files)`** — `_spark_options(header="true", inferSchema="false",
  enforceSchema="false")`, then `SOURCE_FILE_COL` and `_project` as today. `enforceSchema=false`
  is the safety net: a file whose parsed header does not carry the contract's columns at the
  group's positions (possible only where the first physical line under-determines the header —
  a `multiLine` header cell spanning lines, or a `lineSep` the byte sniff could not apply)
  fails the scan with Spark's message naming the file. It surfaces at `load()`'s first action;
  it is left to propagate (the message already says what and where).

Grouping is therefore allowed to be **over-fine** (`"id","v"` and `id,v`, a BOM'd and an
un-BOM'd file → separate groups → one extra scan) and can never be **silently under-fine**:
a file in the wrong group is read correctly (its contract columns line up) or refused by name.

### Removed

`_header_line`, `_parse_header`, `_read_header`, `_spark_header`, `_safe_header`,
`python_codec`, `_JAVA_TO_PYTHON_CODEC`, `_python_knows`, `_SPARK_ONLY_SUFFIXES`,
`_BOM_DEPENDENT_CODECS`, the `codecs` and `csv` imports, and the deferral list they served
(multi-character separator, disabled escape, escape character in the line, quote inside a
cell, `unescapedQuoteHandling`, trimming options, `emptyValue`, `multiLine`, custom `lineSep`,
Java-only and BOM-dependent charsets). None is used outside `csv_loader.py` and its tests.
Kept: `_option`, `_spark_options` (now also carrying `enforceSchema`), `_fold`, `_resolve`,
`_project`, `_cast`, `_scan_positional`, `_validate` and the per-file reports, `load()`,
the `CsvTableSchema` validator (reserved column name).

### Unchanged behaviour (the contract of the refactor)

Same tables, row for row (scratch `EXCEPT` both ways = 0 against the tables #24 loaded);
same error messages for single-file groups; positional loaders (JMA, area actuals),
でんき予報 and e-Stat untouched; `load()` still drops `SOURCE_FILE_COL`; `header` /
`inferSchema` still loader-owned; sources still resolve as the session does.

### Cost model

Python: one bounded byte read per file (no decoding), ~0.1 ms. Spark: two small jobs per
group plus one scan per group — MSM 1 group, OCCTO 1 each, JEPX one per header generation.
The former per-file fallback (56 ms/file) exists only for files `_first_line` cannot open.

## Probes before implementation (each becomes a test or a doc line)

1. `CSVExprUtils.filterCommentAndEmpty`: whitespace-only leading lines are skipped (Java
   `trim`) — the byte sniff must skip the same lines, else a file's key is a blank line.
2. `enforceSchema=false` with a later file that has **fewer / more** columns than the group's
   header, and with a header cell Spark names `_c<i>` (empty / `nullValue`): confirm the check
   fails or passes as expected and names the file.
3. `enforceSchema=false` under `spark.sql.caseSensitive=true` and `false` with a case-only
   header difference between two files (the same first line cannot differ by case, so this only
   documents the net's behaviour).
4. `multiLine=true` with two files sharing a first physical line but different continuation:
   the scan must fail naming the second file (the one documented under-fine case).
5. `zlib.decompressobj()` reads Hadoop `.deflate` (RFC 1950) — the round-3 test wrote zlib
   format, so this should hold.

## Tests

- **Keep as outcome tests** (drop assertions on removed helpers): one-scan-per-layout / union
  count, missing required column named per file, duplicated header (exact and case-only, both
  session settings), reserved `_source_file` cell and column name, `nullValue` header cell,
  blank and comment lines, empty file, gzip / bz2 / deflate, `_metadata` shadowing, `+` in file
  names, option keys case-insensitive, `header` / `inferSchema` ownership, strings-then-cast,
  session-resolver source matching, no `_read_file`.
- **Fold into one parametrised test** — "the dialect is Spark's business: the loader loads
  what Spark reads" — the sixteen former deferral cases (sep / delimiter alias, quote, quoting
  disabled, escape, disabled escape, unquoted escape, doubled and malformed quotes,
  `unescapedQuoteHandling`, multi-character separator, trimming, `emptyValue`, `multiLine`,
  `lineSep`, Java-only charset, UTF-16/32 under `multiLine`): each asserts the load and the
  values, nothing about how the header was read.
- **Delete**: the Python-misparse-verified-by-Spark test, the port-fidelity matrix,
  `TestPythonCodec`.
- **Add**: whitespace-only leading lines skipped; a group error names the first file and the
  count; a file whose header differs from its group fails the scan naming the file (probe 4);
  a file `_first_line` cannot open forms its own group and still loads (monkeypatched
  `_first_line`); the enforceSchema net is on (`_spark_options` for the layout read carries it).

Expected: ~120 lines of header logic instead of 358, ~20 tests instead of 43, coverage gate
unchanged at 100 %.

## Verification (the PR's Proof section)

1. `just test`, `just lint`, `just mypy`.
2. Container, background: JEPX, OCCTO ×2 and MSM loaded into `pma_scratch.*` by the new
   loader; beeline `EXCEPT` both ways against the production tables = 0; group counts and
   wall time recorded (expect JEPX ~13 s, OCCTO seconds, MSM ~61 s, unchanged); drop
   `pma_scratch`.
3. The four real `scripts/load_*.py` entry points, then `just dbt build --select` their
   staging models.
4. Docs: the CLAUDE.md "Many-file raw reloads" gotcha ("header-based ones one scan per exact
   header line" → grouped by their first line, every file's header verified by Spark at scan
   time), the `_read_all` docstring, this spec's status line.

## Delivery

One PR. Branch `chore/csv-loader-spark-verified-header-groups` (a refactor — neither `fix/`
nor `feature/`), commits `refactor(loader): …`, label `enhancement` (closest default; the
`chore/` → `documentation` mapping in CLAUDE.md was written for docs PRs — **decision for the
researcher**), assignee hankehly, Codex → Copilot as documented. Review-loop rule to carry
in: the third finding of the same defect class is a signal to restate the design, not to
patch again — a one-line addition to CLAUDE.md's "Address every finding", in this PR or a
docs PR (**decision for the researcher**).

## Non-goals

No change to contracts, validation rules, overwrite semantics, dbt models, `just` recipes or
script CLIs; no change to the positional, でんき予報 or e-Stat loaders; no new options.
