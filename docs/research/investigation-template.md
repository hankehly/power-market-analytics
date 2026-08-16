# R-XXX — Investigation title

**Status:** Backlog  
**Created:** YYYY-MM-DD  
**Last updated:** YYYY-MM-DD  
**Triggering observations:** O-XXX or `None — modeling idea`  
**Related investigations:** —

## Question

State the coherent forecasting question this investigation should eventually
answer.

## Motivation

Summarize the observations, prior results, or domain reasoning that make this
question worth investigating. Treat causal explanations as useful rationale,
not as claims that a predictive experiment will prove.

## Current predictive hypothesis

State what model or data change is expected to improve genuinely out-of-sample
forecasting performance and why.

> We believe that [model or data change] will [improve primary evaluation
> metric] because [evidence or reasoning].

## Scope and constraints

- **Forecast target:**
- **Information cutoff:** D-1 at 09:55 JST
- **Baseline:** Strategy/version or MLflow run ID
- **Primary metric:** MAE
- **Important segments:** For example, area, season, time of day, or high-price periods
- **Evaluation method:** Rolling out-of-sample backtest

## E-001 — Experiment title

### Why this experiment

Explain why this is an informative and economical next test. For later
experiments, connect it to what was learned previously.

### Experiment hypothesis

State the intervention, expected out-of-sample result, and rationale.

### Change

Describe the single main change from the baseline. Keep implementation details
and parameter grids in MLflow or the code/configuration when possible.

### Expected evidence

- Expected direction of change in the primary metric
- Expected behavior across backtest windows
- Expected effect in important segments, if applicable
- Result that would make the hypothesis less plausible

### Decision rule

Describe what would justify keeping, rejecting, or refining the change. Prefer
practical magnitude, consistency across windows, uncertainty, and important
segment behavior over an arbitrary universal percentage threshold.

### Execution

- **MLflow experiment:**
- **Baseline run:**
- **Candidate runs:**
- **Code or pull request:**

### Results

| Metric | Baseline | Candidate | Absolute change | Relative change |
|---|---:|---:|---:|---:|
| Overall MAE | — | — | — | — |
| Important segment MAE | — | — | — | — |
| Mean error / bias | — | — | — | — |

Add only plots that support the interpretation or decision. Keep detailed
artifacts in MLflow.

<!-- Example: ![MAE by backtest window](assets/R-XXX-E-001-mae-by-window.png) -->

### Interpretation

Explain what the evidence suggests, where the effect was concentrated, and any
limitations or alternative explanations. Improved forecasting supports
incremental predictive value; it does not by itself establish causality.

### Decision

**Decision:** Keep / Reject / Refine / Inconclusive

Record the reason for the decision and any resulting production or backlog
change.

### Follow-up ideas

- Next representation or intervention worth testing
- Diagnostic analysis needed to understand the result

---

<!-- Copy the E-001 section for each additional experiment. -->

## Current conclusion

Summarize what is currently believed after considering all experiments in this
investigation. Update this section as evidence accumulates.

## Open questions

- Remaining uncertainty
- Possible next investigation

## Final disposition

**Investigation status:** In progress / Supported / Not supported / Inconclusive / Superseded  
**Recommended action:**  
**Superseded by:** —
