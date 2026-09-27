<!-- The body describes the pull request as it is now, for the person who merges it: read
to the Checks line and decide; open the folded blocks to review. Delete these comments and
any block you do not need. The rules are in CLAUDE.md, "Code review (pull requests)". -->

## Summary

<!-- One to three lines: what the PR does, the issue or spec it comes from, what it is
stacked on, what closes on merge. A PR with no issue opens with the problem in one line. -->

## Changes

<!-- One line per item, naming the model, column, script, file or setting. No reasoning:
a change that needs a paragraph is a decision and goes in the Decisions block. -->

-

## Effect on what exists

<!-- The fact a merge needs, in one fixed place: what already existed and moved, and what
did not. Rows by kind of change:
- a dbt model or feature mart: the earlier columns' values (rows compared, rows that
  differ), rows added or removed, `available_at`, whether earlier runs stay comparable
- a loader or downloader: rows loaded before and after, equality with the old table,
  files skipped or newly read
- forecasting code or a preset: whose forecasts change, a reference run reproduced
  (period by period, or as a MAE difference), MLflow params and artifacts added
- a dashboard: charts added, moved or detached, datasets changed, load time before and after
- CI or tooling: the jobs that change, what fails now that passed or the reverse
- a dependency upgrade: the packages that matter, from and to, advisories cleared
- docs, templates, settings: the one line "None: docs only." instead of the table -->

| | |
|---|---|
| | |

## Checks

<!-- One line: the commands run and their results, separated by " · ". For example:
`just test` 1,806 passed, 100 % · `just lint`, `just mypy`, `just docs-links` clean ·
`dbt build --select ftr_period_actuals+ dim_feature` PASS=46 · CI on this PR. -->

<details><summary>Decisions (n)</summary>

<!-- Only when a decision was made. Numbered, a bold lead, one to three lines each, the
justification once. An alternative weighed and dropped gets one line. -->

1.

</details>

<details><summary>Evidence</summary>

<!-- What this PR in particular was checked against: equality with the table before the
change, a guard shown to catch a known break, retrieval through Feast, timings before and
after. Tables, not sentences. A fact that decides nothing here, such as a feature's error
as a forecast by itself, goes to the issue as a comment and is linked from here. -->

</details>
