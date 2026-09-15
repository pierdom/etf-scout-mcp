FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Step 1: install dependencies only — layer is cached until lockfile changes
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Step 2: copy source, then install the project wheel
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

# Non-root user — uid/gid pinned (not left to useradd's auto-assignment) so
# docker-compose.yml's `user: "1000:1000"` and volume ownership stay stable
# across rebuilds instead of drifting with the base image.
RUN groupadd --system --gid 1000 etfmcp \
    && useradd --system --no-create-home --uid 1000 --gid 1000 etfmcp \
    && mkdir -p /data && chown etfmcp:etfmcp /data
USER etfmcp

# Persist OIDCProxy's encrypted client store / DCR registrations across container
# recreation (FastMCP derives its data dir from platformdirs → XDG_DATA_HOME). Mount
# /data as a volume when using OIDC so remote sessions survive restarts.
ENV XDG_DATA_HOME=/data

EXPOSE 8765

CMD ["/app/.venv/bin/python", "-m", "etf_scout_mcp"]
