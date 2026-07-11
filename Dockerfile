# Shared, reproducible environment for the whole team. Build once, everyone runs
# the same thing — no "works on my machine." See README for usage.
FROM python:3.12-slim

# uv: fast, reproducible installs driven by the committed uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# --frozen uses uv.lock exactly (reproducible). Falls back to resolving from
# pyproject the first time, before a lockfile is committed.
RUN uv sync --frozen --no-dev || uv sync --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# Default command: prove the container can reach Postgres (proxy must be running,
# env vars passed in). Override for ingestion, migrations, etc.
CMD ["python", "-c", "from h5n1.db import check_connection; print(check_connection())"]
