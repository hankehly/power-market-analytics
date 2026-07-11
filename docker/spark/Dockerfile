ARG OPENJDK_VERSION=17
FROM eclipse-temurin:${OPENJDK_VERSION}-jre

ARG SPARK_VERSION=4.1.1

LABEL org.label-schema.name="Apache Spark ${SPARK_VERSION}" \
    org.label-schema.version=$SPARK_VERSION

ENV SPARK_HOME=/usr/spark
ENV PATH="/usr/spark/bin:/usr/spark/sbin:${PATH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends wget netcat-openbsd procps libpostgresql-jdbc-java ca-certificates && \
    wget -q "https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3.tgz" && \
    tar xzf "spark-${SPARK_VERSION}-bin-hadoop3.tgz" && \
    rm "spark-${SPARK_VERSION}-bin-hadoop3.tgz" && \
    mv "spark-${SPARK_VERSION}-bin-hadoop3" /usr/spark && \
    ln -s /usr/share/java/postgresql-jdbc4.jar /usr/spark/jars/postgresql-jdbc4.jar && \
    apt-get purge -y wget && \
    apt-get autoremove -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

CMD ["--help"]
