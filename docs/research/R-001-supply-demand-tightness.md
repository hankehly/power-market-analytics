# R-001 — Supply and demand tightness signals

- **Status:** Backlog
- **Created:** 2026-08-16
- **Last updated:** 2026-08-16
- **Triggering observation:** [O-001 — Daytime MAE is higher than other day parts](research/observations.md#o-001-daytime-mae-is-higher-than-other-day-parts)
- **Related investigations:** —

## Question

Do supply-and-demand tightness signals available at forecast time improve
Tokyo-area price forecasts?

## Triggering observation

The LightGBM model's daytime MAE is higher than its MAE during the other
predefined day parts. See
[O-001](research/observations.md#o-001-daytime-mae-is-higher-than-other-day-parts).

## Current predictive hypothesis

Adding supply and demand information available by the D-1 09:55 JST cutoff
will reduce the LightGBM model's out-of-sample MAE, both overall and during
daytime periods.
