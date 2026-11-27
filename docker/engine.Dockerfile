# The research engine (MCP server) and the ttdr CLI.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
WORKDIR /opt/ttdr
ENV UV_PROJECT_ENVIRONMENT=/opt/ttdr/.venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md hatch_build.py ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev \
    # the app runs as the host user (compose `user:`); files keep the checkout's mode, which a
    # umask like 007 leaves unreadable to others, so make the install readable to everyone
    && chmod -R a+rX /opt/ttdr
ENV PATH=/opt/ttdr/.venv/bin:$PATH
CMD ["ttdr", "mcp"]
