# Literature review prioritization

**As of** 2026-09-21. **Commit** `bc92de8e3942ca87a7c3cb09fbe9d08cfa1b2211` — the state
of `main` the measurements in §4 were taken against. Later commits on `main` are merged
into this branch but the numbers were not recomputed on them; §12 lists what that costs.

**Scope.** This ranks *interventions* — atomic, testable changes to our forecasting
system — extracted from the 162 sources of
[literature-review.md](research/literature-review.md). It does not rank papers. One
paper can yield several interventions, and several papers can support one. The primary
question is:

> Which unresolved mechanism gives the most credible chance of improving the current
> Tokyo demand forecast, or the most valuable falsifiable learning, for the cost of one
> controlled experiment?

The operational contract is the one in
[demand/README.md](research/demand/README.md): the 48 half-hourly `demand_kwh` values
of delivery day D for one area, issued at D-1 09:30 JST, only information public by
then, Tokyo baseline `e212`, primary metric MAE, rolling out-of-sample matched-run
evaluation. Ideas whose value is a new capability rather than lower point MAE are
ranked separately in §8.

Every claim in this report is marked. **Measured** means a number this task read from
the warehouse, MLflow or the repository on 2026-09-21 — the query is named.
**Reported** means a number a paper reports. **Inferred** means my own judgement.

## 2. Executive recommendation

