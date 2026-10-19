# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv
FROM node:24.10.0-bookworm-slim AS node
FROM python:3.13.9-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock package.json package-lock.json ./
RUN uv sync --frozen --python /usr/local/bin/python && npm ci --ignore-scripts
COPY . .
RUN npm run build && npm run build:check
RUN uv sync --frozen --no-dev --python /usr/local/bin/python

FROM python:3.13.9-slim-bookworm AS runtime
RUN groupadd --gid 10001 oko && useradd --uid 10001 --gid 10001 --no-create-home oko
WORKDIR /app
COPY --from=build /app /app
RUN rm -rf /app/node_modules /app/.scratch /app/tests
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c", "import json,urllib.request; d=json.load(open('/data/runtime.json')); r=urllib.request.Request('http://127.0.0.1:8765/api/v3/status',headers={'Authorization':'Bearer '+d['token']}); s=json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=3)); assert s['runtime_state'] in ('running','disabled')"]
ENTRYPOINT ["python", "-m", "oko"]
CMD ["--persistent", "--host", "0.0.0.0", "--port", "8765", "--public-url", "http://localhost:8765", "--codex-url", "http://host.docker.internal:8767", "--codex-token-file", "/run/secrets/codex", "--data", "/data", "--no-browser", "--initially-disabled"]
