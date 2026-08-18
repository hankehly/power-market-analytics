ARG SPARK_VERSION=4.1.1
FROM apache/spark:${SPARK_VERSION}

ARG POSTGRES_JDBC_VERSION=42.7.13

# The base image does not put the spark binaries on PATH
ENV PATH="/opt/spark/bin:/opt/spark/sbin:${PATH}"

# Postgres JDBC driver (same pinned version across all services). Fetched with
# curl rather than ADD <url> (CKV_DOCKER_4); the base image runs as `spark`,
# which owns /opt/spark/jars.
RUN curl -fsSL -o /opt/spark/jars/postgresql-${POSTGRES_JDBC_VERSION}.jar \
    https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JDBC_VERSION}.jar

# The image serves the Thrift JDBC/ODBC server on 10000 (docker-compose.yaml);
# bash /dev/tcp probe because the image has no netcat.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=30 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/localhost/10000"]

# Same non-root user the base image runs as (uid 185); docker-compose.yaml
# overrides it to root so the thriftserver can write the shared warehouse volume.
USER spark
