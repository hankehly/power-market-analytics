# Research on GitHub Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the research ledger from `docs/research/` into GitHub issues of four kinds, migrate the 26 existing records, record the three pending decisions, and rewrite the docs and CLAUDE.md to the family-batch rules.

**Architecture:** GitHub issues become the ledger (labels `observation`, `investigation`, `feature candidate`, `experiment`; the Load Forecasting Project gains a `Family` field). A one-off Python script in the gitignored `tmp/` parses the existing Markdown records, allocates issue numbers first, then writes bodies with every cross-link resolved, links experiments to their investigations as sub-issues, sets Project fields and closes what is decided. The repo keeps the durable docs (scope defaults, papers, assets, a README describing the ledger) and gains three Markdown issue templates.

**Tech Stack:** `gh` CLI (issues, labels, projects, GraphQL), Python 3 standard library for the migration script, `gh aw` v0.88.7 to recompile the agentic workflow, `just docs-links` / `just zizmor` / `just checkov` as the gates.

**Spec:** `docs/superpowers/specs/2026-09-19-research-on-issues-design.md`

## Global Constraints

- Branch `chore/research-on-issues` (renamed from `feature/research-on-issues` in Task 1: a docs-and-config PR is `chore/` ↔ `docs` / `chore`, CLAUDE.md *Git conventions*). Commit messages `docs(research): …` or `chore(research): …`, imperative, lowercase, each ending with the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer.
- Never write the Codex mention (the bot's handle followed by "review") in a PR body, commit message or file.
- Writing style (CLAUDE.md *Writing style*): short sentences, one idea each, everyday words; a decision is one to three lines.
- Repository: `hankehly/power-market-analytics`, public. Project: **Load Forecasting**, user project number `3`, id `PVT_kwHOALGbus4BjoLc`. Field ids and option ids as of 2026-09-19:
  - Status `PVTSSF_lAHOALGbus4BjoLczhibyqA`: Ready `83d3af7a`, Needs a decision `b263478d`, In progress `d6692d83`, Done `fc535fc7`
  - Task `PVTSSF_lAHOALGbus4BjoLczhibyyc`: demand `ef2b7dd5`, spot_price `f08ee609`
  - Decision `PVTSSF_lAHOALGbus4BjoLczhibyzs`: Supported `33a1a22a`, Not supported `5a372034`, Inconclusive `a6c38d74`, Superseded `d2a81ac1`, Set aside `c6c0846f`
  - Investigation (text, to delete) `PVTF_lAHOALGbus4BjoLczhibyzo`
- Migrated issue titles keep the old ID, task-qualified as the research README's rule for IDs outside their folder requires: `demand/O-002 — …`, `demand/R-006 — …`, `demand/R-006 E-001 — …`, `spot_price/R-001 — …`. New issues carry no prefix.
- Creating an issue is outward-facing and leaves a number behind if deleted. The 26 issues are created once (Task 5), after the dry run of Task 4 has been read, and never re-created; every phase of the script is idempotent so a failed phase is re-run, not restarted.
- The migration script and its outputs live under `tmp/research-migration/` (gitignored: `.gitignore` line 50 `tmp/`). Nothing under `tmp/` is committed; the script's full text is in this plan.
- `chatgpt-response.md` and `tmp/scratch.md` at the repo root are the researcher's untracked files. Do not add, move or delete them.
- Coverage, lint and mypy gates are untouched: this PR adds no Python under `power_market_analytics/` or `scripts/`.

---

### Task 1: Branch, spec status and the design-history row

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-research-on-issues-design.md:3-4`
- Modify: `docs/superpowers/README.md:16` (the table's first data row)

**Interfaces:**
- Produces: the branch name `chore/research-on-issues` every later commit lands on.

- [ ] **Step 1: Rename the branch and confirm the tree is clean**

Run:
```bash
cd /Users/hankehly/Projects/power-market-analytics
git branch --show-current
git branch -m feature/research-on-issues chore/research-on-issues
git branch --show-current
git status --short
```
Expected: the first line prints `feature/research-on-issues`, the second `chore/research-on-issues`, and `git status --short` prints nothing but the two untracked files `?? chatgpt-response.md` and `?? tmp/` (leave them).

- [ ] **Step 2: Mark the spec approved and fix its branch line**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("docs/superpowers/specs/2026-09-19-research-on-issues-design.md")
s = p.read_text()
old = "Date: 2026-09-19. Status: **draft**, for the researcher's review.\nBranch: `feature/research-on-issues`."
new = "Date: 2026-09-19. Status: **approved** on 2026-09-19; implemented on branch `chore/research-on-issues`\n(plan `docs/superpowers/plans/2026-09-19-research-on-issues.md`)."
assert s.count(old) == 1
p.write_text(s.replace(old, new))
print("ok")
EOF
```
Expected: `ok`.

- [ ] **Step 3: Add the design-history row**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("docs/superpowers/README.md")
s = p.read_text()
anchor = "| Date | Work | Spec | Plan |\n|---|---|---|---|\n"
row = ("| 2026-09-19 | Research on GitHub issues — the research ledger moved from `docs/research/` "
       "to issues of four kinds (observation, investigation, feature candidate, experiment), "
       "the 26 records migrated, family batches as one experiment | "
       "[spec](superpowers/specs/2026-09-19-research-on-issues-design.md) | "
       "[plan](superpowers/plans/2026-09-19-research-on-issues.md) |\n")
assert s.count(anchor) == 1
p.write_text(s.replace(anchor, anchor + row))
print("ok")
EOF
grep -n "2026-09-19" docs/superpowers/README.md
```
Expected: `ok`, then one line of the README naming both files.

- [ ] **Step 4: Check the links resolve and commit**

Run:
```bash
just docs-links
git add docs/superpowers/specs/2026-09-19-research-on-issues-design.md docs/superpowers/README.md
git commit -q -m "docs(research): approve the research-on-issues spec and index it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: `just docs-links` exits 0 with no broken link named (the plan file is already committed on this branch). One new commit.

---

### Task 2: Record the three tentative decisions in R-006, R-007 and R-008

**Files:**
- Modify: `docs/research/demand/R-006-recent-load-features.md:3-4,213,224-225,229,233-234`
- Modify: `docs/research/demand/R-007-forecast-weather-elements.md:3-4,208,225-229,240-241`
- Modify: `docs/research/demand/R-008-similar-day-top-k.md:3-4,275,297,301,305-306`

These three files are deleted in Task 7, after their content has become issues in Task 5. Editing them first keeps the decision in git history and makes the migrated issue carry it.

**Interfaces:**
- Produces: the sentence the migrated issues and the demand README rely on: R-006, R-007 and R-008 "kept, provisionally, 2026-09-19, researcher to confirm", the preset `lightgbm_msm_popw_daytype_simday_lags_weather` the Tokyo baseline from that date.

- [ ] **Step 1: Apply the edits with exact-match replacements**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path

ACTION = ("Run `lightgbm_msm_popw_daytype_simday_lags_weather` fresh on the current marts as the\n"
          "baseline of the next experiment.")

def edit(path, pairs):
    p = Path(path)
    s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:50])
        s = s.replace(old, new)
    p.write_text(s)
    print("edited", path)

edit("docs/research/demand/R-006-recent-load-features.md", [
    ("- **Status:** In progress\n- **Last updated:** 2026-09-12 (E-001 run; the researcher's decision pending)",
     "- **Status:** Supported\n- **Last updated:** 2026-09-19 (the researcher kept E-001, tentatively)"),
    ("**Decision:** Pending — the researcher's.",
     "**Decision:** Keep — provisionally, the researcher's on 2026-09-19, to confirm. Kept together\n"
     "with R-007 and R-008: `lightgbm_msm_popw_daytype_simday_lags_weather`, the preset that\n"
     "carries all three, is the Tokyo baseline from that date."),
    ("registered; the decision is the researcher's.",
     "registered. The researcher kept them on 2026-09-19, tentatively, together with R-007\nand R-008."),
    ("- The researcher's verdict.",
     "- Whether the tentative keep holds once the new baseline has a fresh matched run."),
    ("**Investigation status:** In progress  \n**Recommended action:** —  ",
     "**Investigation status:** Supported — provisionally, 2026-09-19; researcher to confirm  \n"
     f"**Recommended action:** {ACTION}  "),
])

edit("docs/research/demand/R-007-forecast-weather-elements.md", [
    ("- **Status:** In progress\n- **Last updated:** 2026-09-12 (E-001 run; the researcher's decision pending)",
     "- **Status:** Supported\n- **Last updated:** 2026-09-19 (the researcher kept E-001, tentatively)"),
    ("**Decision:** Pending the researcher's review.",
     "**Decision:** Keep — provisionally, the researcher's on 2026-09-19, to confirm. Kept together\n"
     "with R-006 and R-008: `lightgbm_msm_popw_daytype_simday_lags_weather`, the preset that\n"
     "carries all three, is the Tokyo baseline from that date."),
    ("the air-conditioning reasoning in the hypothesis, and which of the three\ncarries the gain is not yet known.",
     "the air-conditioning reasoning in the hypothesis, and which of the three\ncarries the gain is not yet known. "
     "The researcher kept the three on 2026-09-19,\ntentatively, together with R-006 and R-008."),
    ("- **Investigation status:** In progress\n- **Recommended action:** the researcher's decision on E-001",
     "- **Investigation status:** Supported — provisionally, 2026-09-19; researcher to confirm\n"
     f"- **Recommended action:** {ACTION}"),
])

