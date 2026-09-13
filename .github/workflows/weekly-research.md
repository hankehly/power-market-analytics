---
name: Weekly Research
description: |
  Weekly reading list for this repository's research: news from the Japanese power market and
  its data publishers, new papers on demand and price forecasting, releases of the tools the
  repository runs on, and pointers back into the code. Posts one GitHub discussion per run.

on:
  schedule: weekly on monday
  workflow_dispatch:

# Minimal read permissions - safe-outputs handles the one write (the discussion)
permissions:
  contents: read
  issues: read
  pull-requests: read
  discussions: read

# Strict mode forbids a "*" wildcard, so every site the agent may fetch is listed. Upstream's
# `network: defaults` reaches only package mirrors and certificate hosts, which left its
# "read the news on the Web" step with nothing to read. Each domain below answered a request on
# 2026-09-13. METI (www.meti.go.jp, www.enecho.meti.go.jp) is left out: it refused plain HTTP
# clients with 403.
network:
  allowed:
    - defaults
    - github
    # Japanese power market and grid operators
    - www.jepx.jp
    - www.occto.or.jp
    - www.tepco.co.jp
    - www.kansai-td.co.jp
    # Weather and statistics publishers the pipelines download from
    - www.jma.go.jp
    - www.data.jma.go.jp
    - www.e-stat.go.jp
    # Industry news (電気新聞)
    - www.denkishimbun.com
    # Papers: OpenAlex is the search API; arXiv serves the abstracts
    - api.openalex.org
    - arxiv.org
    - export.arxiv.org

safe-outputs:
  create-discussion:
    title-prefix: "[weekly-research] "
    category: "ideas"
    # Keep only the newest report open. Older ones are closed as outdated with a link to the
    # new one, and stay readable. The prefix above is what matches them.
    close-older-discussions: true
    # Expiry needs gh-aw's maintenance workflow, which this repository turns off
    # (.github/workflows/aw.json). close-older-discussions does the cleanup instead.
    expires: false

tools:
  # `jq` is required, not a convenience. The generated prompt's only supported way to pass a
  # multi-line safe-output body is a heredoc temp file injected with `jq -Rs`, and gh-aw's
  # default allowlist ships `yq` but not `jq`. Without it the unbloat workflow's agent could not
  # build its body and PR #100 opened with the body `@-` (see PR #105).
  bash: ["cat", "ls", "find", "grep", "head", "tail", "wc", "jq *"]
  github:
    toolsets: [default, discussions]
  web-fetch:

timeout-minutes: 30

source: githubnext/agentics/workflows/weekly-research.md@ae8d551f07c7ed7619f8c58c7bb4c3ac89395d38
---

# Weekly Research

## Context

${{ github.repository }} is one researcher's project on the Japanese electricity market. It
ingests public data — JEPX spot prices, OCCTO demand and reserve-rate forecasts, TEPCO and
関西電力送配電 area demand, JMA observations and MSM GPV forecasts, e-Stat census meshes — into a
dbt star schema on Spark, builds features served by Feast, and runs day-ahead LightGBM backtests
for two tasks: **spot price** and **area demand** (Tokyo, Kansai), tracked in MLflow and shown in
Superset.

Read these before you search, so the report is about this project and not the industry at large:

- `CLAUDE.md` — commands, architecture and conventions.
- `docs/research/README.md`, then `docs/research/demand/README.md` and
  `docs/research/spot_price/README.md` — the investigations (`R-XXX`) run so far and their outcomes.
- `docs/research/papers.md` — papers the research already cites. Do not report them again.
- The pull requests and issues of the last 7 days.
- The previous `[weekly-research]` discussion, if one exists. Do not repeat its items.

## What to look for

Cover the last 7 days where you can; older items are fine when they are new to this repository.

1. **Market and policy news** — JEPX, OCCTO, the TSOs and 電気新聞: rule changes, market
   design, capacity or balancing market news, anything that moves spot prices or demand.
2. **Data source changes** — announcements from the publishers the pipelines download from
   (JEPX, OCCTO, TEPCO, 関西電力送配電, JMA, e-Stat): new or retired datasets, format or URL
   changes, maintenance windows. Name the ingestion module or retrieval doc under `docs/` an
   item would affect. This section matters most: a silent format change breaks a download.
3. **Papers** — recent work on day-ahead electricity price or load forecasting, similar-day
   methods, weather features, gradient boosting for energy, forecast explanation. Search
   OpenAlex (`https://api.openalex.org/works?search=...&filter=from_publication_date:YYYY-MM-DD`)
   and read abstracts on arXiv. Prefer papers with Japanese data or with methods close to an
   open investigation. Give each a one-line summary and a link.
4. **Tools** — notable releases of what the repository runs on: dbt, Spark, Feast, LightGBM,
   MLflow, Superset, gh-aw. Only releases that change something this repository uses.
5. **Ideas worth a look** — at most three, each tied to a concrete file, model or
   investigation in this repository and to an item above. These are pointers for the
   researcher, not conclusions: state what you read, not what the result would be.

Leave a section out when it has nothing worth reading. A short report is better than a padded one.

## Rules

- Create exactly one new discussion. Do not edit or comment on any existing discussion,
  issue or pull request.
- Every item carries a link to its source. Do not report anything you could not open.
- Write plainly: short sentences, one idea each, everyday words. Keep Japanese names as
  published (`広域予備率`, `でんき予報`) next to an English gloss the first time.
- Write the body to a temp file with a heredoc and pass it with `jq -Rs`, as the tool
  instructions describe.

At the end of the report add a collapsed `<details>` section listing:

- every search query and URL you fetched
- every bash command you ran
- every MCP tool you used
