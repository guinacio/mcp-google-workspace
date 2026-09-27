# syntax=docker/dockerfile:1
#
# Published image for the authenticated Streamable HTTP server.
# Release tags are built for linux/amd64 and linux/arm64 by
# .github/workflows/release.yml and pushed to:
#
#   ghcr.io/guinacio/mcp-google-workspace:<version>
#
# Runtime authentication, Google OAuth, and encryption settings are supplied
# through the environment. See README.md and .env.example.

FROM python:3.12-slim AS builder

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Keep dependency installation cacheable across source-only changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

ARG MCP_BUILD_COMMIT=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_BUILD_COMMIT=${MCP_BUILD_COMMIT} \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_USER_TOKEN_DIR=/data/tokens \
    MCP_UPLOAD_DB=/data/uploads.sqlite3 \
    GEMINI_OUTPUT_DIR=/data/gemini \
    PATH="/app/.venv/bin:$PATH"

# Static OCI metadata for every build; release.yml adds version/created labels.
LABEL io.modelcontextprotocol.server.name="io.github.guinacio/mcp-google-workspace" \
      org.opencontainers.image.title="mcp-google-workspace" \
      org.opencontainers.image.description="Google Workspace MCP server (authenticated Streamable HTTP)" \
      org.opencontainers.image.source="https://github.com/guinacio/mcp-google-workspace" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${MCP_BUILD_COMMIT}"

WORKDIR /app
# Application code and virtualenv stay root-owned and read-only for the
# runtime user; only /data (tokens, uploads, Gemini output) is writable.
# (A recursive chown of /app would also duplicate the ~230 MB layer.)
COPY --from=builder /app /app

RUN groupadd --system mcp \
    && useradd --system --gid mcp --home-dir /app --no-create-home mcp \
    && mkdir -p /data/tokens /data/gemini \
    && chown -R mcp:mcp /data

USER mcp

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"

CMD ["mcp-google-workspace-http"]
