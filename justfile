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

[doc("Refresh the warehouse: redownload all sources, reload raw, rebuild + test all dbt models")]
refresh:
    just python scripts/download_jepx_spot.py
    just python scripts/update_holidays_seed.py
    just python scripts/load_jepx_spot.py
    just dbt build