edit("docs/research/demand/R-008-similar-day-top-k.md", [
    ("- **Status:** In progress\n- **Last updated:** 2026-09-15 (E-001 run; the researcher's decision pending)",
     "- **Status:** Supported\n- **Last updated:** 2026-09-19 (the researcher kept E-001, tentatively)"),
    ("**Decision:** Pending — the researcher's.",
     "**Decision:** Keep — provisionally, the researcher's on 2026-09-19, to confirm. Rank 1 of the\n"
     "paper's pool stays in the similar-day presets; kept together with R-006 and R-007."),
    ("The decision is the researcher's.",
     "The researcher kept the new feature on 2026-09-19, tentatively, together with\n"
     "R-006 and R-007; the scratch objects stay until the keep is confirmed."),
    ("- The researcher's verdict.",
     "- Whether the tentative keep holds once the new baseline has a fresh matched run."),
    ("**Investigation status:** In progress  \n**Recommended action:** —  ",
     "**Investigation status:** Supported — provisionally, 2026-09-19; researcher to confirm  \n"
     f"**Recommended action:** {ACTION}  "),
])
EOF
```
Expected: three `edited …` lines and no `AssertionError`. If an assertion fails, the file's text differs from this plan: open the named file at the quoted line, copy its exact text into `old`, and re-run.

- [ ] **Step 2: Verify nothing says pending any more, then commit**

Run:
```bash
grep -n "pending\|Pending" docs/research/demand/R-006-recent-load-features.md docs/research/demand/R-007-forecast-weather-elements.md docs/research/demand/R-008-similar-day-top-k.md
git add docs/research/demand/R-006-recent-load-features.md docs/research/demand/R-007-forecast-weather-elements.md docs/research/demand/R-008-similar-day-top-k.md
git commit -q -m "docs(demand): record the tentative keep of R-006, R-007 and R-008

The researcher's decision of 2026-09-19: all three kept, provisionally, to
confirm; lightgbm_msm_popw_daytype_simday_lags_weather is the Tokyo baseline.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: the grep prints nothing (exit 1 is fine), then one new commit.

---

### Task 3: Labels and Project fields

External state, no files. Everything here is idempotent: a label that exists makes `gh label create` fail with "already exists", which is the signal to skip.

**Interfaces:**
- Produces: labels `observation`, `investigation`, `experiment`, `feature candidate`; `tmp/research-migration/family.json` holding the Family field id and its option ids, which Task 4's script reads.

- [ ] **Step 1: Create the three labels and rename the fourth**

Run:
```bash
gh label create observation --color 006B75 --description "Research: something noteworthy seen in the data or a run, recorded before it is explained"
gh label create investigation --color D93F0B --description "Research: a question worth understanding, with the experiments that answer it"
gh label create experiment --color 7057FF --description "Research: one controlled comparison and its decision"
gh label edit "feature idea" --name "feature candidate" --description "Research: a possible model input, ranked in the Load Forecasting Project and tested in a family batch"
gh label list --limit 50 | grep -E "^(observation|investigation|experiment|feature candidate)\b"
gh issue list --label "feature candidate" --state all --limit 50 --json number --jq 'length'
```
Expected: four label lines, then `24` (the 21 open and 3 closed candidates carried over by the rename).

- [ ] **Step 2: Create the Family field and save its ids**

Run:
```bash
mkdir -p tmp/research-migration
gh project field-create 3 --owner hankehly --name Family --data-type SINGLE_SELECT \
  --single-select-options "recent load,load shape,forecast thermal,weather memory,reference days,calendar,renewables,external forecasts,MSM elements" \
  --format json --jq '.id'
gh project field-list 3 --owner hankehly --format json \
  --jq '.fields[] | select(.name == "Family") | {id: .id, options: ([.options[] | {(.name): .id}] | add)}' \
  > tmp/research-migration/family.json
cat tmp/research-migration/family.json
```
Expected: a field id starting `PVTSSF_`, then `family.json` with `id` and nine `options` entries.

- [ ] **Step 3: Delete the Investigation field**

It holds no value on any item (checked 2026-09-19: `gh project item-list 3 --owner hankehly --format json --jq '[.items[] | select(.investigation != null)]'` printed `[]`). Re-check, then delete.

Run:
```bash
gh project item-list 3 --owner hankehly --format json --limit 50 --jq '[.items[] | select(.investigation != null) | .content.number]'
gh project field-delete --id PVTF_lAHOALGbus4BjoLczhibyzo
gh project field-list 3 --owner hankehly --format json --jq '[.fields[].name]'
```
Expected: `[]`, then a deletion confirmation, then a field list without `Investigation` and with `Family`. If the first command prints numbers, copy each value into that issue's body under a *Status notes* line before deleting.

---

### Task 4: The migration script and its dry run

**Files:**
- Create: `tmp/research-migration/migrate_research.py` (gitignored; the full text is below)
- Outputs: `tmp/research-migration/manifest.json`, `tmp/research-migration/bodies/*.md`

**Interfaces:**
- Consumes: `tmp/research-migration/family.json` (Task 3).
- Produces: the `plan` / `render` / `create` / `apply` subcommands Task 5 runs; `numbers.json` (key → issue number) that Tasks 7 and 8 read.

Record keys: `demand/O-001`, `demand/R-006`, `demand/R-006/E-001`, `spot_price/R-001`, and so on.

- [ ] **Step 1: Write the script**

Write `tmp/research-migration/migrate_research.py` with exactly this content:

