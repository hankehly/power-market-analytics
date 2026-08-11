"""Superset configuration, mounted at /app/pythonpath/superset_config.py.

Only the metadata database (users, dashboards, query history) is configured
here; the analytics data connection (Spark thriftserver) is registered in the
Superset UI. SECRET_KEY comes from the SUPERSET_SECRET_KEY env var, which the
stock config reads directly.
"""

import os
from urllib.parse import quote

_password = quote(os.environ["POSTGRES_PASSWORD"], safe="")

SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://admin:{_password}@postgres-superset:5432/superset"
)

# MCP service (superset-mcp compose service, `superset mcp run`) in no-auth dev
# mode: every MCP request runs as this Superset user. Local-only stack; the port
# is bound to 127.0.0.1 on the host.
MCP_DEV_USERNAME = "admin"
