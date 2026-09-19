---
name: Feature candidate
about: A possible model input, ranked in the Load Forecasting Project and tested in a batch with related candidates
title: ""
labels: [feature candidate]
assignees: []
---

<!-- The title is the feature's short name. Physical names never change once a run has used them, so the expression is the name to get right; the physical name follows at build time (docs/Feature-Naming.md). -->

- **Task:** demand / spot_price
- **Suggested by:** <!-- who suggested it and when, if not the researcher -->

## Feature

<!-- What the model sees at each period, in one or two sentences: which value, from which data, over which days or hours. -->

## Why it should help

<!-- The predictive hypothesis and its evidence: "We believe that <feature> will lower MAE because <observation, result or reasoning>." Say which segments should move (day part, season, day type) if you expect a specific one. -->

## Follows from

<!-- The observation, investigation or paper it follows from: an issue number, or a docs/research/papers.md row; or a dash. -->

## Expression

<!-- The feature's expression in the docs/Feature-Naming.md grammar, e.g. LAG(demand_kwh, 7d) or MEAN(forecast_temperature_c, weight=population). A draft is fine; a column passed through keeps its name. -->

## Source data

<!-- Keep every table the feature reads; delete the rest. -->

- fct_area_demand_generation_actual (A-1 30-minute actuals)
- fct_area_power_usage_hourly (でんき予報 hourly load)
- fct_jma_weather_hourly (JMA observations)
- fct_jma_msm_weather_forecast_hourly (MSM forecast)
- fct_occto_demand_supply_forecast_daily / _30m (OCCTO 翌々日)
- fct_jepx_spot_area_price
- fct_census_population_jma_station
- dim_date
- pma_ml.similar_day (the fit-and-score job)
- another loaded table, or a new source (say which under Build notes)

## Grain

<!-- One of: day (area_code × trade_date), hour (… × hour_ending), period (… × time_code). -->

## Mart

<!-- One of ftr_day_actuals, ftr_day_calendar, ftr_day_msm, ftr_day_occto, ftr_hour_jma_obs, ftr_hour_msm, ftr_period_actuals, ftr_period_jepx, ftr_period_similar_day, or a new mart. -->

## Categorical

<!-- Yes or No: whether LightGBM should treat it as a categorical (the mart's categorical tag). Default No. -->

## Available at

<!-- When each value is public, against the task's issue time (09:30 JST D-1 for demand). Which column or rule gives the mart's available_at? A value that needs a later forecast run or a re-issued file cannot be used. -->

## Build notes

<!-- Any cost that is not obvious: a backfill and its size, a new download, a fitted job, a column that crosses two marts. -->

## Checks

- [ ] Not already a mart column (the Feature Catalogue dashboard, or the marts' YAML)
- [ ] Not among the set-aside ideas (closed feature-candidate issues)
- [ ] The expression follows docs/Feature-Naming.md and no other feature has it
