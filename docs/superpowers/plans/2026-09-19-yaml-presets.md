# Presets as YAML Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the thirteen presets out of `tasks/<task>/presets.py` into one YAML file each under `conf/presets/<task>/`, loaded into the same `Preset` object, with every current preset pinned to today's feature tuple.

**Architecture:** `features/presets.py` gains `load_presets(task)`, which reads `conf/presets/<task>/*.yaml` (a full `features` list, or `base` plus `add` / `drop`), resolves base chains through `Preset.with_changes`, and returns the presets by name. The two task registries call it at import; `features/catalogue.py` walks the task directories. The strategy, MLflow params, Feast service names, warehouse and dashboards are untouched.

**Tech Stack:** Python 3.13, PyYAML (already a dependency), pytest with the 100 % coverage gate, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-19-yaml-presets-design.md`

## Global Constraints

- Branch `feature/yaml-presets`, worked in the worktree `.claude/worktrees/yaml-presets` (the main checkout holds PR #182's branch). Commits `feat(forecasting): …` / `test(forecasting): …` / `docs(forecasting): …`, imperative, lowercase, each ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never write the Codex mention in a PR body, commit or file.
- Feature order is part of a preset: every file lists references in exactly today's order, and the pin test (Task 4) is the proof. No backtest is run.
- The word is "preset" everywhere; the directory is `conf/presets/`; a file's stem is its name (`[a-z0-9_]+`), `description` is required, and a published preset's file is never edited.
- Coverage stays at 100 % (`just test`), `just lint` and `just mypy` clean. The PostToolUse hook runs ruff on every edited `.py` file.
- CLAUDE.md edits use short anchors present on both `main` and `chore/research-on-issues` (PR #182), which edits neighbouring lines of the same bullets; the branch is brought up to date with `main` after #182 merges and the review loop runs again on that head.
- `tmp/` and `.claude/worktrees/` are gitignored. `chatgpt-response.md` at the main checkout's root is the researcher's; leave it.

---

### Task 1: Spec status, design-history row, worktree environment

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-yaml-presets-design.md:3-4`
- Modify: `docs/superpowers/README.md:16` (first table row)

- [ ] **Step 1: Mark the spec approved and add the row**

Run (from the worktree):
```bash
cd /Users/hankehly/Projects/power-market-analytics/.claude/worktrees/yaml-presets
python3 - <<'EOF'
from pathlib import Path
p = Path("docs/superpowers/specs/2026-09-19-yaml-presets-design.md")
s = p.read_text()
old = "Date: 2026-09-19. Status: **draft**, for the researcher's review.\nBranch: `feature/yaml-presets`."
new = "Date: 2026-09-19. Status: **approved** on 2026-09-19; implemented on branch `feature/yaml-presets`\n(plan `docs/superpowers/plans/2026-09-19-yaml-presets.md`)."
assert s.count(old) == 1
p.write_text(s.replace(old, new))
p = Path("docs/superpowers/README.md")
s = p.read_text()
anchor = "| Date | Work | Spec | Plan |\n|---|---|---|---|\n"
row = ("| 2026-09-19 | Presets as YAML files — one file per preset under `conf/presets/<task>/`, "
       "a full list or a base plus add/drop, loaded into the same `Preset`; the thirteen presets pinned | "
       "[spec](superpowers/specs/2026-09-19-yaml-presets-design.md) | "
       "[plan](superpowers/plans/2026-09-19-yaml-presets.md) |\n")
assert s.count(anchor) == 1
p.write_text(s.replace(anchor, anchor + row))
print("ok")
EOF
```
Expected: `ok`.

- [ ] **Step 2: Build the worktree's environment and prove the suite is green before any change**

Run:
```bash
just test -q -x 2>&1 | tail -4
```
Expected: `uv` installs the locked environment into the worktree's `.venv` on first use, then the suite passes with `TOTAL … 100%`.

- [ ] **Step 3: Commit**

Run:
```bash
just docs-links
git add docs/superpowers/specs/2026-09-19-yaml-presets-design.md docs/superpowers/README.md docs/superpowers/plans/2026-09-19-yaml-presets.md
git commit -q -m "docs(forecasting): approve the YAML presets spec and add its plan

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: links resolve, one commit.

---

### Task 2: `Preset.description` and the service description

**Files:**
- Modify: `power_market_analytics/features/presets.py` (the `Preset` dataclass, `with_changes`, `feature_service`)
- Test: `tests/test_features_presets.py`

**Interfaces:**
- Produces: `Preset.description: str = ""`; `with_changes` returns a copy with an empty description; `feature_service(preset).description` is the preset's description, or today's `Preset 'x' of the … task: …` string when it is empty.

- [ ] **Step 1: Write the failing tests**

Add to `class TestPreset` in `tests/test_features_presets.py`:

```python
    def test_description_is_empty_unless_given_and_a_change_clears_it(self):
        assert preset().description == ""
        described = preset(description="Calendar and the lag.")
        assert described.description == "Calendar and the lag."
        assert described.with_changes(add=(DAY_TYPE,), name="q").description == ""
```

Add to `class TestFeatureService`:

```python
    def test_the_description_is_the_presets_or_a_summary(self):
        assert feature_service(preset(description="The lag.")).description == "The lag."
        assert feature_service(preset()).description == (
            "Preset 'p' of the spot_price task: ftr_day_calendar:month, ftr_period_jepx:lag_1d_price."
        )
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_features_presets.py -k "description" -q`
Expected: 2 failed — `TypeError: … unexpected keyword argument 'description'`.

- [ ] **Step 3: Implement**

In `power_market_analytics/features/presets.py`, the dataclass gains a field and `with_changes` clears it:

```python
@dataclasses.dataclass(frozen=True)
class Preset:
    """A named feature list of one task.

    Attributes
    ----------
    task : str
        The task's name (its MLflow experiment), e.g. ``spot_price``.
    name : str
        The strategy label: the registry key and the ``strategy`` column.
    features : tuple of str
        ``<view>:<column>`` references, in feature order.
    base : str or None
        The preset this one was changed from (``with_changes``), by name;
        None for one defined from scratch.
    description : str
        What the preset is, from its file; empty for one built in code.
    """

    task: str
    name: str
    features: tuple[str, ...]
    #: The preset this one was changed from, by name; None for one defined from scratch.
    base: str | None = None
    #: What the preset is, from its file; empty for one built in code or changed on the command line.
    description: str = ""