```python
#!/usr/bin/env python3
"""Migrate docs/research/ records to GitHub issues. One-off; lives under tmp/ (gitignored).

    python3 tmp/research-migration/migrate_research.py plan            # parse -> manifest.json
    python3 tmp/research-migration/migrate_research.py render --fake   # bodies/ with fake numbers, to read
    python3 tmp/research-migration/migrate_research.py create          # stub issues -> numbers.json
    python3 tmp/research-migration/migrate_research.py render          # bodies/ with the real numbers
    python3 tmp/research-migration/migrate_research.py apply --bodies
    python3 tmp/research-migration/migrate_research.py apply --parents
    python3 tmp/research-migration/migrate_research.py apply --project
    python3 tmp/research-migration/migrate_research.py apply --close

Every phase can be re-run: create skips keys already in numbers.json, edits overwrite,
addSubIssue replaces the parent, item-add returns the existing item, close checks the state.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "hankehly/power-market-analytics"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOCS = ROOT / "docs" / "research"
BLOB = f"https://github.com/{REPO}/blob/main/docs/"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/docs/"
ISSUE = f"https://github.com/{REPO}/issues/"
OBSERVATION_SEARCH = f"https://github.com/{REPO}/issues?q=label%3Aobservation"
PROJECT_NUMBER = "3"
PROJECT_ID = "PVT_kwHOALGbus4BjoLc"
OWNER = "hankehly"
FIELDS = {
    "Status": ("PVTSSF_lAHOALGbus4BjoLczhibyqA", {"Ready": "83d3af7a", "Needs a decision": "b263478d",
                                                    "In progress": "d6692d83", "Done": "fc535fc7"}),
    "Task": ("PVTSSF_lAHOALGbus4BjoLczhibyyc", {"demand": "ef2b7dd5", "spot_price": "f08ee609"}),
    "Decision": ("PVTSSF_lAHOALGbus4BjoLczhibyzs", {"Supported": "33a1a22a", "Not supported": "5a372034",
                                                      "Inconclusive": "a6c38d74", "Superseded": "d2a81ac1",
                                                      "Set aside": "c6c0846f"}),
}
LABEL = {"observation": "observation", "investigation": "investigation", "experiment": "experiment"}

# --- what migrates, in creation order -------------------------------------------------------
# (key, closing comment with {key} placeholders for issue numbers, or None to stay open)
OBSERVATIONS = [
    ("demand/O-001", "Captured by {demand/R-003}, the day type as a categorical feature. "
                     "Closed as needing no attention; the observation stands."),
    ("demand/O-002", "Captured by {demand/R-004}: its E-002 ({demand/R-004/E-002}) was kept for these days. "
                     "Closed as needing no attention; the observation stands."),
    ("demand/O-003", "Captured by {demand/R-004}: its E-002 ({demand/R-004/E-002}) was kept for these days. "
                     "Closed as needing no attention; the observation stands."),
    ("spot_price/O-001", None),
]
# (key, file, decision or None to stay open)
INVESTIGATIONS = [
    ("demand/R-001", "R-001-forecast-temperature.md", "Supported"),
    ("demand/R-002", "R-002-population-weighted-temperature.md", "Supported"),
    ("demand/R-003", "R-003-day-type-feature.md", "Supported"),
    ("demand/R-004", "R-004-prior-year-load-lag.md", "Supported"),
    ("demand/R-005", "R-005-calendar-features.md", "Not supported"),
    ("demand/R-006", "R-006-recent-load-features.md", "Supported"),
    ("demand/R-007", "R-007-forecast-weather-elements.md", "Supported"),
    ("demand/R-008", "R-008-similar-day-top-k.md", "Supported"),
    ("spot_price/R-001", "R-001-supply-demand-tightness.md", None),
]
EXPERIMENT_DECISIONS = {
    "demand/R-001/E-001": "Supported", "demand/R-002/E-001": "Supported", "demand/R-003/E-001": "Supported",
    "demand/R-004/E-001": "Not supported", "demand/R-004/E-002": "Supported",
    "demand/R-005/E-001": "Not supported", "demand/R-005/E-002": "Not supported",
    "demand/R-005/E-003": "Not supported", "demand/R-005/E-004": "Not supported",
    "demand/R-006/E-001": "Supported", "demand/R-007/E-001": "Supported", "demand/R-008/E-001": "Supported",
    "spot_price/R-001/E-001": None,
}
INVESTIGATION_FAMILY = {
    "demand/R-001": "forecast thermal", "demand/R-002": "forecast thermal", "demand/R-003": "calendar",
    "demand/R-004": "reference days", "demand/R-005": "calendar", "demand/R-006": "recent load",
    "demand/R-007": "MSM elements", "demand/R-008": "reference days", "spot_price/R-001": "external forecasts",
}
CANDIDATE_FAMILIES = {
    146: "recent load", 147: "recent load", 148: "recent load", 149: "recent load", 154: "recent load",
    137: "load shape",
    132: "forecast thermal", 136: "forecast thermal", 152: "forecast thermal", 153: "forecast thermal",
    133: "weather memory", 150: "weather memory", 151: "weather memory",
    129: "reference days", 134: "reference days", 138: "reference days",
    131: "calendar", 135: "renewables", 139: "external forecasts", 140: "external forecasts",
    130: "MSM elements",
}

HEADING_O = re.compile(r"^## (O-\d{3}) — (.+)$")
HEADING_R = re.compile(r"^# (R-\d{3}) — (.+)$")
HEADING_E = re.compile(r"^## (E-\d{3}) — (.+)$")


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def load(name: str, default):
    p = HERE / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name: str, obj) -> None:
    (HERE / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# --- parsing --------------------------------------------------------------------------------
def parse_observations(task: str) -> list[dict]:
    text = (DOCS / task / "observations.md").read_text().split("\n<!--")[0]
    lines = text.splitlines()
    records, i = [], 0
    while i < len(lines):
        m = HEADING_O.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() != "---":
            j += 1
        records.append({"key": f"{task}/{m.group(1)}", "kind": "observation", "task": task,
                        "title": f"{task}/{m.group(1)} — {m.group(2).strip()}",
                        "body": "\n".join(lines[i + 1:j]).strip() + "\n"})
        i = j
    return records


def parse_investigation(task: str, filename: str) -> tuple[dict, list[dict]]:
    lines = (DOCS / task / filename).read_text().splitlines()
    m = HEADING_R.match(lines[0])
    assert m, filename
    key, title = f"{task}/{m.group(1)}", m.group(2).strip()
    e_starts = [i for i, line in enumerate(lines) if HEADING_E.match(line)]
    tail_start = next(i for i, line in enumerate(lines) if line.startswith("## Current conclusion"))
    assert e_starts and e_starts[-1] < tail_start, filename
    experiments = []
    for n, s in enumerate(e_starts):
        end = e_starts[n + 1] if n + 1 < len(e_starts) else tail_start
        em = HEADING_E.match(lines[s])
        section = lines[s + 1:end]
        while section and section[-1].strip() in ("", "---"):
            section.pop()
        experiments.append({"key": f"{key}/{em.group(1)}", "kind": "experiment", "task": task,
                            "parent": key, "eid": em.group(1), "etitle": em.group(2).strip(),
                            "title": f"{key} {em.group(1)} — {em.group(2).strip()}",
                            "section": "\n".join(section).strip() + "\n"})
    investigation = {"key": key, "kind": "investigation", "task": task, "title": f"{key} — {title}",
                     "head": "\n".join(lines[1:e_starts[0]]).strip(),
                     "tail": "\n".join(lines[tail_start:]).strip()}
    return investigation, experiments


def build_manifest() -> list[dict]:
    records: list[dict] = []
    obs = {r["key"]: r for task in ("demand", "spot_price") for r in parse_observations(task)}
    for key, comment in OBSERVATIONS:
        r = obs.pop(key)
        r.update(state="closed" if comment else "open", close_comment=comment, decision=None, family=None)
        records.append(r)
    assert not obs, f"observations without a row in OBSERVATIONS: {sorted(obs)}"
    experiments: list[dict] = []
    for key, filename, decision in INVESTIGATIONS:
        inv, exps = parse_investigation(key.split("/")[0], filename)
        assert inv["key"] == key, (inv["key"], key)
        inv.update(state="closed" if decision else "open", decision=decision,
                   family=INVESTIGATION_FAMILY[key], close_comment=None)
        records.append(inv)
        for e in exps:
            d = EXPERIMENT_DECISIONS[e["key"]]
            e.update(state="closed" if d else "open", decision=d, family=INVESTIGATION_FAMILY[key],
                     close_comment=None)
        experiments.extend(exps)
    assert {e["key"] for e in experiments} == set(EXPERIMENT_DECISIONS), "experiment keys differ"
    records.extend(experiments)
    return records


# --- bodies ----------------------------------------------------------------------------------
def rewrite(text: str, task: str, n: dict[str, int]) -> str:
    text = re.sub(r"\]\(research/(demand|spot_price)/(R-\d{3})-[a-z0-9-]+\.md(?:#[^)]*)?\)",
                  lambda m: f"]({ISSUE}{n[f'{m.group(1)}/{m.group(2)}']})", text)
    text = re.sub(r"\]\(research/(demand|spot_price)/observations\.md#(o-\d{3})[^)]*\)",
                  lambda m: f"]({ISSUE}{n[f'{m.group(1)}/{m.group(2).upper()}']})", text)
    text = re.sub(r"\]\(research/(demand|spot_price)/observations\.md\)", f"]({OBSERVATION_SEARCH})", text)
    text = re.sub(r"\]\(research/(demand|spot_price)/README\.md(#[^)]*)?\)",
                  lambda m: f"]({BLOB}research/{m.group(1)}/README.md{m.group(2) or ''})", text)
    text = re.sub(r"\]\(research/(demand|spot_price)/assets/README\.md\)",
                  lambda m: f"]({BLOB}research/{m.group(1)}/assets/README.md)", text)
    text = text.replace("](research/README.md)", f"]({BLOB}research/README.md)")
    text = text.replace("](research/papers.md)", f"]({BLOB}research/papers.md)")
    text = text.replace("](research/investigation-template.md)", f"]({BLOB}research/README.md)")
    text = re.sub(r"\]\(assets/([^)]+)\)", lambda m: f"]({RAW}research/{task}/assets/{m.group(1)})", text)
    text = re.sub(r"\]\(([A-Za-z0-9-]+\.md)(#[^)]*)?\)",
                  lambda m: f"]({BLOB}{m.group(1)}{m.group(2) or ''})", text)
    leftovers = re.findall(r"\]\((?!https?://|#)[^)]*\)", text)
    assert not leftovers, leftovers
    return text


def render_bodies(records: list[dict], n: dict[str, int]) -> dict[str, str]:
    by_key = {r["key"]: r for r in records}
    bodies: dict[str, str] = {}
    for r in records:
        if r["kind"] == "observation":
            body = r["body"]
        elif r["kind"] == "investigation":
            exps = [e for e in records if e["kind"] == "experiment" and e["parent"] == r["key"]]
            listing = "\n".join(f"- #{n[e['key']]} — {e['eid']} — {e['etitle']}" for e in exps)
            body = f"{r['head']}\n\n## Experiments\n\n{listing}\n\n{r['tail']}\n"
        else:
            parent = by_key[r["parent"]]
            body = f"Experiment {r['eid']} of #{n[r['parent']]} ({parent['title']}).\n\n{r['section']}"
        body = rewrite(body, r["task"], n)
        assert len(body) < 65000, (r["key"], len(body))
        bodies[r["key"]] = body
    return bodies


# --- phases ----------------------------------------------------------------------------------
def cmd_plan() -> None:
    records = build_manifest()
    save("manifest.json", records)
    kinds = {k: sum(r["kind"] == k for r in records) for k in LABEL}
    print(f"{len(records)} records: {kinds}")


def cmd_render(fake: bool) -> None:
    records = load("manifest.json", None)
    assert records, "run plan first"
    n = {r["key"]: 9000 + i for i, r in enumerate(records)} if fake else load("numbers.json", {})
    assert all(r["key"] in n for r in records), "run create first (or pass --fake)"
    out = HERE / "bodies"
    out.mkdir(exist_ok=True)
    for key, body in render_bodies(records, n).items():
        (out / (key.replace("/", "__") + ".md")).write_text(body)
    print(f"wrote {len(records)} bodies to {out}")


def cmd_create() -> None:
    records = load("manifest.json", None)
    numbers = load("numbers.json", {})
    for r in records:
        if r["key"] in numbers:
            continue
        stub = f"Migrating from `docs/research/{r['task']}/`; the body follows."
        url = run(["gh", "issue", "create", "--repo", REPO, "--title", r["title"],
                   "--label", LABEL[r["kind"]], "--body", stub]).strip().splitlines()[-1]
        numbers[r["key"]] = int(url.rsplit("/", 1)[1])
        save("numbers.json", numbers)
        print(r["key"], numbers[r["key"]])
    print(f"{len(numbers)} issues")


def node_id(number: int) -> str:
    return run(["gh", "issue", "view", str(number), "--repo", REPO, "--json", "id", "--jq", ".id"]).strip()


def cmd_apply(phase: str) -> None:
    records = load("manifest.json", None)
    n = load("numbers.json", None)
    assert records and n
    if phase == "--bodies":
        for r in records:
            path = HERE / "bodies" / (r["key"].replace("/", "__") + ".md")
            run(["gh", "issue", "edit", str(n[r["key"]]), "--repo", REPO, "--body-file", str(path)])
            print("body", r["key"], n[r["key"]])
    elif phase == "--parents":
        ids = {}
        for r in records:
            if r["kind"] != "experiment":
                continue
            for key in (r["parent"], r["key"]):
                ids.setdefault(key, node_id(n[key]))
            run(["gh", "api", "graphql", "-f",
                 "query=mutation($p: ID!, $c: ID!) { addSubIssue(input: {issueId: $p, subIssueId: $c, "
                 "replaceParent: true}) { issue { number } } }",
                 "-f", f"p={ids[r['parent']]}", "-f", f"c={ids[r['key']]}"])
            print("parent", r["key"], "->", r["parent"])
    elif phase == "--project":
        family = load("family.json", None)
        assert family, "family.json missing (Task 3)"
        fields = dict(FIELDS)
        fields["Family"] = (family["id"], family["options"])

        def set_field(item: str, name: str, option: str) -> None:
            fid, options = fields[name]
            run(["gh", "project", "item-edit", "--project-id", PROJECT_ID, "--id", item,
                 "--field-id", fid, "--single-select-option-id", options[option]])

        for r in records:
            item = run(["gh", "project", "item-add", PROJECT_NUMBER, "--owner", OWNER, "--url",
                        f"{ISSUE}{n[r['key']]}", "--format", "json", "--jq", ".id"]).strip()
            set_field(item, "Task", r["task"])
            set_field(item, "Status", "Done" if r["state"] == "closed" else "In progress")
            if r["decision"]:
                set_field(item, "Decision", r["decision"])
            if r["family"]:
                set_field(item, "Family", r["family"])
            print("project", r["key"], item)
        items = json.loads(run(["gh", "project", "item-list", PROJECT_NUMBER, "--owner", OWNER,
                                "--format", "json", "--limit", "100"]))["items"]
        by_number = {i["content"].get("number"): i["id"] for i in items if i.get("content")}
        for number, fam in CANDIDATE_FAMILIES.items():
            set_field(by_number[number], "Family", fam)
            print("family", number, fam)
    elif phase == "--close":
        for r in records:
            if r["state"] != "closed":
                continue
            state = run(["gh", "issue", "view", str(n[r["key"]]), "--repo", REPO, "--json", "state",
                         "--jq", ".state"]).strip()
            if state == "CLOSED":
                continue
            cmd = ["gh", "issue", "close", str(n[r["key"]]), "--repo", REPO, "--reason", "completed"]
            if r["close_comment"]:
                comment = re.sub(r"\{([^}]+)\}", lambda m: f"#{n[m.group(1)]}", r["close_comment"])
                cmd += ["--comment", comment]
            run(cmd)
            print("closed", r["key"], n[r["key"]])
    else:
        sys.exit(f"unknown phase {phase}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["plan"]:
        cmd_plan()
    elif args[:1] == ["render"]:
        cmd_render(fake="--fake" in args)
    elif args[:1] == ["create"]:
        cmd_create()
    elif args[:1] == ["apply"] and len(args) == 2:
        cmd_apply(args[1])
    else:
        sys.exit(__doc__)
```

