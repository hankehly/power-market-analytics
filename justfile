# Pass recipe args as shell positionals so quoting survives, e.g.
#   just python -c "import power_market_analytics"
set positional-arguments

[doc("Run any command inside the devcontainer (e.g. just exec ls data)")]
exec *args:
    @docker compose exec -e PYTHONPATH=/workspace/src devcontainer "$@"

[doc("Run python inside the devcontainer (e.g. just python scripts/load_jepx_spot.py)")]
python *args:
    @docker compose exec -e PYTHONPATH=/workspace/src devcontainer python "$@"

[doc("Run dbt inside the devcontainer (e.g. just dbt run)")]
dbt *args:
    @docker compose exec --workdir /workspace/dbt devcontainer dbt "$@"

[doc("Open a shell inside the devcontainer")]
shell:
    @docker compose exec -e PYTHONPATH=/workspace/src devcontainer bash

[doc("Open a beeline SQL shell on the thriftserver")]
sql:
    @docker compose exec thriftserver /opt/spark/bin/beeline -u 'jdbc:hive2://localhost:10000/;auth=noSasl' -n admin

[doc("Open a web UI in the browser: docsify | dbt (generate + serve dbt docs) | mlflow | spark (thriftserver) | spark-dev (devcontainer session)")]
open target:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{ target }}" in
        docsify)   url="http://localhost:3000" ;;
        mlflow)    url="http://localhost:5005" ;;
        spark)     url="http://localhost:4040" ;;
        spark-dev) url="http://localhost:4041" ;;
        *)
            echo "Unknown target '{{ target }}'. Expected one of: docsify, mlflow, spark, spark-dev" >&2
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
