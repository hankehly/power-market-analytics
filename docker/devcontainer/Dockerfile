# Stage providing uv binaries (avoid unsupported variable expansion in COPY --from)
ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.10.12

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM python:${PYTHON_VERSION}-bookworm AS base

ARG POSTGRES_JDBC_VERSION=42.7.9
ARG NODE_VERSION=25
ARG TARGETARCH
ARG NVM_VERSION=0.40.4

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    POSTGRES_JDBC_DIR=/opt/jdbc \
    POSTGRES_JDBC_JAR=/opt/jdbc/postgresql.jar \
    CLASSPATH=/opt/jdbc/postgresql.jar \
    NVM_DIR=/root/.nvm \
    UV_HTTP_TIMEOUT=600 \
    GIT_PAGER="less -R" \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    openjdk-17-jdk-headless \
    ca-certificates \
    curl \
    bash-completion \
    libpq5 \
    less \
    vim \
    ripgrep \
    jq \
    rsync \
    tini && \
    rm -rf /var/lib/apt/lists/*

RUN echo 'if [ -f /etc/bash_completion ]; then . /etc/bash_completion; fi' >> /etc/bash.bashrc \
 && printf "alias ..='cd ..'\n" >> /etc/bash.bashrc \
 && printf "alias cp='cp -i'\n" >> /etc/bash.bashrc \
 && printf "alias ll='ls -laGhp'\n" >> /etc/bash.bashrc \
 && printf "alias mv='mv -i'\n" >> /etc/bash.bashrc \
 && printf "alias rm='rm -i'\n" >> /etc/bash.bashrc

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash
RUN bash -c "source $NVM_DIR/nvm.sh && nvm install $NODE_VERSION && npm install -g rust-just @anthropic-ai/claude-code"

# AWS CLI (official installer, multi-arch) — required for the S3 data download
RUN AWS_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "aarch64" || echo "x86_64") && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscli.zip && \
    unzip -q /tmp/awscli.zip -d /tmp/awscli-installation && \
    /tmp/awscli-installation/aws/install && \
    rm -rf /tmp/awscli.zip /tmp/awscli-installation

# uv (copy from named stage) pinned version
COPY --from=uvbin /uv /uvx /bin/

# Postgres JDBC driver (for Spark <-> Postgres reads/writes)
RUN mkdir -p "$POSTGRES_JDBC_DIR" && \
    curl -fsSL -o "$POSTGRES_JDBC_DIR/postgresql-${POSTGRES_JDBC_VERSION}.jar" \
    https://jdbc.postgresql.org/download/postgresql-${POSTGRES_JDBC_VERSION}.jar && \
    ln -s "postgresql-${POSTGRES_JDBC_VERSION}.jar" "$POSTGRES_JDBC_DIR/postgresql.jar"

WORKDIR /workspace

# Python deps (most expensive step — only invalidated by pyproject.toml or uv.lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
