# Research on GitHub issues — design

Date: 2026-09-19. Status: **draft**, for the researcher's review.
Branch: `feature/research-on-issues`. Companion:
`docs/superpowers/specs/2026-09-19-yaml-presets-design.md` (the preset files an
experiment issue names).

## 1. Goal

Move the research ledger out of `docs/research/` and into GitHub issues, next
to the feature candidates that already live there, and change the backlog rule
so that one experiment tests a batch of candidates. After this change:

1. **Four issue types**, each a kind of record: observation, investigation,
   feature candidate, experiment. Issues are the searchable ledger of what was
   seen, asked, proposed and tested.
2. **The repo keeps the durable knowledge:** each task's scope defaults, the
   papers index, the figures, and a research README that says how the ledger
   works.
3. **The existing records migrate:** 4 observations, 9 investigations and 13
   experiments become issues that keep their old IDs in the title.
4. **The three pending decisions are recorded** and the Tokyo demand baseline
   moves to `lightgbm_msm_popw_daytype_simday_lags_weather`.
5. **A candidate is not an investigation.** An experiment tests several
   candidates of one family; its decision closes them.

Why (the researcher, 2026-09-16 to 19): Isao-san's approach, batches of
features tested together against the current best model and pruned afterwards,
against a backlog written as one issue per feature and a README rule that
turned each issue into its own investigation. The ChatGPT consultation of
2026-09-16/17 (share `6aadd169`) proposed the issue types; its follow-up of
2026-09-17 (`chatgpt-response.md`, untracked) argued for keeping them loose.

## 2. Decisions

The researcher's, 2026-09-19, unless marked as Claude's default.

1. **Four types, no more.** No data, engineering, bug or decision types. A
   choice that needs a decision is a `Needs a decision` Status on the issue
   that raised it.
2. **The types are independent records, not stages.** Common links are
   observation → investigation, observation → experiment, investigation →
   candidate, investigation → experiment, candidate → experiment, experiment →
   experiment. No link is required. Do not create an intermediate issue solely
   to complete a workflow. An investigation exists only when the question
   itself is worth keeping; "does adding these four features lower MAE" is an
   experiment, not an investigation.
3. **Migrate**, do not freeze. One place to search.
4. **R-006, R-007 and R-008 are kept, tentatively.** Recorded as
   "Provisionally Keep, researcher to confirm", the R-001 precedent, with
   Decision `Supported`. The Tokyo baseline becomes
   `lightgbm_msm_popw_daytype_simday_lags_weather`. The script default stays
   `lightgbm_msm_popw_daytype`, the preset that runs for Kansai.
5. **A family batch is one experiment.** The Project gets a `Family` field;
   an experiment lists the candidates it tests and its decision closes each
   one with the verdict.
