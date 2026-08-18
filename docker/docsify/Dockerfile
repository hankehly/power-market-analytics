FROM node:24-alpine

# figlet >= 1.9 (transitive dep via yargonaut) ships a broken CommonJS build
# that crashes docsify-cli at startup; force the last working release
RUN npm install --global docsify-cli@4.4.4 && \
    npm install --prefix /usr/local/lib/node_modules/docsify-cli figlet@1.8.2

WORKDIR /workspace

# docsify serve on its default port 3000 (docker-compose.yaml).
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=10 \
    CMD ["node", "-e", "fetch('http://localhost:3000').then((response) => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1))"]

# Serving static docs from the bind-mounted workspace needs no root; the base
# image provides the `node` user (uid 1000).
USER node
