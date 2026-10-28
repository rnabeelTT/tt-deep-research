# The research engine (MCP server) and the ttdr CLI.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
WORKDIR /opt/ttdr
ENV UV_PROJECT_ENVIRONMENT=/opt/ttdr/.venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH=/opt/ttdr/.venv/bin:$PATH
CMD ["ttdr", "mcp"]
