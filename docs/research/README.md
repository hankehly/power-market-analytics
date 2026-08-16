# Forecasting Research

This directory records the reasoning and conclusions behind forecasting
experiments. MLflow remains the source of truth for individual runs,
parameters, metrics, code versions, and detailed artifacts.

The working loop is:

> Observation or idea → investigation → predictive hypothesis → experiment →
> out-of-sample result → decision

## Conventions

- Add notable forecast behavior to [the observation log](research/observations.md).
- Create one investigation document for each coherent forecasting question by
  copying [the investigation template](research/investigation-template.md).
- Give investigations stable IDs such as `R-001` and experiments IDs such as
  `E-001` within the investigation.
- Keep multiple related experiments in the same investigation document.
- Create a new investigation when the question changes materially or can reach
  an independent conclusion.
- Link to MLflow runs instead of manually duplicating run-level configuration
  and metrics.
- Store plots used in conclusions under [`assets/`](research/assets/README.md).

## Investigation index

| ID | Investigation | Status | Current conclusion |
|---|---|---|---|
| R-001 | [Supply and demand tightness signals](research/R-001-supply-demand-tightness.md) | Backlog | — |

Suggested statuses are `Backlog`, `In progress`, `Supported`, `Not supported`,
`Inconclusive`, and `Superseded`.
