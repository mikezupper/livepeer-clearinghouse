# syntax=docker/dockerfile:1.7
ARG CADDY_IMAGE=caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

FROM ${CADDY_IMAGE}
RUN setcap -r /usr/bin/caddy
COPY deploy/edge.Caddyfile /etc/caddy/Caddyfile
USER 10001:10001
EXPOSE 8080