**Run the D-2 forecast residual first.** Several candidates below have a measured
*problem* in our data. This is the only one whose *fix* could also be measured without
a new backtest — the correction is arithmetic on forecasts already stored — and it
measures well: feeding the model the mean error of its own forecast for D-2 — public
at D-1 09:30, absent from all 104 features — is worth **−1.62 %** on a held-out year,
with the coefficient chosen on the first year alone and applied untouched to the second. Its partial
correlation with D's daily bias, controlling for the D-2 load level the model already
has, is **0.240**, so it is new information rather than a re-encoding of a lag. Ten
papers support the mechanism ([P-113](research/literature-review.md#p-113),
[P-116](research/literature-review.md#p-116), [P-128](research/literature-review.md#p-128),
[P-145](research/literature-review.md#p-145), [P-146](research/literature-review.md#p-146),
[P-154](research/literature-review.md#p-154), [P-157](research/literature-review.md#p-157),
[P-161](research/literature-review.md#p-161), [P-034](research/literature-review.md#p-034),
[P-035](research/literature-review.md#p-035)) and its result decides that whole family
either way.

**Then prune.** `e212` carries 17 features whose permutation ΔMAE is ≤ 0 and 26 below
500 kWh, inside a feature set whose demand-level block has a median pairwise |r| of
0.833. Dropping them is a preset with a `drop` list — no code, no mart, no new data,
the cheapest possible experiment here. Its best outcome is "MAE unchanged, the preset
is leaner", which is exactly what makes it worth running: it settles whether the ΔMAE
table is actionable at all ([P-003](research/literature-review.md#p-003),
[P-058](research/literature-review.md#p-058), [P-063](research/literature-review.md#p-063)).

**Third, bound the day-type reference window.** `ftr_period_actuals`' day-type features
take the four most recent complete days of D's day type with no limit on how far back
they reach, and the error rises monotonically with that distance: MAE 489,466 when the
window is a week old, 819,693 at 15–30 days, **2,219,186 beyond 60 days** — 4.5× the
baseline, with the bias equal to the MAE. The 2,400 periods whose window is older than
a week are **6.86 % of the run carrying 10.69 % of its total error**.
[#203](https://github.com/hankehly/power-market-analytics/issues/203) already told the
model how stale the window is, and it is not enough: `oldest_daytype_4d_lag_days` sits
at permutation ΔMAE 4,759 while the reference itself runs 40 % low. Three papers
replace a coarse-label match with a deliberately chosen comparable day and report large
special-day gains ([P-104](research/literature-review.md#p-104) 7.29 % → 3.22 % MAPE,
[P-113](research/literature-review.md#p-113) −16 to −30 % on special days,
[P-001](research/literature-review.md#p-001) roughly halved).

**Three measurements close whole families before they are ranked.**

1. *Weather is not the binding constraint.* corr(our daily bias, the daily mean MSM
   temperature forecast error) = **0.060**. Better weather inputs — station selection
   ([P-097](research/literature-review.md#p-097),
   [P-099](research/literature-review.md#p-099)), station combination
   ([P-095](research/literature-review.md#p-095)), ensemble NWP
   ([P-086](research/literature-review.md#p-086),
   [P-087](research/literature-review.md#p-087)) — are bidding against that, on top of
   a population-weighted forecast we already have.
2. *The error is a day-level offset.* Within-day error autocorrelation **0.955**;
   zeroing each day's mean error would cut MAE from 510,465 to **328,362**. Mechanisms
   that fix the day's level compete for 36 % of the residual. Mechanisms that refine
   the within-day shape compete for the rest against a profile the model already gets
   nearly right.
3. *Static recalibration does not transfer.* A per-forecast-decile median shift fitted
   on one year gives **−0.21 %** on the next; conditioned on season, **+1.91 %**. This
   is why the regime-tracking residual works and a fixed bias correction does not — and
   it tempers the case for switching to a median objective on point-MAE grounds alone.

**A caution on the most tempting finding.** `e212` is 6.5 % better than its predecessor
overall but **7.2 % worse on holidays**, and its holiday bias appears to flip sign with
the holiday's weekday, from −128,236 on Mondays to +734,371 on Sundays. The SHAP
decomposition shows why the model cannot fix this itself: it applies an essentially
unmodified weekday effect on holidays (a Tuesday holiday still gets +77,817 of "it's a
Tuesday", against +70,795 on an ordinary Tuesday), while its three holiday features
supply one near-constant −67,000 … −98,000 level shift with no weekday interaction.
That is precisely the defect [P-106](research/literature-review.md#p-106)'s best-of-eight
"replacing dummies" treatment removes. **But the pattern does not transfer.** Fitting a
per-(weekday, holiday) shift on the first year makes the second year's holidays
**16.05 % worse**, and the per-weekday bias flips sign between years on three of seven
weekdays, on three or four observations each. The ledger says the same in its own
words: [#221](https://github.com/hankehly/power-market-analytics/issues/221) records
the pool reference improving 建国記念の日 by 965,669 kWh in 2025 and worsening it by
1,124,133 in 2026. So the holiday weakness is real and measured, the mechanism is
diagnosed, and the effect size is not yet a thing to bet a preset on. The holiday
family's next test is the one the ledger already named — a mart column that picks the
similar-day variant by `special_period` — not another calendar feature.

**Land the pinned evaluation window before any of it.**
[PR #223](https://github.com/hankehly/power-market-analytics/pull/223) is already open
and does exactly this: it pins demand to 2024-04-01 … 2026-03-31 and seals the days
after it as a holdout. The reason is the researcher's, on
[#221](https://github.com/hankehly/power-market-analytics/issues/221) — #212, #219,
#221 and R-006/7/8 all decided on the same 730 days, so the surviving preset is partly
fitted to that window through our keep/reject choices. #223 shows the cost already
paid: `e219` beat `e212` by 2.5 % over the first year of that window and **lost by
0.5 % over the second**, so the −1.0 % that earned it a Refine is the average of a gain
and a loss. It is also the single most common criticism in the corpus's own Appraisals
— seven of the papers cited here are discounted for exactly it. Two consequences for
this report: the rankings below stand, but each experiment will need one fresh `e212`
baseline on the pinned window, and any candidate resting on a single-window number
should be re-read once a second window exists.

**The best new capability is quantile forecasting from the existing strategy**, then
calibrated intervals on top (§8). They reuse the feature marts, Feast retrieval and the
training plumbing; the layers downstream need new work — a quantile-keyed write-back
(the `pma_ml` forecast table holds one value per period, not one per τ), pinball loss
and coverage beside MAE, a calibration check, and dashboards that draw a fan rather
than a line.
[P-147](research/literature-review.md#p-147) gives the reason plainly: real operator
day-ahead errors are peaked, heavy-tailed and biased low, so a Gaussian interval is the
wrong object — and our own error has kurtosis 4.04. The most *valuable* capability in
that lane is the joint demand-and-renewables distribution of
[P-162](research/literature-review.md#p-162), which has the best evidence of anything
in this review, but it is a second forecasting task rather than an experiment.
## 3. Decision objective and method

### The question

Which unresolved mechanism gives the most credible chance of improving the current
Tokyo demand forecast, or the most valuable falsifiable learning, for the cost of one
controlled experiment?

### How the catalogue was built

All 162 sources were read through their breakdowns in
[literature-review.md](research/literature-review.md). Interventions were extracted as
*mechanisms*, then merged: several papers supporting one mechanism became one entry
carrying all their IDs, and one paper describing three separable changes became three
entries. A paper that only re-demonstrates a mechanism another paper already gives is
attached to that mechanism rather than given an entry of its own. Papers yielding
nothing testable here are listed in §10.

### The six hard gates

An intervention is ranked only after it passes, and every non-pass is explained in §6.

| Gate | What it asks |
|---|---|
| Operational legality | Can our implementation use only information public by D-1 09:30 JST? |
| Scope fit | Does it apply to system- or area-level day-ahead forecasting, or is the transfer clearly justified? |
| Incremental distinction | Is it distinct from `e212`'s 104 features and from an already-decided experiment? |
| Testability | Can it be isolated in the matched rolling backtest with `compare_demand_runs.py`? |
| Data realism | Do the inputs exist, or have an attainable and maintainable source? |
| Objective compatibility | Is its value lower point MAE? If not it goes to §8, it is not failed. |

Results are `pass`, `defer`, `set aside` or `other lane`.

A paper's use of realized future weather does **not** by itself fail the legality gate.
It lowers transfer confidence, and our experiment substitutes the 12 UTC D-2 MSM
forecast. A mechanism that cannot be made legal at all does fail.

### Evidence discounting

No paper's headline percentage is carried into an expected impact. Each is discounted
by what the study did.

*Raise* confidence: real day-ahead weather forecasts; clean temporal holdout or rolling
out-of-sample; several systems or test periods; a strong matched baseline; uncertainty
or significance analysis; system/TSO/utility scale; hourly or half-hourly day-ahead
resolution.

*Reduce* confidence: feature or hyperparameter selection on the test period; realized
future weather; a single short test window or a handful of special days; holidays
excluded; a weak or unmatched baseline; gains that combine several changes; no
uncertainty analysis; a different forecast target; probabilistic gains presented as
though they imply point-MAE gains.

Two discounts bind hardest here and are applied throughout. First, **baseline
strength**: a gain over Tao's Vanilla regression, a plain SARMA or an unadapted GAM
says little about a gain over a 105-feature LightGBM with a learned similar-day
selector. Second, **our own measured history**: adding correlated features to this
model has repeatedly *hurt* — R-005's ten calendar features cost +7.3 % MAE, the two
holiday distances +6.5 %, the six calendar counts +4.2 %, `holiday_degree` alone
+0.3 %. Only R-007 (weather elements, −2.9 %) and #212 (all 104, −6.5 %) gave clear
gains, and #212's is confounded by one dominant feature.

### Ratings

Five criteria at 1–3, each with a stated reason, plus an ongoing-burden rating. They
are used to find a Pareto frontier, not summed into a score — the evidence does not
support that precision. Within the frontier the tie-breakers are, in order: higher
learning value; shorter experiment cycle; broader reuse across tasks or areas; lower
ongoing maintenance; a negative result eliminating more related ideas; greater
reversibility.

### What a preset can and cannot change

*Measured* from [presets.py](power_market_analytics/features/presets.py):
`PRESET_KEYS = {"description", "features", "base", "add", "drop"}`. A preset changes
**the feature list and nothing else**. `LGBM_PARAMS` is a module constant in
[lgbm.py](power_market_analytics/forecasting/lgbm.py) applied directly as
`LGBMRegressor(**LGBM_PARAMS)`; `train_window_days` (730) and `refit_every_days` (7)
are constructor arguments with no CLI flag on
[demand_backtest.py](scripts/demand_backtest.py), whose only options are `--strategy`,
`--area`, `--days`, `--start-date`, `--end-date`, `--train-start`, `--shap-nsamples`,
`--importance-repeats`, `--add`, `--drop` and `--name`.

*Inferred:* this is the sharpest feasibility line in the whole ranking. A feature
intervention is a mart column plus a YAML file and reaches feasibility 3. Any
objective, boosting-mode, window, cadence, per-group-model or post-processing change
needs Python, new tests against a 100 % coverage gate, and — by the research README —
an experiment of its own.
## 4. Current baseline and residual headroom

All numbers in this section are **measured** on 2026-09-21 from the live MLflow store
(`localhost:5005`) and the live warehouse (`pma_curated.fct_demand_forecast_accuracy`,
`pma_features.*`) — read-only. Both were available, so this ranking is not provisional
on the baseline evidence. The queries are named so each can be re-run.

### 4.1 The baseline

`e212` reference run `34c506fbb30d4c7eb4efdca973e49384`, Tokyo,
2024-08-18 … 2026-08-17, 35,002 periods over 730 days, 105 refits, 104 preset features
plus `time_code`.

| | `e212` `34c506fb…` | predecessor `32ecbdbc…` |
|---|---|---|
| MAE | **510,464.59 kWh** | 546,201.94 |
| MAPE | 3.122 % | 3.338 % |
| RMSE | 729,125 | 766,508 |
| R² | 0.9576 | 0.9532 |

Mean actual 15,886,862 kWh per period. LightGBM `n_estimators` 500, `learning_rate`
0.05, `num_leaves` 31, seed 0, sliding 730-day window, refit every 7 days, one model
over all 48 periods.

Every measurement in this section is on `34c506fb…`, the run
[demand/README.md](research/demand/README.md) names as the Tokyo baseline. A *new* run
should be matched against `943aab6d21b14fe2877169dabc5d694b` instead — the same `e212`
preset on the same window, scored against the similar-day partition as it stands today,
MAE 509,790 and MAPE 3.118. The two differ by 0.13 %; §12 item 6 says why.

### 4.2 The error is a whole-day level offset, not a shape error

This is the single most important structural fact for the ranking.

- Period-to-period autocorrelation of the error **within** a delivery day: **0.955**.
- Set every day's mean error to zero (an oracle day-level correction) and MAE falls
  from 510,465 to **328,362** — **35.7 % of the MAE is the day's overall level**.
- Mean |daily mean error| = 411,602 kWh, 81 % of the MAE.

*Inferred:* a mechanism that fixes the day's level is competing for 36 % of the
residual; a mechanism that refines the within-day shape is competing for the rest
against a profile the model already gets almost right period to period.

### 4.3 Where the error is

**By day type.** `e212` beat its predecessor on ordinary days and **lost ground on
holidays**.

| day type | periods | `e212` MAE | `e212` bias | predecessor MAE | change |
|---|---|---|---|---|---|
| Weekday | 22,752 | 482,961 | −65,901 | 527,218 | −8.4 % |
| Weekend | 9,370 | 483,576 | +8,553 | 526,391 | −8.1 % |
| Holiday | 2,880 | **815,224** | +119,020 | 760,628 | **+7.2 %** |

Holiday MAPE 5.47 % against 2.80 % on weekdays. Holidays are 8.2 % of periods and
13.1 % of the total absolute error.

**Holiday bias flips sign with the holiday's own weekday.**

| holiday falls on | days | MAE | bias |
|---|---|---|---|
| Monday | 18 | 945,337 | **−128,236** |
| Tuesday | 8 | 943,355 | +263,802 |
| Wednesday | 7 | 836,028 | +261,399 |
| Thursday | 7 | 665,732 | +195,772 |
| Friday | 7 | 609,635 | −153,612 |
| Saturday | 6 | 632,178 | +12,257 |
| Sunday | 7 | 825,390 | **+734,371** |

Non-holidays are far flatter by weekday (bias −135,993 … +35,206). Split another way:
true 祝日 Mon–Fri, 28 days, MAE 876,318, bias −48,729 — the worst segment, and
two-sided; true 祝日 Sat–Sun, 8 days, 602,662, +388,394; customary periods
(正月 / 晦日 / お盆 / GW) Mon–Fri 19 days, 779,251, +173,236, Sat–Sun 5 days, 949,899,
+421,397.

*Inferred:* the weekday × holiday interaction is systematic and the model cannot learn
it from splits — there are 6 to 18 holiday days per weekday in a 730-day window. That
scarcity is the same reason [#143](https://github.com/hankehly/power-market-analytics/issues/143)
(the holiday name as a categorical) was set aside on 2026-09-15.

**By demand level.** The model systematically under-forecasts the top of the
distribution.

- Top-10 % demand days (the compare script's rule, daily mean actual), 74 days:
  MAE 667,508, **bias −433,641**. The other 656 days: MAE 492,289, bias +14,887.
- By 2,000-MWh actual band, `e212` bias runs +236,870 at 8–10 GW, +96,848 at 10–12,
  +112,676 at 12–14, +73,327 at 14–16, −44,530 at 16–18, −115,481 at 18–20,
  −350,796 at 20–22, −633,297 at 22–24, −472,744 at 24–26, −696,877 at 26–28 and
  −1,743,095 at 28+ (25 periods, every one under-forecast).
- By forecast decile the **median** error runs −11,652 (d1) … −131,580 (d9),
  −179,501 (d10): at the top the forecast sits below the conditional *median* of the
  actual, which is the MAE-optimal point. Overall error skew −0.098, kurtosis 4.04.

*Reported, corroborating:* [P-147](research/literature-review.md#p-147) found the same
shape in CAISO's and NYISO's own operational day-ahead forecasts — peaked, heavy-tailed
and biased low, load under-forecast on the morning ramp, most in summer. This is a
general property of day-ahead load forecasts, not a defect peculiar to `e212`.

**By season and day part.** Worst months August 789,154 (4.51 %, bias +227,702),
February 723,914 (4.14 %), July 670,956; best November 323,271 (2.21 %), October
343,569. `e212` **regressed** against its predecessor in April (+1.1 %), May (+2.0 %)
and August (+1.0 %). Day parts: Daytime 656,124 (3.70 %) > Evening 481,693 > Morning
460,004 > Overnight 313,515 (2.41 %). By quarter there is no drift: Q3 635,586 /
692,332 / 711,938 across 2024–2026, Q2 and Q4 steady at 350,000–406,000.

**Error concentration.** The ten worst days are 5.79 % of the total absolute error, the
worst 30 are 13.96 %, the worst 73 (10 % of days) 26.64 %. *Inferred:* the residual is
broadly spread. An intervention that only repairs catastrophic days is bounded at a few
per cent.

### 4.4 Feature structure

From the run's `permutation_importance.csv`:

- `wavg_similar_day_top3_demand_kwh` carries ΔMAE **2,030,747**, **21.6×** the next
  feature (`LAG(DAILY_MEAN(demand_kwh, time=18:00–22:00), 2d)` at 93,937). The model is
  anchored on one similar-day signal.
- **17 of 105 features have ΔMAE ≤ 0** and **26 have ΔMAE < 500 kWh** (under 0.1 % of
  MAE): `half`, `quarter`, `LAG(day_type, 2d)`, `is_business_day`, `fiscal_quarter`,
  `day_of_month`, several D-2 window means and trends.
- Inside `ftr_period_actuals`' 16 demand-level columns the median pairwise |r| is
  **0.833**; 31 % of pairs exceed 0.90 and 14 % exceed 0.95.

*Inferred:* permutation importance under this much collinearity understates a group and
overstates a unique feature, which is exactly why the similar-day mean dominates — it is
the only column of its kind.

### 4.5 Two measurements that deprioritise families

**Weather-forecast accuracy is not the binding constraint.** Over the window, the MSM
temperature forecast at Tokyo's representative station s47662 has mean |hourly error|
1.313 °C and a daily-mean-error SD of 0.942 °C. Against our daily error:

- corr(daily demand bias, daily mean temperature forecast error) = **0.060**
- corr(daily demand MAE, daily mean |temperature forecast error|) = **0.070**

*Inferred:* on this test, weather-forecast accuracy explains under 0.5 % of the variance
of our daily error, so every intervention whose mechanism is "get better weather into the
model" — station selection, station combination, ensemble NWP — is bidding against that.

**What this test does not establish.** It is one station's daily mean, compared by Pearson
correlation. It does not test spatial representativeness across the area, a non-linear or
seasonal response, intraday forecast error, or behaviour in extremes — and
[#212](https://github.com/hankehly/power-market-analytics/issues/212)'s own analysis found
a −1.29 °C MSM cold bias contributing on 2025-07-21, inside a heat wave. So this is
grounds to **deprioritise** the spatial-weather family, which is how §6 and §7 gate it
(`defer`), and not grounds to call it closed. §13 names the re-test that would reopen it.

**Behind-the-meter solar reconstruction finds no support here.** Correlation of the
period error with the population-weighted forecast solar radiation at the same hour:
Daytime 0.018 (absolute error 0.023), Morning 0.047, Evening −0.001, Overnight −0.015.
Nor is there a trend: daytime bias by half-year runs −74,208 / +10,056 / −119,120 /
−65,375 from 2024H2 to 2026H1.

*Inferred, with its limit stated:* the irradiance signal the model already holds appears
to be absorbed, and a growing hidden PV level is not showing as a drifting daytime bias.
Neither test refutes a latent gross-load / behind-the-meter split — a *stable* PV
contribution would leave both flat — so this lowers the priority rather than settling the
question. §7 gates it `defer` on that reading.

### 4.6 One measured, unexploited, operationally legal signal

The daily mean error autocorrelates: lag 1 **0.475**, **lag 2 0.256**, lag 7 0.020;
SD 574,634. The D-2 residual *is* knowable at D-1 09:30 — D-2's actuals are final
(`history_lead_days = 2`) and the forecast for D-2 was issued at D-3 09:30 — and the
model never sees it.

Applying `f' = f − β · (mean error on D-2)` with β fixed, over the 34,906 periods that
have a D-2 residual:

| β | 0.00 | 0.10 | **0.20** | 0.26 | 0.35 | 0.50 |
|---|---|---|---|---|---|---|
| MAE | 509,959 | 502,195 | **498,789** | 498,806 | 501,659 | 513,221 |

β = 0.20 gives **−2.19 %** over the whole window, and the optimum is flat between 0.20
and 0.26. That sweep reads the whole window, so it cannot also serve as a test. The
honest version selects β on the first year alone and applies it untouched to the second:

| on year 1 only | base | β .05 | β .10 | β .15 | β .20 | **β .25** | β .30 |
|---|---|---|---|---|---|---|---|
| MAE | 522,274 | 517,241 | 513,291 | 510,411 | 508,606 | **507,779** | 508,091 |

β = 0.25 wins on year 1. Applied to year 2 with nothing refitted: **497,739 → 489,679,
−1.62 %** over 17,520 periods. That is the number to carry, not the −2.19 %.

The held-out segment pattern is close to the in-window one but not identical: Holiday
DJFM **−4.53 %**, Weekend JJAS −2.65 %, Weekday JJAS −2.60 %, Weekday shoulder −1.59 %,
Weekend DJFM −0.06 %, and Holiday shoulder **+4.40 %** — worse. The gain concentrates in
the hot and cold regimes, as the mechanism predicts; the shoulder seasons are where it
does nothing, and on shoulder-season holidays it hurts.

That contrast is worth stating against the one other
recent candidate whose split has been published:
[PR #223](https://github.com/hankehly/power-market-analytics/pull/223) shows `e219`
beating `e212` by 2.5 % in the first year and **losing by 0.5 %** in the second. Rank 1
is the candidate that holds up across the split; the holiday-pool rule is the one that
does not. By segment at β = 0.20 the gain concentrates where the
error is largest: Holiday JJAS −4.4 %, Holiday DJFM −4.4 %, Weekday JJAS −3.2 %,
Weekday DJFM −3.1 %, Weekend DJFM −1.8 %, and it is neutral to +0.1 % in the shoulder
seasons.

*Inferred:* the persistence is a regime effect — heat waves, cold snaps, holiday
clusters — that a 7-day refit on a 730-day window under-adapts to. This is the largest
measured, unexploited, operationally legal signal this review found, and it is the
empirical anchor of the top-ranked intervention in §7.

### 4.7 Training scarcity

Over four years of Tokyo delivery days, days whose population-weighted MSM forecast
maximum reaches 31.5 °C: **8 holidays** against 105 weekdays and 40 weekends (out of
117 / 952 / 392 days). Inside the 730-day training window,
[#212](https://github.com/hankehly/power-market-analytics/issues/212) measured 5
holidays against 57 weekdays and 26 weekends, and found only one clean non-お盆
precedent for a hot non-summer-festival holiday in two years.

*Inferred:* several literature mechanisms for holidays are really answers to this
scarcity, and should be judged as such rather than as feature ideas.
## 5. Intervention catalogue

All 162 sources were read. **155 contributed at least one intervention**; the seven
that did not are listed in §10.5 with reasons. The raw extraction produced 220
candidate interventions, merged here into distinct mechanisms — several papers
supporting one mechanism became one entry carrying all their IDs, and one paper
describing separable changes became several entries.

The catalogue is grouped by family. Within a family the entries are distinct
mechanisms, not variants: each would be a separate experiment under the repository's
rules. Columns are compressed for reading; the gate results are §6 and the ratings §7.

Status values: **absent**, **partially represented** (something adjacent exists — the
overlap column names it), **already represented** (§10.1), **already tested** (§10.2),
**under experiment**.
### Family 1 — Feedback and adaptation (the model's own errors)

| Mechanism | Precise change | Papers | Lane | Inputs / legal at 09:30 D-1 | Status | Overlap | Segment | Papers report | Evidence limits | Smallest experiment here | Burden |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **A1 Own D-2 forecast residual** | Give the model the mean error of its own forecast for D-2 | P-113, P-116, P-128, P-145, P-146, P-154, P-157, P-161, P-034, P-035 | point MAE | our own stored forecasts + D-2 actuals; **yes** | absent | none | regimes, holidays, the day level | P-145 MAPE 1.83 → 1.63 %; P-113 −16…−30 % on special days; P-161 nRMSE 0.377 → 0.332 | P-145's baseline is a *frozen* model, ours refits weekly; P-157/P-161 test windows are COVID-dominated; observed weather in P-145/P-157 | a `pma_ml` write-back of persisted D-2 residuals, read by a guarded staging model and a day-grain mart (the `fit_similar_day.py` pattern); a wrapper around the walk-forward does **not** work — see §9.1 | low |
| **A2 Recency-weighted training rows** | `sample_weight` decaying with age instead of a flat 730-day window | P-145, P-154, P-157, P-161, P-035, P-114, P-121, P-129, P-019 | point MAE | none new; **yes** | absent | the window is flat and hard-edged | regime transitions | P-154 lowest RMSE on 6 of 7 datasets | hyper-parameters "picked by eye on one data set" (P-154's own appraisal); no significance tests | one argument in `lgbm.py`, its own experiment | low |
| **A3 Refit daily instead of weekly** | `refit_every_days` 7 → 1 | P-067, P-145, P-034, P-161, P-154, P-059 | point MAE | none new; **yes** | absent | `refit_every_days` is a constructor argument with no CLI flag | whole run | P-067 MAPE 3.75 → 2.18 % with daily refits | P-067's base is a GAM, and the same paper found XGBoost no better (RMSE 199 vs 191 MW); one country, one test year | one constructor argument + a CLI flag; 7× the fit cost | med |
| **A4 Online aggregation of several presets** | Weight live forecasts by recent performance | P-146, P-035, P-034, P-157, P-160, P-161 | point MAE | several runs' forecasts; **yes** | absent | none | whole run | P-146 RMSE 27.8 vs 30.4 (best expert); P-035 won its competition track | P-146's French test excludes holidays; the post-COVID competitions reward regime-break adaptation we do not have | needs A5's several runs first | high |
| **A5 Combine several fits (sister forecasts)** | Average *k* fits differing in row or feature subsample (`bagging_freq ≥ 1` or `feature_fraction`; seed alone is inert) | P-059, P-146, P-025, P-149, P-027, P-001, P-067 | point MAE | none new; **yes** | absent | every run is a single fit | whole run + the noise floor | P-059 4.74 → 4.52 % and 2.24 → 2.10 %, DM-tested, never worse in 20–22 of 24 hours | P-059's sisters differ in structure, ours would differ in seed; P-025 saw correlated members fail to help | *k* fits per refit inside the existing strategy | med |

### Family 2 — Reference days and the similar-day machinery

| Mechanism | Precise change | Papers | Lane | Inputs / legal | Status | Overlap | Segment | Papers report | Evidence limits | Smallest experiment here | Burden |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **B1 Bound the day-type reference** | Cap how far back the day-type window may reach; null beyond | P-104, P-113, P-001, P-141, P-106, P-111 | point MAE | none new; **yes** | absent | `mean/ewm_daytype_4d…` are unbounded; #203 added the *age* | the 2,400 stale-window periods | P-104 7.29 → 3.22 % on special days; P-113 −16…−30 %; P-001 roughly halved | P-104: one country, one test year, 24 special days, categories set by eye; all three are load-only, no weather | bounded twin columns in `ftr_period_actuals` + a preset | low |
| **B2 Correct the reference for its weather gap** | Feed the signed difference between D's forecast weather and the reference day's observed weather | P-141, P-128, P-113, P-006, P-002, P-104 | point MAE | MSM + JMA + `pma_ml.similar_day`; **yes** | partially — [#134](https://github.com/hankehly/power-market-analytics/issues/134) specifies it, unbuilt | the raw reference load is a feature, its conditions are not | holidays; the 54 `same_holiday` days | P-141 MAPE 1.76 / 2.62 %, ≈1.1 points below the operator, **XGBoost and LSTM worst** | P-141 corrects trend, weather *and* BTM PV together and reports one number; two test years, no significance test | mart columns + preset | low |
| **B3 Level-normalise the reference** | Scale the retrieved profile to D's expected level before use | P-122, P-115, P-120, P-141, P-128 | point MAE | none new; **yes** | absent | the reference is halved per period and used raw | holidays; high-demand days | P-128 mean error 1.47 % over two years, on JMA forecast temperature | P-128 excludes holidays and hand-sets its thresholds; the 1990s NN papers have no matched modern baseline | a mart column | low |
| **B4 Select the similar-day variant by `special_period`** | A mart column choosing `same_holiday` or `similarity` per day type | P-104, P-113, P-001 | point MAE | none new; **yes** | **under experiment** — the researcher's own follow-up on [#221](https://github.com/hankehly/power-market-analytics/issues/221) | the two variants exist side by side since PR #220 | holidays by family | #221 measured: `e219` wins three families, `e221` wins GW | #219's CI over days includes zero; 54 holiday days | a mart column + preset | low |
| **B5 Pattern-sequence / clustered similar day** | Build the distance from load-shape clusters rather than weather and calendar | P-100, P-101, P-102, P-103, P-110, P-112 | point MAE | none new; **yes** | partially | `fit_similar_day.py` already learns a 7-weight distance | holidays; transition days | varies by paper, mostly against naive or ARIMA baselines | different systems, mostly weak baselines; none compares against a learned-weight similar day | re-parameterise the fit's distance | med |

### Family 3 — Calendar and special days

| Mechanism | Precise change | Papers | Lane | Inputs / legal | Status | Overlap | Segment | Papers report | Evidence limits | Smallest experiment here | Burden |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **C1 Replacing-dummy weekday coding on holidays** | One categorical that is the weekday on ordinary days and a holiday code (optionally weekday-specific) on holidays | P-106, P-004, P-144 | point MAE | none new; **yes** | absent | `day_of_week` and `day_type` are separate features | holidays, 13.14 % of the error | P-106 holiday MAE 8.59 → 1.66 GW (−81 %), overall 1.48 → 1.23 GW, **five test years**, day-ahead, MAE | linear OLS models, no weather, one country — and **our own data does not support the effect size** (§4.3, §7 band C) | a `ftr_day_calendar` column + preset | low |
| **C2 Fine special-day classes** | A per-holiday or learned-cluster categorical | P-004, P-111, P-113, P-104, P-054 | point MAE | none new; **yes** | **already tested** — [#143](https://github.com/hankehly/power-market-analytics/issues/143) set aside | `special_period` is the coarse form | holidays | P-111 special-day error 1.49 → 1.42 % | P-111 needs ≥ 7 years of training; our window is 730 days. #143: 48–96 rows per level vs `min_data_per_group` 100 | — (blocked) | — |
| **C3 Discrete-interval moving seasonality** | Model a named event window as its own seasonal index | P-107, P-109, P-004 | point MAE | none new; **yes** | partially | `special_period` marks the three windows but not the position within them | positions within 年末年始 / GW / お盆 | P-109 twelve special days 12.63 → 3.87 % MAPE | twelve test days in one year; Holt-Winters base, no weather | a position-within-event column | low |
| **C4 Separate model per segment** | Fit one model per day-type group — or per weather regime, blended by forecast temperature ([P-117](research/literature-review.md#p-117), [P-119](research/literature-review.md#p-119)) — instead of one pooled model | P-144, P-137, P-158, P-084, P-038, P-134, P-106 | point MAE | none new; **yes** | absent | `day_type` / `special_period` are categoricals in one model | holidays; day types | P-144 MAPE 6.47 → 4.03 % and three more systems | one test year, no weather, duplicated columns flagged in its own appraisal; **P-106: dropping holidays from training made holidays worse** | a per-group strategy; holiday group ≈ 2,784 rows | high |
| **C5 Cross-area transfer for scarce day types** | Add Kansai's holiday rows to Tokyo's training window, weighted | P-135, P-160, P-081 | point MAE | Kansai marts; **yes** | absent | none | holidays | P-135 Guangzhou 3.50 → 2.14 %, Zhongshan 5.31 → 2.64 % | one province, one test year, no weather, one of eleven cities worse | **blocked** — `ftr_period_similar_day` is Tokyo-only, so borrowed rows lose the top feature | high |

### Family 4 — Weather

| Mechanism | Precise change | Papers | Lane | Inputs / legal | Status | Overlap | Segment | Papers report | Evidence limits | Smallest experiment here | Burden |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **D1 Station selection / fitted weights** | Choose or fit the station subset and weights against load error instead of population | P-095, P-097, P-099, P-128 | point MAE | none new; **yes** | partially — we population-weight every station | `MEAN(<element>, weight=population)` | whole run | P-095 7.00 → 6.96 %; P-097 RMSE −4.8 % DM-significant; P-099 ≈2–3 % overall on a clean 2019 | P-095/P-097 use **observed** temperature and pick K on the test set; only P-099 uses real forecasts, over 24 Reg-ARIMA models | a weighting variant in `ftr_hour_msm` | med |
| **D2 MOS bias-correction of the MSM forecast** | Learn and subtract the NWP's conditional bias before use | P-118, P-119, P-121, P-048, P-044, P-043 | point MAE | MSM + JMA observations; **yes** | absent | raw MSM values only | heat waves, cold snaps | improvements reported inside larger systems, not isolated | the corpus reports no clean isolated MOS gain for load; our own average correlation is 0.060 | a corrected column in `ftr_hour_msm` | med |
| **D3 Lagged forecast temperature within day D** | The forecast temperature of the hours *before* the target hour, same vintage | P-092, P-098, P-026, P-063 | point MAE | MSM only; **yes** | partially — [#204](https://github.com/hankehly/power-market-analytics/issues/204) open, unbuilt | observed recency covered by the 24 h / 72 h windows | daytime and evening | P-092 5.22 → 4.27 % MAPE | **realized** temperature, linear benchmark, best lag pair chosen in hindsight, one utility, one year | `ftr_hour_msm` columns + preset | low |
| **D4 Degree-hours / threshold exposure** | Population-weighted heating and cooling exposure above and below a base | P-092, P-085, P-090, P-091, P-096, P-136, P-040, P-043 | point MAE | MSM + census; **yes** | partially — [#152](https://github.com/hankehly/power-market-analytics/issues/152) / [#153](https://github.com/hankehly/power-market-analytics/issues/153) open | raw temperature + 不快指数; a GBM can find the break itself | hot spells, cold snaps | context papers, not day-ahead accuracy trials | mostly climate-impact studies with no forecast horizon | mart columns + preset | low |
| **D5 Temperature anomaly against a climatological normal** | Express weather relative to a day-of-year normal | P-085, P-090, P-091 | point MAE | JMA normals, **not ingested** | absent | absolute values only | transition seasons | context papers | none is a day-ahead forecasting trial | [#218](https://github.com/hankehly/power-market-analytics/issues/218) is the open investigation | med |
| **D6 Ensemble NWP spread** | Use a multi-member forecast's mean and spread | P-086, P-087, P-089, P-155 | point MAE / new capability | an ensemble NWP, **not ingested** | absent | one deterministic MSM run | extremes | P-087 reports gains from ensembles | our own corr is 0.060; P-155 found gridded features "added nothing reliable"; [#141](https://github.com/hankehly/power-market-analytics/issues/141) blocks later vintages | — (new source) | high |
| **D7 Behind-the-meter PV reconstruction** | Estimate hidden PV, add it back, forecast gross, subtract | P-137, P-141, P-162 | point MAE | MSM radiation + a capacity estimate, **not ingested**; **yes** once it is | absent | `popw_forecast_solar_radiation_mjm2` and its cumulative sum are features | daytime | P-137 MAPE 1.63 → 1.46 % | observed weather, holidays excluded, the BTM capacity never validated — and **our own tests find no support** (§4.5), though they cannot rule out a stable hidden level | a capacity-registry estimate first | high |

### Family 5 — Target and model form

| Mechanism | Precise change | Papers | Lane | Inputs / legal | Status | Overlap | Segment | Papers report | Evidence limits | Smallest experiment here | Burden |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **E1 Level-normalised target** | Predict the ratio to a level anchor, multiply back | P-115, P-120, P-122, P-123, P-032, P-036, P-050, P-055, P-080, P-127 | point MAE | none new; **yes** | absent | the target is the raw level | the upper half of the range | varied; mostly 1990s Japanese peak forecasting and modern de-levelling | no matched modern baseline; **our own test is split** (§7 band B) | a target transform + inverse in the strategy | med |
| **E2 Correlation-aware feature pruning** | Drop the inert and the duplicated | P-003, P-058, P-063, P-025, P-030 | point MAE | none new; **yes** | absent (the machinery exists, nothing acts on it) | permutation importance runs every backtest | whole run | P-058 MAPE 1.773 → 1.745 % | subset size chosen on the test set; random test days leak neighbours; one city, one year | a preset with a `drop` list | low |
| **E3 DART boosting** | `boosting_type='dart'` | P-003 | point MAE | none new; **yes** | absent | `LGBM_PARAMS` is a module constant | whole run | qualification MAPE 3.24 → 2.83 % | that round used **realized future temperatures**; no isolated finals figure; the team placed sixth | one parameter, its own experiment | low |
| **E4 L1 / pinball-median objective** | Optimise the conditional median, which is MAE-optimal | P-147, P-150, P-156, P-159, P-032, P-003 | point MAE | none new; **yes** | absent | L2 objective throughout | high-demand periods | P-159 shows a tailored loss moves the operating point (cost −10…−14 %, **MAPE 5.36 → 5.49 %**) | no paper reports an L1-for-MAE gain on a strong GBM baseline; **our static-recalibration test transfers at −0.21 %** (§4.6) | one parameter; better run inside §8 N1 | low |
| **E5 One model per period or day part** | Split the single pooled model | P-084, P-026, P-044, P-053, P-127, P-129, P-011 | point MAE | none new; **yes** | absent | `time_code` is a feature (ΔMAE 84,844, rank 4) | period-specific structure | P-084 1.8950 % vs 2.3159 % for one unified model | one country, one test year, no simple benchmark; 48 models on a 730-day window | a per-group strategy | high |
| **E6 Neural / foundation-model replacement** | Replace LightGBM | P-074, P-075, P-078, P-079, P-080, P-082, P-084, P-139 | point MAE | none new; **yes** | absent | — | whole run | varied, generally against weak baselines | P-067 measured XGBoost **no better** than a GAM (199 vs 191 MW); replacing the model also replaces TreeSHAP, the importance machinery and the contribution facts | — (not an experiment) | high |

### Family 6 — Exogenous inputs

| Mechanism | Precise change | Papers | Lane | Inputs / legal | Status | Overlap | Segment | Papers report | Evidence limits | Smallest experiment here | Burden |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **F1 An operator's own forecast as an anchor** | Feed OCCTO's 翌々日 forecast and learn its error | P-057, P-055, P-053 | point MAE | OCCTO; daily feed **yes**, half-hourly **yes but short** | partially — `ftr_day_occto` exists, excluded from `e212` by ruling; [#140](https://github.com/hankehly/power-market-analytics/issues/140) set aside the half-hourly feed | `ftr_day_occto` | whole run | — | the half-hourly feed has 538 days; the daily one starts 2024-04-01 | a preset adding `ftr_day_occto` | low |
| **F2 Peer-area load** | Another area's recent load as a feature | P-160, P-162 | point MAE | Kansai marts; **yes** | absent | none | whole run | P-160 RMSE 1,409 → 1,214 MW stacked | case 2's gains "come mostly from the covid break" (its own appraisal) | a mart column | med |
| **F3 News-text features** | Sentiment / topic features from news | P-068 | point MAE | **not ingested**, no Japanese equivalent | absent | none | whole run | reported for a different system | a new source with a standing ingestion burden | — | high |
| **F4 Pressure-pattern / circulation regime label** | A synoptic regime categorical | P-096 | point MAE | MSM pressure fields; **yes** (we hold surface and sea-level pressure) | absent | `popw_forecast_surface_pressure_hpa`, `…_sea_level_pressure_hpa` | winter extremes | P-096 is a climate-attribution study, not a forecast trial | "loads modelled, not metered; two training years" (its own appraisal); Europe, winter only | a clustering column | med |
## 6. Gate results

Every intervention in §5 is gated before it is ranked. `pass` means it can be tested
here as it stands. `other lane` means its value is a capability, not point MAE — it
moves to §8 and is not failed. `defer` means the mechanism is sound but something
must exist first. `set aside` means it cannot be made to work here.

A paper's use of realized future weather never triggers a gate by itself: it lowers
transfer confidence in §7, and our implementation substitutes the 12 UTC D-2 MSM
forecast. What fails the legality gate is a mechanism that cannot be made legal —
`e212`'s own ledger has three such precedents, recorded in §10.4.
| Mechanism | Legality | Scope | Distinction | Testability | Data realism | Objective | **Result** |
|---|---|---|---|---|---|---|---|
| A1 Own D-2 forecast residual | ✓ D-2 actuals are final and the D-2 forecast was issued at D-3 09:30 | ✓ | ✓ no feature is a function of our own output | ✓ | ✓ | ✓ | **pass** |
| A2 Recency-weighted training rows | ✓ | ✓ | ✓ the window is flat | ✓ | ✓ | ✓ | **pass** |
| A3 Refit daily | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| A4 Online aggregation of presets | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **defer** — needs A5's several runs to exist first |
| A5 Combine several fits | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| B1 Bound the day-type reference | ✓ | ✓ | ✓ #203 added the *age*, not a bound | ✓ | ✓ | ✓ | **pass** |
| B2 Correct the reference for weather | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| B3 Level-normalise the reference | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| B4 Select the variant by `special_period` | ✓ | ✓ | — it *is* the ledger's open follow-up | ✓ | ✓ | ✓ | **under experiment** — #221's recorded next step |
| B5 Pattern-sequence similar day | ✓ | ✓ | ✗ the same mechanism our learned distance already parameterises | ✓ | ✓ | ✓ | **defer** — distinct only in the distance's construction |
| C1 Replacing-dummy weekday coding | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **defer** — the effect does not transfer across years in our data (§7) |
| C2 Fine special-day classes | ✓ | ✓ | ✗ | ✗ 48–96 rows per level vs `min_data_per_group` 100 | ✓ | ✓ | **set aside** — [#143](https://github.com/hankehly/power-market-analytics/issues/143) |
| C3 Discrete-interval moving seasonality | ✓ | ✓ | partly — `special_period` marks the windows, not the position within them | ✓ | ✓ | ✓ | **pass**, low priority |
| C4 Separate model per segment | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| C5 Cross-area holiday transfer | ✓ | ✓ | ✓ | ✓ | ✗ `ftr_period_similar_day` is Tokyo-only, so borrowed rows lose the top feature | ✓ | **defer** — needs `fit_similar_day.py --area kansai` |
| D1 Station selection / fitted weights | ✓ | ✓ | partly — population weighting is a different basis reaching the same end | ✓ | ✓ | ✓ | **defer** — corr(our error, weather forecast error) = 0.060 (§4.5) |
| D2 MOS bias-correction | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass**, framed on extremes not averages |
| D3 Lagged forecast temperature within D | ✓ same vintage | ✓ | partly — observed recency is covered, forecast recency is not | ✓ | ✓ | ✓ | **pass** — already [#204](https://github.com/hankehly/power-market-analytics/issues/204) |
| D4 Degree-hours / threshold exposure | ✓ | ✓ | partly — a GBM can find the break point from raw temperature | ✓ | ✓ | ✓ | **pass** — already [#152](https://github.com/hankehly/power-market-analytics/issues/152) / [#153](https://github.com/hankehly/power-market-analytics/issues/153) |
| D5 Climatological-normal anomaly | ✓ | ✓ | ✓ | ✓ | ✗ JMA normals are HTML tables, not ingested | ✓ | **defer** — [#218](https://github.com/hankehly/power-market-analytics/issues/218) |
| D6 Ensemble NWP spread | ✓ in principle | ✓ | ✓ | ✓ | ✗ no ensemble ingested; [#141](https://github.com/hankehly/power-market-analytics/issues/141) blocks later vintages | ✓ | **set aside** |
| D7 Behind-the-meter PV reconstruction | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **defer** — both our tests are negative, but neither refutes a *stable* hidden PV level (§4.5) |
| E1 Level-normalised target | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| E2 Correlation-aware pruning | ✓ | ✓ | ✓ nothing acts on the importance table today | ✓ | ✓ | ✓ | **pass** |
| E3 DART boosting | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass** |
| E4 L1 / pinball-median objective | ✓ | ✓ | ✓ | ✓ | ✓ | partly — its natural home is the quantile work | **other lane** — run inside §8 N1 |
| E5 One model per period or day part | ✓ | ✓ | partly — `time_code` is a feature at rank 4 | ✓ | ✓ | ✓ | **defer** |
| E6 Neural / foundation-model replacement | ✓ | ✓ | ✓ | ✗ not isolable — it replaces the explanation, importance and contribution machinery at once | ✓ | ✓ | **defer** |
| F1 Operator forecast as an anchor | ✓ daily feed; the half-hourly one too | ✓ | ✓ | ✓ | partly — 538 days for the half-hourly feed | ✓ | **defer** — [#140](https://github.com/hankehly/power-market-analytics/issues/140), and `ftr_day_occto` is excluded from `e212` by ruling |
| F2 Peer-area load | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **pass**, low priority |
| F3 News-text features | ✓ | ✓ | ✓ | ✓ | ✗ no ingested source, no Japanese equivalent | ✓ | **set aside** |
| F4 Pressure-pattern regime label | ✓ | partly — P-096 is a climate-attribution study, not a forecast trial | ✓ | ✓ | ✓ | ✓ | **defer** |
| N1–N8 (§8) | — | — | — | — | — | ✗ their value is a capability | **other lane** |

**Every non-pass, in one line each.**

- **A4** defer: it aggregates several forecasts, which do not exist until A5 is built.
- **B4** under experiment: it is the follow-up the researcher recorded on #221 on 2026-09-21, not a new proposal.
- **B5** defer: `fit_similar_day.py` already learns a seven-weight distance; a clustered or pattern-sequence distance is the same mechanism with a different parameterisation, so it fails the distinction gate as a *separate* experiment.
- **C1** defer: the diagnosis holds but the effect does not transfer — a per-(weekday, holiday) shift fitted on year 1 makes year 2's holidays 16.05 % worse, and the per-weekday bias flips sign between years on three of seven weekdays.
- **C2** set aside: measured row counts fail LightGBM's `min_data_per_group`; this is #143's own reason.
- **C5** defer: data realism. Kansai has `ftr_period_actuals` and `ftr_hour_msm` but `ftr_period_similar_day` is Tokyo-only, so every borrowed row would carry NaN in the feature with ΔMAE 2,030,747.
- **D1** defer: not illegal and not duplicated, but bidding against a measured correlation of 0.060 between our error and the weather forecast's.
- **D5** defer: the normals are published as HTML tables with no download link; ingesting them is [#218](https://github.com/hankehly/power-market-analytics/issues/218)'s scope.
- **D6** set aside: no ensemble NWP is ingested, and the one alternative vintage was already ruled illegal at 09:30 D-1.
- **D7** defer, not set aside: both our tests come back negative — no correlation with forecast irradiance, no trend in the daytime bias — but a stable behind-the-meter contribution would leave both flat, so they lower the priority without settling it.
- **E4** other lane: it changes what the model estimates, which belongs with the quantile work in §8 rather than as a point-MAE experiment; §4.6 also measured that static recalibration of this kind transfers at −0.21 %.
- **E5** defer: `time_code` is already a feature at permutation rank 4, and 48 models on a 730-day window is a large change for a partly-covered mechanism.
- **E6** defer: it is not isolable in the matched backtest — it replaces TreeSHAP, the permutation importance and the contribution facts along with the model.
- **F1** defer: `ftr_day_occto` exists and the researcher ruled it out of `e212`; the half-hourly feed was set aside as #140 for having 538 days.
- **F3** set aside: no ingested source and no Japanese-language equivalent.
- **F4** defer: scope — P-096 assigns winter circulation types to *modelled* demand over 1980–2018 and reports no forecast horizon at all.
## 7. Primary ranking — point-MAE improvements

Ratings are 1–3 (3 best) with the reasons in the rationale column and, for the top
five, in §9. They are **not summed**: the evidence does not support a single score.
The bands below are a Pareto reading — band A interventions are not dominated on any
criterion by anything below them — with the tie-breakers of §3 applied inside each
band.

Abbreviations: **EI** expected impact, **TC** transfer confidence, **RN** residual
novelty, **LV** learning value, **FE** feasibility, **OB** ongoing burden.
### Band A — run these

| # | Cat. | Intervention | Papers | Overlap with `e212` | Segment it targets | EI | TC | RN | LV | FE | OB | Gate | Rationale |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A1 | **Own D-2 forecast residual as a feature** | P-113, P-116, P-128, P-145, P-146, P-154, P-157, P-161, P-034, P-035 | none — no feature is a function of the model's own output | summer and winter regimes; holidays; the day level | 3 | 2 | 3 | 3 | 2 | low | pass | The only candidate whose *fix* is measured on our data: **−1.62 % on a held-out year**, with β selected on the first year alone (0.25) and applied untouched to the second; partial r 0.240 after controlling for the D-2 load level. Papers are weak individually (P-145 beats a *frozen* model; P-157 and P-161 are COVID-era) but the mechanism is confirmed here. §9.1 |
| 2 | E2 | **Correlation-aware feature pruning** | P-003, P-058, P-063, P-025 | the importance machinery exists and runs every backtest; nothing acts on it | whole run; the importance table itself | 1 | 2 | 3 | 3 | 3 | low | pass | 17 of 105 features at ΔMAE ≤ 0, 26 under 500 kWh, median pairwise \|r\| 0.833 in the lag block. A preset with a `drop` list — the cheapest experiment available here, and **both outcomes are informative**. P-058's own gain was 1.6 % relative with the subset size chosen on the test set, so expect ~0. §9.2 |
| 3 | B1 | **Bound the day-type reference window** | P-104, P-113, P-001, P-141, P-106, P-111 | `mean/ewm_daytype_4d_demand_kwh` and `…_weekly_lags` exist but are unbounded in age; #203 added the *age* as a feature | the 2,400 periods whose reference is over a week old | 3 | 2 | 3 | 2 | 3 | low | pass | MAE rises monotonically with reference age, 489,466 → 2,219,186 past 60 days; 6.86 % of periods carry **10.69 %** of the error. #203 proved that telling the model is not enough. P-104 (7.29 → 3.22 % on French special days) replaces the coarse match with a typed one. §9.3 |
| 4 | A5 | **Combine several fits (sister forecasts)** | P-059, P-146, P-025, P-149, P-027, P-001, P-067 | none — every run is a single fit | whole run; and the ensemble's own member spread | 2 | 3 | 3 | 3 | 2 | med | pass | The methodologically cleanest point-accuracy evidence in the corpus: P-059's eight sister models, two systems, Diebold-Mariano, "no combination was ever worse" in 20–22 of 24 hours. Note that it does **not** measure how much of our decision record is noise — §9.4 corrects that claim; the paired daily bootstrap and #223's sealed second window are the instruments for that. §9.4 |
| 5 | B2 | **Correct the similar day for its weather gap** | P-141, P-128, P-113, P-006, P-002, P-104 | #134 specifies exactly this and is open; the raw reference load is present, its conditions are not | holidays; the 54 `same_holiday` days | 2 | 2 | 2 | 3 | 3 | low | pass | Attacks the model's dominant input (ΔMAE 2,030,747, 21.6× the next). #212 measured corr(daily bias, rank-1 gap) = 0.906, slope 0.696 on 54 days. P-141 is the closest operational match in the corpus — real 08:00 forecast weather, and XGBoost and LSTM did *worst* on those days. §9.5 |
### Band B — credible, run after band A

| # | Cat. | Intervention | Papers | Overlap with `e212` | Segment | EI | TC | RN | LV | FE | OB | Gate | Rationale |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 6 | E1 | **Level-normalised target** — predict the ratio to a level anchor, multiply back | P-115, P-120, P-122, P-123, P-032, P-036, P-050, P-055, P-080, P-127 | none — the target is the raw level | the upper half of the demand range | 2 | 2 | 3 | 3 | 2 | med | pass | **Measured both ways.** The forecast cannot exceed the training ceiling at all: 145 periods above it run MAE 1,365,463 with bias −1,359,519. But the shrinkage survives normalisation — in ratio space the bias still runs +0.009 → −0.074 across the bands — so it lifts the ceiling (1.1 % of error) without removing the graded under-prediction (26.9 %). Model-method change, its own experiment. |
| 7 | A2 | **Recency-weighted training rows** instead of a hard 730-day window | P-145, P-154, P-157, P-161, P-035, P-114, P-121, P-129, P-019 | none — the window is flat and hard-edged | regime transitions | 2 | 2 | 3 | 2 | 2 | low | pass | One `sample_weight` argument, the same regime-adaptation mechanism as rank 1 in a cheaper form. Weak measured support here: §4.3 found **no drift** — Q2 and Q4 MAE steady at 350,000–406,000 across three years — so the gain would have to come from within-year regimes, which is what rank 1 already tests more directly. Run it only if rank 1 lands. |
| 8 | C4 | **Separate model per calendar segment** | P-144, P-137, P-158, P-084, P-038, P-134, P-106 | `day_type` and `special_period` are categoricals; one model spans everything | holidays; day types | 2 | 2 | 3 | 3 | 1 | high | pass | P-144's gains are large (MAPE 6.47 → 4.03 % and three more systems) but one test year, no weather, and its own appraisal flags duplicated columns. P-106's counter-evidence is the stronger point: **dropping holidays from training made holidays worse**, over five test years. A holiday-only model would fit ~2,784 rows. |
| 9 | C5 | **Cross-area holiday transfer** — borrow Kansai's rows | P-135, P-160, P-081 | none | holidays, 13.14 % of the total error | 2 | 2 | 3 | 3 | 1 | high | **defer** | Treats the measured cause — 5 holidays at ≥ 31.5 °C in the 730-day window against 57 weekdays — by adding *rows* rather than another column, which the record argues for (four of five calendar-feature additions were rejected). **Blocked:** `ftr_period_similar_day` holds 130,944 rows, all Tokyo, so every borrowed row would carry NaN in the model's most important feature. Needs `fit_similar_day.py --area kansai` first. |
| 10 | E3 | **DART boosting** | P-003 | none | whole run | 1 | 1 | 3 | 2 | 2 | low | pass | *Mandated for assessment.* One LightGBM parameter, fully isolable, and it must not share a run with pruning or reconciliation. But P-003's 3.24 % → 2.83 % is its **qualification** round, which used **realized future temperatures**, and the team placed sixth; no isolated finals figure is given. Cheap enough to run on curiosity, not on evidence. |
| 11 | D2 | **MOS bias-correction of the MSM forecast** | P-118, P-119, P-121, P-048, P-044, P-043 | raw MSM values only; no correction layer | heat waves and cold snaps | 2 | 2 | 3 | 2 | 2 | med | pass | §4.5 measured corr(daily bias, temperature forecast error) = 0.060, which discounts this heavily **on average** — but #212 found a −1.29 °C cold bias running through a whole heat wave and attributed part of its worst day to it. So the case is conditional, not average, and the experiment must be framed on the extremes. |
| 12 | D3 | **Lagged forecast temperature within day D** | P-092, P-098, P-026, P-063 | observed recency is covered (24 h / 72 h windows, EWA); the *forecast* hours before the target hour are not | daytime and evening, summer and winter | 2 | 1 | 2 | 2 | 3 | low | pass | Already specified as [#204](https://github.com/hankehly/power-market-analytics/issues/204), open and unbuilt, with its own measured evidence in the issue. P-092's 5.22 → 4.27 % is against a *linear* benchmark, on realized temperature, with the best lag pair chosen in hindsight — one of the most discounted headline numbers in this review. |

### Band C — assessed, not recommended now

| Cat. | Intervention | Papers | Gate | Why not |
|---|---|---|---|---|
| D1 | **Weather-station selection or re-weighting** | P-095, P-097, P-099, P-128 | **defer** | §4.5: corr(our daily bias, MSM temperature forecast error) = 0.060. We already population-weight every station in the area. P-095's own gain was 7.00 → 6.96 % MAPE; P-097 picked K on the test set; only P-099 uses real forecast temperature, and it re-fits 24 Reg-ARIMA models, not a GBM. Re-opens if the correlation re-tests materially higher (§13). |
| D6 | **Weather-ensemble NWP features** | P-086, P-087, P-089, P-155 | **set aside** | We ingest one deterministic MSM run. There is no ensemble in the pipeline, and [#141](https://github.com/hankehly/power-market-analytics/issues/141) already established that no *later* MSM vintage is legal at 09:30 D-1. P-155 also reports that its gridded features "added nothing reliable". |
| D7 | **Behind-the-meter PV reconstruction** | P-137, P-141, P-162 | **defer** | §4.5: error against population-weighted forecast irradiance gives daytime r = 0.018, and the daytime bias shows no trend across four half-years. Both tests are negative — but a *stable* hidden PV level would leave both flat, so they deprioritise rather than refute. A capacity-registry estimate would test it properly. |
| C2 | **Holiday name / fine special-day categorical** | P-004, P-111, P-113, P-104, P-054 | **set aside** | [#143](https://github.com/hankehly/power-market-analytics/issues/143), 2026-09-15: a holiday name gives 48–96 rows per level against LightGBM's `min_data_per_group` of 100 (probed on the installed 4.7.0), so the tree never splits on it. P-111 also needs ≥ 7 years of training data; we run a 730-day window. |
| C1 | **Weekday × holiday re-encoding** ("replacing dummies") | P-106 | **defer** | The diagnosis is solid — SHAP shows the model applying an unmodified weekday effect on holidays (§4.3) — and P-106 is the cleanest holiday study in the corpus. But the effect does not transfer: a per-(weekday, holiday) shift fitted on year 1 makes year 2's holidays **16.05 % worse**, and the per-weekday bias flips sign between years on three of seven weekdays on 3–4 observations each. Needs a stable estimate first. |
| — | **Longer or season-matched training window** | P-090, P-091, P-094, P-004, P-005, P-062, P-022 | **defer** | Aimed at the same scarcity as #9, and the only measured support is indirect (§4.7). It also multiplies every future run's cost and interacts with rank 2 and #7, so it should follow them rather than precede them. |
| — | **Temporal reconciliation to a daily total** | P-153, P-031, P-003 | **other lane** | Two independent reads agree it buys coherence, not point accuracy: P-003 measured MAPE −0.05 … −0.75 % against CRPS −4.49 … −5.16 %, and P-153's RMSE −44 % rests on a weather-free double-seasonal base its own appraisal flags. → §8 N6. |
| F3 | **News-text features** | P-068 | **set aside** | A new source with no Japanese-language equivalent in the pipeline, a heavy standing ingestion burden, and evidence for a different system. |
| E6 | **Neural / foundation-model replacement** | P-074, P-075, P-078, P-079, P-080, P-082, P-084, P-139 | **defer** | Not an experiment: it replaces the model, the explanation machinery (TreeSHAP), the importance machinery and the contribution facts at once. P-067 also measured XGBoost as *no better* than a GAM on Portuguese national load (RMSE 199 vs 191 MW), so the class change is not self-evidently an upgrade. |
| B5 | **Pattern-sequence / clustering similar day** | P-100, P-101, P-102, P-103, P-110, P-112 | **defer** | The same mechanism as our similar-day job reached by a different route. Distinct only in the distance's construction, which is what rank 5 and the `fit_similar_day.py` weights already parameterise. |
| — | **Daylight / sunrise-sunset features** | P-108 | **set aside** | P-108's own result: the AR model gained (sunrise MAPE 1.33 → 1.21 %) but **the neural model gained nothing**, and the combination moved 1.21 → 1.20 %. `day_of_year` (permutation ΔMAE 36,216, rank 6) already carries the annual daylight cycle for a non-linear model. |
| — | **24 solar terms / finer seasonal calendar** | P-105 | **set aside** | System-total MAPE 3.28 → 3.22 %, not a day-ahead test, no significance test. `day_of_year` and `month` cover it; [#217](https://github.com/hankehly/power-market-analytics/issues/217) covers the one Japanese-specific case (梅雨) as its own investigation. |
| F1 | **OCCTO forecast as a feature or anchor** | P-057, P-055, P-053 | **defer** | The daily feed is a mart (`ftr_day_occto`) the researcher ruled out of `e212`; the half-hourly one was set aside as [#140](https://github.com/hankehly/power-market-analytics/issues/140) for having only 538 days. Its measured skill is relevant, though — OCCTO's own forecast runs MAE 392 MWh against our 565 on the recorded comparison — so this is a deferral, not a dismissal. |
## 8. Separate ranking — new forecasting capabilities

These are ranked on their own terms. None is penalised for not lowering MAE; none is
credited with a point-accuracy gain it does not have. Today the system emits a point
forecast only, scored on MAE, so every row here is a capability the repository does not
have at all.

| # | Capability | Papers | What it adds | Evidence quality | Feasibility | Burden | Gate |
|---|---|---|---|---|---|---|---|
| N1 | **Quantile forecasts from the existing strategy** — refit the same features under LightGBM's `quantile` objective at several τ | [P-032](research/literature-review.md#p-032), [P-150](research/literature-review.md#p-150), [P-156](research/literature-review.md#p-156), [P-063](research/literature-review.md#p-063), [P-030](research/literature-review.md#p-030) | a predictive distribution per period, scored by pinball loss | Medium. Quantile regression for load is thoroughly established; P-156's load results are shown as curves only and use observed temperature, P-063's is linear and leaky | 2 — the features, retrieval and training plumbing are reusable; **the downstream layers are not** — a new strategy class, a quantile-keyed `pma_ml` table (the current one holds one value per period, not one per τ), pinball and coverage metrics, a calibration check, and fan-chart dashboards | medium | other lane |
| N2 | **Calibrated prediction intervals** — the quantiles above plus a coverage check and a reliability diagram | [P-148](research/literature-review.md#p-148), [P-156](research/literature-review.md#p-156), [P-046](research/literature-review.md#p-046), [P-147](research/literature-review.md#p-147), [P-029](research/literature-review.md#p-029) | stated coverage a user can rely on | Medium-high. [P-147](research/literature-review.md#p-147) shows why it matters: real operator errors are peaked, heavy-tailed and biased low, so a Gaussian interval is wrong, and our own kurtosis is 4.04 | 2 — an evaluation and a chart on top of N1 | low | other lane |
| N3 | **Probabilistic combination of several models** — CQRA or QRA over sister forecasts | [P-151](research/literature-review.md#p-151), [P-149](research/literature-review.md#p-149), [P-027](research/literature-review.md#p-027), [P-028](research/literature-review.md#p-028) | a better distribution than any single model's | Medium. P-151 is clean across 9 ISO-NE series (pinball −4.39 % average) but never states its models' inputs; P-149 replaces the temperature forecast with the realized temperature, which flatters it against the one ex-ante benchmark | 2 — needs N1 first, then several runs | medium | other lane |
| N4 | **Joint day-ahead distribution of demand and renewables** | [P-162](research/literature-review.md#p-162), [P-155](research/literature-review.md#p-155) | reserve sizing from the joint tails, not from demand alone | **Highest in this lane.** P-162 uses real HRRR forecasts, nine test months, proper scoring rules, and beats CAISO's own operational forecast by ~25 % RMSE; P-155 uses ECMWF-HRES day-ahead forecasts with p<0.001 | 1 — a second forecast target. We hold wind and solar generation in `fct_area_demand_generation_actual` (the `LAG(wind_solar_generation_kwh, …)` features read it), so the data exist, but a renewables model does not | high | other lane |
| N5 | **Daily peak magnitude and timing as its own forecast** | [P-036](research/literature-review.md#p-036), [P-037](research/literature-review.md#p-037), [P-038](research/literature-review.md#p-038), [P-056](research/literature-review.md#p-056), [P-114](research/literature-review.md#p-114), [P-117](research/literature-review.md#p-117), [P-021](research/literature-review.md#p-021), [P-046](research/literature-review.md#p-046) | when the day's peak falls and how large it is, as a product in its own right | Medium-high. The BigDEAL Challenge 2022 was built on exactly this: [P-038](research/literature-review.md#p-038) ran three U.S. utilities over 2018 in six rolling rounds on **real day-ahead temperature forecasts**, and found rank on peak size largely unrelated to rank on timing (its Fig. 12) — so it is genuinely a different target, not a read-off. [P-037](research/literature-review.md#p-037) reaches ≈1 hour MAE on peak timing. Caveats: one region, one test year, and P-036/P-037 pick their best variant with hindsight | 3 — `ftr_day_actuals` already carries `LAG(DAILY_ARGMAX(demand_kwh), 2d)` and `ftr_day_msm` the forecast temperature's argmax, so the inputs exist; the peak can be read off the 48-period forecast as a baseline to beat | low | other lane |
| N6 | **Temporal / hierarchical coherence** — reconcile the 48 periods with the daily total | [P-153](research/literature-review.md#p-153), [P-031](research/literature-review.md#p-031), [P-152](research/literature-review.md#p-152), [P-003](research/literature-review.md#p-003) | forecasts that add up across resolutions, and better interval scores | **Weak for point accuracy, real for coherence.** Two independent reads agree: [P-003](research/literature-review.md#p-003) measured reconciliation at CRPS −4.49–5.16 % and interval score −11.3 % but **MAPE only −0.05–0.75 %**, and [P-153](research/literature-review.md#p-153)'s headline RMSE −44 % rests on a base of weather-free double-seasonal smoothing, which its own appraisal names | 2 | medium | other lane |
| N7 | **Cost-oriented objective** | [P-159](research/literature-review.md#p-159), [P-088](research/literature-review.md#p-088), [P-070](research/literature-review.md#p-070), [P-057](research/literature-review.md#p-057) | forecasts tuned to what an error costs, not to squared error | Low for us, and explicitly a trade: [P-159](research/literature-review.md#p-159) cut mean cost 10.19–13.74 % while **MAPE rose from 5.36 % to 5.49 %**, on one simulated IEEE 30-bus system with simulated costs | 1 — **blocked on data.** It needs an error-cost curve, which needs an imbalance-price feed or a dispatch model. We have JEPX spot prices and neither of those | high | **defer** — not testable here until a cost model exists |
| N8 | **Adaptive probabilistic forecasting** — online updates to a predictive distribution | [P-154](research/literature-review.md#p-154), [P-161](research/literature-review.md#p-161) | a distribution that follows a changing regime | Medium, heavily COVID-confounded: [P-161](research/literature-review.md#p-161)'s test period is 2019–2021 and [P-160](research/literature-review.md#p-160)'s case 2 gains come mostly from the lockdown break. Our window has no regime break — Q2 and Q4 MAE is steady at 350,000–406,000 across three years (§4.3) | 1 — needs N1 plus the online machinery | high | other lane |

**Recommendation for this lane: N1, then N2.** They are one capability delivered in two
steps, and N2 is what makes N1 usable rather than merely available. What they reuse is
the **feature and training plumbing** — the feature marts, Feast retrieval, the preset
mechanism, the sliding-window refit — and that is the whole of the reuse claim. The
layers *downstream* of the model all need new work: a quantile-keyed write-back (the
`pma_ml` forecast table has one value per period, not one per τ), pinball loss and
coverage as metrics beside MAE, a calibration check, and dashboard support for a fan
rather than a line. §8's own row for N1 says "a new strategy class and a `pma_ml`
table"; that is the honest scope, and an earlier draft of this paragraph overstated it
as "the whole existing pipeline unchanged". N4 is the most valuable capability in the lane
and has the best evidence behind it, but it is a second forecasting task, not an
experiment, and it should not be started before N1 exists. N5 is the cheapest of the
eight — the inputs are already in `ftr_day_actuals` and `ftr_day_msm`, and the peak read
off the current 48-period forecast is the baseline to beat — so it is the one to reach
for if a capability is wanted sooner than N1 can be built.

*Inferred:* N1 also has a side benefit the point-MAE lane should note. Fitting at
τ = 0.5 estimates the conditional **median**, which is the MAE-optimal point predictor,
while the current L2 objective estimates the conditional **mean**. §4.3 measured the
forecast sitting below the conditional median at high demand (median error −179,501 in
the top forecast decile). That makes a τ = 0.5 refit a legitimate *point-MAE* candidate
too — but a weak one, because §4.6 measured that static recalibration of exactly this
kind transfers at −0.21 %, and at +1.91 % when conditioned on season. The median
objective is worth running as part of N1, not as a point-MAE experiment of its own.

## 9. Detailed rationale — the top five

### 9.1 Rank 1 — Feed the model its own D-2 forecast residual

Papers: [P-113](research/literature-review.md#p-113),
[P-116](research/literature-review.md#p-116),
[P-128](research/literature-review.md#p-128),
[P-145](research/literature-review.md#p-145),
[P-146](research/literature-review.md#p-146),
[P-157](research/literature-review.md#p-157),
[P-161](research/literature-review.md#p-161),
[P-034](research/literature-review.md#p-034),
[P-035](research/literature-review.md#p-035),
[P-154](research/literature-review.md#p-154).

**The falsifiable hypothesis.** Giving the model the D-2 residual of a **fixed reference
predictor** — the similar-day top-3 mean against that day's actual, public at D-1 09:30
and absent from all 104 features — lowers Tokyo MAE by at least 0.5 %, with a paired
daily bootstrap CI over days that excludes zero, and the gain concentrates in the hot
and cold regimes rather than the shoulder seasons.

The threshold is 0.5 %, not 1.0 %, and the predictor is a fixed reference rather than
the model itself. Both follow from what is buildable: the model's own residual measures
better (−1.62 % against −1.04 % held out) but cannot cover a training window on this
data, for the reason given under *the smallest valid change* below.

**Why this, not the others.** It is the only intervention in this review with a
*measured* effect on our own residual rather than a discounted number from another
system (§4.6): with β selected on the first year alone (0.25) and applied untouched to
the second, it gives **−1.62 % on a held-out year**. The partial
correlation with D's daily bias, controlling for the D-2 load level the model already
has as a feature, is **0.240** — so it is new information, not a re-encoding of a lag.
And the contrast is sharp: a static per-decile calibration shift transfers at −0.21 %
and a per-decile × season shift at **+1.91 %**. Static bias correction fails; tracking
the current regime works. Nothing else on the list has that combination.

The strongest paper number belongs to the same family and comes from a clean setting.
[P-034](research/literature-review.md#p-034) is the **winning** entry of the IEEE
DataPort post-COVID day-ahead competition, scored 16–40 h ahead on **day-ahead forecast
weather**, and it reports that "an intraday autoregressive correction cut the neural
net's MAE by 57 %". Two caveats belong with it: its correction loop is *intraday*,
shorter than our D-2, and it was built for a regime break our window does not contain
(§4.3 measured Q2 and Q4 MAE steady at 350,000–406,000 across three years). The
mechanism transfers; that magnitude does not, which is why the Keep threshold below is
set from our own measurement and not from theirs.

It also targets where the error actually is. §4.2 measured that 35.7 % of the MAE is
the day's overall level and the within-day error autocorrelation is 0.955; the D-2
*daily mean* residual predicts D's error better than the same period's D-2 residual
(r 0.197 against 0.166; corrections −2.19 % against −1.53 %), so the feature is one
day-grain column, not 48.

**The smallest valid repository change.** Not a wrapper around the walk-forward.
[`run_backtest`](power_market_analytics/forecasting/backtest.py) forecasts only
`[start_date, end_date]`, while each fit's window is `target_date − 730 days`
([lgbm.py](power_market_analytics/forecasting/lgbm.py)), so a column holding *the run's
own* residual would be NaN across the whole first training window and fill in only
gradually. The model would get no splits on it in training and then meet a live value at
prediction time. That is not the prespecified signal.

The design that does work is the repository's existing Form B pattern, the one
[`fit_similar_day.py`](scripts/fit_similar_day.py) already uses: **persisted historical
forecasts**. `pma_ml.demand_forecast` holds every published run's row-level forecasts, so
a job can write the D-2 residual per delivery day to `pma_ml.<feature>`, a guarded
staging model can read it, and a day-grain mart column can expose it to Feast with
`available_at` = 00:30 on D-1.

**But that design cannot cover a training window, and no pre-roll fixes it.**
`demand_backtest.py` retrieves features from `start_date − (DEFAULT_TRAIN_WINDOW_DAYS +
1)`, so a candidate whose evaluation opens on #223's 2024-04-01 needs residuals from
2022-04-01. No run can supply them: the demand target itself starts 2022-04-01 and the
sliding 730-day training window consumes the two years before it, which is why
[#223](https://github.com/hankehly/power-market-analytics/pull/223) opens its window at
2024-04-01 — *the first day that can be scored at all*. A model-residual column is
therefore null across the whole first fit and fills in only as the walk advances, from
0 % coverage to 100 % by the last fit. That is a structural limit of the design, not a
scheduling oversight, and it is why the next paragraph is the primary proposal rather
than a fallback.

**So the buildable design is the residual of a fixed reference predictor.**
`wavg_similar_day_top3_demand_kwh` or `LAG(demand_kwh, 7d)` against the actual on D-2 —
a pure mart column, no recursion, no source run, defined for every historical day. It is
what [P-128](research/literature-review.md#p-128)'s "nearby date comparison value"
actually is. **Measured** on 2026-09-21, same method as §4.6 and same split: the
similar-day reference's D-2 residual correlates 0.123 with our daily bias (against 0.249
for the model's own), β = 0.12 wins on year 1, and applying it untouched to year 2 gives
**497,739 → 492,580, −1.04 %**.

| variant | corr with D's bias | held-out gain | buildable today |
|---|---|---|---|
| the model's own D-2 residual | 0.249 | −1.62 % | **no** — needs ~2 years of forecast history the data cannot provide |
| the similar-day reference's D-2 residual | 0.123 | **−1.04 %** | **yes** — one mart column |

Two thirds of the gain, for a fraction of the work and none of the coverage problem.
That is what rank 1 proposes; the model-residual version is the follow-on once enough
forecast history has accumulated for a training window to be covered, which on the
pinned window is not before about 2028.

**The matched baseline.** The fresh pinned-window `e212` run from step 0 — same
`--train-start`, same 104 features plus the one column. This is the one step whose
baseline is `e212`, because it is first; §11's advance rule governs the rest. Before #223 lands that would be
`943aab6d21b14fe2877169dabc5d694b`, the `e212` preset on the old window against the
*current* similar-day partition, and not the `34c506fb…` of
[demand/README.md](research/demand/README.md) (§12 item 6 explains the 0.13 %
difference). Rank 1 is the one step whose baseline is `e212`; §11 explains why every
later step's is whatever survived before it.

**The important segments.** Season × day type, because the held-out gain is
concentrated there and is not uniform: Holiday DJFM −4.53 %, Weekend JJAS −2.65 %,
Weekday JJAS −2.60 %, Weekday shoulder −1.59 %, Weekend DJFM −0.06 %, and Holiday
shoulder **+4.40 %**, which is worse. A run reproducing the overall −1.6 % without that
shape has not reproduced the mechanism. Also the top-10 %
demand days, where the bias is −433,641, and the by-month table, because the shape of
the gain is as much the result as its size.

**The decision rule, set before the run.**

| Evidence | Decision |
|---|---|
The branches are read in order and are mutually exclusive on ΔMAE, so every outcome has
exactly one verdict.

| ΔMAE (candidate − baseline, relative) | CI over days | Verdict |
|---|---|---|
| **≤ −0.5 %** | excludes zero | **Keep.** It becomes the baseline, the online-adaptation family (P-145, P-146, P-154, P-157, P-161) earns its own investigation, and the model-residual version becomes a scheduled follow-on rather than an assumption. |
| **≤ −0.5 %** | includes zero | **Refine.** The size is there and the evidence is not; re-run on more days, or restrict the column to the hot and cold regimes where §4.6 measured the gain. |
| **−0.5 % < ΔMAE < −0.1 %** | either | **Refine.** Directionally right, too small to carry a column. Try the residual as a post-processing level shift instead of a feature — but see risk 1 on the additivity test. |
| **−0.1 % ≤ ΔMAE ≤ +0.3 %** | either | **Reject**, and with real force: the buildable member of the online-adaptation family failed on a signal measured at −1.04 % out of sample, which says the model absorbs it through its lags once it can fit freely. The family drops to the bottom of the point-MAE lane. |
| **> +0.3 %** | either | **Reject.** Most likely the extra column displaced a correlated feature; note which and stop. |

A gain that appears only in the shoulder seasons is a Refine whatever its size, because
§4.6's held-out segment shape is part of the hypothesis: the mechanism is a regime
effect, and a gain in the wrong regime is a different phenomenon wearing its clothes.

**Dependencies and risks.**

1. *The contribution additivity test.* `fct_demand_forecast_contribution` has a singular
   test asserting Σ contributions = the forecast within 1e-6. A post-processing shift
   applied outside the model breaks it; implementing the residual as a **feature**
   keeps TreeSHAP additive and is the reason to prefer that form.
2. *Nulls at the edges, and they must stay null.* The reference-predictor column is
   defined wherever the similar-day mart has a value, which is every day of the window,
   so it has none of the model-residual variant's coverage problem. Where it is missing
   it must be null rather than zero — a zero says "no error" — and LightGBM's NaN
   handling has been the repository's rule since PR #110. The run should still report
   what fraction of training rows carry the feature, because that number is the whole
   difference between this variant and the one deferred.
3. *This tests a reference predictor's error, not the model's own.* Two gaps follow and
   both are stated rather than assumed. First, the measured signal is weaker — corr 0.123
   against 0.249, held-out −1.04 % against −1.62 % — so a null result does **not** close
   the model-residual version, only this proxy for it. Second, production's loop is
   self-referential in a way neither variant reproduces: a correction applied on D-2
   changes the residual fed on D. The reference predictor is immune to that (it does not
   learn), which makes it the cleaner test of *whether regime feedback helps at all*, and
   a worse model of what the deployed system would do.

   So a Keep says "a D-2 error signal carries usable information at the 09:30 D-1
   cutoff", which justifies the model-residual follow-on. A Reject is weaker than it
   would be for the model's own residual, and the table above says so by naming the
   −1.04 % rather than the −1.62 % in its Reject branch.
4. *It could be absorbed.* The model has 104 features including seven demand lags. If
   the tree can already reconstruct the regime from those, the column adds nothing. That
   is exactly what the −0.1 % … +0.3 % branch of the decision rule is for, and it is the
   honest principal risk.

**Why it precedes rank 2.** Pruning is cheaper — a preset, no code — but it is
value-neutral by design: its best outcome is "MAE unchanged, the preset is leaner".
Rank 1 is the only candidate with measured evidence of a real MAE gain, and its result
re-ranks a whole family of literature (P-145, P-146, P-154, P-157, P-161) either way.
Running it first means step 2 onward is decided with that knowledge.
### 9.2 Rank 2 — Correlation-aware feature pruning

Papers: [P-003](research/literature-review.md#p-003),
[P-058](research/literature-review.md#p-058),
[P-063](research/literature-review.md#p-063),
[P-025](research/literature-review.md#p-025).

**The falsifiable hypothesis.** Dropping the features whose permutation ΔMAE is ≤ 0,
and then one representative per high-correlation cluster, does not raise Tokyo MAE on
2024-08-18 … 2026-08-17 — and may lower it, because a 730-day window fitting 105
correlated columns is spending splits on noise.

**Why here.** The measured case is entirely ours (§4.4): **17 of 105** features have
ΔMAE ≤ 0 — `half`, `quarter`, `LAG(day_type, 2d)`, `is_business_day`,
`fiscal_quarter`, `day_of_month` among them — and **26 of 105** are under 500 kWh,
which is 0.1 % of MAE. Inside `ftr_period_actuals`' 16 demand-level columns the median
pairwise |r| is 0.833, 31 % of pairs exceed 0.90 and 14 % exceed 0.95. The paper
evidence is real but thin: [P-058](research/literature-review.md#p-058) pruned 243
candidates to 33 day-ahead and moved MAPE 1.773 % → 1.745 %, a 1.6 % relative gain, on
one city in one year with the subset size chosen on the test set.
[P-003](research/literature-review.md#p-003) uses clustered permutation selection as
standard practice and reports no isolated figure for it.

**The smallest valid repository change.** One preset, `base: <current>` with a single
`drop` list — no code, no mart, no new source, no new test — and **one run, one
decision**, per the research README's batch rule. The list combines both pruning
candidates, because they are two expressions of one mechanism:

- the 17 features with permutation ΔMAE ≤ 0, and
- within each |r| > 0.95 cluster of §4.4, everything but the highest-ΔMAE
  representative.

An earlier draft proposed running these as two experiments. That was wrong: it is the
per-candidate queue slot the README forbids, and it costs a second baseline run for no
extra information. The combined result is also the more useful one — MAE holding with
both the inert and the duplicated columns gone is a stronger statement than two separate
nulls. **Only if the combined run fails** does a follow-up isolate which half carried
the loss, and that follow-up is then justified by a result rather than scheduled in
advance.

Note what the list costs in independence: it is chosen from `34c506fb…`'s own importance
table, on the same days the comparison would score, so this is a development result by
construction. Selecting it on one window and testing on another — which #223 makes
possible — is the stronger form, worth the extra run if the result lands close to the
bound.

**The matched baseline.** Whatever preset is current when this runs — `e212` if rank 1
was rejected, the rank-1 preset if it was kept — on a run of its own. Not a shared arm:
§11 explains why the baseline advances on every Keep.

**The important segments.** Overall MAE and the CI first, because the hypothesis is a
non-inferiority bound. Then the by-month table, because a pruned model that is flat
overall but worse in August and better in November has not lost nothing. Then the
permutation importance of the survivors — with the clusters collapsed that is a
deliverable of the experiment, not a diagnostic.

**The decision rule, set before the run.**

| Evidence | Decision |
|---|---|
| The **upper** bound of the CI on relative MAE change is below **+0.3 %** | **Keep the pruned preset.** This is a non-inferiority test, not a null one: a CI containing zero only says we failed to detect a difference, which is not the same as showing the harm is bounded. The win is the same accuracy on fewer columns, an importance table that means something, and a precedent that the ΔMAE ≤ 0 list can be acted on. |
| The CI contains zero but its upper bound exceeds +0.3 % | **Inconclusive**, and say so rather than reading it as a pass. The window is too small to bound the harm; re-run on the pinned window or widen the drop list so the effect, if any, is larger than the noise. |
| MAE falls, CI excludes zero | **Keep**, and pruning becomes standing practice after every batch experiment. |
| The CI's **lower** bound exceeds +0.3 % | **Reject**, and it is worth as much: it says permutation importance on this correlated feature set does not identify removable columns, which closes P-003's and P-058's selection mechanism for us and stops a recurring temptation. |

**Dependencies and risks.**

1. *Permutation importance understates a correlated group.* Shuffling one member of a
   cluster leaves its twins to carry the signal, so a low ΔMAE can mean "duplicated",
   not "useless". That is precisely why the list drops by **cluster** as well as by
   rank: a rank-only drop could mislead, which is the other reason the two belong in one
   preset rather than in two runs.
2. *The dominant feature must not be touched.* `wavg_similar_day_top3_demand_kwh`
   carries ΔMAE 2,030,747, 21.6× the next. It is not in the drop list, and a run that
   removed it would measure something else entirely.
3. *Published presets are never edited.* A pruned set is a new preset file named after
   its experiment issue, per the naming rule; `e212` stays as it is.

**Why it precedes rank 3.** It is the only candidate in the top five that needs no
Python, and both of its outcomes are informative — which is rare. It also changes how
every later experiment is read: if the drop is non-inferior, the next batch's added
columns are judged against a lean baseline instead of against 104 columns of which a
quarter are measurably inert.
### 9.3 Rank 3 — Bound the day-type reference window

Papers: [P-104](research/literature-review.md#p-104),
[P-113](research/literature-review.md#p-113),
[P-001](research/literature-review.md#p-001),
[P-141](research/literature-review.md#p-141),
[P-106](research/literature-review.md#p-106),
[P-111](research/literature-review.md#p-111).

**The falsifiable hypothesis.** `ftr_period_actuals`' day-type-conditioned features take
the four most recent complete days of D's `day_type` with **no bound on how far back
they may reach**. Giving the model a bounded alternative — or a typed reference in the
sense of [P-104](research/literature-review.md#p-104) rather than a raw `day_type`
match — lowers MAE on the periods whose window is stale, without raising MAE overall.

**Why here.** This is the most localised measured weakness in the baseline. Joining the
run's errors to `newest_daytype_4d_lag_days`:

| day-type window staleness | periods | MAE | bias |
|---|---|---|---|
| ≤ 7 days | 32,602 | 489,466 | −32,229 |
| 8–14 days | 1,152 | 650,973 | +139,612 |
| 15–30 days | 720 | 819,693 | −157,039 |
| 31–60 days | 432 | 825,395 | +281,776 |
| **> 60 days** | 96 | **2,219,186** | **−2,033,482** |

MAE rises **4.5×** as the reference ages past 60 days, monotonically. The 2,400 periods
whose window is older than a week are **6.86 % of the run carrying 10.69 % of the total
absolute error**. The worst bucket is two days — 海の日 2025 and 2026 — which is
exactly [#212](https://github.com/hankehly/power-market-analytics/issues/212)'s finding
2: "the day-type recent-load window was 76 days stale … `ewm_daytype_4d_demand_kwh` fed
the model 22,659 MW against a 36,993 MW truth, 40 % low … `mean_weekly_lags_demand_kwh`
sat at 37,108 MW, within 0.3 % of the actual. The right number was in the feature set;
the holiday branch talked the model out of it."

**The crucial point: telling the model is not enough.**
[#203](https://github.com/hankehly/power-market-analytics/issues/203) already added
`newest_daytype_4d_lag_days` and `oldest_daytype_4d_lag_days` to `e212` precisely so
the model could see the staleness. `oldest` sits at permutation ΔMAE 4,759, rank 27 of
105 — used, but nowhere near enough to undo a reference that is 40 % low. The reference
itself has to change.

The literature says the same thing in its own vocabulary.
[P-104](research/literature-review.md#p-104) does not match on a coarse day type at
all: its rule sorts special days into **seven types**, each pointing at a matching past
day, and on French special days that took MAPE from 7.29 % to 3.22 % at 22–24 hours.
[P-113](research/literature-review.md#p-113) points a special day at *the same special
day a year earlier* and cut error 16–30 % on special days across five Dutch provinces.
[P-001](research/literature-review.md#p-001)'s rules roughly halved special-day MAPE.
All three replace "the most recent days that happen to share a coarse label" with "a
deliberately chosen comparable day".

**The smallest valid repository change.** Columns in `ftr_period_actuals` beside the
existing ones: the same day-type means over a **bounded** lookback (the nearest days of
D's type within, say, 35 days, null beyond), so the model has both the unbounded
reference and one that admits when it has nothing recent to say. A mart change plus a
preset — no new source, no code, no new table.

**The matched baseline.** The preset current when this runs, on a run of its own — see
§11 on the baseline advancing after every Keep.

**The important segments.** The staleness bands above, reported exactly as the table —
they are the hypothesis. Then holidays, since staleness and holidays coincide. Then
overall MAE, which must not rise.

**The decision rule, set before the run.**

| Evidence | Decision |
|---|---|
| MAE falls in the > 14-day staleness bands, overall MAE flat or lower, CI over days excludes zero | **Keep.** And the typed-reference family ([P-104](research/literature-review.md#p-104), [P-113](research/literature-review.md#p-113), [P-001](research/literature-review.md#p-001)) becomes the natural follow-on. |
| The stale bands improve but overall MAE rises | **Refine** — the bound is right, its length is wrong. Report it as a segment gain with an overall cost, not as a win. |
| Nothing moves | **Reject**, with a specific conclusion: the model can already discount a stale reference using the two lag-age columns it holds, and the whole "bound or type the reference" family is closed for this architecture. That is worth knowing, because it is the third distinct way of attacking the same 2,400 periods. |

**Dependencies and risks.**

1. *A bound creates nulls where the reference is most needed.* On a holiday with no
   recent same-type day the bounded column is null by construction — which is the point
   (LightGBM gets a missing-value branch rather than a 40 %-low number), but it means
   the experiment partly tests whether NaN beats a bad value. Say so in the hypothesis
   rather than discovering it afterwards.
2. *It overlaps the similar-day family.* The similar-day job is the sophisticated answer
   to "find a comparable past day"; the day-type window is the crude one. If rank 5
   lands, some of this gain may already be taken. Run them in the stated order and
   compare against whichever preset is current.
3. *R-005's record.* Two more columns on a 104-column preset, against four measured
   calendar-feature rejections. Judged on overall MAE first.
4. *The window length is a free parameter.* Pick it before the run from the staleness
   distribution, not after from the result — otherwise this repeats the very failure
   §11's step 0 is about.

**Why it precedes rank 4.** It is feasibility 3 against rank 4's 2, it costs one run
against rank 4's several, and its target is measured and localised — 10.69 % of the
error in 6.86 % of periods, with a monotone dose-response. Rank 4's expected gain is
[P-059](research/literature-review.md#p-059)'s 0.1–0.2 MAPE points; this one has a
bucket running at 4.5× the baseline MAE.
### 9.4 Rank 4 — Combine several forecasts instead of choosing one

Papers: [P-059](research/literature-review.md#p-059),
[P-146](research/literature-review.md#p-146),
[P-025](research/literature-review.md#p-025),
[P-149](research/literature-review.md#p-149),
[P-027](research/literature-review.md#p-027),
[P-001](research/literature-review.md#p-001),
[P-067](research/literature-review.md#p-067).

**The falsifiable hypothesis.** Averaging several `e212` runs that differ only in
training seed and feature subsample lowers MAE relative to any single run, and shrinks
the run-to-run spread that currently makes a 0.3 % margin unreadable.

**Why here.** This is the one candidate aimed at *variance* rather than at bias, and
the ledger has just measured why that matters.
[#221](https://github.com/hankehly/power-market-analytics/issues/221) concluded on
2026-09-21: "Picking by point estimate would have kept `e221` on a 0.3 % margin that
the segment evidence shows is variance." Every keep/reject in this repository is read
off a single run of each arm. If a combination both lowers MAE and narrows the spread,
it improves the forecast *and* the instrument used to judge every future forecast.

The evidence is also the methodologically cleanest point-accuracy evidence in the
corpus. [P-059](research/literature-review.md#p-059) built eight sister regressions
differing only in variable selection and training length, on two systems (GEFCom2014
and ten ISO New England zones), refit daily, tested by MAPE **and Diebold-Mariano**:
trimmed averaging gave 4.52 % against the best individual's 4.74 %, ISO-NE zone 10
2.10 % against 2.24 %, the three best schemes won in 20–22 of 24 hours, and **no
combination was ever worse**. [P-146](research/literature-review.md#p-146) reaches the
same conclusion online with regret bounds (Slovakia RMSE 27.8 against 30.4 for the best
single expert; France 0.623 GW against 0.782), and
[P-035](research/literature-review.md#p-035) — third in the post-COVID competition, on
**forecast** weather — found that combining five models cut the best single model's
validation MAE by about 10 %.

**The smallest valid repository change.** `SlidingWindowLightGbmStrategy` already keeps
every refit. The minimal version fits *k* models per refit and averages their
predictions — a change inside the existing strategy, no new mart, no new source, no new
table. Start at k = 5.

**The diversity has to be switched on, or there is none.** Probed on the installed
LightGBM 4.7.0: with the defaults in `LGBM_PARAMS`, changing `random_state` alone gives a
*bit-identical* fit, and so does `random_state` plus `bagging_fraction` — because
`bagging_freq` defaults to **0**, which disables row sampling outright. Only
`bagging_fraction` **with `bagging_freq ≥ 1`**, or `feature_fraction`, actually changes
the fit:

| variation | fits identical? |
|---|---|
| `random_state` alone | **yes** |
| `random_state` + `bagging_fraction` (`bagging_freq` = 0, the default) | **yes** |
| `random_state` + `bagging_fraction` + `bagging_freq=1` | no |
| `random_state` + `feature_fraction` | no |

An earlier draft of this report proposed seeds and `bagging_fraction`, which would have
produced five identical fits, a null result, and a false rejection of the whole
combination family. The experiment sets `bagging_freq ≥ 1` or varies `feature_fraction`,
and its first check is that the *k* member forecasts differ at all.

**The matched baseline.** The preset current when this runs, at k = 1 and with the same
stochastic configuration as the candidate — see §11 on the baseline advancing after
every Keep. A k = 1 arm built from the *deterministic* settings would confound the
ensemble's effect with the sampling's.

**The important segments.** Overall MAE and the paired daily bootstrap CI. Then the
member spread: the MAE range across the *k* members of a single ensemble, and how much
of it the average removes.

One correction to an earlier draft of this report, which followed from the probe above.
It proposed measuring a "run-to-run seed spread" for the current baseline as a second
deliverable. **There is none.** With `LGBM_PARAMS` as it stands the fit is deterministic,
so re-running `e212` with a different seed reproduces it exactly, and the spread is zero
by construction. The variance that actually makes a 0.3 % margin unreadable is not seed
noise — it is sensitivity to *which days* were scored, and the instruments for that
already exist: the paired daily bootstrap in `compare_demand_runs.py`, and now the second
window [PR #223](https://github.com/hankehly/power-market-analytics/pull/223) seals. This
experiment's contribution to that question is narrower than claimed: it says how much of
the *model's* variance an ensemble removes, not how much of our decision record is noise.

**The decision rule, set before the run.**

| Evidence | Decision |
|---|---|
| MAE falls and the CI over days excludes zero | **Keep.** Combination becomes the default strategy. |
| MAE flat, but the ensemble's member spread is wide and the average sits materially inside it | **Refine.** The diversity is real and the averaging is not capturing it; try structural sisters (feature subsets, training lengths) as [P-059](research/literature-review.md#p-059) did, rather than sampling alone. |
| Neither moves, **and the *k* members are verified to differ** | **Reject**, and conclude that a 500-tree GBM on 105 features has already exhausted the ensembling that [P-059](research/literature-review.md#p-059)'s eight *linear* sister models had available. That closes the whole combination family (P-025, P-027, P-059, P-146, P-149, P-151) for the point-MAE lane. |
| Neither moves and the members are identical | **Not a result.** The stochastic mechanism was not active; fix it and re-run. This row exists because that failure is silent. |

**Dependencies and risks.**

1. *Cost.* `34c506fb…` ran in about 31 minutes (MLflow start 1789889130315 → end
   1789891008249). k = 5 multiplies the fit cost. This is the most compute-hungry of the
   top five, and the least so once the second deliverable is corrected down to the
   member spread.
2. *P-059's sisters differ in structure, ours in sampling.* Its eight models used
   different variable selections and training lengths; row and column subsampling give a
   thinner kind of diversity, and the gain could be correspondingly thinner.
   [P-025](research/literature-review.md#p-025) saw the failure mode directly — its
   random forests were "too correlated with the boosting machine to help the ensemble".
   If sampling-based averaging is null, feature-subset sisters are the refinement, not a
   rejection.
3. *TreeSHAP and permutation importance.* Both are defined per model. Averaging k
   models means averaging k SHAP decompositions, which stays additive, but the
   contribution write-back and its additivity test need checking before the run rather
   than after.
4. *It is a model-method change.* Its own experiment, per the research README, and it
   must not be bundled with rank 2's pruning even though both touch the same run.

**Why it follows rank 3.** Ranks 1–3 are single, cheap changes with measured local
evidence; rank 4 costs the most compute of the five and its expected gain is the
smallest — [P-059](research/literature-review.md#p-059)'s own margins are 0.1–0.2 MAPE
points. What lifts it into the top five is the quality of its
evidence: [P-059](research/literature-review.md#p-059) is the only point-accuracy result
in the corpus with a matched baseline, two systems, significance testing and a
"never worse" finding. Running it after ranks 1–3 means it averages a baseline worth
keeping. It is *not*, as an earlier draft claimed, the candidate that would tell us how
much of our decision record is noise — the probe above shows why, and
[#221](https://github.com/hankehly/power-market-analytics/issues/221)'s own words point
at the real instrument: "picking by point estimate would have kept `e221` on a 0.3 %
margin that the segment evidence shows is variance." Segment evidence and a second
window answer that, not an ensemble.
### 9.5 Rank 5 — Correct the retrieved similar day for its weather gap

Papers: [P-141](research/literature-review.md#p-141),
[P-128](research/literature-review.md#p-128),
[P-113](research/literature-review.md#p-113),
[P-006](research/literature-review.md#p-006),
[P-002](research/literature-review.md#p-002),
[P-104](research/literature-review.md#p-104).

**The falsifiable hypothesis.** The similar-day reference is used raw. Giving the model
the *difference* between D's forecast weather and the reference day's observed weather
lowers MAE on the days where those differ, and lowers holiday MAE in particular,
without raising MAE overall.

**Why here.** `wavg_similar_day_top3_demand_kwh` carries ΔMAE 2,030,747 — **21.6×** the
next feature (§4.4). The model is anchored on one retrieved load, and that load is
handed over with no statement of the conditions it occurred under.
[#212](https://github.com/hankehly/power-market-analytics/issues/212) measured the
consequence on its worst day: over the 54 `same_holiday` days of the run,
corr(daily bias, rank-1 load − actual) = **0.906**, slope 0.696 — the reference's error
flows almost straight through to the forecast — and `same_holiday` days run at 5.89 %
MAPE against 2.96 % on `similarity` days. On 2025-07-21 the reference was the coolest
海の日 in five years, 28.8 °C forecast max against 32.1 °C.

[P-141](research/literature-review.md#p-141) is the closest operational match in the
corpus: Korean national holidays, day-ahead, using the **KMA forecast issued at 08:00**
rather than realized weather, with rules picking three similar days whose profiles are
then corrected for trend, weather sensitivity and behind-the-meter PV before being
averaged. Mean MAPE 1.76 % (2018) and 2.62 % (2019), about 1.1 points below the
operator's fuzzy linear regression — and, directly relevant here, **XGBoost and LSTM
did worst** on those days. [P-128](research/literature-review.md#p-128) does the same
in Japan with a "nearby date comparison value" shift on a five-city temperature match,
on JMA forecast temperature, mean error 1.47 % over two years.

**The smallest valid repository change.** Columns in `ftr_period_similar_day` or
`ftr_hour_msm` giving D's population-weighted forecast temperature minus the rank-1
reference day's population-weighted observed temperature, at the same hour, plus the
same for humidity. That is exactly
[#134](https://github.com/hankehly/power-market-analytics/issues/134)'s expression,
already specified and open. A mart change plus a preset — no new source, no code.

**The matched baseline.** The preset current when this runs, on a run of its own. Being
last of the five, this one is most exposed to the advance rule in §11: if ranks 1–4 were
kept it is not `e212` it competes against.

**The important segments.** Day type, and holidays above all. Then the `same_holiday`
subset specifically — the diagnostic that motivates this is defined on those 54 days,
so the run should report them separately. Then the top-10 % demand days, since
2025-07-21 was both a holiday and a peak day.

**The decision rule, set before the run.**

| Evidence | Decision |
|---|---|
| Holiday MAE falls, overall MAE does not rise, and the slope of daily bias on the reference gap falls below 0.906 on the `same_holiday` days | **Keep.** The mechanism is confirmed by both its outcome and its diagnostic. |
| Holiday MAE falls but overall MAE rises, or the CI over days includes zero | **Refine**, and report it as a segment gain with a null overall — not as a win. This is the same shape as #219's result and should be written the same way. |
| Nothing moves | **Reject**, with a specific conclusion: the model can already infer the reference's conditions from the weather columns it holds, and the whole "correct the reference" family ([P-141](research/literature-review.md#p-141), [P-128](research/literature-review.md#p-128), [P-113](research/literature-review.md#p-113)) is closed for this architecture. |

**Dependencies and risks.**

1. *It competes with the ledger's own next step.*
   [#221](https://github.com/hankehly/power-market-analytics/issues/221) recorded a
   cheaper test of the same weakness — a column selecting the similar-day variant by
   `special_period`, "let the mart pick, not the model". That is smaller and should be
   run first; this candidate is what follows if the selector is null, because it
   attacks the reference's *quality* rather than the choice between two references.
2. *#221's own warning applies.* "A feature being further from the target is not the
   same as being less useful to the model" — the 2026-09-21 finding that refuted the
   #219 mechanism check. A weather-gap column makes the same kind of assumption, and a
   null result would be its second refutation.
3. *R-005's record.* Four calendar-feature additions to this model were rejected, one
   costing +7.3 % MAE. Adding columns here is not free, and the run must be judged on
   overall MAE first, segments second.
4. *[P-141](research/literature-review.md#p-141)'s gain is not separable.* Its method
   corrects for trend, weather **and** BTM PV at once and reports one number, so no
   part of its 1.1-point margin can be attributed to the weather correction alone. And
   §4.5 found no support here for the BTM PV part (error against forecast irradiance,
   daytime r = 0.018), though that test does not rule out a stable hidden contribution.

**Why it is fifth.** It attacks the model's single most important input, which is the
strongest argument for it — but that is also why it is last of the five. Ranks 1–4 each
have a measurement of their own effect size on this run; this one has a measurement of
the *problem* (corr 0.906, slope 0.696 on 54 days) and only an analogy for the fix.
It is also the candidate most exposed to #221's warning — "a feature being further from
the target is not the same as being less useful to the model" — which was recorded on
2026-09-21 after exactly this kind of reasoning failed once. Running ranks 1–4 first
means this one is attempted against a baseline whose other weaknesses have been
addressed, so a null result is interpretable rather than confounded.
## 10. Already represented, already tested, deferred or set aside

### 10.1 Mechanisms `e212` already implements

Each row names the literature mechanism, the papers that give it, and the `e212`
feature that is it. **Measured** from the mart YAMLs and
[e212.yaml](conf/presets/demand/e212.yaml).

| Literature mechanism | Papers | Already in `e212` as |
|---|---|---|
| Similar-day selection by a learned weighted distance | [P-002](research/literature-review.md#p-002), [P-007](research/literature-review.md#p-007), [P-100](research/literature-review.md#p-100)–[P-103](research/literature-review.md#p-103), [P-110](research/literature-review.md#p-110), [P-122](research/literature-review.md#p-122) | `SIMILAR_DAY(... rank=1/2/3)`, `SIMILAR_DAY_MEAN(... k=3, weight=inverse_distance)`, its distance and lag — a 7-weight softmax distance refit by NLLS every 7 days ([fit_similar_day.py](scripts/fit_similar_day.py)) |
| Graded holiday weighting (休日度合い) | [P-008](research/literature-review.md#p-008) | `holiday_degree` |
| Discomfort index / humidity-conditioned temperature | [P-009](research/literature-review.md#p-009), [P-010](research/literature-review.md#p-010), [P-131](research/literature-review.md#p-131), [P-143](research/literature-review.md#p-143) | `DISCOMFORT_INDEX(popw temp, popw humidity)` plus `MEAN(forecast_relative_humidity_pct, weight=population)` |
| Spatial temperature aggregation over many stations | [P-095](research/literature-review.md#p-095), [P-128](research/literature-review.md#p-128) | every `MEAN(<element>, weight=population)` column, weighted by e-Stat 500 m census mesh population assigned to the nearest station |
| Temperature recency on **observed** weather | [P-092](research/literature-review.md#p-092), [P-098](research/literature-review.md#p-098) | `ROLLING_MEAN(MEAN(temperature_c, weight=population), gap=2d, window=24/72, step=1h)` and `EWA(…, halflife=24)` |
| Day-type conditioning | [P-127](research/literature-review.md#p-127), [P-134](research/literature-review.md#p-134), [P-144](research/literature-review.md#p-144) | `day_type` and `special_period` as categoricals; `ROLLING_MEAN`/`EWA(demand_kwh, …) by day_type` |
| Exponentially weighted load levels | [P-049](research/literature-review.md#p-049), [P-039](research/literature-review.md#p-039), [P-047](research/literature-review.md#p-047) | the `EWA(…)`/`EWSTD(…)` family at day and period grain |
| Triple seasonality (intraday, weekly, annual) | [P-047](research/literature-review.md#p-047), [P-001](research/literature-review.md#p-001), [P-041](research/literature-review.md#p-041) | `time_code`, `day_of_week`, weekly lags 7/14/21/28 d, `day_of_year`, `month` |
| Solar irradiance and cloud as load drivers | [P-127](research/literature-review.md#p-127), [P-108](research/literature-review.md#p-108), [P-137](research/literature-review.md#p-137) | `MEAN(forecast_solar_radiation_mjm2, weight=population)`, its cumulative sum, and the four cloud-cover columns |
| Bridging / sandwiched working days | [P-104](research/literature-review.md#p-104), [P-106](research/literature-review.md#p-106), [P-144](research/literature-review.md#p-144) | `special_period` level 5 and `holiday_degree` 0.5 / 0.3 |
| Permutation importance for input selection | [P-058](research/literature-review.md#p-058) | the machinery exists and runs every backtest ([importance.py](power_market_analytics/forecasting/importance.py)); what is absent is *acting* on it — see §7 |

### 10.2 Ideas the repository has already decided

| Idea | Record | Outcome |
|---|---|---|
| Calendar features from `dim_date` (ten of them) | [#174](https://github.com/hankehly/power-market-analytics/issues/174) | Rejected, MAE **+7.3 %** |
| `holiday_degree` alone | [#175](https://github.com/hankehly/power-market-analytics/issues/175) | Rejected, +0.3 %, CI includes zero |
| Days since / until a holiday alone | [#176](https://github.com/hankehly/power-market-analytics/issues/176) | Rejected, +6.5 % |
| The six calendar counts alone | [#177](https://github.com/hankehly/power-market-analytics/issues/177) | Rejected, +4.2 % overall, but holidays −13.4 % |
| A rule-chosen prior-year reference date | [#172](https://github.com/hankehly/power-market-analytics/issues/172) | Rejected |
| A learned similar day | [#173](https://github.com/hankehly/power-market-analytics/issues/173) | Kept |
| Recent-load features (thirteen) | [#178](https://github.com/hankehly/power-market-analytics/issues/178) | −0.6 %, CI includes zero; kept tentatively |
| Population-weighted forecast humidity, rain, radiation | [#179](https://github.com/hankehly/power-market-analytics/issues/179) | −2.9 %, CI excludes zero; kept tentatively |
| Park (2020)'s similar-day pool | [#180](https://github.com/hankehly/power-market-analytics/issues/180) | −2.3 %, CI includes zero; kept tentatively |
| All 104 own-mart features | [#212](https://github.com/hankehly/power-market-analytics/issues/212) | Kept, −6.5 %, holidays +7.2 % |
| Ranking holidays from the pool instead of copying last year | [#219](https://github.com/hankehly/power-market-analytics/issues/219) | **Refine**, 2026-09-21. −1.0 % overall, −17.4 % on holidays, CI includes zero, lower on 47.3 % of days, ten days carried 311 % of the reduction |
| Both similar-day variants in one model, LightGBM to split | [#221](https://github.com/hankehly/power-market-analytics/issues/221) | **Reject**, 2026-09-21. Holiday MAE beats `e212` by 15.0 % but loses to `e219` by 2.9 %; overall CI includes zero |

*Inferred:* four of the five rejections are **calendar features added to this model**.
Any literature idea that reduces to "give the tree another calendar column" starts from
a measured record of four failures and should carry an unusually clear mechanism to
justify a run.

### 10.3 Candidates already open in the ledger

Several literature mechanisms already have a repository candidate. These are **not**
new interventions; the report names them so the same idea is not proposed twice.

| Literature mechanism | Papers | Repository candidate |
|---|---|---|
| Recency on the **forecast** temperature within day D | [P-092](research/literature-review.md#p-092), [P-098](research/literature-review.md#p-098), [P-026](research/literature-review.md#p-026) | [#204](https://github.com/hankehly/power-market-analytics/issues/204), open, unbuilt |
| Degree-hours / threshold exposure, hourly | [P-092](research/literature-review.md#p-092), [P-085](research/literature-review.md#p-085), [P-090](research/literature-review.md#p-090), [P-091](research/literature-review.md#p-091), [P-096](research/literature-review.md#p-096), [P-136](research/literature-review.md#p-136) | [#152](https://github.com/hankehly/power-market-analytics/issues/152), open |
| Degree-hours, daily totals | same | [#153](https://github.com/hankehly/power-market-analytics/issues/153), open |
| Adaptive (regime-switching) smoothing of the load level | [P-049](research/literature-review.md#p-049), [P-145](research/literature-review.md#p-145) | [#148](https://github.com/hankehly/power-market-analytics/issues/148) (KAMA), open |
| Weather difference between D and its load references | [P-141](research/literature-review.md#p-141), [P-128](research/literature-review.md#p-128), [P-134](research/literature-review.md#p-134) | [#134](https://github.com/hankehly/power-market-analytics/issues/134), open |
| Weather on D-1, which no feature covers | — | [#133](https://github.com/hankehly/power-market-analytics/issues/133), open (needs ~54 GiB/yr of MSM re-extraction) |
| Weather against a climatological normal | [P-085](research/literature-review.md#p-085), [P-090](research/literature-review.md#p-090), [P-091](research/literature-review.md#p-091) | [#218](https://github.com/hankehly/power-market-analytics/issues/218), open investigation |
| A finer within-year seasonal calendar | [P-105](research/literature-review.md#p-105) (24 solar terms) | [#217](https://github.com/hankehly/power-market-analytics/issues/217) (梅雨), open investigation |

### 10.4 Set aside by the repository, with the reason

*Measured* from the issue bodies. These reasons also gate the literature mechanisms
that would re-propose them.

| Candidate | Reason |
|---|---|
| [#143](https://github.com/hankehly/power-market-analytics/issues/143) the holiday name as a categorical | A name has fewer rows in a 730-day window than LightGBM's default minimum per category, so the tree never splits on it. **This gates every "treat each holiday individually" mechanism** ([P-104](research/literature-review.md#p-104), [P-111](research/literature-review.md#p-111), [P-113](research/literature-review.md#p-113)) unless it changes the *model*, not the feature list. |
| [#142](https://github.com/hankehly/power-market-analytics/issues/142) TEPCO's own でんき予報 forecast | The daily file carrying it is public only on D itself — illegal at 09:30 D-1. |
| [#141](https://github.com/hankehly/power-market-analytics/issues/141) a fresher MSM vintage | The 21 UTC D-2 run stops at 21:00 JST of D and the 00 UTC D-1 run is distributed ~11:30 JST D-1, after the cutoff. **This gates every "use a better/later NWP" mechanism.** |
| [#140](https://github.com/hankehly/power-market-analytics/issues/140) OCCTO's half-hourly forecast | Published from 2025-04-01 only, 538 days — too short to fit a bias correction. |
### 10.5 Papers that yield nothing testable here

Two different counts are easy to confuse here, so both are stated.

**Extraction coverage.** Across the extraction pass over all 162 sources, **155 were
cited by at least one extracted intervention** and **seven by none**. That is a property
of the extraction, not of this document.

**Citation coverage of this report.** Every one of the 162 P-IDs is named somewhere in
this report. 132 are cited in the catalogue, the rankings or the rationales; the other
30 are dispositioned by name in §10.6. None is unread and none is silently dropped.

The seven that contributed nothing to the extraction, each with its reason:

| Paper | Why nothing is extracted |
|---|---|
| [P-014](research/literature-review.md#p-014) | A planning-horizon report — two-to-five-year monthly energy and peak — with no accuracy table and no day-ahead result. |
| [P-016](research/literature-review.md#p-016) | A systematic review that counts algorithms, inputs, error measures and horizons across 67 papers without comparing accuracy. |
| [P-024](research/literature-review.md#p-024) | The GEFCom2012 organisers' report: competition design, not a method. |
| [P-029](research/literature-review.md#p-029) | The GEFCom2014 organisers' report. It defines the protocol §8's quantile work would be scored by, but contributes no mechanism. |
| [P-083](research/literature-review.md#p-083) | Sampling-free SHAP for Transformers. The problem it solves — exact Shapley values without sampling — we do not have: TreeSHAP is exact for our model and already runs on every backtest. |
| [P-124](research/literature-review.md#p-124) | A training method for a structured neural network whose stated gain is interpretability, not accuracy, and not for our model class. |
| [P-142](research/literature-review.md#p-142) | eForecaster's separable mechanisms are either already ours (Shapley explanations per row) or need infrastructure we do not have. |

One source sits outside that split. [P-010](research/literature-review.md#p-010) is
cited — once, for a wind- and radiation-aware thermal index — so it counts among the
155, but its full text is unavailable and the review records that access gap. The
discomfort index it is the origin of reaches us through
[P-009](research/literature-review.md#p-009), which `e212` already implements.

Several further papers appear only as *corroboration*: they support an intervention
another paper originates rather than adding one.
[P-042](research/literature-review.md#p-042) re-demonstrates
[P-039](research/literature-review.md#p-039)'s double-seasonal Holt-Winters across ten
European countries; [P-041](research/literature-review.md#p-041) ranks univariate
methods; [P-085](research/literature-review.md#p-085),
[P-128](research/literature-review.md#p-128) and
[P-131](research/literature-review.md#p-131) are context papers whose mechanisms
`e212` already implements — [P-128](research/literature-review.md#p-128)'s finding that
a five-city average beats Nagoya alone is our population weighting, and
[P-131](research/literature-review.md#p-131)'s humidity-shifted temperature break point
is what `DISCOMFORT_INDEX(…)` and the population-weighted humidity column encode.
[P-094](research/literature-review.md#p-094) is a 2030 scenario framework, and
[P-070](research/literature-review.md#p-070) a 1998 survey of ANNSTLF adopters.
### 10.6 The 30 sources this report does not cite

Every one was read in the extraction pass and every one is accounted for. None yielded a
mechanism that survived merging into a distinct entry — each is either a review whose
content reaches the catalogue through the primary sources it surveys, or a study whose
mechanism another paper originates and states more cleanly. They are listed so the
coverage claim above is traceable rather than asserted.

| Sources | Why they are not cited here |
|---|---|
| [P-012](research/literature-review.md#p-012), [P-013](research/literature-review.md#p-013), [P-015](research/literature-review.md#p-015), [P-017](research/literature-review.md#p-017), [P-018](research/literature-review.md#p-018), [P-020](research/literature-review.md#p-020) | Reviews, surveys and a dataset overview. Their mechanisms enter the catalogue through the primary sources they survey; §5 cites those instead. |
| [P-023](research/literature-review.md#p-023), [P-033](research/literature-review.md#p-033) | Competition entries whose mechanisms are covered by the entries this report does cite ([P-022](research/literature-review.md#p-022), [P-034](research/literature-review.md#p-034), [P-035](research/literature-review.md#p-035)). |
| [P-045](research/literature-review.md#p-045), [P-051](research/literature-review.md#p-051), [P-052](research/literature-review.md#p-052), [P-060](research/literature-review.md#p-060) | Statistical models whose mechanisms the catalogue carries under E1 and B5 from the sources that state them most directly. |
| [P-061](research/literature-review.md#p-061), [P-064](research/literature-review.md#p-064), [P-065](research/literature-review.md#p-065), [P-066](research/literature-review.md#p-066) | Tree and ensemble studies duplicating E2 and A5, against weaker baselines than [P-058](research/literature-review.md#p-058) and [P-059](research/literature-review.md#p-059). |
| [P-069](research/literature-review.md#p-069), [P-071](research/literature-review.md#p-071), [P-072](research/literature-review.md#p-072), [P-073](research/literature-review.md#p-073), [P-076](research/literature-review.md#p-076), [P-077](research/literature-review.md#p-077), [P-138](research/literature-review.md#p-138), [P-140](research/literature-review.md#p-140) | Neural-network architectures. E6 gates the whole class `defer`, and these add architecture variety rather than a separable mechanism. |
| [P-093](research/literature-review.md#p-093) | Wind speed as a load-model input; `popw_forecast_wind_speed_ms` and its components are already `e212` features. |
| [P-125](research/literature-review.md#p-125), [P-126](research/literature-review.md#p-126), [P-130](research/literature-review.md#p-130), [P-132](research/literature-review.md#p-132), [P-133](research/literature-review.md#p-133) | Japanese sources whose mechanisms are covered by [P-115](research/literature-review.md#p-115)–[P-123](research/literature-review.md#p-123) and [P-127](research/literature-review.md#p-127)–[P-129](research/literature-review.md#p-129), or which are contest accounts rather than methods. |

*Inferred:* a source-by-source appendix for all 162 would be the fully auditable form.
This is the compact version — the seven non-contributors named individually above, these
30 grouped by reason, and the remaining 125 cited somewhere in §§5–10.

## 11. Recommended experiment sequence

The repository's rules shape this as much as the ranking does: cheap related feature
transforms may go in one batch; a new data source gets its own experiment; a
model-method change gets its own experiment; the LightGBM settings are never tuned per
experiment; and `--start-date`, `--end-date` and `--train-start` are pinned identically
for a candidate and its baseline. Until
[PR #223](https://github.com/hankehly/power-market-analytics/pull/223) lands,
`943aab6d…` — the `e212` preset on 2024-08-18 … 2026-08-17 against the current
similar-day partition — is the matched arm, and it is not the `34c506fb…` named in
[demand/README.md](research/demand/README.md) (§12 item 6 explains why). **After #223
lands, neither is**: one fresh `e212` run on the pinned 2024-04-01 … 2026-03-31 window
opens the sequence. Step 0 says why that is the right order.

**The baseline advances on every Keep.** That fresh `e212` run is the baseline for step
1 only. The research README's rule is that a kept batch's preset *becomes* the baseline,
so a step that lands compares the next step against **its** preset, on a run of its own,
not against `e212` throughout. Sharing one arm across the whole sequence would measure
each change against a superseded model and misstate its incremental effect — and with
several of these candidates plausibly touching the same day-level error (ranks 1 and 3
both do), the overlap is exactly what a stale baseline would hide. Concretely: if step 1
is kept, step 2's arms are the step-1 preset with and without the pruning; if step 1 is
rejected, step 2's baseline stays the `e212` run. Each Keep costs one new baseline run,
which is the price of an honest increment.

Each step prespecifies its hypothesis, the evidence it will produce, and its decision
rule **before** the run.

### Step 0 — land the pinned evaluation window (PR #223), before any of the runs below

This is not an intervention and it does not lower MAE. It is the change that makes
every number the following steps produce mean what it says, and **it is already in
flight**: [PR #223](https://github.com/hankehly/power-market-analytics/pull/223),
*"pin the evaluation window and seal a holdout"*, opened 2026-09-21. It pins demand to
2024-04-01 … 2026-03-31 and seals the days after it as a holdout. Nothing below should
be run before it lands, because every run below would otherwise add another decision
taken on the same 730 days.

The problem it solves is the researcher's, recorded on
[#221](https://github.com/hankehly/power-market-analytics/issues/221):

> Within a run the backtest is out of sample — each day is forecast by a model trained
> on data through D-2 only. Across runs the selection is the leak: #212, #219, #221 and
> R-006/7/8 all score on the same 730 days, and every keep/reject reads MAE on those
> days, so the surviving preset is partly fitted to the window through our choices.

PR #223 shows it is not hypothetical. Splitting #219's published per-day errors in half:
`e219` beat `e212` by 2.5 % over 2024-08-18 … 2025-08-17 and **lost by 0.5 %** over
2025-08-18 … 2026-08-17. The −1.0 % that earned it a Refine is the average of −2.5 %
and +0.5 %.

The literature corroborates the same failure mode more often than it corroborates any
single mechanism in this report. It is the most frequent phrase in the corpus's own
Appraisals: [P-058](research/literature-review.md#p-058) "the subset size is chosen on
the test set"; [P-097](research/literature-review.md#p-097) "K was picked on the test
set"; [P-092](research/literature-review.md#p-092) "a different pair was best in
hindsight"; [P-025](research/literature-review.md#p-025) "kernels and weights set by
hand or by the public leaderboard … that is tuning on part of the test set";
[P-036](research/literature-review.md#p-036) "all 14 variants were re-run afterwards,
so the best is picked with hindsight";
[P-099](research/literature-review.md#p-099) "subsets picked on 2018 error, so only
2019 is a clean test"; [P-154](research/literature-review.md#p-154) "hyper-parameters
picked by eye on one data set". The discounts §3 applies to those papers apply to our
own accumulated decisions for the same reason.

**What this does to the runs below.** PR #223's window (2024-04-01 … 2026-03-31) is not
the window every baseline in this report was measured on (2024-08-18 … 2026-08-17). Once
it lands, `943aab6d…` is no longer a matched arm and each experiment below needs one
fresh `e212` baseline run on the pinned window to **open** the sequence. The
measurements in §4 stay valid as descriptions of the residual; the *baselines* named in
§9 do not.

**What it does not do.** It does not make step 1 a clean held-out test, and nothing in
this report should be read as claiming so. The pinned window overlaps the already-mined
one by roughly two thirds, so a fresh baseline on it is still a **development**
comparison — what #223 buys is that the window stops drifting and that the days after
2026-03-31 are sealed. The clean test is a later, one-time confirmation on those sealed
days, and it is worth spending on whichever candidate survives development rather than
on the first one run.

### Step 1 — the D-2 forecast residual (rank 1)

One model-method change, on its own, against **the fresh pinned-window `e212` run from
step 0** — not `943aab6d…`, whose window step 0 supersedes. Being first, this is the one
step whose baseline is `e212`; every later step's baseline is whatever survived before
it. The prespecified numbers are
in §9.1: the post-processing proxy gives **−1.62 %** on a held-out year with β chosen on
the first year alone, so **−1.0 % or better** is the Keep threshold and the paired daily
bootstrap CI must exclude zero. Run it before anything else because its result re-ranks
the whole online-adaptation family either way.

### Step 2 — correlation-aware pruning (rank 2)

A preset with a `drop` list only — no code, no mart, no new data. **One experiment, one
preset, one run, one decision**, per the README's batch rule: the list holds both the 17
features at ΔMAE ≤ 0 and the redundant members of each |r| > 0.95 cluster, since they are
two expressions of one mechanism. A follow-up isolating which half mattered is justified
only if the combined run fails. §9.2 has the decision rule, which is a non-inferiority
bound on the upper CI limit rather than a null.

### Step 3 — bound the day-type reference window (rank 3)

A mart change plus a preset, one run. The hypothesis and its staleness table are §9.3;
the bound's length is chosen from the staleness distribution **before** the run, not
from the result.

### Step 4 — combine several fits (rank 4)

Switch on a stochastic mechanism first — `bagging_freq ≥ 1` or `feature_fraction` — or
the *k* members are bit-identical and the run is a false negative. §9.4 has the probe.

### Step 5 — the holiday family, where the ledger left it

Both holiday experiments are already decided.
[#219](https://github.com/hankehly/power-market-analytics/issues/219) (rank every
holiday from the pool) is **Refine**: −1.0 % overall and −17.4 % on holidays, but the
CI over days includes zero, the candidate was lower on only 47.3 % of days, and ten
days carried 311 % of the reduction.
[#221](https://github.com/hankehly/power-market-analytics/issues/221) (give the model
both variants and let it split) is **Reject**: holiday MAE beats `e212` by 15.0 % but
loses to `e219` by 2.9 %.

The live next step is the one the researcher recorded on #221 — *let the mart pick, not
the model*: a `ftr_period_similar_day` column that selects the variant by
`special_period`, testing the same split without asking LightGBM to learn a rule from
54 days. That is a feature change, feasibility 3, and it is the holiday family's
cheapest remaining test. Rank 5 (§9.5) — correcting the retrieved reference for its
weather gap, also
[#134](https://github.com/hankehly/power-market-analytics/issues/134)'s mechanism — is
what follows if the selector column is null.

**Do not** run a general weekday × holiday re-encoding on the strength of §4.3 alone.
§2 and §9 explain why: the pattern describes this window but does not transfer across
it, and #221's 建国記念の日 result — the pool improved it by 965,669 kWh in 2025 and
worsened it by 1,124,133 in 2026 — is the same instability in the ledger's own words.

### Step 6 — one further model-method change, chosen by what steps 1–5 show

Candidates, each its own experiment, never combined:

- **DART boosting** ([P-003](research/literature-review.md#p-003)) — one LightGBM
  parameter, the cheapest model-method change available, and mandated for assessment.
- **A level-normalised target** ([P-115](research/literature-review.md#p-115),
  [P-120](research/literature-review.md#p-120),
  [P-122](research/literature-review.md#p-122),
  [P-032](research/literature-review.md#p-032),
  [P-050](research/literature-review.md#p-050)) — predict the ratio to a level anchor
  and multiply back, so the model never has to represent the level. §4 measures both
  its promise and its limit: the forecast cannot exceed the training ceiling at all
  (145 periods, bias = MAE), but the shrinkage survives normalisation (§7).
- **A per-day-type model** ([P-144](research/literature-review.md#p-144),
  [P-137](research/literature-review.md#p-137),
  [P-158](research/literature-review.md#p-158)) — heavier, and its holiday group would
  train on roughly 2,784 rows.
- **Cross-area holiday transfer** ([P-135](research/literature-review.md#p-135)) —
  blocked until `scripts/fit_similar_day.py --area kansai` exists, because every
  borrowed Kansai row would carry NaN in the six `ftr_period_similar_day` columns,
  including the model's most important feature (§7).

### What must not be combined

[P-003](research/literature-review.md#p-003) contains feature pruning, DART and
temporal reconciliation. They are a feature change, a model-method change and a
post-processing change. Three experiments, never one run — the paper's own numbers show
why: DART moved MAPE 3.24 % → 2.83 % while reconciliation moved it 0.05–0.75 %, so a
combined run would credit reconciliation with DART's gain.

### How every step is reported

`scripts/compare_demand_runs.py --baseline <the current preset's own run> --candidate
<run>` after
`just dbt build --select +fct_demand_forecast_accuracy`, citing overall MAE, the paired
daily bootstrap CI over days, and the segments named in each step's §9 entry. **The
baseline is whichever preset is current, not a fixed run id**: `e212`'s step-0 run for
step 1, and after that whatever was last kept, on a run of its own. Reusing the step-0
run throughout would report each candidate's effect as everything that changed since
`e212` rather than its own increment. A result
that improves one segment but not overall is reported as exactly that — a segment gain
with a null overall — and is not promoted to an overall win. `--common-days` is used
whenever the two runs skipped different delivery days.
## 12. Evidence and access limitations

**What was available.** MLflow (`localhost:5005`), the Spark thriftserver warehouse,
Superset and the GitHub issues and Project were all reachable and were read read-only.
Nothing was started, rebuilt or written. So the ranking is **not** provisional on
baseline evidence: §4 is measured, not assumed.

**What was not measured, and could change the ranking.**

1. *No new backtest was run*, as the task requires. Every "expected impact" is an
   inference from the measured residual plus a discounted paper result, never a
   simulated gain — except the D-2 residual correction in §4.6, which is arithmetic on
   the stored forecasts, not a re-fit, and therefore does **not** show what the model
   would learn if the residual were a feature.
2. *The D-2 residual correction was evaluated as post-processing, not as a feature.*
   As a feature LightGBM could use it non-linearly and interact it with day type and
   season, which could be better; it could also displace a correlated feature and be
   worse. Only a run settles it.
3. *The weather-error correlation is single-station and linear* (s47662, Pearson). A
   population-weighted, non-linear or extremes-only version could be materially
   different, and §4.5 would then need re-reading. This is the single measurement whose
   revision would most change §7 and §8, because it currently discounts the whole
   spatial-weather family.
4. *No SHAP interaction analysis was done.* Mean SHAP per component **was** read from
   `fct_demand_forecast_contribution` (3,710,212 rows, 106 components) — that is where
   §4.3's finding comes from, that the model applies an unmodified weekday effect on
   holidays. What was not done is a pairwise *interaction* decomposition. It would say
   whether the model represents the weekday × holiday interaction weakly or not at all,
   which bears on C1's gate result in §6 — currently `defer` on the transfer evidence
   alone.
5. *The collinearity measurement covers one block* — the 16 demand-level columns of
   `ftr_period_actuals`. A full 105 × 105 correlation matrix would size the pruning
   opportunity properly and identify the clusters a clustered selection would form.
6. *#219's and #221's matched runs were read from MLflow but not re-analysed.* Their
   headline metrics were fetched (`943aab6d…` MAE 509,790 / MAPE 3.118;
   `81144e8c…` MAE 504,602 / MAPE 3.084) and the segment findings are quoted from the
   issues, not recomputed. Note the baseline subtlety this exposes:
   [demand/README.md](research/demand/README.md) names `34c506fb…` (MAE 510,465) as the
   Tokyo baseline, but the similar-day partition has since been re-scored, so
   `943aab6d…` — the same `e212` preset on the same window against the current
   partition — is the matched baseline for any new candidate reading
   `ftr_period_similar_day`. The two differ by 0.13 %. Every measurement in §4 is on
   `34c506fb…`, the documented one.
7. *Every measurement is on one window, 2024-08-18 … 2026-08-17.* That is the window
   [PR #223](https://github.com/hankehly/power-market-analytics/pull/223) exists to stop
   us relying on, and its own evidence shows why: `e219`'s gain is −2.5 % in the first
   year and +0.5 % in the second. My out-of-sample checks (rank 1's held-out −1.62 %, the
   calibration tests, the weekday × holiday reversal) are the same guard applied by hand,
   but they split one window rather than reaching a sealed holdout.
8. *Kansai was not analysed.* Every residual number here is Tokyo. Whether a mechanism
   transfers to Kansai — which runs a different baseline preset,
   `lightgbm_msm_popw_daytype` — is untested, and `e212` cannot run there at all until
   Kansai has a similar-day fit.
9. *Paper evidence is read through the breakdowns*, not the full texts. The breakdowns
   are written from the full texts and carry an explicit Appraisal naming each study's
   weaknesses, which is what the discounting in §3 uses. A breakdown can still omit a
   detail that would change a transfer-confidence rating.

## 13. Conditions that should trigger a re-ranking

Re-rank when any of these becomes true.

| Trigger | What it changes |
|---|---|
| The D-2 residual feature is run and lands outside −1 % … −3 % MAE | If better, the whole online-adaptation family (P-145, P-146, P-154, P-157, P-161) rises. If it is null, that family falls to the bottom of the point-MAE lane, because the cheapest member failed on a signal we had already measured. |
| [PR #223](https://github.com/hankehly/power-market-analytics/pull/223) merges | The evaluation window moves to 2024-04-01 … 2026-03-31 and a holdout is sealed. Every baseline named in §9 stops being a matched arm, and the §4 residual tables should be recomputed on the pinned window before they are cited again. This is the most consequential pending change to this report. |
| A candidate is re-scored on the sealed holdout | The first result that disagrees with its in-window result re-ranks everything. #219 is already the example: −2.5 % in year 1, +0.5 % in year 2. Any ranking here that rests on a single-window number should be re-read the moment a second window exists. |
| A population-weighted, non-linear re-test of the weather-error correlation lands materially above 0.06 | The spatial-weather family (P-095, P-097, P-099) and the ensemble-NWP family (P-086, P-087, P-089) rise out of `defer`. |
| A pruning run shows a material MAE change in either direction | A gain makes correlation-aware selection standing practice; a loss establishes that this model tolerates dead features and closes the family. Either way the `e212` importance table stops being ambiguous. |
| A model-method change is run for the first time | The feasibility 2 ratings in §7 are estimates. The first one calibrates the real cost of touching `lgbm.py` under the coverage gate, and every later model-method rating should be re-read against it. |
| Kansai gains a similar-day fit and an `e212`-equivalent baseline | Cross-area transfer (P-135, P-160, P-081) becomes testable, and "broader reuse across tasks or areas" starts discriminating between candidates. |
| A new source is ingested — NWP ensembles, climatological normals ([#218](https://github.com/hankehly/power-market-analytics/issues/218)), an imbalance-price feed | Interventions currently gated on data realism move to `pass`. The cost-sensitive family (P-159) in particular is gated only on the absence of a cost model. |
| The residual stops being a day-level offset | §4.2's 0.955 within-day autocorrelation and 35.7 % day-level share are the premise of ranks 1–3. If a future baseline's error becomes shape-dominated, the day-level mechanisms lose their claim and the within-day and profile mechanisms gain. |
