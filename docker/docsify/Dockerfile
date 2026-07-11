FROM node:24-alpine

# figlet >= 1.9 (transitive dep via yargonaut) ships a broken CommonJS build
# that crashes docsify-cli at startup; force the last working release
RUN npm install --global docsify-cli@4.4.4 && \
    npm install --prefix /usr/local/lib/node_modules/docsify-cli figlet@1.8.2

WORKDIR /workspace
