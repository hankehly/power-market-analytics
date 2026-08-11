"""Superset configuration, mounted at /app/pythonpath/superset_config.py.

Only the metadata database (users, dashboards, query history) is configured
here; the analytics data connection (Spark thriftserver) is registered in the
Superset UI. SECRET_KEY comes from the SUPERSET_SECRET_KEY env var, which the
stock config reads directly.
"""

import os
from urllib.parse import quote

from pyhive.sqlalchemy_hive import HiveDialect
from sqlalchemy import text

_password = quote(os.environ["POSTGRES_PASSWORD"], safe="")


def _spark_get_table_names(self, connection, schema=None, **kw):
    """Table names from SHOW TABLES, tolerating Spark's three-column output.

    PyHive's stock implementation returns ``row[0]``, which is correct for
    Hive (single ``tab_name`` column) but wrong for the Spark thriftserver,
    where SHOW TABLES returns ``(namespace, tableName, isTemporary)`` — every
    table in SQL Lab's schema browser showed up as the schema name and
    ``DESCRIBE <schema>.<schema>`` failed with TABLE_OR_VIEW_NOT_FOUND.

    Parameters
    ----------
    connection : sqlalchemy.engine.Connection
        Connection the reflection query runs on.
    schema : str, optional
        Schema to list tables in; the session's current schema when omitted.

    Returns
    -------
    list of str
        Table names in the schema.
    """
    query = "SHOW TABLES"
    if schema:
        query += " IN " + self.identifier_preparer.quote_identifier(schema)
    rows = connection.execute(text(query)).fetchall()
    return [row[1] if len(row) >= 2 else row[0] for row in rows]


# get_view_names delegates to get_table_names, so this covers both.
HiveDialect.get_table_names = _spark_get_table_names

SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://admin:{_password}@postgres-superset:5432/superset"
)

# MCP service (superset-mcp compose service, `superset mcp run`) in no-auth dev
# mode: every MCP request runs as this Superset user. Local-only stack; the port
# is bound to 127.0.0.1 on the host.
MCP_DEV_USERNAME = "admin"
