# --- Frontend build ---
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Runtime ---
FROM python:3.12-slim
WORKDIR /app
COPY server/ ./server/
# The [mcp] extra is installed even though the Model Context Protocol server
# (issue #31) is off by default, and that is the point: an operator turns it
# on with FERMATA_MCP in their compose file, not by rebuilding an image.
# Nothing it brings in is imported unless that flag is set - see
# server/fermata/main.py's _start_mcp_server.
RUN pip install --no-cache-dir "./server[mcp]"
COPY --from=web /web/dist ./static

# The commit and date GET /api/version reports (see server/fermata/version.py).
# There is no git repository in this image to ask, so these are the only
# source of truth for either value - whoever builds the image supplies them,
# e.g. `docker build --build-arg BUILD_COMMIT=$(git rev-parse --short HEAD)
# --build-arg BUILD_DATE=$(date -u +%Y-%m-%d) .`. Left unset, both default to
# "dev", which is what a plain `docker compose up --build` (no args passed)
# still honestly reports rather than a fabricated commit.
ARG BUILD_COMMIT=dev
ARG BUILD_DATE=dev

ENV FERMATA_LIBRARY=/data/library \
    FERMATA_CONFIG=/data/config \
    FERMATA_WEB_DIST=/app/static \
    FERMATA_BUILD_COMMIT=$BUILD_COMMIT \
    FERMATA_BUILD_DATE=$BUILD_DATE

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/health')" || exit 1

# --no-proxy-headers turns off uvicorn's OWN X-Forwarded-For handling -
# without it, uvicorn rewrites the request's peer address from a
# client-supplied header before Fermata's reverse-proxy authentication
# (fermata/authproxy.py, issue #16) ever sees the request, which lets anyone
# who can reach the container forge that header and impersonate a trusted
# proxy. Fermata does its own peer-based trust for that feature and never
# needs uvicorn's; see docs/deployment.md's "Reverse proxy authentication"
# section. main.py's startup refuses to run reverse-proxy auth at all if it
# cannot confirm this flag is present, as a backstop for anyone who copies
# this CMD without this comment attached.
CMD ["uvicorn", "fermata.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
