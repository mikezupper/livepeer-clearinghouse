# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.14.7-slim-bookworm@sha256:9ab8d9c8514b44f90cf0029dd42fdd7e9e211e639c8b995304cc04568dee900f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.12@sha256:73d2665b478d8fa2de1cf105c6841f8e9cb6b09e568fc7700440c09f8fcd7ac4

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY contracts ./contracts
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 clearinghouse \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent clearinghouse \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
CMD ["clearinghouse"]
