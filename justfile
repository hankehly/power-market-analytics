# Pass recipe args as shell positionals so quoting survives, e.g.
#   just python -c "import power_market_analytics"
set positional-arguments

# First recipe = default, so bare `just` lists the available recipes.
[private]
default:
    @just --list

[doc("Run any command inside the devcontainer (e.g. just exec ls data)")]
exec *args:
    @docker compose exec -e PYTHONPATH=/workspace devcontainer "$@"

[doc("Run python inside the devcontainer (e.g. just python scripts/load_jepx_spot.py)")]
python *args:
    @docker compose exec -e PYTHONPATH=/workspace devcontainer python "$@"

[doc("Run dbt inside the devcontainer (e.g. just dbt run)")]
dbt *args:
    @docker compose exec --workdir /workspace/dbt devcontainer dbt "$@"

[doc("Serve the Feast UI (the feature catalogue: views, fields, entities, sources) on http://localhost:8888, host-side; refreshes the registry from the package first. Stop with Ctrl-C; `just open feast` opens it")]
feast-ui:
    uv run python -c "from power_market_analytics.features.store import open_store; open_store()"
    uv run feast -c conf/feast ui --port 8888

[doc("Regenerate power_market_analytics/features/views.py from the dbt manifest (host-side parse first)")]
feature-views:
    cd dbt && DBT_THRIFT_HOST=localhost uv run dbt parse
    uv run python scripts/generate_feature_views.py

[doc("Open a shell inside the devcontainer")]
shell:
    @docker compose exec -e PYTHONPATH=/workspace devcontainer bash

[doc("Open a beeline SQL shell on the thriftserver")]
sql:
    @docker compose exec thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin

[doc("Open a web UI in the browser: docsify | feast (the feature catalogue, after `just feast-ui`) | github (the repo) | mlflow | spark (thriftserver) | spark-dev (devcontainer session) | superset")]
open target:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{ target }}" in
        docsify)   url="http://localhost:3000" ;;
        feast)     url="http://localhost:8888" ;;
        github)    url="https://github.com/hankehly/power-market-analytics" ;;
        mlflow)    url="http://localhost:5005" ;;
        spark)     url="http://localhost:4040" ;;
        spark-dev) url="http://localhost:4041" ;;
        superset)  url="http://localhost:8088" ;;
        *)
            echo "Unknown target '{{ target }}'. Expected one of: docsify, feast, github, mlflow, spark, spark-dev, superset" >&2
            exit 1
            ;;
    esac
    open "$url"

# One refresh recipe covers every source. A single source is refreshed by running its download +
# load scripts through `just python` (the pairs are listed in CLAUDE.md) and then `just dbt build`.
# JMA runs before MSM because the MSM downloader reads the station seed.

[doc("Refresh every data source (JEPX + holidays seed, JMA hourly + station seed, OCCTO, TEPCO (both datasets), Kansai (both datasets), e-Stat, MSM) with each script's defaults, then one dbt build: ~1.5 h with warm caches, dominated by JMA's current-year files; a failing step aborts before the build")]
refresh-all:
    just python scripts/download_jepx_spot.py
    just python scripts/update_holidays_seed.py
    just python scripts/load_jepx_spot.py

    just python scripts/update_jma_stations_seed.py
    just python scripts/download_jma_hourly_all.py
    just python scripts/load_jma_hourly.py

    just python scripts/download_occto_demand_forecast.py
    just python scripts/download_occto_area_reserve_rate.py
    just python scripts/load_occto_demand_forecast.py
    just python scripts/load_occto_area_reserve_rate.py

    just python scripts/download_tepco_area_demand_generation.py
    just python scripts/load_tepco_area_demand_generation.py
    just python scripts/download_tepco_power_usage.py
    just python scripts/load_tepco_power_usage.py

    just python scripts/download_kansai_area_demand_generation.py
    just python scripts/load_kansai_area_demand_generation.py
    just python scripts/download_kansai_power_usage.py
    just python scripts/load_kansai_power_usage.py

    just python scripts/download_estat_census_population_mesh.py
    just python scripts/load_estat_census_population_mesh.py

    just python scripts/download_jma_msm_surface_forecast.py
    just python scripts/load_jma_msm_surface_forecast.py

    just dbt build

[doc("Run the Python unit tests with a coverage report (pytest, host-side; uses a local SparkSession)")]
test *args:
    uv run pytest --cov --cov-report=term-missing {{args}}

[doc("Lint Python with ruff (rules in pyproject.toml [tool.ruff]; e.g. just lint --fix)")]
lint *args:
    uv run ruff check . {{args}}

