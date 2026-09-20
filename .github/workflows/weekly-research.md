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
# "read the news on the Web" step with nothing to read. The list is the publishers' news sites
# plus every host the downloaders fetch from (`grep` for `https://` under
# power_market_analytics/ and scripts/), so the data-source check can open the pages a format or
# URL change would land on. Add a host here when a downloader gains one. Each domain below
# answered a request on 2026-09-13. Left out, and why:
# - METI (www.meti.go.jp, www.enecho.meti.go.jp) and the market watchdog (www.egc.meti.go.jp)
#   answer plain HTTP clients with 403, even with a browser user agent.
# - doi.org redirects to publishers, and MDPI answers 403. OpenAlex already returns the abstract.
# - Semantic Scholar's API answers 429 without a key.
# After a run, `gh aw audit <run-id> --parse` writes firewall.md, which lists every blocked request.
network:
  allowed:
    - defaults
    - github
    # Japanese power market and grid operators
    - www.jepx.jp
    - www.eprx.or.jp # 需給調整市場 (balancing market) exchange
    - www.occto.or.jp
    - occtonet3.occto.or.jp # ingestion/occto.py: both OCCTO downloads
    - web-kohyo.occto.or.jp # the reserve-rate numbers' second portal (OCCTO doc §9)
    - www.tepco.co.jp
    - www4.tepco.co.jp # ingestion/tso/tepco/area_demand_generation.py: AREA_YYYYMM.zip
    - www.kansai-td.co.jp
    # Weather, statistics and calendar publishers the pipelines download from
    - www.jma.go.jp
    - www.data.jma.go.jp
    - database.rish.kyoto-u.ac.jp # ingestion/msm/vintage.py: the MSM GRIB2 archive
    - www.jmbsc.or.jp # JMBSC: notices of changes to the MSM GPV distribution
    - www.e-stat.go.jp
    - www8.cao.go.jp # scripts/update_holidays_seed.py: the Cabinet Office holiday CSV
    # Industry news (電気新聞)
    - www.denkishimbun.com
    # Papers: OpenAlex is the search API; arXiv serves the abstracts
    - api.openalex.org
    - arxiv.org
    - export.arxiv.org
    # Papers in Japanese journals: J-STAGE (search API + articles) and CiNii Research
    - api.jstage.jst.go.jp
    - www.jstage.jst.go.jp
    - cir.nii.ac.jp
    # Tool releases: Apache Spark publishes none on GitHub, only on its own site
    - spark.apache.org

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

engine:
  id: copilot
  # Copilot CLI has its own URL check on shell commands, apart from the tool allowlist: a
  # command with a literal URL is refused unless the URL is allowed. With `curl` allowed and no
  # URL rule, every fetch in run 34738914652 was refused, until the agent put the URL in a
  # shell variable, which the check does not see. The check is not the boundary, then; the
  # firewall is. So all URLs are allowed here, and `network.allowed` stays the one list of
  # reachable hosts.
  args: ["--allow-all-urls"]

tools:
  # `jq` is required, not a convenience. The generated prompt's only supported way to pass a
  # multi-line safe-output body is a heredoc temp file injected with `jq -Rs`, and gh-aw's
  # default allowlist ships `yq` but not `jq`. Without it the unbloat workflow's agent could not
  # build its body and PR #100 opened with the body `@-` (see PR #105).
  # `curl` is required too: it is the agent's only way to read the web (with `--allow-all-urls`
  # above). The firewall still limits it to the `network.allowed` hosts, because the agent's
  # only way out is the proxy.
  bash: ["cat", "ls", "find", "grep", "head", "tail", "wc", "jq *", "curl *"]
  github:
    toolsets: [default, discussions]
  # Kept, but not usable today. gh-aw v0.88.7 runs Copilot CLI 1.0.80 in offline mode, and in
  # offline mode the CLI never registers its `web_fetch` tool. The compiled
  # `--allow-tool web_fetch` then allows a tool the model cannot see. Run 34735204913 had only
  # this for the web, found no fetch tool, and posted nothing.
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
  `docs/research/spot_price/README.md` — how the research is recorded and each task's scope.
