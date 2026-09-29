# Doosra as one container: the API and the built React app on a single origin.
#
#   docker build -t doosra .
#   docker run -p 8000:8000 --env-file backend/.env.hosted -v doosra-data:/app/backend/data doosra
#
# The cricket database (about 700 MB) is NOT baked in: on first start the
# entrypoint downloads the latest validated build from the project's GitHub
# Releases into the data volume, and DATA_REFRESH_HOURS keeps it current.
# See backend/.env.hosted.example for every setting.

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Same origin as the API, so the browser talks to relative URLs.
ENV VITE_API_BASE=""
RUN npm run build

FROM python:3.14-slim AS app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
RUN chmod +x docker-entrypoint.sh && useradd --system --create-home doosra && mkdir -p data && chown -R doosra /app
USER doosra

ENV AUTH_MODE=oauth \
    FRONTEND_DIST=/app/frontend/dist \
    DATA_REFRESH_HOURS=24 \
    PORT=8000
VOLUME ["/app/backend/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=900s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"
ENTRYPOINT ["./docker-entrypoint.sh"]