```

and in `with_changes`, the `dataclasses.replace(...)` call adds `description=""`:

```python
        return dataclasses.replace(
            self,
            name=name,
            base=self.name,
            features=tuple(ref for ref in self.features if ref not in dropped) + added,
            description="",
        )
```

with its docstring gaining the line "The copy's description is empty: a run changed on the command line is not the base's preset."

In `feature_service`, the description becomes:

```python
        description=preset.description
        or f"Preset {preset.name!r} of the {preset.task} task: {', '.join(preset.features)}.",
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_features_presets.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add power_market_analytics/features/presets.py tests/test_features_presets.py
git commit -q -m "feat(forecasting): a preset carries its description

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The loader

**Files:**
- Modify: `power_market_analytics/features/presets.py` (imports, `PRESETS_DIR`, `preset_tasks`, `load_presets` and its helpers)
- Test: `tests/test_features_presets.py`

**Interfaces:**
- Produces: `PRESETS_DIR: Path` (`<repo>/conf/presets`); `preset_tasks(presets_dir=PRESETS_DIR) -> tuple[str, ...]` (the task directories, sorted); `load_presets(task, presets_dir=PRESETS_DIR) -> dict[str, Preset]` (by name, sorted), raising `ValueError` whose message starts with the offending file's path.

- [ ] **Step 1: Write the failing tests**

Add near the top of `tests/test_features_presets.py`:

```python
import re
import textwrap
from pathlib import Path
```

and the import line gains `PRESETS_DIR, load_presets, preset_tasks`. Add the helper and the class:

```python
def write_preset(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(textwrap.dedent(text))
    return path


ONE = "description: The first.\nfeatures: [ftr_day_calendar:month, ftr_period_jepx:lag_1d_price]\n"


class TestLoadPresets:
    def test_a_full_list_and_a_base_chain_resolve_in_order(self, tmp_path):
        task = tmp_path / "demand"
        write_preset(task, "one", ONE)
        write_preset(task, "two", "description: Plus the day type.\nbase: one\nadd: [ftr_day_calendar:day_type]\n")
        write_preset(task, "three", "description: Minus the lag.\nbase: two\ndrop: [ftr_period_jepx:lag_1d_price]\n")
        presets = load_presets("demand", tmp_path)
        assert list(presets) == ["one", "three", "two"]
        assert presets["one"] == Preset("demand", "one", (CALENDAR, LAG), description="The first.")
        assert presets["two"].features == (CALENDAR, LAG, DAY_TYPE)
        assert presets["two"].base == "one"
        assert presets["three"] == Preset(
            "demand", "three", (CALENDAR, DAY_TYPE), base="two", description="Minus the lag."
        )

    def test_a_base_may_add_and_drop_in_one_file(self, tmp_path):
        task = tmp_path / "demand"
        write_preset(task, "one", ONE)
        write_preset(
            task,
            "two",
            "description: Swap.\nbase: one\nadd: [ftr_day_calendar:day_type]\ndrop: [ftr_day_calendar:month]\n",
        )
        assert load_presets("demand", tmp_path)["two"].features == (LAG, DAY_TYPE)

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("- a\n", "is not a mapping"),
            ("description: x\nfeatures: [ftr_a:b]\nextra: 1\n", "unknown keys ['extra']"),
            ("features: [ftr_a:b]\n", "description is required"),
            ("description: ' '\nfeatures: [ftr_a:b]\n", "description is required"),
            ("description: x\n", "exactly one of features and base"),
            ("description: x\nfeatures: [ftr_a:b]\nbase: one\n", "exactly one of features and base"),
            ("description: x\nfeatures: [ftr_a:b]\nadd: [ftr_a:c]\n", "add and drop need a base"),
            ("description: x\nbase: one\n", "a base needs add or drop"),
            ("description: x\nbase: one\nadd: []\ndrop: []\n", "a base needs add or drop"),
            ("description: x\nbase: 3\nadd: [ftr_a:c]\n", "base must be a preset name"),
            ("description: x\nbase: nope\nadd: [ftr_a:c]\n", "base 'nope' is not a preset of demand"),
            ("description: x\nfeatures: ftr_a:b\n", "features must be a list of 'view:column' strings"),
            ("description: x\nfeatures: [lag]\n", "is not '<view>:<column>'"),
            ("description: x\nfeatures: [ftr_a:b, ftr_c:b]\n", "duplicate feature columns ['b']"),
            ("description: x\nfeatures: [ftr_a:time_code]\n", "every preset's first feature"),
            ("description: x\nbase: one\ndrop: [ftr_x:y]\n", "cannot drop ['ftr_x:y']"),
            ("description: x\nbase: one\nadd: [ftr_day_calendar:month]\n", "cannot add"),
        ],
    )
    def test_rejects_a_bad_file_naming_it(self, tmp_path, text, message):
        task = tmp_path / "demand"
        write_preset(task, "one", ONE)
        bad = write_preset(task, "bad", text)
        with pytest.raises(ValueError, match=re.escape(str(bad))) as excinfo:
            load_presets("demand", tmp_path)
        assert message in str(excinfo.value)

    def test_rejects_a_looping_chain(self, tmp_path):
        task = tmp_path / "demand"
        write_preset(task, "a", "description: x\nbase: b\nadd: [ftr_a:b]\n")
        write_preset(task, "b", "description: x\nbase: a\nadd: [ftr_a:c]\n")
        with pytest.raises(ValueError, match=r"base chain loops: a -> b -> a"):
            load_presets("demand", tmp_path)

    def test_rejects_a_name_outside_lowercase_snake_case(self, tmp_path):
        write_preset(tmp_path / "demand", "Bad-Name", ONE)
        with pytest.raises(ValueError, match=r"Bad-Name\.yaml: a preset name is lowercase a-z0-9_"):
            load_presets("demand", tmp_path)

    def test_rejects_a_task_without_files(self, tmp_path):
        (tmp_path / "demand").mkdir()
        with pytest.raises(ValueError, match="no preset files"):
            load_presets("demand", tmp_path)

    def test_the_task_directories_are_listed_sorted(self, tmp_path):
        write_preset(tmp_path / "spot_price", "one", ONE)
        write_preset(tmp_path / "demand", "one", ONE)
        (tmp_path / "notes.txt").write_text("")
        assert preset_tasks(tmp_path) == ("demand", "spot_price")

    def test_the_repository_directories_are_the_tasks(self):
        assert PRESETS_DIR.name == "presets" and PRESETS_DIR.parent.name == "conf"
        assert preset_tasks() == ("demand", "spot_price")
```

