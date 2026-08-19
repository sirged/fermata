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

ENV FERMATA_LIBRARY=/data/library \
    FERMATA_CONFIG=/data/config \
    FERMATA_WEB_DIST=/app/static

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/health')" || exit 1

CMD ["uvicorn", "fermata.main:app", "--host", "0.0.0.0", "--port", "8080"]
