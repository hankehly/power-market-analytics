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
| Figures an issue embeds | `docs/research/<task>/assets/`, embedded by their raw URL on `main`; named `<issue number>-<slug>.png`, except the figures migrated on 2026-09-19, which keep their `R-XXX-E-XXX-…` and `O-XXX-…` names |
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

The **New issue** chooser offers the four templates and applies their labels.
From the command line, `gh issue create --template Observation --label observation`
(or `Investigation` with `investigation`, `Experiment` with `experiment`) opens the
Markdown template; `gh` copies a template's body but not its label, so the
`--label` is required. A feature candidate is an issue form, filled on the web;
`gh` cannot fill a form, so a candidate opened from the command line is a
Markdown body under the form's headings with `--label "feature candidate"`.

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
- **Build**, **Impact**, **Feasibility**: candidates only. Impact and
  Feasibility run 1 to 3, 3 the highest; the ranking reads both, and there is
  no priority field.
- **Decision**: `Supported`, `Not supported`, `Inconclusive`, `Superseded`,
  `Set aside`, the verdict of an investigation, an experiment or a candidate.
  An experiment's body keeps the words Keep / Reject / Refine / Inconclusive;
  the field takes `Supported` for Keep, `Not supported` for Reject and
  `Inconclusive` for the other two. An observation has no Decision.

The Project's auto-add workflow admits an issue by its label filter, which has
no API and is edited in the browser: it must name all four labels, or a new
record never enters the Project. Auto-add copies nothing from an issue into the
fields, so set Task, Build, Impact and Feasibility by hand when an item first
comes up. GitHub carries no family. A built feature's family belongs in the
repository, on its mart column, once the design for families as column tags (a
`meta.family` key on every tagged mart column, checked against a
`feature_families` seed) is approved and built; until then nothing records it. A candidate has no family until its column
exists: an experiment simply names the candidates it tests. The Project had a
Family field for one day, 2026-09-19. Setting a
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
folder, named by the issue: `assets/160-mae-by-month.png`. The thirteen figures
migrated on 2026-09-19 keep the names their records had
(`R-006-E-001-mae-by-month.png`, `O-001-mae-by-day-part.jpg`), because the
issues embed them by those paths. Keep detailed run artifacts in MLflow.
