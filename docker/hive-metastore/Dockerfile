FROM apache/hive:3.1.3

ARG POSTGRES_JDBC_VERSION=42.7.13

# Postgres JDBC driver for the metastore's JDO connection; drop the ancient
# bundled driver so classpath order can't select it over ours
ADD --chmod=644 https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JDBC_VERSION}.jar /opt/hive/lib/postgresql-${POSTGRES_JDBC_VERSION}.jar

USER root
RUN rm /opt/hive/lib/postgresql-9.4.1208.jre7.jar
USER hive
