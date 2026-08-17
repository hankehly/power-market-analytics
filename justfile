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

[doc("Refresh JEPX market data (+ holidays seed): redownload, reload raw, rebuild + test dbt")]
refresh-jepx:
    just python scripts/download_jepx_spot.py
    just python scripts/update_holidays_seed.py
    just python scripts/load_jepx_spot.py
    just dbt build

[doc("Refresh JMA weather data: update station seed, download hourly files (args pass through, e.g. --prefecture 44), reload raw, rebuild + test dbt")]
refresh-jma *args:
    just python scripts/update_jma_stations_seed.py
    just python scripts/download_jma_hourly_all.py {{ args }}
    just python scripts/load_jma_hourly.py
    just dbt build

[doc("Refresh OCCTO day-after-next data (demand forecast + half-hourly area reserve-rate): redownload the full histories, reload raw, rebuild + test dbt")]
refresh-occto:
    just python scripts/download_occto_demand_forecast.py
    just python scripts/download_occto_area_reserve_rate.py
    just python scripts/load_occto_demand_forecast.py
    just python scripts/load_occto_area_reserve_rate.py
    just dbt build

[doc("Refresh TEPCO Tokyo-area demand/generation actuals: redownload all monthly archives, reload raw, rebuild + test dbt")]
refresh-tepco:
    just python scripts/download_tepco_area_demand_generation.py
    just python scripts/load_tepco_area_demand_generation.py
    just dbt build

[doc("Refresh Kansai-area demand/generation actuals: redownload all monthly archives, reload raw, rebuild + test dbt")]
refresh-kansai:
    just python scripts/download_kansai_area_demand_generation.py
    just python scripts/load_kansai_area_demand_generation.py
    just dbt build

[doc("Run the Python unit tests (pytest, host-side; uses a local SparkSession)")]
test *args:
    uv run pytest {{args}}
