---
name: Experiment
about: One controlled comparison — a preset or rule change against a baseline — and its decision
title: ""
labels: [experiment]
assignees: []
---

- **Task:** demand / spot_price
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

- **Preset:** when the change is a feature set, `e<this issue's number>` with an optional batch slug (`e185_recent_load`), its file under `conf/presets/<task>/`; leave this line out for a rule-only change, which runs the baseline preset

## Expected evidence

- Expected direction of the primary metric
- Expected behaviour across backtest windows and in the important segments
- The result that would make the hypothesis less plausible

## Decision rule

<!-- What justifies keeping, rejecting or refining: practical size, consistency across windows, the CI over days, the important segments. No arbitrary universal threshold. -->

## Execution

- **MLflow experiment:** the task's name, demand or spot_price
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
