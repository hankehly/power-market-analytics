# Forecasting Research

This directory records the reasoning and conclusions behind forecasting
experiments. MLflow remains the source of truth for individual runs,
parameters, metrics, code versions, and detailed artifacts.

The working loop is:

> Observation or idea → investigation → predictive hypothesis → experiment →
> out-of-sample result → decision

## Layout

Research is organised per forecasting task, mirroring
`power_market_analytics/tasks/<task>/`, the MLflow experiment names and the
`--task` flag of `scripts/create_forecast_dashboard.py`:

```
docs/research/
├── README.md                  # this file: shared conventions
├── investigation-template.md  # shared template, copied for every investigation
├── papers.md                  # link index of external papers cited by the research docs
├── spot_price/                # JEPX day-ahead spot price
│   ├── README.md              # task index + scope defaults
│   ├── observations.md        # O-XXX log
│   ├── R-XXX-*.md             # investigations
│   └── assets/                # plots cited in this task's conclusions
└── demand/                    # area demand (load)
    ├── README.md
    ├── observations.md
    └── assets/
```

| Task | Index | Observation log |
|---|---|---|
| Spot price | [`spot_price/`](research/spot_price/README.md) | [log](research/spot_price/observations.md) |
| Demand | [`demand/`](research/demand/README.md) | [log](research/demand/observations.md) |

Adding a task = a new folder with the same three files, plus a row here and a
group in `docs/_sidebar.md`.

External papers that the observations and investigations cite are indexed
in [`papers.md`](research/papers.md) — links only (no PDFs in the repo), one
row per paper: what it is, the PDF, and which research doc cites it.

## Conventions

- Add notable forecast behavior to the task's observation log before trying
  to explain it. Record only observations and ideas supplied by the researcher
  or directly established from the cited evidence; do not generate possible
  explanations or hypotheses on the researcher's behalf unless explicitly
  asked.
- Create one investigation document for each coherent forecasting question by
  copying [the investigation template](research/investigation-template.md)
  into the task folder. Fill its *Scope and constraints* block from the task
  README's scope defaults.
- IDs are stable and **numbered per task**: each task folder has its own
  `O-001…` observations and `R-001…` investigations, and experiments are
  `E-001…` within an investigation. A bare ID is ambiguous across tasks, so
  always qualify it with the task outside its own folder — `spot_price/R-001`
  in prose and commit messages, `docs/research/spot_price/R-001-…md` in code.
- Keep multiple related experiments in the same investigation document.
- Create a new investigation when the question changes materially or can reach
  an independent conclusion.
- Link to MLflow runs instead of manually duplicating run-level configuration
  and metrics.
- Links between pages are written relative to the docs site root
  (`research/spot_price/observations.md#o-001-…`) so they resolve in the
  docsify site; image paths are relative to the page (`assets/…`).

## Assets

Store only plots and other small artifacts used directly in observation or
investigation conclusions under the task's `assets/` folder. Keep detailed run
artifacts in MLflow. Use IDs in filenames so their owners remain clear, for
example:

- `O-001-mae-by-day-part.jpg`
- `R-001-E-001-mae-by-window.png`
- `R-001-E-002-summer-hourly-error.png`

## Statuses

- Observations: `Unreviewed`, `Investigating`, `Investigated`, `No action`.
- Investigations: `Backlog`, `In progress`, `Supported`, `Not supported`,
  `Inconclusive`, `Superseded`.
- Experiment decisions: `Keep`, `Reject`, `Refine`, `Inconclusive`.
