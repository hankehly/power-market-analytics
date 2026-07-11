ARG SPARK_VERSION=4.1.1
FROM apache/spark:${SPARK_VERSION}

ARG POSTGRES_JDBC_VERSION=42.7.13

# The base image does not put the spark binaries on PATH
ENV PATH="/opt/spark/bin:/opt/spark/sbin:${PATH}"

# Postgres JDBC driver (same pinned version across all services)
ADD --chmod=644 https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JDBC_VERSION}.jar /opt/spark/jars/postgresql-${POSTGRES_JDBC_VERSION}.jar