- [ ] **Step 2: Parse the docs into the manifest**

Run:
```bash
python3 tmp/research-migration/migrate_research.py plan
python3 -c "
import json; m = json.load(open('tmp/research-migration/manifest.json'))
for r in m: print(r['kind'][:3], r['state'], (r['decision'] or '-').ljust(13), r['title'])
"
```
Expected: `26 records: {'observation': 4, 'investigation': 9, 'experiment': 13}`, then 26 lines; the titles read `demand/O-001 — Holidays dominate the worst days and are over-forecast` … `spot_price/R-001 E-001 — Add OCCTO maximum-demand and supply-capacity forecasts`, with 4 `open` rows (`spot_price/O-001`, `spot_price/R-001`, its E-001, and no other).

- [ ] **Step 3: Render with fake numbers and read three bodies**

Run:
```bash
python3 tmp/research-migration/migrate_research.py render --fake
ls tmp/research-migration/bodies | wc -l
sed -n '1,40p' tmp/research-migration/bodies/demand__R-006.md
sed -n '1,12p' tmp/research-migration/bodies/demand__R-006__E-001.md
grep -n "raw.githubusercontent\|issues/9" tmp/research-migration/bodies/demand__R-004__E-002.md | head -5
grep -c "" tmp/research-migration/bodies/demand__O-002.md
```
Expected: `26` files. The investigation body starts with the header list (`- **Status:** Supported` …), has a `## Experiments` section listing `- #90xx — E-001 — Add the thirteen recent-load features`, and ends with the `## Final disposition` block edited in Task 2. The experiment body's first line is `Experiment E-001 of #90xx (demand/R-006 — Recent load features).` followed by `### Why this experiment`. The R-004 E-002 body shows a `raw.githubusercontent.com/…/docs/research/demand/assets/R-004-E-002-mae-by-month.png` link and `issues/90xx` links where it named other R-docs. The observation body has its `- **Recorded:**` list at the top. If any `AssertionError` names leftover links, add the missing rule to `rewrite` and re-run.

- [ ] **Step 4: Check no relative link survives and the `---` separators are gone from experiment tails**

Run:
```bash
grep -l "](research/\|](assets/" tmp/research-migration/bodies/*.md || echo "no relative links"
for f in tmp/research-migration/bodies/*E-00*.md; do tail -1 "$f" | grep -q "^---$" && echo "trailing --- in $f"; done; echo "checked"
```
Expected: `no relative links`, then `checked` with no `trailing ---` line.

---

### Task 5: Create the 26 issues and apply everything

External state. Run each phase once, in this order; a phase that fails part-way is re-run as is.

**Interfaces:**
- Consumes: the script and `family.json`.
- Produces: `tmp/research-migration/numbers.json` — the key → issue number map that Tasks 7 and 8 read, and the migrated issues themselves.

- [ ] **Step 1: Allocate the numbers**

Run:
```bash
python3 tmp/research-migration/migrate_research.py create
python3 -c "import json; n = json.load(open('tmp/research-migration/numbers.json')); print(len(n)); print(min(n.values()), max(n.values()))"
```
Expected: 26 `key number` lines, then `26 issues`; the numbers are consecutive from the first one (pull requests share the counter, so a PR opened meanwhile leaves a gap, which is fine).

- [ ] **Step 2: Render the real bodies and write them**

Run:
```bash
python3 tmp/research-migration/migrate_research.py render
python3 tmp/research-migration/migrate_research.py apply --bodies
R006=$(python3 -c "import json; print(json.load(open('tmp/research-migration/numbers.json'))['demand/R-006'])")
gh issue view "$R006" --json title,labels,body --jq '.title, [.labels[].name], (.body | split("\n") | .[0:6])'
```
Expected: `wrote 26 bodies …`, 26 `body …` lines, then the R-006 issue's title, `["investigation"]`, and its first six body lines.

- [ ] **Step 3: Link the experiments to their investigations**

Run:
```bash
python3 tmp/research-migration/migrate_research.py apply --parents
R006=$(python3 -c "import json; print(json.load(open('tmp/research-migration/numbers.json'))['demand/R-006'])")
gh api graphql -f query="{ repository(owner: \"hankehly\", name: \"power-market-analytics\") { issue(number: $R006) { subIssues(first: 10) { totalCount nodes { number title } } } } }" --jq '.data.repository.issue.subIssues'
```
Expected: 13 `parent …` lines; the query shows `totalCount: 1` and the E-001 issue for R-006. Spot-check R-005 the same way: `totalCount: 4`.

