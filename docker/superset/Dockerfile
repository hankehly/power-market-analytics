FROM apache/superset:6.1.0

# PyHive lets Superset query the Spark thriftserver (HiveServer2 protocol);
# the pure-sasl extra avoids needing native SASL headers in the slim image.
# psycopg2 is for the metadata database (postgres-superset); the lean image
# does not bundle it. fastmcp powers `superset mcp run` (superset-mcp service);
# the version range matches the apache-superset 6.1.0 fastmcp extra.
USER root
RUN uv pip install --no-cache "pyhive[hive_pure_sasl]" psycopg2-binary "fastmcp>=3.1.0,<4.0"
USER superset

# The image serves the web app on 8088 (docker-compose.yaml; the superset-mcp
# service overrides this with a probe of its own port).
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=30 \
    CMD ["curl", "-f", "http://localhost:8088/health"]
