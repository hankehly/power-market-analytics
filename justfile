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

# Each source has a private `ingest-<source>` recipe (download + seed updates + reload raw, no
# dbt) that its public `refresh-<source>` runs before a `dbt build`; `refresh-all` chains every
# ingest recipe and builds once at the end.

[private]
ingest-jepx:
    just python scripts/download_jepx_spot.py
    just python scripts/update_holidays_seed.py
    just python scripts/load_jepx_spot.py

[doc("Refresh JEPX market data (+ holidays seed): redownload, reload raw, rebuild + test dbt")]
refresh-jepx: ingest-jepx
    just dbt build

[private]
ingest-jma *args:
    just python scripts/update_jma_stations_seed.py
    just python scripts/download_jma_hourly_all.py {{ args }}
    just python scripts/load_jma_hourly.py

[doc("Refresh JMA weather data: update staffed-station seed, download stitched 7-element hourly files (args pass through, e.g. --prefecture 44; ~14 h cold), reload raw, rebuild + test dbt")]
refresh-jma *args: (ingest-jma args)
    just dbt build

[private]
ingest-occto:
    just python scripts/download_occto_demand_forecast.py
    just python scripts/download_occto_area_reserve_rate.py
    just python scripts/load_occto_demand_forecast.py
    just python scripts/load_occto_area_reserve_rate.py

[doc("Refresh OCCTO day-after-next data (demand forecast + half-hourly area reserve-rate): redownload the full histories, reload raw, rebuild + test dbt")]
refresh-occto: ingest-occto
    just dbt build

[private]
ingest-tepco:
    just python scripts/download_tepco_area_demand_generation.py
    just python scripts/load_tepco_area_demand_generation.py

[doc("Refresh TEPCO Tokyo-area demand/generation actuals: redownload all monthly archives, reload raw, rebuild + test dbt")]
refresh-tepco: ingest-tepco
    just dbt build

[private]
ingest-kansai:
    just python scripts/download_kansai_area_demand_generation.py
    just python scripts/load_kansai_area_demand_generation.py

[doc("Refresh Kansai-area demand/generation actuals: redownload all monthly archives, reload raw, rebuild + test dbt")]
refresh-kansai: ingest-kansai
    just dbt build

[private]
ingest-estat *args:
    just python scripts/download_estat_census_population_mesh.py {{ args }}
    just python scripts/load_estat_census_population_mesh.py

[doc("Refresh e-Stat census 500 m population mesh: download every configured census vintage (cached; args pass through, e.g. --years 2020 --force), reload raw, rebuild + test dbt")]
refresh-estat *args: (ingest-estat args)
    just dbt build

[private]
ingest-msm *args:
    just python scripts/download_jma_msm_surface_forecast.py {{ args }}
    just python scripts/load_jma_msm_surface_forecast.py

[doc("Refresh JMA MSM surface forecasts: download RISH GRIB2 runs (~157 MB per delivery day; args pass through, e.g. --start-date 2026-08-01 --keep-grib), extract station points, reload raw, rebuild + test dbt")]
refresh-msm *args: (ingest-msm args)
    just dbt build

[doc("Refresh every data source (JEPX, JMA hourly, OCCTO, TEPCO, Kansai, e-Stat, MSM) with a single dbt build at the end: each ingest step runs with its defaults (no args forwarded; warm caches make it ~1.5 h, dominated by JMA's current-year files); a failing step aborts before the build")]
refresh-all: ingest-jepx ingest-jma ingest-occto ingest-tepco ingest-kansai ingest-estat ingest-msm
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