- [ ] **Step 4: Project items and fields, then the closes**

Run:
```bash
python3 tmp/research-migration/migrate_research.py apply --project
python3 tmp/research-migration/migrate_research.py apply --close
gh project item-list 3 --owner hankehly --format json --limit 100 --jq '[.items[] | select(.content.number >= 155) | {n: .content.number, status: .status, task: .task, family: .family, decision: .decision}] | length, .[0:4]'
gh project item-list 3 --owner hankehly --format json --limit 100 --jq '[.items[] | select(.content.number <= 154 and .family != null)] | length'
gh issue list --label observation --state all --json number,state --jq 'length, ([.[] | select(.state == "OPEN")] | length)'
gh issue list --label investigation --state all --json number --jq 'length'
gh issue list --label experiment --state all --json number,state --jq 'length, ([.[] | select(.state == "OPEN")] | length)'
```
Expected: 26 `project …` lines and 21 `family …` lines, then 23 `closed …` lines: 3 observations, 8 investigations and 12 experiments close; `spot_price/O-001`, `spot_price/R-001` and its E-001 stay open. The Project shows 26 new items with Status `Done` or `In progress`, Task set and Family set; 21 candidates carry a Family; `label:observation` counts 4 with 1 open; `label:investigation` 9; `label:experiment` 13 with 1 open.

- [ ] **Step 5: Read one closed observation and one closed experiment on the web**

Run:
```bash
O002=$(python3 -c "import json; print(json.load(open('tmp/research-migration/numbers.json'))['demand/O-002'])")
gh issue view "$O002" --comments --json state,comments --jq '.state, (.comments[-1].body)'
gh issue view "$O002" --web
```
Expected: `CLOSED` and the closing comment naming `#<R-004>` and `#<R-004 E-002>`; in the browser the tables render, the links to other issues resolve, and the header list shows at the top. Close the browser tab and continue.

- [ ] **Step 6: Save the number table for the PR body**

Run:
```bash
python3 - <<'EOF'
import json
n = json.load(open("tmp/research-migration/numbers.json"))
m = json.load(open("tmp/research-migration/manifest.json"))
rows = ["| Record | Issue |", "|---|---|"]
for r in m:
    rows.append(f"| `{r['key']}` — {r['title'].split(' — ', 1)[1]} | #{n[r['key']]} |")
open("tmp/research-migration/table.md", "w").write("\n".join(rows) + "\n")
print("\n".join(rows))
EOF
```
Expected: a 26-row Markdown table printed and saved to `tmp/research-migration/table.md`.

---

### Task 6: The issue templates

**Files:**
- Create: `.github/ISSUE_TEMPLATE/observation.md`
- Create: `.github/ISSUE_TEMPLATE/investigation.md`
- Create: `.github/ISSUE_TEMPLATE/experiment.md`
- Modify: `.github/ISSUE_TEMPLATE/feature-idea.yml:1-3,45-49,157`

**Interfaces:**
- Produces: the four entries of the New issue chooser; the section headings the research README (Task 7) describes.

- [ ] **Step 1: Write the observation template**

Write `.github/ISSUE_TEMPLATE/observation.md`:

````markdown
---
name: Observation
about: Something noteworthy seen in the data or a forecast run, recorded before it is explained
title: ""
labels: [observation]
assignees: []
---