- The repository's issues, open and closed: `observation` — behaviour already noticed;
  `investigation` and `experiment` — the investigations run so far and their outcomes;
  `feature candidate` — the ideas already on the backlog. Do not report or propose one of
  them again.
- `docs/research/literature-review.md` — the papers the literature review already lists,
  cited by the research or not, each with a breakdown. Do not report them again.
- The pull requests and issues of the last 7 days.
- The previous `[weekly-research]` discussion, if one exists. Do not repeat its items.

## What to look for

Cover the last 7 days where you can; older items are fine when they are new to this repository.

1. **Market and policy news** — JEPX, EPRX (the balancing-market exchange), OCCTO, the TSOs
   and 電気新聞: rule changes, market design, capacity or balancing market news, anything that
   moves spot prices or demand.
2. **Data source changes** — announcements from the publishers the pipelines download from
   (JEPX, OCCTO, TEPCO, 関西電力送配電, JMA, the RISH MSM archive at Kyoto University, e-Stat, the
   Cabinet Office holiday CSV), and JMBSC's notices about the MSM GPV distribution: new or
   retired datasets, format or URL changes, maintenance windows. The `https://` URLs under `power_market_analytics/ingestion/` and `scripts/` are the
   exact pages the downloads use. Name the ingestion module or retrieval doc under `docs/` an
   item would affect. This section matters most: a silent format change breaks a download.
3. **Papers** — recent work on day-ahead electricity price or load forecasting, similar-day
   methods, weather features, gradient boosting for energy, forecast explanation. Search
   OpenAlex (`https://api.openalex.org/works?search=...&filter=from_publication_date:YYYY-MM-DD`)
   and read abstracts on arXiv. For Japanese journals (電気学会論文誌 and the like), search
   J-STAGE (`https://api.jstage.jst.go.jp/searchapi/do?service=3&text=...&pubyearfrom=YYYY`,
   Atom XML) and CiNii Research
   (`https://cir.nii.ac.jp/opensearch/articles?q=...&from=YYYY&format=json`), with Japanese
   search terms such as `電力需要予測` and `電力価格予測`. Prefer papers with Japanese data or with
   methods close to an open investigation. Give each a one-line summary and a link.
4. **Tools** — notable releases of what the repository runs on: dbt, Spark, Feast, LightGBM,
   MLflow, Superset, gh-aw. Use GitHub releases, and `https://spark.apache.org/news/` for Spark,
   which publishes none on GitHub. Only releases that change something this repository uses.
5. **Ideas worth a look** — at most three, each tied to a concrete file, model or
   investigation in this repository and to an item above. These are pointers for the
   researcher, not conclusions: state what you read, not what the result would be.

Leave a section out when it has nothing worth reading. A short report is better than a padded one.

## Rules

- Fetch web pages and APIs with `curl -sL` (add `-A "Mozilla/5.0"` for a site that refuses
  the default agent). Save large pages to `/tmp/gh-aw/agent/` and read them with `grep`,
  `head` and `jq`. Only the hosts in the network allowlist answer; do not report the others
  as broken.
- Create exactly one new discussion. Do not edit or comment on any existing discussion,
  issue or pull request.
- Every item carries a link to its source. Do not report anything you could not open.
  For a paper, its record in OpenAlex, arXiv, J-STAGE or CiNii counts as opened — the
  publisher's page is often out of reach. Link the DOI when the record has one.
- Write plainly: short sentences, one idea each, everyday words. Keep Japanese names as
  published (`広域予備率`, `でんき予報`) next to an English gloss the first time.
- Write the body to a temp file with a heredoc and pass it with `jq -Rs`, as the tool
  instructions describe.

At the end of the report add a collapsed `<details>` section listing:

- every search query and URL you fetched
- every bash command you ran
- every MCP tool you used
