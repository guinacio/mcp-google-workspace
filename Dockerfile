FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
COPY --from=builder /app /app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "mcp_google_workspace"]