6. **Markdown templates for the three new types** (Claude's default): `gh`
   cannot fill an issue form, and Claude opens most issues. The Feature idea
   form stays as it is, with its label renamed `feature candidate`. Loosening
   the form's checks is a separate decision.
7. **Issue numbers are the IDs.** New issues carry no `O-` / `R-` / `E-`
   prefix. Migrated issues keep the old ID in their title, so MLflow run
   notes, PR bodies and CLAUDE.md that say `demand/R-006` still resolve.
8. **One Decision vocabulary** on the Project field, for investigations,
   experiments and candidates: `Supported`, `Not supported`, `Inconclusive`,
   `Superseded`, `Set aside`. An experiment's body keeps the researcher's words,
   Keep / Reject / Refine / Inconclusive, and the field takes `Supported` for
   Keep, `Not supported` for Reject and `Inconclusive` for the other two. An
   observation has no Decision.
9. **Closing an observation means it needs no attention**, not that it
   stopped being true. It closes with a comment naming what captured it, or
   stays open while the pattern is still watched.
10. **Sub-issues are optional.** The migration makes each experiment a
    sub-issue of its investigation, because every R-doc was one. A new
    experiment gets a parent only when an investigation exists.
11. **A published preset's file is what an experiment names**, never a
    feature list copied into the issue (the companion spec).

## 3. The four types

| Type | Label | The record | Closes when |
|---|---|---|---|
| Observation | `observation` | Something noteworthy happened in the data or a run. | It needs no attention (decision 9). |
| Investigation | `investigation` | A question worth understanding. | Its final disposition is written. |
| Feature candidate | `feature candidate` | A possible input worth remembering. | An experiment's decision covers it, or it is set aside. |
| Experiment | `experiment` | A concrete comparison worth running. | Its decision is written. |

Titles are plain: what was seen, asked, proposed or tested. No prefix.

### 3.1 Observation (`.github/ISSUE_TEMPLATE/observation.md`)

The sections of today's observation log entries, unchanged in substance:

- **Recorded**, **Data period**, **Preset**, **Area**, **MLflow run** as a
  list at the top.
- **Observation**: what was seen, with the numbers as read or queried, and
  which.
- **Researcher's reading**: recorded as supplied. The rule stays: no
  explanation or hypothesis on the researcher's behalf unless asked.
- **References**: MLflow run, Superset chart, figure under `assets/`.
- **Related**: issues, papers.

### 3.2 Investigation (`.github/ISSUE_TEMPLATE/investigation.md`)

The investigation template's sections above its experiments, with the
experiments moved out to their own issues:

- **Question**, **Motivation**, **Current predictive hypothesis** (the "We
  believe that … because …" line).
- **Scope and constraints**: cite the task README's scope defaults and list
  only what this question changes.
- **Experiments**: the experiment issues, sub-issues when they were made so.
- **Current conclusion**, **Open questions**.
- **Final disposition**: Decision, recommended action, superseded by.

### 3.3 Experiment (`.github/ISSUE_TEMPLATE/experiment.md`)

The `E-XXX` section of the investigation template, made a record of its own:

- **Why this experiment**, **Hypothesis** (intervention, expected out-of-sample
  result, rationale).
- **Baseline**: the preset file and the baseline run.
- **Change**: the candidate preset file, so the diff reads as its `base`,
  `add` and `drop`; or the other change under test (a window, a rule). One
  change per experiment.
- **Feature candidates**: the issues this experiment tests, if any.
- **Expected evidence** and **Decision rule**, written before the run.
- **Execution**: MLflow experiment, baseline run, candidate runs, the PR; the
  settings both runs share (`--start-date`, `--end-date`, `--train-start`; the
  LightGBM settings are the strategy's and are never tuned per experiment).
- **Results**: the table (overall MAE, the important segment, bias; absolute
  and relative change) and the figures that support the decision.
- **Interpretation**, **Decision** (Keep / Reject / Refine / Inconclusive, as
  today, with the reason), **Follow-up ideas**.

### 3.4 Feature candidate (`.github/ISSUE_TEMPLATE/feature-idea.yml`)

The form as it is, with `name: Feature candidate` and `labels: [feature
candidate]`. Its `Follows from` field takes an issue number or a paper row.

## 4. Labels, Project and links

**Labels.** Create `observation`, `investigation` and `experiment` with a
one-line description each; rename `feature idea` to `feature candidate`
(`gh label edit`, which carries the 24 issues over). The `research` label
stays a PR label.

**The Load Forecasting Project** (user Project 3) holds all four types.

| Field | Change |
|---|---|
| Status | Unchanged: `Ready`, `Needs a decision`, `In progress`, `Done` (set on close by the built-in workflow). |
| Task | Unchanged. |
| Family | New single-select. Initial options, from the 2026-09-16 grouping: `recent load`, `load shape`, `forecast thermal`, `weather memory`, `reference days`, `calendar`, `renewables`, `external forecasts`, `MSM elements`. Edited in the browser as families change. Set on every candidate and on a family-batch experiment. |
| Build, Impact, Feasibility | Unchanged; candidates only. |
| Decision | Unchanged options, now the verdict of investigations and experiments too. |
| Investigation | Deleted. The parent link and the timeline's cross-references replace it. |

The auto-add workflow's filter has no API: the researcher extends it to the
four labels in the browser. The migrated issues are added with
`gh project item-add` and their fields set with `gh project item-edit`.

**Links.** A sub-issue when an experiment belongs to an investigation
(GraphQL `addSubIssue`; the Project's built-in Parent issue and Sub-issues
progress fields show it). Everything else is a body link: an experiment lists
its candidates and its baseline experiment, a candidate's *Status notes* names
the experiment that took it, an observation's *Related* names what followed.
GitHub's timeline shows every mention both ways.

**Statuses.** Today's four status lists collapse to three things: the issue
open or closed, the Project Status for the pipeline, the Decision for the
verdict.

## 5. Migration

Order: observations, then investigations, then experiments, so that each body
can link the numbers created before it. Then the sub-issue links, the Project
items and fields, the closes. Each body is the doc's Markdown with its links
rewritten: `O-` / `R-` / `E-` cross-links become issue numbers, figure paths
become raw URLs on `main` (the repo is public, so they render), links to the
task READMEs become blob URLs, MLflow links stay as they are.

| Record | Issue | State | Decision |
|---|---|---|---|
| demand O-001 Holidays dominate the worst days | observation | closed: R-003 captured it | — |
| demand O-002 The working day between 山の日 and お盆 | observation | closed: R-004 E-002 kept for these days | — |
| demand O-003 建国記念の日 under-forecast | observation | closed: same | — |
| spot O-001 Daytime MAE is higher | observation | open: R-001 is open | — |
| demand R-001 Forecast temperature | investigation + E-001 | closed | Supported, provisional (as recorded 2026-08-23) |
| demand R-002 Population-weighted temperature | investigation + E-001 | closed | Supported |
| demand R-003 Day type | investigation + E-001 | closed | Supported |
| demand R-004 Prior-year load | investigation + E-001, E-002 | closed | Supported; E-001 Not supported, E-002 Supported |
| demand R-005 Calendar features | investigation + E-001 … E-004 | closed | Not supported, all four |
| demand R-006 Recent load features | investigation + E-001 | closed | Supported, provisional (decision 4) |
| demand R-007 Forecast weather elements | investigation + E-001 | closed | Supported, provisional (decision 4) |
| demand R-008 Similar days from the paper's pool | investigation + E-001 | closed | Supported, provisional (decision 4) |
| spot R-001 Supply and demand tightness | investigation + E-001 | open, `In progress` | — (E-001 open too: run, no verdict) |

Titles keep the ID: `R-006 — Recent load features`, `R-006 E-001 — …`,
`O-002 — …`. Closed issues close as completed. The PR body lists every doc
next to its issue number.

Creating the 26 issues is outward-facing and not undone cleanly (a deleted
issue leaves a gap in the numbering), so it happens once, after the researcher
approves this spec, and before the docs PR, whose rewritten links need the
numbers.

**Deleted after the migration:** the two `observations.md`, the nine
`R-*.md`, `investigation-template.md`, and the *Backlog* and *Investigation
index* sections of the two task READMEs. **Kept:** `docs/research/README.md`
(rewritten, §6), `papers.md`, the task READMEs' scope defaults, `assets/`.

## 6. The repo docs after the move

- **`docs/research/README.md`** says what lives where (issues, the task
  READMEs, `papers.md`, `assets/`, MLflow, the preset files), the four types in
  one line each, the links of decision 2 and its last sentence, how to open
  one (the web chooser, or `gh issue create --template`), the Project fields
  and the family-batch rule, the three status things, the no-hypotheses rule,
  the writing-style pointer, and the asset naming: `assets/<issue number>-…`.
- **The task READMEs** keep *Scope defaults* and *Segments reported by the
  tooling*. The demand *Baseline* paragraph names
  `lightgbm_msm_popw_daytype_simday_lags_weather`, says its runs predate the
  2026-09-14 rank-1 similar day so the first experiment's baseline is a fresh
  run, and points at the R-008 issue for why `d019a370…` is not it. A
  *Research* section replaces *Backlog* and the index: the Project and an
  issue search per label.
- **`docs/README.md`**, **`docs/_sidebar.md`** and **`docs/Forecast-Analysis.md`**
  point at the README and the two task READMEs; the R-doc lines go.
- **`docs/TEPCO-Power-Usage-Retrieval.md`** and
  **`docs/Kansai-Power-Usage-Retrieval.md`** link the R-004 issue instead of
  the file. Plans and specs under `docs/superpowers/` are history and stay as
  they are; `just docs-links` names any live link to a deleted file, and that
  link is rewritten to the issue.
- **CLAUDE.md**, *Forecasting Research*: rewritten to the new rules, about the
  same length. The `demand/R-006` style references elsewhere in the file stay,
  since the titles keep the IDs.
- **`.github/workflows/weekly-research.md`**: the "read these" list points at
  the issues by label instead of the deleted READMEs; the workflow already
  reads the repository's issues. **`unbloat-docs.md`** is unchanged: its
  `research` rule still names what remains under `docs/research/`.

## 7. Family batches: the working rule

A candidate issue records a possible input; it is not a queue slot. When a
family has enough candidates worth a run, one experiment issue names them, a
preset file adds their columns to the baseline preset, one matched run against
a fresh baseline run on the same window, one compare, one decision. The
decision closes each candidate with its verdict; a kept batch's preset becomes
the baseline. Cheap column transforms go in together; a new source (the OCCTO
forecasts, extra MSM leads) gets its own experiment. Pruning is an experiment
whose preset drops. Which family goes first is the researcher's call; the
recent-load family (#146, #147, #148, #149, #154, #137, all mart columns) is the
cheapest.

## 8. Rollout

1. The researcher approves this spec.
2. Labels: create three, rename one. The `Family` field. The `Investigation`
   field deleted after its values are carried into the migrated issues' bodies.
3. The 26 issues (§5), their sub-issue links, Project items and fields, closes.
4. The PR: the three templates, the form's rename, the README rewrite, the task
   READMEs, the deletions, the front page, sidebar, the two retrieval docs,
   CLAUDE.md, the weekly-research workflow. `just docs-links` green, CI green,
   Codex clean.
5. The researcher extends the Project's auto-add filter and its views in the
   browser.
6. The fresh baseline run, then the first experiment issue.

Proof for the PR: the issue chooser shows the four templates; `label:experiment`
lists 13 issues and `label:observation` 4; the Project shows Family on the 21
open candidates; every deleted doc's content is reachable from its issue.

## 9. Alternatives not taken

- **Freeze the docs as history and start new research in issues.** Two places
  to search (decision 3).
- **Issue forms for the new types.** `gh` cannot fill them, and the prose
  sections would fight the widgets (decision 6).
- **A `Type` field on the Project.** The labels already say it, and the
  built-in Labels field filters on them.
- **A required parent for every experiment.** The rigid chain ChatGPT argued
  against on 2026-09-17 (decision 2).
- **Keeping `O-` / `R-` / `E-` numbers for new issues.** Needs machinery to
  allocate them; the issue number is unique already (decision 7).
- **Migrating each R-doc as one issue with its experiments inside.** An
  experiment would then have no issue to link from a candidate or from the
  next experiment.
