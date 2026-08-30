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

[doc("Open a shell inside the devcontainer")]
shell:
    @docker compose exec -e PYTHONPATH=/workspace devcontainer bash

[doc("Open a beeline SQL shell on the thriftserver")]
sql:
    @docker compose exec thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin

[doc("Open a web UI in the browser: docsify | mlflow | spark (thriftserver) | spark-dev (devcontainer session) | superset")]
open target:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{ target }}" in
        docsify)   url="http://localhost:3000" ;;
        mlflow)    url="http://localhost:5005" ;;
        spark)     url="http://localhost:4040" ;;
        spark-dev) url="http://localhost:4041" ;;
        superset)  url="http://localhost:8088" ;;
        *)
            echo "Unknown target '{{ target }}'. Expected one of: docsify, mlflow, spark, spark-dev, superset" >&2
            exit 1
            ;;
    esac
    open "$url"

# One refresh recipe covers every source. A single source is refreshed by running its download +
# load scripts through `just python` (the pairs are listed in CLAUDE.md) and then `just dbt build`.
# JMA runs before MSM because the MSM downloader reads the station seed.

[doc("Refresh every data source (JEPX + holidays seed, JMA hourly + station seed, OCCTO, TEPCO, Kansai, e-Stat, MSM) with each script's defaults, then one dbt build: ~1.5 h with warm caches, dominated by JMA's current-year files; a failing step aborts before the build")]
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