The last test needs the directories of Task 4; it fails until then with `()` and is left failing for the moment.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_features_presets.py -k "LoadPresets" -q`
Expected: `ImportError: cannot import name 'load_presets'`.

- [ ] **Step 3: Implement**

In `power_market_analytics/features/presets.py`, the module docstring gains a paragraph:

```
A preset is a YAML file under ``conf/presets/<task>/<name>.yaml``: ``description``
and either ``features`` (the full list, in feature order) or ``base`` with ``add``
and/or ``drop`` (a change to another preset of the task). ``load_presets`` reads
a task's directory into presets by name; the file's stem is the name.
```

Imports gain `import re`, `from pathlib import Path` and `import yaml`. After `EXPRESSION_TAG` add:

```python
#: The directory of preset files: one subdirectory per task, one YAML file per preset.
PRESETS_DIR = Path(__file__).resolve().parents[2] / "conf" / "presets"
#: The keys a preset file may carry.
PRESET_KEYS = frozenset({"description", "features", "base", "add", "drop"})
#: A preset name: the file's stem, the strategy label.
NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
```

After `feature_service` (end of module) add:

```python
@dataclasses.dataclass(frozen=True)
class _PresetFile:
    """One preset file, read and checked, before its base is resolved."""

    description: str
    features: tuple[str, ...] | None
    base: str | None
    add: tuple[str, ...]
    drop: tuple[str, ...]


def _refs(file: Path, data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(ref, str) for ref in value):
        raise ValueError(f"{file}: {key} must be a list of 'view:column' strings")
    for ref in value:
        try:
            feature_column(ref)
        except ValueError as exc:
            raise ValueError(f"{file}: {exc}") from None
    return tuple(value)


