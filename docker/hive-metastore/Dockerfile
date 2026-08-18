ARG CURL_VERSION=8.21.0

# apache/hive ships neither curl nor wget, so the JDBC driver is fetched in a
# throwaway stage and copied in (COPY rather than ADD <url>, CKV_DOCKER_4).
FROM curlimages/curl:${CURL_VERSION} AS jdbc
ARG POSTGRES_JDBC_VERSION=42.7.13
RUN curl -fsSL -o /tmp/postgresql.jar \
    https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JDBC_VERSION}.jar

FROM apache/hive:3.1.3

ARG POSTGRES_JDBC_VERSION=42.7.13

# Postgres JDBC driver for the metastore's JDO connection; drop the ancient
# bundled driver so classpath order can't select it over ours
COPY --from=jdbc --chmod=644 /tmp/postgresql.jar /opt/hive/lib/postgresql-${POSTGRES_JDBC_VERSION}.jar

USER root
RUN rm /opt/hive/lib/postgresql-9.4.1208.jre7.jar
USER hive

# The image serves the metastore Thrift port (docker-compose.yaml overrides
# this with a schematool probe that also checks the backing database).
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/localhost/9083"]
