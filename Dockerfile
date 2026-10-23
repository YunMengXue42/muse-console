# Frontend assets are platform independent: build once on the native builder.
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS console
WORKDIR /console
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/YunMengXue42/muse-console" \
      org.opencontainers.image.description="Self-hosted OpenAI-style gateway and console for muse.ai" \
      org.opencontainers.image.licenses="MIT"

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
    MUSE_CONSOLE_DRIVER=browser \
    MUSE_CONSOLE_CHROMIUM_PATH=/usr/bin/chromium \
    MUSE_CONSOLE_CHROMIUM_NO_SANDBOX=true \
    PYTHONUNBUFFERED=1
# Docker's default seccomp profile blocks the namespaces Chromium's own sandbox needs,
# so inside the image the container is the isolation boundary: non-root UID 1000, and
# compose drops all capabilities with no-new-privileges. Outside Docker the sandbox stays on.

RUN mkdir -p /app/data && chown -R muse:muse /app
USER muse

EXPOSE 18610
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18610/healthz', timeout=4)"]
CMD ["muse-console"]