def _read_preset_file(file: Path) -> _PresetFile:
    data = yaml.safe_load(file.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{file}: is not a mapping")
    unknown = sorted(set(data) - PRESET_KEYS)
    if unknown:
        raise ValueError(f"{file}: unknown keys {unknown}")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{file}: description is required")
    if ("features" in data) == ("base" in data):
        raise ValueError(f"{file}: exactly one of features and base")
    if "base" not in data and ("add" in data or "drop" in data):
        raise ValueError(f"{file}: add and drop need a base")
    base = data.get("base")
    if "base" in data and not isinstance(base, str):
        raise ValueError(f"{file}: base must be a preset name")
    add, drop = _refs(file, data, "add"), _refs(file, data, "drop")
    if base is not None and not add and not drop:
        raise ValueError(f"{file}: a base needs add or drop")
    features = _refs(file, data, "features") if "features" in data else None
    return _PresetFile(description.strip(), features, base, add, drop)


def preset_tasks(presets_dir: Path = PRESETS_DIR) -> tuple[str, ...]:
    """The tasks with a preset directory, sorted.

    Parameters
    ----------
    presets_dir : pathlib.Path, optional
        The directory of preset files; ``PRESETS_DIR`` when omitted.

    Returns
    -------
    tuple of str
    """
    return tuple(sorted(p.name for p in presets_dir.iterdir() if p.is_dir()))


def load_presets(task: str, presets_dir: Path = PRESETS_DIR) -> dict[str, Preset]:
    """The presets of a task, read from its files, by name and sorted by name.

    A ``features`` file becomes a preset from scratch; a ``base`` file becomes
    the base preset changed with ``add`` and ``drop``, under the file's name
    and description, so ``Preset.base`` names the file it started from.

    Parameters
    ----------
    task : str
        The task's name: the subdirectory of ``presets_dir``.
    presets_dir : pathlib.Path, optional
        The directory of preset files; ``PRESETS_DIR`` when omitted.

    Returns
    -------
    dict of str to Preset

    Raises
    ------
    ValueError
        If the task has no file, a file's name is not lowercase ``a-z0-9_``,
        a file breaks a rule of the format, a base is not a preset of the
        task, a chain of bases loops, or the resolved list breaks a
        :class:`Preset` rule. The message starts with the file's path.
    """
    files = {path.stem: path for path in sorted((presets_dir / task).glob("*.yaml"))}
    if not files:
        raise ValueError(f"{presets_dir / task}: no preset files")
    for stem, path in files.items():
        if not NAME_PATTERN.match(stem):
            raise ValueError(f"{path}: a preset name is lowercase a-z0-9_")
    specs = {stem: _read_preset_file(path) for stem, path in files.items()}
    presets: dict[str, Preset] = {}

    def resolve(name: str, chain: tuple[str, ...]) -> Preset:
        if name in presets:
            return presets[name]
        if name in chain:
            raise ValueError(f"{files[name]}: base chain loops: {' -> '.join((*chain, name))}")
        spec = specs[name]
        try:
            if spec.base is None:
                assert spec.features is not None  # exactly one of the two, checked on read
                preset = Preset(task, name, spec.features, description=spec.description)
            else:
                if spec.base not in specs:
                    raise ValueError(f"base {spec.base!r} is not a preset of {task}")
                base = resolve(spec.base, (*chain, name))
                changed = base.with_changes(add=spec.add, drop=spec.drop, name=name)
                preset = dataclasses.replace(changed, description=spec.description)
        except ValueError as exc:
            message = str(exc)
            raise ValueError(message if message.startswith(str(files[name])) else f"{files[name]}: {message}") from None
        presets[name] = preset
        return preset

    for name in files:
        resolve(name, ())
    return {name: presets[name] for name in sorted(presets)}
```

Note on the loop message: the loop `a -> b -> a` is raised while resolving `a`'s base `b`, whose base `a` is in the chain; the message is raised for file `a` with chain `(a, b)`, and the wrapper leaves it as is because it already starts with `a`'s path. On the way out the wrapper for `b` sees the message starting with `a.yaml`'s path, not `b.yaml`'s, and prefixes it: the test matches on the `base chain loops: a -> b -> a` tail only, which is what a reader needs.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_features_presets.py -q`
Expected: all pass except `test_the_repository_directories_are_the_tasks` (Task 4 creates the directories).

- [ ] **Step 5: Lint and types, then commit**

Run: `just lint && just mypy`
Expected: clean. If mypy objects to `assert spec.features is not None`, replace the assert with `if spec.features is None: raise ValueError("features missing")` (unreachable by the read check, but typed).

```bash
git add power_market_analytics/features/presets.py tests/test_features_presets.py
git commit -q -m "feat(forecasting): load presets from conf/presets/<task>/*.yaml

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The thirteen files and the migration pin

**Files:**
- Create: `conf/presets/demand/*.yaml` (eleven), `conf/presets/spot_price/*.yaml` (two)
- Rewrite: `tests/test_demand_presets.py`
- Create: `tests/test_spot_price_presets.py`

**Interfaces:**
- Consumes: `load_presets` (Task 3).
- Produces: the thirteen files whose resolved lists equal today's registries, proven both by a one-off comparison against the Python registries (still present until Task 5) and by the frozen pin tests.

- [ ] **Step 1: Write the files**

Run:
```bash
mkdir -p conf/presets/demand conf/presets/spot_price
python3 - <<'EOF'
from pathlib import Path

def w(task, name, text):
    Path(f"conf/presets/{task}/{name}.yaml").write_text(text.lstrip("\n"))

CALENDAR_TEN = ["half", "quarter", "day_of_month", "day_of_quarter", "day_of_year", "holiday_degree",
                "is_business_day", "fiscal_quarter", "days_since_holiday", "days_until_holiday"]
COUNTS = ["half", "quarter", "day_of_month", "day_of_quarter", "day_of_year", "fiscal_quarter"]
RECENT = ["ftr_period_actuals:lag_2d_demand_kwh", "ftr_period_actuals:lag_3d_demand_kwh",
          "ftr_period_actuals:lag_14d_demand_kwh", "ftr_period_actuals:lag_21d_demand_kwh",
          "ftr_period_actuals:lag_28d_demand_kwh", "ftr_period_actuals:mean_weekly_lags_demand_kwh",
          "ftr_period_actuals:ewm_weekly_lags_demand_kwh", "ftr_period_actuals:change_2d_9d_demand_kwh",
          "ftr_period_actuals:mean_daytype_4d_demand_kwh", "ftr_period_actuals:ewm_daytype_4d_demand_kwh",
          "ftr_day_actuals:lag_2d_mean_demand_kwh", "ftr_day_actuals:lag_2d_max_demand_kwh",
          "ftr_day_actuals:lag_2d_range_demand_kwh"]
def refs(items): return "".join(f"  - {r}\n" for r in items)
def cal(cols): return refs(f"ftr_day_calendar:{c}" for c in cols)

w("demand", "lightgbm", """
description: >-
  Calendar, the recency-weighted same-hour temperature over D-8..D-2 at the
  representative station and the D-7 demand lag.
features:
  - ftr_day_calendar:month
  - ftr_day_calendar:day_of_week
  - ftr_hour_jma_obs:wavg_temperature_c
  - ftr_period_actuals:lag_7d_demand_kwh
""")
w("demand", "lightgbm_msm", """
description: >-
  Plus the MSM forecast temperature at the representative station (demand/R-001).
base: lightgbm
add:
  - ftr_hour_msm:forecast_temperature_c
""")
w("demand", "lightgbm_msm_popw", """
description: >-
  Plus the same forecast population-weighted over the area's stations instead
  (demand/R-002).
base: lightgbm
add:
  - ftr_hour_msm:popw_forecast_temperature_c
""")
w("demand", "lightgbm_msm_popw_daytype", """
description: >-
  Plus the delivery day's type, categorical by the mart's tag (demand/R-003; the
  script default and the Kansai baseline).
base: lightgbm_msm_popw
add:
  - ftr_day_calendar:day_type
""")
w("demand", "lightgbm_msm_popw_daytype_simday", """
description: >-
  Plus rank 1 of the paper-style similar-day pool (demand/R-004 E-002, R-008): the
  Tokyo baseline until 2026-09-19. Tokyo-only until another TSO's でんき予報 hourly
  load is loaded and its weights fitted.
base: lightgbm_msm_popw_daytype
add:
  - ftr_period_similar_day:similar_day_rank1_demand_kwh
""")
w("demand", "lightgbm_msm_popw_daytype_simday_calendar", f"""
description: >-
  Plus the ten dim_date calendar attributes (demand/R-005 E-001, rejected; a
  reference).
base: lightgbm_msm_popw_daytype_simday
add:
{cal(CALENDAR_TEN)}""")
w("demand", "lightgbm_msm_popw_daytype_simday_calendarcounts", f"""
description: >-
  Plus the six calendar counts alone (demand/R-005 E-004, rejected; a reference).
base: lightgbm_msm_popw_daytype_simday
add:
{cal(COUNTS)}""")
w("demand", "lightgbm_msm_popw_daytype_simday_holidaydegree", """
description: >-
  Plus the holiday degree alone (demand/R-005 E-002, rejected; a reference).
base: lightgbm_msm_popw_daytype_simday
add:
  - ftr_day_calendar:holiday_degree
""")
w("demand", "lightgbm_msm_popw_daytype_simday_holidaydistance", """
description: >-
  Plus the two holiday distances alone (demand/R-005 E-003, rejected; a reference).
base: lightgbm_msm_popw_daytype_simday
add:
  - ftr_day_calendar:days_since_holiday
  - ftr_day_calendar:days_until_holiday
""")
w("demand", "lightgbm_msm_popw_daytype_simday_lags", f"""
description: >-
  Plus the thirteen recent-load features (demand/R-006 E-001), kept tentatively on
  2026-09-19. The D-9 lag stays out: the mart carries it for the change.
base: lightgbm_msm_popw_daytype_simday
add:
{refs(RECENT)}""")
w("demand", "lightgbm_msm_popw_daytype_simday_lags_weather", """
description: >-
  Plus the three other MSM forecast elements, population-weighted like the
  forecast temperature the base already carries (demand/R-007 E-001). The Tokyo
  baseline since 2026-09-19.
base: lightgbm_msm_popw_daytype_simday_lags
add:
  - ftr_hour_msm:popw_forecast_relative_humidity_pct
  - ftr_hour_msm:popw_forecast_precipitation_mm
  - ftr_hour_msm:popw_forecast_solar_radiation_mjm2
""")
w("spot_price", "lightgbm", """
description: >-
  Calendar and the previous day's area price.
features:
  - ftr_day_calendar:month
  - ftr_day_calendar:day_of_week
  - ftr_period_jepx:lag_1d_price
""")
w("spot_price", "lightgbm_occto", """
description: >-
  Plus the three OCCTO 翌々日 daily forecast columns (spot_price/R-001 E-001), null
  before 2024-04-01.
base: lightgbm
add:
  - ftr_day_occto:max_demand_hour_ending
  - ftr_day_occto:max_demand_mw
  - ftr_day_occto:max_supply_capacity_mw
""")
print("written", len(list(Path("conf/presets").rglob("*.yaml"))))
EOF
```
Expected: `written 13`.

- [ ] **Step 2: Prove the files equal the Python registries, one-off, before those go**

Run:
```bash
uv run python - <<'EOF'
from power_market_analytics.features.presets import load_presets
from power_market_analytics.tasks.demand.presets import PRESETS as DEMAND
from power_market_analytics.tasks.spot_price.presets import PRESETS as SPOT
for task, registry in (("demand", DEMAND), ("spot_price", SPOT)):
    loaded = load_presets(task)
    assert list(loaded) == list(registry), (list(loaded), list(registry))
    for name, preset in registry.items():
        got = loaded[name]
        assert (got.features, got.base, got.task) == (preset.features, preset.base, preset.task), name
    print(task, len(loaded), "presets identical")
EOF
```
Expected: `demand 11 presets identical` and `spot_price 2 presets identical`. Anything else is a typo in a file: fix the file, never the expectation.

- [ ] **Step 3: Write the pin tests**

Replace `tests/test_demand_presets.py` with:

```python
"""The demand presets: the eleven files of conf/presets/demand, pinned to the tuples of 2026-09-19."""

from __future__ import annotations

from power_market_analytics.features.presets import (
    categorical_columns,
    feature_dtypes,
    load_presets,
)
from tests.conftest import RECENT_LOAD_COLUMNS

PRESETS = load_presets("demand")

#: The base four, then the MSM temperature, the day type and the similar day.
SIMDAY = (
    "ftr_day_calendar:month",
    "ftr_day_calendar:day_of_week",
    "ftr_hour_jma_obs:wavg_temperature_c",
    "ftr_period_actuals:lag_7d_demand_kwh",
    "ftr_hour_msm:popw_forecast_temperature_c",
    "ftr_day_calendar:day_type",
    "ftr_period_similar_day:similar_day_rank1_demand_kwh",
)
CALENDAR_TEN = tuple(
    f"ftr_day_calendar:{c}"
    for c in (
        "half",
        "quarter",
        "day_of_month",
        "day_of_quarter",
        "day_of_year",
        "holiday_degree",
        "is_business_day",
        "fiscal_quarter",
        "days_since_holiday",
        "days_until_holiday",
    )
)
CALENDAR_COUNTS = tuple(
    f"ftr_day_calendar:{c}"
    for c in ("half", "quarter", "day_of_month", "day_of_quarter", "day_of_year", "fiscal_quarter")
)
RECENT_LOAD = (
    "ftr_period_actuals:lag_2d_demand_kwh",
    "ftr_period_actuals:lag_3d_demand_kwh",
    "ftr_period_actuals:lag_14d_demand_kwh",
    "ftr_period_actuals:lag_21d_demand_kwh",
    "ftr_period_actuals:lag_28d_demand_kwh",
    "ftr_period_actuals:mean_weekly_lags_demand_kwh",
    "ftr_period_actuals:ewm_weekly_lags_demand_kwh",
    "ftr_period_actuals:change_2d_9d_demand_kwh",
    "ftr_period_actuals:mean_daytype_4d_demand_kwh",
    "ftr_period_actuals:ewm_daytype_4d_demand_kwh",
    "ftr_day_actuals:lag_2d_mean_demand_kwh",
    "ftr_day_actuals:lag_2d_max_demand_kwh",
    "ftr_day_actuals:lag_2d_range_demand_kwh",
)
MSM_ELEMENTS = (
    "ftr_hour_msm:popw_forecast_relative_humidity_pct",
    "ftr_hour_msm:popw_forecast_precipitation_mm",
    "ftr_hour_msm:popw_forecast_solar_radiation_mjm2",
)
SIMDAY_NAME = "lightgbm_msm_popw_daytype_simday"
#: Every preset as tasks/demand/presets.py registered it on 2026-09-19: base, then features.
EXPECTED = {
    "lightgbm": (None, SIMDAY[:4]),
    "lightgbm_msm": ("lightgbm", (*SIMDAY[:4], "ftr_hour_msm:forecast_temperature_c")),
    "lightgbm_msm_popw": ("lightgbm", SIMDAY[:5]),
    "lightgbm_msm_popw_daytype": ("lightgbm_msm_popw", SIMDAY[:6]),
    SIMDAY_NAME: ("lightgbm_msm_popw_daytype", SIMDAY),
    f"{SIMDAY_NAME}_calendar": (SIMDAY_NAME, (*SIMDAY, *CALENDAR_TEN)),
    f"{SIMDAY_NAME}_calendarcounts": (SIMDAY_NAME, (*SIMDAY, *CALENDAR_COUNTS)),
    f"{SIMDAY_NAME}_holidaydegree": (SIMDAY_NAME, (*SIMDAY, "ftr_day_calendar:holiday_degree")),
    f"{SIMDAY_NAME}_holidaydistance": (
        SIMDAY_NAME,
        (*SIMDAY, "ftr_day_calendar:days_since_holiday", "ftr_day_calendar:days_until_holiday"),
    ),
    f"{SIMDAY_NAME}_lags": (SIMDAY_NAME, (*SIMDAY, *RECENT_LOAD)),
    f"{SIMDAY_NAME}_lags_weather": (f"{SIMDAY_NAME}_lags", (*SIMDAY, *RECENT_LOAD, *MSM_ELEMENTS)),
}


def test_the_eleven_files_resolve_to_the_tuples_registered_on_2026_09_19():
    # The migration pin: a rerun of any preset sees the same columns in the same
    # order as before the move to files, so no published run's feature set moved.
    assert {name: (p.base, p.features) for name, p in PRESETS.items()} == EXPECTED
    assert all(p.task == "demand" for p in PRESETS.values())
    assert PRESETS["lightgbm"].feature_cols == (
        "time_code",
        "month",
        "day_of_week",
        "wavg_temperature_c",
        "lag_7d_demand_kwh",
    )


def test_every_file_has_a_description():
    assert all(p.description for p in PRESETS.values())
    assert PRESETS[f"{SIMDAY_NAME}_lags_weather"].description.startswith("Plus the three other MSM")


def test_the_calendar_subsets_partition_the_ten_but_the_working_day_flag():
    subsets = (
        ("ftr_day_calendar:holiday_degree",),
        ("ftr_day_calendar:days_since_holiday", "ftr_day_calendar:days_until_holiday"),
        CALENDAR_COUNTS,
    )
    assert sum(len(s) for s in subsets) == len(CALENDAR_TEN) - 1
    assert set().union(*subsets) == set(CALENDAR_TEN) - {"ftr_day_calendar:is_business_day"}


def test_types_and_categoricals_come_from_the_views():
    assert feature_dtypes(PRESETS[SIMDAY_NAME]) == {
        "month": "int64",
        "day_of_week": "int64",
        "wavg_temperature_c": "float64",
        "lag_7d_demand_kwh": "int64",
        "popw_forecast_temperature_c": "float64",
        "day_type": "int64",
        "similar_day_rank1_demand_kwh": "float64",
    }
    assert feature_dtypes(PRESETS["lightgbm_msm"])["forecast_temperature_c"] == "float64"
    calendar = feature_dtypes(PRESETS[f"{SIMDAY_NAME}_calendar"])
    assert calendar["holiday_degree"] == "float64"
    assert all(calendar[c.split(":")[1]] == "int64" for c in CALENDAR_TEN if "holiday_degree" not in c)
    assert all(
        categorical_columns(preset) == (("day_type",) if "daytype" in name else ())
        for name, preset in PRESETS.items()
    )


def test_the_lags_preset_appends_the_thirteen_recent_load_features():
    lags = PRESETS[f"{SIMDAY_NAME}_lags"]
    assert lags.columns == (*(r.split(":")[1] for r in SIMDAY), *RECENT_LOAD_COLUMNS)
    # The D-9 lag is a mart column for the change, not a feature of the preset.
    assert "ftr_period_actuals:lag_9d_demand_kwh" not in lags.features
    dtypes = feature_dtypes(lags)
    assert {c: dtypes[c] for c in RECENT_LOAD_COLUMNS} == {
        "lag_2d_demand_kwh": "int64",
        "lag_3d_demand_kwh": "int64",
        "lag_14d_demand_kwh": "int64",
        "lag_21d_demand_kwh": "int64",
        "lag_28d_demand_kwh": "int64",
        "mean_weekly_lags_demand_kwh": "float64",
        "ewm_weekly_lags_demand_kwh": "float64",
        "change_2d_9d_demand_kwh": "int64",
        "mean_daytype_4d_demand_kwh": "float64",
        "ewm_daytype_4d_demand_kwh": "float64",
        "lag_2d_mean_demand_kwh": "float64",
        "lag_2d_max_demand_kwh": "int64",
        "lag_2d_range_demand_kwh": "int64",
    }
    weather = PRESETS[f"{SIMDAY_NAME}_lags_weather"]
    assert len(weather.features) == len(lags.features) + 3
    assert all(feature_dtypes(weather)[c.split(":")[1]] == "float64" for c in MSM_ELEMENTS)
```

Create `tests/test_spot_price_presets.py`:

```python
"""The spot-price presets: the two files of conf/presets/spot_price, pinned to the tuples of 2026-09-19."""

from __future__ import annotations

from power_market_analytics.features.presets import (
    PRESETS_DIR,
    feature_dtypes,
    load_presets,
    preset_tasks,
)

PRESETS = load_presets("spot_price")
BASE = ("ftr_day_calendar:month", "ftr_day_calendar:day_of_week", "ftr_period_jepx:lag_1d_price")
OCCTO = (
    "ftr_day_occto:max_demand_hour_ending",
    "ftr_day_occto:max_demand_mw",
    "ftr_day_occto:max_supply_capacity_mw",
)


def test_the_two_files_resolve_to_the_tuples_registered_on_2026_09_19():
    assert {name: (p.base, p.features) for name, p in PRESETS.items()} == {
        "lightgbm": (None, BASE),
        "lightgbm_occto": ("lightgbm", (*BASE, *OCCTO)),
    }
    assert all(p.task == "spot_price" and p.description for p in PRESETS.values())


def test_every_preset_file_of_every_task_resolves_against_the_views():
    # A typo in a file fails here, not at the first backtest.
    for task in preset_tasks():
        for name, preset in load_presets(task).items():
            dtypes = feature_dtypes(preset)
            assert list(dtypes) == list(preset.columns), f"{task}/{name}"
    assert sorted(p.name for p in PRESETS_DIR.iterdir()) == ["demand", "spot_price"]
```

- [ ] **Step 4: Run the new tests and the whole preset test file**

Run: `uv run pytest tests/test_demand_presets.py tests/test_spot_price_presets.py tests/test_features_presets.py -q`
Expected: all pass, including `test_the_repository_directories_are_the_tasks` from Task 3.

- [ ] **Step 5: Commit**

```bash
git add conf/presets tests/test_demand_presets.py tests/test_spot_price_presets.py
git commit -q -m "feat(forecasting): the thirteen presets as files under conf/presets

Each file resolves to the tuple tasks/<task>/presets.py registered on
2026-09-19, checked against the registries before they go and pinned in the
tests.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The registries read the files; the Python modules go

**Files:**
- Modify: `power_market_analytics/tasks/demand/strategies/__init__.py:26-29`
- Modify: `power_market_analytics/tasks/spot_price/strategies/__init__.py:27-35`
- Modify: `power_market_analytics/features/catalogue.py`
- Delete: `power_market_analytics/tasks/demand/presets.py`, `power_market_analytics/tasks/spot_price/presets.py`
- Modify: `tests/test_features_presets.py:24`, `tests/test_demand_strategies.py:10`, `tests/test_spot_price_strategies.py:10`, `tests/test_forecasting_preset_lgbm.py:35`

**Interfaces:**
- Consumes: `load_presets`, `preset_tasks`.
- Produces: `PRESETS` in each `strategies` package as before, now `load_presets(TASK.name)`; `feature_services()` over the task directories.

- [ ] **Step 1: Switch the imports in the tests first**

In `tests/test_features_presets.py` replace `from power_market_analytics.tasks.spot_price.presets import LIGHTGBM` with nothing, and after the import block add `LIGHTGBM = load_presets("spot_price")["lightgbm"]` (below `REGISTERED_SERVICES`). In `tests/test_demand_strategies.py` replace `from power_market_analytics.tasks.demand.presets import PRESETS` with `from power_market_analytics.features.presets import load_presets` and add `PRESETS = load_presets("demand")` after the imports. The same in `tests/test_spot_price_strategies.py` with `load_presets("spot_price")`. In `tests/test_forecasting_preset_lgbm.py` replace `from power_market_analytics.tasks.spot_price.presets import LIGHTGBM, LIGHTGBM_OCCTO` with two module-level lines after the imports:

```python
SPOT_PRESETS = load_presets("spot_price")
LIGHTGBM, LIGHTGBM_OCCTO = SPOT_PRESETS["lightgbm"], SPOT_PRESETS["lightgbm_occto"]
```

and add `load_presets` to that file's `from power_market_analytics.features.presets import Preset` line. Run `uv run pytest tests/test_features_presets.py tests/test_demand_strategies.py tests/test_spot_price_strategies.py tests/test_forecasting_preset_lgbm.py -q -x` and expect them to pass unchanged in count: the registries are still the Python ones, and the loaded presets equal them.

- [ ] **Step 2: Switch the registries and the catalogue, delete the modules**

`power_market_analytics/tasks/demand/strategies/__init__.py`: the docstring's first sentence becomes "A strategy name is a preset of ``conf/presets/demand/``, built as a …"; replace `from power_market_analytics.tasks.demand.presets import PRESETS` with `from power_market_analytics.features.presets import load_presets` (merge it into the existing `from power_market_analytics.features.presets import (…)` block, alphabetically) and replace

```python
#: Every strategy name the backtest script accepts: the presets.
STRATEGIES: tuple[str, ...] = tuple(PRESETS)
```
with
```python
#: Every preset of the task, read from its files at import.
PRESETS: dict[str, Preset] = load_presets(TASK.name)
#: Every strategy name the backtest script accepts: the presets.
STRATEGIES: tuple[str, ...] = tuple(PRESETS)
```
adding `Preset` to the same import block. The same in `power_market_analytics/tasks/spot_price/strategies/__init__.py` (docstring: "or a preset of ``conf/presets/spot_price/``"), keeping `STRATEGIES = (*NAVE_STRATEGIES, *PRESETS)` as it is.

`power_market_analytics/features/catalogue.py` becomes:

```python
"""Every task's presets as Feast feature services, for the registry and the UI."""

from __future__ import annotations

from feast import FeatureService

from power_market_analytics.features.presets import feature_service, load_presets, preset_tasks


def feature_services() -> list[FeatureService]:
    """The feature services of every preset file, by task then name.

    Returns
    -------
    list of feast.FeatureService
    """
    return [
        feature_service(preset)
        for task in preset_tasks()
        for preset in load_presets(task).values()
    ]
```

Then:
```bash
git rm -q power_market_analytics/tasks/demand/presets.py power_market_analytics/tasks/spot_price/presets.py
grep -rn "tasks.demand.presets\|tasks.spot_price.presets\|tasks/demand/presets\|tasks/spot_price/presets" power_market_analytics scripts tests || echo "no reference left"
```
Expected: `no reference left`.

- [ ] **Step 3: The full suite, lint and types**

Run: `just test -q 2>&1 | tail -4 && just lint && just mypy`
Expected: every test passes with `TOTAL … 100%`; ruff and mypy clean. A coverage miss in `presets.py` means a loader branch no test reaches: add the parametrised case rather than a pragma.

- [ ] **Step 4: Commit**

```bash
git add -A power_market_analytics tests
git commit -q -m "feat(forecasting): the registries and the Feast catalogue read the preset files

tasks/<task>/presets.py are deleted; PRESETS is load_presets(TASK.name) and
feature_services() walks conf/presets/, so the catalogue no longer imports the
tasks.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the spot and demand backtest bullets, the Demand task bullet's tuple names)

- [ ] **Step 1: Apply the edits with short anchors**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("CLAUDE.md")
s = p.read_text()
pairs = [
    ("(`tasks/spot_price/presets.py`: a named list of `<view>:<column>` references into the\n  Feast feature views;",
     "(a YAML file under `conf/presets/spot_price/` since 2026-09-19, `features.presets.load_presets`:\n"
     "  `description` and either `features`, the full ordered list of `<view>:<column>` references into\n"
     "  the Feast feature views, or `base` plus `add` / `drop`; the name is the file's stem;"),
    ("A\n  new preset = an entry in `PRESETS`;",
     "A\n  new preset = a new file under `conf/presets/<task>/` (a published preset's file is never edited);"),
    ("the eleven presets of `tasks/demand/presets.py`",
     "the eleven files under `conf/presets/demand/`"),
    ("every strategy is a **preset** (`tasks/demand/presets.py`,",
     "every strategy is a **preset** (a YAML file under `conf/presets/demand/` since 2026-09-19,"),
    ("(`DAY_CALENDAR_FEATURES` in\n  `presets.py`, the old join order)", "(the old join order)"),
    ("(`HOLIDAY_DEGREE_FEATURES` = `holiday_degree`;", "(`holiday_degree`;"),
    ("(`HOLIDAY_DISTANCE_FEATURES` =\n  `days_since_holiday`, `days_until_holiday`;", "(`days_since_holiday`, `days_until_holiday`;"),
    ("(`CALENDAR_COUNT_FEATURES` = `half`,", "(`half`,"),
    ("`RECENT_LOAD_FEATURES`, the thirteen columns", "the thirteen columns"),
    ("`MSM_ELEMENT_FEATURES`, the three `ftr_hour_msm` columns", "the three `ftr_hour_msm` columns"),
]
for old, new in pairs:
    assert s.count(old) == 1, old[:50]
    s = s.replace(old, new)
p.write_text(s)
print("edited", len(pairs))
EOF
grep -n "presets\.py\|_FEATURES\b" CLAUDE.md || echo "no module or tuple name left"
```
Expected: `edited 10`, then `no module or tuple name left`.

- [ ] **Step 2: Commit**

```bash
just docs-links
git add CLAUDE.md
git commit -q -m "docs(forecasting): CLAUDE.md names the preset files

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The pull request and the review loop

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feature/yaml-presets
gh pr create --title "feat(forecasting): presets as YAML files under conf/presets" --body "$(cat <<'EOF'
## Why

The researcher's decision of 2026-09-19 (spec `docs/superpowers/specs/2026-09-19-yaml-presets-design.md`): a preset should be a file that reads easily and that an experiment issue can name, not a Python constant.

## What

- `conf/presets/<task>/<name>.yaml`: `description` and either `features` (the full ordered list) or `base` plus `add` / `drop`. Thirteen files, one per preset registered today, the rejected R-005 references included.
- `features.presets.load_presets(task)` reads a task's directory into the same `Preset` objects, resolving base chains through `with_changes`; every format rule is a `ValueError` naming the file. `Preset.description` is new; the Feast service's description is the file's.
- The two `strategies` registries call the loader at import; `features/catalogue.py` walks the task directories, so it no longer imports the tasks. `tasks/<task>/presets.py` are deleted.
- Nothing downstream moves: `build_strategy`, `--add` / `--drop` / `--name`, the MLflow params, the Feast service names, the warehouse and dashboards.

## Proof

- Before the Python registries were deleted, every file's resolved features, base and task compared equal to the registry's (`demand 11 presets identical`, `spot_price 2 presets identical`), and the tests pin all thirteen tuples as registered on 2026-09-19: LightGBM sees the same columns in the same order, so no run's feature set moved. No backtest run.
- `just test` at 100 % coverage, `just lint`, `just mypy`, `just docs-links`; CI on this PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
PR=$(gh pr view --json number --jq .number)
gh pr edit "$PR" --add-assignee hankehly --add-label enhancement --add-label forecasting
```

- [ ] **Step 2: The review loop**

Follow CLAUDE.md *Code review (pull requests)*: a main-session background poll every 60 s (`tmp/research-migration/watch_codex.sh <PR> <push time>` in the main checkout does this) until Codex posts a review or 👍; fix or rebut each finding in its thread, resolve, push, repeat until a round is clean; CI green on the current head. When PR #182 merges first, merge `main` into this branch (never rebase), resolve the CLAUDE.md neighbours by keeping both edits, push, and run the loop again.

- [ ] **Step 3: Report**

The PR URL, the proof lines, and that the researcher merges.
