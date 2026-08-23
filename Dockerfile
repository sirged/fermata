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
RUN pip install --no-cache-dir ./server
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

CMD ["uvicorn", "fermata.main:app", "--host", "0.0.0.0", "--port", "8080"]