[doc("Type-check Python with mypy (checked packages + config in pyproject.toml [tool.mypy])")]
mypy *args:
    uv run mypy {{args}}

# Version pinned here and in .github/workflows/ci.yml — bump both together.
[doc("Scan Dockerfiles, workflows and committed files with checkov (config in .checkov.yaml)")]
checkov *args:
    uvx checkov@3.3.11 {{args}}

# Unlike checkov's, this version is pinned here and nowhere else: the ci job
# runs this recipe through `uvx --from rust-just` instead of repeating the
# command, because the --ignore-vuln list below must have exactly one
# definition. Exits 1 on any advisory, so it gates on its own.
#
# `uv export` is how the lock reaches pip-audit: `pip-audit --locked` reads only
# a PEP 751 pylock.toml, not uv.lock. --no-emit-project drops the `-e .` entry
# pip-audit cannot version, and --no-deps audits exactly what the lock pins
# rather than re-resolving.
#
# Every --ignore-vuln below is an advisory whose fix this repo cannot reach: a
# dbt package pins the vulnerable version, so the fix arrives with a dbt upgrade
# (issue #84), not with a lock bump. Drop an entry the moment its fix becomes
# reachable — the list is for advisories with nowhere to go, never for ones we
# have not got to. Reviewed 2026-09-12, recheck by 2026-12-12.
#
#   sqlparse 0.5.5 -> 0.6.0 is held by dbt-core 1.11; taking it pulls dbt-core
#   1.12 and a release-candidate parser. All five are DoS or code-generation
#   flaws that need attacker-supplied SQL: 3696 is the Python/PHP output filters
#   (never used here), 3697/3698/3699/3923 are parser and reindent blowups. The
#   only SQL sqlparse sees here is this repo's own dbt models.
#
#   thrift 0.16.0 -> 0.24.0 is held by dbt-spark 1.10, which allows it only in a
#   pre-release. 3927 is TLS hostname validation, 3925 data amplification, 3926
#   an infinite loop — all against a hostile Thrift peer. The only peer here is
#   the Spark thriftserver on the local compose network, reached without TLS.
[doc("Audit the locked dependencies for known vulnerabilities (pip-audit over uv.lock)")]
pip-audit *args:
    #!/usr/bin/env bash
    # A bash recipe for `pipefail`. Without it a linewise recipe runs under
    # /bin/sh and the pipeline's status is pip-audit's alone, so a failed
    # `uv export` — a stale lock — feeds it an empty stream and the gate passes
    # reporting no vulnerabilities. Measured before the fix: export exit 1,
    # recipe exit 0. A security check that passes on no input is worse than none.
    set -euo pipefail
    uv export --locked --no-hashes --no-emit-project --format requirements.txt \
      | uvx pip-audit@2.10.1 --no-deps --disable-pip --requirement /dev/stdin \
          --ignore-vuln PYSEC-2026-3696 --ignore-vuln PYSEC-2026-3697 \
          --ignore-vuln PYSEC-2026-3698 --ignore-vuln PYSEC-2026-3699 \
          --ignore-vuln PYSEC-2026-3923 \
          --ignore-vuln PYSEC-2026-3925 --ignore-vuln PYSEC-2026-3926 \
          --ignore-vuln PYSEC-2026-3927 "$@"

# Version pinned here and in .github/workflows/ci.yml — bump both together.
# Exits 1 on any finding, so it gates on its own.
#
# zizmor is a GitHub Actions analyser, and covers the one surface checkov is
# weakest on: it reads workflows the way an attacker would. It is here rather
# than semgrep because a measurement on 2026-09-12 (issue #33, closed) found
# semgrep's github-actions ruleset adds only the unpinned-uses class over
# checkov, while zizmor finds that plus artipacked, in 0.18 s against 8.2 s.
#
# --persona=regular is the default persona: findings the maintainer is expected
# to act on, without the pedantic set's stylistic noise.
#
# zizmor reads GH_TOKEN and runs its online audits when one is set, which is
# how the ci job runs it. Bare, it is offline and says so on stderr — a few
# audits (stale-action-refs and friends) are skipped. To match CI exactly:
#
#     GH_TOKEN=$(gh auth token) just zizmor
#
# Offline is the weaker run, so local can only miss what CI then catches.
[doc("Audit the GitHub Actions workflows with zizmor (pinning, injection, token handling)")]
zizmor *args:
    uvx zizmor@1.30.1 --persona=regular .github/workflows/ {{args}}
