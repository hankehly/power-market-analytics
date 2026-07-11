# power-market-analytics

Power market analytics.

## Development environment

The project runs inside a Docker Compose stack (see `docker-compose.yaml`):

- **devcontainer** — Python 3.13 + uv + Spark client tooling; open the repo in VS Code and reopen in container
- **postgres-metastore** — backing store for the Hive Metastore (host port 5432)
- **postgres-mlflow** — backing store for MLflow (host port 5433)
- **hive-metastore** — standalone Hive Metastore backed by Postgres
- **thriftserver** — Spark Thrift Server (JDBC/ODBC, port 10000; Spark UI on 4040)
- **mlflow** — experiment tracking UI on port 5005
- **docsify** — serves `docs/` on port 3000

### Setup

1. Copy `.env.template` to `.env` and fill in the values (see the comments for per-host memory settings).
2. `docker compose up -d`
3. Open the repo in VS Code and use "Reopen in Container", or `docker compose exec devcontainer bash`.
