FROM node:24-bookworm-slim AS console
WORKDIR /console
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-wqy-zenhei ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 muse
WORKDIR /app

# Pin the lockfile consumer; no install-time dependency re-resolution.
COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY --from=console /src/muse_console/web/ ./src/muse_console/web/
RUN uv sync --locked --no-dev --no-editable --no-cache
ENV PATH="/app/.venv/bin:$PATH"

ENV MUSE_CONSOLE_HOST=0.0.0.0 \
    MUSE_CONSOLE_DATA_DIR=/app/data \
    MUSE_CONSOLE_CHROMIUM_PATH=/usr/bin/chromium

RUN mkdir -p /app/data && chown -R muse:muse /app
USER muse

EXPOSE 18610
CMD ["muse-console"]