- **Recorded:** YYYY-MM-DD
- **Data period:** YYYY-MM-DD through YYYY-MM-DD
- **Preset:** `lightgbm_msm_popw_daytype_simday_lags_weather`
- **Area:** tokyo
- **MLflow run:** [`<run_id>`](http://localhost:5005/#/experiments/<id>/runs/<run_id>)

## Observation

<!-- What was seen, with the numbers as read or queried, and which. The behaviour, not the explanation. -->

## Researcher's reading

<!-- As the researcher supplied it. Leave this empty rather than add an explanation on their behalf. -->

## References

<!-- The MLflow run, the Superset chart (dashboard → chart name), a figure under docs/research/<task>/assets/ embedded by its raw URL on main. -->

## Related

<!-- Issues and papers (docs/research/papers.md). Closing this issue means it needs no attention, not that it stopped being true: say what captured it in the closing comment. -->
````

- [ ] **Step 2: Write the investigation template**

Write `.github/ISSUE_TEMPLATE/investigation.md`:

````markdown
---
name: Investigation
about: A forecasting question worth understanding, with the experiments that answer it
title: ""
labels: [investigation]
assignees: []
---

- **Created:** YYYY-MM-DD
- **Triggering observations:** #NNN, or none — a modeling idea
- **Related:** —

## Question

<!-- The coherent forecasting question this investigation should eventually answer. -->

## Motivation

<!-- The observations, prior results or domain reasoning that make it worth asking. A causal explanation is rationale, not a claim a predictive experiment will prove. -->

## Current predictive hypothesis

> We believe that [model or data change] will [improve the primary metric] because [evidence or reasoning].

## Scope and constraints

<!-- Start from the task README's scope defaults (docs/research/<task>/README.md) and list only what this question changes: target, cutoff, baseline, metric, segments, evaluation. -->

## Experiments

<!-- The experiment issues, oldest first. Make each a sub-issue of this one. -->

- #NNN —

## Current conclusion

<!-- What is believed after every experiment so far. Update it as evidence accumulates. -->

## Open questions

## Final disposition

- **Decision:** Supported / Not supported / Inconclusive / Superseded
- **Recommended action:**
- **Superseded by:** —
````

- [ ] **Step 3: Write the experiment template**

Write `.github/ISSUE_TEMPLATE/experiment.md`:

````markdown
---
name: Experiment
about: One controlled comparison — a preset or rule change against a baseline — and its decision
title: ""
labels: [experiment]
assignees: []
---

- **Investigation:** #NNN, or none
- **Feature candidates:** #NNN, #NNN, or none
- **Family:**

## Why this experiment

<!-- Why this is an informative and economical next test, and what earlier experiments it builds on. -->

## Hypothesis

<!-- The intervention, the expected out-of-sample result and the rationale. -->

## Baseline

- **Preset:** the baseline preset, by name (its file once the presets live under `conf/presets/<task>/`)
- **Run:** a fresh run of the baseline preset on the candidate's window, or an existing run id

## Change

<!-- The candidate preset, so the diff reads as its base, add and drop; or the one other change under test (a window, a rule). One change per experiment. -->

- **Preset:**

## Expected evidence

- Expected direction of the primary metric
- Expected behaviour across backtest windows and in the important segments
- The result that would make the hypothesis less plausible

## Decision rule

<!-- What justifies keeping, rejecting or refining: practical size, consistency across windows, the CI over days, the important segments. No arbitrary universal threshold. -->

## Execution

- **MLflow experiment:** demand
- **Baseline run:**
- **Candidate run:**
- **Shared settings:** `--start-date`, `--end-date` and `--train-start` pinned identically; the LightGBM settings are the strategy's and are not tuned per experiment
- **Pull request:**

## Results

| Metric | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| Overall MAE | | | | |
| Important segment MAE | | | | |
| Mean error / bias | | | | |

<!-- Only the figures that support the decision, from docs/research/<task>/assets/ by raw URL on main. -->

## Interpretation

<!-- What the evidence suggests, where the effect sits, the limits. A better forecast supports incremental predictive value, not causality. -->

## Decision

- **Decision:** Keep / Reject / Refine / Inconclusive — the reason and the date
- **Follows:** the candidates above close with this verdict; a kept batch's preset becomes the baseline

## Follow-up ideas
````

- [ ] **Step 4: Rename the feature form**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path(".github/ISSUE_TEMPLATE/feature-idea.yml")
s = p.read_text()
pairs = [
    ("name: Feature idea\ndescription: A candidate feature for a forecasting model, to be ranked in the backlog\nlabels: [feature idea]",
     "name: Feature candidate\ndescription: A possible model input, ranked in the Load Forecasting Project and tested in a family batch\nlabels: [feature candidate]"),
    ("        The observation, investigation or paper it follows from (demand/O-002,\n        demand/R-007, a papers.md row), or a dash.",
     "        The observation, investigation or paper it follows from (an issue number, or a\n        docs/research/papers.md row), or a dash."),
    ("        - label: Not among the set-aside ideas (closed feature-idea issues)",
     "        - label: Not among the set-aside ideas (closed feature-candidate issues)"),
]
for old, new in pairs:
    assert s.count(old) == 1, old[:40]
    s = s.replace(old, new)
p.write_text(s)
print("ok")
EOF
```
Expected: `ok`.

- [ ] **Step 5: Check every template parses, then commit**

Run:
```bash
uv run python - <<'EOF'
from pathlib import Path
import yaml
for p in sorted(Path(".github/ISSUE_TEMPLATE").glob("*.md")):
    front = p.read_text().split("---\n")[1]
    meta = yaml.safe_load(front)
    assert set(meta) == {"name", "about", "title", "labels", "assignees"}, (p, meta)
    print(p.name, meta["name"], meta["labels"])
form = yaml.safe_load(Path(".github/ISSUE_TEMPLATE/feature-idea.yml").read_text())
print("feature-idea.yml", form["name"], form["labels"])
EOF
git add .github/ISSUE_TEMPLATE/
git commit -q -m "chore(research): issue templates for observations, investigations and experiments

Three Markdown templates next to the Feature candidate form (renamed from
Feature idea, its label with it), so gh can open every kind of record.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: four lines naming `Observation ['observation']`, `Investigation ['investigation']`, `Experiment ['experiment']`, `Feature candidate ['feature candidate']`, then one commit.

---

### Task 7: The research README, the task READMEs and the deletions

**Files:**
- Modify: `docs/research/README.md` (rewritten whole)
- Modify: `docs/research/demand/README.md` (the intro list, the *Baseline* paragraph, everything from `## Backlog` on)
- Modify: `docs/research/spot_price/README.md` (the intro list, everything from `## Backlog` on)
- Delete: `docs/research/demand/observations.md`, `docs/research/spot_price/observations.md`, `docs/research/demand/R-001-forecast-temperature.md` … `R-008-similar-day-top-k.md`, `docs/research/spot_price/R-001-supply-demand-tightness.md`, `docs/research/investigation-template.md`

**Interfaces:**
- Consumes: `tmp/research-migration/numbers.json` (Task 5) for the issue numbers the demand README names.

- [ ] **Step 1: Rewrite the research README**

Write `docs/research/README.md`:

````markdown
# Forecasting Research

The research ledger is the repository's GitHub issues. This page says what
lives where, what the four kinds of record are, and how to open one. MLflow
stays the source of truth for runs: parameters, metrics, code versions and
artifacts.

## Where things live

| What | Where |
|---|---|
| Observations, investigations, feature candidates, experiments | [Issues](https://github.com/hankehly/power-market-analytics/issues), one label per kind, ranked in the [Load Forecasting](https://github.com/users/hankehly/projects/3) Project |
| Each task's scope defaults and the tooling that reports segments | [`demand/README.md`](research/demand/README.md), [`spot_price/README.md`](research/spot_price/README.md) |
| Papers the research cites | [`papers.md`](research/papers.md), links only |
| Figures an issue embeds | `docs/research/<task>/assets/`, named `<issue number>-<slug>.png`, embedded by their raw URL on `main` |
| The feature list a run used | its preset: the `feature_preset` and `feature_refs` params of the MLflow run |
| What a run did | MLflow (`just open mlflow`) |

Until 2026-09-19 the records were files under `docs/research/<task>/`:
`O-XXX` observations, `R-XXX` investigations with their `E-XXX` experiments.
They were migrated to issues whose titles keep the ID, task-qualified
(`demand/R-006 — Recent load features`, `demand/R-006 E-001 — …`), so a
reference elsewhere still finds its record by searching the title.

## The four kinds of record

| Kind | Label | The record | Closes when |
|---|---|---|---|
| Observation | `observation` | Something noteworthy happened in the data or a run. | It needs no attention: the closing comment says what captured it. Closed does not mean no longer true. |
| Investigation | `investigation` | A question worth understanding. | Its final disposition is written. |
| Feature candidate | `feature candidate` | A possible model input worth remembering. | An experiment's decision covers it, or it is set aside. |
| Experiment | `experiment` | One controlled comparison and its decision. | Its decision is written. |

They are independent records, not stages. Common links: an observation leads
to an investigation or straight to an experiment; an investigation produces
candidates or experiments; candidates feed an experiment; an experiment
follows another. No link is required. Do not create an intermediate issue
solely to complete a workflow: "does adding these four features lower MAE" is
an experiment, not an investigation. An investigation exists when the question
itself is worth keeping. An experiment that belongs to an investigation is
made a sub-issue of it.

## Opening one

The **New issue** chooser offers the four templates. From the command line,
`gh issue create --template Observation` (or `Investigation`, `Experiment`)
opens the Markdown template. A feature candidate is an issue form, filled on
the web; `gh` cannot fill a form, so a candidate opened from the command line
is a Markdown body under the form's headings with `--label "feature candidate"`.

Titles are plain: what was seen, asked, proposed or tested. No prefix; the
issue number is the ID.

Record only observations and ideas the researcher supplied or that the cited
evidence establishes directly. Do not add explanations or hypotheses on the
researcher's behalf unless asked. An idea the researcher did not supply says
who suggested it and when. Writing style: `CLAUDE.md`, *Writing style*.

## The Project

Every issue of the four kinds is an item of the Load Forecasting Project. Its
fields:

- **Status**: `Ready`, `Needs a decision`, `In progress`, `Done`. `Done` is set
  on close by the built-in workflow, so it is the pipeline, not the verdict.
- **Task**: `demand` or `spot_price`.
- **Family**: the signal a candidate or a batch belongs to (`recent load`,
  `load shape`, `forecast thermal`, `weather memory`, `reference days`,
  `calendar`, `renewables`, `external forecasts`, `MSM elements`); the options
  are edited in the browser as families change.
- **Build**, **Impact**, **Feasibility**: candidates only. Impact and
  Feasibility run 1 to 3, 3 the highest; the ranking reads both, and there is
  no priority field.
- **Decision**: `Supported`, `Not supported`, `Inconclusive`, `Superseded`,
  `Set aside`, the verdict of an investigation, an experiment or a candidate.
  An experiment's body keeps the words Keep / Reject / Refine / Inconclusive;
  the field takes `Supported` for Keep, `Not supported` for Reject and
  `Inconclusive` for the other two. An observation has no Decision.

Auto-add copies nothing from an issue into the fields, so set Task, Family,
Build, Impact and Feasibility by hand when an item first comes up. Setting a
candidate aside: close it as not planned with the reason, Decision `Set aside`.

## Family batches

A candidate is a record, not a queue slot. When a family has enough candidates
worth a run, one experiment names them, a preset adds their columns to the
baseline preset, one matched run against a fresh baseline run on the same
window, one compare (`scripts/compare_<task>_runs.py`), one decision. The
decision closes each candidate with its verdict, and a kept batch's preset
becomes the baseline. Cheap column transforms go in together; a new source
gets its own experiment. Pruning is an experiment whose preset drops features.
The LightGBM settings are the strategy's and are never tuned per experiment.
Pin `--start-date`, `--end-date` and `--train-start` identically for a
candidate and its baseline.

## Assets

Store only the plots an issue's conclusion cites, under the task's `assets/`
folder, named by the issue: `assets/160-mae-by-month.png`. Keep detailed run
artifacts in MLflow.
````

- [ ] **Step 2: Edit the demand README**

Run:
```bash
python3 - <<'EOF'
import json
from pathlib import Path
n = json.load(open("tmp/research-migration/numbers.json"))
issue = lambda key: f"https://github.com/hankehly/power-market-analytics/issues/{n[key]}"
p = Path("docs/research/demand/README.md")
s = p.read_text()

old_intro = ("- Observations: [observation log](research/demand/observations.md)\n"
             "- Investigations: `R-XXX-*.md` in this folder, indexed below\n"
             "- Plots cited in conclusions: [`assets/`](research/demand/assets/README.md)")
new_intro = ("- Records: GitHub issues, see [Research](#research) below\n"
             "- Plots cited in conclusions: [`assets/`](research/demand/assets/README.md)")
assert s.count(old_intro) == 1
s = s.replace(old_intro, new_intro)

start = s.index("**Baseline.**")
end = s.index("for a candidate and its baseline.\n", start) + len("for a candidate and its baseline.\n")
new_baseline = f"""**Baseline.** A strategy run in the `demand` MLflow experiment. The Tokyo
baseline is `lightgbm_msm_popw_daytype_simday_lags_weather` since 2026-09-19,
when the researcher kept [R-006]({issue('demand/R-006')}),
[R-007]({issue('demand/R-007')}) and [R-008]({issue('demand/R-008')})
together, tentatively. It runs for Tokyo only, because its similar-day mart
needs the でんき予報 hourly load and a fit of the weights
(`scripts/fit_similar_day.py`). No run of it is matched to the current marts:
its runs (`e6d6d4ef…`, 2026-09-12) predate the 2026-09-14 switch to rank 1 of
the paper-style similar-day pool, and R-008's `d019a370…` is
`lightgbm_msm_popw_daytype_simday` without the lag and weather features. So
the first experiment's baseline is a fresh run of the preset on the same window
as its candidate. `scripts/demand_backtest.py` keeps `lightgbm_msm_popw_daytype`
as its default and as the Kansai baseline ([R-003]({issue('demand/R-003')}),
2026-08-26). `lightgbm`, `lightgbm_msm` and `lightgbm_msm_popw` stay registered
as reference presets. Pin `--start-date`, `--end-date` and `--train-start`
identically for a candidate and its baseline.
"""
s = s[:start] + new_baseline + s[end:]

cut = s.index("## Backlog")
research = """## Research

The demand records are GitHub issues, ranked in the
[Load Forecasting](https://github.com/users/hankehly/projects/3) Project under
Task `demand`: [observations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aobservation),
[investigations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Ainvestigation),
[feature candidates](https://github.com/hankehly/power-market-analytics/issues?q=label%3A%22feature+candidate%22)
and [experiments](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aexperiment),
open and closed. How the ledger works, the kinds of record and the Project's
fields: the [research README](research/README.md). The records written before
2026-09-19 keep their `O-XXX` / `R-XXX` / `E-XXX` IDs in their titles
(`demand/R-006 — Recent load features`).
"""
s = s[:cut] + research
p.write_text(s)
print("ok")
EOF
tail -20 docs/research/demand/README.md
```
Expected: `ok`, then the file ends with the *Research* section and no *Backlog* or *Investigation index* remains.

- [ ] **Step 3: Edit the spot README**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("docs/research/spot_price/README.md")
s = p.read_text()
old_intro = ("- Observations: [observation log](research/spot_price/observations.md)\n"
             "- Investigations: `R-XXX-*.md` in this folder, indexed below\n"
             "- Plots cited in conclusions: [`assets/`](research/spot_price/assets/README.md)")
new_intro = ("- Records: GitHub issues, see [Research](#research) below\n"
             "- Plots cited in conclusions: [`assets/`](research/spot_price/assets/README.md)")
assert s.count(old_intro) == 1
s = s.replace(old_intro, new_intro)
cut = s.index("## Backlog")
research = """## Research

The spot-price records are GitHub issues, ranked in the same
[Load Forecasting](https://github.com/users/hankehly/projects/3) Project as the
demand task's under Task `spot_price`, until the spot task has a Project of its
own: [observations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aobservation),
[investigations](https://github.com/hankehly/power-market-analytics/issues?q=label%3Ainvestigation),
[feature candidates](https://github.com/hankehly/power-market-analytics/issues?q=label%3A%22feature+candidate%22)
and [experiments](https://github.com/hankehly/power-market-analytics/issues?q=label%3Aexperiment),
open and closed. How the ledger works: the [research README](research/README.md).
The records written before 2026-09-19 keep their IDs in their titles
(`spot_price/R-001 — Supply and demand tightness signals`).
"""
p.write_text(s[:cut] + research)
print("ok")
EOF
tail -14 docs/research/spot_price/README.md
```
Expected: `ok`, then the *Research* section at the end of the file.

- [ ] **Step 4: Delete the migrated files and check the links**

Run:
```bash
git rm -q docs/research/demand/observations.md docs/research/spot_price/observations.md \
  docs/research/demand/R-00[1-8]-*.md docs/research/spot_price/R-001-supply-demand-tightness.md \
  docs/research/investigation-template.md
ls docs/research docs/research/demand docs/research/spot_price
just docs-links
```
Expected: the listing shows `README.md papers.md demand spot_price` and, per task, `README.md assets`. `just docs-links` fails here, naming the links from `docs/README.md`, `docs/_sidebar.md`, `docs/Forecast-Analysis.md`, `docs/TEPCO-Power-Usage-Retrieval.md`, `docs/Kansai-Power-Usage-Retrieval.md` and possibly plan files under `docs/superpowers/plans/` to the deleted files. Task 8 fixes them; note every path it names.

- [ ] **Step 5: Commit**

Run:
```bash
git add docs/research/
git commit -q -m "docs(research): the ledger is GitHub issues; the records move out of the repo

The research README describes the four kinds of record, the Project and the
family-batch rule; the task READMEs keep their scope defaults and point at the
issues; the observation logs, the nine investigations and the template are
deleted after their migration.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: one commit; `git status --short` shows nothing tracked.

---

### Task 8: The other docs that linked the research files

**Files:**
- Modify: `docs/README.md:17-18`
- Modify: `docs/_sidebar.md:16-33`
- Modify: `docs/Forecast-Analysis.md:99-101`
- Modify: `docs/TEPCO-Power-Usage-Retrieval.md:25`
- Modify: `docs/Kansai-Power-Usage-Retrieval.md:21`
- Modify: whatever else `just docs-links` named in Task 7 Step 4

- [ ] **Step 1: Apply the five known edits**

Run:
```bash
python3 - <<'EOF'
import json
from pathlib import Path
n = json.load(open("tmp/research-migration/numbers.json"))
r004 = f"https://github.com/hankehly/power-market-analytics/issues/{n['demand/R-004']}"

def edit(path, old, new):
    p = Path(path); s = p.read_text()
    assert s.count(old) == 1, (path, old[:50])
    p.write_text(s.replace(old, new)); print("edited", path)

edit("docs/README.md",
     "- [**Forecasting research**](research/README.md) — the observation log and the\n"
     "  investigations of each task, and the [papers](research/papers.md) they cite.",
     "- [**Forecasting research**](research/README.md) — how the research ledger on\n"
     "  GitHub issues works, each task's scope defaults, and the\n"
     "  [papers](research/papers.md) the research cites.")

sidebar = Path("docs/_sidebar.md"); s = sidebar.read_text()
start = s.index("  - [Research index](research/README.md)\n")
end = s.index("- [Development and code review](Development.md)\n")
new = ("  - [Research](research/README.md)\n"
       "  - [Papers](research/papers.md)\n"
       "  - [Demand — scope defaults](research/demand/README.md)\n"
       "  - [Spot price — scope defaults](research/spot_price/README.md)\n")
sidebar.write_text(s[:start] + new + s[end:]); print("edited docs/_sidebar.md")

edit("docs/Forecast-Analysis.md",
     "Experiments are written up under\n"
     "[`research/spot_price/`](research/spot_price/README.md), with conventions in\n"
     "[`research/`](research/README.md).",
     "Experiments are recorded as GitHub issues\n"
     "(the [research README](research/README.md)); the task's scope defaults are in\n"
     "[`research/spot_price/`](research/spot_price/README.md).")

edit("docs/TEPCO-Power-Usage-Retrieval.md",
     "([demand/R-004](research/demand/R-004-prior-year-load-lag.md), Not supported —",
     f"([demand/R-004]({r004}) E-001, Not supported —")

edit("docs/Kansai-Power-Usage-Retrieval.md",
     "([demand/R-004](research/demand/R-004-prior-year-load-lag.md)\n  E-002, the Tokyo baseline;",
     f"([demand/R-004]({r004})\n  E-002, the Tokyo baseline until 2026-09-19;")
EOF
```
Expected: five `edited …` lines.

- [ ] **Step 2: Run the link check and fix what remains**

Run:
```bash
just docs-links
```
Expected: exit 0. If it names a link in a file under `docs/superpowers/plans/` or `docs/superpowers/specs/` (a plan that quoted an R-doc link outside a code fence), replace that link's target with the issue URL from `numbers.json` (`demand/R-004` → `https://github.com/hankehly/power-market-analytics/issues/<n>`; a task README target stays as it is, those files exist), leaving the link text unchanged, and re-run until it exits 0.

- [ ] **Step 3: Commit**

Run:
```bash
git add docs/
git commit -q -m "docs: point the front page, sidebar and retrieval docs at the research issues

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: one commit.

---

### Task 9: CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` — the *Forecasting Research* section (from `## Forecasting Research` to the line before `## dbt`), and the sentences elsewhere that say a decision is pending or name the old Tokyo baseline.

- [ ] **Step 1: Replace the section**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("CLAUDE.md")
s = p.read_text()
start = s.index("## Forecasting Research\n")
end = s.index("## dbt\n", start)
new = """## Forecasting Research

- The research ledger is GitHub issues, four kinds by label — `observation`, `investigation`,
  `feature candidate`, `experiment` — ranked in the user-level Project **Load Forecasting**
  (https://github.com/users/hankehly/projects/3, number 3, id `PVT_kwHOALGbus4BjoLc`; fields
  Status / Task / Family / Impact / Feasibility / Build / Decision). What each kind is, when it
  closes, the Project's fields and the family-batch rule:
  [docs/research/README.md](docs/research/README.md). The repo keeps each task's scope
  defaults (`docs/research/<task>/README.md`), the papers index (`docs/research/papers.md`)
  and the figures (`docs/research/<task>/assets/`, embedded in issues by raw URL on `main`).
  Since 2026-09-19; until then the records were `O-XXX` / `R-XXX` / `E-XXX` files under
  `docs/research/<task>/`, migrated as issues whose titles keep the ID, task-qualified
  (`demand/R-006 — Recent load features`), so those IDs elsewhere in this file still resolve
  by searching issue titles.
- The kinds are independent records, not stages: no link is required, and no issue is created
  solely to complete a workflow. An investigation exists only when the question itself is
  worth keeping; "does this batch lower MAE" is an experiment. An experiment that belongs to
  an investigation is its sub-issue (the migrated ones are). Closing an observation means it
  needs no attention, not that it stopped being true.
- Family batches: a candidate is a record, not a queue slot. One experiment tests the
  candidates of a family together — one preset adding their columns to the baseline preset,
  one matched run against a fresh baseline run on the same window, one compare, one decision —
  and its decision closes each candidate with the verdict; a kept batch's preset becomes the
  baseline. A new source gets its own experiment; pruning is an experiment whose preset drops.
- Do not generate hypotheses, explanations, or initial ideas for the research ledger unless the
  researcher explicitly asks; record the researcher's thinking faithfully. An idea the
  researcher did not supply says who suggested it and when (the first fifteen candidates,
  #129 to #143, are Claude's 2026-09-15 suggestions).
- Opening one: the New issue chooser (three Markdown templates and the Feature candidate form,
  `.github/ISSUE_TEMPLATE/`), or `gh issue create --template <Name>`; `gh` cannot fill the
  form, so a candidate opened from the CLI is a Markdown body under the form's headings with
  `--label "feature candidate"`. Titles are plain, no prefix. Auto-add sets Status only, so
  Task, Family, Build, Impact and Feasibility are set by hand when an item first comes up.
  Setting a candidate aside = close as not planned with the reason and Decision `Set aside`
  (Status is the pipeline only: the item-closed workflow sets `Done` on every close). The
  Project's views and built-in workflows have no API and are edited in the browser; `gh
  project` needs the `project` token scope (granted 2026-09-16).
- Decisions: the Project's Decision field (`Supported`, `Not supported`, `Inconclusive`,
  `Superseded`, `Set aside`) is the verdict of investigations, experiments and candidates; an
  experiment's body keeps Keep / Reject / Refine / Inconclusive (`Supported` for Keep,
  `Not supported` for Reject, `Inconclusive` for the other two). R-006, R-007 and R-008 were
  kept tentatively on 2026-09-19 ("Provisionally Keep, researcher to confirm"), which made
  `lightgbm_msm_popw_daytype_simday_lags_weather` the Tokyo baseline; no run of it is matched
  to the current marts, so the first experiment's baseline is a fresh run.
- Keep reasoning, interpretations and decisions in the issues; keep run-level parameters,
  metrics, code versions and detailed artifacts in MLflow. Asset files are named by the issue,
  `assets/<issue number>-<slug>.png`; links inside `docs/research/` stay docsify
  site-root-relative (`research/demand/README.md`).

"""
s = s[:start] + new + s[end:]
p.write_text(s)
print("section replaced")
EOF
```
Expected: `section replaced`.

- [ ] **Step 2: Update the pending-decision and old-baseline sentences**

Run:
```bash
grep -c "decision pending" CLAUDE.md
grep -c "Tokyo demand baseline, reference run" CLAUDE.md
python3 - <<'EOF'
import re
from pathlib import Path
p = Path("CLAUDE.md")
s = p.read_text()
s, a = re.subn(r"the researcher's\s+decision pending", "kept tentatively on 2026-09-19", s)
s, b = re.subn(r"Tokyo demand baseline, reference run", "Tokyo demand baseline until 2026-09-19, reference run", s)
p.write_text(s)
print("pending ->", a, "| baseline ->", b)
EOF
grep -n "kept tentatively on 2026-09-19\|baseline until 2026-09-19" CLAUDE.md | cut -c1-120
```
Expected: the two `grep -c` counts (about 5 and 2), the same two numbers from the script, and the rewritten lines listed. Read each rewritten line: the sentence must still read; if one does not (a stray "the" before "kept"), fix that line by hand.

- [ ] **Step 3: Commit**

Run:
```bash
just docs-links
git add CLAUDE.md
git commit -q -m "docs(research): CLAUDE.md follows the research ledger on issues

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: `docs-links` exit 0, one commit.

---

### Task 10: The weekly-research workflow reads the issues

**Files:**
- Modify: `.github/workflows/weekly-research.md:121-123`
- Regenerate: `.github/workflows/weekly-research.lock.yml` (by `gh aw compile`, never by hand)

- [ ] **Step 1: Edit the reading list**

Run:
```bash
python3 - <<'EOF'
from pathlib import Path
p = Path(".github/workflows/weekly-research.md")
s = p.read_text()
old = ("- `docs/research/README.md`, then `docs/research/demand/README.md` and\n"
       "  `docs/research/spot_price/README.md` — the investigations (`R-XXX`) run so far and their outcomes.\n"
       "- `docs/research/papers.md` — papers the research already cites. Do not report them again.\n")
new = ("- `docs/research/README.md`, then `docs/research/demand/README.md` and\n"
       "  `docs/research/spot_price/README.md` — how the research is recorded and each task's scope.\n"
       "- The repository's issues labelled `investigation` and `experiment`, open and closed — the\n"
       "  investigations run so far and their outcomes; and those labelled `feature candidate` — the\n"
       "  ideas already on the backlog. Do not propose one of them again.\n"
       "- `docs/research/papers.md` — papers the research already cites. Do not report them again.\n")
assert s.count(old) == 1
p.write_text(s.replace(old, new))
print("ok")
EOF
```
Expected: `ok`.

- [ ] **Step 2: Recompile and check the lock changed only where it should**

Run:
```bash
gh aw compile weekly-research
git diff --stat .github/workflows/
git diff .github/workflows/weekly-research.lock.yml | grep "^[-+]" | grep -v "^[-+][-+]" | cut -c1-120 | head -30
```
Expected: `weekly-research.md` and `weekly-research.lock.yml` changed; the lock diff is the `body_hash` in the metadata line and the prompt text lines that carry the reading list, nothing else. If the compiler version in the lock's first line changed, stop: the installed `gh aw` is not v0.88.7 and the lock must not be regenerated with another version — report it.

- [ ] **Step 3: Run the workflow gates and commit**

Run:
```bash
just zizmor
just checkov
git add .github/workflows/weekly-research.md .github/workflows/weekly-research.lock.yml
git commit -q -m "ci(research): the weekly research workflow reads the research issues

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline -1
```
Expected: both gates exit 0 (zizmor says the online audits are off without a token; that is fine locally), one commit.

---

### Task 11: The pull request and the review loop

**Interfaces:**
- Consumes: `tmp/research-migration/table.md` (Task 5 Step 6).

- [ ] **Step 1: Push and open the PR**

Run:
```bash
git push -u origin chore/research-on-issues
TABLE=$(cat tmp/research-migration/table.md)
gh pr create --title "docs(research): move the research ledger to GitHub issues" --body "$(cat <<EOF
## Why

The researcher's decision of 2026-09-19 (spec \`docs/superpowers/specs/2026-09-19-research-on-issues-design.md\`): run feature research as family batches, with the records where the feature candidates already are. One issue per feature plus a rule that each became its own investigation made every idea a separate experiment.

## What

- Four kinds of record as issue labels: \`observation\`, \`investigation\`, \`feature candidate\` (renamed from \`feature idea\`), \`experiment\`; three Markdown templates next to the renamed form.
- The 26 records under \`docs/research/\` migrated to issues (below), experiments as sub-issues of their investigation, Project fields set, decided ones closed. The three pending decisions recorded first: R-006, R-007 and R-008 kept tentatively, \`lightgbm_msm_popw_daytype_simday_lags_weather\` the Tokyo baseline.
- The Project gains a \`Family\` field (set on the 21 open candidates) and loses the \`Investigation\` text field.
- The repo keeps the research README (rewritten: what lives where, the four kinds, the Project, the family-batch rule), the task READMEs' scope defaults, \`papers.md\` and the assets. The observation logs, the nine investigations and the template are deleted.
- Front page, sidebar, Forecast-Analysis, the two retrieval docs, CLAUDE.md and the weekly-research workflow follow.

## Proof

- \`just docs-links\`, \`just zizmor\`, \`just checkov\` exit 0; CI green.
- \`label:observation\` lists 4 issues (1 open), \`label:investigation\` 9 (1 open), \`label:experiment\` 13 (1 open); every migrated issue is a Project item with Task, Status and Family; the 21 open candidates carry a Family.

## Migrated records

$TABLE

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
PR=$(gh pr view --json number --jq .number)
gh pr edit "$PR" --add-assignee hankehly --add-label documentation --add-label research
gh pr view "$PR" --json url,labels --jq '.url, [.labels[].name]'
```
Expected: the PR URL and `["documentation", "research"]`.

- [ ] **Step 2: Wait for Codex and CI, address every finding**

Follow CLAUDE.md *Code review (pull requests)*: poll the PR's reviews, reactions and comments every 60 s from a main-session background task with `gh api --method GET …`, with no timeout, until the bot posts a `Codex Review` or reacts 👍. Fix or rebut each finding in its thread, resolve the thread, push, and repeat until a round ends clean. Check `gh pr checks "$PR"` is green on the current head.

- [ ] **Step 3: Report**

Tell the researcher: the PR URL; the number table; that the Project's auto-add filter still names the old label and its views need the new labels and the Family field, both browser-only; that the first experiment's baseline is a fresh run of `lightgbm_msm_popw_daytype_simday_lags_weather`; and that the researcher merges.
