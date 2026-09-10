# syntax=docker/dockerfile:1.7
ARG NODE_IMAGE=node:26.8.1-bookworm-slim@sha256:367679cf9792759492a486e4aa4b421764d71a9546a6dae8aab81a99eb797b3e
ARG CADDY_IMAGE=caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

FROM ${NODE_IMAGE} AS build
ARG APP
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/apps ./apps
COPY frontend/packages ./packages
COPY frontend/tsconfig.json frontend/tsconfig.base.json frontend/eslint.config.js ./
RUN npm ci --ignore-scripts \
    && case "$APP" in \
      admin-web) npm run build --workspace @livepeer/clearinghouse-admin-web -- --base=/admin/ ;; \
      user-web) npm run build --workspace @livepeer/clearinghouse-user-web -- --base=/ ;; \
      *) printf '%s\n' 'APP must be admin-web or user-web' >&2; exit 1 ;; \
    esac

FROM ${CADDY_IMAGE} AS runtime
ARG APP
RUN setcap -r /usr/bin/caddy
COPY deploy/web.Caddyfile /etc/caddy/Caddyfile
COPY --from=build --chown=10001:10001 /src/apps/${APP}/dist /srv
USER 10001:10001
EXPOSE 8080
