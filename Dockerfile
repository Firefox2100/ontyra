# The Ontyra daemon: REST + MCP on one port. SurrealDB runs as its own container (compose.yaml).
#   docker build --build-arg VERSION=0.1.0 -t ontyra:local .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ARG VERSION=0.0.0
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_PREFERENCE=only-system \
    SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra mcp --extra langchain --extra openai
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra mcp --extra langchain --extra openai

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --home-dir /nonexistent ontyra
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    ONTYRA_SERVER__HOST=0.0.0.0 ONTYRA_SERVER__PORT=8080 ONTYRA_MCP__ENABLED=true
USER ontyra
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:%s/health'%os.environ.get('ONTYRA_SERVER__PORT','8080'),timeout=2)"
ENTRYPOINT ["ontyra"]
CMD ["serve"]
