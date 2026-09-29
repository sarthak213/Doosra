#!/bin/sh
# Container start: make sure there is a database, then serve.
set -e

if [ ! -f data/cricket.duckdb ] && [ -z "$SKIP_DB_PULL" ]; then
  echo "No database yet: downloading the latest published build (a few hundred MB)..."
  python -m ingest.pull
fi

# Behind a platform's proxy the client address arrives in X-Forwarded-For.
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
